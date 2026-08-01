#!/usr/bin/env sh
set -eu

config_file=/usr/local/etc/redis/redis.conf
password=$(awk '$1 == "requirepass" { print $2; exit }' "$config_file")

if [ -z "$password" ]; then
  echo "Redis 8 健康检查未读取到认证配置" >&2
  exit 1
fi

export REDISCLI_AUTH="$password"
exec redis-cli \
  -h 127.0.0.1 \
  -p 9397 \
  --no-auth-warning \
  ping
