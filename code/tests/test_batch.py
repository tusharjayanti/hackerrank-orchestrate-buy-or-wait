from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from buyorwait.config import Settings
from buyorwait.obs.llm_client import LLMClient, StructuredJob
from buyorwait.obs.run_context import RunContext
from buyorwait.obs.usage_report import build_usage_report, load_llm_calls


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


def usage() -> SimpleNamespace:
    return SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0)


class FakeBatches:
    def __init__(self, results, statuses=("ended",)) -> None:
        self._results = results
        self._statuses = list(statuses)
        self.created = None
        self.cancelled = False

    def create(self, requests):
        self.created = list(requests)
        return SimpleNamespace(id="msgbatch_test", processing_status="in_progress")

    def retrieve(self, batch_id):
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id):
        return iter(self._results)

    def cancel(self, batch_id):
        self.cancelled = True


class FakeMessages:
    def __init__(self, batches: FakeBatches) -> None:
        self.batches = batches
        self.parse_calls = 0

    def parse(self, **kwargs):
        self.parse_calls += 1
        return SimpleNamespace(
            parsed_output=kwargs["output_format"](answer="synchronous"),
            usage=usage(),
            stop_reason="end_turn",
            model="claude-sonnet-5",
            id="msg_sync",
        )


def succeeded(custom_id: str, text: str) -> SimpleNamespace:
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=usage(), stop_reason="end_turn", model="claude-sonnet-5", id="msg_batch"
    )
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


def errored(custom_id: str) -> SimpleNamespace:
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="errored"))


FIELDS = dict(purpose="evidence.message", output_model=Reply, system="s", prompt_version="v1", effort="low", max_tokens=100)


def job(custom_id: str, content: str) -> StructuredJob:
    return StructuredJob(custom_id=custom_id, messages=[{"role": "user", "content": content}], **FIELDS)


def client(tmp_path, batches: FakeBatches) -> tuple[LLMClient, FakeMessages, RunContext]:
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", runs_dir=tmp_path / "runs")
    run = RunContext.create(settings.runs_dir, "t")
    messages = FakeMessages(batches)
    return LLMClient(settings, run, client=SimpleNamespace(messages=messages)), messages, run


def test_batch_results_fill_the_cache_at_half_price_and_failures_fall_back(tmp_path):
    batches = FakeBatches([succeeded("msg-a", '{"answer": "batched"}'), errored("msg-b")], statuses=("in_progress", "ended"))
    llm, messages, run = client(tmp_path, batches)

    outcome = llm.parse_batch([job("msg-a", "first"), job("msg-b", "second")], poll_seconds=0, timeout_seconds=60, sleep=lambda _: None)

    assert outcome == {"msg-a": True, "msg-b": False}
    assert batches.created[0]["params"]["output_config"]["format"]["type"] == "json_schema"
    assert batches.created[0]["params"]["output_config"]["effort"] == "low"
    assert llm.parse(messages=[{"role": "user", "content": "first"}], record_replay=False, **FIELDS).answer == "batched"
    assert llm.parse(messages=[{"role": "user", "content": "second"}], record_replay=False, **FIELDS).answer == "synchronous"
    assert messages.parse_calls == 1

    records = load_llm_calls(run.llm_calls.path)
    assert [(record.batch, record.cached_replay) for record in records] == [(True, False), (False, False)]
    assert records[0].cost_usd == pytest.approx((1000 * 2 / 1e6 + 200 * 10 / 1e6) * 0.5)
    report = build_usage_report(records, run_id="t", request_count=2)
    assert "- Batch API calls: 1 of 2 (billed at 50% of list price)" in report
    assert "| evidence.message (batch) |" in report


def test_batch_timeout_cancels_and_leaves_everything_to_synchronous_calls(tmp_path):
    batches = FakeBatches([], statuses=("in_progress",))
    llm, _, run = client(tmp_path, batches)

    outcome = llm.parse_batch([job("msg-a", "first")], poll_seconds=10, timeout_seconds=30, sleep=lambda _: None)

    assert outcome == {"msg-a": False}
    assert batches.cancelled
    assert load_llm_calls(run.llm_calls.path) == []


def test_invalid_batch_output_is_recorded_but_not_cached(tmp_path):
    batches = FakeBatches([succeeded("msg-a", '{"wrong": 1}')])
    llm, messages, run = client(tmp_path, batches)

    assert llm.parse_batch([job("msg-a", "first")], poll_seconds=0, timeout_seconds=60, sleep=lambda _: None) == {"msg-a": False}
    assert llm.parse(messages=[{"role": "user", "content": "first"}], **FIELDS).answer == "synchronous"
    records = load_llm_calls(run.llm_calls.path)
    assert records[0].batch and records[0].error
