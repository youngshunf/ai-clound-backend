#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
data_root=${REDIS8_DATA_DIR:-/data2/huanxing-redis8}
secrets_file=${REDIS8_SECRETS_FILE:-"$data_root/secrets/bootstrap.env"}
source_rdb=${REDIS8_SOURCE_RDB:-}
image='redis:8.8.0@sha256:0b13f549ab871acafaa84b673c4e29bd7dce8d12526aaafe3b4ea3366c322daf'
container_name=huanxing-redis8
staged_rdb="$data_root/data/.dump.rdb.importing.$$"

fail() {
  printf 'Redis 8 启动失败：%s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail '未安装 Docker'
docker compose version >/dev/null 2>&1 || fail '未安装 Docker Compose 插件'
[ -n "$source_rdb" ] || fail '必须通过 REDIS8_SOURCE_RDB 指定已冻结的 RDB 快照'
[ -f "$source_rdb" ] || fail 'REDIS8_SOURCE_RDB 指向的文件不存在'
[ -f "$secrets_file" ] || fail '密钥文件不存在'

secret_mode=$(stat -c '%a' "$secrets_file")
[ "$secret_mode" = '600' ] || fail '密钥文件权限必须为 600'

redis8_password=
while IFS='=' read -r key value; do
  value=${value%$'\r'}
  if [ "$key" = 'REDIS8_PASSWORD' ]; then
    redis8_password=$value
  fi
done <"$secrets_file"

if [[ ! "$redis8_password" =~ ^[A-Za-z0-9_-]{32,128}$ ]]; then
  fail 'REDIS8_PASSWORD 必须是 32–128 位 URL-safe 随机字符串'
fi

if docker inspect "$container_name" >/dev/null 2>&1; then
  fail '目标容器已存在；为避免覆盖数据，拒绝重复初始化'
fi

if [ -e "$data_root/data/dump.rdb" ] || [ -e "$data_root/data/appendonlydir" ]; then
  fail '目标数据目录已有持久化数据；为避免覆盖，拒绝初始化'
fi

install -d -m 0750 "$data_root"
install -d -m 0750 -o 999 -g 999 "$data_root/data"
install -d -m 0700 "$data_root/secrets"
install -d -m 0750 "$data_root/backups"

rendered_config="$data_root/secrets/redis.conf"
sed "s/{{REDIS8_PASSWORD}}/$redis8_password/g" \
  "$script_dir/redis.conf.template" >"$rendered_config"
chown 999:999 "$rendered_config"
chmod 0400 "$rendered_config"

cleanup() {
  rm -f "$staged_rdb"
}
trap cleanup EXIT

install -m 0640 -o 999 -g 999 "$source_rdb" "$staged_rdb"
docker run --rm \
  --user 999:999 \
  --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --mount "type=bind,src=$staged_rdb,dst=/input/dump.rdb,readonly" \
  "$image" \
  redis-check-rdb /input/dump.rdb

mv "$staged_rdb" "$data_root/data/dump.rdb"
trap - EXIT

export REDIS8_DATA_DIR="$data_root"
cd "$script_dir"
docker compose config --quiet
docker compose pull
docker compose up -d

reported_version=$(docker compose exec -T redis8 redis-server --version)
case "$reported_version" in
  *'v=8.8.0'*) ;;
  *) fail '容器中的 Redis 版本不是 8.8.0' ;;
esac

for _ in $(seq 1 30); do
  health_status=$(docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
    "$container_name")
  if [ "$health_status" = 'healthy' ]; then
    printf 'Redis 8 已启动并通过健康检查，监听 127.0.0.1:9397。\n'
    exit 0
  fi
  if [ "$health_status" = 'unhealthy' ]; then
    docker compose logs --tail 50 redis8 >&2
    fail '容器健康检查失败'
  fi
  sleep 2
done

docker compose logs --tail 50 redis8 >&2
fail '容器未在等待窗口内进入健康状态'
