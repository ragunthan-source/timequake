@echo off
echo Stopping all TimeQuake services and python processes...
taskkill /F /IM python.exe /T 2>nul
taskkill /F /IM node.exe /T 2>nul
echo All TimeQuake services stopped.
