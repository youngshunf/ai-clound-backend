"""LlmNewapiUserMappingService 集成测试（零 mock，真实 new-api :3180 + 真实 PG）。

验证 D5 解耦后 service 层经 HTTP 管理 API 的全链路：
- ensure_newapi_user：建 new-api 用户 + 设额度 + 铸 access_token（加密落库）+ 建 relay token + 写映射
- get_api_key：sk- 前缀
- get_quota_info：经 API 读额度（断言等于设定值）
- ensure_agent_token / revoke_agent_token：Agent 级 token 签发与撤销
- get_usage_summary / get_usage_detail：新用户为空，断言形状不报错

new-api 不可达或 PG 连不上时整体 skip（infra-gated）；可达则全程真实联调并自清理。
"""

from __future__ import annotations

import secrets
import string

import httpx
import pytest
from sqlalchemy import delete, select

from backend.app.hermes.model import HermesAgentLlmToken
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.service import (
    DEFAULT_TIER_QUOTA,
    credits_to_quota,
    llm_newapi_user_mapping_service as svc,
)
from backend.common.security.encryption import key_encryption
from backend.core.conf import settings
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio


def _rnd(n: int = 6) -> str:
    return ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def _newapi_reachable() -> bool:
    if not settings.NEWAPI_ADMIN_ACCESS_TOKEN:
        return False
    try:
        with httpx.Client(timeout=3, trust_env=False) as c:
            r = c.get(f'{settings.NEWAPI_ADMIN_BASE_URL.rstrip("/")}/status')
            return r.status_code == 200 and r.json().get('success') is True
    except Exception:
        return False


pytest_skip = pytest.mark.skipif(
    not _newapi_reachable(),
    reason='new-api :3180 不可达或未配 NEWAPI_ADMIN_ACCESS_TOKEN（集成测试需真实 new-api，零 mock）',
)


@pytest_skip
async def test_service_full_lifecycle():
    from backend.app.newapi.client import newapi_admin_client

    # 用极高随机 huanxing_user_id 避免与真实数据/并发冲突（该列无 FK）
    huanxing_user_id = 900_000_000 + secrets.randbelow(90_000_000)
    username = f'hx_{huanxing_user_id}'
    agent_id = f'agent_ct_{_rnd()}'
    newapi_user_id: int | None = None
    try:
        async with async_db_session.begin() as db:
            # 1. ensure_newapi_user：建用户 + 设额度 + 写映射（含加密 access_token）
            info = await svc.ensure_newapi_user(
                db, huanxing_user_id, initial_quota=DEFAULT_TIER_QUOTA['pro']
            )
            assert info.huanxing_user_id == huanxing_user_id
            assert info.newapi_user_id > 0
            assert info.status == 'active'
            assert not info.newapi_token_key.startswith('sk-')  # 库里存裸 key
            newapi_user_id = info.newapi_user_id

            # 幂等：再次 ensure 返回同一映射
            info2 = await svc.ensure_newapi_user(db, huanxing_user_id)
            assert info2.newapi_user_id == newapi_user_id

            # 2. 映射落库且 access_token 已加密（可解密回明文）
            row = (
                await db.execute(
                    select(LlmNewapiUserMapping).where(
                        LlmNewapiUserMapping.huanxing_user_id == huanxing_user_id
                    )
                )
            ).scalar_one()
            assert row.newapi_access_token, 'access_token 应已加密落库'
            assert key_encryption.decrypt(row.newapi_access_token)  # 解密不抛即有效

            # 3. get_api_key：sk- 前缀
            api_key = await svc.get_api_key(db, huanxing_user_id)
            assert api_key.startswith('sk-')
            assert api_key[3:] == row.newapi_token_key

            # 4. get_quota_info：经 API 读额度，等于设定值（pro = 1000 积分）
            quota = await svc.get_quota_info(db, huanxing_user_id)
            assert quota.total_quota == credits_to_quota(1_000)
            assert quota.used_quota == 0
            assert quota.request_count == 0

            # 5. sync_quota：改额度后读回生效
            new_q = credits_to_quota(2_000)
            await svc.sync_quota(db, huanxing_user_id, new_q)
            assert (await svc.get_quota_info(db, huanxing_user_id)).total_quota == new_q

            # 6. ensure_agent_token：签发 Agent 级 token（复用已存 access_token）
            issued = await svc.ensure_agent_token(db, agent_id=agent_id, user_id=huanxing_user_id)
            assert issued['reused'] is False
            assert issued['newapi_token_id'] > 0
            assert issued['raw_token_key'] and not issued['raw_token_key'].startswith('sk-')
            assert issued['newapi_user_id'] == newapi_user_id

            # 幂等：再次 ensure 复用，不返回明文
            again = await svc.ensure_agent_token(db, agent_id=agent_id, user_id=huanxing_user_id)
            assert again['reused'] is True
            assert again['raw_token_key'] is None
            assert again['newapi_token_id'] == issued['newapi_token_id']

            # 7. revoke_agent_token：撤销有效记录 → True；再撤 → False（幂等）
            assert await svc.revoke_agent_token(db, agent_id) is True
            assert await svc.revoke_agent_token(db, agent_id) is False

            # 8. 用量（新用户为空，断言形状与不报错）
            now = 2_000_000_000
            summary = await svc.get_usage_summary(db, huanxing_user_id, now - 86400, now)
            assert summary.total_requests == 0
            assert summary.items == []
            detail = await svc.get_usage_detail(db, huanxing_user_id, now - 86400, now)
            assert detail.total == 0
            assert detail.items == []
            by_agent = await svc.get_usage_summary_by_agent(db, agent_id, now - 86400, now)
            assert by_agent['agent_id'] == agent_id
            assert by_agent['by_model'] == []
    finally:
        # 自清理：删 new-api 用户 + 本地映射 + hermes token 行
        if newapi_user_id is not None:
            await newapi_admin_client.delete_user(newapi_user_id)
        async with async_db_session.begin() as db:
            await db.execute(
                delete(LlmNewapiUserMapping).where(
                    LlmNewapiUserMapping.huanxing_user_id == huanxing_user_id
                )
            )
            await db.execute(
                delete(HermesAgentLlmToken).where(HermesAgentLlmToken.agent_id == agent_id)
            )
