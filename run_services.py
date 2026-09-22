"""TimeQuake Service Orchestrator.

Launches all 10 microservices (ports 5001-5010), the API gateway (port 5000),
and the Vite frontend dev server (port 3000) using subprocesses.
"""

import subprocess
import sys
import os
import time
import json

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_PROJECT_DIR, "chain_config.json"), "r") as f:
    cfg = json.load(f)

SERVICES = cfg.get("services", [])

processes = []

def start_all():
    print("========================================================")
    print("       STARTING TIMEQUAKE MICROSERVICES PLATFORM        ")
    print("========================================================")

    # 1. Start Microservices
    for idx, svc in enumerate(SERVICES):
        port = 5001 + idx
        print(f"[{idx+1}/12] Starting microservice '{svc}' on port {port}...")
        p = subprocess.Popen(
            [sys.executable, "services/service_node.py", svc],
            cwd=_PROJECT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        processes.append(p)
        time.sleep(0.3)

    # 2. Start API Gateway
    print("[11/12] Starting API Gateway on port 5000...")
    p_api = subprocess.Popen(
        [sys.executable, "api.py"],
        cwd=_PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes.append(p_api)
    time.sleep(0.5)

    # 3. Start Frontend Dev Server
    frontend_dir = os.path.join(_PROJECT_DIR, "frontend")
    print("[12/12] Starting Frontend Dev Server on port 3000...")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    p_front = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=frontend_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes.append(p_front)

    print("\n========================================================")
    print("All 10 Microservices + API Gateway + Frontend UI running!")
    print("  Dashboard UI : http://localhost:3000")
    print("  API Gateway  : http://localhost:5000")
    print("  Microservices: Ports 5001 to 5010")
    print("========================================================")

if __name__ == "__main__":
    start_all()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping all services...")
        for p in processes:
            p.terminate()
