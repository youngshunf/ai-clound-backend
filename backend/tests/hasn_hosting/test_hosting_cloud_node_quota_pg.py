"""云端节点订阅门与配额门（H4 · 契约 §3.1）——零 mock，打真实 PostgreSQL。

覆盖：无有效订阅拒 `cloud_node_subscription_required`；已达上限拒 `cloud_node_quota_exceeded`；
配额取值优先级（合同快照 → 配置默认）；以及「已删除节点不再占配额」。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn_hosting.constants import ERR_QUOTA_EXCEEDED, ERR_SUBSCRIPTION_REQUIRED
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.exception import errors
from backend.utils.timezone import timezone
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

OWNER = 'h_hosting_quota_owner'
USER_ID = 990303
NODE_PREFIX = 'n_cloud_test_quota_'


async def _purge(sess) -> None:
    await sess.execute(text('DELETE FROM hasn_cloud_node_events WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await sess.execute(text('DELETE FROM hasn_cloud_nodes WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await sess.execute(text('DELETE FROM hasn_billing.user_subscription WHERE user_id = :u'), {'u': USER_ID})
    await sess.commit()


def _cloud_node(node_id: str, *, status: str = 'online') -> HasnCloudNodes:
    return HasnCloudNodes(
        node_id=node_id,
        user_id=USER_ID,
        owner_hasn_id=OWNER,
        host='hosting-test',
        container_ref=None,
        status=status,
        failure_reason=None,
        failure_detail=None,
        image_version='0.0.1',
        image_digest='sha256:' + '1' * 64,
        credential_session_uuid=None,
        retain_until=None,
        last_backup_at=None,
        online_since=None,
    )


def _subscription(*, plan_snapshot: dict | None = None) -> UserSubscription:
    now = timezone.now()
    return UserSubscription(
        app_code='huanxing',
        user_id=USER_ID,
        tier='pro',
        subscription_type='monthly',
        monthly_credits=Decimal('0'),
        current_credits=Decimal('0'),
        used_credits=Decimal('0'),
        purchased_credits=Decimal('0'),
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        subscription_start_date=now,
        subscription_end_date=now + timedelta(days=30),
        next_grant_date=None,
        status='active',
        auto_renew=True,
        plan_snapshot=plan_snapshot,
    )


@pytest_asyncio.fixture
async def sess() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(s)
        yield s
    finally:
        await _purge(s)
        await s.rollback()
        await s.close()
        await engine.dispose()
        await async_engine.dispose()


async def test_create_without_subscription_is_rejected(sess) -> None:
    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert exc.value.data == {'error': ERR_SUBSCRIPTION_REQUIRED}


async def test_expired_subscription_is_rejected(sess) -> None:
    sub = _subscription()
    sub.subscription_end_date = timezone.now() - timedelta(days=1)
    sess.add(sub)
    await sess.commit()

    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert exc.value.data == {'error': ERR_SUBSCRIPTION_REQUIRED}


async def test_default_quota_is_one_node_per_subscription(sess) -> None:
    """默认每订阅 1 个：第一个放行，第二个必须落 cloud_node_quota_exceeded。"""
    sess.add(_subscription())
    await sess.commit()

    # 尚无节点 → 放行（不抛即通过）
    await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)

    sess.add(_cloud_node(f'{NODE_PREFIX}0001'))
    await sess.commit()

    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert exc.value.data is not None
    assert exc.value.data['error'] == ERR_QUOTA_EXCEEDED
    assert exc.value.data['used'] == 1
    assert exc.value.data['quota'] == 1


async def test_plan_snapshot_raises_quota(sess) -> None:
    """合同快照写了 max_cloud_nodes 就按它算，不回落配置默认值。"""
    sess.add(_subscription(plan_snapshot={'max_cloud_nodes': 3}))
    sess.add(_cloud_node(f'{NODE_PREFIX}0001'))
    sess.add(_cloud_node(f'{NODE_PREFIX}0002'))
    await sess.commit()

    # 2 < 3 → 放行
    await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)

    sess.add(_cloud_node(f'{NODE_PREFIX}0003'))
    await sess.commit()
    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)
    assert exc.value.data is not None and exc.value.data['quota'] == 3


async def test_deleted_node_does_not_occupy_quota(sess) -> None:
    """已删除节点不再占名额，否则删了也建不回来。"""
    sess.add(_subscription())
    sess.add(_cloud_node(f'{NODE_PREFIX}0001', status='deleted'))
    await sess.commit()

    await cloud_node_service._assert_can_create(sess, user_id=USER_ID, owner_hasn_id=OWNER)


async def test_list_hides_deleted_nodes(sess) -> None:
    sess.add(_cloud_node(f'{NODE_PREFIX}0001', status='deleted'))
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='stopped'))
    await sess.commit()

    views = await cloud_node_service.list_nodes(sess, owner_hasn_id=OWNER)
    node_ids = {v['node_id'] for v in views}
    assert node_ids == {f'{NODE_PREFIX}0002'}


async def test_view_never_leaks_container_ref_or_credential_session(sess) -> None:
    """契约 §3.1.1：运维内部信息禁止透出给主人面。"""
    node = _cloud_node(f'{NODE_PREFIX}0002', status='stopped')
    node.container_ref = 'container-abc'
    node.credential_session_uuid = 'session-uuid-abc'
    sess.add(node)
    await sess.commit()

    views = await cloud_node_service.list_nodes(sess, owner_hasn_id=OWNER)
    assert len(views) == 1
    view = views[0]
    assert 'container_ref' not in view
    assert 'credential_session_uuid' not in view
    # 契约 §3.1.1 要求的字段一个都不能少
    for key in (
        'node_id', 'node_name', 'status', 'failure_reason', 'failure_detail', 'host',
        'image_version', 'image_digest', 'online_since', 'last_backup_at', 'retain_until',
        'created_time', 'updated_time', 'update_available', 'latest_image_version', 'backup_configured',
    ):
        assert key in view, key


async def test_offline_row_marked_online_is_reported_as_daemon_not_online(sess) -> None:
    """库里 online 但 Redis presence 没命中 → 必须如实报 failed(daemon_not_online)，不许报在线。"""
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='online'))
    await sess.commit()

    views = await cloud_node_service.list_nodes(sess, owner_hasn_id=OWNER)
    assert len(views) == 1
    assert views[0]['presence_online'] is False
    assert views[0]['status'] == 'failed'
    assert views[0]['failure_reason'] == 'daemon_not_online'


async def test_report_status_rejects_online_from_agent(sess) -> None:
    """hosting-agent 永不上报 online（契约 §4.5①）：在线判定归云端 presence，收到即拒。"""
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='starting'))
    await sess.commit()

    with pytest.raises(errors.RequestError):
        await cloud_node_service.report_status(sess, node_id=f'{NODE_PREFIX}0002', status='online')


async def test_report_status_persists_agent_host(sess) -> None:
    """agent 主动带自己的 HOST_ID，云端必须落 hasn_cloud_nodes.host（多宿主调度依据）。"""
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='provisioning'))
    await sess.commit()

    await cloud_node_service.report_status(
        sess, node_id=f'{NODE_PREFIX}0002', status='starting', host='hosting-07', container_ref='c-7'
    )
    await sess.commit()

    row = (
        await sess.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == f'{NODE_PREFIX}0002'))
    ).scalar_one()
    await sess.refresh(row)
    assert row.host == 'hosting-07'
    assert row.status == 'starting'
    assert row.container_ref == 'c-7'


async def test_append_event_rejects_unknown_event_type(sess) -> None:
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='starting'))
    await sess.commit()

    with pytest.raises(errors.RequestError):
        await cloud_node_service.append_event(
            sess, node_id=f'{NODE_PREFIX}0002', event_type='not_a_real_event', detail={}
        )

    # 合法事件照常落库
    result = await cloud_node_service.append_event(
        sess, node_id=f'{NODE_PREFIX}0002', event_type='backup', detail={'path': '/x.tar.zst'}
    )
    await sess.commit()
    assert result == {'ok': True}


async def test_pending_updates_reports_no_image_honestly(sess) -> None:
    """没有已发布 headless 镜像时诚实返回 image_available=false，不臆造更新目标。"""
    data = await cloud_node_service.pending_updates(sess)
    assert set(data) >= {'image_available', 'nodes'}
    if not data['image_available']:
        assert data['nodes'] == []
    else:
        # 有镜像时每个条目必须自带 node_id + image_ref（agent 会跳过缺这两项的条目）
        for item in data['nodes']:
            assert item['node_id']
            assert item['image_ref']
            assert item['image_digest']
            assert 'image_version' in item


async def test_report_status_rejects_unknown_failure_reason(sess) -> None:
    """failure_reason 是契约 §2.4 的固定九项，前端锁死了枚举，非法值必须拒。"""
    sess.add(_cloud_node(f'{NODE_PREFIX}0002', status='starting'))
    await sess.commit()

    with pytest.raises(errors.RequestError):
        await cloud_node_service.report_status(
            sess, node_id=f'{NODE_PREFIX}0002', status='failed', failure_reason='made_up_reason'
        )
