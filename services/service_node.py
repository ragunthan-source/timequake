"""Generic live microservice node for the configurable TimeQuake chain."""

import json
import os
import random
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, request


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracer


_CHAIN_CONFIG_PATH = os.path.join(_PROJECT_DIR, "chain_config.json")


def load_chain_config():
    try:
        with open(_CHAIN_CONFIG_PATH, "r") as file_handle:
            return json.load(file_handle)
    except Exception:
        return {}


CFG = load_chain_config()
SERVICES = CFG.get("services", [])
LATENCY_RANGES = CFG.get("default_latency_ranges_ms", {})
TRACE_ID_HEADER = CFG.get("trace_id_header", "X-Trace-Id")
PARENT_SPAN_HEADER = CFG.get("parent_span_id_header", "X-Parent-Span-Id")


class ServiceNode:
    """A request-processing service whose live faults are read from shared SQLite."""

    def __init__(self, name):
        self.name = name
        self.uptime = time.time()
        self.request_count = 0
        self.active_requests = 0
        self.last_duration_ms = None
        self.last_trace_id = None
        self.last_fault_at = None
        self._state_lock = threading.Lock()
        self.app = Flask(__name__)
        self.port = 5001 + SERVICES.index(name) if name in SERVICES else 5001
        self._setup_routes()

    def _set_request_started(self):
        with self._state_lock:
            self.request_count += 1
            self.active_requests += 1

    def _set_request_finished(self, trace_id, duration_ms, was_fault):
        with self._state_lock:
            self.active_requests = max(0, self.active_requests - 1)
            self.last_duration_ms = duration_ms
            self.last_trace_id = trace_id
            if was_fault:
                self.last_fault_at = time.time()

    def _setup_routes(self):
        @self.app.route("/hop", methods=["POST"])
        def hop():
            self._set_request_started()
            start_time = time.time()
            trace_id = request.headers.get(TRACE_ID_HEADER, "").strip() or f"tr_{uuid.uuid4().hex[:12]}"
            parent_span_id = request.headers.get(PARENT_SPAN_HEADER, "").strip()
            current_span_id = f"sp_{uuid.uuid4().hex[:12]}"

            # A fault is consumed by the already-running target service. No config,
            # environment variable, or request header declares it before startup.
            live_fault = tracer.consume_fault(self.name)
            is_fault_run = live_fault is not None
            fault_delay_ms = live_fault["delay_ms"] if live_fault else 0

            if self.name in LATENCY_RANGES:
                lower_ms, upper_ms = LATENCY_RANGES[self.name]
                own_latency_s = random.uniform(lower_ms / 1000.0, upper_ms / 1000.0)
            else:
                own_latency_s = 0.05
            own_latency_s += fault_delay_ms / 1000.0

            status_str = "ok"
            downstream_data = None
            error_msg = None
            try:
                time.sleep(own_latency_s)
                if self.name in SERVICES:
                    index = SERVICES.index(self.name)
                    if index + 1 < len(SERVICES):
                        next_service = SERVICES[index + 1]
                        next_port = 5001 + index + 1
                        try:
                            import requests
                            downstream_response = requests.post(
                                f"http://127.0.0.1:{next_port}/hop",
                                headers={
                                    TRACE_ID_HEADER: trace_id,
                                    PARENT_SPAN_HEADER: current_span_id,
                                },
                                timeout=60,
                            )
                            downstream_data = downstream_response.json()
                            status_str = "ok" if downstream_response.status_code == 200 else "error"
                        except Exception as error:
                            status_str = "error"
                            error_msg = str(error)
            finally:
                end_time = time.time()
                duration_ms = int((end_time - start_time) * 1000)
                tracer.record_service_span({
                    "trace_id": trace_id,
                    "span_id": current_span_id,
                    "parent_span_id": parent_span_id,
                    "service": self.name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_ms": duration_ms,
                    "status": status_str,
                    "is_fault_run": is_fault_run,
                })
                self._set_request_finished(trace_id, duration_ms, is_fault_run)

            response_headers = {
                TRACE_ID_HEADER: trace_id,
                PARENT_SPAN_HEADER: current_span_id,
            }
            if error_msg:
                return jsonify({
                    "status": f"error forwarding to {next_service}",
                    "error": error_msg,
                    "service": self.name,
                    "trace_id": trace_id,
                    "span_id": current_span_id,
                    "duration_ms": duration_ms,
                }), 500, response_headers
            if downstream_data:
                return jsonify({
                    "status": "forwarded",
                    "service": self.name,
                    "trace_id": trace_id,
                    "span_id": current_span_id,
                    "duration_ms": duration_ms,
                    "downstream_response": downstream_data,
                }), 200, response_headers
            return jsonify({
                "status": "completed",
                "service": self.name,
                "trace_id": trace_id,
                "span_id": current_span_id,
                "duration_ms": duration_ms,
            }), 200, response_headers

        @self.app.route("/health", methods=["GET"])
        def health():
            with self._state_lock:
                return jsonify({
                    "status": "up",
                    "uptime": int(time.time() - self.uptime),
                    "request_count": self.request_count,
                    "active_requests": self.active_requests,
                    "last_duration_ms": self.last_duration_ms,
                    "last_trace_id": self.last_trace_id,
                    "last_fault_at": self.last_fault_at,
                    "service": self.name,
                    "port": self.port,
                }), 200

    def run(self):
        print(f"[Service] {self.name} running on port {self.port}")
        self.app.run(host="0.0.0.0", port=self.port, threaded=True)


if __name__ == "__main__":
    service_name = sys.argv[1] if len(sys.argv) > 1 else "auth"
    ServiceNode(service_name).run()
