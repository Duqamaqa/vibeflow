"""Concrete agent execution adapters for backend-neutral strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import AgentExecutor, AgentRequest, AgentResult, AgentUsage
from .fcc_client import FCCClient


@dataclass(slots=True)
class FCCAgentExecutor(AgentExecutor):
    """Execute one isolated agent request through an FCC model slug."""

    client: FCCClient
    model: str

    def execute(self, request: AgentRequest) -> AgentResult:
        transcript: list[str] = []
        if request.system_prompt:
            transcript.append(f"SYSTEM:\n{request.system_prompt}")
        transcript.extend(
            f"{message.role.upper()}:\n{message.content}" for message in request.history
        )
        transcript.append(f"USER:\n{request.prompt}")
        response = self.client.create_response(
            model=self.model,
            input="\n\n".join(transcript),
            stream=True,
        )
        usage_data: dict[str, Any] = dict(response.usage or {})
        usage = AgentUsage(
            input_tokens=int(usage_data.get("input_tokens", 0) or 0),
            output_tokens=int(usage_data.get("output_tokens", 0) or 0),
        )
        return AgentResult(
            content=response.text,
            decision=response.text.strip() or None,
            usage=usage,
            metadata={
                "model": self.model,
                "request_id": response.request_id,
                "role": request.role,
            },
        )
