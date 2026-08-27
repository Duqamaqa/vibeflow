import json
import unittest
from unittest.mock import MagicMock, patch

from src.vibeflow.fcc_client import (
    FCCClient,
    FCCHTTPError,
    FCCResponse,
    SSEEvent,
    iter_sse_events,
)


class FakeResponse:
    def __init__(self, *, status=200, body=b"", chunks=None, headers=None):
        self.status = status
        self._body = body
        self._chunks = iter(chunks) if chunks is not None else None
        self._headers = {key.lower(): value for key, value in (headers or {}).items()}

    def getheader(self, name):
        return self._headers.get(name.lower())

    def read(self, amount=None):
        if amount is None:
            if self._chunks is not None:
                return b"".join(self._chunks)
            body, self._body = self._body, b""
            return body
        if self._chunks is not None:
            return next(self._chunks, b"")
        body, self._body = self._body, b""
        return body


def mock_connection(response):
    connection = MagicMock()
    connection.getresponse.return_value = response
    return patch(
        "src.vibeflow.fcc_client.http.client.HTTPConnection",
        return_value=connection,
    ), connection


class TestSSEParser(unittest.TestCase):
    def test_fragmented_multiline_fields_and_terminal_flush(self):
        wire = (
            b"\xef\xbb\xbf: keepalive\r\n"
            b"event: response.output_text.delta\r\n"
            b"id: evt-1\r\n"
            b"retry: 250\r\n"
            b'data: {"type":"response.output_text.delta",\r\n'
            b'data: "delta":"H\xc3\xa9"}\r\n\r\n'
            b"event: custom\n"
            b"id: evt-2\n"
            b"data: line one\n"
            b"data: line two"
        )
        chunks = [wire[index : index + 3] for index in range(0, len(wire), 3)]

        events = list(iter_sse_events(chunks))

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event, "response.output_text.delta")
        self.assertEqual(events[0].id, "evt-1")
        self.assertEqual(events[0].retry, 250)
        self.assertEqual(events[0].json()["delta"], "Hé")
        self.assertEqual(events[1].event, "custom")
        self.assertEqual(events[1].id, "evt-2")
        self.assertEqual(events[1].data, "line one\nline two")


class TestFCCClient(unittest.TestCase):
    def test_exact_health_and_models_paths(self):
        health_response = FakeResponse(body=b'{"ok":true}')
        patcher, connection = mock_connection(health_response)
        with patcher as connection_type:
            client = FCCClient()
            self.assertTrue(client.health_check())

        connection_type.assert_called_once_with("127.0.0.1", 8082, timeout=30.0)
        connection.request.assert_called_once_with(
            "GET",
            "/health",
            body=None,
            headers={"Accept": "application/json"},
        )

        models_response = FakeResponse(body=b'{"models":[{"id":"provider/model"}]}')
        patcher, connection = mock_connection(models_response)
        with patcher:
            client = FCCClient(base_url="http://gateway.local:9090/v1")
            models = client.list_models()

        self.assertEqual(models["models"][0]["id"], "provider/model")
        connection.request.assert_called_once_with(
            "GET",
            "/v1/models",
            body=None,
            headers={"Accept": "application/json"},
        )

    def test_create_response_extracts_json_and_keeps_auth_out_of_payload(self):
        secret = "proxy-secret-value"
        raw_response = {
            "id": "resp_body",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Hello"},
                        {"type": "output_text", "text": " world"},
                    ],
                }
            ],
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        }
        response = FakeResponse(
            body=json.dumps(raw_response).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": "req_header",
            },
        )
        patcher, connection = mock_connection(response)
        response_input = [{"role": "user", "content": "Say hello"}]

        with patcher:
            result = FCCClient(proxy_auth_token=secret).create_response(
                "provider/model",
                response_input,
                temperature=0,
            )

        self.assertIsInstance(result, FCCResponse)
        self.assertEqual(result.text, "Hello world")
        self.assertEqual(result.output_text, "Hello world")
        self.assertEqual(result.usage["total_tokens"], 6)
        self.assertEqual(result.request_id, "req_header")
        self.assertEqual(result.raw, raw_response)
        self.assertEqual(result.events, [])

        method, path = connection.request.call_args.args[:2]
        request_body = connection.request.call_args.kwargs["body"]
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual((method, path), ("POST", "/v1/responses"))
        self.assertEqual(
            json.loads(request_body),
            {
                "model": "provider/model",
                "input": response_input,
                "stream": False,
                "temperature": 0,
            },
        )
        self.assertNotIn(secret, request_body.decode())
        self.assertEqual(headers["Authorization"], f"Bearer {secret}")
        self.assertNotIn(secret, path)
        self.assertTrue(
            all(
                secret not in value
                for name, value in headers.items()
                if name != "Authorization"
            )
        )

    def test_create_response_collects_fragmented_sse(self):
        wire = (
            b"event: response.created\n"
            b'data: {"type":"response.created","response":{"id":"resp_sse"}}\n\n'
            b"event: response.output_text.delta\n"
            b"id: event-1\n"
            b'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
            b"event: response.output_text.delta\n"
            b"id: event-2\n"
            b'data: {"type":"response.output_text.delta","delta":" world"}\n\n'
            b"event: response.completed\n"
            b'data: {"type":"response.completed","response":{"id":"resp_sse",\n'
            b'data: "usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}}'
        )
        chunks = [wire[:11], wire[11:37], wire[37:93], wire[93:181], wire[181:]]
        response = FakeResponse(
            chunks=chunks,
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
        )
        patcher, connection = mock_connection(response)

        with patcher:
            result = FCCClient().create_response(
                "provider/model",
                "hello",
                stream=True,
            )

        self.assertEqual(result.text, "Hello world")
        self.assertEqual(
            result.usage,
            {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        )
        self.assertEqual(result.request_id, "resp_sse")
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[1].event, "response.output_text.delta")
        self.assertEqual(result.events[1].id, "event-1")
        self.assertEqual(len(result.raw), 4)
        self.assertEqual(
            connection.request.call_args.kwargs["headers"]["Accept"],
            "text/event-stream",
        )

    def test_post_responses_preserves_messages_compatibility(self):
        events = [
            SSEEvent(
                event="response.output_text.delta",
                data='{"type":"response.output_text.delta","delta":"Hi"}',
            ),
            SSEEvent(
                event="response.completed",
                data=(
                    '{"type":"response.completed","response":{"id":"resp_1",'
                    '"usage":{"input_tokens":2,"output_tokens":1}}}'
                ),
            ),
        ]
        collected = FCCResponse(
            text="Hi",
            usage={"input_tokens": 2, "output_tokens": 1},
            request_id="resp_1",
            raw=[],
            events=events,
        )
        client = FCCClient()
        messages = [{"role": "user", "content": "hello"}]

        with patch.object(client, "create_response", return_value=collected) as create:
            chunks = list(
                client.post_responses(
                    "provider/model",
                    messages=messages,
                    stream=True,
                    max_output_tokens=12,
                )
            )

        create.assert_called_once_with(
            "provider/model",
            messages,
            stream=True,
            max_output_tokens=12,
        )
        self.assertEqual(chunks[0]["delta"], "Hi")
        self.assertEqual(chunks[1]["request_id"], "resp_1")
        self.assertEqual(chunks[1]["usage"]["output_tokens"], 1)

    def test_http_error_is_structured_and_redacts_proxy_token(self):
        secret = "do-not-leak"
        response = FakeResponse(
            status=401,
            body=(
                '{"error":"denied","Authorization":"Bearer '
                + secret
                + '","echo":"'
                + secret
                + '"}'
            ).encode(),
            headers={"X-Request-Id": "req_denied"},
        )
        patcher, connection = mock_connection(response)

        with patcher:
            with self.assertRaises(FCCHTTPError) as raised:
                FCCClient(proxy_auth_token=secret).list_models()

        error = raised.exception
        self.assertEqual(error.status, 401)
        self.assertEqual(error.request_id, "req_denied")
        self.assertIn("[REDACTED]", error.body)
        self.assertNotIn(secret, error.body)
        self.assertNotIn(secret, str(error))
        self.assertEqual(
            connection.request.call_args.kwargs["headers"]["Authorization"],
            f"Bearer {secret}",
        )


if __name__ == "__main__":
    unittest.main()
