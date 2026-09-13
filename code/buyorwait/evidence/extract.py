"""Run Claude extraction over every message and image, synchronously or through the Message Batches API, then validate."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..ingest.loaders import Dataset
from ..obs.llm_client import LLMClient, LLMError, StructuredJob
from ..obs.logging import get_logger
from ..obs.run_context import RunContext
from ..obs.tracing import submit_in_context
from ..schemas.domain import ImageRef, Message
from ..schemas.enums import Severity
from ..schemas.evidence import AcceptedFact, EvidenceKind, EvidenceReview, ImageExtraction, MessageExtraction
from ..schemas.obs import GuardrailViolation
from .prompts import IMAGE_PROMPT_VERSIONS, IMAGE_SYSTEM_A, IMAGE_SYSTEM_B, MESSAGE_PROMPT_VERSION, MESSAGE_SYSTEM
from .validate import review_image, review_message

logger = get_logger("evidence")


@dataclass
class EvidenceStore:
    reviews: dict[str, EvidenceReview] = field(default_factory=dict)

    def facts_for_user(self, user_id: str) -> list[AcceptedFact]:
        return [fact for review in self.reviews.values() if review.user_id == user_id for fact in review.accepted]

    def amount_overrides(self) -> dict[str, Decimal]:
        return {
            fact.related_event_id: fact.amount_home
            for review in self.reviews.values()
            for fact in review.accepted
            if fact.kind is EvidenceKind.DOCUMENT_AMOUNT and fact.related_event_id and fact.amount_home is not None
        }


class EvidenceExtractor:
    def __init__(self, llm: LLMClient, settings: Settings, dataset: Dataset, run: RunContext) -> None:
        self.llm = llm
        self.settings = settings
        self.dataset = dataset
        self.run = run
        self._record_replays = True

    def extract_all(self) -> EvidenceStore:
        store = EvidenceStore()
        attributes = {
            "evidence.messages": len(self.dataset.messages),
            "evidence.images": len(self.dataset.images),
            "evidence.batch": self.settings.evidence_batch,
        }
        with self.run.tracer.span("evidence.extract", **attributes):
            if self.settings.evidence_batch:
                self._prefill_with_batch()
            with ThreadPoolExecutor(max_workers=self.settings.concurrency) as pool:
                futures = [submit_in_context(pool, self._message, message) for message in self.dataset.messages]
                futures += [submit_in_context(pool, self._image, image) for image in self.dataset.images]
                for future in as_completed(futures):
                    review = future.result()
                    store.reviews[review.source_id] = review
                    self.run.evidence.write(review)
                    self.run.record_violations(review.violations)
        rejected = sum(1 for review in store.reviews.values() for v in review.violations if v.severity is Severity.ERROR)
        logger.info(
            "evidence extracted",
            extra={"fields": {"sources": len(store.reviews), "accepted_facts": sum(len(r.accepted) for r in store.reviews.values()), "error_violations": rejected}},
        )
        return store

    def _prefill_with_batch(self) -> None:
        """Submit every extraction as one Message Batch; anything the batch does not return is called synchronously."""
        jobs = [StructuredJob(custom_id=f"msg-{message.message_id}", **self._message_call(message)) for message in self.dataset.messages]
        for image in self.dataset.images:
            for index, call in enumerate(self._image_calls(image) or []):
                jobs.append(StructuredJob(custom_id=f"img-{image.image_id}-{index}", **call))
        outcome = self.llm.parse_batch(
            jobs, poll_seconds=self.settings.batch_poll_seconds, timeout_seconds=self.settings.batch_timeout_seconds
        )
        # Results the batch just cached are already recorded as billed batch calls; don't record them again as replays.
        self._record_replays = False
        fallback = sorted(custom_id for custom_id, ok in outcome.items() if not ok)
        logger.info(
            "evidence batch finished",
            extra={"fields": {"jobs": len(jobs), "cached": len(jobs) - len(fallback), "synchronous_fallback": fallback[:20]}},
        )

    def _context(self, user_id: str, related_event_id: str | None) -> str:
        profile = self.dataset.profiles[user_id]
        lines = [f"- customer home currency: {profile.home_currency}"]
        event = self.dataset.events_by_id.get(related_event_id) if related_event_id else None
        if event is not None:
            lines.append(
                f"- linked event {event.event_id}: {event.description} | category {event.category} | {event.direction} | "
                f"status {event.status} | amount {event.amount if event.amount is not None else 'BLANK'} {event.currency} | "
                f"event date {event.event_date} | settlement date {event.settlement_date or 'none'}"
            )
        return "\n".join(lines)

    def _message_call(self, message: Message) -> dict[str, Any]:
        prompt = (
            "Trusted context from the bank's records:\n"
            f"- message_id: {message.message_id}\n- source_type: {message.source_type}\n- sent_on: {message.sent_at.date()}\n"
            f"{self._context(message.user_id, message.related_event_id)}\n\n"
            f'<untrusted_message id="{message.message_id}">\n{message.message_text}\n</untrusted_message>\n\n'
            "Extract the financial facts from this message."
        )
        return dict(
            purpose="evidence.message",
            output_model=MessageExtraction,
            system=MESSAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            prompt_version=MESSAGE_PROMPT_VERSION,
            effort=self.settings.message_effort,
            max_tokens=6000,
            request_id=message.request_id,
        )

    def _image_calls(self, image: ImageRef) -> list[dict[str, Any]] | None:
        event = self.dataset.events_by_id.get(image.related_event_id or "")
        path = self.dataset.image_path(image)
        if event is None or not path.exists():
            return None
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        calls = []
        for system, version in zip((IMAGE_SYSTEM_A, IMAGE_SYSTEM_B), IMAGE_PROMPT_VERSIONS):
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                {
                    "type": "text",
                    "text": f"Trusted context from the bank's records:\n- image_id: {image.image_id}\n"
                    f"{self._context(image.user_id, image.related_event_id)}\n\nReturn the amount for the linked event.",
                },
            ]
            calls.append(
                dict(
                    purpose="evidence.image",
                    output_model=ImageExtraction,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                    prompt_version=version,
                    effort=self.settings.image_effort,
                    max_tokens=8000,
                    request_id=image.request_id,
                )
            )
        return calls

    def _message(self, message: Message) -> EvidenceReview:
        try:
            extraction = self.llm.parse(**self._message_call(message), record_replay=self._record_replays)
        except LLMError as exc:
            return _failed(message.message_id, "message", message.user_id, message.request_id, str(exc))
        return review_message(extraction, message, self.dataset.profiles[message.user_id], self.dataset.fx)

    def _image(self, image: ImageRef) -> EvidenceReview:
        calls = self._image_calls(image)
        event = self.dataset.events_by_id.get(image.related_event_id or "")
        if calls is None or event is None:
            return _failed(image.image_id, "image", image.user_id, image.request_id, "image file or linked event missing")
        reads: list[ImageExtraction] = []
        for call in calls:
            try:
                reads.append(self.llm.parse(**call, record_replay=self._record_replays))
            except LLMError as exc:
                logger.error("image read failed", extra={"fields": {"image_id": image.image_id, "error": str(exc)}})
        if not reads:
            return _failed(image.image_id, "image", image.user_id, image.request_id, "all image reads failed")
        return review_image(reads, image, event, self.dataset.profiles[image.user_id], self.dataset.fx)


def _failed(source_id: str, source_type: str, user_id: str, request_id: str | None, error: str) -> EvidenceReview:
    violation = GuardrailViolation(
        layer="G3", code="extraction_failed", severity=Severity.ERROR, message=error, entity_id=source_id, request_id=request_id
    )
    return EvidenceReview(source_id=source_id, source_type=source_type, user_id=user_id, violations=[violation], error=error)
