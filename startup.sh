#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FASTAPI_PORT=${PORT:-8000}
MCP_LEDGER_PORT=8003
MINIO_API_PORT=${MINIO_API_PORT:-9000}
MINIO_CONSOLE_PORT=${MINIO_CONSOLE_PORT:-9001}
MINIO_DATA_DIR=${MINIO_DATA_DIR:-"$HOME/codex/minio/data"}

MCP_TRANSPORT=${MCP_TRANSPORT:-stdio}
DATABASE_URL=${DATABASE_URL:-postgresql://klaudia:klaudia@localhost:5432/klaudia}

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

mkdir -p "$PROJECT_DIR/logs"

echo -e "${GREEN}Starting Klaudia services (MCP_TRANSPORT=$MCP_TRANSPORT)...${NC}"

# Start MinIO
echo -e "${YELLOW}Starting MinIO (data: $MINIO_DATA_DIR)...${NC}"
minio server "$MINIO_DATA_DIR" \
  --address ":$MINIO_API_PORT" \
  --console-address ":$MINIO_CONSOLE_PORT" \
  > "$PROJECT_DIR/logs/minio.log" 2>&1 &
echo $! > "$PROJECT_DIR/logs/minio.pid"
echo -e "${GREEN}MinIO started (PID: $(cat "$PROJECT_DIR/logs/minio.pid"))${NC}"
echo -e "${YELLOW}Waiting for MinIO to start...${NC}"
sleep 2

if [ "$MCP_TRANSPORT" = "http" ] || [ "$MCP_TRANSPORT" = "sse" ]; then
  echo -e "${YELLOW}Starting MCP-Ledger on port $MCP_LEDGER_PORT...${NC}"
  cd "$PROJECT_DIR/mcp-ledger"
  FASTMCP_PORT=$MCP_LEDGER_PORT DATABASE_URL="$DATABASE_URL" \
    "$PYTHON" main.py --transport "$MCP_TRANSPORT" > "$PROJECT_DIR/logs/mcp-ledger.log" 2>&1 &
  echo $! > "$PROJECT_DIR/logs/mcp-ledger.pid"

  echo -e "${YELLOW}Waiting for MCP servers to start...${NC}"
  sleep 3
else
  echo -e "${YELLOW}stdio mode: MCP servers will be spawned by FastAPI as subprocesses.${NC}"
fi

# Start FastAPI
echo -e "${YELLOW}Starting FastAPI on port $FASTAPI_PORT...${NC}"
cd "$PROJECT_DIR"
MCP_TRANSPORT=$MCP_TRANSPORT \
  DATABASE_URL="$DATABASE_URL" \
  "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT \
  > "$PROJECT_DIR/logs/fastapi.log" 2>&1 &
echo $! > "$PROJECT_DIR/logs/fastapi.pid"
echo -e "${GREEN}FastAPI started (PID: $(cat "$PROJECT_DIR/logs/fastapi.pid"))${NC}"

echo -e "${GREEN}All services started.${NC}"
echo -e "  FastAPI:     http://localhost:$FASTAPI_PORT"
echo -e "  MinIO API:   http://localhost:$MINIO_API_PORT"
echo -e "  MinIO UI:    http://localhost:$MINIO_CONSOLE_PORT"
if [ "$MCP_TRANSPORT" = "http" ] || [ "$MCP_TRANSPORT" = "sse" ]; then
  echo -e "  MCP-Ledger:  http://localhost:$MCP_LEDGER_PORT"
fi
