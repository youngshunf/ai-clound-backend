"""Agent 产物当前态与参与记录拆分的真实 PostgreSQL 集成测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from sqlalchemy import select

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox, HasnArtifacts
from backend.app.hasn.schema.artifact_contract import ArtifactMutation
from backend.app.hasn.service.artifact_registration_service import artifact_registration_service
from backend.app.hasn.service.artifact_query_service import artifact_query_service
from backend.app.hasn.service.artifact_registration_outbox_service import artifact_registration_outbox_service
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio(loop_scope='session')


def _id(prefix: str) -> str:
    """生成不与真实开发库既有数据冲突的稳定测试标识。"""
    return f'{prefix}_{uuid4().hex[:20]}'


def _resource_mutation(
    *,
    owner: str,
    agent: str,
    action: str,
    dispatch_id: str,
    session_id: str | None,
    project_id: str | None,
    title: str,
) -> ArtifactMutation:
    """构造同一云端资源的真实登记命令。"""
    return ArtifactMutation.model_validate(
        {
            'owner_hasn_id': owner,
            'agent_hasn_id': agent,
            'action': action,
            'source_kind': 'app_write',
            'resource_uri': 'hasn://deck/deck_phase1',
            'resource_kind': 'deck.presentation',
            'resource_app_id': 'deck',
            'dispatch_id': dispatch_id,
            'work_session_id': session_id,
            'project_id': project_id,
            'title': title,
        }
    )


async def test_resource_create_and_update_keep_one_current_artifact_and_two_contributions() -> None:
    """同一资源更新当前态，但每次参与的 action、会话和项目必须永久保留。"""
    owner = _id('owner')
    agent_one = _id('agent')
    agent_two = _id('agent')
    session_one = _id('session')
    session_two = _id('session')
    project_one = str(uuid4())
    project_two = str(uuid4())

    async with async_db_session() as db:
        try:
            created = await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent_one,
                    action='create',
                    dispatch_id='dispatch_create',
                    session_id=session_one,
                    project_id=project_one,
                    title='初稿',
                ),
            )
            updated = await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent_two,
                    action='update',
                    dispatch_id='dispatch_update',
                    session_id=session_two,
                    project_id=project_two,
                    title='二稿',
                ),
            )

            assert updated.artifact_id == created.artifact_id

            artifacts = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
            ).scalars().all()
            contributions = (
                await db.execute(
                    select(HasnArtifactContributions)
                    .where(HasnArtifactContributions.owner_hasn_id == owner)
                    .order_by(HasnArtifactContributions.id)
                )
            ).scalars().all()

            assert len(artifacts) == 1
            assert artifacts[0].artifact_key == 'resource:hasn://deck/deck_phase1'
            assert artifacts[0].title == '二稿'
            assert str(artifacts[0].project_id) == project_two
            assert len(contributions) == 2
            assert [(row.action, row.agent_hasn_id) for row in contributions] == [
                ('create', agent_one),
                ('update', agent_two),
            ]
            assert [(row.work_session_id, str(row.project_id)) for row in contributions] == [
                (session_one, project_one),
                (session_two, project_two),
            ]
        finally:
            await db.rollback()


async def test_resource_replay_uses_contribution_idempotency_key() -> None:
    """同一 agent、dispatch 和资源 URI 的重放不能追加第二条参与记录。"""
    owner = _id('owner')
    agent = _id('agent')
    mutation = _resource_mutation(
        owner=owner,
        agent=agent,
        action='create',
        dispatch_id='dispatch_replay',
        session_id=_id('session'),
        project_id=str(uuid4()),
        title='可重放资源',
    )

    async with async_db_session() as db:
        try:
            first = await artifact_registration_service.register(db, mutation)
            second = await artifact_registration_service.register(db, mutation)

            assert second.artifact_id == first.artifact_id
            rows = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.owner_hasn_id == owner
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
        finally:
            await db.rollback()


async def test_query_returns_contextual_latest_contribution_without_local_path() -> None:
    """查询必须按筛选上下文选择最新参与记录，且读模型不泄露本地绝对路径。"""
    owner = _id('owner')
    agent_one = _id('agent')
    agent_two = _id('agent')
    project_one = str(uuid4())
    project_two = str(uuid4())

    async with async_db_session() as db:
        try:
            await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent_one,
                    action='create',
                    dispatch_id='query_create',
                    session_id=_id('session'),
                    project_id=project_one,
                    title='初稿',
                ),
            )
            await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent_two,
                    action='update',
                    dispatch_id='query_update',
                    session_id=_id('session'),
                    project_id=project_two,
                    title='终稿',
                ),
            )

            page = await artifact_query_service.list(
                db,
                owner_hasn_id=owner,
                project_id=project_two,
            )

            assert page.total == 1
            item = page.items[0]
            assert item.title == '终稿'
            assert item.latest_contribution.agent_hasn_id == agent_two
            assert item.latest_contribution.action == 'update'
            assert item.project_relation is not None
            assert item.project_relation.via == 'participation'
            assert 'local_path' not in item.model_dump()
        finally:
            await db.rollback()


async def test_opaque_local_locator_never_advertises_unavailable_locate_action() -> None:
    """P06 尚无受守卫的本机反查时，不得把不可打开的 locator 声称为可定位。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')

    async with async_db_session() as db:
        try:
            await artifact_registration_service.register(
                db,
                ArtifactMutation.model_validate(
                    {
                        'owner_hasn_id': owner,
                        'agent_hasn_id': agent,
                        'action': 'create',
                        'source_kind': 'runtime_file',
                        'artifact_kind': 'file',
                        'local_locator_key': 'legacy-path-v1:opaque-only',
                        'node_id': node_id,
                        'local_entry_kind': 'file',
                        'dispatch_id': 'opaque_locator',
                        'title': '只含定位键的 Runtime 文件',
                    }
                ),
            )

            page = await artifact_query_service.list(
                db,
                owner_hasn_id=owner,
                current_node_id=node_id,
            )

            assert page.total == 1
            assert page.items[0].local_entry is not None
            assert page.items[0].availability == 'local_unavailable'
            assert page.items[0].allowed_actions == []
        finally:
            await db.rollback()


async def test_outbox_claim_retry_dead_letter_and_reconcile() -> None:
    """outbox 必须可领取、可重试、可终局失败，且 reconcile 可补齐漏登记意图。"""
    owner = _id('owner')
    agent = _id('agent')

    async with async_db_session() as db:
        try:
            result = await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent,
                    action='create',
                    dispatch_id='outbox_create',
                    session_id=_id('session'),
                    project_id=str(uuid4()),
                    title='待核验资源',
                ),
            )
            claimed = await artifact_registration_outbox_service.claim(db, limit=1)
            assert claimed == [], '同步登记已确认的 outbox 不应再次被领取'

            recovered = await artifact_registration_outbox_service.reconcile(db, owner_hasn_id=owner)
            assert recovered == 0, '完整登记不应被 reconcile 重复写入'

            # 先存在一条已完成 outbox，再追加一条遗漏 contribution。reconcile(limit=1)
            # 不能总停在最早的已完成记录，否则后面的遗漏将永远得不到修复。
            missing_key = 'reconcile_missing_after_completed'
            db.add(
                HasnArtifactContributions(
                    contribution_id=_id('con'),
                    artifact_id=result.artifact_id,
                    owner_hasn_id=owner,
                    agent_hasn_id=agent,
                    action='update',
                    source_kind='app_write',
                    idempotency_key=missing_key,
                )
            )
            await db.flush()
            recovered = await artifact_registration_outbox_service.reconcile(db, owner_hasn_id=owner, limit=1)
            assert recovered == 1
            repaired = (
                await db.execute(
                    select(HasnArtifactRegistrationOutbox).where(
                        HasnArtifactRegistrationOutbox.owner_hasn_id == owner,
                        HasnArtifactRegistrationOutbox.idempotency_key == f'{agent}:{missing_key}',
                    )
                )
            ).scalar_one_or_none()
            assert repaired is not None

            pending_id = f'aor_{uuid4().hex}'
            db.add(
                HasnArtifactRegistrationOutbox(
                    outbox_id=pending_id,
                    owner_hasn_id=owner,
                    artifact_id=result.artifact_id,
                    idempotency_key=f'manual:{result.artifact_id}',
                    payload={'resource_uri': result.resource_uri},
                    status='pending',
                )
            )
            await db.flush()
            claimed = await artifact_registration_outbox_service.claim(db, limit=1)
            assert [row.outbox_id for row in claimed] == [pending_id]
            assert claimed[0].status == 'processing'
            assert claimed[0].attempt_count == 1

            retried = await artifact_registration_outbox_service.mark_retry(
                db,
                outbox_id=pending_id,
                reason='上游暂时不可用',
            )
            assert retried is True
            claimed[0].next_retry_at = timezone.now()
            await db.flush()
            claimed_again = await artifact_registration_outbox_service.claim(db, limit=1)
            assert [row.outbox_id for row in claimed_again] == [pending_id]
            dead_lettered = await artifact_registration_outbox_service.mark_retry(
                db,
                outbox_id=pending_id,
                reason='请求字段不符合契约',
                contract_error=True,
            )
            assert dead_lettered is True
            assert claimed_again[0].status == 'dead_letter'
        finally:
            await db.rollback()


async def test_reconcile_replays_persisted_app_resource_registration_intent() -> None:
    """首次登记失败后留下的真实意图必须能在后续对账中补回产物与参与记录。"""
    owner = _id('owner')
    agent = _id('agent')
    descriptor = ai_native_app_registry.resource_descriptor('deck', 'deck.presentation')
    assert descriptor is not None

    async with async_db_session() as db:
        try:
            await artifact_registration_outbox_service.enqueue_app_resource_repair_intent(
                db,
                descriptor=descriptor,
                server_id='deck_repair_phase1',
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                title='待补登记演示文稿',
                summary='业务资源已落库，登记意图待重放',
                source_tool='hasn.deck.create',
                work_session_id=_id('session'),
                project_id=str(uuid4()),
                action='create',
                dispatch_id='repair_dispatch',
            )

            claimed = await artifact_registration_outbox_service.claim(db, limit=1)
            assert claimed == [], '修复意图只能由 reconcile 重放，不能被普通投递 worker 误领取'

            repaired = await artifact_registration_outbox_service.reconcile(db, owner_hasn_id=owner)
            assert repaired == 1

            intent = (
                await db.execute(
                    select(HasnArtifactRegistrationOutbox).where(
                        HasnArtifactRegistrationOutbox.owner_hasn_id == owner,
                        HasnArtifactRegistrationOutbox.idempotency_key.like('repair:%'),
                    )
                )
            ).scalar_one()
            assert intent.status == 'completed'
            assert intent.artifact_id is not None

            contributions = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.owner_hasn_id == owner
                    )
                )
            ).scalars().all()
            assert len(contributions) == 1
            assert contributions[0].artifact_id == intent.artifact_id
            assert contributions[0].source_kind == 'app_write'
        finally:
            await db.rollback()


async def test_failed_best_effort_registration_persists_repair_intent() -> None:
    """真实数据库拒绝登记时，业务调用必须返回 URI 并留下可恢复意图。"""
    owner = _id('owner')
    # contributions.agent_hasn_id 的 PostgreSQL 长度约束会使首次登记真实失败；outbox JSON 仍可保留
    # 原始意图，验证 SAVEPOINT 没有把外层业务事务置为回滚态。
    oversized_agent = f'agent_{uuid4().hex}{uuid4().hex}'

    async with async_db_session() as db:
        try:
            registration = await register_app_resource_artifact(
                db,
                app_id='deck',
                resource_kind='deck.presentation',
                server_id='deck_failed_registration',
                agent_hasn_id=oversized_agent,
                owner_hasn_id=owner,
                title='登记失败仍可打开的演示文稿',
                source_tool='hasn.deck.create',
                dispatch_id='failed_registration_dispatch',
            )
            assert registration is not None
            assert registration.artifact_id is None
            assert registration.resource_uri == 'hasn://deck/deck_failed_registration'

            intent = (
                await db.execute(
                    select(HasnArtifactRegistrationOutbox).where(
                        HasnArtifactRegistrationOutbox.owner_hasn_id == owner
                    )
                )
            ).scalar_one()
            assert intent.status == 'pending'
            assert intent.artifact_id is None
            assert intent.payload['intent_kind'] == 'app_resource'

            recovered = await artifact_registration_outbox_service.reconcile(db, owner_hasn_id=owner)
            assert recovered == 0
            assert intent.status == 'dead_letter'
            assert intent.attempt_count == 1
            assert intent.last_error
        finally:
            await db.rollback()
