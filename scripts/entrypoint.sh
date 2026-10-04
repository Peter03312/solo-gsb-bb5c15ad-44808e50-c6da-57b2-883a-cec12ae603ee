#!/bin/sh
# 容器入口：确保数据库已初始化（幂等），随后启动服务。
set -e

if command -v python >/dev/null 2>&1; then PYTHON_BIN=python
elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3
else echo "python not found" >&2; exit 1; fi
export PYTHON_BIN

if [ -x /app/scripts/init_db.sh ]; then
  /app/scripts/init_db.sh
else
  scripts/init_db.sh
fi

if command -v uvicorn >/dev/null 2>&1; then
  exec uvicorn app.main:app --host "0.0.0.0" --port "${QSO_PORT:-8000}"
else
  exec "$PYTHON_BIN" -m uvicorn app.main:app --host "0.0.0.0" --port "${QSO_PORT:-8000}"
fi
