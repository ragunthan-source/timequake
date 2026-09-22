"""Generic microservice node parametrized by name and config.

This module defines ServiceNode instances that form the configurable chain.
Each service:
- Exposes POST /hop - does simulated work (randomized latency within configured range),
  optionally injects a fault (only on "fault run" requests), then calls the next service
  and forwards the response.
- Exposes GET /health - returns status, uptime, and current request count.
- Forwards trace_id and parent_span_id via headers.
- The first service in the chain generates a new trace_id if none is passed.
"""

import json
import time
import random
import os
import sys
import uuid
import threading
# pyrefly: ignore [missing-import]
from flask import Flask, request, jsonify

# Add parent directory to sys.path to import tracer
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracer

# Load chain config
_CHAIN_CONFIG_PATH = os.path.join(_PROJECT_DIR, 'chain_config.json')


def load_chain_config():
    try:
        with open(_CHAIN_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return None


CFG = load_chain_config()
SERVICES = CFG['services'] if CFG else []
LATENCY_RANGES = CFG['default_latency_ranges_ms'] if CFG else {}
FAULT_SERVICE = CFG.get('default_fault_service') if CFG else None
TRACE_ID_HEADER = CFG.get('trace_id_header', 'X-Trace-Id') if CFG else 'X-Trace-Id'
PARENT_SPAN_HEADER = CFG.get('parent_span_id_header', 'X-Parent-Span-Id') if CFG else 'X-Parent-Span-Id'

# Registry: name -> ServiceNode instance
_registry = {}


def get_service(name):
    return _registry.get(name)


class ServiceNode:
    """A single service in the microservices chain."""

    def __init__(self, name):
        self.name = name
        self.uptime = time.time()
        self.request_count = 0
        self.fault_injection_enabled = False
        self.app = Flask(__name__)
        # Port: base 5001 + index in chain (5000 reserved for API gateway)
        if SERVICES and self.name in SERVICES:
            idx = SERVICES.index(self.name)
            self.port = 5001 + idx
        else:
            self.port = 5001
        self._setup_routes()
        _registry[self.name] = self

    def _setup_routes(self):

        @self.app.route('/hop', methods=['POST'])
        def hop():
            self.request_count += 1
            start_time = time.time()

            # Extract or generate trace_id
            trace_id = request.headers.get(TRACE_ID_HEADER, '').strip()
            if not trace_id:
                trace_id = f"tr_{uuid.uuid4().hex[:12]}"

            # Parent span id is incoming X-Parent-Span-Id
            parent_span_id = request.headers.get(PARENT_SPAN_HEADER, '').strip()

            # Current span id for THIS service hop
            current_span_id = f"sp_{uuid.uuid4().hex[:12]}"

            # Determine if this is a fault-run request
            is_fault_run = request.headers.get('X-Fault-Run', '').lower() == 'true'

            # Reload config dynamically in case fault_service changed
            fresh_cfg = load_chain_config()
            target_fault_svc = fresh_cfg.get('default_fault_service') if fresh_cfg else FAULT_SERVICE

            # Simulate own work: randomized latency within configured normal range
            if self.name in LATENCY_RANGES:
                lo, hi = LATENCY_RANGES[self.name]
                own_latency_s = random.uniform(lo / 1000.0, hi / 1000.0)
            else:
                own_latency_s = 0.05  # default 50ms

            # ONLY inject artificial delay if this is a fault run and this service is targeted
            if is_fault_run and target_fault_svc and self.name == target_fault_svc:
                own_latency_s += random.uniform(1.2, 2.5)  # artificial fault delay

            # Sleep to simulate own work
            time.sleep(own_latency_s)

            # Determine next service in chain
            next_service_name = None
            next_service_port = None
            if SERVICES and self.name in SERVICES:
                idx = SERVICES.index(self.name)
                if idx + 1 < len(SERVICES):
                    next_service_name = SERVICES[idx + 1]
                    next_service_port = 5001 + (idx + 1)

            status_str = "ok"
            downstream_data = None
            error_msg = None

            # Forward request if downstream service exists
            if next_service_name:
                try:
                    import requests as _req
                    forward_resp = _req.post(
                        f'http://127.0.0.1:{next_service_port}/hop',
                        headers={
                            TRACE_ID_HEADER: trace_id,
                            PARENT_SPAN_HEADER: current_span_id,
                            'X-Fault-Run': 'true' if is_fault_run else 'false',
                        },
                        timeout=60
                    )
                    downstream_data = forward_resp.json()
                    status_str = "ok" if forward_resp.status_code == 200 else "error"
                except Exception as e:
                    status_str = "error"
                    error_msg = str(e)

            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)

            # Record span into shared tracer store
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

            # Build response headers
            resp_headers = {
                TRACE_ID_HEADER: trace_id,
                PARENT_SPAN_HEADER: current_span_id,
            }

            if error_msg:
                return jsonify({
                    'status': 'error forwarding to ' + str(next_service_name),
                    'error': error_msg,
                    'service': self.name,
                    'trace_id': trace_id,
                    'span_id': current_span_id,
                    'duration_ms': duration_ms,
                }), 500, resp_headers
            elif downstream_data:
                return jsonify({
                    'status': 'forwarded',
                    'service': self.name,
                    'trace_id': trace_id,
                    'span_id': current_span_id,
                    'duration_ms': duration_ms,
                    'downstream_response': downstream_data,
                }), 200, resp_headers
            else:
                return jsonify({
                    'status': 'completed',
                    'service': self.name,
                    'trace_id': trace_id,
                    'span_id': current_span_id,
                    'duration_ms': duration_ms,
                }), 200, resp_headers

        @self.app.route('/health', methods=['GET'])
        def health():
            uptime_s = time.time() - self.uptime
            return jsonify({
                'status': 'up',
                'uptime': int(uptime_s),
                'request_count': self.request_count,
                'service': self.name,
                'port': self.port
            }), 200

    def run(self):
        print(f'[Service] {self.name} running on port {self.port}')
        try:
            self.app.run(host='0.0.0.0', port=self.port, threaded=True)
        except Exception as e:
            print(f'[Service] {self.name} error: {e}')


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'auth'
    node = ServiceNode(name)
    node.run()