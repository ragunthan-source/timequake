#!/bin/bash
echo "Stopping all TimeQuake services..."
pkill -f service_node.py 2>/dev/null || true
pkill -f api.py 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
echo "All TimeQuake services stopped."
