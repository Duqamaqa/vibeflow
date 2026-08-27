import subprocess
import tempfile
import unittest
from pathlib import Path

from src.vibeflow.worktrees import (
    DirtyWorktreeError,
    GitWorktreeManager,
    UnsafeWorktreeOperation,
)


class RecordingRunner:
    def __init__(self, repo_root):
        self.repo_root = repo_root
        self.calls = []
        self.dirty_paths = set()
        self.worktree_output = ""

    def __call__(self, argv, *, cwd):
        argv = tuple(argv)
        cwd = Path(cwd)
        self.calls.append((argv, cwd))
        operation = argv[1:]
        if operation == ("rev-parse", "--show-toplevel"):
            stdout = f"{self.repo_root}\n"
        elif operation[:1] == ("status",):
            stdout = " M changed.py\n" if cwd in self.dirty_paths else ""
        elif operation == ("worktree", "list", "--porcelain"):
            stdout = self.worktree_output
        else:
            stdout = ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


class TestGitWorktreeManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.repo = self.root / "repo"
        self.worktree_root = self.root / "worktrees"
        self.repo.mkdir()
        self.worktree_root.mkdir()
        self.runner = RecordingRunner(self.repo)
        self.manager = GitWorktreeManager(
            self.repo,
            worktree_root=self.worktree_root,
            runner=self.runner,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_uses_argv_and_checks_dirty_state(self):
        target = self.worktree_root / "feature-one"

        info = self.manager.create(
            target,
            branch="feature/one",
            start_point="main",
        )

        self.assertEqual(info.path, target)
        self.assertEqual(info.branch, "feature/one")
        self.assertEqual(
            self.runner.calls[-1],
            ((
                "git",
                "worktree",
                "add",
                "-b",
                "feature/one",
                str(target),
                "main",
            ), self.repo),
        )
        self.assertTrue(all(isinstance(call[0], tuple) for call in self.runner.calls))
        self.assertNotIn("commit", {call[0][1] for call in self.runner.calls})
        self.assertNotIn("merge", {call[0][1] for call in self.runner.calls})

    def test_dirty_repository_blocks_create(self):
        self.runner.dirty_paths.add(self.repo)

        with self.assertRaises(DirtyWorktreeError):
            self.manager.create(
                self.worktree_root / "feature-two",
                branch="feature/two",
            )

        self.assertFalse(
            any(call[0][1:3] == ("worktree", "add") for call in self.runner.calls)
        )

    def test_explicit_main_repository_path_can_be_checked(self):
        self.assertFalse(self.manager.is_dirty(self.repo))

        self.runner.dirty_paths.add(self.repo)
        self.assertTrue(self.manager.is_dirty(self.repo))

    def test_paths_outside_root_and_nested_paths_are_rejected_early(self):
        with self.assertRaises(UnsafeWorktreeOperation):
            self.manager.create(self.root / "outside", branch="feature/outside")
        with self.assertRaises(UnsafeWorktreeOperation):
            self.manager.create(self.repo / "nested", branch="feature/nested")

        self.assertEqual(self.runner.calls, [])

    def test_invalid_branch_is_rejected_without_subprocess(self):
        for branch in ("-dangerous", "@", "feature/.hidden", "feature.lock/next"):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                self.manager.create(
                    self.worktree_root / "bad-branch",
                    branch=branch,
                )

        self.assertEqual(self.runner.calls, [])

    def test_dirty_worktree_blocks_remove_and_force_is_never_allowed(self):
        target = self.worktree_root / "feature-three"
        target.mkdir()
        self.runner.worktree_output = (
            f"worktree {self.repo}\nHEAD abc\nbranch refs/heads/main\n\n"
            f"worktree {target}\nHEAD def\nbranch refs/heads/feature/three\n\n"
        )
        self.runner.dirty_paths.add(target)

        with self.assertRaises(DirtyWorktreeError):
            self.manager.remove(target)
        with self.assertRaises(UnsafeWorktreeOperation):
            self.manager.remove(target, force=True)

        self.assertFalse(
            any(call[0][1:3] == ("worktree", "remove") for call in self.runner.calls)
        )

    def test_clean_registered_worktree_can_be_removed_without_merge_or_commit(self):
        target = self.worktree_root / "feature-four"
        target.mkdir()
        self.runner.worktree_output = (
            f"worktree {self.repo}\nHEAD abc\nbranch refs/heads/main\n\n"
            f"worktree {target}\nHEAD def\nbranch refs/heads/feature/four\n\n"
        )

        self.manager.remove(target)

        operations = [call[0][1] for call in self.runner.calls]
        self.assertEqual(
            self.runner.calls[-1],
            (("git", "worktree", "remove", str(target)), self.repo),
        )
        self.assertNotIn("merge", operations)
        self.assertNotIn("commit", operations)

    def test_worktree_list_parsing_is_deterministic(self):
        target = self.worktree_root / "detached"
        self.runner.worktree_output = (
            f"worktree {self.repo}\nHEAD abc\nbranch refs/heads/main\n\n"
            f"worktree {target}\nHEAD def\ndetached\nlocked reason\n\n"
        )

        worktrees = self.manager.list_worktrees()

        self.assertEqual(worktrees[0].branch, "main")
        self.assertTrue(worktrees[1].detached)
        self.assertTrue(worktrees[1].locked)


if __name__ == "__main__":
    unittest.main()
