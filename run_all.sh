#!/bin/bash
echo "========================================================"
echo "       STARTING TIMEQUAKE MICROSERVICES PLATFORM"
echo "========================================================"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

python3 services/service_node.py auth &
python3 services/service_node.py cart &
python3 services/service_node.py inventory &
python3 services/service_node.py pricing &
python3 services/service_node.py discount &
python3 services/service_node.py fraud_check &
python3 services/service_node.py payment &
python3 services/service_node.py shipping &
python3 services/service_node.py notification &
python3 services/service_node.py analytics &
python3 api.py &

cd frontend && npm run dev &

echo "========================================================"
echo "All 10 Microservices + API Gateway + Frontend UI running!"
echo "Dashboard UI : http://localhost:3000"
echo "API Gateway  : http://localhost:5000"
echo "Services     : Ports 5001 to 5010"
echo "========================================================"
