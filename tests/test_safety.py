import subprocess
import tempfile
from pathlib import Path
import unittest

from src.vibeflow.safety import (
    REDACTED,
    SafetyGuard,
    SafetyViolation,
    is_protected_path,
    redact_secrets,
    report_dirty_state,
    validate_automated_command,
    validate_repo_scope,
)


class TestRepositoryScope(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name).resolve()
        (self.repo_root / "src").mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_accepts_paths_inside_repository(self):
        resolved = validate_repo_scope(self.repo_root, "src/new.py")
        self.assertEqual(resolved, self.repo_root / "src" / "new.py")

    def test_rejects_parent_traversal(self):
        with self.assertRaises(SafetyViolation):
            validate_repo_scope(self.repo_root, "../outside.py")

    def test_rejects_symlink_escape(self):
        outside = self.repo_root.parent / f"{self.repo_root.name}-outside"
        outside.mkdir()
        try:
            (self.repo_root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(SafetyViolation):
                validate_repo_scope(self.repo_root, "linked/file.py")
        finally:
            outside.rmdir()

    def test_rejects_protected_paths(self):
        self.assertTrue(is_protected_path(".env"))
        self.assertTrue(is_protected_path("nested/private.key"))
        self.assertTrue(is_protected_path(".git/config"))
        with self.assertRaises(SafetyViolation):
            validate_repo_scope(self.repo_root, ".env")

    def test_guard_can_explicitly_read_protected_path(self):
        guard = SafetyGuard(self.repo_root)
        path = guard.validate_path(".env", allow_protected=True)
        self.assertEqual(path, self.repo_root / ".env")


class TestDirtyState(unittest.TestCase):
    def test_reports_dirty_entries_without_mutating(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=" M src/app.py\0?? tests/new.py\0R  dst.py\0src.py\0",
                stderr="",
            )

        state = report_dirty_state(
            "/tmp",
            runner=runner,
            which=lambda command: "/usr/bin/git",
        )

        self.assertTrue(state.available)
        self.assertTrue(state.is_repository)
        self.assertTrue(state.dirty)
        self.assertEqual([entry.path for entry in state.entries], [
            "src/app.py",
            "tests/new.py",
            "dst.py",
        ])
        self.assertEqual(state.entries[-1].original_path, "src.py")
        argv, kwargs = calls[0]
        self.assertEqual(
            argv[:4],
            ["git", "-C", str(Path("/tmp").resolve()), "status"],
        )
        self.assertFalse(kwargs["shell"])
        self.assertFalse(kwargs["check"])

    def test_reports_git_as_unavailable(self):
        state = report_dirty_state("/tmp", which=lambda command: None)
        self.assertFalse(state.available)
        self.assertFalse(state.dirty)
        self.assertIn("unavailable", state.error)


class TestSecretRedaction(unittest.TestCase):
    def test_redacts_common_secret_forms(self):
        source = (
            'api_key="alpha123" password=hunter2 '
            "Authorization: Bearer token-value "
            "https://alice:secret@example.test "
            "sk-proj-" + "abcdefghijklmnop"
        )
        redacted = redact_secrets(source)
        for secret in ("alpha123", "hunter2", "token-value", "alice:secret", "sk-proj-"):
            self.assertNotIn(secret, redacted)
        self.assertIn(REDACTED, redacted)

    def test_redacts_nested_secret_keys_without_hiding_metrics(self):
        value = {
            "api_key": "alpha",
            "usage": {"input_tokens": 10, "output_tokens": 4},
            "message": "client_secret=bravo",
        }
        redacted = redact_secrets(value)
        self.assertEqual(redacted["api_key"], REDACTED)
        self.assertEqual(redacted["usage"], {"input_tokens": 10, "output_tokens": 4})
        self.assertNotIn("bravo", redacted["message"])

    def test_redacts_explicit_values(self):
        self.assertEqual(
            redact_secrets("prefix custom-value suffix", extra_values=("custom-value",)),
            f"prefix {REDACTED} suffix",
        )


class TestAutomatedActions(unittest.TestCase):
    def test_blocks_commit_and_deployment_commands(self):
        blocked = (
            ("git", "commit", "-m", "automatic"),
            ("git", "-C", "/repo", "push"),
            ("npm", "run", "deploy"),
            ("kubectl", "apply", "-f", "service.yaml"),
            ("terraform", "apply"),
        )
        for argv in blocked:
            with self.subTest(argv=argv), self.assertRaises(SafetyViolation):
                validate_automated_command(argv)

    def test_allows_read_only_commands(self):
        argv = ("git", "status", "--short")
        self.assertEqual(validate_automated_command(argv), argv)


if __name__ == "__main__":
    unittest.main()
