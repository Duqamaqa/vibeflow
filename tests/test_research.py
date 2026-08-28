from types import SimpleNamespace
import unittest

from src.vibeflow.research import OpenRouterResearcher, ResearchError, _extract_sources


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.kwargs = None

    def create_response(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            text=self.text,
            raw=[{
                "type": "url_citation",
                "url_citation": {
                    "title": "Primary source",
                    "url": "https://example.org/evidence",
                },
            }],
            usage={},
            request_id="request-1",
        )


class TestOpenRouterResearcher(unittest.TestCase):
    def test_uses_bounded_online_openrouter_request_and_collects_citations(self):
        client = FakeClient("Finding with [another source](https://example.com/page).")

        result = OpenRouterResearcher(client, max_results=4).research(
            "Find current evidence",
            "open_router/google/gemini-3-flash-preview",
        )

        self.assertEqual(client.kwargs["model"], "open_router/google/gemini-3-flash-preview:online")
        self.assertTrue(client.kwargs["stream"])
        self.assertEqual(client.kwargs["plugins"], [{"id": "web", "max_results": 4}])
        self.assertEqual(
            [source.url for source in result.sources],
            ["https://example.org/evidence", "https://example.com/page"],
        )

    def test_rejects_reports_without_safe_public_sources(self):
        client = FakeClient("Unsupported source http://user:password@example.com/private")
        client.create_response = lambda **kwargs: SimpleNamespace(
            text=client.text,
            raw=[],
            usage={},
            request_id=None,
        )

        with self.assertRaisesRegex(ResearchError, "no safe source URLs"):
            OpenRouterResearcher(client).research(
                "Find evidence",
                "open_router/google/gemini-3-flash-preview",
            )

    def test_source_extraction_deduplicates_and_rejects_credentials(self):
        sources = _extract_sources(
            "[One](https://example.com/a) https://example.com/a "
            "https://user:secret@example.com/private",
            [],
        )

        self.assertEqual([source.url for source in sources], ["https://example.com/a"])


if __name__ == "__main__":
    unittest.main()
