#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON="$PROJECT_ROOT/.venv-macos/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "未找到 macOS Python 环境：.venv-macos" >&2
  echo "请先执行：" >&2
  echo "  python3.11 -m venv .venv-macos" >&2
  echo "  ./.venv-macos/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

echo "UAV 实验平台正在启动：http://127.0.0.1:8000"
exec "$PYTHON" -m uav_sim.main
