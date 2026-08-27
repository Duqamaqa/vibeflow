import unittest

from src.vibeflow.browser import (
    BrowserAction,
    BrowserActionResult,
    BrowserController,
    BrowserOperation,
    BrowserPolicy,
    BrowserSession,
)


class RecordingBrowserPlugin:
    def __init__(self):
        self.opened = []
        self.executed = []
        self.closed = []

    def open_session(self, session):
        self.opened.append(session)

    def execute(self, session, action):
        self.executed.append((session, action))
        return BrowserActionResult(True, {"operation": action.operation.value})

    def close_session(self, session):
        self.closed.append(session)


class TestBrowserController(unittest.TestCase):
    def setUp(self):
        self.plugin = RecordingBrowserPlugin()
        self.controller = BrowserController(self.plugin)
        self.session = self.controller.open_session(
            workspace_id="workspace-a",
            session_id="session-a",
        )

    def test_backend_is_injected_and_receives_isolated_context(self):
        result = self.controller.execute(
            self.session,
            BrowserAction(BrowserOperation.NAVIGATE, "https://example.com/path"),
        )

        self.assertTrue(result.success)
        self.assertEqual(self.plugin.opened, [self.session])
        self.assertEqual(self.plugin.executed[0][0].workspace_id, "workspace-a")
        self.assertEqual(result.data["operation"], "navigate")

    def test_forged_workspace_context_is_rejected_before_plugin(self):
        forged = BrowserSession("workspace-b", "session-a")

        with self.assertRaises(ValueError):
            self.controller.execute(forged, BrowserAction("read"))

        self.assertEqual(self.plugin.executed, [])

    def test_unsafe_navigation_schemes_are_rejected(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/plain,x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.controller.execute(
                    self.session,
                    BrowserAction(BrowserOperation.NAVIGATE, url),
                )
        self.assertEqual(self.plugin.executed, [])

    def test_host_allowlist_is_enforced(self):
        plugin = RecordingBrowserPlugin()
        controller = BrowserController(
            plugin,
            policy=BrowserPolicy(allowed_hosts=frozenset({"example.com"})),
        )
        session = controller.open_session(
            workspace_id="workspace-a",
            session_id="allowlisted",
        )

        controller.execute(
            session,
            BrowserAction("navigate", "https://docs.example.com"),
        )
        with self.assertRaises(ValueError):
            controller.execute(
                session,
                BrowserAction("navigate", "https://example.org"),
            )
        self.assertEqual(len(plugin.executed), 1)

    def test_actions_are_schema_validated(self):
        with self.assertRaises(ValueError):
            self.controller.execute(self.session, BrowserAction("click"))
        with self.assertRaises(ValueError):
            self.controller.execute(
                self.session,
                BrowserAction("type", target="#query"),
            )
        self.assertEqual(self.plugin.executed, [])

    def test_sessions_cannot_be_reused_or_used_after_close(self):
        with self.assertRaises(ValueError):
            self.controller.open_session(
                workspace_id="workspace-b",
                session_id="session-a",
            )

        self.controller.close_session(self.session)
        with self.assertRaises(KeyError):
            self.controller.execute(self.session, BrowserAction("read"))
        self.assertEqual(self.plugin.closed, [self.session])


if __name__ == "__main__":
    unittest.main()

