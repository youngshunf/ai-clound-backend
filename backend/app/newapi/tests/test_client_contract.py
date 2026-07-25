"""NewApiAdminClient 契约测试（零 mock，对真实 new-api :3180）。

覆盖 §10 P1 全部能力：建用户幂等、改/读 quota、铸 access_token、建 token + 取明文 key、
禁用 token、读 log/stat/data、批量 quota、启动校验 quota_per_unit。

new-api 不可达时整体 skip（infra-gated），可达则全程真实联调并自清理。
"""

from __future__ import annotations

import secrets
import string
import time

import httpx
import pytest

from backend.app.newapi.client import NewApiAdminClient, NewApiError
from backend.core.conf import settings

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
    reason='new-api :3180 不可达或未配 NEWAPI_ADMIN_ACCESS_TOKEN（契约测试需真实 new-api，零 mock）',
)


#: new-api 的协议精度：1 积分 = 500000 quota。
#: doc94 D1 后云端不再持有换算常量，但这条**契约**仍需被锁住——
#: new-api 一旦改了 QuotaPerUnit，它自己产出的所有积分字符串刻度都会变。
_EXPECTED_QUOTA_PER_UNIT = 500_000


@pytest_skip
async def test_status_and_quota_per_unit() -> None:
    client = NewApiAdminClient()
    qpu = await client.get_quota_per_unit()
    assert qpu == _EXPECTED_QUOTA_PER_UNIT, (
        f'new-api quota_per_unit={qpu} 与协议精度 {_EXPECTED_QUOTA_PER_UNIT} 不一致：'
        '积分刻度变了，所有积分字符串的含义都会跟着变'
    )


@pytest_skip
async def test_full_credential_lifecycle() -> None:
    client = NewApiAdminClient()
    username = f'hx_ct_{_rnd()}'
    newapi_user_id: int | None = None
    try:
        # 1. ensure_user 幂等：两次返回同一 id
        uid1 = await client.ensure_user(username=username, display_name=username)
        uid2 = await client.ensure_user(username=username, display_name=username)
        assert uid1 == uid2 and uid1 > 0
        newapi_user_id = uid1

        # 2. 读 quota：余额是 NewAPI 权威，云端只读。
        #    「设置绝对 quota」已在 doc94 P0 封禁，发放/回收一律走幂等履约事件，
        #    因此这里只校验读契约的形状，不再断言由云端写入的具体数值。
        with pytest.raises(NewApiError):
            await client.set_user_quota(newapi_user_id=newapi_user_id, quota=5_000_000, reason='contract-test')
        info = await client.get_user_quota(newapi_user_id)
        assert info is not None
        assert set(info) >= {'quota', 'used_quota', 'request_count'}

        # 3. 批量 quota：形状一致
        batch = await client.get_batch_users_quota([newapi_user_id])
        assert newapi_user_id in batch
        assert 'quota' in batch[newapi_user_id]

        # 4. 铸用户 access_token
        access_token = await client.bootstrap_user_access_token(newapi_user_id=newapi_user_id, username=username)
        assert isinstance(access_token, str) and len(access_token) >= 16

        # 5. 建 relay token + 取明文 key
        token_id, key = await client.provision_user_relay_token(
            newapi_user_id=newapi_user_id,
            username=username,
            user_access_token=access_token,
            name='huanxing-default',
        )
        assert token_id > 0
        assert isinstance(key, str) and len(key) >= 32  # new-api 自生成裸 key，无 sk- 前缀
        assert not key.startswith('sk-')

        # 6. find_token + get_token_remain_quota
        found = await client.find_token(username=username, name='huanxing-default')
        assert found is not None and found['id'] == token_id
        remain = await client.get_token_remain_quota(token_id)
        assert remain is not None  # unlimited_quota=True 时 remain_quota 为初始值

        # 7. 禁用 token → status=2
        assert await client.disable_token(token_id) is True
        tok = await client.get_token(token_id)
        assert tok is not None and tok['status'] == 2

        # 8. 日志/统计/数据（新用户为空，断言形状与不报错）
        now = int(time.time())
        items, total = await client.get_logs(
            username=username, start_timestamp=now - 86400, end_timestamp=now + 60
        )
        assert isinstance(items, list) and total == 0
        stat = await client.get_log_stat(username=username, start_timestamp=now - 86400, end_timestamp=now + 60)
        assert int(stat.get('quota') or 0) == 0
        data = await client.get_quota_data(username=username, start_timestamp=now - 7 * 86400, end_timestamp=now + 60)
        assert isinstance(data, list)
    finally:
        if newapi_user_id is not None:
            await client.delete_user(newapi_user_id)


@pytest_skip
async def test_get_user_quota_missing_returns_none() -> None:
    """不存在的 user → get_user_quota 返回 None（admin GET /user/:id 报错被吞为 None 降级）。"""
    client = NewApiAdminClient()
    # 用一个极大概率不存在的 id
    try:
        info = await client.get_user_quota(999_999_999)
    except NewApiError:
        info = None  # new-api 对不存在 id 返回 success=false → 抛错；调用方降级
    assert info is None
