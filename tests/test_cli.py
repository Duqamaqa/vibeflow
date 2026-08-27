import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from src.vibeflow.benchmark import BenchmarkResponse
from src.vibeflow.cli import CLIDependencies, main
from src.vibeflow.safety import DirtyState, validate_repo_scope
from src.vibeflow.telemetry import Telemetry
from src.vibeflow.verifier import VerificationCommand


class FakeSafetyGuard:
    def __init__(self, repo_root):
        self.repo_root = Path(repo_root).resolve()

    def validate_path(self, candidate, allow_protected=False):
        return validate_repo_scope(
            self.repo_root,
            candidate,
            allow_protected=allow_protected,
        )

    def dirty_state(self):
        return DirtyState(
            available=True,
            is_repository=True,
            dirty=False,
        )


class FakeVerifier:
    def __init__(self, repo_root):
        self.repo_root = Path(repo_root)
        self.command = VerificationCommand(
            "tests",
            "unittest",
            ("python3", "-m", "unittest"),
            "tests/test*.py",
        )

    def discover_commands(self):
        return (self.command,)

    def command_availability(self, command):
        return True, "available"


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name).resolve()
        (self.repo_root / ".ai").mkdir()
        (self.repo_root / ".ai" / "routing.toml").write_text(
            """
[tiers.cheap]
model = "model/cheap"

[tiers.standard]
model = "model/standard"

[tiers.strong]
model = "model/strong"
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def dependencies(self, **overrides):
        values = {
            "verifier_factory": lambda root: FakeVerifier(root),
            "safety_factory": lambda root: FakeSafetyGuard(root),
            "fcc_health_checker": lambda: {
                "healthy": True,
                "server_root": "http://127.0.0.1:8082",
            },
            "live_model_lister": lambda: {
                "data": [
                    {"id": "model/cheap"},
                    {"id": "model/standard"},
                    {"id": "model/strong"},
                ]
            },
        }
        values.update(overrides)
        return CLIDependencies(**values)

    def invoke(self, argv, dependencies=None, stdin=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = main(
            argv,
            dependencies=dependencies or self.dependencies(),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
        return exit_code, output, error


class TestCLIEntrypoint(unittest.TestCase):
    def test_python_module_entrypoint_exposes_daily_commands(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "vibeflow", "--help"],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("chat", completed.stdout)
        self.assertIn("run", completed.stdout)
        self.assertIn("doctor", completed.stdout)
        self.assertIn("web", completed.stdout)


class TestWebCommand(CLITestCase):
    def test_web_starts_injected_loopback_dashboard(self):
        dashboard_server = Mock()
        dependencies = self.dependencies(dashboard_server=dashboard_server)

        exit_code, output, error = self.invoke(
            [
                "web",
                "--repo",
                str(self.repo_root),
                "--port",
                "9876",
                "--no-open",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertIsNone(output)
        self.assertIsNone(error)
        dashboard_server.assert_called_once()
        call = dashboard_server.call_args.args
        self.assertEqual(call[:4], (self.repo_root, "127.0.0.1", 9876, False))


class TestInitCommand(CLITestCase):
    def test_init_creates_once_without_overwrite(self):
        config_path = self.repo_root / ".ai" / "vibeflow.toml"

        first_code, first, first_error = self.invoke(
            ["init", "--repo", str(self.repo_root)]
        )
        original = config_path.read_text(encoding="utf-8")
        second_code, second, second_error = self.invoke(
            ["init", "--repo", str(self.repo_root)]
        )

        self.assertEqual(first_code, 0)
        self.assertEqual(first["status"], "created")
        self.assertFalse(first["auto_commit"])
        self.assertEqual(second_code, 0)
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(first["default_mode"], "autonomous")
        self.assertEqual(config_path.read_text(encoding="utf-8"), original)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)


class TestDoctorAndModelsCommands(CLITestCase):
    def test_doctor_uses_injected_read_only_dependencies(self):
        exit_code, output, error = self.invoke(
            ["doctor", "--repo", str(self.repo_root)]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(output["ok"])
        self.assertEqual(
            output["verification_commands"][0]["argv"],
            ["python3", "-m", "unittest"],
        )
        self.assertFalse(output["dirty_state"]["dirty"])
        self.assertIsNone(error)

    def test_models_are_local_by_default(self):
        live_lister = Mock(return_value={
            "models": [
                {"slug": "model/cheap"},
                {"slug": "model/standard"},
                {"slug": "model/strong"},
            ]
        })
        dependencies = self.dependencies(live_model_lister=live_lister)

        exit_code, output, error = self.invoke(
            ["models", "--repo", str(self.repo_root)],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output["models"],
            ["model/cheap", "model/standard", "model/strong"],
        )
        live_lister.assert_not_called()
        self.assertIsNone(error)

    def test_models_live_flag_calls_injected_lister(self):
        live_lister = Mock(return_value={
            "models": [
                {"slug": "model/cheap"},
                {"slug": "model/standard"},
                {"slug": "model/strong"},
            ]
        })
        dependencies = self.dependencies(live_model_lister=live_lister)

        exit_code, output, error = self.invoke(
            ["models", "--repo", str(self.repo_root), "--live"],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["source"], "live")
        self.assertEqual(
            output["models"],
            ["model/cheap", "model/standard", "model/strong"],
        )
        self.assertEqual(output["catalog_count"], 3)
        self.assertEqual(output["resolved_tiers"]["strong"], "model/strong")
        live_lister.assert_called_once_with()
        self.assertIsNone(error)


class TestRunAndPlanCommands(CLITestCase):
    def test_run_dry_run_is_explicit(self):
        task_runner = Mock(return_value={"success": True})
        dependencies = self.dependencies(task_runner=task_runner)

        exit_code, output, error = self.invoke(
            ["run", "--repo", str(self.repo_root), "--dry-run", "fix", "the", "parser"],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "dry_run")
        self.assertFalse(output["live_calls_made"])
        self.assertEqual(output["goal"], "fix the parser")
        self.assertEqual(output["blocked_automatic_actions"], ["commit", "deploy"])
        task_runner.assert_not_called()
        self.assertIsNone(error)

    def test_run_live_flag_calls_injected_runner(self):
        task_runner = Mock(return_value={"success": True, "task_id": "task-1"})
        dependencies = self.dependencies(task_runner=task_runner)

        exit_code, output, error = self.invoke(
            [
                "run",
                "--repo",
                str(self.repo_root),
                "--live",
                "--context",
                "src/new.py",
                "fix parser",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "done")
        self.assertTrue(output["live_calls_made"])
        task_runner.assert_called_once_with(
            "fix parser",
            self.repo_root,
            ("src/new.py",),
            False,
        )
        self.assertIsNone(error)

    def test_plan_uses_local_orchestrator_planner_by_default(self):
        planner = Mock(return_value={"automatic_commit": False, "subtasks": ["task"]})
        dependencies = self.dependencies(task_planner=planner)

        exit_code, output, error = self.invoke(
            [
                "plan",
                "--repo",
                str(self.repo_root),
                "--constraint",
                "stdlib only",
                "--acceptance",
                "tests pass",
                "add verifier",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "planned")
        self.assertFalse(output["live_calls_made"])
        self.assertFalse(output["plan"]["automatic_commit"])
        planner.assert_called_once_with(
            "add verifier",
            self.repo_root,
            ("stdlib only",),
            ("tests pass",),
        )
        self.assertIsNone(error)

    def test_blocked_task_result_is_not_reported_as_success(self):
        class BlockedResult:
            success = False

            class Status:
                value = "blocked"

            status = Status()

            def to_dict(self):
                return {"success": False, "status": "blocked", "blocker": "review rejected"}

        dependencies = self.dependencies(task_runner=lambda *_: BlockedResult())

        exit_code, output, error = self.invoke(
            ["run", "--repo", str(self.repo_root), "--live", "fix parser"],
            dependencies,
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(output["ok"])
        self.assertEqual(output["status"], "blocked")
        self.assertIsNone(error)

    def test_protected_context_is_rejected_before_live_runner(self):
        task_runner = Mock(return_value={"success": True})
        dependencies = self.dependencies(task_runner=task_runner)

        exit_code, output, error = self.invoke(
            [
                "run",
                "--repo",
                str(self.repo_root),
                "--live",
                "--context",
                ".env",
                "read secret",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 1)
        self.assertIsNone(output)
        self.assertFalse(error["ok"])
        self.assertIn("protected", error["error"])
        task_runner.assert_not_called()

    def test_chat_reads_prompts_until_quit(self):
        task_runner = Mock(return_value={"success": True, "status": "done"})
        dependencies = self.dependencies(task_runner=task_runner)
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = main(
            ["chat", "--repo", str(self.repo_root)],
            dependencies=dependencies,
            stdin=io.StringIO("fix parser\n/quit\n"),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("fix parser", stdout.getvalue())
        self.assertIn('"status": "done"', stdout.getvalue())
        task_runner.assert_called_once_with("fix parser", self.repo_root, (), False)
        self.assertEqual(stderr.getvalue(), "")


class TestBenchmarkCommand(CLITestCase):
    def test_benchmark_is_dry_by_default(self):
        provider = Mock(
            return_value=BenchmarkResponse(
                "range length index",
                input_tokens=3,
                output_tokens=3,
            )
        )
        dependencies = self.dependencies(benchmark_provider=provider)

        exit_code, output, error = self.invoke(
            [
                "benchmark",
                "--repo",
                str(self.repo_root),
                "--model",
                "model/test",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(output["report"]["dry_run"])
        self.assertFalse(output["report"]["live_calls_made"])
        provider.assert_not_called()
        self.assertIsNone(error)

    def test_benchmark_live_flag_calls_injected_provider(self):
        provider = Mock(
            return_value=BenchmarkResponse(
                "range length index empty boundary error rollback observability risk",
                input_tokens=9,
                output_tokens=9,
            )
        )
        dependencies = self.dependencies(benchmark_provider=provider)

        exit_code, output, error = self.invoke(
            [
                "benchmark",
                "--repo",
                str(self.repo_root),
                "--model",
                "model/test",
                "--live",
            ],
            dependencies,
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(output["report"]["dry_run"])
        self.assertTrue(output["report"]["live_calls_made"])
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(output["report"]["tier_mappings"]["strong"], "model/test")
        self.assertIsNone(error)


class TestTelemetryCommand(CLITestCase):
    def test_report_alias_reads_sqlite_telemetry(self):
        telemetry_dir = self.repo_root / ".vibeflow"
        Telemetry(telemetry_dir).log_event(
            task_id="task-1",
            agent_role="worker",
            model="model/test",
            tier="standard",
            usage={"input_tokens": 10, "output_tokens": 5},
            duration=0.25,
            request_id="req-1",
            success=True,
            api_key="supersecret",
        )

        exit_code, output, error = self.invoke(
            ["report", "--repo", str(self.repo_root)]
        )

        self.assertEqual(exit_code, 0)
        summary = output["report"]["summary"]
        self.assertEqual(summary["events"], 1)
        self.assertEqual(summary["input_tokens"], 10)
        self.assertNotIn("supersecret", json.dumps(output))
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
