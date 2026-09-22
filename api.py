"""TimeQuake Backend API Gateway.

Exposes all required REST endpoints:
- POST /hop          - Proxies incoming hop request to the starting service (auth on port 5001)
- GET /api/chain     - Configured service chain list and parameters
- GET /api/health    - Aggregated live health status of all 10 microservices
- GET /api/traces    - List of recent request trace IDs and summaries
- GET /api/trace/<id>- Full causal graph for one trace with RCA, self-times, z-scores, & rollbacks
- GET /api/anomalies - Time-series of max z-scores across recent traces
- GET /api/incidents - Chronological feed of anomalous/fault-injected traces
"""

import os
import sys
import json
import threading
import requests

# Add project root to sys.path
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
# pyrefly: ignore [missing-import]
from flask import Flask, jsonify, request as flask_request

# Load chain config JSON
_CHAIN_CFG_PATH = os.path.join(_PROJECT_DIR, "chain_config.json")
try:
    with open(_CHAIN_CFG_PATH, "r") as _f:
        CFG = json.load(_f)
except Exception:
    CFG = {}

SERVICES = CFG.get("services", [])
TRACE_ID_HEADER = CFG.get("trace_id_header", "X-Trace-Id")
PARENT_SPAN_HEADER = CFG.get("parent_span_id_header", "X-Parent-Span-Id")

import tracer
from rca_engine import RCAEngine
from rollback_engine import trigger_rollback

app = Flask(__name__)
_rca_engine = RCAEngine()
_lock = threading.Lock()


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Trace-Id, X-Parent-Span-Id, X-Fault-Run'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


@app.route('/hop', methods=['POST', 'OPTIONS'])
def proxy_hop():
    """Proxy entry-point for initiating a trace hop via auth service (port 5001)."""
    if flask_request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    auth_url = "http://127.0.0.1:5001/hop"
    headers = {
        TRACE_ID_HEADER: flask_request.headers.get(TRACE_ID_HEADER, ''),
        PARENT_SPAN_HEADER: flask_request.headers.get(PARENT_SPAN_HEADER, ''),
        'X-Fault-Run': flask_request.headers.get('X-Fault-Run', 'false'),
        'Content-Type': 'application/json',
    }
    try:
        r = requests.post(auth_url, headers=headers, json=flask_request.get_json(silent=True) or {}, timeout=30)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': f"Failed to connect to auth service on port 5001: {e}"}), 502


@app.route("/api/chain", methods=["GET"])
def api_chain():
    """Return the configured service chain list and specs."""
    return jsonify({
        "services": SERVICES,
        "default_fault_service": CFG.get("default_fault_service"),
        "latency_ranges_ms": CFG.get("default_latency_ranges_ms", {}),
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    """Aggregated live status of every service by polling /health in parallel."""
    health_status = {}
    result_lock = threading.Lock()

    def check_service(idx, svc_name):
        port = 5001 + idx
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status_code == 200:
                data = r.json()
                entry = {
                    "status": data.get("status", "up"),
                    "uptime": data.get("uptime", 0),
                    "request_count": data.get("request_count", 0),
                    "service": svc_name,
                    "port": port,
                }
            else:
                entry = {"status": "down", "uptime": 0, "request_count": 0, "service": svc_name, "port": port}
        except Exception:
            entry = {"status": "down", "uptime": 0, "request_count": 0, "service": svc_name, "port": port}
        with result_lock:
            health_status[svc_name] = entry

    threads = [threading.Thread(target=check_service, args=(i, s)) for i, s in enumerate(SERVICES)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)

    return jsonify(health_status)


@app.route("/api/traces", methods=["GET"])
def api_traces():
    """List recent traces (ID, timestamp, initiating service, is_fault_run)."""
    with _lock:
        spans = tracer.get_all_spans(limit=1000)

    trace_map = {}
    for s in spans:
        tid = s["trace_id"]
        if not tid:
            continue
        if tid not in trace_map:
            trace_map[tid] = {
                "trace_id": tid,
                "start_time": s["start_time"],
                "service": s["service"],
                "is_fault_run": s.get("is_fault_run", False),
                "span_count": 1,
            }
        else:
            trace_map[tid]["span_count"] += 1
            if s.get("is_fault_run", False):
                trace_map[tid]["is_fault_run"] = True
            if s["start_time"] < trace_map[tid]["start_time"]:
                trace_map[tid]["start_time"] = s["start_time"]
                trace_map[tid]["service"] = s["service"]

    sorted_traces = sorted(trace_map.values(), key=lambda t: t["start_time"], reverse=True)
    return jsonify({"traces": sorted_traces[:50]})


@app.route("/api/trace/<trace_id>", methods=["GET"])
def api_trace(trace_id):
    """Full causal graph and RCA detail for a specific trace ID."""
    spans = tracer.get_spans_by_trace(trace_id)
    if not spans:
        return jsonify({"error": f"No trace found with ID {trace_id}"}), 404

    # Perform RCA analysis
    analysis = _rca_engine.analyze_trace(trace_id)
    spans_sorted = sorted(spans, key=lambda s: s["start_time"])

    root_cause_svc = analysis.get("root_cause_service")
    cascade_path = analysis.get("cascade_path", [])
    symptom_svc = analysis.get("symptom_service")
    z_scores_map = analysis.get("z_scores", {})
    details_map = analysis.get("details", {})

    # Trigger rollback events persistence if root cause exists and no rollbacks recorded yet
    rollback_events = analysis.get("rollback_events", [])
    if root_cause_svc and not rollback_events:
        rollback_events = trigger_rollback(trace_id, root_cause_svc, cascade_path)

    nodes = []
    span_id_to_node = {}

    for s in spans_sorted:
        sid = s["span_id"]
        z_info = z_scores_map.get(sid, {})
        det_info = details_map.get(sid, {})

        svc = s["service"]
        is_rc = (svc == root_cause_svc)
        is_sym = (svc == symptom_svc)
        is_casc = (svc in cascade_path) and not is_rc

        node = {
            "span_id": sid,
            "service": svc,
            "parent_span_id": s["parent_span_id"],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration_ms": s["duration_ms"],
            "self_time_ms": det_info.get("self_time_ms", s["duration_ms"]),
            "baseline_mean": z_info.get("baseline_mean", 0),
            "baseline_stdev": z_info.get("baseline_stdev", 0),
            "z_score": z_info.get("z_score", 0.0),
            "is_anomalous": z_info.get("is_anomalous", False),
            "is_root_cause": is_rc,
            "is_cascade": is_casc,
            "is_symptom": is_sym,
            "status": s["status"],
            "is_fault_run": s.get("is_fault_run", False),
        }
        nodes.append(node)
        span_id_to_node[sid] = node

    # Edges: parent_span_id -> span_id
    edges = []
    for node in nodes:
        pid = node["parent_span_id"]
        if pid and pid in span_id_to_node:
            edges.append({
                "from": pid,
                "to": node["span_id"],
                "from_service": span_id_to_node[pid]["service"],
                "to_service": node["service"],
            })

    return jsonify({
        "trace_id": trace_id,
        "symptom_service": symptom_svc,
        "root_cause_service": root_cause_svc,
        "cascade_path": cascade_path,
        "rollback_events": rollback_events,
        "explanation": analysis.get("explanation", ""),
        "nodes": nodes,
        "edges": edges,
        "anomaly_info": analysis.get("anomaly_info", {}),
    })


@app.route("/api/anomalies", methods=["GET"])
def api_anomalies():
    """Time-series of z-scores across all recent traces."""
    with _lock:
        spans = tracer.get_all_spans(limit=1500)

    by_trace = {}
    for s in spans:
        tid = s["trace_id"]
        if not tid:
            continue
        if tid not in by_trace:
            by_trace[tid] = []
        by_trace[tid].append(s)

    anomalies_ts = []
    for tid, trace_spans in by_trace.items():
        analysis = _rca_engine.analyze_trace(tid)
        z_scores = analysis.get("z_scores", {})
        
        # Max z-score in trace
        max_z = 0.0
        max_svc = trace_spans[0]["service"] if trace_spans else "unknown"
        is_fault = any(s.get("is_fault_run", False) for s in trace_spans)

        for sid, info in z_scores.items():
            if info.get("z_score", 0) > max_z:
                max_z = info["z_score"]
                max_svc = info.get("service", max_svc)

        start_time = min(s["start_time"] for s in trace_spans)
        anomalies_ts.append({
            "trace_id": tid,
            "timestamp": start_time,
            "service": max_svc,
            "z_score": max_z,
            "is_fault_run": is_fault,
            "is_anomalous": max_z > 2.0 or is_fault,
        })

    anomalies_ts.sort(key=lambda x: x["timestamp"], reverse=False)
    return jsonify({"anomalies": anomalies_ts[-100:]})


@app.route("/api/incidents", methods=["GET"])
def api_incidents():
    """Chronological feed of anomalous traces."""
    with _lock:
        spans = tracer.get_all_spans(limit=1500)

    by_trace = {}
    for s in spans:
        tid = s["trace_id"]
        if not tid:
            continue
        if tid not in by_trace:
            by_trace[tid] = []
        by_trace[tid].append(s)

    incidents = []
    for tid, trace_spans in by_trace.items():
        analysis = _rca_engine.analyze_trace(tid)
        root_cause = analysis.get("root_cause_service")
        is_fault = any(s.get("is_fault_run", False) for s in trace_spans)

        if root_cause or is_fault:
            start_time = min(s["start_time"] for s in trace_spans)
            incidents.append({
                "trace_id": tid,
                "service": root_cause or trace_spans[0]["service"],
                "symptom_service": analysis.get("symptom_service"),
                "timestamp": start_time,
                "is_fault_run": is_fault,
                "explanation": analysis.get("explanation", ""),
            })

    incidents.sort(key=lambda x: x["timestamp"], reverse=True)
    return jsonify({"incidents": incidents[:30]})


def run(app, host="0.0.0.0", port=5000, debug=False):
    """Run the Flask app on Port 5000."""
    tracer.init_default_db()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    tracer.init_default_db()
    run(app)