"""S11 经营复盘、建议审阅与项目策略的真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.growth_attribution_event import GrowthAttributionEvent
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_playbook import GrowthProjectPlaybook
from backend.app.hasn_growth.model.growth_review_suggestion import GrowthReviewSuggestion
from backend.app.hasn_growth.model.playbook import Playbook
from backend.app.hasn_growth.service.growth_project_app_service import (
    growth_project_app_service,
)
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_growth.service.review_service import growth_review_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_S11_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-29-growth-review-v8.sql'


async def _apply_s11_sql(db: AsyncSession) -> None:
    raw = await (await db.connection()).get_raw_connection()
    driver_connection = raw.driver_connection
    assert driver_connection is not None
    await driver_connection.execute(_S11_SQL.read_text(encoding='utf-8'))


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
    await _apply_s11_sql(session)
    tag = uuid.uuid4().hex[:10]
    owner = f'h_growth_review_{tag}'
    user_id = 95_700_000_000 + int(uuid.uuid4().int % 200_000_000)
    platform = HasnProject(owner_id=owner, name=f'经营复盘项目 {tag}', status='active')
    session.add(platform)
    await session.flush()
    growth = GrowthProject(
        platform_project_id=platform.id,
        user_id=user_id,
        owner_hasn_id=owner,
        owner_scope='personal',
        owner_agent_id=f'a_growth_review_{tag}',
        name=f'经营复盘漏斗 {tag}',
        product_profile={'offering': '企业知识助手', 'value_propositions': ['减少重复答疑']},
        icp_profile={
            'industries': ['软件'],
            'buyer_roles': ['销售负责人'],
            'pain_points': ['线索转化低'],
            'exclusions': ['个人消费者'],
        },
        profile_version=1,
        status='active',
        provision_status='ready',
        monthly_budget=Decimal('100.00'),
        budget_currency='CNY',
        quiet_hours_start=21,
        quiet_hours_end=9,
        daily_outreach_limit=20,
        policy_version=1,
    )
    playbook = Playbook(
        user_id=None,
        name=f'顾问式销售 {tag}',
        version=1,
        enabled=True,
        goal='取得有效回复',
        target_profile={'buyer_roles': ['销售负责人']},
        cadence=cast('Any', [{'day': 1, 'channel': 'email', 'goal': '建立联系'}]),
        tone_guide='具体、克制',
        exit_rule={'max_silent_rounds': 2, 'action': 'stop'},
        is_builtin=True,
        owner_scope='personal',
    )
    session.add_all((growth, playbook))
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            owner=owner,
            user_id=user_id,
            growth=growth,
            playbook=playbook,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_review_schedule_is_idempotent_and_project_lifecycle_stops_it(
    ctx: SimpleNamespace,
) -> None:
    """周期复盘由 Owner 显式启用，使用稳定任务键；暂停后不自动恢复。"""
    first = await growth_review_service.set_review_schedule(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        enabled=True,
    )
    replay = await growth_review_service.set_review_schedule(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        enabled=True,
    )
    assert replay['task_uuid'] == first['task_uuid']
    assert replay['enabled'] is True
    task = await ctx.session.scalar(sa.select(HasnTask).where(HasnTask.task_uuid == first['task_uuid']))
    assert task is not None
    assert task.schedule_type == 'cron'
    assert task.schedule_config == {'expr': '0 9 * * 1'}
    assert task.timezone == 'Asia/Shanghai'
    assert task.next_run_at is not None
    next_run_local = task.next_run_at.astimezone(ZoneInfo(task.timezone))
    assert next_run_local.weekday() == 0
    assert (next_run_local.hour, next_run_local.minute) == (9, 0)
    assert task.execution_spec['kind'] == 'growth_cycle_review'
    assert task.execution_spec['idempotency_scope'] == 'growth_project_cycle'
    assert task.execution_spec['cancel_when'] == [
        'project_paused',
        'project_archived',
        'entitlement_unavailable',
    ]
    assert (
        await ctx.session.scalar(
            sa.select(sa.func.count()).select_from(HasnTask).where(HasnTask.task_uuid == first['task_uuid'])
        )
    ) == 1

    paused_project = await growth_project_app_service.pause(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert paused_project['status'] == 'paused'
    await ctx.session.refresh(task)
    assert task.enabled is False
    assert task.state == 'paused'
    assert task.next_run_at is None
    schedule = await growth_review_service.get_review_schedule(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert schedule['enabled'] is False

    with pytest.raises(errors.ConflictError) as paused_error:
        await growth_review_service.set_review_schedule(
            ctx.session,
            owner_hasn_id=ctx.owner,
            growth_project_id=ctx.growth.id,
            enabled=True,
        )
    assert paused_error.value.data['error_code'] == 'GROWTH_PROJECT_INACTIVE'


async def test_paused_project_rejects_new_agent_review_suggestion(
    ctx: SimpleNamespace,
) -> None:
    """已暂停或归档项目即使遇到在途任务，也不能继续写入下一周期建议。"""
    ctx.growth.status = 'paused'
    await ctx.session.flush()
    with pytest.raises(errors.ConflictError) as paused_error:
        await growth_review_service.create_suggestion(
            ctx.session,
            owner_hasn_id=ctx.owner,
            growth_project_id=ctx.growth.id,
            suggestion_kind='channel',
            proposal={
                'quiet_hours_start': 22,
                'quiet_hours_end': 8,
                'daily_outreach_limit': 12,
            },
            evidence={
                'scope': 'current_month',
                'event_count': 3,
                'insufficient_data': True,
                'limitations': ['样本量不足'],
            },
            proposed_by_kind='agent',
            proposed_by_id=ctx.growth.owner_agent_id,
            idempotency_key='review:paused:2026-07',
        )
    assert paused_error.value.data['error_code'] == 'GROWTH_PROJECT_INACTIVE'


async def test_performance_report_traces_source_playbook_touchpoints_and_win_loss(
    ctx: SimpleNamespace,
) -> None:
    now = datetime.now(UTC)
    adoption = await playbook_service.adopt_for_project(
        ctx.session,
        owner_hasn_id=ctx.owner,
        user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        playbook_id=ctx.playbook.id,
        expected_playbook_version=1,
        configuration={},
    )
    ctx.session.add_all((
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='inbound',
            source_kind='inbound_form',
            source_ref='hasn://publish/sites/9001',
            campaign_ref='campaign-hmac',
            occurred_time=now,
            idempotency_key='form:first:1',
            meta_data={'form_submission_id': 7001, 'touch_model': 'first_touch'},
        ),
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='outreach_sent',
            growth_project_playbook_id=adoption['id'],
            playbook_id=ctx.playbook.id,
            playbook_version=1,
            source_kind='email',
            source_ref='outreach:8101',
            occurred_time=now,
            idempotency_key='outreach:8101:sent',
            meta_data={'outreach_message_id': 8101, 'channel': 'email'},
        ),
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='replied',
            growth_project_playbook_id=adoption['id'],
            playbook_id=ctx.playbook.id,
            playbook_version=1,
            source_kind='email',
            source_ref='outreach:8102',
            occurred_time=now,
            idempotency_key='outreach:8102:replied',
            meta_data={'outreach_message_id': 8102, 'channel': 'email'},
        ),
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='closed_won',
            growth_project_playbook_id=adoption['id'],
            playbook_id=ctx.playbook.id,
            playbook_version=1,
            source_kind='inbound_form',
            source_ref='hasn://publish/sites/9001',
            amount=Decimal('1200.00'),
            currency='CNY',
            occurred_time=now,
            idempotency_key='deal:1:won',
            meta_data={'result': 'won'},
        ),
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='closed_lost',
            source_kind='research',
            source_ref='research:batch:3',
            occurred_time=now,
            idempotency_key='deal:2:lost',
            meta_data={'result': 'lost', 'lost_reason': '预算冻结'},
        ),
    ))
    await ctx.session.flush()

    report = await growth_report_service.project_overview(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )

    assert report['performance']['event_count'] == 5
    assert report['performance']['touchpoints'] == {
        'forms': 1,
        'sites': 1,
        'outreach_sent': 1,
        'replies': 1,
    }
    assert report['performance']['sources'][0]['source_kind'] == 'inbound_form'
    assert report['performance']['playbooks'][0]['playbook_id'] == ctx.playbook.id
    assert report['performance']['win_loss']['won'] == 1
    assert report['performance']['win_loss']['lost'] == 1
    assert report['performance']['win_loss']['lost_reasons'] == [{'reason': '预算冻结', 'count': 1}]
    assert report['revenue'] == {'amount': 1200.0, 'currency': 'CNY'}
    assert report['trace']['event_ids']


async def test_cost_status_distinguishes_unrecorded_unknown_zero_and_recorded(
    ctx: SimpleNamespace,
) -> None:
    async def cost() -> dict[str, Any]:
        report = await growth_report_service.project_overview(
            ctx.session,
            owner_hasn_id=ctx.owner,
            growth_project_id=ctx.growth.id,
        )
        return report['cost']

    assert (await cost())['status'] == 'unrecorded'
    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='cost',
            amount=None,
            currency='CNY',
            idempotency_key='cost:unknown',
            meta_data={'cost_state': 'unknown', 'usage_kind': 'outreach'},
        )
    )
    await ctx.session.flush()
    assert (await cost())['status'] == 'unknown'
    await ctx.session.execute(
        sa.delete(GrowthAttributionEvent).where(
            GrowthAttributionEvent.growth_project_id == ctx.growth.id,
        )
    )
    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='cost',
            amount=Decimal(0),
            currency='CNY',
            idempotency_key='cost:zero',
            meta_data={'cost_state': 'known', 'usage_kind': 'manual_assist'},
        )
    )
    await ctx.session.flush()
    assert (await cost()) == {
        'status': 'zero',
        'recorded': True,
        'amount': 0.0,
        'currency': 'CNY',
        'event_count': 1,
    }
    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='cost',
            amount=Decimal('8.50'),
            currency='CNY',
            idempotency_key='cost:known',
            meta_data={'cost_state': 'known', 'usage_kind': 'email'},
        )
    )
    await ctx.session.flush()
    assert (await cost())['status'] == 'recorded'
    assert (await cost())['amount'] == pytest.approx(8.5)


async def test_reject_suggestion_keeps_project_policy_and_playbook_unchanged(
    ctx: SimpleNamespace,
) -> None:
    suggestion = await growth_review_service.create_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        suggestion_kind='channel',
        proposal={
            'quiet_hours_start': 22,
            'quiet_hours_end': 8,
            'daily_outreach_limit': 12,
        },
        evidence={
            'scope': 'current_month',
            'event_count': 2,
            'insufficient_data': True,
            'limitations': ['样本不足，不能保证结果'],
        },
        proposed_by_kind='system',
        proposed_by_id='growth_review',
        idempotency_key='review:channel:2026-07',
    )
    replay = await growth_review_service.create_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        suggestion_kind='channel',
        proposal={
            'quiet_hours_start': 22,
            'quiet_hours_end': 8,
            'daily_outreach_limit': 12,
        },
        evidence={
            'scope': 'current_month',
            'event_count': 2,
            'insufficient_data': True,
            'limitations': ['样本不足，不能保证结果'],
        },
        proposed_by_kind='system',
        proposed_by_id='growth_review',
        idempotency_key='review:channel:2026-07',
    )
    assert replay['id'] == suggestion['id']

    reviewed = await growth_review_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=suggestion['id'],
        decision='reject',
    )
    await ctx.session.refresh(ctx.growth)
    assert reviewed['status'] == 'rejected'
    assert ctx.growth.policy_version == 1
    assert ctx.growth.quiet_hours_start == 21
    assert ctx.growth.quiet_hours_end == 9
    assert ctx.growth.daily_outreach_limit == 20
    assert (
        await ctx.session.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProjectPlaybook)
            .where(GrowthProjectPlaybook.growth_project_id == ctx.growth.id)
        )
    ) == 0


async def test_accept_channel_and_playbook_suggestions_create_versions(
    ctx: SimpleNamespace,
) -> None:
    channel = await growth_review_service.create_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        suggestion_kind='channel',
        proposal={
            'quiet_hours_start': 22,
            'quiet_hours_end': 8,
            'daily_outreach_limit': 12,
            'monthly_budget': '80.00',
            'budget_currency': 'CNY',
        },
        evidence={
            'scope': 'current_month',
            'event_count': 20,
            'insufficient_data': False,
            'limitations': ['仅覆盖当前已记录渠道'],
        },
        proposed_by_kind='system',
        proposed_by_id='growth_review',
        idempotency_key='review:channel:accepted',
    )
    accepted_channel = await growth_review_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=channel['id'],
        decision='accept',
    )
    assert accepted_channel['status'] == 'accepted'
    assert accepted_channel['applied_version'] == 2
    await ctx.session.refresh(ctx.growth)
    assert ctx.growth.daily_outreach_limit == 12
    assert ctx.growth.monthly_budget == Decimal('80.00')

    playbook = await growth_review_service.create_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        suggestion_kind='playbook',
        proposal={
            'playbook_id': ctx.playbook.id,
            'playbook_version': 1,
            'configuration': {'daily_limit': 10},
        },
        evidence={
            'scope': 'current_month',
            'event_count': 20,
            'insufficient_data': False,
            'limitations': ['历史表现不代表未来结果'],
        },
        proposed_by_kind='agent',
        proposed_by_id='a_growth_review',
        idempotency_key='review:playbook:accepted',
    )
    accepted_playbook = await growth_review_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=playbook['id'],
        decision='accept',
    )
    assert accepted_playbook['status'] == 'accepted'
    adoption = await ctx.session.scalar(
        sa.select(GrowthProjectPlaybook).where(
            GrowthProjectPlaybook.growth_project_id == ctx.growth.id,
            GrowthProjectPlaybook.playbook_id == ctx.playbook.id,
            GrowthProjectPlaybook.status == 'active',
        )
    )
    assert adoption is not None
    assert adoption.playbook_version == 1
    stored = await ctx.session.get(GrowthReviewSuggestion, playbook['id'])
    assert stored is not None
    assert stored.applied_version == 1
