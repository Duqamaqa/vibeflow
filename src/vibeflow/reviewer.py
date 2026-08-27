"""Fresh, isolated review of evidence rather than implementer reasoning."""

from __future__ import annotations

from dataclasses import dataclass
import ast
import json
from typing import Any, Mapping

from .context import ContextBundle
from .contracts import Contract
from .fcc_client import FCCClient


@dataclass(frozen=True, slots=True)
class ReviewResult:
    approved: bool
    feedback: str
    required_changes: tuple[str, ...] = ()
    confidence: float = 0.0
    request_id: str | None = None
    usage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "feedback": self.feedback,
            "required_changes": list(self.required_changes),
            "confidence": self.confidence,
            "request_id": self.request_id,
            "usage": self.usage,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class Reviewer:
    """Stateless reviewer; each invocation receives only review evidence."""

    def __init__(self, fcc_client: FCCClient) -> None:
        self.fcc_client = fcc_client

    def review(
        self,
        contract: Contract,
        context: ContextBundle,
        diff: str,
        verification_results: Mapping[str, Any] | Any,
        model: str,
    ) -> ReviewResult:
        prompt = self.build_review_prompt(contract, context, diff, verification_results)
        response = self.fcc_client.create_response(model=model, input=prompt, stream=True)
        return self.parse_review(
            response.text,
            request_id=response.request_id,
            usage=dict(getattr(response, "usage", None) or {}),
        )

    @staticmethod
    def build_review_prompt(
        contract: Contract,
        context: ContextBundle,
        diff: str,
        verification_results: Mapping[str, Any] | Any,
    ) -> str:
        if hasattr(verification_results, "to_dict"):
            verification_results = verification_results.to_dict()
        verification = json.dumps(verification_results, indent=2, default=str)
        return (
            "You are a fresh independent reviewer. You have no access to implementer reasoning or "
            "conversation history. Judge only the contract, bounded context, diff, and deterministic "
            "verification. Return JSON only with approved, feedback, required_changes, confidence.\n\n"
            f"{context.render()}\n\n"
            f"=== DIFF ===\n{diff or '(no diff)'}\n\n"
            f"=== VERIFICATION ===\n{verification}"
        )

    @staticmethod
    def parse_review(
        text: str,
        *,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> ReviewResult:
        raw = text.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return Reviewer._parse_legacy(raw, request_id=request_id, usage=usage)
        changes = payload.get("required_changes", ())
        if isinstance(changes, str):
            changes = [changes]
        if not isinstance(changes, list):
            changes = []
        return ReviewResult(
            approved=bool(payload.get("approved", False)),
            feedback=str(payload.get("feedback", "")),
            required_changes=tuple(str(change) for change in changes if str(change).strip()),
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            request_id=request_id,
            usage=usage,
        )

    @staticmethod
    def _parse_legacy(
        text: str,
        *,
        request_id: str | None,
        usage: dict[str, Any] | None,
    ) -> ReviewResult:
        values: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip().upper()] = value.strip()
        approved = values.get("APPROVED", "NO").upper() == "YES"
        changes_text = values.get("REQUIRED_CHANGES", "")
        changes: list[str] = []
        if changes_text and changes_text != "[]":
            try:
                parsed = ast.literal_eval(changes_text)
                changes = parsed if isinstance(parsed, list) else [str(parsed)]
            except (SyntaxError, ValueError):
                changes = [item.strip() for item in changes_text.split(",") if item.strip()]
        return ReviewResult(
            approved=approved,
            feedback=values.get("FEEDBACK", text),
            required_changes=tuple(str(change) for change in changes),
            request_id=request_id,
            usage=usage,
        )
