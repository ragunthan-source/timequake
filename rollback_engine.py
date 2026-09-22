"""Durable saga-style rollback orchestration for anomalous TimeQuake traces."""

import os
import sys
import threading
import time


_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracer


ROLLBACK_COMPLETION_DELAY_SECONDS = 0.9


def _complete_later(rollback_id):
    """Transition a persisted rollback after a short observable pending phase."""
    timer = threading.Timer(ROLLBACK_COMPLETION_DELAY_SECONDS, tracer.complete_rollback, args=(rollback_id,))
    timer.daemon = True
    timer.start()


def trigger_rollback(trace_id, root_cause_service, cascade_path=None, root_cause_span_id=None):
    """Persist compensating actions for every direct upstream ancestor of a root cause.

    The SQLite uniqueness constraint makes this safe to call from the live request
    path and again from polling endpoints.  Only newly created rows are scheduled
    for completion, so the status transition is a real backend/database event.
    """
    if not trace_id or not root_cause_service:
        return tracer.get_rollbacks_by_trace(trace_id)

    spans = tracer.get_spans_by_trace(trace_id)
    if not spans:
        return []

    by_span_id = {span["span_id"]: span for span in spans}
    root_span = by_span_id.get(root_cause_span_id) if root_cause_span_id else None
    if root_span is None:
        root_span = next((span for span in spans if span["service"] == root_cause_service), None)
    if root_span is None:
        return []

    now = time.time()
    events = []
    current_parent_id = root_span.get("parent_span_id")
    while current_parent_id:
        ancestor = by_span_id.get(current_parent_id)
        if ancestor is None:
            break
        events.append({
            "trace_id": trace_id,
            "service": ancestor["service"],
            "span_id": ancestor["span_id"],
            "triggered_by_service": root_cause_service,
            "reason": f"Compensating transaction: downstream root cause in {root_cause_service}",
            "timestamp": now,
        })
        current_parent_id = ancestor.get("parent_span_id")

    created = tracer.create_rollbacks(events)
    for rollback in created:
        _complete_later(rollback["id"])

    return tracer.get_rollbacks_by_trace(trace_id)
