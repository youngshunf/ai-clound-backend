#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
secrets_file=${RABBITMQ_SECRETS_FILE:-/data2/huanxing-rabbitmq/secrets/bootstrap.env}
compose=(docker compose --project-directory "$script_dir" -f "$script_dir/docker-compose.yml")

fail() {
  printf 'RabbitMQ 初始化失败：%s\n' "$*" >&2
  exit 1
}

[[ -r "$secrets_file" ]] || fail "密钥文件不可读：$secrets_file"
[[ $(stat -c '%a' "$secrets_file") == '600' ]] || fail '密钥文件权限必须为 600'

while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
  [[ -z "${key:-}" || "$key" == \#* ]] && continue
  case "$key" in
    RABBITMQ_CELERY_PASSWORD|RABBITMQ_REALTIME_PASSWORD|RABBITMQ_MONITOR_PASSWORD)
      printf -v "$key" '%s' "$value"
      ;;
    *)
      fail "密钥文件包含未知字段：$key"
      ;;
  esac
done < "$secrets_file"

for secret_name in \
  RABBITMQ_CELERY_PASSWORD \
  RABBITMQ_REALTIME_PASSWORD \
  RABBITMQ_MONITOR_PASSWORD
do
  secret_value=${!secret_name:-}
  [[ "$secret_value" =~ ^[A-Za-z0-9_-]{32,128}$ ]] \
    || fail "$secret_name 必须是 32–128 位 URL-safe 随机字符串"
done

rabbitmqctl() {
  "${compose[@]}" exec -T rabbitmq rabbitmqctl "$@"
}

rabbitmq_diagnostics() {
  "${compose[@]}" exec -T rabbitmq rabbitmq-diagnostics "$@"
}

user_exists() {
  rabbitmqctl list_users --silent \
    | awk -v expected="$1" '$1 == expected { found = 1 } END { exit !found }'
}

upsert_user() {
  local username=$1
  local password=$2
  if user_exists "$username"; then
    rabbitmqctl change_password "$username" "$password"
  else
    rabbitmqctl add_user "$username" "$password"
  fi
}

rabbitmqctl await_startup
rabbitmqctl import_definitions /etc/rabbitmq/definitions.json

upsert_user huanxing_celery "$RABBITMQ_CELERY_PASSWORD"
upsert_user huanxing_realtime "$RABBITMQ_REALTIME_PASSWORD"
upsert_user huanxing_monitor "$RABBITMQ_MONITOR_PASSWORD"

celery_resources='^(huanxing\.celery(?:\..*)?|celeryev(?:\..*)?|celery(?:\..*)?|.*\.celery\.pidbox|reply\.celery\.pidbox|amq\.default)$'
realtime_resources='^(huanxing\.(socketio|realtime)|python-socketio\..*|huanxing\.realtime\..*)$'

rabbitmqctl set_permissions -p huanxing huanxing_celery \
  "$celery_resources" "$celery_resources" "$celery_resources"
rabbitmqctl set_permissions -p huanxing huanxing_realtime \
  "$realtime_resources" "$realtime_resources" "$realtime_resources"
rabbitmqctl set_permissions -p huanxing huanxing_monitor '^$' '^$' '.*'
rabbitmqctl set_user_tags huanxing_monitor monitoring
rabbitmqctl set_vhost_limits -p huanxing \
  '{"max-connections":256,"max-queues":2048}'

if user_exists guest; then
  rabbitmqctl delete_user guest
fi

rabbitmq_diagnostics check_running
rabbitmq_diagnostics check_local_alarms
rabbitmqctl list_permissions -p huanxing

unset \
  RABBITMQ_CELERY_PASSWORD \
  RABBITMQ_REALTIME_PASSWORD \
  RABBITMQ_MONITOR_PASSWORD

printf 'RabbitMQ vhost、拓扑、角色权限和资源上限初始化完成。\n'
