"""R3 三数据库角色连接池与生产启动硬闸验收。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.hasn_im.protocol.version_gate import (
    R3_COMPANION_DAEMON_VERSION,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROLE_URLS = {
    'IM_SERVICE_DATABASE_URL': (
        'postgresql+asyncpg://astra_im_service:test-only@127.0.0.1:15432/'
        'huanxing_im_r3_pool_probe'
    ),
    'SYNC_SERVICE_DATABASE_URL': (
        'postgresql+asyncpg://astra_sync_service:test-only@127.0.0.1:15432/'
        'huanxing_im_r3_pool_probe'
    ),
    'PYTHON_BACKEND_DATABASE_URL': (
        'postgresql+asyncpg://astra_python_backend:test-only@127.0.0.1:15432/'
        'huanxing_im_r3_pool_probe'
    ),
}


def _run_backend(code: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    """在独立解释器中按完整启动导入配置，避免全局 Settings 缓存污染断言。"""
    env = os.environ.copy()
    env.update(
        {
            'DATABASE_TYPE': 'postgresql',
            'DATABASE_HOST': '127.0.0.1',
            'DATABASE_PORT': '15432',
            'DATABASE_USER': 'mac',
            'DATABASE_PASSWORD': '',
            'DATABASE_SCHEMA': 'huanxing_im_r3_pool_probe',
            **overrides,
        }
    )
    return subprocess.run(
        [sys.executable, '-c', code],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_three_nonempty_role_dsns_build_three_independent_pools() -> None:
    """三 DSN 非空时，engine、pool 与 session maker bind 必须逐角色独立。"""
    proc = _run_backend(
        """
import json
from backend.database import db

engines = [
    db.im_service_engine,
    db.sync_service_engine,
    db.python_backend_engine,
]
makers = [
    db.im_service_db_session,
    db.sync_service_db_session,
    db.python_backend_db_session,
]
print(json.dumps({
    "engine_ids": [id(engine) for engine in engines],
    "pool_ids": [id(engine.pool) for engine in engines],
    "bind_ids": [id(maker.kw["bind"]) for maker in makers],
    "users": [engine.url.username for engine in engines],
    "pool_sizes": [engine.pool.size() for engine in engines],
    "max_overflows": [engine.pool._max_overflow for engine in engines],
}))
""",
        ENVIRONMENT='dev',
        HASN_IM_SCHEMA_CUTOVER='false',
        DATABASE_POOL_SIZE='2',
        DATABASE_MAX_OVERFLOW='3',
        **_ROLE_URLS,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(set(result['engine_ids'])) == 3
    assert len(set(result['pool_ids'])) == 3
    assert result['bind_ids'] == result['engine_ids']
    assert result['users'] == [
        'astra_im_service',
        'astra_sync_service',
        'astra_python_backend',
    ]
    assert result['pool_sizes'] == [2, 2, 2]
    assert result['max_overflows'] == [3, 3, 3]


def test_prod_create_tables_does_not_open_database_connection() -> None:
    """生产启动的建表接缝必须在连库前返回，因此不可能执行 schema/table DDL。"""
    proc = _run_backend(
        """
import asyncio
from backend.database.db import create_tables

asyncio.run(create_tables())
print("prod-no-ddl")
""",
        ENVIRONMENT='prod',
        DATABASE_HOST='127.0.0.1',
        DATABASE_PORT='1',
        DATABASE_USER='must_not_connect',
        DATABASE_PASSWORD='must_not_connect',
        DATABASE_SCHEMA='huanxing_im_r3_no_ddl',
        DATABASE_AUTO_CREATE_TABLES='true',
        HASN_IM_SCHEMA_CUTOVER='false',
        IM_SERVICE_DATABASE_URL='',
        SYNC_SERVICE_DATABASE_URL='',
        PYTHON_BACKEND_DATABASE_URL='',
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == 'prod-no-ddl'


@pytest.mark.parametrize(
    'missing_name',
    [
        'IM_SERVICE_DATABASE_URL',
        'SYNC_SERVICE_DATABASE_URL',
        'PYTHON_BACKEND_DATABASE_URL',
        'HASN_WS_MIN_CLIENT_VERSION',
    ],
)
def test_prod_cutover_missing_required_setting_rejects_process_start(
    missing_name: str,
) -> None:
    """生产硬切换缺任一角色 DSN 或 daemon 最低版本时，模块导入即失败。"""
    required = {
        **_ROLE_URLS,
        'HASN_WS_MIN_CLIENT_VERSION': R3_COMPANION_DAEMON_VERSION,
    }
    required[missing_name] = ''
    proc = _run_backend(
        'from backend.core.conf import settings',
        ENVIRONMENT='prod',
        HASN_IM_SCHEMA_CUTOVER='true',
        **required,
    )
    assert proc.returncode != 0
    assert 'R3 生产硬切换配置不完整' in proc.stderr
    assert missing_name in proc.stderr
