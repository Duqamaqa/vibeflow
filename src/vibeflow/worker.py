"""Implementation worker with an explicit result boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Iterable

from .changes import ChangeError, ChangeProposal
from .context import ContextBundle, ContextManager
from .contracts import Contract
from .fcc_client import FCCClient


@dataclass(frozen=True, slots=True)
class WorkerResult:
    success: bool
    summary: str
    diff: str = ""
    changed_files: tuple[str, ...] = ()
    applied: bool = False
    uncertainty: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    raw_output: str = ""
    proposal: ChangeProposal | None = None


class Worker:
    """Ask one isolated implementer for a reviewable change proposal."""

    def __init__(self, fcc_client: FCCClient, context_manager: ContextManager | None = None) -> None:
        self.fcc_client = fcc_client
        self.context_manager = context_manager or ContextManager()

    def execute(
        self,
        contract: Contract,
        model: str,
        context: ContextBundle,
        *,
        feedback: Iterable[str] = (),
        variation: str | None = None,
    ) -> WorkerResult:
        prompt = self.build_prompt(contract, context, feedback=feedback, variation=variation)
        response = self.fcc_client.create_response(model=model, input=prompt, stream=True)
        return self._parse_response(
            response.text,
            usage=dict(response.usage or {}),
            request_id=response.request_id,
        )

    def implement(
        self,
        contract: Contract,
        model: str,
        context_files: list[str] | None = None,
    ) -> Iterable[str]:
        """Compatibility iterator for early Phase-1 callers."""
        context = self.context_manager.build_context(contract, context_files or ())
        result = self.execute(contract, model, context)
        yield result.raw_output

    @staticmethod
    def build_prompt(
        contract: Contract,
        context: ContextBundle,
        *,
        feedback: Iterable[str] = (),
        variation: str | None = None,
    ) -> str:
        feedback_text = "\n".join(f"- {item}" for item in feedback if item)
        return (
            "You are an isolated implementation agent. Produce a structured file-change proposal; "
            "do not run commands, commit, deploy, or expose secrets. Return JSON only with keys: "
            "success, summary, operations, uncertainty. success must be a JSON boolean and "
            "uncertainty must be a JSON number from 0.0 to 1.0, never a list. Each operation is one of: "
            '{"op":"create","path":"relative/path","content":"full UTF-8 contents"}, '
            '{"op":"update","path":"relative/path","content":"full UTF-8 contents",'
            '"expected_sha256":"64 lowercase hex characters"}, '
            '{"op":"delete","path":"relative/path",'
            '"expected_sha256":"64 lowercase hex characters"}, or '
            '{"op":"rename","path":"old/relative/path","destination":"new/relative/path",'
            '"expected_sha256":"64 lowercase hex characters"}. '
            "Use only repository-relative paths. Never target .git, secrets, credentials, key files, "
            "or unrelated files. Hash preconditions must match the exact file contents supplied in "
            "context. Do not return unified diffs or shell prose.\n\n"
            f"{context.render()}\n\n"
            f"RESOLVER FEEDBACK:\n{feedback_text or '(none)'}\n"
            f"PROMPT VARIATION:\n{variation or '(none)'}"
        )

    @staticmethod
    def _parse_response(text: str, *, usage: dict[str, Any], request_id: str | None) -> WorkerResult:
        raw = text.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = _extract_json_payload(raw)
            if payload is None:
                return WorkerResult(
                    success=False,
                    summary="Agent returned an invalid non-JSON proposal",
                    usage=usage,
                    request_id=request_id,
                    raw_output=raw,
                )
        if not isinstance(payload, dict):
            return WorkerResult(
                success=False,
                summary="Agent response must be a JSON object",
                usage=usage,
                request_id=request_id,
                raw_output=raw,
            )
        proposal = None
        parse_error = None
        success_claim = payload.get("success", True)
        if not isinstance(success_claim, bool):
            parse_error = "success must be a JSON boolean"
            success_claim = False
        uncertainty, uncertainty_error = _parse_uncertainty(
            payload.get("uncertainty", 0.0)
        )
        if parse_error is None and uncertainty_error is not None:
            parse_error = uncertainty_error
        if success_claim:
            try:
                proposal = ChangeProposal.from_payload(payload.get("operations"))
            except ChangeError as exc:
                if parse_error is None:
                    parse_error = str(exc)
        return WorkerResult(
            success=success_claim and proposal is not None and parse_error is None,
            summary=(
                f"Invalid structured proposal: {parse_error}"
                if parse_error
                else str(payload.get("summary", ""))
            ),
            changed_files=() if proposal is None else proposal.changed_files,
            uncertainty=uncertainty,
            usage=usage,
            request_id=request_id,
            raw_output=raw,
            proposal=proposal,
        )


def _parse_uncertainty(value: Any) -> tuple[float, str | None]:
    if value == []:
        return 0.0, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, "uncertainty must be a JSON number between 0 and 1"
    uncertainty = float(value)
    if not math.isfinite(uncertainty) or not 0.0 <= uncertainty <= 1.0:
        return 0.0, "uncertainty must be a finite number between 0 and 1"
    return uncertainty, None


def _extract_json_payload(raw: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {"success", "operations"}.issubset(value):
            candidates.append(value)
    return candidates[0] if len(candidates) == 1 else None
