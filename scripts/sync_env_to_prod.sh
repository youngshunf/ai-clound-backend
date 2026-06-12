#!/usr/bin/env bash
#
# sync_env_to_prod.sh —— 把本地 backend/.env.prod 一键同步到生产服务器的 backend/.env
#
# 设计要点：
#   - .env.prod 是生产配置的「权威事实源」（已包含 webhook 密钥 / hub 路径 / clawhub 阈值等
#     生产专属键），整体覆盖线上 .env。
#   - 覆盖前自动备份线上 .env（带时间戳），可随时回滚。
#   - 覆盖前展示「将新增 / 删除 / 改值」的键级 diff，确认后才推。
#   - 重启服务用「精确程序名」，绝不 supervisorctl restart all（共享机铁律：会误伤同机其它租户）。
#   - 重启后用 captcha 探针校验 API 200。
#
# 用法：
#   ./scripts/sync_env_to_prod.sh            # 交互式：看 diff → 确认 → 同步
#   ./scripts/sync_env_to_prod.sh --dry-run  # 只看 diff，不推送、不重启
#   ./scripts/sync_env_to_prod.sh --yes      # 跳过确认（CI / 自动化）
#
set -euo pipefail

# ==================== 配置（与 deploy.sh 对齐）====================
SERVER="root@117.72.92.229"
BACKEND_REMOTE="/www/wwwroot/api.huanxing.dcfuture.cn"
SUPERVISOR_SERVICE="api.huanxing.dcfuture.cn"   # ⚠️ 精确程序名，禁止 restart all
REMOTE_ENV="$BACKEND_REMOTE/backend/.env"

# 本地 .env.prod（相对脚本所在目录定位，cwd 无关）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ENV="$SCRIPT_DIR/../backend/.env.prod"

# ==================== 参数 ====================
DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help)
            grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "未知参数: ${arg}（见 --help）" >&2; exit 2 ;;
    esac
done

# ==================== 颜色 ====================
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; BLU=$'\033[0;34m'; NC=$'\033[0m'
info() { echo "${GRN}✔${NC} $*"; }
step() { echo "${BLU}▸${NC} $*"; }
warn() { echo "${YLW}⚠${NC} $*"; }
err()  { echo "${RED}✗${NC} $*" >&2; }

# ==================== 临时文件（含密钥，安全清理）====================
TMPDIR_LOCAL="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_LOCAL"; }
trap cleanup EXIT
REMOTE_SNAPSHOT="$TMPDIR_LOCAL/remote.env"

# 规范化：取 KEY=VALUE 行、去外层引号、按键排序去重 —— 用于键级 diff 比对
norm() { grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | sed "s/=['\"]\(.*\)['\"]\$/=\1/" | sort -u; }

# ==================== 0. 前置检查 ====================
[ -f "$LOCAL_ENV" ] || { err "本地 .env.prod 不存在: $LOCAL_ENV"; exit 1; }
step "检查 SSH 连接 $SERVER ..."
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "$SERVER" "echo ok" >/dev/null 2>&1; then
    err "无法连接 ${SERVER}，请检查 SSH 配置 / 网络"
    exit 1
fi
info "SSH 连接正常"

# ==================== 1. 拉取线上快照并 diff ====================
step "拉取线上 .env 当前内容用于比对 ..."
if ! ssh "$SERVER" "cat $REMOTE_ENV" > "$REMOTE_SNAPSHOT" 2>/dev/null; then
    warn "线上 $REMOTE_ENV 不存在或读取失败（首次部署？将直接写入）"
    : > "$REMOTE_SNAPSHOT"
fi

LOCAL_KEYS="$TMPDIR_LOCAL/local.keys"; REMOTE_KEYS="$TMPDIR_LOCAL/remote.keys"
norm "$LOCAL_ENV"       > "$LOCAL_KEYS"
norm "$REMOTE_SNAPSHOT" > "$REMOTE_KEYS"

echo
echo "================ 同步预览（本地 .env.prod → 线上 .env）================"
keyname() { sed 's/=.*//'; }
ADDED=$(comm -23 <(keyname < "$LOCAL_KEYS" | sort -u) <(keyname < "$REMOTE_KEYS" | sort -u) || true)
REMOVED=$(comm -13 <(keyname < "$LOCAL_KEYS" | sort -u) <(keyname < "$REMOTE_KEYS" | sort -u) || true)
# 改值：键两边都有但值不同（脱敏，只列键名，不打印密钥）
CHANGED=$(comm -12 <(keyname < "$LOCAL_KEYS" | sort -u) <(keyname < "$REMOTE_KEYS" | sort -u) | while read -r k; do
    lv=$(grep -E "^$k=" "$LOCAL_KEYS"  | head -1)
    rv=$(grep -E "^$k=" "$REMOTE_KEYS" | head -1)
    [ "$lv" != "$rv" ] && echo "$k"
done || true)

[ -n "$ADDED" ]   && { echo "${GRN}+ 新增到线上：${NC}"; echo "$ADDED"   | sed 's/^/    + /'; } || echo "${GRN}+ 新增：无${NC}"
[ -n "$REMOVED" ] && { echo "${RED}- 从线上删除：${NC}"; echo "$REMOVED" | sed 's/^/    - /'; } || echo "${RED}- 删除：无${NC}"
[ -n "$CHANGED" ] && { echo "${YLW}~ 改值（仅列键名）：${NC}"; echo "$CHANGED" | sed 's/^/    ~ /'; } || echo "${YLW}~ 改值：无${NC}"
echo "======================================================================"
echo

if [ -z "$ADDED$REMOVED$CHANGED" ]; then
    info "线上 .env 与本地 .env.prod 内容一致，无需同步。"
    [ "$DRY_RUN" = "1" ] && exit 0
    if [ "$ASSUME_YES" != "1" ]; then
        read -r -p "仍要强制覆盖并重启吗？[y/N] " a; [ "$a" = "y" ] || { info "已取消"; exit 0; }
    fi
fi

if [ "$DRY_RUN" = "1" ]; then
    info "dry-run 结束（未推送、未重启）。"
    exit 0
fi

if [ -n "$REMOVED" ]; then
    warn "注意：上述键将从线上 .env 删除。若非预期，请先把它们补进本地 .env.prod 再同步。"
fi
if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "确认推送到 $SERVER 并重启 ${SUPERVISOR_SERVICE}？[y/N] " a
    [ "$a" = "y" ] || { info "已取消"; exit 0; }
fi

# ==================== 2. 备份线上 .env ====================
TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$REMOTE_ENV.backup-$TS"
if [ -s "$REMOTE_SNAPSHOT" ]; then
    step "备份线上 .env → $BACKUP"
    ssh "$SERVER" "cp -a $REMOTE_ENV $BACKUP"
    info "已备份"
fi

# ==================== 3. 推送 ====================
step "scp 本地 .env.prod → 线上 .env"
scp -q "$LOCAL_ENV" "$SERVER:$REMOTE_ENV"
ssh "$SERVER" "chown root:root $REMOTE_ENV && chmod 644 $REMOTE_ENV"
info "已写入并修复权限"

# 校验写入字节一致
LOCAL_SUM=$(shasum "$LOCAL_ENV" | cut -d' ' -f1)
REMOTE_SUM=$(ssh "$SERVER" "shasum $REMOTE_ENV" | cut -d' ' -f1)
if [ "${LOCAL_SUM}" = "${REMOTE_SUM}" ]; then
    info "内容校验一致 sha1=${LOCAL_SUM}"
else
    err "内容校验不一致! local=${LOCAL_SUM} remote=${REMOTE_SUM} -- 请人工核查, 备份在 ${BACKUP}"
    exit 1
fi

# ==================== 4. 重启服务（精确程序名）====================
step "重启后端服务 ${SUPERVISOR_SERVICE}（精确程序名，不 restart all）..."
ssh "$SERVER" "supervisorctl restart $SUPERVISOR_SERVICE" || { err "重启失败"; exit 1; }
sleep 3

# ==================== 5. 验证 ====================
step "验证服务状态 ..."
STATUS=$(ssh "$SERVER" "supervisorctl status $SUPERVISOR_SERVICE" 2>/dev/null || echo UNKNOWN)
echo "    $STATUS"
if ! echo "$STATUS" | grep -q RUNNING; then
    err "服务未 RUNNING！查看日志（备份在 ${BACKUP}，可回滚）："
    ssh "$SERVER" "supervisorctl tail $SUPERVISOR_SERVICE stderr 2>/dev/null | tail -20" || true
    exit 1
fi

# captcha 探针：uvicorn worker 重启后约 5-10s 才就绪，轮询最多 8 次（每 3s）避免误报
step "API captcha 探针（最多 8 次）..."
HTTP="000"
for _ in $(seq 1 8); do
    HTTP=$(ssh "$SERVER" "curl -s -o /dev/null -w '%{http_code}' http://localhost:8020/api/v1/auth/captcha" 2>/dev/null || true)
    HTTP="${HTTP:-000}"
    [ "$HTTP" = "200" ] && break
    sleep 3
done

ROLLBACK="ssh $SERVER \"cp -a $BACKUP $REMOTE_ENV && supervisorctl restart $SUPERVISOR_SERVICE\""
if [ "$HTTP" = "200" ]; then
    info "同步成功 ✅  API 探针 captcha=200，服务正常"
    echo
    echo "回滚命令（如需）：$ROLLBACK"
else
    warn "服务 RUNNING 但 captcha 多次探针仍为 HTTP ${HTTP}，请人工核查日志："
    warn "  ssh $SERVER \"supervisorctl tail $SUPERVISOR_SERVICE stderr | tail -40\""
    warn "回滚：$ROLLBACK"
    exit 1
fi
