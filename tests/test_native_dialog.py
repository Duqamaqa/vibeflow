from pathlib import Path
import subprocess
import tempfile
import unittest

from vibeflow.native_dialog import NativeDialogError, choose_directory


class TestNativeDialog(unittest.TestCase):
    def test_macos_uses_fixed_osascript_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def runner(command, **options):
                calls.append((command, options))
                return subprocess.CompletedProcess(command, 0, directory + "\n", "")

            selected = choose_directory(
                "Choose repository",
                directory,
                platform="darwin",
                runner=runner,
            )

            self.assertEqual(selected, Path(directory).resolve())
        self.assertEqual(calls[0][0][0], "osascript")
        self.assertFalse(calls[0][1]["shell"])

    def test_cancel_returns_none(self):
        def runner(command, **options):
            return subprocess.CompletedProcess(command, 1, "", "cancelled")

        self.assertIsNone(
            choose_directory("Choose", platform="darwin", runner=runner)
        )

    def test_linux_without_dialog_has_manual_path_fallback_error(self):
        with self.assertRaises(NativeDialogError):
            choose_directory(
                "Choose",
                platform="linux",
                which=lambda command: None,
            )


if __name__ == "__main__":
    unittest.main()
