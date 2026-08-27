import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from src.vibeflow.verifier import (
    AcceptancePolicy,
    CheckStatus,
    VerificationCommand,
    VerificationResult,
    Verifier,
)


class TemporaryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, relative_path, content):
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class TestCommandDiscovery(TemporaryRepositoryTest):
    def test_discovers_python_tools_from_project_files(self):
        self.write(
            "pyproject.toml",
            """
[project]
name = "sample"
version = "0.1.0"

[project.optional-dependencies]
test = ["pytest"]
dev = ["ruff", "mypy"]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]

[tool.mypy]
""",
        )
        self.write("tests/test_sample.py", "def test_sample():\n    assert True\n")

        commands = Verifier(self.repo_root).discover_commands()
        by_tool = {command.tool: command for command in commands}

        self.assertEqual(by_tool["pytest"].argv[:3], (sys.executable, "-m", "pytest"))
        self.assertEqual(by_tool["ruff"].category, "lint")
        self.assertEqual(by_tool["mypy"].category, "typecheck")
        self.assertEqual(by_tool["build"].category, "build")

    def test_discovers_unittest_from_test_sources(self):
        self.write(
            "tests/test_sample.py",
            "import unittest\n\nclass Sample(unittest.TestCase):\n    pass\n",
        )

        commands = Verifier(self.repo_root).discover_commands()

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].tool, "unittest")
        self.assertEqual(
            commands[0].argv,
            (sys.executable, "-m", "unittest", "discover", "-s", "tests"),
        )

    def test_discovers_package_scripts_without_executing_script_text(self):
        self.write(
            "package.json",
            json.dumps(
                {
                    "scripts": {
                        "test": "node test.js && echo unsafe",
                        "lint": "eslint .",
                        "typecheck": "tsc --noEmit",
                        "build": "vite build",
                    }
                }
            ),
        )

        commands = Verifier(self.repo_root).discover_commands()

        self.assertEqual(
            [command.argv for command in commands],
            [
                ("npm", "run", "test"),
                ("npm", "run", "lint"),
                ("npm", "run", "typecheck"),
                ("npm", "run", "build"),
            ],
        )

    def test_custom_commands_require_argv_arrays(self):
        self.write(
            "pyproject.toml",
            """
[tool.vibeflow.commands]
tests = ["python3", "-m", "unittest"]
lint = "ruff check ."
""",
        )

        commands = Verifier(self.repo_root).discover_commands()

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].argv, ("python3", "-m", "unittest"))


class TestCommandExecution(TemporaryRepositoryTest):
    def setUp(self):
        super().setUp()
        self.write(
            "pyproject.toml",
            """
[tool.vibeflow.commands]
tests = ["test-tool", "--suite", "unit"]
""",
        )

    def test_uses_argv_only_and_captures_redacted_streams(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="all passed api_key=alpha",
                stderr="diagnostic",
            )

        clock = iter((10.0, 10.25))
        verifier = Verifier(
            self.repo_root,
            timeout_seconds=12,
            runner=runner,
            which=lambda command: "/bin/test-tool",
            clock=lambda: next(clock),
        )

        report = verifier.verify()

        self.assertTrue(report.accepted)
        test_result = report.by_category("tests")[0]
        self.assertEqual(test_result.status, CheckStatus.PASSED)
        self.assertNotIn("alpha", test_result.stdout)
        self.assertEqual(test_result.stderr, "diagnostic")
        self.assertEqual(test_result.duration_seconds, 0.25)
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["test-tool", "--suite", "unit"])
        self.assertEqual(kwargs["cwd"], str(self.repo_root))
        self.assertEqual(kwargs["timeout"], 12)
        self.assertFalse(kwargs["shell"])
        self.assertFalse(kwargs["check"])

    def test_nonzero_exit_is_failed_and_rejected(self):
        verifier = Verifier(
            self.repo_root,
            runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv,
                2,
                stdout="",
                stderr="failure",
            ),
            which=lambda command: "/bin/test-tool",
        )

        report = verifier.verify()

        self.assertFalse(report.accepted)
        result = report.by_category("tests")[0]
        self.assertEqual(result.status, CheckStatus.FAILED)
        self.assertEqual(result.returncode, 2)

    def test_missing_executable_is_unavailable_not_failed(self):
        runner = Mock()
        verifier = Verifier(
            self.repo_root,
            runner=runner,
            which=lambda command: None,
        )

        report = verifier.verify()

        result = report.by_category("tests")[0]
        self.assertEqual(result.status, CheckStatus.UNAVAILABLE)
        self.assertFalse(report.accepted)
        runner.assert_not_called()

    def test_timeout_preserves_partial_output(self):
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(
                argv,
                kwargs["timeout"],
                output="partial",
                stderr="password=hunter2",
            )

        clock = iter((5.0, 8.0))
        verifier = Verifier(
            self.repo_root,
            timeout_seconds=3,
            runner=runner,
            which=lambda command: "/bin/test-tool",
            clock=lambda: next(clock),
        )

        report = verifier.verify()

        result = report.by_category("tests")[0]
        self.assertEqual(result.status, CheckStatus.TIMED_OUT)
        self.assertEqual(result.stdout, "partial")
        self.assertNotIn("hunter2", result.stderr)
        self.assertEqual(result.duration_seconds, 3.0)


class TestAcceptancePolicy(TemporaryRepositoryTest):
    def test_missing_required_tests_are_skipped_and_rejected(self):
        report = Verifier(self.repo_root).verify()

        self.assertFalse(report.accepted)
        self.assertEqual(report.by_category("tests")[0].status, CheckStatus.SKIPPED)
        self.assertIn("required tests check was not discovered", report.decision.reasons)

    def test_optional_unavailable_checks_can_be_allowed(self):
        test_command = VerificationCommand("tests", "unit", ("unit",), "test config")
        lint_command = VerificationCommand("lint", "lint", ("lint",), "lint config")
        results = (
            VerificationResult(
                category="tests",
                status=CheckStatus.PASSED,
                command=test_command,
            ),
            VerificationResult(
                category="lint",
                status=CheckStatus.UNAVAILABLE,
                command=lint_command,
            ),
        )

        strict = AcceptancePolicy().evaluate(results)
        permissive = AcceptancePolicy(allow_unavailable=True).evaluate(results)

        self.assertFalse(strict.accepted)
        self.assertTrue(permissive.accepted)

    def test_required_unavailable_check_never_passes(self):
        command = VerificationCommand("tests", "unit", ("unit",), "test config")
        result = VerificationResult(
            category="tests",
            status=CheckStatus.UNAVAILABLE,
            command=command,
        )

        decision = AcceptancePolicy(allow_unavailable=True).evaluate((result,))

        self.assertFalse(decision.accepted)
        self.assertIn("required tests check did not pass", decision.reasons)


if __name__ == "__main__":
    unittest.main()
