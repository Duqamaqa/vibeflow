"""Loopback-only web dashboard for daily Vibeflow operation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, TextIO
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import webbrowser

from .autonomous import AutonomousRunner, _infer_plan_options
from .context import ContextManager
from .fcc_client import FCCClient
from .model_selection import load_research_preferences, load_routing_preferences
from .native_dialog import NativeDialogError, choose_directory
from .orchestrator import Orchestrator
from .project_setup import initialize_repository
from .safety import SafetyGuard, SafetyViolation, redact_secrets
from .skills import RepositorySkillStore, skill_metadata_dict


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765
MAX_REQUEST_BYTES = 64 * 1024
MAX_PROMPT_CHARACTERS = 20_000
MAX_SELECTED_SKILLS = 16
_SAFE_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

BUILTIN_ENGINES: tuple[dict[str, str], ...] = (
    {
        "id": "context-iceberg",
        "name": "Context Iceberg",
        "description": "Shows each agent only the contract, project memory, and files relevant to the task.",
        "activation": "always-on",
        "when": "Every task",
    },
    {
        "id": "isolated-agent-room",
        "name": "Isolated Agent Room",
        "description": "Builds changes in a separate Git worktree or safe copy so current work stays protected.",
        "activation": "always-on",
        "when": "Every change task",
    },
    {
        "id": "semantic-router",
        "name": "Semantic Model Router",
        "description": "Chooses cheap, standard, or strong models from task complexity, scope, risk, and failures.",
        "activation": "always-on",
        "when": "Before model work",
    },
    {
        "id": "cited-web-research",
        "name": "Cited Live Web Research",
        "description": "Uses a bounded OpenRouter web-search model and keeps attributable source links with the result.",
        "activation": "automatic",
        "when": "When your prompt requests live web research",
    },
    {
        "id": "structured-change-gate",
        "name": "Structured Change Gate",
        "description": "Accepts validated file operations instead of arbitrary shell commands or prose patches.",
        "activation": "always-on",
        "when": "Every proposed change",
    },
    {
        "id": "deterministic-verification",
        "name": "Deterministic Verification",
        "description": "Runs discovered tests, linting, type checks, and builds instead of trusting an AI opinion.",
        "activation": "always-on",
        "when": "After changes",
    },
    {
        "id": "fresh-reviewer",
        "name": "Fresh Independent Reviewer",
        "description": "Reviews only the contract, relevant context, diff, and verification—not worker reasoning.",
        "activation": "always-on",
        "when": "Before safe apply",
    },
    {
        "id": "resolver-loop",
        "name": "Bounded Resolver Loop",
        "description": "Repairs reviewer failures, then verifies and reviews again without retrying forever.",
        "activation": "automatic",
        "when": "When review fails",
    },
    {
        "id": "parallel-consensus",
        "name": "Parallel Agents / Consensus",
        "description": "Asks independent agents to solve a difficult uncertain problem, then compares their answers.",
        "activation": "automatic",
        "when": "High complexity and uncertainty",
    },
    {
        "id": "agent-debate",
        "name": "Agent Debate",
        "description": "Uses pragmatist, contrarian, edge-case, and security perspectives before a final judgment.",
        "activation": "automatic",
        "when": "High-risk uncertain decisions",
    },
    {
        "id": "safe-apply",
        "name": "Hash-Guarded Safe Apply",
        "description": "Promotes only reviewed files whose original content still matches the expected hashes.",
        "activation": "always-on",
        "when": "After a passing review",
    },
)


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


def _safe_payload(value: Any) -> Any:
    return redact_secrets(_json_ready(value))


def _validate_repository(candidate: str | Path) -> Path:
    root = Path(candidate).expanduser().resolve()
    if not root.is_dir():
        raise SafetyViolation(f"Repository root is not a directory: {root}")
    return root


def _default_plan(
    goal: str,
    repo_root: Path,
    selected_skills: tuple[str, ...],
) -> Any:
    return Orchestrator(context_manager=ContextManager(repo_root)).plan_task(
        goal,
        selected_skills=selected_skills,
        **_infer_plan_options(goal),
    )


def _default_run(
    goal: str,
    repo_root: Path,
    approved: bool,
    selected_skills: tuple[str, ...],
) -> Any:
    return AutonomousRunner(repo_root).run(
        goal,
        approved=approved,
        selected_skills=selected_skills,
    )


@dataclass(slots=True)
class DashboardTask:
    """One in-memory dashboard task with a redacted public representation."""

    task_id: str
    action: str
    repo_root: str
    prompt: str
    approved: bool
    task_type: str = "implementation"
    selected_skills: tuple[str, ...] = ()
    status: str = "queued"
    stage: str = "queued"
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _safe_payload(asdict(self))


class DashboardService:
    """Own dashboard state while delegating code changes to AutonomousRunner."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        planner: Callable[[str, Path, tuple[str, ...]], Any] = _default_plan,
        runner: Callable[[str, Path, bool, tuple[str, ...]], Any] = _default_run,
        fcc_factory: Callable[[], FCCClient] | None = None,
        directory_picker: Callable[[str, Path], Path | None] = choose_directory,
    ) -> None:
        self.repo_root = _validate_repository(repo_root)
        self.planner = planner
        self.runner = runner
        self.fcc_factory = fcc_factory or (lambda: FCCClient(timeout=1.0))
        self.directory_picker = directory_picker
        self._tasks: dict[str, DashboardTask] = {}
        self._tasks_lock = threading.Lock()
        self._repo_locks: dict[str, threading.Lock] = {}

    def bootstrap(self, repo: str | Path | None = None) -> dict[str, Any]:
        root = _validate_repository(repo or self.repo_root)
        guard = SafetyGuard(root)
        dirty_state = guard.dirty_state()
        preferred: dict[str, str] = {}
        alternatives: dict[str, tuple[str, ...]] = {}
        routing_error = None
        try:
            preferred, alternatives = load_routing_preferences(
                root / ".ai" / "routing.toml"
            )
            research_model, research_max_results = load_research_preferences(
                root / ".ai" / "routing.toml"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            routing_error = str(exc)
            research_model, research_max_results = None, None

        skill_catalog = RepositorySkillStore(root).catalog()

        fcc_healthy = False
        fcc_error = None
        try:
            fcc_healthy = self.fcc_factory().health_check()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            fcc_error = str(exc)

        return _safe_payload(
            {
                "ok": True,
                "repo_root": str(root),
                "repo_name": root.name,
                "fcc": {
                    "healthy": fcc_healthy,
                    "endpoint": "http://127.0.0.1:8082",
                    "error": fcc_error,
                },
                "git": dirty_state.to_dict(),
                "routing": {
                    "tiers": preferred,
                    "alternatives": alternatives,
                    "research": {
                        "model": research_model,
                        "max_results": research_max_results,
                    },
                    "error": routing_error,
                },
                "setup_required": not (root / ".ai" / "routing.toml").is_file(),
                "engines": BUILTIN_ENGINES,
                "skills": skill_catalog.to_dict(),
                "last_task": self._read_last_task(root),
                "tasks": self.list_tasks(root),
                "safety": {
                    "loopback_only": True,
                    "credentials_in_browser": False,
                    "auto_commit": False,
                    "auto_push": False,
                    "isolated_workspace": True,
                },
            }
        )

    def submit(
        self,
        action: str,
        prompt: str,
        *,
        repo: str | Path | None = None,
        approved: bool = False,
        selected_skills: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        if action not in {"plan", "run"}:
            raise ValueError("Action must be plan or run")
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt must not be empty")
        if len(normalized_prompt) > MAX_PROMPT_CHARACTERS:
            raise ValueError(
                f"Prompt exceeds {MAX_PROMPT_CHARACTERS:,} characters"
            )
        root = _validate_repository(repo or self.repo_root)
        normalized_skills = self._validate_selected_skills(root, selected_skills)
        now = time.time()
        task = DashboardTask(
            task_id=str(uuid.uuid4()),
            action=action,
            repo_root=str(root),
            prompt=normalized_prompt,
            approved=bool(approved),
            task_type=str(_infer_plan_options(normalized_prompt)["task_type"]),
            selected_skills=normalized_skills,
            created_at=now,
            updated_at=now,
        )
        with self._tasks_lock:
            self._tasks[task.task_id] = task
            repo_lock = self._repo_locks.setdefault(str(root), threading.Lock())
        thread = threading.Thread(
            target=self._execute,
            args=(task.task_id, root, repo_lock),
            name=f"vibeflow-{action}-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return task.to_dict()

    def select_directory(
        self,
        purpose: str,
        *,
        current: str | Path | None = None,
    ) -> dict[str, Any]:
        if purpose not in {"repository", "skill"}:
            raise ValueError("Picker purpose must be repository or skill")
        initial = _validate_repository(current or self.repo_root)
        prompt = (
            "Choose a Git repository for Vibeflow"
            if purpose == "repository"
            else "Choose a skill folder containing SKILL.md"
        )
        selected = self.directory_picker(prompt, initial)
        if selected is None:
            return {"ok": True, "selected": False, "path": None}
        path = _validate_repository(selected)
        return {"ok": True, "selected": True, "path": str(path)}

    def initialize(
        self,
        repo: str | Path | None = None,
        *,
        initialize_git: bool = False,
    ) -> dict[str, Any]:
        root = _validate_repository(repo or self.repo_root)
        dirty_state = SafetyGuard(root).dirty_state()
        git_initialized = False
        if not dirty_state.is_repository:
            if not initialize_git:
                raise SafetyViolation(
                    "This folder is not a Git repository. Use Prepare this folder to create local Git tracking first."
                )
            completed = subprocess.run(
                ["git", "init", "--quiet", str(root)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise SafetyViolation(
                    f"Git initialization failed: {completed.stderr.strip() or 'unknown error'}"
                )
            if not SafetyGuard(root).dirty_state().is_repository:
                raise SafetyViolation("Git initialization did not create a usable repository")
            git_initialized = True
        config_path, created = initialize_repository(root)
        return {
            "ok": True,
            "repo_root": str(root),
            "config_path": str(config_path),
            "created": [str(path.relative_to(root)) for path in created],
            "git_initialized": git_initialized,
            "status": "created" if created else "already-ready",
        }

    def create_skill(self, repo: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
        root = _validate_repository(repo)
        metadata = RepositorySkillStore(root).create(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            instructions=str(payload.get("instructions", "")),
            triggers=_string_list(payload.get("triggers"), "triggers"),
            capabilities=_string_list(payload.get("capabilities"), "capabilities"),
            cost=str(payload.get("cost", "low")),
            risk=str(payload.get("risk", "low")),
        )
        return {"ok": True, "skill": skill_metadata_dict(metadata)}

    def import_skill(self, repo: str | Path, source: str | Path) -> dict[str, Any]:
        root = _validate_repository(repo)
        metadata = RepositorySkillStore(root).import_from(source)
        return {"ok": True, "skill": skill_metadata_dict(metadata)}

    def remove_skill(self, repo: str | Path, name: str) -> dict[str, Any]:
        root = _validate_repository(repo)
        RepositorySkillStore(root).remove(name)
        return {"ok": True, "removed": name}

    @staticmethod
    def _validate_selected_skills(
        root: Path,
        selected_skills: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        if not isinstance(selected_skills, (list, tuple)):
            raise ValueError("Selected skills must be a list")
        if len(selected_skills) > MAX_SELECTED_SKILLS:
            raise ValueError(f"Select at most {MAX_SELECTED_SKILLS} skills")
        registry = RepositorySkillStore(root).catalog().registry
        normalized: list[str] = []
        for name in selected_skills:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Selected skill names must be non-empty strings")
            normalized.append(registry.get(name.strip()).metadata.name)
        return tuple(dict.fromkeys(normalized))

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return task.to_dict()

    def list_tasks(self, repo: str | Path | None = None) -> list[dict[str, Any]]:
        root = str(_validate_repository(repo or self.repo_root))
        with self._tasks_lock:
            tasks = [
                task.to_dict()
                for task in self._tasks.values()
                if task.repo_root == root
            ]
        return sorted(tasks, key=lambda item: item["created_at"], reverse=True)[:20]

    def _execute(
        self,
        task_id: str,
        root: Path,
        repo_lock: threading.Lock,
    ) -> None:
        with repo_lock:
            with self._tasks_lock:
                task_type = self._tasks[task_id].task_type
            self._update_task(
                task_id,
                status="running",
                stage="researching" if task_type.startswith("research") else "orchestrating",
            )
            try:
                with self._tasks_lock:
                    task = self._tasks[task_id]
                    action = task.action
                    prompt = task.prompt
                    approved = task.approved
                    selected_skills = task.selected_skills
                result = (
                    self.planner(prompt, root, selected_skills)
                    if action == "plan"
                    else self.runner(prompt, root, approved, selected_skills)
                )
                ready = _safe_payload(result)
                if action == "plan":
                    status = "planned"
                else:
                    status = str(
                        ready.get("status", "done")
                        if isinstance(ready, Mapping)
                        else "done"
                    )
                if action == "run":
                    try:
                        self._write_last_task(root, task_id, status, ready)
                    except OSError as exc:
                        self._update_task(
                            task_id,
                            error=f"Task finished, but recent-status persistence failed: {exc}",
                        )
                self._update_task(
                    task_id,
                    status=status,
                    stage="complete" if status in {"done", "planned"} else status,
                    result=ready,
                )
            except (KeyError, OSError, RuntimeError, SafetyViolation, TypeError, ValueError) as exc:
                self._update_task(
                    task_id,
                    status="blocked",
                    stage="blocked",
                    error=f"{type(exc).__name__}: {exc}",
                )

    def _update_task(self, task_id: str, **changes: Any) -> None:
        with self._tasks_lock:
            task = self._tasks[task_id]
            for name, value in changes.items():
                setattr(task, name, value)
            task.updated_at = time.time()

    @staticmethod
    def _read_last_task(root: Path) -> Any:
        path = root / ".vibeflow" / "last-task.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return _safe_payload(value)

    @staticmethod
    def _write_last_task(
        root: Path,
        task_id: str,
        status: str,
        result: Any,
    ) -> None:
        state_dir = root / ".vibeflow"
        state_dir.mkdir(parents=True, exist_ok=True)
        payload = _safe_payload(
            {
                "ok": status == "done",
                "command": "web",
                "status": status,
                "task_id": task_id,
                "result": result,
                "recorded_at_unix": time.time(),
            }
        )
        temporary = state_dir / "last-task.json.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(state_dir / "last-task.json")


class VibeflowHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in _SAFE_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def make_handler(
    service: DashboardService,
    asset_root: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    assets = asset_root or Path(__file__).with_name("ui")

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "VibeflowDashboard/1.0"

        def do_GET(self) -> None:
            if not self._request_is_local():
                self._json_error(HTTPStatus.FORBIDDEN, "Localhost requests only")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/bootstrap":
                repo = parse_qs(parsed.query).get("repo", [None])[0]
                self._json_response(HTTPStatus.OK, service.bootstrap(repo))
                return
            if parsed.path == "/api/tasks":
                repo = parse_qs(parsed.query).get("repo", [None])[0]
                self._json_response(HTTPStatus.OK, {"tasks": service.list_tasks(repo)})
                return
            if parsed.path.startswith("/api/tasks/"):
                task_id = unquote(parsed.path.removeprefix("/api/tasks/"))
                try:
                    task = service.get_task(task_id)
                except KeyError:
                    self._json_error(HTTPStatus.NOT_FOUND, "Task not found")
                    return
                self._json_response(HTTPStatus.OK, task)
                return
            static_files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
                "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
            }
            static_file = static_files.get(parsed.path)
            if static_file is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            name, content_type = static_file
            try:
                body = (assets / name).read_bytes()
            except OSError:
                self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI asset missing")
                return
            self._send(HTTPStatus.OK, body, content_type, cache="no-cache")

        def do_POST(self) -> None:
            if not self._request_is_local() or not self._origin_is_safe():
                self._json_error(HTTPStatus.FORBIDDEN, "Local same-origin requests only")
                return
            request_path = urlsplit(self.path).path
            if self.headers.get_content_type() != "application/json":
                self._json_error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Content-Type must be application/json",
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
                return
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                self._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large")
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return
            if not isinstance(payload, Mapping):
                self._json_error(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
                return
            try:
                if request_path == "/api/tasks":
                    response = service.submit(
                        str(payload.get("action", "")),
                        str(payload.get("prompt", "")),
                        repo=payload.get("repo"),
                        approved=payload.get("approved") is True,
                        selected_skills=payload.get("skills", ()),
                    )
                    status = HTTPStatus.ACCEPTED
                elif request_path == "/api/picker":
                    response = service.select_directory(
                        str(payload.get("purpose", "")),
                        current=payload.get("current"),
                    )
                    status = HTTPStatus.OK
                elif request_path == "/api/repositories/init":
                    response = service.initialize(
                        payload.get("repo"),
                        initialize_git=payload.get("initialize_git") is True,
                    )
                    status = HTTPStatus.OK
                elif request_path == "/api/skills/create":
                    response = service.create_skill(payload.get("repo"), payload)
                    status = HTTPStatus.CREATED
                elif request_path == "/api/skills/import":
                    response = service.import_skill(
                        payload.get("repo"),
                        payload.get("source", ""),
                    )
                    status = HTTPStatus.CREATED
                elif request_path == "/api/skills/remove":
                    response = service.remove_skill(
                        payload.get("repo"),
                        str(payload.get("name", "")),
                    )
                    status = HTTPStatus.OK
                else:
                    self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                    return
            except (
                KeyError,
                NativeDialogError,
                OSError,
                RuntimeError,
                SafetyViolation,
                TypeError,
                ValueError,
            ) as exc:
                self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json_response(status, response)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _request_is_local(self) -> bool:
            client_host = self.client_address[0]
            host_header = self.headers.get("Host", "")
            header_host = host_header
            if host_header.startswith("["):
                header_host = host_header.partition("]")[0] + "]"
            elif ":" in host_header:
                header_host = host_header.rsplit(":", 1)[0]
            return _loopback_host(client_host) and _loopback_host(header_host)

        def _origin_is_safe(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or not parsed.hostname:
                return False
            host_header = self.headers.get("Host", "").lower()
            return _loopback_host(parsed.hostname) and parsed.netloc.lower() == host_header

        def _json_response(self, status: HTTPStatus, payload: Any) -> None:
            body = json.dumps(_safe_payload(payload), separators=(",", ":")).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", cache="no-store")

        def _json_error(self, status: HTTPStatus, message: str) -> None:
            self._json_response(status, {"ok": False, "error": message})

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            cache: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "style-src 'self'; script-src 'self'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def create_server(
    repo_root: str | Path,
    *,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
    service: DashboardService | None = None,
) -> VibeflowHTTPServer:
    """Create a loopback-only HTTP server without starting its event loop."""

    if not _loopback_host(host):
        raise SafetyViolation("The Vibeflow dashboard only binds to localhost")
    if not 0 <= port <= 65_535:
        raise ValueError("Port must be between 0 and 65535")
    dashboard_service = service or DashboardService(repo_root)
    return VibeflowHTTPServer((host, port), make_handler(dashboard_service))


def serve_dashboard(
    repo_root: str | Path,
    *,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
    open_browser: bool = True,
    output: TextIO | None = None,
) -> None:
    """Start the dashboard and block until interrupted."""

    server = create_server(repo_root, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}"
    stream = output
    if stream is not None:
        stream.write(f"Vibeflow is ready: {url}\n")
        stream.write(f"Target repository: {_validate_repository(repo_root)}\n")
        stream.write("Keep this window open. Press Ctrl+C to stop Vibeflow.\n")
        stream.flush()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
