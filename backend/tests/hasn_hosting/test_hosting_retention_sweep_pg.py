"""云端节点订阅生命周期 sweep（设计 §7 / D-14）——零 mock，打真实 PostgreSQL + Redis。

**不 mock 任何被测代码**：`hosting_agent_provider` 走真实 httpx 打一个**真跑在本机端口上的
契约服务器**（形态照抄 `backend/tests/marketplace/test_clawhub_metadata_distribution.py` 的
`_ClawHubContractHandler`），响应码由用例控制；「宿主不可达」用例则指向一个必定没人监听的端口。
托管行、事件流水、`hasn_nodes` 退役标记、通知行全部落真实库并回读断言。

覆盖：
1. 订阅到期 → 真停容器 → 进 30 天保留期 + `stopped` 事件 + 通知
2. 逾期销毁的**完整顺序**：契约服务器在收到 `DELETE /v1/nodes/{id}` 的那一刻回查 Redis，
   断言设备凭据的 JWT session **此时已被吊销**（顺序反了就会查到 key 还在）
3. 续订恢复；启动失败如实落 `failed` + 原因码，不假装恢复成功
4. 宿主不可达 → 本轮跳过，绝不把状态改成「看起来正常」的值
5. 到期前 7/3/1 天提醒不重不漏（跑两轮只留一行）
6. 幂等：同一轮跑两次，第二次全部为 0 且库里状态不再变化
"""

from __future__ import annotations

import asyncio
import socket

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import uvicorn

from sqlalchemy import func, select, text
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_nodes import HasnNodes
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.hasn_hosting.model import HasnCloudNodeEvents, HasnCloudNodes
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.app.hasn_hosting.tasks import RETENTION_DAYS, run_cloud_node_retention_sweep
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.requests import Request

pytestmark = pytest.mark.asyncio

OWNER = 'h_hosting_sweep_owner'
USER_ID = 990707
NODE_PREFIX = 'n_cloud_test_sweep_'
NODE_A = f'{NODE_PREFIX}0001'
CREDENTIAL_SESSION = 'sweep-session-uuid-0001'


def _free_port() -> int:
    """借一个立刻归还的端口号——保证该端口上没有监听者（连接必定被拒）。"""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def _token_key(user_id: int = USER_ID, session_uuid: str = CREDENTIAL_SESSION) -> str:
    """设备凭据 JWT session 在 Redis 里的 key（`revoke_token` 删的就是它）。"""
    return f'{settings.TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}'


# ─── 真跑在本机端口上的 hosting-agent 契约服务器 ───


class _HostingAgentContract:
    """实现契约 §4 的最小子集：`/health` + `stop` / `start` / `DELETE`。

    不是 mock 对象：它是一个真的 HTTP 服务，被测代码经真实 httpx + 真实 Bearer 头打进来。
    每个端点的状态码可由用例调整，用来复现「agent 明确拒绝」这类真实分支。
    """

    def __init__(self) -> None:
        self.base_url = ''
        #: 收到的调用序列 `(method, path)`，用于断言顺序与幂等
        self.calls: list[tuple[str, str]] = []
        #: 收到 DELETE 时回查 Redis 的结果：设备凭据 key 是否**还在**
        self.credential_present_at_delete: bool | None = None
        self.delete_query: dict[str, str] = {}
        self.health_ok = True
        self.stop_status = 200
        self.start_status = 200
        self.delete_status = 200

    async def _health(self, request: Request) -> JSONResponse:
        self.calls.append(('GET', '/health'))
        if not self.health_ok:
            return JSONResponse({'detail': 'docker unavailable'}, status_code=503)
        return JSONResponse({'ok': True, 'docker': True, 'backup_configured': False})

    async def _stop(self, request: Request) -> JSONResponse:
        self.calls.append(('POST', request.url.path))
        if self.stop_status != 200:
            return JSONResponse({'detail': {'failure_reason': 'container_crashed'}}, status_code=self.stop_status)
        return JSONResponse({'ok': True, 'status': 'stopped'})

    async def _start(self, request: Request) -> JSONResponse:
        self.calls.append(('POST', request.url.path))
        if self.start_status != 200:
            return JSONResponse({'detail': {'failure_reason': 'container_crashed'}}, status_code=self.start_status)
        return JSONResponse({'ok': True, 'status': 'starting'})

    async def _delete(self, request: Request) -> JSONResponse:
        self.calls.append(('DELETE', request.url.path))
        self.delete_query = dict(request.query_params)
        # 顺序探针：容器被销毁的这一刻，设备凭据必须**已经**失效（契约 §3.1 / 设计 §7）
        self.credential_present_at_delete = bool(await redis_client.exists(_token_key()))
        if self.delete_status != 200:
            return JSONResponse({'detail': 'purge failed'}, status_code=self.delete_status)
        return JSONResponse({'ok': True, 'purged': True})

    def build_app(self) -> Starlette:
        return Starlette(
            routes=[
                Route('/health', self._health, methods=['GET']),
                Route('/v1/nodes/{node_id}/stop', self._stop, methods=['POST']),
                Route('/v1/nodes/{node_id}/start', self._start, methods=['POST']),
                Route('/v1/nodes/{node_id}', self._delete, methods=['DELETE']),
            ]
        )


@pytest_asyncio.fixture
async def agent() -> AsyncIterator[_HostingAgentContract]:
    """起一个真 uvicorn 服务承载契约服务器，用例结束后关闭。"""
    contract = _HostingAgentContract()
    config = uvicorn.Config(contract.build_app(), host='127.0.0.1', port=0, log_level='error', lifespan='off')
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    if not server.started:
        server.should_exit = True
        await task
        pytest.fail('契约服务器未能启动')
    contract.base_url = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
    try:
        yield contract
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def agent_reachable(agent: _HostingAgentContract, monkeypatch: pytest.MonkeyPatch) -> _HostingAgentContract:
    """把 provider 指向契约服务器（env 优先级最高，`service_endpoint` 每次调用重读）。"""
    monkeypatch.setenv('HOSTING_AGENT_URL', agent.base_url)
    return agent


@pytest.fixture
def agent_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 provider 指向一个必定没人监听的端口——真实的「宿主不可达」。"""
    monkeypatch.setenv('HOSTING_AGENT_URL', f'http://127.0.0.1:{_free_port()}')


# ─── 库夹具 ───


async def _purge(db: AsyncSession) -> None:
    await db.execute(text('DELETE FROM hasn_cloud_node_events WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await db.execute(text('DELETE FROM hasn_cloud_nodes WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await db.execute(text('DELETE FROM hasn_nodes WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    # 通知卡片承载会顺带建「服务号 ⇄ 主人」会话；那是通知域自己的幂等资源，留着不影响重跑
    await db.execute(text('DELETE FROM hasn_notifications WHERE target_id = :o'), {'o': OWNER})
    await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :o'), {'o': OWNER})
    await db.execute(text('DELETE FROM hasn_billing.user_subscription WHERE user_id = :u'), {'u': USER_ID})
    await db.commit()


def _owner_identity() -> HasnHumans:
    """通知卡片承载要求收件方是真实身份（`ImSendRejected 2002`），所以主人必须真存在。"""
    return HasnHumans(
        hasn_id=OWNER,
        star_id='990707',
        user_id=USER_ID,
        nickname='托管 sweep 测试主人',
        bio=None,
        avatar=None,
        status='active',
        contact_policy={},
        timezone=None,
        tags=None,
        stats={},
        community_settings={},
    )


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """测试自己的会话；sweep 内部另开自己的事务，两边靠 commit + expunge 对齐。"""
    session = async_db_session()
    try:
        await session.execute(select(1))
    except Exception as exc:
        await session.close()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    try:
        await _purge(session)
        await redis_client.delete(_token_key())
        session.add(_owner_identity())
        await session.commit()
        yield session
    finally:
        await session.rollback()
        await _purge(session)
        await redis_client.delete(_token_key())
        await session.close()


def _cloud_node(
    node_id: str = NODE_A,
    *,
    status: str = 'starting',
    retain_until: Any = None,
    credential_session_uuid: str | None = None,
) -> HasnCloudNodes:
    return HasnCloudNodes(
        node_id=node_id,
        user_id=USER_ID,
        owner_hasn_id=OWNER,
        host='hosting-test',
        container_ref='c-sweep',
        status=status,
        failure_reason=None,
        failure_detail=None,
        image_version='0.0.1',
        image_digest='sha256:' + '3' * 64,
        credential_session_uuid=credential_session_uuid,
        retain_until=retain_until,
        last_backup_at=None,
        online_since=None,
    )


def _hasn_node(node_id: str = NODE_A) -> HasnNodes:
    return HasnNodes(
        node_id=node_id,
        user_id=USER_ID,
        allowed_owner_hasn_ids=[OWNER],
        node_type='cloud',
        node_name='云端节点',
        device_fingerprint=None,
        device_platform='server',
        app_version='0.0.1',
        ip_address=None,
        ip_location=None,
        node_info={'hosting': True},
        node_key_hash=None,
        capacity=1,
        created_by_owner_id=OWNER,
        last_seen_at=None,
        status='active',
    )


def _subscription(*, days_left: int | None = 30, status: str = 'active') -> UserSubscription:
    now = timezone.now()
    end = now + timedelta(days=days_left) if days_left is not None else None
    return UserSubscription(
        app_code='huanxing',
        user_id=USER_ID,
        tier='pro',
        subscription_type='monthly',
        monthly_credits=Decimal(0),
        current_credits=Decimal(0),
        used_credits=Decimal(0),
        purchased_credits=Decimal(0),
        billing_cycle_start=now,
        billing_cycle_end=end or now,
        subscription_start_date=now,
        subscription_end_date=end,
        next_grant_date=None,
        status=status,
        auto_renew=True,
        plan_snapshot=None,
    )


async def _reload(db: AsyncSession, node_id: str = NODE_A) -> HasnCloudNodes:
    """sweep 在自己的事务里提交，本会话必须先丢掉旧身份映射才能读到新事实。"""
    await db.rollback()
    db.expunge_all()
    return (
        await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
    ).scalar_one()


async def _events(db: AsyncSession, node_id: str = NODE_A) -> list[HasnCloudNodeEvents]:
    return list(
        (
            await db.execute(
                select(HasnCloudNodeEvents)
                .where(HasnCloudNodeEvents.node_id == node_id)
                .order_by(HasnCloudNodeEvents.created_time)
            )
        ).scalars().all()
    )


# ─── 动作 1：订阅到期 → 进保留期 ───


async def test_expired_subscription_stops_container_then_enters_retention(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """订阅失效 → 先真停容器，再落 stopped + retain_until=now+30d + stopped 事件 + 通知主人。"""
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=-1))  # 已过期
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['retention_marked'] == 1
    assert result['succeeded'] == 1
    assert result['skipped_agent_unreachable'] == 0
    assert result['failed'] == 0
    # 停容器必须真发生在云端改状态之前——没这一步就是「库里 stopped、容器还在跑」的假状态
    assert ('POST', f'/v1/nodes/{NODE_A}/stop') in agent_reachable.calls

    row = await _reload(db)
    assert row.status == 'stopped'
    assert row.retain_until is not None
    retain_until_value = row.retain_until
    expected = timezone.now() + timedelta(days=RETENTION_DAYS)
    assert abs((row.retain_until - expected).total_seconds()) < 300
    # `stopped` 是正常状态，不该被写成故障
    assert row.failure_reason is None

    stopped = [e for e in await _events(db) if e.event_type == 'stopped']
    assert len(stopped) == 1
    assert stopped[0].detail['reason'] == 'subscription_expired'
    assert stopped[0].detail['retention_days'] == RETENTION_DAYS

    notices = (
        await db.execute(
            select(HasnNotifications).where(
                HasnNotifications.target_id == OWNER, HasnNotifications.type == 'cloud_node_retained'
            )
        )
    ).scalars().all()
    assert len(notices) == 1
    # 主人必须被明确告知数据保留到哪天（比时刻，不比时区书写形式）
    assert datetime.fromisoformat(notices[0].data['retain_until']) == retain_until_value
    assert notices[0].data['retention_days'] == RETENTION_DAYS
    # 正文必须写出具体的保留截止日期（按服务时区），不能只说「保留一段时间」
    assert timezone.from_datetime(retain_until_value).strftime('%Y-%m-%d') in (notices[0].body or '')


async def test_second_sweep_is_a_noop(db: AsyncSession, agent_reachable: _HostingAgentContract) -> None:
    """幂等：同一轮跑两次，第二次不再迁移任何节点，`retain_until` 也不被顺延。"""
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=-1))
    await db.commit()

    first = await run_cloud_node_retention_sweep()
    row = await _reload(db)
    first_retain_until = row.retain_until
    stop_calls = agent_reachable.calls.count(('POST', f'/v1/nodes/{NODE_A}/stop'))

    second = await run_cloud_node_retention_sweep()

    assert first['retention_marked'] == 1
    assert second['retention_marked'] == 0
    assert second['succeeded'] == 0
    assert second['failed'] == 0
    # 第二轮不该再去骚扰宿主
    assert agent_reachable.calls.count(('POST', f'/v1/nodes/{NODE_A}/stop')) == stop_calls
    assert (await _reload(db)).retain_until == first_retain_until
    assert len([e for e in await _events(db) if e.event_type == 'stopped']) == 1


async def test_agent_unreachable_skips_instead_of_faking_stopped(
    db: AsyncSession, agent_unreachable: None
) -> None:
    """宿主不可达 → 本轮跳过并 warn；绝不把库里改成 stopped（容器可能还在跑）。"""
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=-1))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['retention_skipped_agent_unreachable'] == 1
    assert result['skipped_agent_unreachable'] == 1
    assert result['retention_marked'] == 0
    assert result['succeeded'] == 0

    row = await _reload(db)
    assert row.status == 'online'  # 一个字节都没动
    assert row.retain_until is None
    assert await _events(db) == []


async def test_agent_rejecting_stop_is_recorded_as_failure_not_retention(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """宿主明确拒绝停止（500）→ 落 `failed` 事件 + 真实原因码，绝不当成已停止。"""
    agent_reachable.stop_status = 500
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=-1))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['retention_failed'] == 1
    assert result['failed'] == 1
    assert result['retention_marked'] == 0

    row = await _reload(db)
    assert row.status == 'online'
    assert row.retain_until is None
    assert row.failure_reason == 'container_crashed'
    failed = [e for e in await _events(db) if e.event_type == 'failed']
    assert len(failed) == 1
    assert failed[0].detail['stage'] == 'retention_stop'


async def test_notification_failure_does_not_block_retention(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """通知投递失败（这里用主人身份缺失复现真实的 `ImSendRejected`）不得回滚已经发生的停机。

    否则一个投递侧的问题就能让节点永远停不下来、也永远销毁不掉——生命周期动作与「告知」必须解耦。
    """
    await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :o'), {'o': OWNER})
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=-1))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['retention_marked'] == 1
    row = await _reload(db)
    assert row.status == 'stopped'
    assert row.retain_until is not None
    assert [e.event_type for e in await _events(db)] == ['stopped']


# ─── 动作 2：续订恢复 ───


async def test_renewed_subscription_clears_retention_and_starts_container(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    db.add(_cloud_node(status='stopped', retain_until=timezone.now() + timedelta(days=10)))
    db.add(_subscription(days_left=30))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['restored'] == 1
    assert result['restore_failed'] == 0
    assert ('POST', f'/v1/nodes/{NODE_A}/start') in agent_reachable.calls

    row = await _reload(db)
    assert row.retain_until is None
    assert row.status == 'starting'
    assert row.failure_reason is None
    started = [e for e in await _events(db) if e.event_type == 'started']
    assert len(started) == 1
    assert started[0].detail['reason'] == 'subscription_renewed'


async def test_restore_start_failure_is_reported_honestly(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """启动失败必须落 `failed` + 原因码；保留期仍要清掉（已续费的数据不该再挂销毁倒计时）。"""
    agent_reachable.start_status = 500
    db.add(_cloud_node(status='stopped', retain_until=timezone.now() + timedelta(days=10)))
    db.add(_subscription(days_left=30))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['restore_failed'] == 1
    assert result['restored'] == 0
    assert result['failed'] == 1

    row = await _reload(db)
    assert row.status == 'failed'  # 不许假装恢复成功
    assert row.failure_reason == 'container_crashed'
    assert row.failure_detail
    assert row.retain_until is None
    failed = [e for e in await _events(db) if e.event_type == 'failed']
    assert failed and failed[-1].detail['stage'] == 'retention_restore'


# ─── 动作 3：逾期 → 销毁 ───


async def test_overdue_node_is_purged_with_credentials_revoked_first(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """逾期销毁的完整顺序：**凭据先吊销** → 销毁容器与卷 → `hasn_nodes` 退役 → `deleted`。"""
    db.add(_cloud_node(
        status='stopped',
        retain_until=timezone.now() - timedelta(days=1),
        credential_session_uuid=CREDENTIAL_SESSION,
    ))
    db.add(_hasn_node())
    db.add(_subscription(days_left=-40))
    await db.commit()
    # 设备凭据此刻确实在 Redis 里；销毁流程必须先把它删掉
    await redis_client.set(_token_key(), 'device-jwt', ex=600)
    assert await redis_client.exists(_token_key())

    result = await run_cloud_node_retention_sweep()

    assert result['purged'] == 1
    assert result['purge_agent_error'] == 0
    assert result['purge_skipped_agent_unreachable'] == 0
    # 契约服务器在收到 DELETE 的那一刻回查 Redis：凭据必须已经不在了
    assert agent_reachable.credential_present_at_delete is False
    assert ('DELETE', f'/v1/nodes/{NODE_A}') in agent_reachable.calls
    assert agent_reachable.delete_query.get('purge_volume') == 'true'

    row = await _reload(db)
    assert row.status == 'deleted'
    assert row.credential_session_uuid is None
    assert row.retain_until is None
    assert row.container_ref is None

    node = (await db.execute(select(HasnNodes).where(HasnNodes.node_id == NODE_A))).scalar_one()
    assert node.status == 'deleted'

    deleted = [e for e in await _events(db) if e.event_type == 'deleted']
    assert len(deleted) == 1
    assert deleted[0].detail['reason'] == 'retention_expired'
    assert deleted[0].detail['purge_volume'] is True

    # 销毁前必须再通知一次
    notices = (
        await db.execute(
            select(HasnNotifications).where(
                HasnNotifications.target_id == OWNER, HasnNotifications.type == 'cloud_node_purged'
            )
        )
    ).scalars().all()
    assert len(notices) == 1


async def test_overdue_purge_is_skipped_when_agent_unreachable(
    db: AsyncSession, agent_unreachable: None
) -> None:
    """探不通宿主就一个都不动：销毁的第一步（吊销凭据）不可逆，宁可明天再来。"""
    db.add(_cloud_node(
        status='stopped',
        retain_until=timezone.now() - timedelta(days=1),
        credential_session_uuid=CREDENTIAL_SESSION,
    ))
    db.add(_subscription(days_left=-40))
    await db.commit()
    await redis_client.set(_token_key(), 'device-jwt', ex=600)

    result = await run_cloud_node_retention_sweep()

    assert result['purge_skipped_agent_unreachable'] == 1
    assert result['purged'] == 0
    row = await _reload(db)
    assert row.status == 'stopped'
    assert row.credential_session_uuid == CREDENTIAL_SESSION
    # 凭据也不该被提前吊销
    assert await redis_client.exists(_token_key())


async def test_overdue_node_is_not_purged_when_subscription_recovered(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """最后一天续订：恢复必须先跑，销毁动作绝不能删掉刚付费用户的数据卷。"""
    db.add(_cloud_node(
        status='stopped',
        retain_until=timezone.now() - timedelta(hours=1),
        credential_session_uuid=CREDENTIAL_SESSION,
    ))
    db.add(_subscription(days_left=30))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['purged'] == 0
    assert result['restored'] == 1
    row = await _reload(db)
    assert row.status == 'starting'
    assert row.retain_until is None
    assert ('DELETE', f'/v1/nodes/{NODE_A}') not in agent_reachable.calls


# ─── 动作 4：到期前提醒 ───


@pytest.mark.parametrize('days_left', [7, 3, 1])
async def test_expiry_reminder_fires_on_each_threshold(
    db: AsyncSession, agent_reachable: _HostingAgentContract, days_left: int
) -> None:
    """到期前 7/3/1 天各提醒一次，文案必须点明「节点会停 + 数据保留 30 天」。"""
    db.add(_cloud_node(status='online'))
    # 阈值按向上取整算，留半天余量落在整数天上
    db.add(_subscription(days_left=days_left))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['reminded'] == 1
    notice = (
        await db.execute(
            select(HasnNotifications).where(
                HasnNotifications.target_id == OWNER, HasnNotifications.type == 'cloud_node_expiry'
            )
        )
    ).scalars().one()
    assert notice.data['days_left'] == days_left
    assert notice.dedupe_key == f'cloud_node_expiry:{NODE_A}:{days_left}'
    assert '停止' in (notice.body or '')
    assert str(RETENTION_DAYS) in (notice.body or '')


async def test_expiry_reminder_is_not_sent_outside_thresholds(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """不到阈值就不提醒——不漏也不能吵。"""
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=5))
    await db.commit()

    result = await run_cloud_node_retention_sweep()

    assert result['reminded'] == 0
    count = (
        await db.execute(
            select(func.count())
            .select_from(HasnNotifications)
            .where(HasnNotifications.target_id == OWNER, HasnNotifications.type == 'cloud_node_expiry')
        )
    ).scalar()
    assert count == 0


async def test_expiry_reminder_does_not_duplicate_across_runs(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """同一阈值跑两轮只留一行通知（dedupe_key 去重），不重不漏。"""
    db.add(_cloud_node(status='online'))
    db.add(_subscription(days_left=3))
    await db.commit()

    await run_cloud_node_retention_sweep()
    await run_cloud_node_retention_sweep()

    await db.rollback()
    count = (
        await db.execute(
            select(func.count())
            .select_from(HasnNotifications)
            .where(
                HasnNotifications.target_id == OWNER,
                HasnNotifications.dedupe_key == f'cloud_node_expiry:{NODE_A}:3',
            )
        )
    ).scalar()
    assert count == 1


# ─── 账号注销：立即销毁，不留保留期 ───


async def test_account_deletion_purges_immediately_without_retention(
    db: AsyncSession, agent_reachable: _HostingAgentContract
) -> None:
    """设计 §7 末行：账号注销立即走完整删除流程，不落 `retain_until`。"""
    db.add(_cloud_node(status='online', credential_session_uuid=CREDENTIAL_SESSION))
    db.add(_hasn_node())
    await db.commit()
    await redis_client.set(_token_key(), 'device-jwt', ex=600)

    async with async_db_session.begin() as session:
        summary = await cloud_node_service.purge_for_account_deletion(session, user_id=USER_ID)

    assert summary['purged'] == [NODE_A]
    assert summary['failed'] == []
    assert agent_reachable.credential_present_at_delete is False

    row = await _reload(db)
    assert row.status == 'deleted'
    assert row.retain_until is None  # 不留保留期
    assert row.credential_session_uuid is None
    deleted = [e for e in await _events(db) if e.event_type == 'deleted']
    assert deleted and deleted[0].detail['reason'] == 'account_deletion'
