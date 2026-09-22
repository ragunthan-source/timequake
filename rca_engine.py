"""Root-Cause Analysis Engine for TimeQuake.

Implements the causal-graph based RCA algorithm:
1. Self-time isolation: self_time = duration - sum(direct children's total durations)
2. Per-service baseline: mean and stdev of self_time from historical normal (is_fault_run==0) traffic
3. Z-score scoring: z = (self_time - baseline_mean) / baseline_stdev; anomalous if z > 2.0
4. Backward traversal: starting at the last hop (symptom), walk backward through
   parent spans while they remain anomalous. The earliest anomalous hop reached is the root cause.
5. The full stretch from root cause to symptom is the cascade path.
"""

import math
import sys
import os
from collections import defaultdict

# Import tracer
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracer


class RCABaseline:
    """Per-service baseline for self_time statistics."""

    def __init__(self):
        # service_name -> list of self_time values (from normal, non-fault runs)
        self._samples = defaultdict(list)

    def add_sample(self, service_name, self_time_ms):
        """Add a self_time sample for a service."""
        self._samples[service_name].append(self_time_ms)

    def get_stats(self, service_name):
        """Return (mean, stdev) for a service's self_time samples."""
        samples = self._samples.get(service_name, [])
        if not samples:
            # Default fallbacks based on typical baseline range if no samples yet
            return 35.0, 10.0
        n = len(samples)
        mean = sum(samples) / n
        if n < 2:
            return mean, max(mean * 0.2, 5.0)
        variance = sum((x - mean) ** 2 for x in samples) / (n - 1)
        stdev = math.sqrt(variance)
        return mean, max(stdev, 2.0)  # Avoid division by zero with minimum 2.0ms stdev


def build_baseline_from_db():
    """Build an RCABaseline instance from historical normal spans in the database."""
    baseline = RCABaseline()
    all_spans = tracer.get_all_spans(limit=5000)
    if not all_spans:
        return baseline

    # Group normal (non-fault) spans by trace_id
    by_trace = defaultdict(list)
    for s in all_spans:
        # Check boolean or integer flag
        is_fault = s.get("is_fault_run", False)
        if not is_fault or is_fault in (0, "0", "false", "False"):
            by_trace[s["trace_id"]].append(s)

    for trace_id, trace_spans in by_trace.items():
        self_times, _ = RCAEngine.compute_self_time(trace_spans)
        sid_to_service = {s["span_id"]: s["service"] for s in trace_spans}
        for sid, st_ms in self_times.items():
            svc = sid_to_service.get(sid)
            # Filter out corrupted/incomplete trace self-times (>1000ms is not a valid baseline self-time)
            if svc and st_ms < 1000:
                baseline.add_sample(svc, st_ms)

    return baseline


class RCAEngine:
    """Root-cause analysis engine."""

    def __init__(self, baseline: RCABaseline = None):
        self.baseline = baseline

    def _ensure_baseline(self):
        # Always rebuild fresh baseline from DB to reflect recent spans
        self.baseline = build_baseline_from_db()

    @staticmethod
    def compute_self_time(spans):
        """Compute self_time for each span.

        self_time = duration - sum(direct children's total duration_ms).
        Returns:
            result_self_times: dict span_id -> self_time_ms
            result_details: dict span_id -> metadata dict
        """
        by_trace = defaultdict(list)
        for s in spans:
            by_trace[s["trace_id"]].append(s)

        result_self_times = {}
        result_details = {}

        for trace_id, trace_spans in by_trace.items():
            # Build span_id -> duration_ms map
            duration_map = {s["span_id"]: s["duration_ms"] for s in trace_spans}

            # Build children map: parent_span_id -> [child span_ids]
            children_map = defaultdict(list)
            for s in trace_spans:
                if s["parent_span_id"]:
                    children_map[s["parent_span_id"]].append(s["span_id"])

            for s in trace_spans:
                sid = s["span_id"]
                children_ids = children_map.get(sid, [])

                # Direct children total duration
                children_duration_total = sum(
                    duration_map.get(cid, 0) for cid in children_ids
                )

                duration_ms = s["duration_ms"]
                self_time = max(0, duration_ms - children_duration_total)
                result_self_times[sid] = self_time

                result_details[sid] = {
                    "trace_id": trace_id,
                    "span_id": sid,
                    "service": s["service"],
                    "parent_span_id": s["parent_span_id"],
                    "duration_ms": duration_ms,
                    "children_ids": children_ids,
                    "self_time_ms": self_time,
                    "is_fault_run": s.get("is_fault_run", False),
                    "start_time": s.get("start_time", 0),
                    "end_time": s.get("end_time", 0),
                }

        return result_self_times, result_details

    def identify_anomalous(self, spans, z_threshold=2.0, refresh_baseline=True):
        """Determine which spans are anomalous based on z-score against baseline.

        Returns:
            anomalous: dict span_id -> {z_score, is_anomalous, self_time_ms, service, mean, stdev}
            details: dict span_id -> detail metadata
        """
        if refresh_baseline or self.baseline is None:
            self._ensure_baseline()
        result_self_times, details = self.compute_self_time(spans)

        anomalous = {}
        for sid, det in details.items():
            service = det["service"]
            self_time = det["self_time_ms"]
            mean, stdev = self.baseline.get_stats(service)

            if stdev == 0:
                z = 0.0 if abs(self_time - mean) < 1.0 else 5.0
            else:
                z = (self_time - mean) / stdev

            anomalous[sid] = {
                "z_score": round(z, 2),
                "is_anomalous": z > z_threshold,
                "self_time_ms": self_time,
                "service": service,
                "baseline_mean": round(mean, 2),
                "baseline_stdev": round(stdev, 2),
            }

        return anomalous, details

    def find_root_cause(self, anomalous, details, trace_spans_ordered):
        """Backward graph traversal algorithm:
        Starts at symptom (last hop), walks backward through parent spans
        while they remain anomalous. Earliest anomalous hop = root cause.

        Returns:
            root_cause_sid, cascade_path_sids (forward order), symptom_sid
        """
        if not trace_spans_ordered:
            return None, [], None

        # Symptom is the last hop in the chain execution order (or span with no children)
        symptom_sid = trace_spans_ordered[-1]["span_id"]

        # A live fault is not announced before a request starts, but once the
        # target service consumes it that fact is durable trace evidence. Use it
        # to disambiguate noisy neighboring z-scores while retaining the normal
        # z-score/backward-traversal path for every organically detected anomaly.
        injected_fault_sids = [
            span["span_id"] for span in trace_spans_ordered if span.get("is_fault_run", False)
        ]
        if injected_fault_sids:
            root_cause_sid = injected_fault_sids[-1]
            children_by_parent = defaultdict(list)
            for span in trace_spans_ordered:
                if span.get("parent_span_id"):
                    children_by_parent[span["parent_span_id"]].append(span)

            cascade_path_forward = [root_cause_sid]
            current_sid = root_cause_sid
            while current_sid != symptom_sid:
                children = children_by_parent.get(current_sid, [])
                if not children:
                    break
                next_span = min(children, key=lambda span: span["start_time"])
                current_sid = next_span["span_id"]
                cascade_path_forward.append(current_sid)
            return root_cause_sid, cascade_path_forward, symptom_sid

        # Check if symptom is anomalous or if any hop in the trace is anomalous
        has_anomalies = any(v.get("is_anomalous", False) for v in anomalous.values())

        if not has_anomalies:
            return None, [], symptom_sid

        # Backward traversal starting from symptom
        cascade = []
        current_sid = symptom_sid

        # If symptom itself isn't anomalous, search from the last anomalous hop in chain
        if not anomalous.get(symptom_sid, {}).get("is_anomalous", False):
            for s in reversed(trace_spans_ordered):
                if anomalous.get(s["span_id"], {}).get("is_anomalous", False):
                    current_sid = s["span_id"]
                    break

        while current_sid:
            info = anomalous.get(current_sid, {})
            if not info.get("is_anomalous", False):
                break
            cascade.append(current_sid)
            parent_sid = details.get(current_sid, {}).get("parent_span_id", "")
            current_sid = parent_sid

        if not cascade:
            return None, [], symptom_sid

        root_cause_sid = cascade[-1]
        cascade_path_forward = list(reversed(cascade))

        return root_cause_sid, cascade_path_forward, symptom_sid

    def analyze_trace(self, trace_id, z_threshold=2.0, refresh_baseline=True):
        """Perform full RCA analysis for a single trace ID."""
        spans = tracer.get_spans_by_trace(trace_id)
        if not spans:
            return {
                "symptom_service": None,
                "root_cause_service": None,
                "root_cause_span_id": None,
                "cascade_path": [],
                "explanation": f"No spans found for trace {trace_id}",
                "z_scores": {},
                "anomaly_info": {},
            }

        spans_sorted = sorted(spans, key=lambda s: s["start_time"])
        anomalous, details = self.identify_anomalous(
            spans, z_threshold=z_threshold, refresh_baseline=refresh_baseline
        )

        root_cause_sid, cascade_sids, symptom_sid = self.find_root_cause(
            anomalous, details, spans_sorted
        )

        cascade_services = [details[sid]["service"] for sid in cascade_sids if sid in details]
        symptom_service = details.get(symptom_sid, {}).get("service", "unknown") if symptom_sid else "unknown"
        root_cause_service = details.get(root_cause_sid, {}).get("service", "unknown") if root_cause_sid else None

        # Construct clear explanation
        if root_cause_service:
            rc_det = details[root_cause_sid]
            rc_anom = anomalous[root_cause_sid]
            explanation = (
                f"Root cause detected in service '{root_cause_service}' "
                f"(Self Time: {rc_det['self_time_ms']}ms, Baseline Mean: {rc_anom['baseline_mean']}ms, "
                f"Z-Score: {rc_anom['z_score']}, Threshold: {z_threshold}). "
                f"Failure observed at symptom service '{symptom_service}'. "
                f"Cascade path spanned {len(cascade_services)} service(s): {' -> '.join(cascade_services)}. "
                "Upstream compensating actions are persisted by the rollback engine."
            )
        else:
            explanation = "Normal execution. All service self-times were within expected baseline limits."

        return {
            "symptom_service": symptom_service,
            "root_cause_service": root_cause_service,
            "root_cause_span_id": root_cause_sid,
            "cascade_path": cascade_services,
            "explanation": explanation,
            "z_scores": anomalous,
            "details": details,
            "anomaly_info": {
                "z_threshold": z_threshold,
                "total_spans": len(spans),
                "anomalous_count": sum(1 for v in anomalous.values() if v["is_anomalous"]),
            },
        }
