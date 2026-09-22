import requests
import os
import sys

# Add the project directory to Python path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

import json
with open(os.path.join(project_dir, 'chain_config.json'), 'r') as f:
    CFG = json.load(f)
SERVICES = CFG.get('services', [])

print('=== TimeQuake Core Functionality Test ===')
print()

# 1. Check all services running
print('1. Service Health Check:')
all_up = True
for i, svc in enumerate(['auth', 'cart', 'inventory', 'pricing', 'discount', 'fraud_check', 'payment', 'shipping', 'notification', 'analytics']):
    try:
        r = requests.get(f'http://127.0.0.1:{5001+i}/health', timeout=5)
        data = r.json()
        status = data.get('status', 'unknown')
        if status != 'up':
            all_up = False
        print(f'   {svc:12s}: {status} (requests={data.get("request_count")}, uptime={data.get("uptime")}s)')
    except Exception as e:
        print(f'   {svc:12s}: ERROR {e}')
        all_up = False
print(f'   All services up: {all_up}')
print()

# 2. Send request through the chain
print('2. End-to-End Request:')
try:
    r = requests.post('http://127.0.0.1:5000/hop', headers={}, timeout=30)
    print(f'   Auth hop: status={r.status_code}')
    resp = r.json()
    print(f'   Service: {resp.get("service")}')
    print(f'   Duration: {resp.get("duration_ms")}ms')
    print(f'   Status: {resp.get("status")}')
except Exception as e:
    print(f'   Error: {e}')
print()

# 3. Test API chain configuration
print('3. API Configuration:')
print(f'   Configured services: {SERVICES}')
print(f'   Fault service: {CFG.get("default_fault_service")}')
print(f'   Latency ranges: {CFG.get("default_latency_ranges_ms")}')
print()

# 4. Test fault injection
print('4. Fault Injection Test (inventory):')
try:
    r = requests.post('http://127.0.0.1:5000/hop', 
                     headers={'X-Fault-Run': 'true'}, timeout=30)
    resp = r.json()
    print(f'   Fault run request: service={resp.get("service")}, duration={resp.get("duration_ms")}ms')
    print(f'   Status: {resp.get("status")}')
except Exception as e:
    print(f'   Error: {e}')

print()
print('=== Test Complete ===')