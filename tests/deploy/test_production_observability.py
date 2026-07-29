from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = (
    PROJECT_ROOT
    / 'deploy'
    / 'backend'
    / 'grafana'
    / 'production'
    / 'docker-compose.yml'
)
PRODUCTION_DIR = COMPOSE_FILE.parent
NGINX_METRICS_SNIPPET = (
    PROJECT_ROOT
    / 'deploy'
    / 'backend'
    / 'nginx'
    / 'observability-metrics-private.conf'
)


def test_production_observability_compose_exists() -> None:
    assert COMPOSE_FILE.is_file(), '缺少生产可观测性 Docker Compose 配置'


def test_production_observability_has_complete_signal_stack() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))

    assert set(compose['services']) == {
        'alloy',
        'grafana',
        'loki',
        'prometheus',
        'tempo',
    }


def test_production_observability_images_are_pinned() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    images = {
        name: service.get('image', '').split('@', maxsplit=1)[0]
        for name, service in compose['services'].items()
    }

    assert images == {
        'alloy': 'grafana/alloy:v1.18.0',
        'grafana': 'grafana/grafana:13.1.1',
        'loki': 'grafana/loki:3.7.4',
        'prometheus': 'prom/prometheus:v3.13.1',
        'tempo': 'grafana/tempo:3.0.2',
    }


def test_production_observability_images_are_digest_locked() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    digests = {
        name: service.get('image', '').partition('@')[2]
        for name, service in compose['services'].items()
    }

    assert digests == {
        'alloy': (
            'sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308'
        ),
        'grafana': (
            'sha256:7cb8c64c4d57a57e734073f3cc94620adb24a0acb929bd80ba9f14017e3a975b'
        ),
        'loki': (
            'sha256:87f0a067673756a3cede1bcbf0c74875f7df9b09fddb53e399d0c576f756cfcc'
        ),
        'prometheus': (
            'sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893'
        ),
        'tempo': (
            'sha256:cda87c212d8c584dc0b89e337e7ed648a5100feb657e5d528480ee4fa03dbbe3'
        ),
    }


def test_production_observability_only_publishes_loopback_ports() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    published_ports = {
        name: service.get('ports', [])
        for name, service in compose['services'].items()
    }

    assert published_ports == {
        'alloy': ['127.0.0.1:4317:4317'],
        'grafana': ['127.0.0.1:13000:3000'],
        'loki': [],
        'prometheus': [],
        'tempo': [],
    }
    assert all(
        service.get('network_mode') != 'host'
        for service in compose['services'].values()
    )


def test_production_observability_containers_use_least_privilege() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    expected_users = {
        'alloy': '473:473',
        'grafana': '472:0',
        'loki': '10001:10001',
        'prometheus': '65534:65534',
        'tempo': '10001:10001',
    }

    for name, service in compose['services'].items():
        assert service.get('user') == expected_users[name], name
        assert service.get('read_only') is True, name
        assert service.get('cap_drop') == ['ALL'], name
        assert service.get('security_opt') == ['no-new-privileges:true'], name
        assert service.get('pids_limit') == 256, name
        assert service.get('restart') == 'unless-stopped', name
        assert service.get('mem_limit'), name
        assert service.get('cpus'), name

    network = compose['networks']['observability']
    assert network.get('internal') is not True
    assert network['driver'] == 'bridge'
    assert network['driver_opts'] == {
        'com.docker.network.bridge.enable_ip_masquerade': 'false'
    }


def test_production_grafana_requires_secret_backed_login() -> None:
    raw = COMPOSE_FILE.read_text(encoding='utf-8')
    compose = yaml.safe_load(raw)
    grafana = compose['services']['grafana']
    environment = grafana.get('environment', {})

    assert environment.get('GF_AUTH_ANONYMOUS_ENABLED') == 'false'
    assert environment.get('GF_USERS_ALLOW_SIGN_UP') == 'false'
    assert environment.get('GF_SECURITY_COOKIE_SAMESITE') == 'strict'
    assert environment.get('GF_SECURITY_ADMIN_PASSWORD__FILE') == (
        '/run/secrets/grafana_admin_password'
    )
    assert 'GF_SECURITY_ADMIN_PASSWORD' not in environment
    assert grafana.get('secrets') == ['grafana_admin_password']
    assert compose['secrets']['grafana_admin_password']['file'].endswith(
        '/secrets/grafana_admin_password'
    )
    assert '123456' not in raw


def test_production_grafana_disables_plugin_downloads() -> None:
    grafana_config = (
        PRODUCTION_DIR / 'grafana' / 'grafana.ini'
    ).read_text(encoding='utf-8')

    assert 'plugin_admin_enabled = false' in grafana_config
    assert 'preinstall_disabled = true' in grafana_config
    assert 'preinstall_auto_update = false' in grafana_config


def test_production_grafana_secret_permissions_are_container_readable() -> None:
    deployment_doc = (
        PROJECT_ROOT
        / 'deploy'
        / 'backend'
        / 'grafana'
        / '生产可观测性部署说明.md'
    ).read_text(encoding='utf-8')

    assert 'chown root:root "$OBS_ROOT/secrets/grafana_admin_password"' in deployment_doc
    assert 'chmod 440 "$OBS_ROOT/secrets/grafana_admin_password"' in deployment_doc
    assert 'chmod 600 "$OBS_ROOT/secrets/grafana_admin_password"' not in deployment_doc


def test_production_observability_config_files_exist() -> None:
    expected = {
        'alloy/config.alloy',
        'grafana/dashboards.yml',
        'grafana/datasources.yml',
        'grafana/grafana.ini',
        'loki/loki.yml',
        'prometheus/prometheus.yml',
        'tempo/tempo.yml',
    }

    assert {
        str(path.relative_to(PRODUCTION_DIR))
        for path in PRODUCTION_DIR.rglob('*')
        if path.is_file() and path != COMPOSE_FILE
    } >= expected


def test_production_observability_persists_data_under_data_disk() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))

    for name, service in compose['services'].items():
        volumes = service.get('volumes', [])
        assert any(
            volume.startswith(
                f'${{OBS_DATA_DIR:-/data2/huanxing-observability}}/data/{name}:'
            )
            for volume in volumes
        ), name
        assert service.get('tmpfs') == [
            '/tmp:rw,noexec,nosuid,nodev,size=64m'
        ], name


def test_production_observability_mounts_configs_read_only() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding='utf-8'))
    expected = {
        'alloy': {'./alloy/config.alloy:/etc/alloy/config.alloy:ro'},
        'grafana': {
            './grafana/dashboards.yml:/etc/grafana/provisioning/dashboards/dashboards.yml:ro',
            './grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro',
            './grafana/grafana.ini:/etc/grafana/grafana.ini:ro',
            '../dashboards:/etc/grafana/dashboards:ro',
        },
        'loki': {'./loki/loki.yml:/etc/loki/loki.yml:ro'},
        'prometheus': {
            './prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro'
        },
        'tempo': {'./tempo/tempo.yml:/etc/tempo/tempo.yml:ro'},
    }

    for name, config_mounts in expected.items():
        assert set(compose['services'][name].get('volumes', [])) >= config_mounts


def test_production_tempo_uses_v3_retention_config() -> None:
    tempo_config = yaml.safe_load(
        (PRODUCTION_DIR / 'tempo' / 'tempo.yml').read_text(encoding='utf-8')
    )

    assert 'compactor' not in tempo_config
    assert (
        tempo_config['overrides']['defaults']['compaction']['block_retention']
        == '168h'
    )


def test_public_nginx_rejects_both_metrics_paths() -> None:
    assert NGINX_METRICS_SNIPPET.is_file(), '缺少生产 Nginx 指标收口配置'

    nginx = NGINX_METRICS_SNIPPET.read_text(encoding='utf-8')
    assert 'location = /metrics {' in nginx
    assert 'location ^~ /metrics/ {' in nginx
    assert nginx.count('return 404;') == 2
