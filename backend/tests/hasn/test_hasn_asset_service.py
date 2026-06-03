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


def _short_id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:20]}'  # ≤ varchar(40)


async def _fake_sign(_db, *, items, expires_in=3600):
    return {it: f'https://signed/{it[1]}?e={expires_in}' for it in items}


@pytest.mark.asyncio
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
