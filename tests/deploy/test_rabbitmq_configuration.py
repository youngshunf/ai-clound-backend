from __future__ import annotations

import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RABBITMQ_DIR = PROJECT_ROOT / 'deploy' / 'rabbitmq'
CONFIG_FILE = RABBITMQ_DIR / 'rabbitmq.conf'
DEFINITIONS_FILE = RABBITMQ_DIR / 'definitions.json'
COMPOSE_FILE = RABBITMQ_DIR / 'docker-compose.yml'
PLUGINS_FILE = RABBITMQ_DIR / 'enabled_plugins'
BOOTSTRAP_FILE = RABBITMQ_DIR / 'bootstrap.sh'
README_FILE = RABBITMQ_DIR / 'README.md'


def test_rabbitmq_deployment_files_exist() -> None:
    expected = {
        CONFIG_FILE,
        DEFINITIONS_FILE,
        COMPOSE_FILE,
        PLUGINS_FILE,
        BOOTSTRAP_FILE,
        README_FILE,
    }

    assert all(path.is_file() for path in expected), 'RabbitMQ 部署文件不完整'


def test_rabbitmq_only_listens_on_loopback_with_resource_guards() -> None:
    config = CONFIG_FILE.read_text(encoding='utf-8')

    assert 'listeners.tcp.default = 127.0.0.1:5672' in config
    assert 'management.tcp.ip = 127.0.0.1' in config
    assert 'management.tcp.port = 15672' in config
    assert 'prometheus.tcp.ip = 127.0.0.1' in config
    assert 'prometheus.tcp.port = 15692' in config
    assert 'distribution.listener.interface = 127.0.0.1' in config
    assert 'distribution.listener.port_range.min = 25672' in config
    assert 'distribution.listener.port_range.max = 25672' in config
    assert 'vm_memory_high_watermark.absolute = 1Gi' in config
    assert 'disk_free_limit.absolute = 5GB' in config
    assert 'max_message_size = 1048576' in config
    assert 'channel_max = 128' in config
    assert 'consumer_timeout = 10800000' in config


def test_rabbitmq_definitions_have_expected_topology_without_secrets() -> None:
    raw = DEFINITIONS_FILE.read_text(encoding='utf-8')
    definitions = json.loads(raw)

    assert definitions.get('users', []) == []
    assert definitions.get('permissions', []) == []
    assert definitions.get('topic_permissions', []) == []
    assert 'password' not in raw.lower()
    assert 'guest' not in raw.lower()
    assert 'amqp://' not in raw.lower()

    assert {item['name'] for item in definitions['vhosts']} == {'huanxing'}
    exchanges = {
        item['name']: item
        for item in definitions['exchanges']
        if item['vhost'] == 'huanxing'
    }
    assert exchanges['huanxing.celery']['type'] == 'direct'
    assert exchanges['huanxing.socketio']['type'] == 'fanout'
    assert exchanges['huanxing.realtime']['type'] == 'fanout'
    assert all(item['durable'] for item in exchanges.values())

    queues = {
        item['name']: item
        for item in definitions['queues']
        if item['vhost'] == 'huanxing'
    }
    celery_queue = queues['huanxing.celery.default']
    assert celery_queue['durable'] is True
    assert celery_queue['auto_delete'] is False
    assert celery_queue['arguments']['x-queue-type'] == 'classic'
    assert {
        (
            item['source'],
            item['destination'],
            item['routing_key'],
        )
        for item in definitions['bindings']
    } >= {
        (
            'huanxing.celery',
            'huanxing.celery.default',
            'huanxing.celery.default',
        )
    }

    policies = {item['name']: item for item in definitions['policies']}
    transient = policies['huanxing-realtime-transient-queues']
    assert transient['apply-to'] == 'queues'
    assert transient['definition']['expires'] == 300000
    assert transient['pattern'] == r'^(python-socketio|huanxing\.realtime)\.'


def test_rabbitmq_compose_is_digest_locked_and_uses_data_disk() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    service = compose['services']['rabbitmq']

    assert service['image'] == (
        'rabbitmq:4.3.4-management@'
        'sha256:656e8ab6b06fb4f84a8a3d90fe80c4f151c4e731bf3e87a50477774b3d7c08b3'
    )
    assert service['network_mode'] == 'host'
    assert service.get('ports', []) == []
    assert service['restart'] == 'unless-stopped'
    assert service['mem_limit'] == '2g'
    assert service['cpus'] == 1.5
    assert service['pids_limit'] == 512
    assert service['read_only'] is True
    assert service['security_opt'] == ['no-new-privileges:true']
    assert service['environment']['ERL_EPMD_ADDRESS'] == '127.0.0.1'

    volumes = set(service['volumes'])
    data_root = '${RABBITMQ_DATA_DIR:-/data2/huanxing-rabbitmq}'
    assert f'{data_root}/data:/var/lib/rabbitmq' in volumes
    assert f'{data_root}/logs:/var/log/rabbitmq' in volumes
    assert './rabbitmq.conf:/etc/rabbitmq/rabbitmq.conf:ro' in volumes
    assert './definitions.json:/etc/rabbitmq/definitions.json:ro' in volumes
    assert './enabled_plugins:/etc/rabbitmq/enabled_plugins:ro' in volumes


def test_rabbitmq_management_and_prometheus_plugins_are_pre_enabled() -> None:
    plugins = PLUGINS_FILE.read_text(encoding='utf-8')

    assert plugins.strip() == '[rabbitmq_management,rabbitmq_prometheus].'


def test_rabbitmq_bootstrap_uses_role_secrets_and_separated_permissions() -> None:
    script = BOOTSTRAP_FILE.read_text(encoding='utf-8')

    for name in (
        'RABBITMQ_CELERY_PASSWORD',
        'RABBITMQ_REALTIME_PASSWORD',
        'RABBITMQ_MONITOR_PASSWORD',
    ):
        assert name in script
    for username in (
        'huanxing_celery',
        'huanxing_realtime',
        'huanxing_monitor',
    ):
        assert username in script

    assert 'import_definitions' in script
    assert 'set_vhost_limits' in script
    assert 'delete_user guest' in script
    assert 'set_permissions' in script
    assert 'set_user_tags huanxing_monitor monitoring' in script
    assert 'rabbitmq-diagnostics "$@"' in script
    assert 'rabbitmq_diagnostics check_running' in script
    assert 'rabbitmq_diagnostics check_local_alarms' in script
    assert 'rabbitmqctl check_running' not in script
    assert 'rabbitmqctl check_local_alarms' not in script
    assert 'guest:guest' not in script
    assert 'amqp://' not in script
    assert 'source ' not in script
