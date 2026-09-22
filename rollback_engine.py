"""Rollback / compensating-transaction engine for TimeQuake.

When a trace is flagged as failed, every service upstream of the root cause
in that trace's cascade path gets a simulated rollback event.

This represents the compensating action a real saga/orchestrator would trigger
— e.g., Inventory releasing a stock reservation because Payment downstream failed.
"""

import sys
import os
import time

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracer


def trigger_rollback(trace_id, root_cause_service, cascade_path=None):
    """Simulate rollback events for services upstream of the root cause.

    Args:
        trace_id: The trace ID being rolled back.
        root_cause_service: The service identified as root cause.
        cascade_path: List of service names from root cause to symptom.

    Returns:
        List of rollback event dicts that were recorded.
    """
    rollback_events = []
    spans = tracer.get_spans_by_trace(trace_id)
    if not spans or not root_cause_service:
        return rollback_events

    spans_sorted = sorted(spans, key=lambda s: s["start_time"])

    root_cause_idx = None
    for i, s in enumerate(spans_sorted):
        if s["service"] == root_cause_service and root_cause_idx is None:
            root_cause_idx = i
            break

    if root_cause_idx is None:
        return rollback_events

    # Services upstream (earlier in trace before root cause) receive rollbacks
    for i in range(root_cause_idx):
        s = spans_sorted[i]
        ev = {
            "trace_id": trace_id,
            "service": s["service"],
            "span_id": s["span_id"],
            "triggered_by_service": root_cause_service,
            "reason": f"Compensating transaction: downstream failure in {root_cause_service}",
            "timestamp": time.time(),
        }
        rollback_events.append(ev)

        # Record span in DB for persistence
        tracer.record_span({
            "trace_id": ev["trace_id"],
            "span_id": f"rb_{ev['span_id']}",
            "parent_span_id": ev["span_id"],
            "service": ev["service"],
            "start_time": ev["timestamp"],
            "end_time": ev["timestamp"] + 0.005,
            "duration_ms": 5,
            "status": "rolled_back",
            "is_fault_run": False,
            "timestamp": ev["timestamp"],
        })

    return rollback_events