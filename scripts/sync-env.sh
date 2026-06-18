#!/usr/bin/env bash
# ============================================================
# 同步本地 backend/.env.prod  →  线上 .env（生产唯一事实源 = 本地 .env.prod）
#
# 约定：改生产配置 **只改本地 backend/.env.prod**，然后跑本脚本同步到线上。
#       线上 .env 不应手改；任何线上独有的 key 都必须先补回本地 .env.prod。
#
# 用法（在 huanxing-cloud-backend 仓库内任意目录执行）：
#   scripts/sync-env.sh              # 比对→确认→备份线上→上传→重启→验活
#   scripts/sync-env.sh --dry-run    # 只比对 key 差异，绝不改线上
#   scripts/sync-env.sh --yes        # 跳过交互确认（CI/熟手）
#   scripts/sync-env.sh --no-restart # 同步但不重启（改完多项后手动统一重启）
#   HX_SSH=huanxing-server2 scripts/sync-env.sh   # 用 ssh 别名替代 root@IP
#
# 安全护栏：若线上 .env 存在 .env.prod 没有的 key（直接同步会删掉它们而搞挂
#           生产，如 NEWAPI_ADMIN_* 这类），脚本**直接中止**，要求先补回本地。
# ============================================================
set -euo pipefail

SSH_TARGET="${HX_SSH:-root@117.72.92.229}"
REMOTE_DIR="/www/wwwroot/api.huanxing.dcfuture.cn/backend"
REMOTE_ENV="$REMOTE_DIR/.env"
SUPERVISOR_PROGRAMS="api.huanxing.dcfuture.cn huanxing-backend-worker huanxing-backend-beat huanxing-backend-flower"
HEALTH_URL="http://localhost:8020/api/v1/auth/captcha"
SSH_OPTS=(-o ConnectTimeout=25 -o ServerAliveInterval=8 -o ServerAliveCountMax=10)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info(){ echo -e "${GREEN}[✓]${NC} $1"; }
warn(){ echo -e "${YELLOW}[!]${NC} $1"; }
err(){  echo -e "${RED}[✗]${NC} $1"; }
step(){ echo -e "${BLUE}[➤]${NC} $1"; }

DRY_RUN=0; ASSUME_YES=0; DO_RESTART=1
for a in "$@"; do case "$a" in
  --dry-run) DRY_RUN=1;;
  --yes|-y)  ASSUME_YES=1;;
  --no-restart) DO_RESTART=0;;
  -h|--help) sed -n '2,22p' "$0"; exit 0;;
  *) err "未知参数: $a"; exit 1;;
esac; done

# 定位本地 .env.prod（脚本在 <repo>/scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_ENV="$REPO_ROOT/backend/.env.prod"
[ -f "$LOCAL_ENV" ] || { err "本地 .env.prod 不存在: $LOCAL_ENV"; exit 1; }

ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'echo ok' >/dev/null 2>&1 || { err "无法连接 $SSH_TARGET"; exit 1; }
info "服务器连接正常: $SSH_TARGET"

# ── 1. key 级比对（只打印 key 名，绝不打印值；线上独有 key → 退出码 2） ──
step "比对本地 .env.prod 与线上 .env（仅 key 名）..."
TMP_REMOTE="/tmp/.env.prod.sync.$$"
scp "${SSH_OPTS[@]}" -q "$LOCAL_ENV" "$SSH_TARGET:$TMP_REMOTE"
set +e
DIFF_OUT="$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "python3 - '$TMP_REMOTE' '$REMOTE_ENV'; rc=\$?; rm -f '$TMP_REMOTE'; exit \$rc" <<'PY'
import sys
def load(p):
    d={}
    for line in open(p, encoding='utf-8'):
        line=line.rstrip('\n').rstrip('\r'); s=line.strip()
        if not s or s.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); k=k.strip()
        if k and (k[0].isalpha() or k[0]=='_'): d[k]=v
    return d
prod=load(sys.argv[1]); live=load(sys.argv[2])
kp,kl=set(prod),set(live)
only_live=sorted(kl-kp); only_prod=sorted(kp-kl)
vdiff=[k for k in sorted(kp&kl) if prod[k]!=live[k]]
print(f"    本地 .env.prod={len(kp)} keys  |  线上 .env={len(kl)} keys")
print(f"    ❗仅线上有(同步会删除): {only_live or '无'}")
print(f"    ➕仅本地有(同步会新增): {only_prod or '无'}")
print(f"    ✏️值不同(同步会覆盖线上): {vdiff or '无'}")
if only_live: sys.exit(2)
sys.exit(0 if (not only_prod and not vdiff) else 3)  # 3 = 有可同步差异
PY
)"
DIFF_RC=$?
set -e
echo "$DIFF_OUT"

if [ "$DIFF_RC" -eq 2 ]; then
  err "线上存在 .env.prod 没有的 key（见上 ❗）——直接同步会删除它们，可能搞挂生产，已中止。"
  err "请先把这些 key（含其值）补进本地 backend/.env.prod，再重跑本脚本。"
  exit 1
fi
if [ "$DIFF_RC" -eq 0 ]; then
  info "本地 .env.prod 与线上 .env 已完全一致，无需同步。"
  exit 0
fi
if [ "$DIFF_RC" -ne 3 ]; then err "比对异常（rc=$DIFF_RC）"; exit 1; fi

if [ "$DRY_RUN" -eq 1 ]; then warn "--dry-run：仅比对，不改线上。"; exit 0; fi

# ── 2. 确认 ──
if [ "$ASSUME_YES" -ne 1 ]; then
  echo ""; read -r -p "确认用本地 .env.prod 覆盖线上 .env 并重启服务？[y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) warn "已取消。"; exit 0;; esac
fi

# ── 3. 备份线上 .env（时间戳，便于回滚） ──
step "备份线上 .env..."
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cp '$REMOTE_ENV' '$REMOTE_ENV.bak-\$(date +%Y%m%d-%H%M%S)' && ls -t '$REMOTE_DIR'/.env.bak-* | head -1"
info "已备份（回滚：cp 该 .bak 文件覆盖回 .env 再重启）"

# ── 4. 上传 + 权限 ──
step "上传 .env.prod → 线上 .env..."
scp "${SSH_OPTS[@]}" -q "$LOCAL_ENV" "$SSH_TARGET:$REMOTE_ENV"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "chown root:root '$REMOTE_ENV' && chmod 600 '$REMOTE_ENV'"
info "上传完成（chmod 600 保护密钥）"

# ── 5. 重启（env 改动需重启才生效） ──
if [ "$DO_RESTART" -eq 1 ]; then
  step "重启服务（env 改动重启才生效）..."
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "supervisorctl restart $SUPERVISOR_PROGRAMS" || warn "部分服务重启返回非零，下面验活确认"
  sleep 4
else
  warn "--no-restart：未重启，新配置尚未生效，请稍后手动 supervisorctl restart $SUPERVISOR_PROGRAMS"
fi

# ── 6. 验活 ──
step "验证后端健康..."
CODE="$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "curl -s -o /dev/null -w '%{http_code}' --max-time 8 '$HEALTH_URL'" 2>/dev/null || echo 000)"
if [ "$CODE" = "200" ]; then info "后端正常（$HEALTH_URL → 200）✅ 同步完成"; else
  warn "健康探针返回 HTTP $CODE（可能还在启动）。可查：ssh $SSH_TARGET 'supervisorctl status; tail -n 40 $REMOTE_DIR/../logs/api.log'"
fi
