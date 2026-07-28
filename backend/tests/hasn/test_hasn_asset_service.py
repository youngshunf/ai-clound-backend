"""hasn_asset_service 真实 PG 集成测试（09 Stage1d-1f 验收：鉴权三态 + public 恒可读）。

零 mock 原则：用真实本地 PostgreSQL(15432) 跑注册/授权/解析全链路；仅签名网络边界
（StorageService.signed_urls_cached）用 fake，避免真实 S3/Redis。事务结束回滚，不污染库。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.hasn.model import HasnConversations
from backend.app.hasn.service import hasn_asset_service as svc_mod
from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import ObjectRef

# 同模块多个 async 测试共用一个 event loop：全局 async engine 连接池绑定首个 loop，
# 缺此标记时第二个测试会撞 "attached to a different loop"（仓内先例 test_hasn_artifacts_service.py）。
pytestmark = pytest.mark.asyncio(loop_scope='session')


def _short_id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:20]}'  # ≤ varchar(40)


async def _fake_sign(_db, *, items, expires_in=3600):
    return {it: f'https://signed/{it[1]}?e={expires_in}' for it in items}


async def test_resolve_authz_three_state_and_public_always_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc_mod.StorageService,
        'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)),
        raising=True,
    )

    owner_a = _short_id('hasnA')
    peer_b = _short_id('hasnB')
    stranger_c = _short_id('hasnC')

    async with async_db_session() as db:
        try:
            # 单聊会话：A(human) <-> B(agent)
            conv = HasnConversations(
                type='direct',
                participant_a_id=owner_a,
                participant_a_type='human',
                participant_b_id=peer_b,
                participant_b_type='agent',
            )
            db.add(conv)
            await db.flush()
            conv_id = conv.id

            # A 拥有：一个私有附件 + 一个公开图
            priv = await HasnAssetService.register_asset(
                db,
                owner_hasn_id=owner_a,
                ref=ObjectRef(storage_id=1, object_key='dm/secret.png', access='private', stable_url='', mime='image/png', size=100),
                kind='image',
            )
            pub = await HasnAssetService.register_asset(
                db,
                owner_hasn_id=owner_a,
                ref=ObjectRef(storage_id=1, object_key='posts/cover.png', access='public', stable_url='', mime='image/png', size=200),
                kind='image',
            )
            ids = [priv.asset_id, pub.asset_id]

            # —— 授权前 ——
            # owner A：私有+公开都可读
            a_res = {r.asset_id for r in await HasnAssetService.resolve(db, requester_hasn_id=owner_a, asset_ids=ids, conversation_id=conv_id)}
            assert a_res == set(ids)

            # 参与者 B 授权前：私有不可读（未 grant），公开可读
            b_before = {r.asset_id for r in await HasnAssetService.resolve(db, requester_hasn_id=peer_b, asset_ids=ids, conversation_id=conv_id)}
            assert b_before == {pub.asset_id}

            # —— 落消息：为私有附件按会话写 grant ——
            await HasnAssetService.grant_to_conversation(db, asset_id=priv.asset_id, conversation_id=conv_id)
            await db.flush()
            # 幂等：再写一次不报错
            await HasnAssetService.grant_to_conversation(db, asset_id=priv.asset_id, conversation_id=conv_id)
            await db.flush()

            # 参与者 B 授权后：私有+公开都可读
            b_after = {r.asset_id for r in await HasnAssetService.resolve(db, requester_hasn_id=peer_b, asset_ids=ids, conversation_id=conv_id)}
            assert b_after == set(ids)

            # 陌生人 C：私有不可读（非参与者，即便会话已 grant），公开仍可读
            c_res = {r.asset_id for r in await HasnAssetService.resolve(db, requester_hasn_id=stranger_c, asset_ids=ids, conversation_id=conv_id)}
            assert c_res == {pub.asset_id}

            # public 带 None 过期；private 带签名 URL + expires_at
            full = await HasnAssetService.resolve(db, requester_hasn_id=peer_b, asset_ids=ids, conversation_id=conv_id)
            by_id = {r.asset_id: r for r in full}
            assert by_id[pub.asset_id].expires_at is None
            assert by_id[priv.asset_id].expires_at is not None
            assert by_id[priv.asset_id].display_url.startswith('https://signed/')
        finally:
            await db.rollback()  # 不污染本地库


async def test_resolve_a2a_participant_agent_owner_can_read_granted_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2A 附件场景：会话参与者是两个 agent，接收方 daemon 以主人 owner JWT 解析。

    复刻 2026-07-05 真实 bug：福仔分身发附件给明远（瘦瘦福仔的分身），接收方 daemon 用
    瘦瘦福仔 owner JWT resolve 被旧 is_participant 拒（主人不在 a/b 名单）→ 附件进不了
    runtime。修复后：参与 agent 的主人可读已 grant 资产；无关主人仍不可读。
    """
    monkeypatch.setattr(
        svc_mod.StorageService,
        'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)),
        raising=True,
    )

    from backend.app.hasn.model import HasnAgents

    sender_owner = _short_id('hasnS')  # 发送方主人（资产 owner）
    recipient_owner = _short_id('hasnR')  # 接收方主人（其分身是会话参与者）
    outsider_owner = _short_id('hasnO')  # 无关主人（有分身但不参与该会话）
    sender_agent = f'a_{uuid4().hex[:18]}'
    recipient_agent = f'a_{uuid4().hex[:18]}'
    outsider_agent = f'a_{uuid4().hex[:18]}'

    async with async_db_session() as db:
        try:
            # star_id 有唯一约束，逐个给唯一值（默认空串会撞车）
            db.add(HasnAgents(hasn_id=sender_agent, owner_id=sender_owner, star_id=_short_id('star')))
            db.add(HasnAgents(hasn_id=recipient_agent, owner_id=recipient_owner, star_id=_short_id('star')))
            db.add(HasnAgents(hasn_id=outsider_agent, owner_id=outsider_owner, star_id=_short_id('star')))
            # A2A 单聊会话：两个参与者都是 agent（主人都不在 a/b 名单里）
            conv = HasnConversations(
                type='direct',
                participant_a_id=sender_agent,
                participant_a_type='agent',
                participant_b_id=recipient_agent,
                participant_b_type='agent',
            )
            db.add(conv)
            await db.flush()
            conv_id = conv.id

            # 发送方主人的私有附件，落消息时已 grant 给该会话
            priv = await HasnAssetService.register_asset(
                db,
                owner_hasn_id=sender_owner,
                ref=ObjectRef(storage_id=1, object_key='a2a/doc.md', access='private', stable_url='', mime='text/markdown', size=1308),
                kind='file',
            )
            await HasnAssetService.grant_to_conversation(db, asset_id=priv.asset_id, conversation_id=conv_id)
            await db.flush()

            # 参与 agent 本身可读（原行为不回归）
            got_agent = await HasnAssetService.resolve(db, requester_hasn_id=recipient_agent, asset_ids=[priv.asset_id], conversation_id=conv_id)
            assert [r.asset_id for r in got_agent] == [priv.asset_id]

            # 接收方主人（参与 agent 的 owner）：修复点——可读
            got_owner = await HasnAssetService.resolve(db, requester_hasn_id=recipient_owner, asset_ids=[priv.asset_id], conversation_id=conv_id)
            assert [r.asset_id for r in got_owner] == [priv.asset_id]

            # 发送方主人：资产 owner 恒可读（与会话判定无关）
            got_sender = await HasnAssetService.resolve(db, requester_hasn_id=sender_owner, asset_ids=[priv.asset_id], conversation_id=conv_id)
            assert [r.asset_id for r in got_sender] == [priv.asset_id]

            # 无关主人（其分身不参与该会话）：仍不可读——扩展没有放宽到任意 owner
            got_outsider = await HasnAssetService.resolve(db, requester_hasn_id=outsider_owner, asset_ids=[priv.asset_id], conversation_id=conv_id)
            assert got_outsider == []

            # 不传会话上下文：接收方主人不可读（参与者判定必须落在具体会话上）
            got_no_conv = await HasnAssetService.resolve(db, requester_hasn_id=recipient_owner, asset_ids=[priv.asset_id])
            assert got_no_conv == []
        finally:
            await db.rollback()  # 不污染本地库


async def test_legacy_local_snapshot_writer_is_removed() -> None:
    """本地原件上传不得绕过统一 Owner Storage 编排。"""
    assert not hasattr(HasnAssetService, 'upload_local_source_snapshot')
