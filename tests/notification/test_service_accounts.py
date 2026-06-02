"""服务号身份服务测试（§4.5）——连真库，事务回滚隔离。"""
from __future__ import annotations

import pytest

from backend.app.notification.service.service_account_service import service_account_service
from tests.notification.conftest import seed_human


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent(db):
    owner = await seed_human(db, nickname='主人')
    src = {'kind': 'app', 'id': 'install_123', 'display_name': '翻译星'}
    a1 = await service_account_service.get_or_create_for_source(db, owner_id=owner['hasn_id'], source=src)
    a2 = await service_account_service.get_or_create_for_source(db, owner_id=owner['hasn_id'], source=src)
    assert a1 is not None
    assert a1.sa_hasn_id == a2.sa_hasn_id  # 同 owner+kind+ref → 同一服务号
    assert a1.sa_hasn_id.startswith('sv_')
    assert a1.kind == 'app'
    assert a1.ref_id == 'install_123'


@pytest.mark.asyncio
async def test_system_source_is_verified(db):
    owner = await seed_human(db, nickname='主人')
    a = await service_account_service.get_or_create_for_source(
        db, owner_id=owner['hasn_id'], source={'kind': 'system', 'id': 'announcement', 'display_name': '唤星官方'}
    )
    assert a is not None
    assert a.verified is True


@pytest.mark.asyncio
async def test_agent_and_user_sources_create_no_account(db):
    owner = await seed_human(db, nickname='主人')
    a_agent = await service_account_service.get_or_create_for_source(
        db, owner_id=owner['hasn_id'], source={'kind': 'agent', 'id': 'a_x'}
    )
    a_user = await service_account_service.get_or_create_for_source(
        db, owner_id=owner['hasn_id'], source={'kind': 'user', 'id': 'h_y'}
    )
    assert a_agent is None  # agent 本身是会话身份，不建服务号
    assert a_user is None


@pytest.mark.asyncio
async def test_distinct_sources_distinct_accounts(db):
    owner = await seed_human(db, nickname='主人')
    a1 = await service_account_service.get_or_create_for_source(
        db, owner_id=owner['hasn_id'], source={'kind': 'app', 'id': 'app_1'}
    )
    a2 = await service_account_service.get_or_create_for_source(
        db, owner_id=owner['hasn_id'], source={'kind': 'app', 'id': 'app_2'}
    )
    assert a1.sa_hasn_id != a2.sa_hasn_id
