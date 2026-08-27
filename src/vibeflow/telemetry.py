"""SQLite-backed execution telemetry and aggregate reporting."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Mapping

from .budget import CostEstimator


def infer_provider(model: str) -> str | None:
    """Infer an FCC provider from the first segment of a provider/model slug."""

    if not isinstance(model, str):
        return None
    provider, separator, remainder = model.strip().partition("/")
    if not separator or not provider or not remainder:
        return None
    return provider


def _token_count(usage: Mapping[str, Any] | None, primary: str, legacy: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(primary, usage.get(legacy))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{primary} must be a non-negative integer")
    return value


def _cache_token_count(usage: Mapping[str, Any] | None) -> int | None:
    if usage is None:
        return None
    value = next(
        (usage[key] for key in ("cache_tokens", "cached_tokens", "input_cached_tokens") if key in usage),
        None,
    )
    if value is None:
        for details_key in ("input_tokens_details", "prompt_tokens_details"):
            details = usage.get(details_key)
            if isinstance(details, Mapping) and "cached_tokens" in details:
                value = details["cached_tokens"]
                break
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("cache_tokens must be a non-negative integer")
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, (Path, Decimal)):
        return str(value)
    raise TypeError(f"Unsupported telemetry metadata type: {type(value).__name__}")


class Telemetry:
    """Record one row per model request in telemetry.db."""

    def __init__(
        self,
        log_dir: Path | str | None = None,
        *,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        directory = Path(log_dir) if log_dir is not None else Path.cwd() / ".vibeflow"
        directory.mkdir(parents=True, exist_ok=True)
        self.log_dir = directory
        self.db_path = directory / "telemetry.db"
        self.cost_estimator = cost_estimator
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    task_id TEXT NOT NULL,
                    agent_role TEXT NOT NULL,
                    provider TEXT,
                    model TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_tokens INTEGER,
                    total_tokens INTEGER,
                    duration_ms REAL NOT NULL,
                    request_id TEXT,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    error_type TEXT,
                    strategy TEXT,
                    escalation_count INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS telemetry_timestamp_idx
                    ON telemetry(timestamp);
                CREATE INDEX IF NOT EXISTS telemetry_task_id_idx
                    ON telemetry(task_id);
                CREATE INDEX IF NOT EXISTS telemetry_provider_model_idx
                    ON telemetry(provider, model);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(telemetry)")
            }
            migrations = {
                "cache_tokens": "ALTER TABLE telemetry ADD COLUMN cache_tokens INTEGER",
                "strategy": "ALTER TABLE telemetry ADD COLUMN strategy TEXT",
                "escalation_count": (
                    "ALTER TABLE telemetry ADD COLUMN escalation_count INTEGER NOT NULL DEFAULT 0"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)

    def log_event(
        self,
        task_id: str,
        agent_role: str,
        model: str,
        tier: str,
        usage: Mapping[str, Any] | None,
        duration: float,
        request_id: str | None,
        success: bool,
        *,
        provider: str | None = None,
        error_type: str | None = None,
        strategy: str | None = None,
        escalation_count: int = 0,
        estimated_cost_usd: float | Decimal | None = None,
        timestamp: float | None = None,
        **extra_fields: Any,
    ) -> int:
        """Insert an event and return its row id; duration is in seconds."""

        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("duration must be a non-negative finite number")
        if duration < 0 or not math.isfinite(duration):
            raise ValueError("duration must be a non-negative finite number")
        if not isinstance(success, bool):
            raise ValueError("success must be a bool")

        input_tokens = _token_count(usage, "input_tokens", "prompt_tokens")
        output_tokens = _token_count(usage, "output_tokens", "completion_tokens")
        cache_tokens = _cache_token_count(usage)
        total_tokens = _token_count(usage, "total_tokens", "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        if isinstance(escalation_count, bool) or not isinstance(escalation_count, int):
            raise ValueError("escalation_count must be a non-negative integer")
        if escalation_count < 0:
            raise ValueError("escalation_count must be a non-negative integer")

        resolved_provider = provider or infer_provider(model)
        if estimated_cost_usd is None and self.cost_estimator is not None and usage is not None:
            estimate = self.cost_estimator.estimate(model, usage)
            if estimate is not None:
                estimated_cost_usd = estimate.total_cost
        if estimated_cost_usd is not None:
            estimated_cost_usd = float(estimated_cost_usd)
            if estimated_cost_usd < 0 or not math.isfinite(estimated_cost_usd):
                raise ValueError("estimated_cost_usd must be a non-negative finite number")

        event_timestamp = time.time() if timestamp is None else float(timestamp)
        if not math.isfinite(event_timestamp):
            raise ValueError("timestamp must be finite")
        metadata_json = json.dumps(
            extra_fields,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO telemetry (
                    timestamp, task_id, agent_role, provider, model, tier,
                    input_tokens, output_tokens, total_tokens, duration_ms,
                    cache_tokens, request_id, success, error_type, strategy,
                    escalation_count, estimated_cost_usd,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_timestamp,
                    task_id,
                    agent_role,
                    resolved_provider,
                    model,
                    tier,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    float(duration) * 1000.0,
                    cache_tokens,
                    request_id,
                    int(success),
                    error_type,
                    strategy,
                    escalation_count,
                    estimated_cost_usd,
                    metadata_json,
                ),
            )
            row_id = cursor.lastrowid
        if row_id is None:
            raise sqlite3.DatabaseError("Telemetry insert did not return a row id")
        return row_id

    @staticmethod
    def _aggregate_columns() -> str:
        return """
            COUNT(*) AS request_count,
            COALESCE(SUM(success), 0) AS successful_requests,
            COUNT(*) - COALESCE(SUM(success), 0) AS failed_requests,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cache_tokens), 0) AS cache_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(duration_ms), 0.0) AS duration_ms,
            COALESCE(SUM(estimated_cost_usd), 0.0) AS estimated_cost_usd,
            COUNT(estimated_cost_usd) AS priced_requests
        """

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["unpriced_requests"] = result["request_count"] - result["priced_requests"]
        return result

    def report(self, *, task_id: str | None = None) -> dict[str, Any]:
        """Return deterministic totals plus provider, model and role breakdowns."""

        where_clause = " WHERE task_id = ?" if task_id is not None else ""
        parameters: tuple[Any, ...] = (task_id,) if task_id is not None else ()
        aggregate = self._aggregate_columns()

        with self._connect() as connection:
            totals = connection.execute(
                f"SELECT {aggregate} FROM telemetry{where_clause}", parameters
            ).fetchone()
            by_provider = connection.execute(
                f"""
                SELECT COALESCE(provider, 'unknown') AS provider, {aggregate}
                FROM telemetry{where_clause}
                GROUP BY COALESCE(provider, 'unknown')
                ORDER BY provider
                """,
                parameters,
            ).fetchall()
            by_model = connection.execute(
                f"""
                SELECT COALESCE(provider, 'unknown') AS provider, model, {aggregate}
                FROM telemetry{where_clause}
                GROUP BY COALESCE(provider, 'unknown'), model
                ORDER BY provider, model
                """,
                parameters,
            ).fetchall()
            by_agent_role = connection.execute(
                f"""
                SELECT agent_role, {aggregate}
                FROM telemetry{where_clause}
                GROUP BY agent_role
                ORDER BY agent_role
                """,
                parameters,
            ).fetchall()
            by_tier = connection.execute(
                f"""
                SELECT tier, {aggregate}
                FROM telemetry{where_clause}
                GROUP BY tier
                ORDER BY tier
                """,
                parameters,
            ).fetchall()
            by_strategy = connection.execute(
                f"""
                SELECT COALESCE(strategy, 'unspecified') AS strategy, {aggregate}
                FROM telemetry{where_clause}
                GROUP BY COALESCE(strategy, 'unspecified')
                ORDER BY strategy
                """,
                parameters,
            ).fetchall()

        if totals is None:
            raise sqlite3.DatabaseError("Telemetry aggregate query returned no row")
        return {
            "totals": self._row_dict(totals),
            "by_provider": [self._row_dict(row) for row in by_provider],
            "by_model": [self._row_dict(row) for row in by_model],
            "by_agent_role": [self._row_dict(row) for row in by_agent_role],
            "by_tier": [self._row_dict(row) for row in by_tier],
            "by_strategy": [self._row_dict(row) for row in by_strategy],
        }

    def report_by_provider(self, *, task_id: str | None = None) -> list[dict[str, Any]]:
        return self.report(task_id=task_id)["by_provider"]
