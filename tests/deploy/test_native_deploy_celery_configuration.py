from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DEPLOY_FILE = PROJECT_ROOT / 'deploy-native.sh'
WORKER_SUPERVISOR_FILE = PROJECT_ROOT / 'deploy' / 'backend' / 'supervisor' / 'fba_celery_worker.conf'
FLOWER_SUPERVISOR_FILE = PROJECT_ROOT / 'deploy' / 'backend' / 'supervisor' / 'fba_celery_flower.conf'


def test_native_worker_uses_application_queue_and_unique_hostname() -> None:
    content = NATIVE_DEPLOY_FILE.read_text(encoding='utf-8')
    worker_line = next(line for line in content.splitlines() if 'celery_app worker' in line)
    worker_section = content.split('# Celery Worker 配置', maxsplit=1)[1].split('# Celery Beat 配置', maxsplit=1)[0]

    assert '-Q celery' not in worker_line
    assert '--queues=celery' not in worker_line
    assert '--hostname=huanxing@%%h' in worker_line
    assert 'stopasgroup=true' in worker_section
    assert 'killasgroup=true' in worker_section


def test_supervisor_worker_uses_application_queue_and_unique_hostname() -> None:
    content = WORKER_SUPERVISOR_FILE.read_text(encoding='utf-8')
    worker_line = next(line for line in content.splitlines() if line.startswith('command='))

    assert '-Q celery' not in worker_line
    assert '--queues=celery' not in worker_line
    assert '-A app.task.celery:celery_app' in worker_line
    assert '--hostname=fba@%%h' in worker_line
    assert 'stopasgroup=true' in content
    assert 'killasgroup=true' in content


def test_flower_supervisor_commands_use_secure_loopback_runner() -> None:
    native_content = NATIVE_DEPLOY_FILE.read_text(encoding='utf-8')
    supervisor_content = FLOWER_SUPERVISOR_FILE.read_text(encoding='utf-8')

    assert 'python -m backend.app.task.flower --port=$FLOWER_PORT' in native_content
    assert 'python -m app.task.flower --address=0.0.0.0 --port=8555 --url-prefix=flower' in supervisor_content
    for content in (native_content, supervisor_content):
        assert '--basic-auth' not in content
        assert 'admin:123456' not in content


def test_native_deploy_never_restarts_all_supervisor_programs() -> None:
    content = NATIVE_DEPLOY_FILE.read_text(encoding='utf-8')

    assert 'supervisorctl restart all' not in content


def test_native_deploy_stops_beat_before_worker_and_accepts_flower_auth_challenge() -> None:
    content = NATIVE_DEPLOY_FILE.read_text(encoding='utf-8')

    beat_stop = content.index('supervisorctl stop ${SERVICE_NAME}-beat')
    worker_stop = content.index('supervisorctl stop ${SERVICE_NAME}-worker')
    api_stop = content.index('supervisorctl stop ${SERVICE_NAME}-api')
    assert beat_stop < worker_stop < api_stop
    assert 'validate_flower_basic_auth' in content
    assert '"$flower_http_code" = "401"' in content
