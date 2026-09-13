import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from buyorwait.config import Settings
from buyorwait.obs.llm_client import LLMClient
from buyorwait.obs.logging import JsonFormatter
from buyorwait.obs.redaction import redact
from buyorwait.obs.run_context import RunContext
from buyorwait.obs.usage_report import build_usage_report, load_llm_calls
from buyorwait.schemas.enums import SpanStatus
from buyorwait.schemas.obs import LLMCallRecord


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_spans_nest_and_inherit_request_id(tmp_path):
    run = RunContext.create(tmp_path, "t")
    with run.tracer.span("outer", request_id="request_26") as outer:
        with run.tracer.span("inner"):
            pass
    spans = {record["name"]: record for record in read_jsonl(run.run_dir / "spans.jsonl")}
    assert spans["inner"]["parent_span_id"] == outer.span_id
    assert spans["inner"]["attributes"]["request.id"] == "request_26"
    assert spans["inner"]["trace_id"] == spans["outer"]["trace_id"]


def test_span_records_errors(tmp_path):
    run = RunContext.create(tmp_path, "t")
    with pytest.raises(ValueError), run.tracer.span("boom"):
        raise ValueError("bad")
    (span,) = read_jsonl(run.run_dir / "spans.jsonl")
    assert span["status"] == SpanStatus.ERROR.value
    assert "bad" in span["status_message"]


def test_redaction_masks_api_keys():
    fake_key = "sk-" + "ant-api03-" + "abcdefghijklmnop"  # assembled so secret scanners never see a key literal
    text = redact("key " + fake_key + ' and {"x-api-key": "secret123"}')
    assert "sk-ant" not in text
    assert "secret123" not in text


def test_json_formatter_includes_span_context(tmp_path):
    run = RunContext.create(tmp_path, "t")
    record = logging.LogRecord("buyorwait.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.fields = {"n": 1}
    with run.tracer.span("s", request_id="request_27") as span:
        payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["span_id"] == span.span_id
    assert payload["request_id"] == "request_27"
    assert payload["fields"] == {"n": 1}


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class FakeMessages:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            parsed_output=kwargs["output_format"](answer="ok"),
            usage=SimpleNamespace(
                input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=None
            ),
            stop_reason="end_turn",
            model="claude-sonnet-5",
            id="msg_test",
        )


def test_llm_client_records_usage_and_replays_cache(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", runs_dir=tmp_path / "runs", model="claude-sonnet-5")
    run = RunContext.create(settings.runs_dir, "t")
    fake = SimpleNamespace(messages=FakeMessages())
    client = LLMClient(settings, run, client=fake)
    call = dict(
        purpose="evidence.message",
        output_model=Reply,
        system="system",
        messages=[{"role": "user", "content": "hi"}],
        prompt_version="v1",
        effort="low",
        request_id="request_26",
    )

    assert client.parse(**call).answer == "ok"
    assert client.parse(**call).answer == "ok"

    assert fake.messages.calls == 1
    records = load_llm_calls(run.llm_calls.path)
    assert [record.cached_replay for record in records] == [False, True]
    assert records[0].request_id == "request_26"
    assert records[0].cost_usd == pytest.approx(1000 * 2 / 1e6 + 200 * 10 / 1e6)


def make_record(**overrides) -> LLMCallRecord:
    values = dict(
        timestamp=datetime.now(UTC),
        run_id="r",
        trace_id="t",
        span_id="s",
        request_id="request_26",
        purpose="agent.turn",
        model="claude-sonnet-5",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.004,
        prompt_version="v1",
        prompt_sha256="x",
    )
    values.update(overrides)
    return LLMCallRecord(**values)


def test_usage_report_totals_and_averages():
    records = [make_record(), make_record(purpose="evidence.image"), make_record(cached_replay=True)]
    report = build_usage_report(records, run_id="r", request_count=2)
    assert "| Model calls | 2 |" in report
    assert "| Total tokens | 2,400 |" in report
    assert "| Average tokens per request | 1,200.0 |" in report
    assert "| Estimated total cost (USD) | $0.0080 |" in report
    assert "replayed from cache: 1" in report
    assert "| anthropic | claude-sonnet-5 |" in report


def test_usage_report_records_provenance_of_the_output(tmp_path):
    output = tmp_path / "output.csv"
    output.write_text('request_id,decision_explanation\nrequest_26,"Pay today.\nSecond line."\n', encoding="utf-8")
    report = build_usage_report(
        [make_record()], run_id="final", request_count=1, command="python code/main.py --fresh", source_revision="abc1234", output_path=output
    )
    assert "- Command: `python code/main.py --fresh`" in report
    assert "- Code revision: `abc1234`" in report
    assert "`output.csv` (1 rows, sha256 `" in report
    assert "| Input tokens (all) | 1,000 |" in report
