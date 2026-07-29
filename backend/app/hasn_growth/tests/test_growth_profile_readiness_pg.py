"""S5 产品/ICP 画像、Knowledge 新鲜度与待确认建议的真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_profile_suggestion import GrowthProfileSuggestion
from backend.app.hasn_growth.model.growth_profile_version import GrowthProfileVersion
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.service.growth_profile_service import growth_profile_service
from backend.app.hasn_growth.service.growth_project_app_service import (
    growth_project_app_service,
)
from backend.app.hasn_growth.service.report_service import growth_report_service
from backend.app.hasn_knowledge.model.document import Document
from backend.app.hasn_knowledge.model.document_version import DocumentVersion
from backend.app.hasn_knowledge.model.kb import Kb
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_PROFILE_SQL = _REPO / 'backend/sql/hasn_growth/010_create_growth_profile_tables.sql'


async def _apply_profile_sql(db: AsyncSession) -> None:
    raw = await (await db.connection()).get_raw_connection()
    driver_connection = raw.driver_connection
    assert driver_connection is not None
    await driver_connection.execute(_PROFILE_SQL.read_text(encoding='utf-8'))


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
    await _apply_profile_sql(session)
    tag = uuid.uuid4().hex[:10]
    owner = f'h_growth_profile_{tag}'
    owner_user_id = 95_100_000_000 + int(uuid.uuid4().int % 800_000_000)
    agent_hasn_id = f'a_growth_profile_{tag}'
    platform = HasnProject(
        owner_id=owner,
        name=f'S5 画像项目 {tag}',
        status='active',
        bound_agent_id=agent_hasn_id,
    )
    other_platform = HasnProject(
        owner_id=owner,
        name=f'S5 其他项目 {tag}',
        status='active',
    )
    session.add_all((platform, other_platform))
    await session.flush()
    growth = GrowthProject(
        platform_project_id=platform.id,
        user_id=owner_user_id,
        owner_hasn_id=owner,
        owner_scope='personal',
        name=f'S5 获客项目 {tag}',
        kb_ref=None,
        owner_agent_id=agent_hasn_id,
        status='active',
        provision_status='ready',
    )
    session.add(growth)
    session.add(
        HasnAgents(
            hasn_id=agent_hasn_id,
            star_id=f'star_{tag}',
            owner_id=owner,
            display_name='获客分身',
            agent_name=f'growth_{tag}',
            status='active',
        )
    )
    await session.flush()
    kb = Kb(
        owner_id=owner,
        scope='personal',
        visibility='private',
        name=f'S5 知识库 {tag}',
        ragflow_dataset_id=f'ragflow_{tag}',
        embedding_model='BAAI/bge-m3',
        platform_project_id=platform.id,
        client_request_id=f'growth:{growth.id}:knowledge',
        status='active',
    )
    other_kb = Kb(
        owner_id=owner,
        scope='personal',
        visibility='private',
        name=f'S5 跨项目知识库 {tag}',
        ragflow_dataset_id=f'ragflow_other_{tag}',
        embedding_model='BAAI/bge-m3',
        platform_project_id=other_platform.id,
        status='active',
    )
    session.add_all((kb, other_kb))
    await session.flush()
    documents = []
    for index, name in enumerate(('产品与服务', '理想客户画像', '品牌与合规边界'), start=1):
        content = f'# {name}\n\n真实资料 {index}'
        document = Document(
            kb_id=kb.id,
            owner_id=owner,
            kind='native',
            name=name,
            size_bytes=len(content.encode()),
            mime_type='text/markdown',
            content=content,
            current_version=1,
            parse_status='parsed',
            source='system',
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersion(
                document_id=document.id,
                version_no=1,
                title=name,
                content=content,
                source='ui',
            )
        )
        documents.append(document)
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            owner=owner,
            owner_user_id=owner_user_id,
            agent_hasn_id=agent_hasn_id,
            platform=platform,
            growth=growth,
            kb=kb,
            other_kb=other_kb,
            documents=documents,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _product_profile() -> dict:
    return {
        'offering': '企业获客协作平台',
        'value_propositions': ['让销售与 AI 分身共享真实项目上下文'],
        'pricing_model': '订阅制',
        'delivery_regions': ['中国大陆'],
        'evidence_refs': [],
        'constraints': ['首次触达必须审批'],
        'prohibited_claims': ['不得承诺必然成交'],
    }


def _icp_profile() -> dict:
    return {
        'industries': ['企业软件'],
        'regions': ['中国大陆'],
        'company_size': ['50-500人'],
        'buyer_roles': ['销售负责人'],
        'pain_points': ['线索与跟进割裂'],
        'buying_signals': ['正在扩充销售团队'],
        'exclusions': ['无明确合法来源的联系人'],
        'scoring_rules': {'industry_match': 30},
    }


async def _bind_knowledge(ctx: SimpleNamespace) -> dict:
    return await growth_profile_service.bind_knowledge(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
        kb_id=ctx.kb.id,
        expected_profile_version=1,
    )


async def _submit(ctx: SimpleNamespace, *, suffix: str = '1') -> dict:
    return await growth_profile_service.submit_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        agent_hasn_id=ctx.agent_hasn_id,
        growth_project_id=ctx.growth.id,
        expected_version=1,
        product_profile=_product_profile(),
        icp_profile=_icp_profile(),
        knowledge_document_ids=[document.id for document in ctx.documents],
        trace_id=f'11111111-1111-4111-8111-0000000000{suffix.zfill(2)}',
        idempotency_key=f'growth-profile-suggestion-{suffix}',
    )


async def test_agent_suggestion_never_overwrites_confirmed_profile(ctx: SimpleNamespace) -> None:
    await _bind_knowledge(ctx)
    suggestion = await _submit(ctx)
    await ctx.session.refresh(ctx.growth)

    assert suggestion['status'] == 'pending'
    assert suggestion['expected_version'] == 1
    assert ctx.growth.profile_version == 1
    assert ctx.growth.product_profile == {}
    assert ctx.growth.icp_profile == {}
    assert (
        await ctx.session.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthProfileVersion)
            .where(GrowthProfileVersion.growth_project_id == ctx.growth.id)
        )
        == 0
    )


async def test_owner_accepts_suggestion_as_immutable_version_and_reject_is_noop(
    ctx: SimpleNamespace,
) -> None:
    await _bind_knowledge(ctx)
    accepted = await _submit(ctx, suffix='2')
    result = await growth_profile_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.owner_user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=accepted['id'],
        decision='accept',
    )
    assert result['profile_version'] == 2
    assert result['readiness']['ready'] is True
    paused = await growth_project_app_service.pause(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert paused['status'] == 'paused'
    resumed = await growth_project_app_service.resume(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert resumed['status'] == 'active'
    version = (
        await ctx.session.execute(
            sa.select(GrowthProfileVersion).where(
                GrowthProfileVersion.growth_project_id == ctx.growth.id,
                GrowthProfileVersion.version == 2,
            )
        )
    ).scalar_one()
    assert version.product_profile == _product_profile()
    assert version.confirmed_by_kind == 'owner'
    assert len(version.knowledge_document_versions) == 3

    rejected = await growth_profile_service.submit_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        agent_hasn_id=ctx.agent_hasn_id,
        growth_project_id=ctx.growth.id,
        expected_version=2,
        product_profile={**_product_profile(), 'offering': '不应生效'},
        icp_profile=_icp_profile(),
        knowledge_document_ids=[document.id for document in ctx.documents],
        trace_id='22222222-2222-4222-8222-222222222222',
        idempotency_key='growth-profile-suggestion-reject',
    )
    await growth_profile_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.owner_user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=rejected['id'],
        decision='reject',
    )
    await ctx.session.refresh(ctx.growth)
    assert ctx.growth.profile_version == 2
    assert ctx.growth.product_profile['offering'] == '企业获客协作平台'


async def test_readiness_is_recomputed_from_current_knowledge_versions(
    ctx: SimpleNamespace,
) -> None:
    await _bind_knowledge(ctx)
    suggestion = await _submit(ctx, suffix='3')
    await growth_profile_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.owner_user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=suggestion['id'],
        decision='accept',
    )
    assert (
        await growth_profile_service.compute_readiness(
            ctx.session,
            owner_hasn_id=ctx.owner,
            growth_project_id=ctx.growth.id,
        )
    )['ready'] is True

    changed = ctx.documents[0]
    changed.current_version = 2
    changed.content = '# 产品与服务\n\nOwner 更新后的真实资料'
    ctx.session.add(
        DocumentVersion(
            document_id=changed.id,
            version_no=2,
            title=changed.name,
            content=changed.content,
            source='ui',
        )
    )
    await ctx.session.flush()
    stale = await growth_profile_service.compute_readiness(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert stale['ready'] is False
    assert stale['profile_sync_status'] == 'stale'
    assert 'profile_stale' in stale['blocking_reasons']


async def test_bind_relink_and_reconcile_never_cross_platform_project(
    ctx: SimpleNamespace,
) -> None:
    bound = await _bind_knowledge(ctx)
    assert bound['kb_ref'] == f'hasn://knowledge/kbs/{ctx.kb.id}'

    replay = await _bind_knowledge(ctx)
    assert replay['changed'] is False

    with pytest.raises(errors.NotFoundError):
        await growth_profile_service.bind_knowledge(
            ctx.session,
            owner_hasn_id=ctx.owner,
            growth_project_id=ctx.growth.id,
            kb_id=ctx.other_kb.id,
            expected_profile_version=1,
        )

    ctx.growth.kb_ref = None
    await ctx.session.flush()
    repaired = await growth_profile_service.reconcile_knowledge_binding(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert repaired['repaired'] is True
    assert repaired['kb_ref'] == f'hasn://knowledge/kbs/{ctx.kb.id}'


async def test_accepting_stale_suggestion_is_rejected_without_profile_mutation(
    ctx: SimpleNamespace,
) -> None:
    await _bind_knowledge(ctx)
    first = await _submit(ctx, suffix='4')
    second = await _submit(ctx, suffix='5')
    await growth_profile_service.review_suggestion(
        ctx.session,
        owner_hasn_id=ctx.owner,
        owner_user_id=ctx.owner_user_id,
        growth_project_id=ctx.growth.id,
        suggestion_id=first['id'],
        decision='accept',
    )
    with pytest.raises(errors.ConflictError):
        await growth_profile_service.review_suggestion(
            ctx.session,
            owner_hasn_id=ctx.owner,
            owner_user_id=ctx.owner_user_id,
            growth_project_id=ctx.growth.id,
            suggestion_id=second['id'],
            decision='accept',
        )
    await ctx.session.refresh(ctx.growth)
    assert ctx.growth.profile_version == 2
    stale = await ctx.session.get(GrowthProfileSuggestion, second['id'])
    assert stale is not None
    assert stale.status == 'stale'


async def test_project_overview_distinguishes_unrecorded_cost_from_zero(
    ctx: SimpleNamespace,
) -> None:
    empty = await growth_report_service.project_overview(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert empty['cost'] == {
        'recorded': False,
        'amount': None,
        'currency': 'CNY',
    }
    assert empty['conversion']['lead_to_customer']['rate'] is None

    ctx.session.add(
        GrowthAttributionEvent(
            growth_project_id=ctx.growth.id,
            event_type='cost',
            amount=Decimal('123.45'),
            currency='CNY',
            idempotency_key=f'cost-{uuid.uuid4()}',
        )
    )
    await ctx.session.flush()
    recorded = await growth_report_service.project_overview(
        ctx.session,
        owner_hasn_id=ctx.owner,
        growth_project_id=ctx.growth.id,
    )
    assert recorded['cost']['recorded'] is True
    assert recorded['cost']['amount'] == pytest.approx(123.45)
