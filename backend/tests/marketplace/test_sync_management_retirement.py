"""技能市场旧服务器同步入口退役契约测试。"""

from __future__ import annotations

import subprocess
import sys

from pathlib import Path
from typing import Any, cast

import pytest

from fastapi import HTTPException

from backend.app.marketplace.api.v1.admin.sync_management import (
    SyncRequest,
    trigger_github_sync,
)


@pytest.mark.asyncio
async def test_github_skill_server_sync_endpoint_is_explicitly_retired() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await trigger_github_sync(cast(Any, None), SyncRequest())

    assert exc_info.value.status_code == 410
    assert 'publish_skills.py' in str(exc_info.value.detail)


def test_legacy_github_sync_script_is_explicitly_retired() -> None:
    root = Path(__file__).parents[3]
    result = subprocess.run(
        [sys.executable, str(root / 'scripts' / 'run_github_sync.py')],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 1
    assert 'publish_skills.py' in result.stderr
