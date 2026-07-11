"""doc08 RT1：信任等级 ↔ 联系人状态联动（B1 拉黑 / D1 移出黑名单恢复普通朋友）。

策略：
1. 直测纯判定函数 `_resolve_status_on_trust_change`（无副作用，最干净的单测面）；
2. 用假 contact + AsyncMock db + patch 掉 DAO，驱动 `update_trust_level` 端点，
   证明联动确实写回 `contact.status` 并回显在响应体（不依赖真实 PG）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.api.v1.app import contacts as contacts_api
from backend.app.hasn.api.v1.app.contacts import _resolve_status_on_trust_change
from backend.app.hasn.schema.hasn_contacts_business import HasnTrustLevelReq


# ── 一、纯判定函数（B1 / B6 / D1） ─────────────────────────


def test_resolve_status_blocks_on_trust_zero() -> None:
    """trust=0（拉入黑名单）→ blocked，无论原状态（B1）。"""
    assert _resolve_status_on_trust_change('connected', 0) == 'blocked'
    assert _resolve_status_on_trust_change('pending', 0) == 'blocked'


def test_resolve_status_recovers_connected_from_blocked() -> None:
    """当前 blocked 且 trust≥1（移出黑名单）→ connected（D1 恢复普通朋友）。"""
    assert _resolve_status_on_trust_change('blocked', 2) == 'connected'
    assert _resolve_status_on_trust_change('blocked', 1) == 'connected'
    assert _resolve_status_on_trust_change('blocked', 5) == 'connected'


def test_resolve_status_keeps_status_on_normal_retune() -> None:
    """普通调档（非 0、原状态非 blocked）→ 保持原 status，不误改状态。"""
    assert _resolve_status_on_trust_change('connected', 3) == 'connected'
    assert _resolve_status_on_trust_change('connected', 4) == 'connected'
    assert _resolve_status_on_trust_change('pending', 2) == 'pending'


# ── 二、端点联动（证明纯判定被正确接线到 contact.status） ─────


def _make_contact(status: str, trust_level: int) -> SimpleNamespace:
    """造一个满足 update_trust_level 端点读写需要的假 contact。

    relation_type=social 保证 validate_relation_constraints 对 0/2/3 均放行；
    owner_id 与端点 auth 的 hasn_id 对齐，绕过 403。
    """
    return SimpleNamespace(
        owner_id='h_owner_test',
        peer_id='a_peer_test',
        peer_type='agent',
        relation_type='social',
        status=status,
        trust_level=trust_level,
    )


async def _invoke(contact: SimpleNamespace, new_trust: int) -> dict:
    """patch 掉 DAO + 用 AsyncMock db 驱动 update_trust_level，返回响应 data。"""
    db = AsyncMock()
    auth = {'hasn_id': contact.owner_id}
    with patch.object(contacts_api.hasn_contacts_dao, 'get', AsyncMock(return_value=contact)):
        resp = await contacts_api.update_trust_level(
            contact_id=1,
            obj_in=HasnTrustLevelReq(trust_level=new_trust),
            db=db,
            auth=auth,
        )
    db.commit.assert_awaited_once()
    return resp.data


@pytest.mark.asyncio
async def test_endpoint_blocks_contact_on_trust_zero() -> None:
    """初始 connected，传 trust=0 → 结束时 status=blocked 且 trust_level=0（拉黑 B1）。"""
    contact = _make_contact(status='connected', trust_level=3)
    data = await _invoke(contact, new_trust=0)
    assert contact.status == 'blocked'
    assert contact.trust_level == 0
    assert data['status'] == 'blocked'
    assert data['trust_level'] == 0


@pytest.mark.asyncio
async def test_endpoint_recovers_connected_from_blocked() -> None:
    """初始 blocked，传 trust=2 → 结束时 status=connected（D1 移出黑名单恢复普通朋友）。"""
    contact = _make_contact(status='blocked', trust_level=0)
    data = await _invoke(contact, new_trust=2)
    assert contact.status == 'connected'
    assert contact.trust_level == 2
    assert data['status'] == 'connected'


@pytest.mark.asyncio
async def test_endpoint_keeps_status_on_normal_retune() -> None:
    """初始 connected，传 trust=3 → status 保持 connected（普通调档不误改状态）。"""
    contact = _make_contact(status='connected', trust_level=2)
    data = await _invoke(contact, new_trust=3)
    assert contact.status == 'connected'
    assert contact.trust_level == 3
    assert data['status'] == 'connected'
