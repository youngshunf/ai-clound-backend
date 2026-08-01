"""S9 商机、阶段、成交/流失与复盘任务真实 PostgreSQL 验收。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.service.opportunity_flow_service import (
    growth_opportunity_service,
)
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_MIGRATION = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-opportunity-version-review-task.sql'


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    raw = await (await session.connection()).get_raw_connection()
    driver_connection = raw.driver_connection
    assert driver_connection is not None
    await driver_connection.execute(_MIGRATION.read_text(encoding='utf-8'))

    tag = uuid.uuid4().hex[:10]
    user_id = 97_300_000_000 + int(uuid.uuid4().int % 500_000_000)
    owner_hasn_id = f'h_growth_s9_{tag}'
    agent_hasn_id = f'a_growth_s9_{tag}'
    platform_project = HasnProject(
        owner_id=owner_hasn_id,
        name=f'S9 商机项目 {tag}',
        status='active',
        bound_agent_id=agent_hasn_id,
    )
    session.add_all((
        HasnHumans(
            hasn_id=owner_hasn_id,
            star_id=f's_{user_id}',
            user_id=user_id,
            nickname=f'S9 主人 {tag}',
            status='active',
        ),
        platform_project,
    ))
    await session.flush()
    growth_project = GrowthProject(
        platform_project_id=platform_project.id,
        user_id=user_id,
        owner_hasn_id=owner_hasn_id,
        owner_scope='personal',
        name=f'S9 获客漏斗 {tag}',
        owner_agent_id=agent_hasn_id,
        status='active',
        provision_status='ready',
        profile_version=7,
    )
    session.add(growth_project)
    await session.flush()
    customer = Customer(
        customer_no=f'C-S9-{tag}',
        user_id=user_id,
        growth_project_id=growth_project.id,
        source_kind='controlled_import',
        company_name=f'S9 受控客户 {tag}',
        lifecycle_status='active',
        owner_agent_id=agent_hasn_id,
        owner_scope='personal',
    )
    session.add(customer)
    await session.flush()
    followup_task_id = str(uuid.uuid4())
    session.add(
        HasnTask(
            owner_id=owner_hasn_id,
            agent_id=agent_hasn_id,
            name='S9 既有跟进任务',
            prompt='继续跟进当前客户',
            schedule_type='once',
            schedule_config={'run_at': timezone.now().isoformat()},
            state='scheduled',
            task_uuid=followup_task_id,
            project_id=platform_project.id,
            app_id='growth',
            execution_kind='freeform',
            execution_spec={'prompt': '继续跟进当前客户'},
        )
    )
    customer.followup_task_id = followup_task_id
    await session.flush()

    try:
        yield SimpleNamespace(
            session=session,
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            platform_project=platform_project,
            growth_project=growth_project,
            customer=customer,
            followup_task_id=followup_task_id,
            scope=GrowthScope(
                user_id=user_id,
                owner_hasn_id=owner_hasn_id,
            ),
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _create(
    ctx: SimpleNamespace,
    *,
    customer_id: int | None = None,
    idempotency_key: str = 's9-create-1',
) -> dict:
    return await growth_opportunity_service.create_opportunity(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        customer_id=customer_id or ctx.customer.id,
        name='年度增长服务',
        amount=150_000,
        currency='CNY',
        stage='contacted',
        probability=0.65,
        idempotency_key=idempotency_key,
        created_by_kind='agent',
        actor_id=ctx.agent_hasn_id,
        scope=ctx.scope,
    )


async def test_create_and_stage_retry_are_idempotent_and_stale_version_conflicts(
    ctx: SimpleNamespace,
) -> None:
    created = await _create(ctx)
    replay = await _create(ctx)
    assert replay['id'] == created['id']
    assert created['version'] == 1
    assert created['resource_uri'] == f'hasn://growth/opportunities/{created["id"]}'
    assert (
        await ctx.session.scalar(
            sa
            .select(sa.func.count())
            .select_from(Opportunity)
            .where(
                Opportunity.growth_project_id == ctx.growth_project.id,
                Opportunity.customer_id == ctx.customer.id,
            )
        )
        == 1
    )

    moved = await growth_opportunity_service.update_stage(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=created['id'],
        stage='proposal',
        note='客户已确认查看正式方案',
        expected_version=1,
        idempotency_key='s9-stage-1',
        actor_kind='agent',
        actor_id=ctx.agent_hasn_id,
        scope=ctx.scope,
    )
    moved_replay = await growth_opportunity_service.update_stage(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=created['id'],
        stage='proposal',
        note='客户已确认查看正式方案',
        expected_version=1,
        idempotency_key='s9-stage-1',
        actor_kind='agent',
        actor_id=ctx.agent_hasn_id,
        scope=ctx.scope,
    )
    assert moved['version'] == moved_replay['version'] == 2
    with pytest.raises(errors.ConflictError):
        await growth_opportunity_service.update_stage(
            ctx.session,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth_project.id,
            opportunity_id=created['id'],
            stage='negotiation',
            note='陈旧页面尝试推进',
            expected_version=1,
            idempotency_key='s9-stage-stale',
            scope=ctx.scope,
        )
    assert (
        await ctx.session.scalar(
            sa
            .select(sa.func.count())
            .select_from(Activity)
            .where(
                Activity.growth_project_id == ctx.growth_project.id,
                Activity.opportunity_id == created['id'],
                Activity.kind == 'stage_change',
            )
        )
        == 2
    )


async def test_won_close_requires_facts_and_creates_one_review_chain(
    ctx: SimpleNamespace,
) -> None:
    created = await _create(ctx, idempotency_key='s9-create-won')
    with pytest.raises(errors.RequestError):
        await growth_opportunity_service.close_deal(
            ctx.session,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth_project.id,
            opportunity_id=created['id'],
            result='won',
            expected_version=1,
            idempotency_key='s9-close-missing',
            scope=ctx.scope,
        )

    closed = await growth_opportunity_service.close_deal(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=created['id'],
        result='won',
        amount=128_000,
        currency='CNY',
        close_note='年度合同已确认',
        expected_version=1,
        idempotency_key='s9-close-won',
        actor_kind='owner',
        actor_id=str(ctx.user_id),
        scope=ctx.scope,
    )
    replay = await growth_opportunity_service.close_deal(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=created['id'],
        result='won',
        amount=128_000,
        currency='CNY',
        close_note='年度合同已确认',
        expected_version=1,
        idempotency_key='s9-close-won',
        actor_kind='owner',
        actor_id=str(ctx.user_id),
        scope=ctx.scope,
    )
    assert closed['stage'] == replay['stage'] == 'closed_won'
    assert closed['version'] == replay['version'] == 2
    assert closed['amount'] == 128_000
    assert closed['review_task_id']
    assert ctx.customer.lifecycle_status == 'won'
    assert ctx.growth_project.profile_version == 7

    review_tasks = list(
        (
            await ctx.session.execute(
                sa.select(HasnTask).where(
                    HasnTask.task_uuid == closed['review_task_id'],
                    HasnTask.project_id == ctx.platform_project.id,
                    HasnTask.app_id == 'growth',
                )
            )
        ).scalars()
    )
    assert len(review_tasks) == 1
    assert '不得自动修改' in review_tasks[0].prompt
    followup = (
        await ctx.session.execute(
            sa.select(HasnTask).where(
                HasnTask.task_uuid == ctx.followup_task_id,
            )
        )
    ).scalar_one()
    assert followup.state == 'completed'
    assert followup.enabled is False
    assert (
        await ctx.session.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthAttributionEvent)
            .where(
                GrowthAttributionEvent.opportunity_id == created['id'],
                GrowthAttributionEvent.event_type == 'closed_won',
            )
        )
        == 1
    )

    detail = await growth_opportunity_service.get_opportunity_detail(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=created['id'],
        scope=ctx.scope,
    )
    assert detail['opportunity']['stage'] == 'closed_won'
    assert detail['customer']['id'] == ctx.customer.id
    assert any(task['task_uuid'] == closed['review_task_id'] for task in detail['tasks'])
    assert any(event['event_type'] == 'closed_won' for event in detail['attribution'])


async def test_lost_reason_and_parallel_open_opportunity_keep_customer_consistent(
    ctx: SimpleNamespace,
) -> None:
    first = await _create(ctx, idempotency_key='s9-create-lost-1')
    second = await _create(ctx, idempotency_key='s9-create-lost-2')
    with pytest.raises(errors.RequestError):
        await growth_opportunity_service.close_deal(
            ctx.session,
            user_id=ctx.user_id,
            growth_project_id=ctx.growth_project.id,
            opportunity_id=first['id'],
            result='lost',
            expected_version=1,
            idempotency_key='s9-close-lost-missing',
            scope=ctx.scope,
        )
    first_closed = await growth_opportunity_service.close_deal(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=first['id'],
        result='lost',
        lost_reason='budget_frozen',
        close_note='客户本财年冻结新增预算',
        expected_version=1,
        idempotency_key='s9-close-lost-1',
        scope=ctx.scope,
    )
    assert first_closed['lost_reason'] == 'budget_frozen'
    assert ctx.customer.lifecycle_status == 'opportunity'

    await growth_opportunity_service.close_deal(
        ctx.session,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth_project.id,
        opportunity_id=second['id'],
        result='lost',
        lost_reason='no_decision_timeline',
        expected_version=1,
        idempotency_key='s9-close-lost-2',
        scope=ctx.scope,
    )
    assert ctx.customer.lifecycle_status == 'lost'

    reopened = await _create(ctx, idempotency_key='s9-create-after-lost')
    assert reopened['stage'] == 'contacted'
    assert ctx.customer.lifecycle_status == 'opportunity'
