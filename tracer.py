"""Tracing layer for TimeQuake.

Records every request as a causal graph span with OpenTelemetry-compatible data shape.
Spans are persisted to SQLite for later root-cause analysis.

Span shape: {trace_id, span_id, parent_span_id, service, start_time, end_time,
duration_ms, status, is_fault_run}
"""

import sqlite3
import time
import uuid
import threading
from datetime import datetime, timezone


import os

# DB path default
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_PROJECT_DIR, "timequake.db")
_lock = threading.Lock()


def set_db_path(path):
    global _DB_PATH
    _DB_PATH = path


def get_connection():
    """Return a new DB connection, creating tables if needed."""
    conn = sqlite3.connect(_DB_PATH)
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
    conn.commit()
    return conn


def record_span(span):
    """Record a span dict into the shared SQLite store.

    span: dict with keys trace_id, span_id, parent_span_id, service,
          start_time (unix epoch float), end_time (unix epoch float),
          duration_ms, status string, is_fault_run bool.
    """
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


def get_spans_by_trace(trace_id):
    """Return all spans for a given trace_id, ordered by start_time."""
    with _lock:
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT trace_id, span_id, parent_span_id, service, "
                "start_time, end_time, duration_ms, status, is_fault_run, timestamp "
                "FROM spans WHERE trace_id = ? ORDER BY start_time ASC",
                (trace_id,),
            )
            rows = cur.fetchall()
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
        finally:
            conn.close()


def get_all_spans(limit=1000):
    """Return recent spans across all traces, limited by count."""
    with _lock:
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT trace_id, span_id, parent_span_id, service, "
                "start_time, end_time, duration_ms, status, is_fault_run, timestamp "
                "FROM spans ORDER BY start_time ASC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
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
        finally:
            conn.close()


# Convenience: record a span from the service runner
def record_service_span(span_data):
    """Minimal wrapper: span_data dict must have trace_id, span_id, parent_span_id, service, duration_ms."""
    record_span({
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
    })


# Export a default DB path helper for the API module
def init_default_db(path="timequake.db"):
    set_db_path(path)
    get_connection().close()