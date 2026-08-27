import os
from pathlib import Path
import tempfile
import unittest

from vibeflow.changes import (
    ChangeError,
    ChangeProposal,
    FileOperation,
    StructuredChangeApplier,
    file_sha256,
)


class TestStructuredChangeApplier(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "update.py").write_text("old\n", encoding="utf-8")
        (self.root / "delete.txt").write_text("delete me\n", encoding="utf-8")
        (self.root / "rename.txt").write_text("rename me\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_update_delete_and_rename(self):
        proposal = ChangeProposal((
            FileOperation("create", "src/new.py", content="new\n"),
            FileOperation(
                "update",
                "src/update.py",
                content="updated\n",
                expected_sha256=file_sha256(self.root / "src" / "update.py"),
            ),
            FileOperation(
                "delete",
                "delete.txt",
                expected_sha256=file_sha256(self.root / "delete.txt"),
            ),
            FileOperation(
                "rename",
                "rename.txt",
                destination="renamed.txt",
                expected_sha256=file_sha256(self.root / "rename.txt"),
            ),
        ))

        result = StructuredChangeApplier(self.root).apply(proposal)

        self.assertTrue(result.applied)
        self.assertEqual((self.root / "src" / "update.py").read_text(), "updated\n")
        self.assertEqual((self.root / "src" / "new.py").read_text(), "new\n")
        self.assertFalse((self.root / "delete.txt").exists())
        self.assertFalse((self.root / "rename.txt").exists())
        self.assertEqual((self.root / "renamed.txt").read_text(), "rename me\n")
        self.assertIn("src/update.py", result.diff)

    def test_rejects_traversal_protected_paths_and_stale_hash(self):
        applier = StructuredChangeApplier(self.root)
        cases = (
            FileOperation("create", "../escape.txt", content="bad"),
            FileOperation("create", ".env", content="SECRET=bad"),
            FileOperation("update", "src/update.py", content="bad", expected_sha256="0" * 64),
        )
        for operation in cases:
            with self.subTest(operation=operation):
                with self.assertRaises(ChangeError):
                    applier.apply(ChangeProposal((operation,)))

    def test_rejects_symlink_escape(self):
        outside_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temporary.cleanup)
        outside = Path(outside_temporary.name)
        os.symlink(outside, self.root / "linked")

        with self.assertRaises(ChangeError):
            StructuredChangeApplier(self.root).apply(
                ChangeProposal((FileOperation("create", "linked/escape.txt", content="bad"),))
            )

    def test_rollback_restores_task_baseline(self):
        path = self.root / "src" / "update.py"
        applier = StructuredChangeApplier(self.root)
        applier.apply(ChangeProposal((
            FileOperation(
                "update",
                "src/update.py",
                content="changed\n",
                expected_sha256=file_sha256(path),
            ),
        )))

        applier.rollback()

        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
