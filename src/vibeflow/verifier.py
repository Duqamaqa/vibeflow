"""Deterministic discovery and execution of repository verification commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from typing import Any, Callable, Iterable, Mapping, Sequence

from .safety import SafetyViolation, redact_secrets, validate_automated_command


CHECK_CATEGORIES: tuple[str, ...] = ("tests", "lint", "typecheck", "build")


class CheckStatus(str, Enum):
    """Terminal state for a verification check."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class VerificationCommand:
    """An argv-only command backed by repository tooling evidence."""

    category: str
    tool: str
    argv: tuple[str, ...]
    source: str
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.category not in CHECK_CATEGORIES:
            raise ValueError(f"Unsupported verification category: {self.category}")
        if not self.argv or any(not part for part in self.argv):
            raise ValueError("Verification commands require non-empty argv entries")

    @property
    def display(self) -> str:
        return shlex.join(self.argv)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "tool": self.tool,
            "argv": list(self.argv),
            "display": self.display,
            "source": self.source,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class VerificationResult:
    """Captured result of one command or one skipped category."""

    category: str
    status: CheckStatus
    command: VerificationCommand | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "status": self.status.value,
            "passed": self.passed,
            "command": self.command.to_dict() if self.command else None,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
        }


@dataclass(frozen=True)
class AcceptanceDecision:
    """Result of applying an acceptance policy to verification results."""

    accepted: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class AcceptancePolicy:
    """Fail closed for required tests and every discovered broken check."""

    required_categories: tuple[str, ...] = ("tests",)
    allow_unavailable: bool = False

    def evaluate(self, results: Sequence[VerificationResult]) -> AcceptanceDecision:
        reasons: list[str] = []
        for result in results:
            label = result.command.display if result.command else result.category
            if result.status is CheckStatus.FAILED:
                reasons.append(f"{label} failed")
            elif result.status is CheckStatus.TIMED_OUT:
                reasons.append(f"{label} timed out")
            elif result.status is CheckStatus.UNAVAILABLE and not self.allow_unavailable:
                reasons.append(f"{label} is unavailable")

        for category in self.required_categories:
            category_results = [result for result in results if result.category == category]
            discovered = [result for result in category_results if result.command is not None]
            if not discovered:
                reasons.append(f"required {category} check was not discovered")
            elif not any(result.status is CheckStatus.PASSED for result in discovered):
                reasons.append(f"required {category} check did not pass")

        unique_reasons = tuple(dict.fromkeys(reasons))
        return AcceptanceDecision(accepted=not unique_reasons, reasons=unique_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_categories": list(self.required_categories),
            "allow_unavailable": self.allow_unavailable,
        }


@dataclass(frozen=True)
class VerificationReport:
    """Complete, serializable verification outcome."""

    repo_root: Path
    results: tuple[VerificationResult, ...]
    policy: AcceptancePolicy
    decision: AcceptanceDecision

    @property
    def accepted(self) -> bool:
        return self.decision.accepted

    def by_category(self, category: str) -> tuple[VerificationResult, ...]:
        return tuple(result for result in self.results if result.category == category)

    def to_dict(self) -> dict[str, Any]:
        checks = {
            category: [result.to_dict() for result in self.by_category(category)]
            for category in CHECK_CATEGORIES
        }
        return {
            "repo_root": str(self.repo_root),
            "accepted": self.accepted,
            "reasons": list(self.decision.reasons),
            "policy": self.policy.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "checks": checks,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


class Verifier:
    """Discover configured checks and execute them without a shell."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        *,
        timeout_seconds: float = 60.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        which: Callable[[str], str | None] | None = None,
        module_available: Callable[[str], bool] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.repo_root = Path(repo_root or Path.cwd()).expanduser().resolve()
        if not self.repo_root.is_dir():
            raise ValueError(f"Repository root is not a directory: {self.repo_root}")
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner or subprocess.run
        self._which = which or shutil.which
        self._module_available = module_available or self._find_module
        self._clock = clock or time.perf_counter

    @staticmethod
    def _find_module(module_name: str) -> bool:
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _read_text(self, relative_path: str) -> str:
        path = self.repo_root / relative_path
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _load_pyproject(self) -> Mapping[str, Any]:
        path = self.repo_root / "pyproject.toml"
        if not path.is_file():
            return {}
        try:
            with path.open("rb") as handle:
                value = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        return value if isinstance(value, Mapping) else {}

    def _load_package_json(self) -> Mapping[str, Any]:
        path = self.repo_root / "package.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _dependency_declared(dependencies: Iterable[str], package: str) -> bool:
        pattern = re.compile(
            rf"^{re.escape(package)}(?:$|\[|[;@\s<>=!~])",
            re.IGNORECASE,
        )
        return any(pattern.search(dependency.strip()) for dependency in dependencies)

    def _python_dependencies(self, pyproject: Mapping[str, Any]) -> tuple[str, ...]:
        dependencies: list[str] = []
        project = pyproject.get("project", {})
        if isinstance(project, Mapping):
            declared = project.get("dependencies", [])
            if isinstance(declared, list):
                dependencies.extend(item for item in declared if isinstance(item, str))
            optional = project.get("optional-dependencies", {})
            if isinstance(optional, Mapping):
                for group in optional.values():
                    if isinstance(group, list):
                        dependencies.extend(item for item in group if isinstance(item, str))
        build_system = pyproject.get("build-system", {})
        if isinstance(build_system, Mapping):
            requirements = build_system.get("requires", [])
            if isinstance(requirements, list):
                dependencies.extend(item for item in requirements if isinstance(item, str))
        return tuple(dependencies)

    def _custom_commands(self, pyproject: Mapping[str, Any]) -> list[VerificationCommand]:
        tool = pyproject.get("tool", {})
        if not isinstance(tool, Mapping):
            return []
        vibeflow = tool.get("vibeflow", {})
        if not isinstance(vibeflow, Mapping):
            return []

        command_tables: list[tuple[str, Mapping[str, Any]]] = []
        direct = vibeflow.get("commands", {})
        if isinstance(direct, Mapping):
            command_tables.append(("[tool.vibeflow.commands]", direct))
        verification = vibeflow.get("verification", {})
        if isinstance(verification, Mapping):
            nested = verification.get("commands", {})
            if isinstance(nested, Mapping):
                command_tables.append(("[tool.vibeflow.verification.commands]", nested))

        aliases = {
            "test": "tests",
            "tests": "tests",
            "lint": "lint",
            "typecheck": "typecheck",
            "build": "build",
        }
        commands: list[VerificationCommand] = []
        for table_name, table in command_tables:
            for raw_category, raw_argv in table.items():
                category = aliases.get(str(raw_category).lower())
                valid_argv = isinstance(raw_argv, list) and raw_argv
                if category is None or not valid_argv:
                    continue
                if not all(isinstance(part, str) and part for part in raw_argv):
                    continue
                commands.append(
                    VerificationCommand(
                        category=category,
                        tool="configured",
                        argv=tuple(raw_argv),
                        source=f"pyproject.toml:{table_name}",
                    )
                )
        return commands

    def _make_commands(self) -> list[VerificationCommand]:
        makefile_name = next(
            (name for name in ("GNUmakefile", "Makefile", "makefile") if (self.repo_root / name).is_file()),
            None,
        )
        if makefile_name is None:
            return []
        targets = set(
            re.findall(
                r"(?m)^([A-Za-z0-9_.-]+)\s*:(?!=)",
                self._read_text(makefile_name),
            )
        )
        category_targets = {
            "tests": ("test", "tests", "pytest", "unittest"),
            "lint": ("lint",),
            "typecheck": ("typecheck", "type-check", "types", "mypy"),
            "build": ("build",),
        }
        commands: list[VerificationCommand] = []
        for category in CHECK_CATEGORIES:
            target = next((name for name in category_targets[category] if name in targets), None)
            if target:
                commands.append(
                    VerificationCommand(
                        category=category,
                        tool="make",
                        argv=("make", target),
                        source=f"{makefile_name}:{target}",
                    )
                )
        return commands

    def _package_commands(self, package_json: Mapping[str, Any]) -> list[VerificationCommand]:
        scripts = package_json.get("scripts", {})
        if not isinstance(scripts, Mapping):
            return []
        if (self.repo_root / "pnpm-lock.yaml").is_file():
            manager = "pnpm"
        elif (self.repo_root / "yarn.lock").is_file():
            manager = "yarn"
        else:
            manager = "npm"
        names = {
            "tests": ("test", "tests"),
            "lint": ("lint",),
            "typecheck": ("typecheck", "type-check", "types"),
            "build": ("build",),
        }
        commands: list[VerificationCommand] = []
        for category in CHECK_CATEGORIES:
            script = next(
                (name for name in names[category] if isinstance(scripts.get(name), str)),
                None,
            )
            if script:
                commands.append(
                    VerificationCommand(
                        category=category,
                        tool=manager,
                        argv=(manager, "run", script),
                        source=f"package.json:scripts.{script}",
                    )
                )
        return commands

    def _test_sources(self) -> tuple[Path, ...]:
        tests_root = self.repo_root / "tests"
        if not tests_root.is_dir():
            return ()
        return tuple(sorted(path for path in tests_root.rglob("test*.py") if path.is_file()))

    @staticmethod
    def _read_test_source(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:131_072]
        except OSError:
            return ""

    def _python_test_command(
        self,
        tool: Mapping[str, Any],
        dependencies: Sequence[str],
        setup_cfg: str,
        tox_ini: str,
    ) -> VerificationCommand | None:
        tox_configured = bool(
            tox_ini and re.search(r"(?m)^\[testenv(?::[^]]+)?\]", tox_ini)
        )
        if tox_configured:
            return VerificationCommand(
                "tests",
                "tox",
                (sys.executable, "-m", "tox"),
                "tox.ini:[testenv]",
            )

        pytest_configured = bool(
            (self.repo_root / "pytest.ini").is_file()
            or isinstance(tool.get("pytest"), Mapping)
            or re.search(r"(?m)^\[(?:tool:)?pytest\]", setup_cfg)
            or self._dependency_declared(dependencies, "pytest")
        )
        if pytest_configured:
            return VerificationCommand(
                "tests",
                "pytest",
                (sys.executable, "-m", "pytest"),
                "Python pytest configuration",
            )

        test_sources = self._test_sources()
        if not test_sources:
            return None
        unittest_pattern = re.compile(
            r"(?:^|\n)\s*(?:import unittest|from unittest\b|class\s+\w+\(unittest\.TestCase\))"
        )
        if any(unittest_pattern.search(self._read_test_source(path)) for path in test_sources):
            return VerificationCommand(
                "tests",
                "unittest",
                (sys.executable, "-m", "unittest", "discover", "-s", "tests"),
                "tests/test*.py:unittest imports",
            )
        return VerificationCommand(
            "tests",
            "pytest",
            (sys.executable, "-m", "pytest"),
            "tests/test*.py:pytest-style tests",
        )

    def _python_commands(self, pyproject: Mapping[str, Any]) -> list[VerificationCommand]:
        dependencies = self._python_dependencies(pyproject)
        tool = pyproject.get("tool", {})
        tool = tool if isinstance(tool, Mapping) else {}
        setup_cfg = self._read_text("setup.cfg")
        tox_ini = self._read_text("tox.ini")
        commands: list[VerificationCommand] = []

        test_command = self._python_test_command(
            tool,
            dependencies,
            setup_cfg,
            tox_ini,
        )
        if test_command:
            commands.append(test_command)

        ruff_configured = bool(
            (self.repo_root / "ruff.toml").is_file()
            or (self.repo_root / ".ruff.toml").is_file()
            or isinstance(tool.get("ruff"), Mapping)
            or self._dependency_declared(dependencies, "ruff")
        )
        flake8_configured = bool(
            (self.repo_root / ".flake8").is_file()
            or re.search(r"(?m)^\[flake8\]", setup_cfg)
            or re.search(r"(?m)^\[flake8\]", tox_ini)
            or self._dependency_declared(dependencies, "flake8")
        )
        pylint_configured = bool(
            (self.repo_root / ".pylintrc").is_file()
            or isinstance(tool.get("pylint"), Mapping)
            or self._dependency_declared(dependencies, "pylint")
        )
        if ruff_configured:
            commands.append(
                VerificationCommand(
                    "lint",
                    "ruff",
                    (sys.executable, "-m", "ruff", "check", "."),
                    "Python ruff configuration",
                )
            )
        if flake8_configured:
            commands.append(
                VerificationCommand(
                    "lint",
                    "flake8",
                    (sys.executable, "-m", "flake8", "."),
                    "Python flake8 configuration",
                )
            )
        if pylint_configured:
            target = "src" if (self.repo_root / "src").is_dir() else "."
            commands.append(
                VerificationCommand(
                    "lint",
                    "pylint",
                    (sys.executable, "-m", "pylint", target),
                    "Python pylint configuration",
                )
            )

        mypy_configured = bool(
            (self.repo_root / "mypy.ini").is_file()
            or re.search(r"(?m)^\[mypy\]", setup_cfg)
            or isinstance(tool.get("mypy"), Mapping)
            or self._dependency_declared(dependencies, "mypy")
        )
        pyright_configured = bool(
            (self.repo_root / "pyrightconfig.json").is_file()
            or isinstance(tool.get("pyright"), Mapping)
            or self._dependency_declared(dependencies, "pyright")
        )
        if mypy_configured:
            commands.append(
                VerificationCommand(
                    "typecheck",
                    "mypy",
                    (sys.executable, "-m", "mypy", "."),
                    "Python mypy configuration",
                )
            )
        if pyright_configured:
            commands.append(
                VerificationCommand(
                    "typecheck",
                    "pyright",
                    ("pyright",),
                    "Python pyright configuration",
                )
            )

        if isinstance(pyproject.get("build-system"), Mapping):
            commands.append(
                VerificationCommand(
                    "build",
                    "build",
                    (sys.executable, "-m", "build"),
                    "pyproject.toml:[build-system]",
                    120.0,
                )
            )
        elif (self.repo_root / "setup.py").is_file():
            commands.append(
                VerificationCommand(
                    "build",
                    "setuptools",
                    (sys.executable, "setup.py", "build"),
                    "setup.py",
                    120.0,
                )
            )
        return commands

    @staticmethod
    def _deduplicate(commands: Iterable[VerificationCommand]) -> tuple[VerificationCommand, ...]:
        unique: dict[tuple[str, tuple[str, ...]], VerificationCommand] = {}
        for command in commands:
            unique.setdefault((command.category, command.argv), command)
        return tuple(
            command
            for category in CHECK_CATEGORIES
            for command in unique.values()
            if command.category == category
        )

    def discover_commands(self) -> tuple[VerificationCommand, ...]:
        """Discover commands only when repository files provide evidence for them."""

        pyproject = self._load_pyproject()
        package_json = self._load_package_json()
        commands: list[VerificationCommand] = []
        commands.extend(self._custom_commands(pyproject))
        commands.extend(self._make_commands())
        commands.extend(self._package_commands(package_json))
        commands.extend(self._python_commands(pyproject))
        return self._deduplicate(commands)

    def command_availability(self, command: VerificationCommand) -> tuple[bool, str]:
        """Check executable and Python module availability without running it."""

        executable = command.argv[0]
        has_separator = os.sep in executable or bool(os.altsep and os.altsep in executable)
        if has_separator:
            executable_path = Path(executable)
            if not executable_path.is_absolute():
                executable_path = self.repo_root / executable_path
            if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
                return False, f"executable is unavailable: {executable}"
        elif self._which(executable) is None:
            return False, f"executable is unavailable: {executable}"

        if len(command.argv) >= 3 and command.argv[1] == "-m":
            module_name = command.argv[2]
            if not self._module_available(module_name):
                return False, f"Python module is unavailable: {module_name}"
        return True, "available"

    @staticmethod
    def _captured_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _run(self, command: VerificationCommand) -> VerificationResult:
        available, message = self.command_availability(command)
        if not available:
            return VerificationResult(
                category=command.category,
                status=CheckStatus.UNAVAILABLE,
                command=command,
                message=message,
            )

        try:
            validate_automated_command(command.argv)
        except SafetyViolation as exc:
            return VerificationResult(
                category=command.category,
                status=CheckStatus.UNAVAILABLE,
                command=command,
                message=str(exc),
            )

        started = self._clock()
        timeout = command.timeout_seconds or self.timeout_seconds
        try:
            completed = self._runner(
                list(command.argv),
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return VerificationResult(
                category=command.category,
                status=CheckStatus.TIMED_OUT,
                command=command,
                stdout=redact_secrets(self._captured_text(exc.stdout)),
                stderr=redact_secrets(self._captured_text(exc.stderr)),
                duration_seconds=max(0.0, self._clock() - started),
                message=f"command exceeded {timeout:g} seconds",
            )
        except FileNotFoundError as exc:
            return VerificationResult(
                category=command.category,
                status=CheckStatus.UNAVAILABLE,
                command=command,
                duration_seconds=max(0.0, self._clock() - started),
                message=redact_secrets(str(exc)),
            )
        except OSError as exc:
            return VerificationResult(
                category=command.category,
                status=CheckStatus.FAILED,
                command=command,
                duration_seconds=max(0.0, self._clock() - started),
                message=redact_secrets(str(exc)),
            )

        status = CheckStatus.PASSED if completed.returncode == 0 else CheckStatus.FAILED
        return VerificationResult(
            category=command.category,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout=redact_secrets(self._captured_text(completed.stdout)),
            stderr=redact_secrets(self._captured_text(completed.stderr)),
            duration_seconds=max(0.0, self._clock() - started),
            message="command passed" if status is CheckStatus.PASSED else "command returned a non-zero exit code",
        )

    def verify(self, policy: AcceptancePolicy | None = None) -> VerificationReport:
        """Run all discovered checks and apply the supplied acceptance policy."""

        acceptance_policy = policy or AcceptancePolicy()
        commands = self.discover_commands()
        results: list[VerificationResult] = [self._run(command) for command in commands]
        discovered_categories = {command.category for command in commands}
        for category in CHECK_CATEGORIES:
            if category not in discovered_categories:
                results.append(
                    VerificationResult(
                        category=category,
                        status=CheckStatus.SKIPPED,
                        message=f"no {category} command discovered from repository tooling files",
                    )
                )
        results.sort(key=lambda result: CHECK_CATEGORIES.index(result.category))
        decision = acceptance_policy.evaluate(results)
        return VerificationReport(
            repo_root=self.repo_root,
            results=tuple(results),
            policy=acceptance_policy,
            decision=decision,
        )

    def _discover_test_command(self) -> list[str] | None:
        command = next((item for item in self.discover_commands() if item.category == "tests"), None)
        return list(command.argv) if command else None

    def _discover_lint_command(self) -> list[str] | None:
        command = next((item for item in self.discover_commands() if item.category == "lint"), None)
        return list(command.argv) if command else None

    def _discover_build_command(self) -> list[str] | None:
        command = next((item for item in self.discover_commands() if item.category == "build"), None)
        return list(command.argv) if command else None
