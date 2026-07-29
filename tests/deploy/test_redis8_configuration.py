from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REDIS8_DIR = PROJECT_ROOT / 'deploy' / 'redis8'
CONFIG_TEMPLATE = REDIS8_DIR / 'redis.conf.template'
COMPOSE_FILE = REDIS8_DIR / 'docker-compose.yml'
HEALTHCHECK_FILE = REDIS8_DIR / 'healthcheck.sh'
BOOTSTRAP_FILE = REDIS8_DIR / 'bootstrap.sh'
VERIFY_FILE = REDIS8_DIR / 'verify_snapshot.py'
README_FILE = REDIS8_DIR / 'README.md'


def test_redis8_deployment_files_exist() -> None:
    expected = {
        CONFIG_TEMPLATE,
        COMPOSE_FILE,
        HEALTHCHECK_FILE,
        BOOTSTRAP_FILE,
        VERIFY_FILE,
        README_FILE,
    }

    assert all(path.is_file() for path in expected), 'Redis 8 部署文件不完整'


def test_redis8_config_is_loopback_persistent_and_fails_closed() -> None:
    config = CONFIG_TEMPLATE.read_text(encoding='utf-8')

    assert 'bind 127.0.0.1' in config
    assert 'port 9397' in config
    assert 'protected-mode yes' in config
    assert 'requirepass {{REDIS8_PASSWORD}}' in config
    assert 'appendonly yes' in config
    assert 'appendfsync everysec' in config
    assert 'save 3600 1' in config
    assert 'save 300 100' in config
    assert 'save 60 10000' in config
    assert 'stop-writes-on-bgsave-error yes' in config
    assert 'rdbchecksum yes' in config
    assert 'maxmemory 512mb' in config
    assert 'maxmemory-policy noeviction' in config
    assert '0.0.0.0' not in config


def test_redis8_compose_is_digest_locked_and_uses_data_disk() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    service = compose['services']['redis8']

    assert service['image'] == ('redis:8.8.0@sha256:0b13f549ab871acafaa84b673c4e29bd7dce8d12526aaafe3b4ea3366c322daf')
    assert service['container_name'] == 'huanxing-redis8'
    assert service['network_mode'] == 'host'
    assert service.get('ports', []) == []
    assert service['user'] == '999:999'
    assert service['restart'] == 'unless-stopped'
    assert service['read_only'] is True
    assert service['security_opt'] == ['no-new-privileges:true']
    assert service['cap_drop'] == ['ALL']
    assert service['mem_limit'] == '1g'
    assert service['cpus'] == pytest.approx(1.0)
    assert service['pids_limit'] == 128
    assert service['command'] == [
        'redis-server',
        '/usr/local/etc/redis/redis.conf',
    ]

    volumes = set(service['volumes'])
    data_root = '${REDIS8_DATA_DIR:-/data2/huanxing-redis8}'
    assert f'{data_root}/data:/data' in volumes
    assert f'{data_root}/secrets/redis.conf:/usr/local/etc/redis/redis.conf:ro' in volumes
    assert './healthcheck.sh:/usr/local/bin/redis8-healthcheck:ro' in volumes
    assert service['healthcheck']['test'] == [
        'CMD',
        '/usr/local/bin/redis8-healthcheck',
    ]


def test_redis8_bootstrap_requires_strong_secret_and_existing_rdb() -> None:
    script = BOOTSTRAP_FILE.read_text(encoding='utf-8')

    assert 'REDIS8_PASSWORD' in script
    assert '权限必须为 600' in script
    assert '32–128 位 URL-safe 随机字符串' in script
    assert 'REDIS8_SOURCE_RDB' in script
    assert 'redis-check-rdb' in script
    assert 'docker compose config --quiet' in script
    assert 'docker compose pull' in script
    assert 'docker compose up -d' in script
    assert 'source ' not in script
    assert 'redis://' not in script
    assert 'requirepass changeme' not in script


def test_redis8_healthcheck_does_not_expose_password_in_process_args() -> None:
    script = HEALTHCHECK_FILE.read_text(encoding='utf-8')

    assert 'REDISCLI_AUTH=' in script
    assert 'redis-cli' in script
    assert '-a ' not in script
    assert '--pass' not in script


def test_redis8_snapshot_verifier_is_read_only_and_covers_all_core_types() -> None:
    script = VERIFY_FILE.read_text(encoding='utf-8')

    for redis_type in ('string', 'list', 'set', 'zset', 'hash', 'stream'):
        assert f"'{redis_type}'" in script
    assert 'SOURCE_REDIS_URL' in script
    assert 'TARGET_REDIS_URL' in script
    assert 'sha256' in script
    assert 'pttl' in script
    assert 'dbsize' in script
    assert 'memory_stats' in script
    assert '.set(' not in script
    assert '.delete(' not in script
    assert '.flush' not in script
    assert '.restore(' not in script
    assert '.migrate(' not in script
