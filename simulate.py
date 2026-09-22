"""TimeQuake Demo Traffic Generation Script.

Generates normal baseline traffic and fault-injected traffic to demonstrate
causal-graph based root-cause analysis (RCA) and compensating rollbacks.

Usage:
    python simulate.py                        # sends 20 normal baseline requests
    python simulate.py --fault inventory      # sends normal baseline + 1 fault run on inventory
    python simulate.py --fault payment        # sends normal baseline + 1 fault run on payment
    python simulate.py --single --fault cart  # sends single fault run on cart
"""

import sys
import time
import json
import argparse
import urllib.request
import urllib.parse
import os

BASE_URL = "http://127.0.0.1:5000"
CHAIN_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chain_config.json")


def load_chain_config():
    try:
        with open(CHAIN_CONFIG, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not load chain config: {e}")
        return None


def save_chain_config(cfg):
    try:
        with open(CHAIN_CONFIG, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Could not save chain config: {e}")


def send_hop(trace_id=None, fault_run=False):
    """Send a POST /hop request to the API gateway (which proxies to auth)."""
    url = f"{BASE_URL}/hop"
    headers = {
        "Content-Type": "application/json",
    }
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    if fault_run:
        headers["X-Fault-Run"] = "true"

    data = json.dumps({}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            return json.loads(body), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e.fp else "{}"
        try:
            return json.loads(body), e.code
        except Exception:
            return {"error": str(e)}, e.code
    except Exception as e:
        return {"error": str(e)}, None


def fetch_api(path):
    """Fetch JSON from the API backend."""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="TimeQuake Traffic Generator & Demo Suite")
    parser.add_argument("--fault", type=str, default=None,
                        help="Service to inject fault into (e.g. inventory, payment, cart)")
    parser.add_argument("--normal", type=int, default=20,
                        help="Number of normal requests to send for baseline building (default: 20)")
    parser.add_argument("--single", action="store_true",
                        help="Send single fault-injected request only (skip baseline phase)")
    args = parser.parse_args()

    cfg = load_chain_config()
    if not cfg:
        print("[ERROR] Could not load chain_config.json")
        sys.exit(1)

    if args.fault:
        if args.fault not in cfg.get("services", []):
            print(f"[WARNING] Fault service '{args.fault}' not in configured chain: {cfg.get('services')}")
        cfg["default_fault_service"] = args.fault
        save_chain_config(cfg)
        print(f"[CONFIG] Target fault service set to: '{args.fault}'")

    print("\n" + "=" * 65)
    print("           TIMEQUAKE TIME-TRAVEL DEBUGGER DEMO")
    print("=" * 65)

    trace_ids = []

    # Phase 1: Baseline Generation
    if not args.single and args.normal > 0:
        print(f"\n[PHASE 1] Generating Baseline Traffic ({args.normal} normal requests)...")
        print("Progress: ", end="", flush=True)
        for i in range(args.normal):
            res, status = send_hop(fault_run=False)
            tid = res.get("trace_id", f"normal_{i}")
            trace_ids.append(tid)
            print(".", end="", flush=True)
            time.sleep(0.05)
        print(" Done!")
        print(f"[PHASE 1] Recorded {args.normal} normal traces for baseline calculation.")

    # Phase 2: Fault Injection
    if args.fault or args.single:
        target_svc = args.fault or cfg.get("default_fault_service", "inventory")
        print(f"\n[PHASE 2] Injecting Artificial Fault into Service '{target_svc}'...")
        fault_trace_id = f"fault_{int(time.time())}"
        
        print(f"--> Sending fault-injected request (X-Fault-Run: true)...")
        t0 = time.time()
        res, status = send_hop(trace_id=fault_trace_id, fault_run=True)
        t_elapsed = round(time.time() - t0, 3)
        actual_tid = res.get("trace_id", fault_trace_id)
        trace_ids.append(actual_tid)

        print(f"[RECEIVED] Request completed in {t_elapsed}s. Trace ID: {actual_tid}")
        time.sleep(0.5)

        # Phase 3: Fetch RCA Report from API
        print(f"\n[PHASE 3] Executing Causal Graph Root-Cause Analysis for Trace: {actual_tid}...")
        analysis = fetch_api(f"/api/trace/{actual_tid}")

        if "error" in analysis:
            print(f"[ERROR] RCA analysis query failed: {analysis['error']}")
        else:
            print("\n" + "-" * 65)
            print("                 TIMEQUAKE RCA FINDINGS")
            print("-" * 65)
            print(f"  Visible Symptom Service : {analysis.get('symptom_service', 'N/A')}")
            print(f"  Flagged ROOT CAUSE      : {analysis.get('root_cause_service', 'N/A')}")
            print(f"  Cascade Path            : {' -> '.join(analysis.get('cascade_path', []))}")
            print(f"  Upstream Rollbacks      : {len(analysis.get('rollback_events', []))} compensating event(s)")
            print("\n  Plain-English Explanation:")
            print(f"  \"{analysis.get('explanation', '')}\"")

            print("\n  Per-Service Hop Metrics:")
            print(f"  {'Service':<14} {'Duration':<10} {'Self-Time':<10} {'Baseline Mean':<14} {'Z-Score':<10} {'Anomalous?'}")
            print("  " + "-" * 72)
            for node in analysis.get("nodes", []):
                svc = node["service"]
                dur = f"{node['duration_ms']}ms"
                st = f"{node['self_time_ms']}ms"
                bm = f"{node['baseline_mean']}ms"
                zs = f"{node['z_score']:.2f}"
                is_anom = "YES (ROOT CAUSE)" if node.get("is_root_cause") else ("YES (CASCADE)" if node.get("is_cascade") else "No")
                print(f"  {svc:<14} {dur:<10} {st:<10} {bm:<14} {zs:<10} {is_anom}")

            if analysis.get("rollback_events"):
                print("\n  Compensating Rollback Actions (Saga Pattern):")
                for rb in analysis["rollback_events"]:
                    print(f"  [ROLLBACK] Service '{rb['service']}' -> Reason: {rb['reason']}")

            print("-" * 65)

    print(f"\n[SUCCESS] TimeQuake simulation complete. Open dashboard at http://localhost:3000 to view.")


if __name__ == "__main__":
    main()