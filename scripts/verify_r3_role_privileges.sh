#!/usr/bin/env bash
# =============================================================================
# R3 三角色权限矩阵实证脚本（只读 + 事务内回滚，不留痕）
# =============================================================================
# 背景：开发机 `.env` 的三个角色 DSN 通常为空 → `_resolve_role_engine` 回落主 engine →
# 所有会话都是超级用户，跨域访问永远「能跑」。**只有真实部署环境会按 R3 最小权限拒绝**，
# 所以「通用会话直接读写 sync/IM 域表」这类缺陷在本地和测试里一路绿灯直达线上
# （2026-08-23 一次性暴露三处，见 backend/tests/test_r3_cross_domain_session_contract.py）。
#
# 本脚本在**目标环境本机**跑，用 `.env` 里三个角色的真实 DSN 连库，逐条验证权限矩阵是否
# 仍与设计一致。所有写操作都包在 BEGIN...ROLLBACK 里，**不产生任何持久变更**。
#
# 用法（在部署机上，后端目录下）：
#   bash scripts/verify_r3_role_privileges.sh
#
# 退出码：0=矩阵符合预期；非 0=有偏离（会打印哪一条）。
# =============================================================================
set -uo pipefail

BACKEND_DIR="${BACKEND_DIR:-$(pwd)}"
ENVFILE="$BACKEND_DIR/backend/.env"
[ -f "$ENVFILE" ] || { echo "✗ 不存在: $ENVFILE" >&2; exit 1; }

read_env() { grep -E "^$1=" "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }
DB_HOST="$(read_env DATABASE_HOST)"; DB_PORT="$(read_env DATABASE_PORT)"; DB_NAME="$(read_env DATABASE_SCHEMA)"

# 从角色 DSN 里取用户名与密码（形如 postgresql+asyncpg://user:pw@host:port/db）
dsn_user() { read_env "$1" | sed -E 's#.*://([^:]+):.*#\1#'; }
dsn_pass() { read_env "$1" | sed -E 's#.*://[^:]+:([^@]*)@.*#\1#'; }

fail=0
# 期望：$1=角色 DSN 键  $2=SQL  $3=expect(ok|denied)  $4=说明
expect() {
  local key="$1" sql="$2" want="$3" desc="$4"
  local user pass out
  user="$(dsn_user "$key")"; pass="$(dsn_pass "$key")"
  if [ -z "$user" ]; then echo "  ⚠ 跳过（$key 未配置）: $desc"; return; fi
  out="$(PGPASSWORD="$pass" psql -h "$DB_HOST" -p "$DB_PORT" -U "$user" -d "$DB_NAME" -X -tAc \
        "BEGIN; $sql ROLLBACK;" 2>&1)"
  if printf '%s' "$out" | grep -q 'permission denied'; then got=denied; else got=ok; fi
  if [ "$got" = "$want" ]; then
    echo "  ✓ $desc（$user → $got）"
  else
    echo "  ✗ $desc（$user → 期望 $want，实际 $got）"; printf '      %s\n' "$(printf '%s' "$out" | head -2)"
    fail=1
  fi
}

echo "R3 权限矩阵实证 @ $DB_HOST:$DB_PORT/$DB_NAME"
echo "--------------------------------------------------------------"
echo "[python_backend] 应无 sync/IM 表权限，但可执行 append_event"
expect PYTHON_BACKEND_DATABASE_URL \
  "SELECT 1 FROM hasn_sync.hasn_sync_inbox_events LIMIT 1;" denied "直读 sync inbox 表被拒"
expect PYTHON_BACKEND_DATABASE_URL \
  "SELECT 1 FROM hasn_sync.hasn_sync_events LIMIT 1;" denied "直读 sync events 表被拒"
expect PYTHON_BACKEND_DATABASE_URL \
  "SELECT 1 FROM hasn_sync.append_event('probe','probe','test','test','1','{}'::jsonb,'probe_chk','probe_src',NULL);" \
  ok "经 append_event 跨域追加放行"

echo "[sync_service] 应可维护 inbox，但不得绕过 append_event"
expect SYNC_SERVICE_DATABASE_URL \
  "SELECT 1 FROM hasn_sync.hasn_sync_inbox_events LIMIT 1;" ok "可读 sync inbox 表"
expect SYNC_SERVICE_DATABASE_URL \
  "SELECT 1 FROM hasn_sync.append_event('probe','probe','test','test','1','{}'::jsonb,'probe_chk','probe_src2',NULL);" \
  denied "不得执行 append_event"

echo "--------------------------------------------------------------"
if [ "$fail" = 0 ]; then echo "✓ 权限矩阵与 R3 设计一致"; else echo "✗ 权限矩阵有偏离（见上）"; fi
exit "$fail"
