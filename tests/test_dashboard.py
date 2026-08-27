import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from src.vibeflow.dashboard import (
    DashboardService,
    create_server,
)
from src.vibeflow.safety import SafetyViolation
from src.vibeflow.skills import RepositorySkillStore


class FakeFCC:
    def __init__(self, healthy=True):
        self.healthy = healthy

    def health_check(self):
        return self.healthy


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name).resolve()
        (self.repo_root / ".ai").mkdir()
        (self.repo_root / ".ai" / "routing.toml").write_text(
            """
[tiers.cheap]
model = "provider/cheap"

[tiers.standard]
model = "provider/standard"

[tiers.strong]
model = "auto:openai-codex"
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def wait_for_task(self, service, task_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = service.get_task(task_id)
            if task["status"] not in {"queued", "running"}:
                return task
            time.sleep(0.01)
        self.fail("dashboard task did not finish")


class TestDashboardService(DashboardTestCase):
    def test_bootstrap_reports_local_system_without_exposing_credentials(self):
        service = DashboardService(
            self.repo_root,
            fcc_factory=lambda: FakeFCC(True),
        )

        payload = service.bootstrap()

        self.assertTrue(payload["fcc"]["healthy"])
        self.assertEqual(payload["routing"]["tiers"]["cheap"], "provider/cheap")
        self.assertFalse(payload["safety"]["credentials_in_browser"])
        self.assertFalse(payload["safety"]["auto_push"])
        engines = {engine["id"]: engine for engine in payload["engines"]}
        self.assertEqual(engines["context-iceberg"]["activation"], "always-on")
        self.assertEqual(engines["parallel-consensus"]["activation"], "automatic")

    def test_plan_runs_in_background_without_live_worker(self):
        service = DashboardService(
            self.repo_root,
            planner=lambda prompt, root, skills: {
                "status": "planned",
                "goal": prompt,
                "repo": str(root),
                "skills": skills,
            },
        )

        submitted = service.submit("plan", "Add parser tests")
        finished = self.wait_for_task(service, submitted["task_id"])

        self.assertEqual(finished["status"], "planned")
        self.assertEqual(finished["result"]["goal"], "Add parser tests")

    def test_run_persists_redacted_result(self):
        secret = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz"
        service = DashboardService(
            self.repo_root,
            runner=lambda prompt, root, approved, skills: {
                "status": "done",
                "summary": f"used {secret}",
                "approved": approved,
                "skills": skills,
            },
        )

        submitted = service.submit("run", "Fix parser", approved=True)
        finished = self.wait_for_task(service, submitted["task_id"])
        persisted = json.loads(
            (self.repo_root / ".vibeflow" / "last-task.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(finished["status"], "done")
        self.assertNotIn(secret, json.dumps(finished))
        self.assertNotIn(secret, json.dumps(persisted))
        self.assertIn("[REDACTED]", json.dumps(persisted))

    def test_status_persistence_failure_does_not_reverse_completed_task(self):
        service = DashboardService(
            self.repo_root,
            runner=lambda prompt, root, approved, skills: {"status": "done"},
        )
        service._write_last_task = lambda *args: (_ for _ in ()).throw(
            OSError("disk unavailable")
        )

        submitted = service.submit("run", "Fix parser")
        finished = self.wait_for_task(service, submitted["task_id"])

        self.assertEqual(finished["status"], "done")
        self.assertIn("persistence failed", finished["error"])

    def test_rejects_empty_oversized_and_unknown_actions(self):
        service = DashboardService(self.repo_root)

        with self.assertRaises(ValueError):
            service.submit("run", "")
        with self.assertRaises(ValueError):
            service.submit("shell", "do anything")
        with self.assertRaises(ValueError):
            service.submit("run", "x" * 20_001)

    def test_native_picker_returns_selected_repository(self):
        service = DashboardService(
            self.repo_root,
            directory_picker=lambda prompt, initial: self.repo_root,
        )

        selected = service.select_directory("repository")

        self.assertTrue(selected["selected"])
        self.assertEqual(selected["path"], str(self.repo_root))

    def test_repository_setup_requires_git_and_creates_missing_config(self):
        (self.repo_root / ".ai" / "routing.toml").unlink()
        service = DashboardService(self.repo_root)
        with self.assertRaises(SafetyViolation):
            service.initialize()
        subprocess.run(
            ["git", "init", str(self.repo_root)],
            check=True,
            capture_output=True,
            text=True,
        )

        result = service.initialize()

        self.assertEqual(result["status"], "created")
        self.assertTrue((self.repo_root / ".ai" / "routing.toml").is_file())

    def test_selected_repository_skill_reaches_planner(self):
        captured = {}
        RepositorySkillStore(self.repo_root).create(
            name="accessibility",
            description="Apply accessibility checks",
            instructions="Require labels and keyboard navigation.",
            triggers=("accessibility",),
        )
        service = DashboardService(
            self.repo_root,
            planner=lambda prompt, root, skills: captured.update(
                {"skills": skills}
            ) or {"status": "planned", "skills": skills},
        )

        submitted = service.submit(
            "plan",
            "Improve the form",
            selected_skills=["accessibility"],
        )
        finished = self.wait_for_task(service, submitted["task_id"])

        self.assertEqual(finished["status"], "planned")
        self.assertEqual(captured["skills"], ("accessibility",))


class TestDashboardHTTP(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.service = DashboardService(
            self.repo_root,
            planner=lambda prompt, root, skills: {"goal": prompt, "status": "planned"},
            directory_picker=lambda prompt, initial: self.repo_root,
            fcc_factory=lambda: FakeFCC(True),
        )
        self.server = create_server(
            self.repo_root,
            port=0,
            service=self.service,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def test_serves_polished_ui_with_strict_browser_headers(self):
        status, headers, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn(b"YOUR DAILY CODING FRONT DOOR", body)
        self.assertIn(b"prompt-input", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_submit_and_poll_plan_task(self):
        body = json.dumps(
            {"action": "plan", "prompt": "Add tests", "repo": str(self.repo_root)}
        )
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.port}",
        }

        status, _, response_body = self.request("POST", "/api/tasks", body, headers)
        submitted = json.loads(response_body)
        finished = self.wait_for_task(self.service, submitted["task_id"])
        get_status, _, get_body = self.request(
            "GET", f"/api/tasks/{submitted['task_id']}"
        )

        self.assertEqual(status, 202)
        self.assertEqual(finished["status"], "planned")
        self.assertEqual(get_status, 200)
        self.assertEqual(json.loads(get_body)["result"]["goal"], "Add tests")

    def test_picker_and_skill_create_endpoints(self):
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.port}",
        }
        picker_status, _, picker_body = self.request(
            "POST",
            "/api/picker",
            json.dumps({"purpose": "repository", "current": str(self.repo_root)}),
            headers,
        )
        skill_status, _, skill_body = self.request(
            "POST",
            "/api/skills/create",
            json.dumps(
                {
                    "repo": str(self.repo_root),
                    "name": "docs",
                    "description": "Write clear docs",
                    "triggers": ["documentation"],
                    "instructions": "Use plain language.",
                }
            ),
            headers,
        )

        self.assertEqual(picker_status, 200)
        self.assertTrue(json.loads(picker_body)["selected"])
        self.assertEqual(skill_status, 201)
        self.assertEqual(json.loads(skill_body)["skill"]["name"], "docs")

    def test_rejects_cross_origin_post(self):
        body = json.dumps({"action": "run", "prompt": "Change files"})

        status, _, response_body = self.request(
            "POST",
            "/api/tasks",
            body,
            {
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:9999",
            },
        )

        self.assertEqual(status, 403)
        self.assertFalse(json.loads(response_body)["ok"])

    def test_rejects_https_origin_on_plain_http_server(self):
        body = json.dumps({"action": "run", "prompt": "Change files"})

        status, _, _ = self.request(
            "POST",
            "/api/tasks",
            body,
            {
                "Content-Type": "application/json",
                "Origin": f"https://127.0.0.1:{self.port}",
            },
        )

        self.assertEqual(status, 403)

    def test_rejects_non_json_write(self):
        status, _, _ = self.request(
            "POST",
            "/api/tasks",
            "action=run",
            {"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(status, 415)

    def test_rejects_non_loopback_bind(self):
        with self.assertRaises(SafetyViolation):
            create_server(self.repo_root, host="0.0.0.0", port=0)


class TestDashboardAssets(unittest.TestCase):
    def test_frontend_assets_are_local_and_have_accessible_controls(self):
        asset_root = Path(__file__).parents[1] / "src" / "vibeflow" / "ui"
        html = (asset_root / "index.html").read_text(encoding="utf-8")
        css = (asset_root / "app.css").read_text(encoding="utf-8")
        javascript = (asset_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('label class="sr-only" for="prompt-input"', html)
        self.assertIn('id="browse-repo"', html)
        self.assertIn('id="skill-dialog"', html)
        self.assertIn('id="engine-list"', html)
        self.assertIn("Built in—nothing to import", html)
        self.assertIn("Import skill folder", html)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("[hidden] { display: none !important; }", css)
        self.assertNotIn("https://", css)
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)
        self.assertIn('skills: [...state.selectedSkills]', javascript)


if __name__ == "__main__":
    unittest.main()
