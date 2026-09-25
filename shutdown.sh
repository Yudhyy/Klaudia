#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MCP_ARCHIVE_PORT=8001
MCP_GSHEETS_PORT=8002
MCP_LEDGER_PORT=8003
FASTAPI_PORT=${PORT:-8000}
MINIO_API_PORT=${MINIO_API_PORT:-9000}

echo -e "${YELLOW}Stopping Klaudia services...${NC}"

# Kill by PID file
for service in fastapi mcp-ledger minio; do
    pidfile="logs/$service.pid"
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo -e "${GREEN}Stopped $service (PID: $pid)${NC}"
        else
            echo -e "${YELLOW}$service PID $pid already gone${NC}"
        fi
        rm -f "$pidfile"
    else
        echo -e "${YELLOW}No PID file for $service${NC}"
    fi
done

# Fallback: kill anything still holding the ports
echo -e "${YELLOW}Checking for leftover processes on ports...${NC}"
for port in $FASTAPI_PORT $MCP_ARCHIVE_PORT $MCP_GSHEETS_PORT $MCP_LEDGER_PORT $MINIO_API_PORT; do
    pids=$(lsof -ti tcp:$port 2>/dev/null | tr '\n' ' ')
    if [ -n "$pids" ]; then
        kill $pids 2>/dev/null
        echo -e "${GREEN}Killed leftover on port $port (PIDs: $pids)${NC}"
    fi
done

echo -e "${GREEN}All services stopped.${NC}"
