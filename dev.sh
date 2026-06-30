#!/usr/bin/env bash
# 本地后端开发启动脚本（macOS / Linux / Windows-Git-Bash 通用）
#   1. 自动 source venv（.venv，自适应 bin/activate 与 Scripts/activate）
#   2. 杀掉占用目标端口（默认 8020）的旧进程（跨平台：lsof / netstat+taskkill）
#   3. 用 fba run 启动（默认开启热重载——改代码/schema 自动生效，省去手动重启）
#      默认 **单 worker**（--workers 1）：多 worker + 热重载会让 reload 时经常卡死
#      （reloader 与多进程相互抢文件监听/端口）。要压测多进程再用 WORKERS=N 覆盖。
#
# 用法：
#   ./dev.sh                      # 127.0.0.1:8020，热重载，单 worker
#   PORT=8030 ./dev.sh            # 改端口
#   HOST=0.0.0.0 ./dev.sh         # 对局域网开放
#   WORKERS=4 ./dev.sh            # 改 worker 数（默认 1）
#   ./dev.sh --no-reload --workers 4   # 透传给 fba run 的额外参数（显式 --workers 覆盖默认）
#
# Windows：用 Git Bash 直接跑本脚本；若用 PowerShell，请改用同目录的 dev.ps1。
set -euo pipefail

# 始终以脚本所在目录（仓库根）为工作目录，无论从哪里调用
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8020}"
VENV_DIR="${VENV_DIR:-.venv}"
# 默认单 worker：多 worker + 热重载会让 reload 经常卡死。需要时用 WORKERS=N 覆盖，
# 或在额外参数里显式传 --workers（此时尊重你给的值，不再注入默认，避免重复参数）。
WORKERS="${WORKERS:-1}"

# 1. source venv —— Unix 是 bin/activate，Windows(Git Bash) 是 Scripts/activate
if [[ -f "$VENV_DIR/bin/activate" ]]; then
  VENV_ACTIVATE="$VENV_DIR/bin/activate"
elif [[ -f "$VENV_DIR/Scripts/activate" ]]; then
  VENV_ACTIVATE="$VENV_DIR/Scripts/activate"
else
  echo "✗ 找不到虚拟环境 $VENV_DIR（缺 bin/activate 或 Scripts/activate；先创建 venv 并装依赖）" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
echo "✓ 已 source venv：$ROOT_DIR/$VENV_DIR"

if ! command -v fba >/dev/null 2>&1; then
  echo "✗ venv 内找不到 fba 命令（依赖未装全？）" >&2
  exit 1
fi

# 2. 杀掉占用端口的旧进程（先 TERM 给优雅退出机会，仍在则 KILL；跨平台）
# 返回监听 $1 端口的 PID 列表（换行分隔，空表示无）
list_listening_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v netstat >/dev/null 2>&1; then
    # Windows(Git Bash) 兜底：netstat -ano 末列为 PID，状态列含 LISTENING
    netstat -ano 2>/dev/null \
      | grep -iE 'LISTEN' \
      | grep -E ":$port[[:space:]]" \
      | awk '{print $NF}' \
      | sort -u || true
  fi
}

# 优雅终止单个 PID
term_pid() {
  if command -v taskkill >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 taskkill /PID "$1" >/dev/null 2>&1 || true
  else
    kill "$1" 2>/dev/null || true
  fi
}

# 强制终止单个 PID
kill_pid() {
  if command -v taskkill >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 taskkill /F /PID "$1" >/dev/null 2>&1 || true
  else
    kill -9 "$1" 2>/dev/null || true
  fi
}

PIDS="$(list_listening_pids "$PORT")"
if [[ -n "$PIDS" ]]; then
  echo "→ 端口 $PORT 被旧进程占用（PID: ${PIDS//$'\n'/ }），正在终止…"
  # shellcheck disable=SC2086
  for _p in $PIDS; do term_pid "$_p"; done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.3
    PIDS="$(list_listening_pids "$PORT")"
    [[ -z "$PIDS" ]] && break
  done
  if [[ -n "$PIDS" ]]; then
    echo "→ 旧进程未优雅退出，强制 KILL（PID: ${PIDS//$'\n'/ }）"
    # shellcheck disable=SC2086
    for _p in $PIDS; do kill_pid "$_p"; done
    sleep 0.5
  fi
  echo "✓ 端口 $PORT 已释放"
else
  echo "✓ 端口 $PORT 空闲，无需清理"
fi

# 3. 启动（fba run 默认开热重载；默认单 worker；额外参数原样透传）
#    若调用方已在额外参数里显式传 --workers，则尊重其值、不再注入默认（避免重复参数报错）。
case " $* " in
  *" --workers "*) WORKERS_ARG="" ;;                 # 调用方已显式指定 worker 数
  *)               WORKERS_ARG="--workers $WORKERS" ;;
esac
echo "→ 启动后端：fba run --host $HOST --port $PORT $WORKERS_ARG $*"
# shellcheck disable=SC2086  # 故意让 $WORKERS_ARG 词分割成 `--workers N` 两个参数
exec fba run --host "$HOST" --port "$PORT" $WORKERS_ARG "$@"
