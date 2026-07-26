"""客户端幂等键与本地定位键归并的真实 PostgreSQL 集成测试（设计 A12 · §4.7）。

这批用例钉死 P2-5 的硬前提：节点侧的 outbox 会无限重试同一份 payload，云端必须按客户端带来的
幂等键去重。历史实现在无 `dispatch_id` 时回落随机键——runtime 文件写恰好命中那条路径，于是每
重试一次云端就多一条参与记录。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from sqlalchemy import select

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifacts
from backend.app.hasn.schema.artifact_contract import ArtifactMutation
from backend.app.hasn.service.artifact_registration_service import artifact_registration_service
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')


def _id(prefix: str) -> str:
    """生成不与真实开发库既有数据冲突的稳定测试标识。"""
    return f'{prefix}_{uuid4().hex[:20]}'


def _local_mutation(
    *,
    owner: str,
    agent: str,
    node_id: str,
    locator: str,
    idempotency_key: str | None,
    action: str = 'create',
    supersedes: str | None = None,
    artifact_kind: str | None = None,
) -> ArtifactMutation:
    """构造一条本地文件产物的登记命令，形状对齐 daemon 侧 sink 的真实上报体。"""
    payload: dict[str, object] = {
        'owner_hasn_id': owner,
        'agent_hasn_id': agent,
        'action': action,
        'source_kind': 'runtime_file',
        'local_locator_key': locator,
        'local_entry_kind': 'file',
        'node_id': node_id,
        'source_tool': 'Write',
        'title': '周报.md',
    }
    if idempotency_key is not None:
        payload['idempotency_key'] = idempotency_key
    if supersedes is not None:
        payload['supersedes_locator_key'] = supersedes
    if artifact_kind is not None:
        payload['artifact_kind'] = artifact_kind
    return ArtifactMutation.model_validate(payload)


async def _contribution_count(owner: str) -> int:
    async with async_db_session() as db:
        rows = (
            await db.execute(
                select(HasnArtifactContributions.contribution_id).where(
                    HasnArtifactContributions.owner_hasn_id == owner
                )
            )
        ).all()
    return len(rows)


async def test_client_idempotency_key_dedupes_replays_without_dispatch_id() -> None:
    """无 dispatch_id 的重放必须只留一条参与记录——这正是 runtime 文件写的常态。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')
    locator = f'locator-v2:{uuid4().hex}'
    key = f'runtime:cap_{uuid4().hex[:10]}:{uuid4().hex}'

    artifact_ids = set()
    for _ in range(3):
        async with async_db_session.begin() as db:
            result = await artifact_registration_service.register(
                db,
                _local_mutation(
                    owner=owner, agent=agent, node_id=node_id, locator=locator, idempotency_key=key
                ),
            )
            artifact_ids.add(result.artifact_id)

    assert len(artifact_ids) == 1, '同一对象键重放必须收敛到同一条当前态'
    assert await _contribution_count(owner) == 1, '同一幂等键重放不得新增参与记录'


async def test_missing_idempotency_key_falls_back_deterministically() -> None:
    """过渡期缺键也不得生成随机键，否则重试就会刷出流水账。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')
    locator = f'locator-v2:{uuid4().hex}'

    for _ in range(3):
        async with async_db_session.begin() as db:
            await artifact_registration_service.register(
                db,
                _local_mutation(
                    owner=owner, agent=agent, node_id=node_id, locator=locator, idempotency_key=None
                ),
            )

    assert await _contribution_count(owner) == 1, '确定性兜底键必须让重放收敛'


async def test_distinct_capture_batches_keep_separate_contributions() -> None:
    """跨轮次的真实再修改各记一条：幂等收敛不能把历史也吞掉。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')
    locator = f'locator-v2:{uuid4().hex}'

    async with async_db_session.begin() as db:
        await artifact_registration_service.register(
            db,
            _local_mutation(
                owner=owner,
                agent=agent,
                node_id=node_id,
                locator=locator,
                idempotency_key=f'runtime:cap_first:{locator}',
            ),
        )
    async with async_db_session.begin() as db:
        await artifact_registration_service.register(
            db,
            _local_mutation(
                owner=owner,
                agent=agent,
                node_id=node_id,
                locator=locator,
                idempotency_key=f'runtime:cap_second:{locator}',
                action='update',
            ),
        )

    assert await _contribution_count(owner) == 2

    async with async_db_session() as db:
        artifacts = (
            await db.execute(select(HasnArtifacts.artifact_id).where(HasnArtifacts.owner_hasn_id == owner))
        ).all()
    assert len(artifacts) == 1, '同一个文件跨轮次修改仍只有一条当前态'


async def test_supersedes_locator_merges_legacy_row_in_place() -> None:
    """节点带上历史定位键时，存量行原地改键并保留参与记录，不留两条同名产物。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')
    legacy_locator = f'legacy-path-v1:{uuid4().hex}'
    new_locator = f'locator-v2:{uuid4().hex}'

    async with async_db_session.begin() as db:
        legacy = await artifact_registration_service.register(
            db,
            _local_mutation(
                owner=owner,
                agent=agent,
                node_id=node_id,
                locator=legacy_locator,
                idempotency_key=f'runtime:cap_legacy:{legacy_locator}',
            ),
        )

    async with async_db_session.begin() as db:
        merged = await artifact_registration_service.register(
            db,
            _local_mutation(
                owner=owner,
                agent=agent,
                node_id=node_id,
                locator=new_locator,
                idempotency_key=f'runtime:cap_new:{new_locator}',
                action='update',
                supersedes=legacy_locator,
            ),
        )

    assert merged.artifact_id == legacy.artifact_id, '归并必须原地改键，不得新建一条产物'

    async with async_db_session() as db:
        rows = (
            await db.execute(
                select(HasnArtifacts.artifact_key, HasnArtifacts.local_locator_key).where(
                    HasnArtifacts.owner_hasn_id == owner
                )
            )
        ).all()
    assert len(rows) == 1, '同一个文件不得留下两条产物'
    assert rows[0].local_locator_key == new_locator
    assert rows[0].artifact_key == f'local:{node_id}:{new_locator}'
    assert await _contribution_count(owner) == 2, '归并不得丢掉历史参与记录'


async def test_supersedes_does_not_clobber_existing_target_row() -> None:
    """新旧两条都已上云时不动存量：改键会撞唯一键，宁可保留历史也不能丢数据。"""
    owner = _id('owner')
    agent = _id('agent')
    node_id = _id('node')
    legacy_locator = f'legacy-path-v1:{uuid4().hex}'
    new_locator = f'locator-v2:{uuid4().hex}'

    for locator in (legacy_locator, new_locator):
        async with async_db_session.begin() as db:
            await artifact_registration_service.register(
                db,
                _local_mutation(
                    owner=owner,
                    agent=agent,
                    node_id=node_id,
                    locator=locator,
                    idempotency_key=f'runtime:cap_{locator}',
                ),
            )

    async with async_db_session.begin() as db:
        await artifact_registration_service.register(
            db,
            _local_mutation(
                owner=owner,
                agent=agent,
                node_id=node_id,
                locator=new_locator,
                idempotency_key=f'runtime:cap_merge:{new_locator}',
                action='update',
                supersedes=legacy_locator,
            ),
        )

    async with async_db_session() as db:
        rows = (
            await db.execute(select(HasnArtifacts.artifact_key).where(HasnArtifacts.owner_hasn_id == owner))
        ).all()
    keys = {row.artifact_key for row in rows}
    assert keys == {f'local:{node_id}:{legacy_locator}', f'local:{node_id}:{new_locator}'}


def test_local_artifact_kind_allows_media_and_rejects_document() -> None:
    """本地对象按打开方式分 image/video/voice/file；正文类只属于 body（设计 §4.1）。"""
    base = {
        'owner_hasn_id': 'h_owner',
        'agent_hasn_id': 'h_agent',
        'action': 'create',
        'source_kind': 'runtime_file',
        'local_locator_key': 'locator-v2:abc',
        'local_entry_kind': 'file',
        'node_id': 'node_a',
    }

    assert ArtifactMutation.model_validate({**base, 'artifact_kind': 'image'}).artifact_kind == 'image'
    assert ArtifactMutation.model_validate(base).artifact_kind == 'file'

    with pytest.raises(ValueError):
        ArtifactMutation.model_validate({**base, 'artifact_kind': 'document'})
