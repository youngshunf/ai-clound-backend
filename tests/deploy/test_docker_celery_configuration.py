from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / 'docker-compose.yml'
WORKER_SUPERVISOR_FILE = PROJECT_ROOT / 'deploy' / 'backend' / 'supervisor' / 'fba_celery_worker.conf'
FLOWER_SUPERVISOR_FILE = PROJECT_ROOT / 'deploy' / 'backend' / 'supervisor' / 'fba_celery_flower.conf'


def test_docker_supervisor_uses_importable_app_package() -> None:
    worker = WORKER_SUPERVISOR_FILE.read_text(encoding='utf-8')
    flower = FLOWER_SUPERVISOR_FILE.read_text(encoding='utf-8')

    assert 'directory=/fba/backend' in worker
    assert '-A app.task.celery:celery_app worker' in worker
    assert 'directory=/fba/backend' in flower
    assert 'python -m app.task.flower' in flower


def test_docker_compose_requires_secrets_and_binds_rabbit_management_to_loopback() -> None:
    content = COMPOSE_FILE.read_text(encoding='utf-8')

    assert 'rabbitmq:4.3.4-management@sha256:' in content
    assert 'guest:guest' not in content
    assert 'RABBITMQ_DEFAULT_PASS=guest' not in content
    assert '127.0.0.1:${DOCKER_MAP_RABBITMA_UI_PORT:-15672}:15672' in content
    assert '127.0.0.1:${DOCKER_MAP_RABBITMA_PORT:-5672}:5672' in content
    assert '${DOCKER_RABBITMQ_CELERY_PASSWORD:?' in content
    assert '${DOCKER_FLOWER_BASIC_AUTH:?' in content
    assert 'CELERY_BROKER=rabbitmq' not in content
    assert content.count('CELERY_BROKER_MODE=rabbitmq') == 4


def test_docker_services_keep_supervisor_in_foreground_without_blank_restart() -> None:
    content = COMPOSE_FILE.read_text(encoding='utf-8')

    assert 'supervisorctl restart' not in content
    assert content.count('exec supervisord -n -c /etc/supervisor/supervisord.conf') == 4
