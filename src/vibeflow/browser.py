"""Safe, backend-neutral boundary for browser automation plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import uuid4


_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value in {".", ".."} or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


class BrowserOperation(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    READ = "read"
    SCREENSHOT = "screenshot"


@dataclass(frozen=True, slots=True)
class BrowserSession:
    workspace_id: str
    session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_id",
            _validate_identifier("workspace_id", self.workspace_id),
        )
        object.__setattr__(
            self,
            "session_id",
            _validate_identifier("session_id", self.session_id),
        )


@dataclass(frozen=True, slots=True)
class BrowserAction:
    operation: BrowserOperation
    target: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.operation, str):
            try:
                operation = BrowserOperation(self.operation)
            except ValueError as error:
                raise ValueError(f"unsupported browser operation: {self.operation}") from error
            object.__setattr__(self, "operation", operation)
        elif not isinstance(self.operation, BrowserOperation):
            raise TypeError("operation must be BrowserOperation")
        if self.target is not None and not isinstance(self.target, str):
            raise TypeError("target must be a string or None")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("text must be a string or None")


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    success: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise TypeError("success must be a bool")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class BrowserPolicy:
    """Validation performed before a command reaches a plugin."""

    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_hosts: frozenset[str] = frozenset()
    max_text_length: int = 10_000

    def __post_init__(self) -> None:
        schemes = tuple(scheme.strip().casefold() for scheme in self.allowed_schemes)
        if not schemes or any(not scheme for scheme in schemes):
            raise ValueError("allowed_schemes must not be empty")
        if any(scheme not in {"http", "https"} for scheme in schemes):
            raise ValueError("only HTTP(S) navigation can be enabled")
        hosts = frozenset(host.strip().casefold().rstrip(".") for host in self.allowed_hosts)
        if any(not host or "/" in host for host in hosts):
            raise ValueError("allowed_hosts must contain host names")
        if not isinstance(self.max_text_length, int) or isinstance(self.max_text_length, bool):
            raise TypeError("max_text_length must be an integer")
        if self.max_text_length < 1:
            raise ValueError("max_text_length must be positive")
        object.__setattr__(self, "allowed_schemes", schemes)
        object.__setattr__(self, "allowed_hosts", hosts)

    def validate(self, action: BrowserAction) -> None:
        operation = action.operation
        if operation is BrowserOperation.NAVIGATE:
            self._validate_url(action.target)
            if action.text is not None:
                raise ValueError("navigate actions do not accept text")
        elif operation is BrowserOperation.CLICK:
            self._require_target(action)
            if action.text is not None:
                raise ValueError("click actions do not accept text")
        elif operation is BrowserOperation.TYPE:
            self._require_target(action)
            if action.text is None:
                raise ValueError("type actions require text")
            if len(action.text) > self.max_text_length:
                raise ValueError("typed text exceeds policy limit")
        elif operation in {BrowserOperation.READ, BrowserOperation.SCREENSHOT}:
            if action.text is not None:
                raise ValueError(f"{operation.value} actions do not accept text")
        else:
            raise ValueError(f"unsupported browser operation: {operation}")

    @staticmethod
    def _require_target(action: BrowserAction) -> None:
        if action.target is None or not action.target.strip():
            raise ValueError(f"{action.operation.value} actions require a target")

    def _validate_url(self, target: str | None) -> None:
        if target is None or not target.strip():
            raise ValueError("navigate actions require a URL")
        parsed = urlsplit(target)
        if parsed.scheme.casefold() not in self.allowed_schemes:
            raise ValueError("URL scheme is not allowed")
        if parsed.hostname is None:
            raise ValueError("URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URLs containing credentials are not allowed")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("URL contains an invalid port") from error
        host = parsed.hostname.casefold().rstrip(".")
        if self.allowed_hosts and not any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in self.allowed_hosts
        ):
            raise ValueError("URL host is not allowed")


@runtime_checkable
class BrowserPlugin(Protocol):
    """Protocol implemented by an optional browser backend."""

    def open_session(self, session: BrowserSession) -> None:
        """Allocate backend state for one isolated session."""

    def execute(
        self,
        session: BrowserSession,
        action: BrowserAction,
    ) -> BrowserActionResult:
        """Perform one validated action."""

    def close_session(self, session: BrowserSession) -> None:
        """Release backend state for one isolated session."""


class BrowserController:
    """Own session isolation and delegate validated commands to a plugin."""

    def __init__(
        self,
        plugin: BrowserPlugin,
        *,
        policy: BrowserPolicy | None = None,
    ) -> None:
        if not isinstance(plugin, BrowserPlugin):
            raise TypeError("plugin must implement BrowserPlugin")
        self._plugin = plugin
        self.policy = policy or BrowserPolicy()
        self._sessions: dict[str, BrowserSession] = {}

    def open_session(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
    ) -> BrowserSession:
        resolved_session_id = session_id or uuid4().hex
        session = BrowserSession(workspace_id, resolved_session_id)
        if session.session_id in self._sessions:
            raise ValueError(f"session already exists: {session.session_id}")
        self._plugin.open_session(session)
        self._sessions[session.session_id] = session
        return session

    def execute(
        self,
        session: BrowserSession,
        action: BrowserAction,
    ) -> BrowserActionResult:
        active_session = self._require_active(session)
        if not isinstance(action, BrowserAction):
            raise TypeError("action must be BrowserAction")
        self.policy.validate(action)
        result = self._plugin.execute(active_session, action)
        if not isinstance(result, BrowserActionResult):
            raise TypeError("browser plugins must return BrowserActionResult")
        return result

    def close_session(self, session: BrowserSession) -> None:
        active_session = self._require_active(session)
        self._plugin.close_session(active_session)
        del self._sessions[active_session.session_id]

    def active_sessions(self) -> tuple[BrowserSession, ...]:
        return tuple(self._sessions[key] for key in sorted(self._sessions))

    def _require_active(self, session: BrowserSession) -> BrowserSession:
        if not isinstance(session, BrowserSession):
            raise TypeError("session must be BrowserSession")
        active = self._sessions.get(session.session_id)
        if active is None:
            raise KeyError(f"unknown browser session: {session.session_id}")
        if active != session:
            raise ValueError("session does not belong to this workspace")
        return active


BrowserAutomation = BrowserController
BrowserResult = BrowserActionResult

