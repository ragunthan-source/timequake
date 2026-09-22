@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo        STARTING TIMEQUAKE MICROSERVICES PLATFORM
echo ========================================================

cd /d "%~dp0"

echo [1/12] Starting auth service (Port 5001)...
start /B python services/service_node.py auth

echo [2/12] Starting cart service (Port 5002)...
start /B python services/service_node.py cart

echo [3/12] Starting inventory service (Port 5003)...
start /B python services/service_node.py inventory

echo [4/12] Starting pricing service (Port 5004)...
start /B python services/service_node.py pricing

echo [5/12] Starting discount service (Port 5005)...
start /B python services/service_node.py discount

echo [6/12] Starting fraud_check service (Port 5006)...
start /B python services/service_node.py fraud_check

echo [7/12] Starting payment service (Port 5007)...
start /B python services/service_node.py payment

echo [8/12] Starting shipping service (Port 5008)...
start /B python services/service_node.py shipping

echo [9/12] Starting notification service (Port 5009)...
start /B python services/service_node.py notification

echo [10/12] Starting analytics service (Port 5010)...
start /B python services/service_node.py analytics

echo [11/12] Starting TimeQuake API Gateway (Port 5000)...
start /B python api.py

echo [12/12] Starting Frontend Dashboard Server (Port 3000)...
cd frontend
start /B npm run dev
cd ..

echo ========================================================
echo All 10 Microservices + API Gateway + Frontend UI running!
echo Dashboard UI : http://localhost:3000
echo API Gateway  : http://localhost:5000
echo Services     : Ports 5001 to 5010
echo ========================================================