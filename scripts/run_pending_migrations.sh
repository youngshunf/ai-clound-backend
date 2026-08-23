#!/bin/bash
# =============================================================================
# 唤星云端后端 — 幂等数据库迁移 runner
# =============================================================================
# 背景：hasn-cloud-backend 自 2026-05-23 起放弃 alembic，改用
#   backend/sql/<模块>/migrations/YYYY-MM-DD-*.sql 手写迁移文件（全部幂等：
#   CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / DROP ... IF EXISTS /
#   INSERT ... ON CONFLICT）。历史上靠人工 psql -f 一个个执行，无追踪表。
#
# 本脚本补上「迁移追踪表 public.schema_migrations」，使迁移可重复、可审计、
# 只跑未应用的文件。设计为在【生产服务器本机】运行（psql 连 localhost:5432）。
#
# 用法（在服务器上，后端代码已 rsync 到位后执行）：
#   cd /www/wwwroot/api.huanxing.dcfuture.cn
#   bash docs.../run_pending_migrations.sh --dry-run              # 预览将执行哪些
#   bash docs.../run_pending_migrations.sh                        # 真正执行
#   bash docs.../run_pending_migrations.sh --baseline-before=2026-06-18
#                                          # 首次引入追踪表时，把该日期之前的迁移
#                                          # 直接标记为「已应用」（不执行），只跑之后的
#
# 关键环境变量（默认从 $BACKEND_DIR/backend/.env 读取，无需手填密码）：
#   BACKEND_DIR   后端根目录（含 backend/.env 与 backend/sql/）。默认：脚本推断 / 当前目录
#   SQL_ROOT      迁移 SQL 根目录。默认：$BACKEND_DIR/backend/sql
#   DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME  覆盖 .env（一般不用）
# =============================================================================
set -euo pipefail

# ---------- 生产暂缓策略 ----------
# 仅收录已在 SQL 文件中明确声明“不能由常规生产 runner 自动执行”的迁移。
# 暂缓项既不执行也不写入 schema_migrations，待前置条件满足后必须显式移除策略并重新评审。
migration_defer_reason() {
  local key="$1"
  case "$key" in
    hasn/migrations/2026-07-16-r2-11-permission-negative-test.sql)
      echo "仅用于已完成 R2 正向切换的快照副本权限负测"
      ;;
    hasn/migrations/2026-07-16-r2-11-schema-cutover.reverse.sql)
      echo "仅用于 R2 快照演练反向回滚，禁止作为正向生产迁移"
      ;;
    hasn/migrations/2026-07-16-r2-11-schema-cutover.sql)
      echo "SQL 注释限定为本地快照演练，生产须等待 R3 维护窗口"
      ;;
    billing/migrations/2026-07-25-credit-authority-drop-legacy-credit-objects.sql)
      echo "删除旧积分对象前须完成 rebase、三端改指并观察满 30 天"
      ;;
    billing/migrations/2026-07-25-credit-authority-drop-user-subscription-credit-columns.sql)
      echo "删除订阅旧字段前须完成三端契约改造并通过观察期"
      ;;
    *)
      return 0
      ;;
  esac
}

if [ "${MIGRATION_POLICY_TEST_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# ---------- 参数解析 ----------
DRY_RUN=0
BASELINE_BEFORE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --baseline-before=*) BASELINE_BEFORE="${arg#*=}" ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

# ---------- 定位后端目录与 .env ----------
BACKEND_DIR="${BACKEND_DIR:-}"
if [ -z "$BACKEND_DIR" ]; then
  if [ -f "./backend/.env" ]; then BACKEND_DIR="$(pwd)"
  elif [ -f "/www/wwwroot/api.huanxing.dcfuture.cn/backend/.env" ]; then BACKEND_DIR="/www/wwwroot/api.huanxing.dcfuture.cn"
  else echo "✗ 找不到 backend/.env，请用 BACKEND_DIR=... 指定后端根目录" >&2; exit 1; fi
fi
ENVFILE="$BACKEND_DIR/backend/.env"
SQL_ROOT="${SQL_ROOT:-$BACKEND_DIR/backend/sql}"
[ -f "$ENVFILE" ] || { echo "✗ 不存在: $ENVFILE" >&2; exit 1; }
[ -d "$SQL_ROOT" ] || { echo "✗ 不存在 SQL 根目录: $SQL_ROOT" >&2; exit 1; }

# ---------- 读取数据库连接（不回显密码） ----------
read_env() { grep -E "^$1=" "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }
DB_HOST="${DB_HOST:-$(read_env DATABASE_HOST)}"
DB_PORT="${DB_PORT:-$(read_env DATABASE_PORT)}"
DB_USER="${DB_USER:-$(read_env DATABASE_USER)}"
DB_NAME="${DB_NAME:-$(read_env DATABASE_SCHEMA)}"   # fba 里 DATABASE_SCHEMA 即库名
export PGPASSWORD="${DB_PASSWORD:-$(read_env DATABASE_PASSWORD)}"
: "${DB_HOST:?}" "${DB_PORT:?}" "${DB_USER:?}" "${DB_NAME:?}"

PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1)

echo "================================================================"
echo " 唤星数据库迁移 runner"
echo "   目标库 : $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
echo "   SQL 根 : $SQL_ROOT"
echo "   模式   : $([ $DRY_RUN = 1 ] && echo 预览[dry-run] || echo 执行)"
[ -n "$BASELINE_BEFORE" ] && echo "   基线   : 把 < $BASELINE_BEFORE 的迁移标记为已应用(不执行)"
echo "================================================================"

# ---------- 确保追踪表存在 ----------
"${PSQL[@]}" -q -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (
  filename   text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);" >/dev/null
"${PSQL[@]}" -q -c "COMMENT ON TABLE public.schema_migrations IS '手写 SQL 迁移追踪表（run_pending_migrations.sh 维护）';" >/dev/null 2>&1 || true

# ---------- 枚举迁移文件，按文件名中的日期排序 ----------
# key = 相对 SQL_ROOT 的路径（含模块前缀，避免跨模块同名冲突）
# 排序：按 basename（YYYY-MM-DD 前缀）稳定排序，保证依赖顺序（建表 create 早于 seed）
mapfile -t ALL_FILES < <(
  find "$SQL_ROOT" -path "*/migrations/*.sql" -type f | while read -r f; do
    bn="$(basename "$f")"; printf '%s\t%s\n' "$bn" "$f"
  done | sort | cut -f2-
)

applied=0; skipped=0; baselined=0; deferred=0; total=${#ALL_FILES[@]}
echo "发现迁移文件: $total"
echo "----------------------------------------------------------------"

for f in "${ALL_FILES[@]}"; do
  key="${f#$SQL_ROOT/}"
  bn="$(basename "$f")"
  fdate="${bn:0:10}"   # YYYY-MM-DD 前缀

  # 已记录 → 跳过
  exists=$("${PSQL[@]}" -tAq -c "SELECT 1 FROM public.schema_migrations WHERE filename='$key'")
  if [ "$exists" = "1" ]; then skipped=$((skipped+1)); continue; fi

  # 生产暂缓项既不执行也不登记，避免以后误以为已经应用。
  defer_reason="$(migration_defer_reason "$key")"
  if [ -n "$defer_reason" ]; then
    echo "  [暂缓] $key（$defer_reason）"
    deferred=$((deferred+1))
    continue
  fi

  # 基线：日期早于 baseline-before → 只记录不执行
  if [ -n "$BASELINE_BEFORE" ] && [[ "$fdate" < "$BASELINE_BEFORE" ]]; then
    if [ $DRY_RUN = 1 ]; then echo "  [基线] $key"; else
      "${PSQL[@]}" -q -c "INSERT INTO public.schema_migrations(filename) VALUES('$key') ON CONFLICT DO NOTHING" >/dev/null
    fi
    baselined=$((baselined+1)); continue
  fi

  # 待执行
  if [ $DRY_RUN = 1 ]; then
    echo "  [待执行] $key"; applied=$((applied+1)); continue
  fi

  echo "  ▶ 执行: $key"
  # 含 CONCURRENTLY 的迁移不能在单事务里跑（极少；目前仅 2026-05-15 两个）
  if grep -qi "CONCURRENTLY" "$f"; then
    "${PSQL[@]}" -f "$f"
  else
    "${PSQL[@]}" --single-transaction -f "$f"
  fi
  "${PSQL[@]}" -q -c "INSERT INTO public.schema_migrations(filename) VALUES('$key') ON CONFLICT DO NOTHING" >/dev/null
  echo "    ✓ 完成并登记"
  applied=$((applied+1))
done

echo "----------------------------------------------------------------"
if [ $DRY_RUN = 1 ]; then
  echo "预览：待执行 $applied，将基线标记 $baselined，生产暂缓 $deferred，已应用跳过 $skipped（共 $total）"
else
  echo "✓ 完成：本次执行 $applied，基线标记 $baselined，生产暂缓 $deferred，已应用跳过 $skipped（共 $total）"
fi
