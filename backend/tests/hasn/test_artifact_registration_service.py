"""Agent 产物当前态与参与记录拆分的真实 PostgreSQL 集成测试。"""

from __future__ import annotations

import asyncio

from uuid import uuid4

import pytest

from sqlalchemy import select, update

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox, HasnArtifacts
from backend.app.hasn.schema.artifact_contract import ArtifactMutation
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.artifact_query_service import artifact_query_service
from backend.app.hasn.service.artifact_registration_outbox_service import artifact_registration_outbox_service
from backend.app.hasn.service.artifact_registration_service import artifact_registration_service
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.common.exception import errors
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


async def test_imagelab_composite_dispatch_id_persists_without_truncation() -> None:
    """图坊真实派发 ID 与输出 ID 组合后超过 64 字符，当前态和参与记录必须原样保存。"""
    owner = _id('owner')
    agent = _id('agent')
    dispatch_id = (
        f'work_disp_{uuid4().hex[:26]}:'
        f'ilab_out_{uuid4().hex[:26]}'
    )
    assert 64 < len(dispatch_id) <= 128

    async with async_db_session() as db:
        try:
            result = await artifact_registration_service.register(
                db,
                _resource_mutation(
                    owner=owner,
                    agent=agent,
                    action='create',
                    dispatch_id=dispatch_id,
                    session_id=_id('session'),
                    project_id=str(uuid4()),
                    title='图坊真实扩图产物',
                ),
            )

            artifact = (
                await db.execute(
                    select(HasnArtifacts).where(
                        HasnArtifacts.artifact_id == result.artifact_id
                    )
                )
            ).scalar_one()
            contribution = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.artifact_id == result.artifact_id
                    )
                )
            ).scalar_one()
            assert artifact.dispatch_id == dispatch_id
            assert contribution.dispatch_id == dispatch_id
        finally:
            await db.rollback()


async def test_resource_metadata_counters_accumulate_once_per_contribution() -> None:
    """批次摘要按新参与原子累加；同一 dispatch 重放不能重复计数。"""
    owner = _id('owner')
    agent = _id('agent')

    def mutation(
        *,
        dispatch_id: str,
        inserted: int,
        updated: int,
        skipped: int,
        error_count: int,
    ) -> ArtifactMutation:
        return ArtifactMutation.model_validate({
            'owner_hasn_id': owner,
            'agent_hasn_id': agent,
            'action': 'update',
            'source_kind': 'app_write',
            'resource_uri': 'hasn://growth/leads/project-s6-counter',
            'resource_kind': 'growth.leads',
            'resource_app_id': 'growth',
            'dispatch_id': dispatch_id,
            'title': '获客线索批次',
            'metadata': {
                'inserted': inserted,
                'updated': updated,
                'skipped': skipped,
                'error_count': error_count,
            },
            'accumulate_metadata_keys': [
                'inserted',
                'updated',
                'skipped',
                'error_count',
            ],
        })

    async with async_db_session() as db:
        try:
            first = mutation(
                dispatch_id='growth-s6-batch-1',
                inserted=2,
                updated=1,
                skipped=0,
                error_count=1,
            )
            await artifact_registration_service.register(db, first)
            await artifact_registration_service.register(db, first)
            await artifact_registration_service.register(
                db,
                mutation(
                    dispatch_id='growth-s6-batch-2',
                    inserted=3,
                    updated=0,
                    skipped=4,
                    error_count=0,
                ),
            )

            artifact = (
                await db.execute(
                    select(HasnArtifacts).where(
                        HasnArtifacts.owner_hasn_id == owner
                    )
                )
            ).scalar_one()
            assert artifact.meta_data == {
                'inserted': 5,
                'updated': 1,
                'skipped': 4,
                'error_count': 1,
            }
            contributions = (
                (
                    await db.execute(
                        select(HasnArtifactContributions).where(
                            HasnArtifactContributions.owner_hasn_id == owner
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(contributions) == 2
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
            assert item.latest_contribution is not None
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


async def test_body_artifact_without_dispatch_falls_back_to_content_key_not_random() -> None:
    """无 dispatch/source_event 的正文产物必须按内容派生对象键（设计 A12，绝不随机）。

    历史实现用 `uuid4().hex` 兜底：outbox 每重试一次就新生成一个对象键，同一正文在云端堆出
    一排产物。内容键兜底下，重放折叠回同一产物与同一参与记录；内容不同才落在不同产物上。
    """
    owner = _id('owner')
    agent = _id('agent')

    def body_mutation(body: str) -> ArtifactMutation:
        return ArtifactMutation.model_validate(
            {
                'owner_hasn_id': owner,
                'agent_hasn_id': agent,
                'action': 'create',
                'source_kind': 'agent_note',
                'artifact_kind': 'document',
                'body': body,
                'title': '随想',
            }
        )

    async with async_db_session() as db:
        try:
            first = await artifact_registration_service.register(db, body_mutation('第一版正文'))
            replay = await artifact_registration_service.register(db, body_mutation('第一版正文'))
            other = await artifact_registration_service.register(db, body_mutation('另一条正文'))

            assert replay.artifact_id == first.artifact_id, '同一正文重放不得新生成产物'
            assert other.artifact_id != first.artifact_id, '不同正文不得折叠成同一产物'

            artifacts = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
            ).scalars().all()
            assert len(artifacts) == 2
            assert all(row.artifact_key.startswith('body:agent:content:') for row in artifacts)

            contributions = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.owner_hasn_id == owner
                    )
                )
            ).scalars().all()
            assert len(contributions) == 2, '重放的参与记录必须按确定性幂等键去重'
        finally:
            await db.rollback()


async def test_soft_deleted_artifact_revives_on_reregister() -> None:
    """软删后的同一对象被分身再次写入时必须复活，否则参与记录会在一条隐形产物上累积。"""
    owner = _id('owner')
    agent = _id('agent')
    mutation = _resource_mutation(
        owner=owner,
        agent=agent,
        action='create',
        dispatch_id='dispatch_revive',
        session_id=_id('session'),
        project_id=str(uuid4()),
        title='删后又写',
    )

    async with async_db_session() as db:
        try:
            created = await artifact_registration_service.register(db, mutation)
            await db.execute(
                update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == created.artifact_id)
                .values(status='deleted')
            )

            revived = await artifact_registration_service.register(db, mutation)

            assert revived.artifact_id == created.artifact_id
            row = (
                await db.execute(
                    select(HasnArtifacts).where(HasnArtifacts.artifact_id == created.artifact_id)
                )
            ).scalar_one()
            assert row.status == 'active'
        finally:
            await db.rollback()


def _local_file_mutation(
    *,
    owner: str,
    agent: str,
    node: str,
    locator_key: str,
    dispatch_id: str,
    supersedes: str | None = None,
) -> ArtifactMutation:
    """构造一条本地文件产物的登记命令（可带 supersedes 归并意图）。"""
    payload: dict[str, object] = {
        'owner_hasn_id': owner,
        'agent_hasn_id': agent,
        'action': 'create',
        'source_kind': 'runtime_file',
        'artifact_kind': 'file',
        'local_locator_key': locator_key,
        'node_id': node,
        'local_entry_kind': 'file',
        'dispatch_id': dispatch_id,
        'title': '本地文件产物',
    }
    if supersedes is not None:
        payload['supersedes_locator_key'] = supersedes
    return ArtifactMutation.model_validate(payload)


async def test_supersede_merge_rekeys_legacy_row_and_keeps_contributions() -> None:
    """带 supersedes 的登记命中存量 legacy 行时原地改键，同一文件不留下两条产物（设计 §4.7）。"""
    owner = _id('owner')
    agent = _id('agent')
    node = _id('node')
    legacy_locator = f'legacy-path-v1:{uuid4().hex[:16]}'
    v2_locator = f'locator-v2:{uuid4().hex[:16]}'

    async with async_db_session() as db:
        try:
            legacy = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=legacy_locator,
                    dispatch_id='legacy_dispatch',
                ),
            )
            merged = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=v2_locator,
                    dispatch_id='v2_dispatch',
                    supersedes=legacy_locator,
                ),
            )

            assert merged.artifact_id == legacy.artifact_id, '归并必须改键复用同一行，不得新生成产物'
            artifacts = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
            ).scalars().all()
            assert len(artifacts) == 1
            assert artifacts[0].artifact_key == f'local:{node}:{v2_locator}'
            assert artifacts[0].local_locator_key == v2_locator

            contributions = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.owner_hasn_id == owner
                    )
                )
            ).scalars().all()
            assert len(contributions) == 2, '改键前的参与记录必须随行保留'
        finally:
            await db.rollback()


async def test_supersede_merge_skips_when_target_key_already_exists() -> None:
    """目标键已上云时归并不动旧行：撞唯一约束的旧路径，留给 upsert 按新键正常合并。"""
    owner = _id('owner')
    agent = _id('agent')
    node = _id('node')
    legacy_locator = f'legacy-path-v1:{uuid4().hex[:16]}'
    v2_locator = f'locator-v2:{uuid4().hex[:16]}'

    async with async_db_session() as db:
        try:
            legacy = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=legacy_locator,
                    dispatch_id='legacy_dispatch',
                ),
            )
            target = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=v2_locator,
                    dispatch_id='v2_dispatch',
                ),
            )
            again = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=v2_locator,
                    dispatch_id='v2_dispatch_again',
                    supersedes=legacy_locator,
                ),
            )

            assert again.artifact_id == target.artifact_id
            rows = (
                await db.execute(
                    select(HasnArtifacts)
                    .where(HasnArtifacts.owner_hasn_id == owner)
                    .order_by(HasnArtifacts.id)
                )
            ).scalars().all()
            assert len(rows) == 2, '目标键已存在时旧行必须原样保留为历史'
            assert rows[0].artifact_id == legacy.artifact_id
            assert rows[0].artifact_key == f'local:{node}:{legacy_locator}'
            assert rows[0].local_locator_key == legacy_locator
            assert rows[1].artifact_key == f'local:{node}:{v2_locator}'
        finally:
            await db.rollback()


async def test_supersede_merge_survives_concurrent_target_insert() -> None:
    """归并改键与并发插入目标键撞车时不许把整笔登记炸成 5xx（TOCTOU 修复回归）。

    复现路径：outbox 重放的旧 mutation（不带 supersedes）直走 upsert，插入目标键行但**未提交**；
    带 supersedes 的新登记同时到达——其改键语句在唯一索引上阻塞，对方提交后报 UniqueViolation。
    SAVEPOINT 只回滚归并这一步，后续 upsert 经 ON CONFLICT 与胜方合并：登记照常完成、旧行原样、
    目标行吸收双方参与记录。若时序偏移（对方先提交），语句内 NOT EXISTS 让改键退化为 0 行，
    断言同样成立——两种交错共用同一确定结局。
    """
    owner = _id('owner')
    agent = _id('agent')
    node = _id('node')
    legacy_locator = f'legacy-path-v1:{uuid4().hex[:16]}'
    v2_locator = f'locator-v2:{uuid4().hex[:16]}'
    v2_key = f'local:{node}:{v2_locator}'
    winner_artifact_id = _id('art')
    winner_inserted = asyncio.Event()

    async def insert_winner_without_commit() -> None:
        """模拟并发登记：插入目标键行后挂起片刻再提交，压出改键的索引阻塞窗口。"""
        async with async_db_session() as db:
            db.add(
                HasnArtifacts(
                    artifact_id=winner_artifact_id,
                    owner_hasn_id=owner,
                    agent_hasn_id=agent,
                    artifact_key=v2_key,
                    artifact_kind='file',
                    kind='file',
                    local_locator_key=v2_locator,
                    local_entry_kind='file',
                    node_id=node,
                )
            )
            await db.flush()
            winner_inserted.set()
            # 归并方的改键必须在此期间抵达唯一索引并阻塞；窗口内未抵达则退化为
            # 「对方已提交」路径（语句内 NOT EXISTS 判 0 行），两种结局断言一致。
            await asyncio.sleep(0.5)
            await db.commit()

    async with async_db_session() as db:
        try:
            legacy = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=legacy_locator,
                    dispatch_id='legacy_dispatch',
                ),
            )
            await db.commit()  # legacy 行必须已提交，归并语句才见得到

            winner = asyncio.create_task(insert_winner_without_commit())
            await winner_inserted.wait()
            merged = await artifact_registration_service.register(
                db,
                _local_file_mutation(
                    owner=owner,
                    agent=agent,
                    node=node,
                    locator_key=v2_locator,
                    dispatch_id='v2_race_dispatch',
                    supersedes=legacy_locator,
                ),
            )
            await winner

            assert merged.artifact_id == winner_artifact_id, '撞键后必须经 upsert 与胜方合并'
            rows = (
                await db.execute(
                    select(HasnArtifacts)
                    .where(HasnArtifacts.owner_hasn_id == owner)
                    .order_by(HasnArtifacts.id)
                )
            ).scalars().all()
            assert len(rows) == 2, '归并放弃改键后旧行必须原样保留，不得消失也不得改键'
            assert rows[0].artifact_id == legacy.artifact_id
            assert rows[0].artifact_key == f'local:{node}:{legacy_locator}'
            assert rows[1].artifact_id == winner_artifact_id

            contributions = (
                await db.execute(
                    select(HasnArtifactContributions).where(
                        HasnArtifactContributions.owner_hasn_id == owner
                    )
                )
            ).scalars().all()
            assert any(row.dispatch_id == 'v2_race_dispatch' for row in contributions), (
                '归并撞键后本笔登记的参与记录仍须落库'
            )
        finally:
            await db.rollback()


async def test_list_orders_by_updated_time_desc_then_artifact_id() -> None:
    """设计 02 §8.2：排序键统一 `(updated_time DESC, artifact_id DESC)`，与本地索引对齐——
    daemon 合并本地/云端两个分页源时只有同一排序键才能稳定去重。"""
    owner = _id('owner')
    agent = _id('agent')

    async with async_db_session() as db:
        try:
            ids: list[str] = []
            for index in range(3):
                created = await artifact_registration_service.register(
                    db,
                    _resource_mutation(
                        owner=owner,
                        agent=agent,
                        action='create',
                        dispatch_id=f'order_dispatch_{index}',
                        session_id=None,
                        project_id=None,
                        title=f'产物{index}',
                    ).model_copy(
                        update={'resource_uri': f'hasn://deck/deck_order_{index}'}
                    ),
                )
                ids.append(created.artifact_id)

            # 回写确定时间戳：t3 > t2 > t1；同刻两条靠 artifact_id DESC 决胜。
            base = timezone.now()
            await db.execute(
                update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == ids[0])
                .values(updated_time=base)
            )
            await db.execute(
                update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == ids[1])
                .values(updated_time=base)
            )
            await db.execute(
                update(HasnArtifacts)
                .where(HasnArtifacts.artifact_id == ids[2])
                .values(updated_time=base.replace(year=base.year - 1))
            )

            page = await artifact_query_service.list(db, owner_hasn_id=owner, size=10)

            assert page.total == 3
            got = [item.artifact_id for item in page.items]
            # 同刻两条按 artifact_id 字典序倒序，最后是去年那条。
            same_tick = sorted([ids[0], ids[1]], reverse=True)
            assert got == [*same_tick, ids[2]]
            # updated_time 回落与 DTO 同口径：排序键不显式 updated_time 时用 created_time。
            assert page.items[0].updated_time is not None
        finally:
            await db.rollback()


async def test_list_keyset_cursor_paginates_without_overlap() -> None:
    """keyset 游标（A16）：翻页不重不漏；total 恒为全量权威值（云端单一数据源）。"""
    owner = _id('owner')
    agent = _id('agent')

    async with async_db_session() as db:
        try:
            ids: list[str] = []
            for index in range(3):
                created = await artifact_registration_service.register(
                    db,
                    _resource_mutation(
                        owner=owner,
                        agent=agent,
                        action='create',
                        dispatch_id=f'cursor_dispatch_{index}',
                        session_id=None,
                        project_id=None,
                        title=f'产物{index}',
                    ).model_copy(
                        update={'resource_uri': f'hasn://deck/deck_cursor_{index}'}
                    ),
                )
                ids.append(created.artifact_id)

            base = timezone.now()
            for index, artifact_id in enumerate(ids):
                await db.execute(
                    update(HasnArtifacts)
                    .where(HasnArtifacts.artifact_id == artifact_id)
                    .values(updated_time=base.replace(hour=10 - index))
                )

            first = await artifact_query_service.list(db, owner_hasn_id=owner, size=2)
            assert first.total == 3
            assert [item.artifact_id for item in first.items] == [ids[0], ids[1]]

            tail = first.items[-1]
            from backend.app.hasn.service.artifact_query_service import encode_keyset_cursor

            cursor = encode_keyset_cursor(tail.updated_time, tail.artifact_id)
            second = await artifact_query_service.list(
                db, owner_hasn_id=owner, size=2, cursor=cursor
            )
            assert second.total == 3, 'keyset 翻页下 total 仍是全量权威值，不是剩余条数'
            assert [item.artifact_id for item in second.items] == [ids[2]]
        finally:
            await db.rollback()


async def test_list_rejects_broken_cursor() -> None:
    """损坏/手改的游标一律 422——静默退回第一页会丢页还看似正常。"""
    owner = _id('owner')

    async with async_db_session() as db:
        try:
            # 空串与未传同义（不进 decode）；其余损坏/手改形态一律 422。
            for bad in ('not-a-cursor', '2026-01-01|', '|art_x', 'garbage|art_x'):
                with pytest.raises(errors.RequestError):
                    await artifact_query_service.list(db, owner_hasn_id=owner, cursor=bad)
        finally:
            await db.rollback()


async def test_contributionless_history_row_surfaces_with_honest_lost_mark() -> None:
    """A15：历史回填无法恢复参与事实的行必须出现在全量列表里，latest_contribution 合法
    留空并透 migration_lost_history——INNER JOIN 静默吞行或伪填占位分身都违反诚实原则。"""
    owner = _id('owner')

    async with async_db_session() as db:
        try:
            db.add(
                HasnArtifacts(
                    artifact_id=_id('art'),
                    owner_hasn_id=owner,
                    agent_hasn_id='',
                    artifact_key=f'legacy:{_id("key")}',
                    artifact_kind='document',
                    kind='document',
                    body='无法考证发起者的历史正文',
                    status='active',
                    meta_data={'migration_lost_history': True},
                )
            )
            await db.flush()

            page = await artifact_query_service.list(db, owner_hasn_id=owner, size=10)

            assert page.total == 1
            item = page.items[0]
            assert item.latest_contribution is None, '无参与记录可考时如实留空，不伪填'
            assert item.migration_lost_history is True
            assert item.agent_identity is None
            assert item.body_preview is not None, '产物本体仍正常呈现'
        finally:
            await db.rollback()


async def test_contributionless_row_excluded_from_contribution_axis_but_not_owner_list() -> None:
    """A15 补充：按分身/会话等参与轴筛选时，无参与记录的行天然不可能命中（INNER JOIN 语义）；
    未打标记的缺参与行也不吞——照常在全量列表透出（登记链路缺陷由 service warn 显式告警）。"""
    owner = _id('owner')

    async with async_db_session() as db:
        try:
            db.add(
                HasnArtifacts(
                    artifact_id=_id('art'),
                    owner_hasn_id=owner,
                    agent_hasn_id='',
                    artifact_key=f'legacy:{_id("key")}',
                    artifact_kind='document',
                    kind='document',
                    body='缺参与且未打标记的异常行',
                    status='active',
                )
            )
            await db.flush()

            by_session = await artifact_query_service.list(
                db, owner_hasn_id=owner, work_session_id=_id('ws'), size=10
            )
            assert by_session.total == 0, '会话轴筛选下无参与记录的行不得混入'

            by_agent = await artifact_query_service.list(
                db, owner_hasn_id=owner, agent_hasn_id=_id('agent'), size=10
            )
            assert by_agent.total == 0, '分身轴筛选下无参与记录的行不得混入'

            unfiltered = await artifact_query_service.list(db, owner_hasn_id=owner, size=10)
            assert unfiltered.total == 1, '全量列表不吞缺参与行（缺陷显式透出而非隐藏）'
            assert unfiltered.items[0].latest_contribution is None
            assert unfiltered.items[0].migration_lost_history is False
        finally:
            await db.rollback()
