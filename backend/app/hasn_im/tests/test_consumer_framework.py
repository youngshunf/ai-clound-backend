"""R2-05 验收：消费者框架 durable/best-effort 分道语义（真实 PG·零 mock·doc16 §7.2）。

钉死 §12.1 消费者停机/重启/重放/死信矩阵：

1. **durable happy path**：顺序处理全批、cursor 推进到末位。
2. **durable 停机/重启接续**：新 runner 实例从落库 cursor 续拉，不重不漏。
3. **durable 失败→退避 park→自愈**：失败即停在失败事件前（顺序不越过）；退避到期后成功则推进。
4. **durable dead letter→park→skip 放行**：达 max_attempts 进 dead letter、park；resolve=skipped
   后越过该事件继续；retention 低水位停在 park 处。
5. **best-effort 已尝试即推进**：投递失败**不**进失败表、不阻塞，cursor 照常推进；不参与 retention。
6. **lease 单实例**：先占租约的实例持有，另一实例 tick 让路（lease_held=False）。

PG 不可达跳过；每用例 uuid 派生独立 aggregate + consumer_name，末尾清理自身行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_im.application import event_appender as appender
from backend.app.hasn_im.consumers import store
from backend.app.hasn_im.consumers.base import ConsumerClass, IntegrationEvent
from backend.app.hasn_im.consumers.framework import ConsumerRunner
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_EVENTS = 'public.hasn_im_integration_events'
_OFFSETS = 'public.hasn_im_event_consumer_offsets'
_FAILURES = 'public.hasn_im_event_consumer_failures'


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 R2-05 消费者框架测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# ---------- 测试用消费者 ----------


class _RecordingConsumer:
    """记录处理过的 event_seq，可配置在某个 seq 上失败（模拟故障）。"""

    def __init__(self, name: str, consumer_class: ConsumerClass, *, fail_on: set[int] | None = None):
        self._name = name
        self._class = consumer_class
        self._fail_on = fail_on or set()
        self.handled: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def consumer_class(self) -> ConsumerClass:
        return self._class

    async def handle(self, event: IntegrationEvent, db) -> None:  # noqa: ANN001
        if event.event_seq in self._fail_on:
            raise RuntimeError(f'注入失败@{event.event_seq}')
        self.handled.append(event.event_seq)


async def _seed_events(sm, aggregate_id: str, n: int) -> None:
    async with sm() as db:
        for i in range(n):
            await appender.append_event(
                db,
                event_type='im.message.committed.v1',
                aggregate_type='conversation',
                aggregate_id=aggregate_id,
                payload={'i': i},
            )
        await db.commit()


async def _min_seq_for(sm, aggregate_id: str) -> int:
    """本 aggregate 的最小 event_seq（并发下全局 seq 有偏移，测试按相对区间断言）。"""
    async with sm() as db:
        v = (
            await db.execute(
                sa.text(f'SELECT MIN(event_seq) FROM {_EVENTS} WHERE aggregate_id = :aid'),  # noqa: S608
                {'aid': aggregate_id},
            )
        ).scalar_one()
    return int(v)


async def _cleanup(sm, aggregate_id: str, *consumer_names: str) -> None:
    async with sm() as db:
        await db.execute(
            sa.text(f'DELETE FROM {_EVENTS} WHERE aggregate_id = :aid'),  # noqa: S608
            {'aid': aggregate_id},
        )
        for cn in consumer_names:
            await db.execute(
                sa.text(f'DELETE FROM {_FAILURES} WHERE consumer_name = :cn'),  # noqa: S608
                {'cn': cn},
            )
            await db.execute(
                sa.text(f'DELETE FROM {_OFFSETS} WHERE consumer_name = :cn'),  # noqa: S608
                {'cn': cn},
            )
        await db.commit()


# ---------- 1) durable happy path ----------


async def test_durable_processes_batch_in_order(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'sync_projector_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 3)
    base = await _min_seq_for(sessionmaker_pg, aid)

    consumer = _RecordingConsumer(cn, ConsumerClass.DURABLE)
    runner = ConsumerRunner(consumer=consumer, sessionmaker=sessionmaker_pg, instance_id='w1')
    stats = await runner.tick(batch_limit=100)

    assert stats.lease_held and stats.processed == stats.fetched >= 3
    # 本 aggregate 的三条被顺序处理
    assert consumer.handled[-3:] == [base, base + 1, base + 2]
    async with sessionmaker_pg() as db:
        cursor = await store.get_cursor(db, cn)
    assert cursor >= base + 2
    await _cleanup(sessionmaker_pg, aid, cn)


# ---------- 2) 停机/重启接续 ----------


async def test_durable_resumes_from_cursor_after_restart(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'sync_projector_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 2)
    base = await _min_seq_for(sessionmaker_pg, aid)

    c1 = _RecordingConsumer(cn, ConsumerClass.DURABLE)
    await ConsumerRunner(consumer=c1, sessionmaker=sessionmaker_pg, instance_id='w1').tick()

    # 追加两条后「同一 worker 重启」——同实例名重占自己的租约，从落库 cursor 续拉只处理新增。
    # （跨实例接替须等旧租约过期，由 test_lease_single_instance 单独覆盖。）
    await _seed_events(sessionmaker_pg, aid, 2)  # 再 2 条（更大 seq）
    c2 = _RecordingConsumer(cn, ConsumerClass.DURABLE)
    await ConsumerRunner(consumer=c2, sessionmaker=sessionmaker_pg, instance_id='w1').tick()

    # 重启后的实例只处理了 cursor 之后的事件，无重复
    assert base not in c2.handled and (base + 1) not in c2.handled
    async with sessionmaker_pg() as db:
        cursor = await store.get_cursor(db, cn)
    assert cursor >= base + 3
    await _cleanup(sessionmaker_pg, aid, cn)


# ---------- 3) 失败→退避 park→自愈 ----------


async def test_durable_parks_on_failure_then_recovers(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'sync_projector_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 3)
    base = await _min_seq_for(sessionmaker_pg, aid)

    # 在中间那条失败；backoff=0 便于同测内立即重试
    consumer = _RecordingConsumer(cn, ConsumerClass.DURABLE, fail_on={base + 1})
    runner = ConsumerRunner(
        consumer=consumer, sessionmaker=sessionmaker_pg, instance_id='w1', backoff_schedule=(0,)
    )
    stats = await runner.tick()
    # 第一条成功，第二条失败 → park；cursor 停在 base
    assert base in consumer.handled and (base + 1) not in consumer.handled
    assert stats.retried == 1 and stats.parked
    async with sessionmaker_pg() as db:
        assert await store.get_cursor(db, cn) == base

    # 修好故障（消费者不再失败）+ backoff 已到 → 下一 tick 推进过失败点直到末位
    consumer._fail_on = set()  # 模拟底层问题已修复
    await runner.tick()
    assert (base + 1) in consumer.handled and (base + 2) in consumer.handled
    async with sessionmaker_pg() as db:
        assert await store.get_cursor(db, cn) >= base + 2
    await _cleanup(sessionmaker_pg, aid, cn)


# ---------- 4) dead letter→park→skip 放行 + retention 低水位 ----------


async def test_durable_dead_letter_parks_and_skip_releases(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'sync_projector_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 3)
    base = await _min_seq_for(sessionmaker_pg, aid)

    # 永远在 base+1 失败；max_attempts=2、backoff=0 → 两次尝试后 dead letter
    consumer = _RecordingConsumer(cn, ConsumerClass.DURABLE, fail_on={base + 1})
    runner = ConsumerRunner(
        consumer=consumer, sessionmaker=sessionmaker_pg, instance_id='w1',
        max_attempts=2, backoff_schedule=(0,),
    )
    # tick 1：base 成功，base+1 第 1 次失败 → retry
    await runner.tick()
    # tick 2：base+1 第 2 次失败 → dead letter + park
    s2 = await runner.tick()
    assert s2.dead_lettered == 1 and s2.parked
    async with sessionmaker_pg() as db:
        fs = await store.get_failure(db, consumer_name=cn, event_seq=base + 1)
        assert fs is not None and fs.dead_lettered and fs.resolution is None
        # retention 低水位停在 dead letter 之前（durable 参与）
        low = await store.retention_low_water(db, durable_consumers=[cn])
    assert low == base

    # tick 3：仍 park（未 resolve）
    s3 = await runner.tick()
    assert s3.parked and s3.processed == 0

    # 确认跳过 → 越过失败事件继续处理 base+2
    async with sessionmaker_pg() as db:
        await store.resolve_dead_letter(db, consumer_name=cn, event_seq=base + 1, resolution='skipped')
        await db.commit()
    s4 = await runner.tick()
    assert s4.skipped == 1
    assert (base + 2) in consumer.handled and (base + 1) not in consumer.handled
    async with sessionmaker_pg() as db:
        assert await store.get_cursor(db, cn) >= base + 2
    await _cleanup(sessionmaker_pg, aid, cn)


# ---------- 5) best-effort：已尝试即推进、不进失败表、不参与 retention ----------


async def test_best_effort_advances_on_failure_no_dlq(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'realtime_notifier_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 3)
    base = await _min_seq_for(sessionmaker_pg, aid)

    # 中间那条投递失败，但 best-effort 仍推进
    consumer = _RecordingConsumer(cn, ConsumerClass.BEST_EFFORT, fail_on={base + 1})
    runner = ConsumerRunner(consumer=consumer, sessionmaker=sessionmaker_pg, instance_id='w1')
    stats = await runner.tick()

    assert stats.best_effort_failed >= 1 and not stats.parked
    # cursor 越过失败事件到末位（成败均推进）
    async with sessionmaker_pg() as db:
        assert await store.get_cursor(db, cn) >= base + 2
        # best-effort 不写失败表
        fcount = (
            await db.execute(
                sa.text(f'SELECT count(*) FROM {_FAILURES} WHERE consumer_name = :cn'),  # noqa: S608
                {'cn': cn},
            )
        ).scalar_one()
    assert fcount == 0
    # base 与 base+2 成功投递，base+1 失败被跳过
    assert base in consumer.handled and (base + 2) in consumer.handled
    await _cleanup(sessionmaker_pg, aid, cn)


# ---------- 6) lease 单实例 ----------


async def test_lease_single_instance(sessionmaker_pg) -> None:
    aid = f'conv_{uuid.uuid4().hex[:12]}'
    cn = f'sync_projector_{uuid.uuid4().hex[:8]}'
    await _seed_events(sessionmaker_pg, aid, 2)

    # w1 先占租约（TTL 长），w2 抢不到 → 让路
    async with sessionmaker_pg() as db:
        held1 = await store.try_acquire_lease(db, consumer_name=cn, owner='w1', ttl_seconds=300)
        await db.commit()
    assert held1

    c2 = _RecordingConsumer(cn, ConsumerClass.DURABLE)
    s2 = await ConsumerRunner(
        consumer=c2, sessionmaker=sessionmaker_pg, instance_id='w2'
    ).tick()
    assert not s2.lease_held and c2.handled == []
    await _cleanup(sessionmaker_pg, aid, cn)
