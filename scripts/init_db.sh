#!/bin/sh
# 数据初始化：创建 SQLite 数据库文件并建立全部表结构（幂等）。
# Docker 构建时执行一次；容器入口也会再执行一次，
# 保证挂载空数据卷 / 首次启动时数据库可用。
set -e

DB_PATH="${QSO_DATABASE:-/data/qso.db}"
mkdir -p "$(dirname "$DB_PATH")"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python >/dev/null 2>&1; then PYTHON_BIN=python
  elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3
  else echo "python not found" >&2; exit 1; fi
fi

"$PYTHON_BIN" - <<PY
import os
from app.storage import Database
path = os.environ.get("QSO_DATABASE", "/data/qso.db")
db = Database(path)
db.close()
print("database initialized:", path)
PY
