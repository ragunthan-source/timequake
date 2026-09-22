"""SQLite-backed tracing, fault-control, and rollback persistence for TimeQuake."""

import json
import os
import sqlite3
import threading
import time
import uuid


_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_PROJECT_DIR, "timequake.db")
_lock = threading.Lock()

DEFAULT_MONITOR_CONFIG = {
    "active_metrics": {"over_max_count": True, "delta_of_maximum": True, "over_average_count": True, "max_moving_average": True},
    "thresholds": {"over_max_count": 1.0, "delta_of_maximum": 2.0, "over_average_count": 1.35, "max_moving_average": 1.25},
    "coverage_percent": 50.0,
    "auto_rollback": True,
    "manual_override": "auto",
}


def set_db_path(path):
    """Override the SQLite database path (primarily useful for tests)."""
    global _DB_PATH
    _DB_PATH = path if os.path.isabs(path) else os.path.join(_PROJECT_DIR, path)


def _ensure_column(conn, table, column, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_connection():
    """Return a SQLite connection with the TimeQuake persistence schema ready."""
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS spans ("
        "trace_id TEXT NOT NULL, "
        "span_id TEXT NOT NULL, "
        "parent_span_id TEXT, "
        "service TEXT NOT NULL, "
        "start_time REAL NOT NULL, "
        "end_time REAL NOT NULL, "
        "duration_ms INTEGER NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'ok', "
        "is_fault_run INTEGER NOT NULL DEFAULT 0, "
        "timestamp REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rollbacks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "trace_id TEXT NOT NULL, "
        "service TEXT NOT NULL, "
        "span_id TEXT NOT NULL, "
        "triggered_by_service TEXT NOT NULL, "
        "reason TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "timestamp REAL NOT NULL, "
        "completed_at REAL, "
        "UNIQUE(trace_id, span_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fault_injections ("
        "service TEXT PRIMARY KEY, "
        "delay_ms INTEGER NOT NULL, "
        "remaining_requests INTEGER NOT NULL DEFAULT 1, "
        "created_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS service_states ("
        "service TEXT PRIMARY KEY, "
        "operation_count INTEGER NOT NULL DEFAULT 0, "
        "rollback_count INTEGER NOT NULL DEFAULT 0, "
        "last_trace_id TEXT, "
        "last_span_id TEXT, "
        "status TEXT NOT NULL DEFAULT 'idle', "
        "updated_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS service_state_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "trace_id TEXT NOT NULL, "
        "service TEXT NOT NULL, "
        "span_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, "
        "previous_state TEXT NOT NULL, "
        "current_state TEXT NOT NULL, "
        "timestamp REAL NOT NULL, "
        "rollback_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS monitor_configs ("
        "service TEXT PRIMARY KEY, active_metrics TEXT NOT NULL, thresholds TEXT NOT NULL, "
        "coverage_percent REAL NOT NULL DEFAULT 50, auto_rollback INTEGER NOT NULL DEFAULT 1, "
        "manual_override TEXT NOT NULL DEFAULT 'auto', updated_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS monitor_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, span_id TEXT NOT NULL, service TEXT NOT NULL, "
        "metric TEXT NOT NULL, metric_value REAL NOT NULL, threshold REAL NOT NULL, coverage_percent REAL NOT NULL, "
        "status TEXT NOT NULL, timestamp REAL NOT NULL, UNIQUE(trace_id, span_id, metric))"
    )
    _ensure_column(conn, "rollbacks", "completed_at", "REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, start_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rollbacks_trace ON rollbacks(trace_id, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_state_events_trace ON service_state_events(trace_id, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_events_service ON monitor_events(service, timestamp)")
    conn.commit()
    return conn


def record_span(span):
    """Record one actual service request span in the shared SQLite store."""
    with _lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO spans (trace_id, span_id, parent_span_id, service, "
                "start_time, end_time, duration_ms, status, is_fault_run, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    span.get("trace_id", ""),
                    span.get("span_id", ""),
                    span.get("parent_span_id", ""),
                    span.get("service", ""),
                    span.get("start_time", time.time()),
                    span.get("end_time", time.time()),
                    span.get("duration_ms", 0),
                    span.get("status", "ok"),
                    1 if span.get("is_fault_run", False) else 0,
                    span.get("timestamp", time.time()),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _span_rows(rows):
    return [
        {
            "trace_id": r[0],
            "span_id": r[1],
            "parent_span_id": r[2],
            "service": r[3],
            "start_time": r[4],
            "end_time": r[5],
            "duration_ms": r[6],
            "status": r[7],
            "is_fault_run": bool(r[8]),
            "timestamp": r[9],
        }
        for r in rows
    ]


def get_spans_by_trace(trace_id):
    """Return the actual service spans for a trace, ordered by start time."""
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT trace_id, span_id, parent_span_id, service, "
                "start_time, end_time, duration_ms, status, is_fault_run, timestamp "
                "FROM spans WHERE trace_id = ? ORDER BY start_time ASC",
                (trace_id,),
            ).fetchall()
            return _span_rows(rows)
        finally:
            conn.close()


def get_all_spans(limit=1000):
    """Return recent actual service spans across traces."""
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT trace_id, span_id, parent_span_id, service, "
                "start_time, end_time, duration_ms, status, is_fault_run, timestamp "
                "FROM spans ORDER BY start_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return _span_rows(list(reversed(rows)))
        finally:
            conn.close()


def arm_fault(service, delay_ms, request_count=1):
    """Persist a one-or-more-request fault for a live service process to consume."""
    with _lock:
        conn = get_connection()
        try:
            now = time.time()
            conn.execute(
                "INSERT INTO fault_injections (service, delay_ms, remaining_requests, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(service) DO UPDATE SET delay_ms = excluded.delay_ms, "
                "remaining_requests = excluded.remaining_requests, created_at = excluded.created_at",
                (service, int(delay_ms), int(request_count), now),
            )
            conn.commit()
            return {
                "service": service,
                "delay_ms": int(delay_ms),
                "remaining_requests": int(request_count),
                "created_at": now,
            }
        finally:
            conn.close()


def consume_fault(service):
    """Atomically consume a queued fault. Returns None when no fault is armed."""
    with _lock:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT delay_ms, remaining_requests, created_at FROM fault_injections WHERE service = ?",
                (service,),
            ).fetchone()
            if not row:
                conn.commit()
                return None

            delay_ms, remaining_requests, created_at = row
            if remaining_requests <= 1:
                conn.execute("DELETE FROM fault_injections WHERE service = ?", (service,))
                remaining_after = 0
            else:
                remaining_after = remaining_requests - 1
                conn.execute(
                    "UPDATE fault_injections SET remaining_requests = ? WHERE service = ?",
                    (remaining_after, service),
                )
            conn.commit()
            return {
                "service": service,
                "delay_ms": delay_ms,
                "remaining_requests": remaining_after,
                "created_at": created_at,
            }
        finally:
            conn.close()


def get_armed_faults():
    """Return live, not-yet-consumed fault controls for observability."""
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT service, delay_ms, remaining_requests, created_at "
                "FROM fault_injections ORDER BY created_at DESC"
            ).fetchall()
            return [
                {
                    "service": row[0],
                    "delay_ms": row[1],
                    "remaining_requests": row[2],
                    "created_at": row[3],
                }
                for row in rows
            ]
        finally:
            conn.close()


def _normalise_monitor_config(config):
    """Merge persisted monitor settings into safe, complete defaults."""
    value = {**DEFAULT_MONITOR_CONFIG, "active_metrics": {**DEFAULT_MONITOR_CONFIG["active_metrics"]}, "thresholds": {**DEFAULT_MONITOR_CONFIG["thresholds"]}}
    if config:
        value["active_metrics"].update(config.get("active_metrics") or {})
        value["thresholds"].update(config.get("thresholds") or {})
        for key in ("coverage_percent", "auto_rollback", "manual_override"):
            if key in config:
                value[key] = config[key]
    value["coverage_percent"] = max(1.0, min(100.0, float(value["coverage_percent"])))
    value["auto_rollback"] = bool(value["auto_rollback"])
    value["manual_override"] = value["manual_override"] if value["manual_override"] in ("auto", "normal", "error") else "auto"
    return value


def get_monitor_config(service):
    with _lock:
        conn = get_connection()
        try:
            row = conn.execute("SELECT active_metrics, thresholds, coverage_percent, auto_rollback, manual_override FROM monitor_configs WHERE service = ?", (service,)).fetchone()
            persisted = {} if row is None else {"active_metrics": json.loads(row[0]), "thresholds": json.loads(row[1]), "coverage_percent": row[2], "auto_rollback": bool(row[3]), "manual_override": row[4]}
            return {"service": service, **_normalise_monitor_config(persisted)}
        finally:
            conn.close()


def save_monitor_config(service, config):
    """Persist user monitoring controls in the existing SQLite state store."""
    value = _normalise_monitor_config(config)
    with _lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO monitor_configs (service, active_metrics, thresholds, coverage_percent, auto_rollback, manual_override, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(service) DO UPDATE SET active_metrics=excluded.active_metrics, thresholds=excluded.thresholds, coverage_percent=excluded.coverage_percent, auto_rollback=excluded.auto_rollback, manual_override=excluded.manual_override, updated_at=excluded.updated_at",
                (service, json.dumps(value["active_metrics"]), json.dumps(value["thresholds"]), value["coverage_percent"], int(value["auto_rollback"]), value["manual_override"], time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    return {"service": service, **value}


def record_monitor_events(events):
    if not events:
        return []
    with _lock:
        conn = get_connection()
        try:
            for event in events:
                conn.execute(
                    "INSERT OR IGNORE INTO monitor_events (trace_id, span_id, service, metric, metric_value, threshold, coverage_percent, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event["trace_id"], event["span_id"], event["service"], event["metric"], event["metric_value"], event["threshold"], event["coverage_percent"], event["status"], event.get("timestamp", time.time())),
                )
            conn.commit()
        finally:
            conn.close()
    return events


def get_monitor_events(service=None, limit=250):
    with _lock:
        conn = get_connection()
        try:
            query = "SELECT trace_id, span_id, service, metric, metric_value, threshold, coverage_percent, status, timestamp FROM monitor_events"
            parameters = []
            if service:
                query += " WHERE service = ?"
                parameters.append(service)
            query += " ORDER BY timestamp DESC LIMIT ?"
            parameters.append(limit)
            rows = conn.execute(query, parameters).fetchall()
            return [{"trace_id": r[0], "span_id": r[1], "service": r[2], "metric": r[3], "metric_value": r[4], "threshold": r[5], "coverage_percent": r[6], "status": r[7], "timestamp": r[8]} for r in rows]
        finally:
            conn.close()


def _default_service_state(service):
    return {
        "service": service,
        "operation_count": 0,
        "rollback_count": 0,
        "last_trace_id": None,
        "last_span_id": None,
        "status": "idle",
        "updated_at": None,
    }


def _read_service_state(conn, service):
    row = conn.execute(
        "SELECT service, operation_count, rollback_count, last_trace_id, last_span_id, status, updated_at "
        "FROM service_states WHERE service = ?",
        (service,),
    ).fetchone()
    if row is None:
        return _default_service_state(service)
    return {
        "service": row[0],
        "operation_count": row[1],
        "rollback_count": row[2],
        "last_trace_id": row[3],
        "last_span_id": row[4],
        "status": row[5],
        "updated_at": row[6],
    }


def _write_service_state(conn, state):
    conn.execute(
        "INSERT INTO service_states "
        "(service, operation_count, rollback_count, last_trace_id, last_span_id, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(service) DO UPDATE SET operation_count = excluded.operation_count, "
        "rollback_count = excluded.rollback_count, last_trace_id = excluded.last_trace_id, "
        "last_span_id = excluded.last_span_id, status = excluded.status, updated_at = excluded.updated_at",
        (
            state["service"],
            state["operation_count"],
            state["rollback_count"],
            state["last_trace_id"],
            state["last_span_id"],
            state["status"],
            state["updated_at"],
        ),
    )


def _record_state_event(conn, trace_id, service, span_id, event_type, previous_state, current_state, timestamp, rollback_id=None):
    conn.execute(
        "INSERT INTO service_state_events "
        "(trace_id, service, span_id, event_type, previous_state, current_state, timestamp, rollback_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trace_id,
            service,
            span_id,
            event_type,
            json.dumps(previous_state, sort_keys=True),
            json.dumps(current_state, sort_keys=True),
            timestamp,
            rollback_id,
        ),
    )


def get_service_state(service):
    """Return the current persisted application state for one simulated service."""
    with _lock:
        conn = get_connection()
        try:
            return _read_service_state(conn, service)
        finally:
            conn.close()


def get_state_history_by_trace(trace_id):
    """Return durable before/after state snapshots for trace replay and rollback playback."""
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, trace_id, service, span_id, event_type, previous_state, current_state, "
                "timestamp, rollback_id FROM service_state_events "
                "WHERE trace_id = ? ORDER BY timestamp ASC, id ASC",
                (trace_id,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "trace_id": row[1],
                    "service": row[2],
                    "span_id": row[3],
                    "event_type": row[4],
                    "previous_state": json.loads(row[5]),
                    "current_state": json.loads(row[6]),
                    "timestamp": row[7],
                    "rollback_id": row[8],
                }
                for row in rows
            ]
        finally:
            conn.close()


def record_service_state(span_data):
    """Persist a service state transition that corresponds to a completed real span."""
    service = span_data.get("service", "")
    trace_id = span_data.get("trace_id", "")
    span_id = span_data.get("span_id", "")
    if not service or not trace_id or not span_id:
        return

    with _lock:
        conn = get_connection()
        try:
            now = span_data.get("timestamp", time.time())
            previous_state = _read_service_state(conn, service)
            current_state = {
                **previous_state,
                "operation_count": previous_state["operation_count"] + 1,
                "last_trace_id": trace_id,
                "last_span_id": span_id,
                "status": "degraded" if span_data.get("is_fault_run", False) else "committed",
                "updated_at": now,
            }
            _write_service_state(conn, current_state)
            _record_state_event(
                conn, trace_id, service, span_id, "state_applied", previous_state, current_state, now
            )
            if span_data.get("is_fault_run", False):
                _record_state_event(
                    conn, trace_id, service, span_id, "fault_detected", current_state, current_state, now
                )
            conn.commit()
        finally:
            conn.close()


def create_rollbacks(events):
    """Insert idempotent, durable rollback rows and return newly created records."""
    if not events:
        return []

    created = []
    with _lock:
        conn = get_connection()
        try:
            for event in events:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO rollbacks "
                    "(trace_id, service, span_id, triggered_by_service, reason, status, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        event["trace_id"],
                        event["service"],
                        event["span_id"],
                        event["triggered_by_service"],
                        event["reason"],
                        event.get("timestamp", time.time()),
                    ),
                )
                if cursor.rowcount:
                    rollback_id = cursor.lastrowid
                    previous_state = _read_service_state(conn, event["service"])
                    current_state = {
                        **previous_state,
                        "last_trace_id": event["trace_id"],
                        "last_span_id": event["span_id"],
                        "status": "rollback_pending",
                        "updated_at": event.get("timestamp", time.time()),
                    }
                    _write_service_state(conn, current_state)
                    _record_state_event(
                        conn,
                        event["trace_id"],
                        event["service"],
                        event["span_id"],
                        "rollback_scheduled",
                        previous_state,
                        current_state,
                        event.get("timestamp", time.time()),
                        rollback_id,
                    )
                    created.append({**event, "id": rollback_id, "status": "pending", "completed_at": None})
            conn.commit()
        finally:
            conn.close()
    return created


def complete_rollback(rollback_id):
    """Complete a rollback and persist the restored service state snapshot."""
    with _lock:
        conn = get_connection()
        try:
            completed_at = time.time()
            rollback = conn.execute(
                "SELECT trace_id, service, span_id FROM rollbacks WHERE id = ? AND status = 'pending'",
                (rollback_id,),
            ).fetchone()
            if rollback is None:
                conn.commit()
                return

            trace_id, service, span_id = rollback
            previous_state = _read_service_state(conn, service)
            restored_state = {
                **previous_state,
                "operation_count": max(0, previous_state["operation_count"] - 1),
                "rollback_count": previous_state["rollback_count"] + 1,
                "last_trace_id": trace_id,
                "last_span_id": span_id,
                "status": "restored",
                "updated_at": completed_at,
            }
            _write_service_state(conn, restored_state)
            _record_state_event(
                conn,
                trace_id,
                service,
                span_id,
                "rollback_completed",
                previous_state,
                restored_state,
                completed_at,
                rollback_id,
            )
            conn.execute(
                "UPDATE rollbacks SET status = 'completed', completed_at = ? WHERE id = ?",
                (completed_at, rollback_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_rollbacks_by_trace(trace_id):
    """Return persisted rollback rows for a trace; no state is calculated client-side."""
    with _lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, trace_id, service, span_id, triggered_by_service, reason, "
                "status, timestamp, completed_at FROM rollbacks "
                "WHERE trace_id = ? ORDER BY timestamp ASC, id ASC",
                (trace_id,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "trace_id": row[1],
                    "service": row[2],
                    "span_id": row[3],
                    "triggered_by_service": row[4],
                    "reason": row[5],
                    "status": row[6],
                    "timestamp": row[7],
                    "completed_at": row[8],
                }
                for row in rows
            ]
        finally:
            conn.close()


def record_service_span(span_data):
    """Minimal wrapper used by a running service to persist one request span."""
    span = {
        "trace_id": span_data.get("trace_id", ""),
        "span_id": span_data.get("span_id", str(uuid.uuid4())),
        "parent_span_id": span_data.get("parent_span_id", ""),
        "service": span_data.get("service", ""),
        "start_time": span_data.get("start_time", time.time()),
        "end_time": span_data.get("end_time", time.time()),
        "duration_ms": span_data.get("duration_ms", 0),
        "status": span_data.get("status", "ok"),
        "is_fault_run": bool(span_data.get("is_fault_run", False)),
        "timestamp": time.time(),
    }
    record_span(span)
    record_service_state(span)


def init_default_db(path=None):
    """Initialize the default project database or an explicitly supplied test database."""
    if path is not None:
        set_db_path(path)
    conn = get_connection()
    conn.close()
