import subprocess, time, requests

# Start API on port 5010
proc = subprocess.Popen(['python', 'api.py'], cwd='D:\\timequake', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)

# Test API endpoints
print('Testing API endpoints on port 5010...')
try:
    r = requests.get('http://localhost:5010/api/chain', timeout=5)
    print(f'/api/chain: {r.status_code} - {r.text[:200]}')
except Exception as e:
    print(f'/api/chain error: {e}')

try:
    r = requests.get('http://localhost:5010/api/health', timeout=5)
    print(f'/api/health: {r.status_code} - {r.text[:200]}')
except Exception as e:
    print(f'/api/health error: {e}')

try:
    r = requests.get('http://localhost:5010/api/traces', timeout=5)
    print(f'/api/traces: {r.status_code} - {r.text[:200]}')
except Exception as e:
    print(f'/api/traces error: {e}')

try:
    r = requests.get('http://localhost:5010/api/anomalies', timeout=5)
    print(f'/api/anomalies: {r.status_code} - {r.text[:200]}')
except Exception as e:
    print(f'/api/anomalies error: {e}')

try:
    r = requests.get('http://localhost:5010/api/incidents', timeout=5)
    print(f'/api/incidents: {r.status_code} - {r.text[:200]}')
except Exception as e:
    print(f'/api/incidents error: {e}')