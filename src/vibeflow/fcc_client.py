"""Synchronous standard-library client for the Free Claude Code gateway."""

from __future__ import annotations

import codecs
import http.client
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlsplit


DEFAULT_FCC_SERVER_ROOT = "http://127.0.0.1:8082"
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "openai-request-id",
    "x-amzn-requestid",
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?!bearer\s+)[^\"'\s,}]+"
)


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One Server-Sent Event after field folding."""

    data: str
    event: str | None = None
    id: str | None = None
    retry: int | None = None
    raw: str = ""

    @property
    def type(self) -> str:
        return self.event or "message"

    def json(self) -> Any:
        return json.loads(self.data)


@dataclass(slots=True)
class FCCResponse:
    """Collected result from an FCC Responses API request."""

    text: str = ""
    usage: dict[str, Any] | None = None
    request_id: str | None = None
    raw: Any = None
    events: list[SSEEvent] = field(default_factory=list)

    @property
    def output_text(self) -> str:
        return self.text


class FCCError(RuntimeError):
    """Base error carrying safe, structured FCC failure details."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body
        self.request_id = request_id

    def __str__(self) -> str:
        details = [self.message]
        if self.status is not None:
            details.append(f"status={self.status}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        if self.body:
            details.append(f"body={self.body}")
        return "; ".join(details)


class FCCHTTPError(FCCError):
    """FCC returned a non-success HTTP status."""


class FCCProtocolError(FCCError):
    """FCC returned a response that could not be decoded."""


class FCCTransportError(FCCError):
    """The HTTP exchange failed before a valid FCC response completed."""


def _line_break_index(buffer: str) -> int:
    newline = buffer.find("\n")
    carriage_return = buffer.find("\r")
    if newline == -1:
        return carriage_return
    if carriage_return == -1:
        return newline
    return min(newline, carriage_return)


def _iter_sse_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""

    for chunk in chunks:
        if not chunk:
            continue
        buffer += decoder.decode(chunk, final=False)
        while True:
            index = _line_break_index(buffer)
            if index < 0:
                break
            if buffer[index] == "\r" and index + 1 == len(buffer):
                break
            width = 2 if buffer[index : index + 2] == "\r\n" else 1
            yield buffer[:index]
            buffer = buffer[index + width :]

    buffer += decoder.decode(b"", final=True)
    while True:
        index = _line_break_index(buffer)
        if index < 0:
            break
        width = 2 if buffer[index : index + 2] == "\r\n" else 1
        yield buffer[:index]
        buffer = buffer[index + width :]
    if buffer:
        yield buffer


def iter_sse_events(chunks: Iterable[bytes]) -> Iterator[SSEEvent]:
    """Parse fragmented UTF-8 SSE bytes, including an unterminated final event."""

    data_lines: list[str] = []
    raw_lines: list[str] = []
    event_name: str | None = None
    last_event_id: str | None = None
    retry: int | None = None
    saw_data = False
    first_line = True

    for line in _iter_sse_lines(chunks):
        if first_line:
            line = line.removeprefix("\ufeff")
            first_line = False

        if line == "":
            if saw_data:
                yield SSEEvent(
                    data="\n".join(data_lines),
                    event=event_name,
                    id=last_event_id,
                    retry=retry,
                    raw="\n".join(raw_lines),
                )
            data_lines = []
            raw_lines = []
            event_name = None
            retry = None
            saw_data = False
            continue

        raw_lines.append(line)
        if line.startswith(":"):
            continue

        field_name, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]

        if field_name == "data":
            data_lines.append(value)
            saw_data = True
        elif field_name == "event":
            event_name = value or None
        elif field_name == "id" and "\x00" not in value:
            last_event_id = value
        elif field_name == "retry" and value.isdecimal():
            retry = int(value)

    if saw_data:
        yield SSEEvent(
            data="\n".join(data_lines),
            event=event_name,
            id=last_event_id,
            retry=retry,
            raw="\n".join(raw_lines),
        )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return _content_text(content.get("content"))
    if isinstance(content, list):
        return "".join(_content_text(part) for part in content)
    return ""


def _complete_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""

    output_text = value.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = value.get("output")
    if isinstance(output, list):
        text = "".join(
            _content_text(item.get("content"))
            for item in output
            if isinstance(item, Mapping)
        )
        if text:
            return text

    choices = value.get("choices")
    if isinstance(choices, list):
        text_parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            message = choice.get("message")
            if isinstance(message, Mapping):
                text_parts.append(_content_text(message.get("content")))
            elif isinstance(choice.get("text"), str):
                text_parts.append(choice["text"])
        if any(text_parts):
            return "".join(text_parts)

    response = value.get("response")
    if isinstance(response, Mapping):
        text = _complete_text(response)
        if text:
            return text

    message = value.get("message")
    if isinstance(message, Mapping):
        text = _content_text(message.get("content"))
        if text:
            return text

    content = value.get("content")
    if isinstance(content, (str, list, Mapping)):
        text = _content_text(content)
        if text:
            return text

    text = value.get("text")
    return text if isinstance(text, str) else ""


def _delta_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None

    choices = value.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                parts.append(_content_text(delta.get("content")))
            elif isinstance(delta, str):
                parts.append(delta)
        if parts:
            return "".join(parts)

    event_type = value.get("type")
    delta = value.get("delta")
    if isinstance(delta, str):
        if not isinstance(event_type, str) or event_type.endswith(".delta"):
            return delta
    if isinstance(delta, Mapping):
        text = _content_text(delta)
        if text:
            return text
    if isinstance(event_type, str) and event_type.endswith(".delta"):
        text = value.get("text")
        if isinstance(text, str):
            return text
    if event_type is None and "content" in value:
        text = _content_text(value.get("content"))
        if text:
            return text
    return None


def _stream_text(payloads: list[Any]) -> str:
    deltas = [part for payload in payloads if (part := _delta_text(payload)) is not None]
    if deltas:
        return "".join(deltas)
    for payload in reversed(payloads):
        text = _complete_text(payload)
        if text:
            return text
    return ""


def _extract_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        return dict(usage)
    response = value.get("response")
    if isinstance(response, Mapping):
        return _extract_usage(response)
    return None


def _extract_payload_request_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("request_id", "requestId", "request-id", "_request_id"):
        request_id = value.get(key)
        if isinstance(request_id, str) and request_id:
            return request_id
    response = value.get("response")
    if isinstance(response, Mapping):
        request_id = _extract_payload_request_id(response)
        if request_id:
            return request_id
    response_id = value.get("id")
    if isinstance(response_id, str) and response_id:
        return response_id
    return None


def _decode_sse_data(event: SSEEvent) -> Any:
    try:
        return event.json()
    except json.JSONDecodeError:
        return event.data


class FCCClient:
    """Client for the health, models and Responses API endpoints."""

    def __init__(
        self,
        server_root: str = DEFAULT_FCC_SERVER_ROOT,
        *,
        base_url: str | None = None,
        proxy_auth_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if base_url is not None:
            if server_root != DEFAULT_FCC_SERVER_ROOT:
                raise ValueError("Use either server_root or base_url, not both")
            server_root = base_url
        if proxy_auth_token == "":
            raise ValueError("proxy_auth_token cannot be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        parsed = urlsplit(server_root)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("FCC server_root must use http or https")
        if not parsed.hostname:
            raise ValueError("FCC server_root must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("FCC server_root must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("FCC server_root must not contain a query or fragment")

        root_path = parsed.path.rstrip("/")
        if root_path.endswith("/v1"):
            root_path = root_path[:-3]

        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.base_path = root_path
        self.timeout = float(timeout)
        self.server_root = server_root.rstrip("/")
        self._proxy_auth_token = proxy_auth_token

    def _endpoint(self, path: str) -> str:
        return f"{self.base_path}{path}"

    def _connection(self) -> http.client.HTTPConnection:
        connection_type = (
            http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        )
        return connection_type(self.host, self.port, timeout=self.timeout)

    def _headers(self, *, accept: str | None = None, json_body: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._proxy_auth_token:
            token = self._proxy_auth_token
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        return headers

    def _sanitize(self, value: str | None) -> str | None:
        if value is None:
            return None
        sanitized = value
        if self._proxy_auth_token:
            sanitized = sanitized.replace(self._proxy_auth_token, "[REDACTED]")
        sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
        return _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", sanitized)

    @staticmethod
    def _header(response: http.client.HTTPResponse, name: str) -> str | None:
        value = response.getheader(name)
        return value if isinstance(value, str) and value else None

    def _response_request_id(self, response: http.client.HTTPResponse) -> str | None:
        for header_name in _REQUEST_ID_HEADERS:
            value = self._header(response, header_name)
            if value:
                return self._sanitize(value)
        return None

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse, str | None]:
        connection = self._connection()
        try:
            connection.request(method, self._endpoint(path), body=body, headers=dict(headers or {}))
            response = connection.getresponse()
        except (OSError, TimeoutError, http.client.HTTPException):
            connection.close()
            raise FCCTransportError("FCC request failed") from None

        request_id = self._response_request_id(response)
        if not 200 <= response.status < 300:
            try:
                error_body = response.read().decode("utf-8", errors="replace")
            except (OSError, http.client.HTTPException):
                error_body = ""
            connection.close()
            raise FCCHTTPError(
                "FCC returned an error",
                status=response.status,
                body=self._sanitize(error_body),
                request_id=request_id,
            )
        return connection, response, request_id

    @staticmethod
    def _read_body(response: http.client.HTTPResponse, request_id: str | None) -> bytes:
        try:
            return response.read()
        except (OSError, TimeoutError, http.client.HTTPException):
            raise FCCTransportError("FCC response read failed", request_id=request_id) from None

    def health_check(self) -> bool:
        """Return whether the gateway answered GET /health successfully."""

        connection: http.client.HTTPConnection | None = None
        try:
            connection, response, request_id = self._send(
                "GET", "/health", headers=self._headers(accept="application/json")
            )
            self._read_body(response, request_id)
            return True
        except FCCError:
            return False
        finally:
            if connection is not None:
                connection.close()

    def list_models(self) -> dict[str, Any]:
        """Return the decoded response from GET /v1/models."""

        connection, response, request_id = self._send(
            "GET", "/v1/models", headers=self._headers(accept="application/json")
        )
        try:
            body = self._read_body(response, request_id)
        finally:
            connection.close()
        text = body.decode("utf-8", errors="replace")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise FCCProtocolError(
                "FCC models response was not valid JSON",
                status=response.status,
                body=self._sanitize(text),
                request_id=request_id,
            ) from None
        if not isinstance(decoded, dict):
            raise FCCProtocolError(
                "FCC models response must be a JSON object",
                status=response.status,
                body=self._sanitize(text),
                request_id=request_id,
            )
        return decoded

    def _stream_response(
        self,
        response: http.client.HTTPResponse,
        header_request_id: str | None,
    ) -> FCCResponse:
        def chunks() -> Iterator[bytes]:
            while True:
                try:
                    chunk = response.read(8192)
                except (OSError, TimeoutError, http.client.HTTPException):
                    raise FCCTransportError(
                        "FCC response stream failed", request_id=header_request_id
                    ) from None
                if not chunk:
                    return
                yield chunk

        events: list[SSEEvent] = []
        payloads: list[Any] = []
        for event in iter_sse_events(chunks()):
            if event.data.strip() == "[DONE]":
                break
            events.append(event)
            payloads.append(_decode_sse_data(event))

        usage = next(
            (usage for payload in reversed(payloads) if (usage := _extract_usage(payload))),
            None,
        )
        payload_request_id = next(
            (
                request_id
                for payload in payloads
                if (request_id := _extract_payload_request_id(payload))
            ),
            None,
        )
        event_request_id = next((event.id for event in reversed(events) if event.id), None)
        return FCCResponse(
            text=_stream_text(payloads),
            usage=usage,
            request_id=header_request_id or payload_request_id or event_request_id,
            raw=payloads,
            events=events,
        )

    def _json_response(
        self,
        response: http.client.HTTPResponse,
        header_request_id: str | None,
    ) -> FCCResponse:
        body = self._read_body(response, header_request_id)
        text = body.decode("utf-8", errors="replace")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise FCCProtocolError(
                "FCC response was not valid JSON",
                status=response.status,
                body=self._sanitize(text),
                request_id=header_request_id,
            ) from None
        return FCCResponse(
            text=_complete_text(decoded),
            usage=_extract_usage(decoded),
            request_id=header_request_id or _extract_payload_request_id(decoded),
            raw=decoded,
        )

    def create_response(
        self,
        model: str,
        input: Any,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> FCCResponse:
        """Create one Responses-style completion and collect its result."""

        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        payload = {"model": model, "input": input, "stream": bool(stream), **kwargs}
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise FCCProtocolError("FCC request payload is not JSON serializable") from None

        accept = "text/event-stream" if stream else "application/json"
        connection, response, request_id = self._send(
            "POST",
            "/v1/responses",
            body=body,
            headers=self._headers(accept=accept, json_body=True),
        )
        try:
            content_type = self._header(response, "content-type") or ""
            if "text/event-stream" in content_type.lower() or (
                stream and "application/json" not in content_type.lower()
            ):
                return self._stream_response(response, request_id)
            return self._json_response(response, request_id)
        finally:
            connection.close()

    @staticmethod
    def _compat_event(event: SSEEvent) -> dict[str, Any]:
        decoded = _decode_sse_data(event)
        if isinstance(decoded, Mapping):
            compatible = dict(decoded)
            delta = _delta_text(decoded)
            if delta is not None and not isinstance(compatible.get("delta"), str):
                compatible["delta"] = delta
            usage = _extract_usage(decoded)
            if usage is not None and "usage" not in compatible:
                compatible["usage"] = usage
            request_id = _extract_payload_request_id(decoded) or event.id
            if request_id and "request_id" not in compatible:
                compatible["request_id"] = request_id
            return compatible
        compatible = {"raw": decoded}
        if event.event:
            compatible["event"] = event.event
        if event.id:
            compatible["id"] = event.id
        return compatible

    def post_responses(
        self,
        model: str,
        messages: Any = None,
        stream: bool = True,
        *,
        input: Any = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """Compatibility iterator for callers that still pass Chat-style messages."""

        if messages is not None and input is not None:
            raise ValueError("Use either messages or input, not both")
        response_input = input if input is not None else messages
        if response_input is None:
            raise ValueError("messages or input is required")

        response = self.create_response(model, response_input, stream=stream, **kwargs)
        if response.events:
            for event in response.events:
                yield self._compat_event(event)
            return
        if isinstance(response.raw, Mapping):
            compatible = dict(response.raw)
            if response.text and "content" not in compatible:
                compatible["content"] = response.text
            if response.usage is not None and "usage" not in compatible:
                compatible["usage"] = response.usage
            if response.request_id and "request_id" not in compatible:
                compatible["request_id"] = response.request_id
            yield compatible
        else:
            yield {
                "content": response.text,
                "usage": response.usage,
                "request_id": response.request_id,
                "raw": response.raw,
            }
