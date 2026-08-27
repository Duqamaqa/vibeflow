from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from vibeflow.changes import ChangeError, ChangeProposal, FileOperation, file_sha256
from vibeflow.workspace import IsolatedWorkspace


@unittest.skipUnless(shutil.which("git"), "git is required")
class TestIsolatedWorkspace(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._git("init")
        (self.root / "clean.txt").write_text("clean\n", encoding="utf-8")
        (self.root / "dirty.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "clean.txt", "dirty.txt")
        self._git("-c", "user.name=Vibeflow Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        (self.root / "dirty.txt").write_text("user change\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_promotes_reviewed_change_and_preserves_unrelated_dirty_file(self):
        with IsolatedWorkspace(self.root) as workspace:
            self.assertEqual(workspace.info.strategy, "git-worktree")
            clean_path = workspace.path / "clean.txt"
            workspace.applier.apply(ChangeProposal((FileOperation(
                "update",
                "clean.txt",
                content="reviewed\n",
                expected_sha256=file_sha256(clean_path),
            ),)))
            promoted = workspace.promote()

        self.assertTrue(promoted.applied)
        self.assertEqual((self.root / "clean.txt").read_text(), "reviewed\n")
        self.assertEqual((self.root / "dirty.txt").read_text(), "user change\n")

    def test_rejects_changes_to_preexisting_dirty_path(self):
        with IsolatedWorkspace(self.root) as workspace:
            dirty_path = workspace.path / "dirty.txt"
            with self.assertRaises(ChangeError):
                workspace.applier.apply(ChangeProposal((FileOperation(
                    "update",
                    "dirty.txt",
                    content="overwrite\n",
                    expected_sha256=file_sha256(dirty_path),
                ),)))


if __name__ == "__main__":
    unittest.main()
