"""Native directory selection for the loopback dashboard."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable


class NativeDialogError(RuntimeError):
    """Raised when no supported local directory dialog is available."""


def choose_directory(
    prompt: str,
    initial_directory: str | Path | None = None,
    *,
    platform: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Open the operating system directory picker and return a resolved path."""

    title = " ".join(str(prompt).split())[:120] or "Choose a folder"
    initial = Path(initial_directory or Path.home()).expanduser().resolve()
    if not initial.is_dir():
        initial = Path.home().resolve()
    current_platform = platform or sys.platform

    if current_platform == "darwin":
        script = (
            "on run argv\n"
            "set dialogPrompt to item 1 of argv\n"
            "set startPath to item 2 of argv\n"
            "try\n"
            "set selectedFolder to choose folder with prompt dialogPrompt "
            "default location POSIX file startPath\n"
            "return POSIX path of selectedFolder\n"
            "on error number -128\n"
            "return \"\"\n"
            "end try\n"
            "end run"
        )
        command = ["osascript", "-e", script, title, str(initial)]
    elif current_platform.startswith("win"):
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = $args[0]; $dialog.SelectedPath = $args[1]; "
            "if ($dialog.ShowDialog() -eq 'OK') { $dialog.SelectedPath }"
        )
        command = [
            "powershell",
            "-NoProfile",
            "-STA",
            "-Command",
            script,
            title,
            str(initial),
        ]
    elif which("zenity"):
        command = [
            "zenity",
            "--file-selection",
            "--directory",
            f"--title={title}",
            f"--filename={initial}/",
        ]
    elif which("kdialog"):
        command = ["kdialog", "--getexistingdirectory", str(initial), "--title", title]
    else:
        raise NativeDialogError(
            "No native folder chooser is available. Enter the repository path manually."
        )

    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    selected = completed.stdout.strip()
    if completed.returncode != 0 or not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if not path.is_dir():
        raise NativeDialogError("The selected folder is no longer available")
    return path
