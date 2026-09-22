import requests

# Test health endpoints for all services
print('=== Service Health Check ===')
for i, svc in enumerate(['auth', 'cart', 'inventory', 'pricing', 'discount', 'fraud_check', 'payment', 'shipping', 'notification', 'analytics']):
    try:
        r = requests.get(f'http://localhost:{5000+i}/health', timeout=5)
        data = r.json()
        print(f'{svc:12s}: status={data.get("status")} requests={data.get("request_count")} uptime={data.get("uptime")}s')
    except Exception as e:
        print(f'{svc:12s}: ERROR {e}')
print()

# Send single hop request
print('=== Sending single hop request ===')
try:
    r = requests.post('http://localhost:5000/hop', headers={}, timeout=30)
    print(f'Status: {r.status_code}')
    print(f'Response: {r.json()}')
except Exception as e:
    print(f'Error: {e}')

# Test API endpoints
print()
print('=== API Endpoints ===')
try:
    r = requests.get('http://localhost:5000/api/chain', timeout=5)
    print(f'/api/chain: {r.json()}')
except Exception as e:
    print(f'/api/chain error: {e}')

try:
    r = requests.get('http://localhost:5000/api/health', timeout=5)
    print(f'/api/health: {r.json()}')
except Exception as e:
    print(f'/api/health error: {e}')