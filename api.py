"""TimeQuake Backend API Gateway.

Exposes all required REST endpoints:
- POST /hop          - Proxies incoming hop request to the starting service (auth on port 5001)
- GET /api/chain     - Configured service chain list and parameters
- GET /api/health    - Aggregated live health status of all 10 microservices
- GET /api/traces    - List of recent request trace IDs and summaries
- GET /api/trace/<id>- Full causal graph for one trace with RCA, self-times, z-scores, & rollbacks
- GET /api/trace/<id>/rollbacks - Persisted saga rollback rows for one trace
- POST /api/fault   - Arm a live service delay for its next request
- GET /api/anomalies - Per-service z-score histories across recent traces
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
from rca_engine import RCAEngine, build_baseline_from_db
from rollback_engine import trigger_rollback

app = Flask(__name__)
_rca_engine = RCAEngine()
_lock = threading.Lock()


def _evaluate_monitoring(trace_id, analysis):
    """Layer configurable four-signal monitoring over the existing RCA analysis."""
    decisions = {}
    for span_id, detail in analysis.get("details", {}).items():
        service = detail["service"]
        config = tracer.get_monitor_config(service)
        score = analysis.get("z_scores", {}).get(span_id, {})
        mean = max(float(score.get("baseline_mean", 35)), 1.0)
        stdev = max(float(score.get("baseline_stdev", 2)), 1.0)
        value = float(score.get("self_time_ms", 0))
        checks = {
            "over_max_count": (1.0 if value > mean + stdev else 0.0, float(config["thresholds"]["over_max_count"])),
            "delta_of_maximum": (max(0.0, (value - mean) / stdev), float(config["thresholds"]["delta_of_maximum"])),
            "over_average_count": (value / mean, float(config["thresholds"]["over_average_count"])),
            "max_moving_average": (((mean + value) / 2) / mean, float(config["thresholds"]["max_moving_average"])),
        }
        active = [name for name, enabled in config["active_metrics"].items() if enabled and name in checks]
        breached = [name for name in active if checks[name][0] >= checks[name][1]]
        coverage = (len(breached) / len(active) * 100) if active else 0
        status = "anomaly" if coverage >= config["coverage_percent"] else "normal"
        tracer.record_monitor_events([{"trace_id": trace_id, "span_id": span_id, "service": service, "metric": name, "metric_value": checks[name][0], "threshold": checks[name][1], "coverage_percent": coverage, "status": status if name in breached else "normal", "timestamp": detail.get("end_time")} for name in active])
        decisions[span_id] = {"service": service, "status": status, "coverage_percent": coverage, "breached_metrics": breached, "config": config}
    return decisions


def _persist_rollbacks(trace_id, analysis, monitor_decisions=None):
    """Write the RCA compensation path once, then always read it from SQLite."""
    root_cause_service = analysis.get("root_cause_service")
    root_cause_span_id = analysis.get("root_cause_span_id")
    decision = (monitor_decisions or {}).get(root_cause_span_id, {})
    config = decision.get("config") or tracer.get_monitor_config(root_cause_service or "")
    if root_cause_service and root_cause_span_id and config.get("auto_rollback", True):
        trigger_rollback(
            trace_id,
            root_cause_service,
            analysis.get("cascade_path", []),
            root_cause_span_id=root_cause_span_id,
        )
    return tracer.get_rollbacks_by_trace(trace_id)


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Trace-Id, X-Parent-Span-Id'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, OPTIONS'
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
        'Content-Type': 'application/json',
    }
    try:
        r = requests.post(auth_url, headers=headers, json=flask_request.get_json(silent=True) or {}, timeout=30)
        payload = r.json()
        trace_id = payload.get("trace_id")
        if trace_id:
            analysis = _rca_engine.analyze_trace(trace_id)
            _persist_rollbacks(trace_id, analysis, _evaluate_monitoring(trace_id, analysis))
        return jsonify(payload), r.status_code
    except Exception as e:
        return jsonify({'error': f"Failed to connect to auth service on port 5001: {e}"}), 502


@app.route("/api/chain", methods=["GET"])
def api_chain():
    """Return the configured service chain list and specs."""
    return jsonify({
        "services": SERVICES,
        "latency_ranges_ms": CFG.get("default_latency_ranges_ms", {}),
    })


@app.route("/api/fault", methods=["GET", "POST", "OPTIONS"])
def api_fault():
    """Arm a live latency fault for one or more upcoming service requests."""
    if flask_request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    if flask_request.method == "GET":
        return jsonify({"faults": tracer.get_armed_faults()})

    payload = flask_request.get_json(silent=True) or {}
    service = str(payload.get("service", "")).strip()
    if service not in SERVICES:
        return jsonify({"error": "service must be one of the configured services", "services": SERVICES}), 400

    try:
        delay_ms = int(payload.get("delay_ms", 1500))
    except (TypeError, ValueError):
        return jsonify({"error": "delay_ms must be an integer"}), 400
    if delay_ms < 1 or delay_ms > 60000:
        return jsonify({"error": "delay_ms must be between 1 and 60000"}), 400

    try:
        request_count = int(payload.get("request_count", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "request_count must be an integer"}), 400
    if request_count < 1 or request_count > 25:
        return jsonify({"error": "request_count must be between 1 and 25"}), 400

    armed = tracer.arm_fault(service, delay_ms, request_count=request_count)
    return jsonify({
        "status": "armed",
        "message": f"Live fault armed for the next {request_count} request(s) reaching {service}",
        "fault": armed,
    }), 201


@app.route("/api/service/<service>/monitor-config", methods=["GET", "PUT", "OPTIONS"])
def api_monitor_config(service):
    if flask_request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    if service not in SERVICES:
        return jsonify({"error": "Unknown service"}), 404
    if flask_request.method == "GET":
        return jsonify(tracer.get_monitor_config(service))
    current = tracer.get_monitor_config(service)
    return jsonify(tracer.save_monitor_config(service, {**current, **(flask_request.get_json(silent=True) or {})}))


@app.route("/api/service/<service>/override", methods=["POST", "OPTIONS"])
def api_monitor_override(service):
    if flask_request.method == "OPTIONS":
        return jsonify({"status": "ok"})
    if service not in SERVICES:
        return jsonify({"error": "Unknown service"}), 404
    override = (flask_request.get_json(silent=True) or {}).get("state", "auto")
    if override not in ("auto", "normal", "error"):
        return jsonify({"error": "state must be auto, normal, or error"}), 400
    current = tracer.get_monitor_config(service)
    return jsonify(tracer.save_monitor_config(service, {**current, "manual_override": override}))


@app.route("/api/monitor-events", methods=["GET"])
def api_monitor_events():
    return jsonify({"events": tracer.get_monitor_events(service=flask_request.args.get("service"))})


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
                    "active_requests": data.get("active_requests", 0),
                    "last_duration_ms": data.get("last_duration_ms"),
                    "last_trace_id": data.get("last_trace_id"),
                    "last_fault_at": data.get("last_fault_at"),
                    "service": svc_name,
                    "port": port,
                }
            else:
                entry = {"status": "down", "uptime": 0, "request_count": 0, "active_requests": 0, "service": svc_name, "port": port}
        except Exception:
            entry = {"status": "down", "uptime": 0, "request_count": 0, "active_requests": 0, "service": svc_name, "port": port}
        entry["transaction_state"] = tracer.get_service_state(svc_name)
        entry["monitor_config"] = tracer.get_monitor_config(svc_name)
        latest_event = tracer.get_monitor_events(service=svc_name, limit=1)
        override = entry["monitor_config"]["manual_override"]
        if override == "error":
            entry["monitor_state"] = "anomaly"
        elif override == "normal":
            entry["monitor_state"] = "normal"
        elif entry["transaction_state"].get("status") == "rollback_pending":
            entry["monitor_state"] = "rolling_back"
        elif latest_event and latest_event[0]["status"] == "anomaly":
            entry["monitor_state"] = "anomaly"
        else:
            entry["monitor_state"] = "normal"
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

    # Rollback rows are a durable backend side effect, then always read from SQLite.
    rollback_events = _persist_rollbacks(trace_id, analysis, _evaluate_monitoring(trace_id, analysis))
    rollbacks_by_span = {event["span_id"]: event for event in rollback_events}
    state_history = tracer.get_state_history_by_trace(trace_id)
    state_events_by_span = {}
    for event in state_history:
        state_events_by_span.setdefault(event["span_id"], []).append(event)

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
            "rollback_status": rollbacks_by_span.get(sid, {}).get("status"),
            "state_transition": state_events_by_span.get(sid, [])[-1].get("event_type") if state_events_by_span.get(sid) else None,
            "current_state": tracer.get_service_state(svc),
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
        "rollbacks": rollback_events,
        "state_history": state_history,
        "explanation": analysis.get("explanation", ""),
        "nodes": nodes,
        "edges": edges,
        "anomaly_info": analysis.get("anomaly_info", {}),
    })


@app.route("/api/trace/<trace_id>/rollbacks", methods=["GET"])
def api_trace_rollbacks(trace_id):
    """Return the persisted rollback state for one trace."""
    return jsonify({"trace_id": trace_id, "rollbacks": tracer.get_rollbacks_by_trace(trace_id)})


@app.route("/api/trace/<trace_id>/state-history", methods=["GET"])
def api_trace_state_history(trace_id):
    """Return persisted before/after state transitions for the replay UI."""
    return jsonify({"trace_id": trace_id, "state_history": tracer.get_state_history_by_trace(trace_id)})


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

    # Emit one point per service span instead of a combined max-z point per trace.
    # The dashboard groups these live records into dynamic service panels.
    anomalies_ts = []
    # Reuse one snapshot baseline for the batch. It preserves the per-service
    # self-time/z-score algorithm while keeping the live dashboard responsive.
    batch_engine = RCAEngine(build_baseline_from_db())
    for tid, trace_spans in by_trace.items():
        analysis = batch_engine.analyze_trace(tid, refresh_baseline=False)
        z_scores = analysis.get("z_scores", {})
        _persist_rollbacks(tid, analysis)

        for span in trace_spans:
            info = z_scores.get(span["span_id"], {})
            z_score = info.get("z_score", 0.0)
            anomalies_ts.append({
                "trace_id": tid,
                "span_id": span["span_id"],
                "timestamp": span["start_time"],
                "service": span["service"],
                "z_score": z_score,
                "is_fault_run": span.get("is_fault_run", False),
                "is_anomalous": info.get("is_anomalous", False) or span.get("is_fault_run", False),
            })

    anomalies_ts.sort(key=lambda x: x["timestamp"], reverse=False)
    return jsonify({"anomalies": anomalies_ts[-500:]})


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
    batch_engine = RCAEngine(build_baseline_from_db())
    for tid, trace_spans in by_trace.items():
        analysis = batch_engine.analyze_trace(tid, refresh_baseline=False)
        _persist_rollbacks(tid, analysis)
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
