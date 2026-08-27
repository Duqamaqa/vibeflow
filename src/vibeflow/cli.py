"""Command-line interface for the deterministic Vibeflow tooling slice."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import closing
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time
import tomllib
from typing import Any, Callable, Mapping, Sequence, TextIO

from .benchmark import (
    BenchmarkCase,
    BenchmarkResponse,
    BenchmarkRunner,
    ModelSpec,
)
from .model_selection import (
    ModelSelectionError,
    extract_model_ids,
    load_routing_preferences,
    resolve_tier_models,
)
from .project_setup import initialize_repository
from .safety import SafetyGuard, SafetyViolation, redact_secrets
from .verifier import Verifier


def _make_verifier(repo_root: Path) -> Verifier:
    return Verifier(repo_root)


def _make_safety_guard(repo_root: Path) -> SafetyGuard:
    return SafetyGuard(repo_root)


def _make_benchmark_runner(
    provider: Callable[[str, BenchmarkCase], BenchmarkResponse | Mapping[str, Any]]
    | None,
) -> BenchmarkRunner:
    return BenchmarkRunner(provider=provider)


def _list_live_models() -> Any:
    from .fcc_client import FCCClient

    return FCCClient().list_models()


def _check_fcc_health() -> dict[str, Any]:
    from .fcc_client import FCCClient

    client = FCCClient()
    return {"healthy": client.health_check(), "server_root": client.server_root}


def _plan_task(
    goal: str,
    repo_root: Path,
    constraints: Sequence[str],
    acceptance: Sequence[str],
) -> Any:
    from .context import ContextManager
    from .orchestrator import Orchestrator

    return Orchestrator(context_manager=ContextManager(repo_root)).plan_task(
        goal,
        constraints=constraints,
        acceptance_criteria=acceptance or None,
    )


def _run_live_task(
    goal: str,
    repo_root: Path,
    context_files: Sequence[str],
    approved: bool = False,
) -> Any:
    from .autonomous import AutonomousRunner

    return AutonomousRunner(repo_root).run(
        goal,
        context_files=context_files,
        approved=approved,
    )


def _serve_dashboard(
    repo_root: Path,
    host: str,
    port: int,
    open_browser: bool,
    output: TextIO,
) -> None:
    from .dashboard import serve_dashboard

    serve_dashboard(
        repo_root,
        host=host,
        port=port,
        open_browser=open_browser,
        output=output,
    )


def _benchmark_live_model(model: str, case: BenchmarkCase) -> BenchmarkResponse:
    from .fcc_client import FCCClient

    text_parts: list[str] = []
    usage: Mapping[str, Any] = {}
    for chunk in FCCClient().post_responses(
        model=model,
        messages=[{"role": "user", "content": case.prompt}],
        stream=True,
    ):
        if not isinstance(chunk, Mapping):
            text_parts.append(str(chunk))
            continue
        delta = chunk.get("delta", chunk.get("content"))
        if isinstance(delta, str):
            text_parts.append(delta)
        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, Mapping):
            usage = chunk_usage
    return BenchmarkResponse(
        text="".join(text_parts),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


def _read_telemetry(path: Path, limit: int) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("Telemetry limit must be positive")
    if not path.is_file():
        return {
            "status": "unavailable",
            "path": str(path),
            "events": [],
            "summary": {
                "events": 0,
                "successful": 0,
                "failed": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "duration_seconds": 0.0,
                "invalid_lines": 0,
                "models": {},
                "tiers": {},
            },
        }

    if path.suffix == ".db":
        if path.name != "telemetry.db":
            raise ValueError("SQLite telemetry path must be named telemetry.db")
        from .telemetry import Telemetry

        report = Telemetry(path.parent).report()
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        events = []
        for row in reversed(rows):
            event = dict(row)
            metadata = event.pop("metadata_json", "{}")
            try:
                event["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                event["metadata"] = {}
            events.append(event)
        summary = dict(report["totals"])
        summary["events"] = summary["request_count"]
        summary["successful"] = summary["successful_requests"]
        summary["failed"] = summary["failed_requests"]
        summary["duration_seconds"] = summary["duration_ms"] / 1000.0
        return {
            "status": "ok",
            "path": str(path),
            "events": events,
            "summary": summary,
            "by_provider": report["by_provider"],
            "by_model": report["by_model"],
            "by_agent_role": report["by_agent_role"],
            "by_tier": report["by_tier"],
            "by_strategy": report["by_strategy"],
        }

    events: deque[Mapping[str, Any]] = deque(maxlen=limit)
    invalid_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(event, Mapping):
                events.append(event)
            else:
                invalid_lines += 1

    event_list = list(events)
    model_counts = Counter(
        str(event["model"]) for event in event_list if event.get("model") is not None
    )
    tier_counts = Counter(
        str(event["tier"]) for event in event_list if event.get("tier") is not None
    )
    successful = sum(event.get("success") is True for event in event_list)
    failed = sum(event.get("success") is False for event in event_list)
    return {
        "status": "ok",
        "path": str(path),
        "events": event_list,
        "summary": {
            "events": len(event_list),
            "successful": successful,
            "failed": failed,
            "input_tokens": sum(
                int(event.get("input_tokens") or 0) for event in event_list
            ),
            "output_tokens": sum(
                int(event.get("output_tokens") or 0) for event in event_list
            ),
            "duration_seconds": sum(
                float(event.get("duration") or 0.0) for event in event_list
            ),
            "invalid_lines": invalid_lines,
            "models": dict(sorted(model_counts.items())),
            "tiers": dict(sorted(tier_counts.items())),
        },
    }


@dataclass
class CLIDependencies:
    """Injectable boundaries for command tests and live integrations."""

    verifier_factory: Callable[[Path], Any] = _make_verifier
    safety_factory: Callable[[Path], Any] = _make_safety_guard
    benchmark_factory: Callable[
        [
            Callable[
                [str, BenchmarkCase],
                BenchmarkResponse | Mapping[str, Any],
            ]
            | None
        ],
        Any,
    ] = _make_benchmark_runner
    live_model_lister: Callable[[], Any] | None = _list_live_models
    fcc_health_checker: Callable[[], Any] | None = _check_fcc_health
    task_runner: Callable[[str, Path, Sequence[str], bool], Any] | None = _run_live_task
    task_planner: Callable[
        [str, Path, Sequence[str], Sequence[str]],
        Any,
    ] | None = _plan_task
    dashboard_server: Callable[[Path, str, int, bool, TextIO], None] = _serve_dashboard
    benchmark_provider: Callable[
        [str, BenchmarkCase],
        BenchmarkResponse | Mapping[str, Any],
    ] | None = _benchmark_live_model
    telemetry_reader: Callable[[Path, int], Any] = _read_telemetry


Dependencies = CLIDependencies


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without executing any command dependencies."""

    parser = argparse.ArgumentParser(prog="vibeflow")
    parser.add_argument("--repo", default=".", help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_repo_option(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--repo",
            dest="command_repo",
            default=None,
            help="repository root",
        )

    init_parser = subparsers.add_parser("init", help="create deterministic local config")
    add_repo_option(init_parser)
    init_parser.add_argument("--config", default=".ai/vibeflow.toml")
    init_parser.set_defaults(handler=_handle_init)

    doctor_parser = subparsers.add_parser("doctor", help="inspect repository readiness")
    add_repo_option(doctor_parser)
    doctor_parser.add_argument(
        "--verify",
        action="store_true",
        help="run discovered verification commands",
    )
    doctor_parser.set_defaults(handler=_handle_doctor)

    models_parser = subparsers.add_parser("models", help="list configured model mappings")
    add_repo_option(models_parser)
    models_parser.add_argument(
        "--live",
        action="store_true",
        help="query the configured model service",
    )
    models_parser.set_defaults(handler=_handle_models)

    run_parser = subparsers.add_parser("run", help="run one autonomous coding task")
    add_repo_option(run_parser)
    run_parser.add_argument("goal", nargs="+")
    run_parser.add_argument("--context", action="append", default=[])
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show preflight without inference or file changes",
    )
    run_parser.add_argument("--approve", action="store_true", help="approve a flagged high-risk contract")
    run_parser.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    run_parser.set_defaults(handler=_handle_run)

    chat_parser = subparsers.add_parser("chat", help="type coding prompts interactively")
    add_repo_option(chat_parser)
    chat_parser.add_argument("--approve", action="store_true", help="approve flagged contracts in this session")
    chat_parser.set_defaults(handler=_handle_chat)

    plan_parser = subparsers.add_parser("plan", help="create a deterministic task plan")
    add_repo_option(plan_parser)
    plan_parser.add_argument("goal", nargs="+")
    plan_parser.add_argument("--constraint", action="append", default=[])
    plan_parser.add_argument("--acceptance", action="append", default=[])
    plan_parser.add_argument(
        "--live",
        action="store_true",
        help="permit an injected live planner",
    )
    plan_parser.set_defaults(handler=_handle_plan)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="plan or explicitly run representative model cases",
    )
    add_repo_option(benchmark_parser)
    benchmark_parser.add_argument("--model", action="append", default=[])
    benchmark_parser.add_argument("--input-cost", type=float, default=None)
    benchmark_parser.add_argument("--output-cost", type=float, default=None)
    benchmark_parser.add_argument(
        "--live",
        "--allow-paid",
        dest="live",
        action="store_true",
        help="explicitly permit live or paid model calls",
    )
    benchmark_parser.set_defaults(handler=_handle_benchmark)

    telemetry_parser = subparsers.add_parser(
        "telemetry",
        aliases=["report"],
        help="read a redacted local telemetry report",
    )
    add_repo_option(telemetry_parser)
    telemetry_parser.add_argument("action", nargs="?", choices=("report",))
    telemetry_parser.add_argument("--path", default=".vibeflow/telemetry.db")
    telemetry_parser.add_argument("--limit", type=int, default=100)
    telemetry_parser.set_defaults(handler=_handle_telemetry)

    status_parser = subparsers.add_parser("status", help="show the most recent task outcome")
    add_repo_option(status_parser)
    status_parser.set_defaults(handler=_handle_status)

    web_parser = subparsers.add_parser(
        "web",
        aliases=["ui"],
        help="start the localhost coding dashboard",
    )
    add_repo_option(web_parser)
    web_parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    web_parser.add_argument("--port", type=int, default=8765, help="localhost port")
    web_parser.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the browser automatically",
    )
    web_parser.set_defaults(handler=_handle_web)
    return parser


def _repo_root(args: argparse.Namespace) -> Path:
    configured = getattr(args, "command_repo", None) or args.repo
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise SafetyViolation(f"Repository root is not a directory: {root}")
    return root


def _local_model_mappings(repo_root: Path) -> dict[str, str]:
    config_path = repo_root / ".ai" / "routing.toml"
    if not config_path.is_file():
        return {}
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot read model routing config: {exc}") from exc

    tiers = config.get("tiers", {})
    if not isinstance(tiers, Mapping):
        return {}
    mappings: dict[str, str] = {}
    preferred_order = ("cheap", "standard", "strong")
    ordered_tiers = preferred_order + tuple(
        sorted(str(key) for key in tiers if str(key) not in preferred_order)
    )
    for tier in ordered_tiers:
        value = tiers.get(tier)
        if isinstance(value, Mapping):
            model = value.get("model")
        else:
            model = value
        if isinstance(model, str) and model.strip():
            mappings[tier] = model
    return mappings


def _local_model_candidates(repo_root: Path) -> dict[str, list[str]]:
    config_path = repo_root / ".ai" / "routing.toml"
    if not config_path.is_file():
        return {}
    _, candidates = load_routing_preferences(config_path)
    return {tier: list(values) for tier, values in candidates.items()}


def _command_payload(verifier: Any) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for command in verifier.discover_commands():
        available, message = verifier.command_availability(command)
        command_data = command.to_dict()
        command_data.update({"available": available, "availability": message})
        payload.append(command_data)
    return payload


def _handle_init(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    config_path, created_paths = initialize_repository(
        repo_root,
        config_path=args.config,
    )
    created = [str(path) for path in created_paths]
    return 0, {
        "ok": True,
        "command": "init",
        "status": "created" if created else "skipped",
        "reason": None if created else "configuration already exists",
        "path": str(config_path),
        "created": created,
        "default_mode": "autonomous",
        "auto_commit": False,
        "auto_deploy": False,
    }


def _handle_doctor(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    guard = dependencies.safety_factory(repo_root)
    dirty_state = guard.dirty_state()
    verifier = dependencies.verifier_factory(repo_root)
    commands = _command_payload(verifier)
    issues: list[str] = []
    warnings: list[str] = []

    python_supported = sys.version_info >= (3, 11)
    if not python_supported:
        issues.append("Python 3.11 or newer is required")
    if not dirty_state.available:
        issues.append(dirty_state.error or "Git is unavailable")
    elif not dirty_state.is_repository:
        issues.append(dirty_state.error or "Path is not a Git repository")
    elif dirty_state.dirty:
        warnings.append("Repository has uncommitted changes")

    test_commands = [command for command in commands if command["category"] == "tests"]
    if not test_commands:
        issues.append("No test command was discovered")
    elif not all(command["available"] for command in test_commands):
        issues.append("At least one discovered test command is unavailable")
    for command in commands:
        if command["category"] != "tests" and not command["available"]:
            warnings.append(
                f"{command['tool']} {command['category']} check is unavailable"
            )

    verification = None
    if args.verify:
        report = verifier.verify()
        verification = report.to_dict()
        if not report.accepted:
            issues.append("Verification acceptance policy rejected the repository")

    fcc: Any = {"healthy": False, "status": "not-configured"}
    if dependencies.fcc_health_checker is not None:
        try:
            fcc = dependencies.fcc_health_checker()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            fcc = {
                "healthy": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    if not isinstance(fcc, Mapping) or not fcc.get("healthy"):
        issues.append("FCC gateway is not healthy")

    model_check: dict[str, Any] = {"available": False, "resolved_tiers": {}}
    if isinstance(fcc, Mapping) and fcc.get("healthy") and dependencies.live_model_lister is not None:
        try:
            live_payload = dependencies.live_model_lister()
            resolved_tiers = resolve_tier_models(
                repo_root / ".ai" / "routing.toml",
                live_payload,
            )
            model_check = {
                "available": True,
                "catalog_count": len(extract_model_ids(live_payload)),
                "resolved_tiers": resolved_tiers,
            }
        except (ModelSelectionError, OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(f"Configured tier models are unavailable: {exc}")
            model_check = {"available": False, "error": str(exc), "resolved_tiers": {}}

    ok = not issues
    return (0 if ok else 1), {
        "ok": ok,
        "command": "doctor",
        "repo_root": str(repo_root),
        "python": {
            "version": platform.python_version(),
            "supported": python_supported,
        },
        "dirty_state": dirty_state.to_dict(),
        "verification_commands": commands,
        "verification": verification,
        "fcc": fcc,
        "model_check": model_check,
        "issues": issues,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _handle_models(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    configured = _local_model_mappings(repo_root)
    alternatives = _local_model_candidates(repo_root)
    catalog_count = None
    resolved = None
    if args.live:
        if dependencies.live_model_lister is None:
            raise RuntimeError("Live model listing is not configured")
        live_payload = dependencies.live_model_lister()
        catalog_count = len(extract_model_ids(live_payload))
        resolved = resolve_tier_models(repo_root / ".ai" / "routing.toml", live_payload)
    return 0, {
        "ok": True,
        "command": "models",
        "source": "live" if args.live else "repository",
        "tier_mappings": configured,
        "configured_alternatives": alternatives,
        "resolved_tiers": resolved,
        "catalog_count": catalog_count,
        "models": (
            list(dict.fromkeys(resolved.values()))
            if resolved is not None
            else list(dict.fromkeys(configured.values()))
        ),
    }


def _validated_context(guard: Any, context_files: Sequence[str]) -> tuple[str, ...]:
    validated: list[str] = []
    for context_file in context_files:
        path = guard.validate_path(context_file)
        validated.append(path.relative_to(guard.repo_root).as_posix())
    return tuple(validated)


def _handle_run(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    guard = dependencies.safety_factory(repo_root)
    context_files = _validated_context(guard, args.context)
    goal = " ".join(args.goal).strip()
    if not goal:
        raise ValueError("Task goal must not be empty")

    dirty_state = guard.dirty_state()
    verifier = dependencies.verifier_factory(repo_root)
    preflight = {
        "dirty_state": dirty_state.to_dict(),
        "verification_commands": _command_payload(verifier),
    }
    if args.dry_run:
        return 0, {
            "ok": True,
            "command": "run",
            "status": "dry_run",
            "live_calls_made": False,
            "goal": goal,
            "context_files": list(context_files),
            "preflight": preflight,
            "blocked_automatic_actions": ["commit", "deploy"],
        }

    if dependencies.task_runner is None:
        raise RuntimeError("Live task execution is not configured")
    result = dependencies.task_runner(goal, repo_root, context_files, args.approve)
    if isinstance(result, Mapping):
        successful = result.get("success") is not False
        result_status = result.get("status")
    else:
        successful = bool(getattr(result, "success", True))
        result_status = getattr(result, "status", None)
    if isinstance(result_status, Enum):
        result_status = result_status.value
    if not isinstance(result_status, str):
        result_status = "done" if successful else "blocked"
    payload = {
        "ok": successful,
        "command": "run",
        "status": result_status,
        "live_calls_made": True,
        "preflight": preflight,
        "result": result,
        "auto_commit": False,
        "auto_deploy": False,
    }
    _write_last_task(repo_root, payload)
    return (0 if successful else 1), payload


def _handle_chat(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, None]:
    repo_root = _repo_root(args)
    if dependencies.task_runner is None:
        raise RuntimeError("Live task execution is not configured")
    input_stream: TextIO = args._stdin
    output_stream: TextIO = args._stdout
    interactive = bool(getattr(input_stream, "isatty", lambda: False)())
    output_stream.write(
        f"Vibeflow chat for {repo_root}\nType a coding request. Use /plan <request>, /status, or /quit.\n"
    )
    output_stream.flush()
    while True:
        if interactive:
            output_stream.write("vibeflow> ")
            output_stream.flush()
        line = input_stream.readline()
        if line == "":
            break
        goal = line.strip()
        if not goal:
            continue
        if goal.lower() in {"/quit", "/exit", "quit", "exit"}:
            break
        try:
            if goal == "/status":
                _, payload = _recent_status(repo_root)
                _emit(output_stream, payload)
                continue
            if goal.startswith("/plan "):
                planned = dependencies.task_planner(
                    goal[6:].strip(), repo_root, (), ()
                ) if dependencies.task_planner is not None else _deterministic_plan(
                    goal[6:].strip(), (), (), dependencies.verifier_factory(repo_root)
                )
                _emit(output_stream, {"prompt": goal[6:].strip(), "status": "planned", "plan": planned})
                continue
            result = dependencies.task_runner(goal, repo_root, (), args.approve)
            if isinstance(result, Mapping):
                successful = result.get("success") is not False
                status = result.get("status", "done" if successful else "blocked")
            else:
                successful = bool(getattr(result, "success", True))
                status = getattr(result, "status", "done" if successful else "blocked")
            if isinstance(status, Enum):
                status = status.value
            payload = {
                "ok": successful,
                "command": "chat",
                "prompt": goal,
                "status": status,
                "result": result,
                "auto_commit": False,
                "auto_deploy": False,
            }
            _write_last_task(repo_root, payload)
            _emit(output_stream, payload)
        except (OSError, RuntimeError, SafetyViolation, TypeError, ValueError) as exc:
            _emit(
                output_stream,
                {"ok": False, "prompt": goal, "status": "blocked", "error": f"{type(exc).__name__}: {exc}"},
            )
    return 0, None


def _deterministic_plan(
    goal: str,
    constraints: Sequence[str],
    acceptance: Sequence[str],
    verifier: Any,
) -> dict[str, Any]:
    verification_commands = [
        {
            "category": command.category,
            "argv": list(command.argv),
            "source": command.source,
        }
        for command in verifier.discover_commands()
    ]
    steps = [
        "validate repository scope and dirty state",
        "inspect only task-relevant files",
        "implement the smallest scoped change",
    ]
    if verification_commands:
        steps.append("run discovered repository verification commands")
    steps.append("report changed files and verification results")
    return {
        "goal": goal,
        "constraints": list(constraints),
        "acceptance_criteria": list(acceptance),
        "steps": steps,
        "verification_commands": verification_commands,
        "automatic_commit": False,
        "automatic_deploy": False,
    }


def _handle_plan(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    goal = " ".join(args.goal).strip()
    if not goal:
        raise ValueError("Task goal must not be empty")
    verifier = dependencies.verifier_factory(repo_root)
    if dependencies.task_planner is not None:
        plan = dependencies.task_planner(
            goal,
            repo_root,
            tuple(args.constraint),
            tuple(args.acceptance),
        )
    else:
        plan = _deterministic_plan(
            goal,
            tuple(args.constraint),
            tuple(args.acceptance),
            verifier,
        )
    return 0, {
        "ok": True,
        "command": "plan",
        "status": "live" if args.live else "planned",
        "live_calls_made": bool(args.live),
        "plan": plan,
    }


def _handle_benchmark(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    configured = _local_model_mappings(repo_root)
    model_names = args.model or list(dict.fromkeys(configured.values()))
    if not model_names:
        raise ValueError("No models were supplied or configured")
    models = tuple(
        ModelSpec(
            name=model,
            input_cost_per_million=args.input_cost,
            output_cost_per_million=args.output_cost,
        )
        for model in model_names
    )
    runner = dependencies.benchmark_factory(dependencies.benchmark_provider)
    report = runner.run(models, allow_paid=args.live)
    return 0, {
        "ok": True,
        "command": "benchmark",
        "report": report.to_dict(),
    }


def _handle_telemetry(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    repo_root = _repo_root(args)
    guard = dependencies.safety_factory(repo_root)
    telemetry_path = guard.validate_path(args.path, allow_protected=True)
    report = dependencies.telemetry_reader(telemetry_path, args.limit)
    return 0, {
        "ok": True,
        "command": "telemetry",
        "report": report,
    }


def _write_last_task(repo_root: Path, payload: Mapping[str, Any]) -> None:
    state_dir = repo_root / ".vibeflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    ready = redact_secrets(_json_ready(payload))
    ready["recorded_at_unix"] = time.time()
    path = state_dir / "last-task.json"
    temporary = state_dir / "last-task.json.tmp"
    temporary.write_text(
        json.dumps(ready, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _recent_status(repo_root: Path) -> tuple[int, dict[str, Any]]:
    path = repo_root / ".vibeflow" / "last-task.json"
    if not path.is_file():
        return 1, {
            "ok": False,
            "command": "status",
            "status": "unavailable",
            "reason": "No task has been run in this repository yet.",
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read recent task status: {exc}") from exc
    return 0, {
        "ok": True,
        "command": "status",
        "path": str(path),
        "last_task": payload,
    }


def _handle_status(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, dict[str, Any]]:
    del dependencies
    return _recent_status(_repo_root(args))


def _handle_web(
    args: argparse.Namespace,
    dependencies: CLIDependencies,
) -> tuple[int, None]:
    dependencies.dashboard_server(
        _repo_root(args),
        args.host,
        args.port,
        not args.no_open,
        args._stdout,
    )
    return 0, None


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_ready(value.to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    return str(value)


def _emit(stream: TextIO, payload: Any) -> None:
    ready = redact_secrets(_json_ready(payload))
    stream.write(json.dumps(ready, indent=2, sort_keys=True))
    stream.write("\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: CLIDependencies | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one CLI command and return its process exit code."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    services = dependencies or CLIDependencies()
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    args._stdin = stdin or sys.stdin
    args._stdout = output
    try:
        exit_code, payload = args.handler(args, services)
    except (OSError, RuntimeError, SafetyViolation, TypeError, ValueError) as exc:
        _emit(
            errors,
            {
                "ok": False,
                "command": getattr(args, "command", None),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 1
    if payload is not None:
        _emit(output, payload)
    return exit_code
