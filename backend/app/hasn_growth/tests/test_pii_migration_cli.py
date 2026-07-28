"""获客存量 PII 迁移 CLI 的安全输出契约测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
_TEST_KEY = 'ERERERERERERERERERERERERERERERERERERERERERE='


def test_invalid_key_configuration_outputs_only_safe_json() -> None:
    """密钥初始化失败时不得输出堆栈、配置值或数据库细节。"""
    environment = os.environ.copy()
    environment.update(
        {
            'GROWTH_PII_ENCRYPTION_KEYS_JSON': json.dumps({'1': _TEST_KEY}),
            'GROWTH_PII_HMAC_KEYS_JSON': json.dumps({'1': _TEST_KEY}),
            'GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION': '1',
            'GROWTH_PII_ACTIVE_HMAC_KEY_VERSION': '1',
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'backend.scripts.migrate_growth_pii',
            '--source',
            'contact',
            '--after-id',
            '42',
            '--max-batches',
            '1',
        ],
        cwd=_REPO,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert not result.stdout
    stderr_lines = result.stderr.splitlines()
    assert len(stderr_lines) == 1
    assert json.loads(stderr_lines[0]) == {
        'status': 'failed',
        'source_table': 'contact',
        'after_id': 42,
        'error_type': 'GrowthPiiKeyConfigurationError',
    }
    assert 'Traceback' not in result.stderr
    assert _TEST_KEY not in result.stderr
