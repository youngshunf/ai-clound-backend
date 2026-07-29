"""知识库容器级挂靠平台项目（doc38 层2 / 实施/97 轴 A）真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑注册表 + knowledge service；直接构造 Kb/Document 行
（不触 RAGFlow，引擎路径见 test_knowledge_real_e2e.py），事务回滚不污染库。

覆盖：
- **adapter 注册**（A-C2）：`knowledge/kbs` 在注册表、容器级、展示元数据齐全；
- **link/unlink**（A-C2）：经注册表落 `kb.platform_project_id`，跨 owner 404、已软删库 404、
  摘错项目 409；
- **并集读子产物钩子**（A-C4）：挂靠库 → 项目产物流同时含「库产物」与「库内文档产物」，
  摘出后即时消失（读时派生不回填）；
- **读侧口径**（A-C3 / doc38 §5.6）：`list_kbs`/`list_accessible_kbs` 缺省**不收窄**、传参才过滤，
  每行回带 `platform_project_id`；工具面 `list_datasets`/`search` 同口径；
- **建库归属校验**（A-C7）：非本主人项目 → 404，空 → 不挂。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_artifact_contributions import HasnArtifactContributions
from backend.app.hasn_knowledge.model import Document, Kb

# 注册模块 import 副作用：确保 knowledge 容器 adapter 已进注册表（生产由 ai_native_app_registry 加载）
from backend.app.hasn_knowledge.service import project_linkage as _knowledge_project_linkage  # noqa: F401
from backend.app.hasn_knowledge.service import tool_handlers
from backend.app.hasn_knowledge.service.knowledge_service import Subject, knowledge_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _agent(owner_id: str, agent_id: str) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='测试分身',
        owner_hasn_id=owner_id,
        owner_user_id=1,
        session_uuid=f's-{agent_id}',
        expire_time=timezone.now(),
    )


def _kb_row(owner_id: str, tag: str, *, project_id: uuid.UUID | None = None) -> Kb:
    return Kb(
        owner_id=owner_id,
        scope='personal',
        enterprise_id=None,
        name=f'测试库-{tag}',
        description=None,
        ragflow_dataset_id=f'ds-{tag}',
        embedding_model='test@OpenAI',
        document_count=0,
        chunk_count=0,
        status='active',
        platform_project_id=project_id,
    )


def _doc_row(owner_id: str, kb_id: int, tag: str) -> Document:
    return Document(
        kb_id=kb_id,
        folder_id=None,
        owner_id=owner_id,
        kind='native',
        name=f'文档-{tag}',
        size_bytes=0,
        mime_type='text/markdown',
        content='正文',
        current_version=1,
        parse_status='parsed',
        chunk_count=0,
        source='agent',
    )


async def _seed_project(session, owner: str, name: str = '测试项目') -> HasnProject:
    project = HasnProject(owner_id=owner, name=name, status='active')
    session.add(project)
    await session.flush()
    return project


async def _seed_resource_artifact(session, *, owner: str, agent: str, resource_uri: str) -> HasnArtifacts:
    """插一条指向某资源 URI 的产物行（并集读命中的就是这类行）。"""
    artifact = HasnArtifacts(
        artifact_id=f'art_{uuid.uuid4().hex[:16]}',
        agent_hasn_id=agent,
        owner_hasn_id=owner,
        # owner 维度唯一键：同一 owner 下不能重复（真实登记用 (agent,dispatch,resource_uri) 派生）
        artifact_key=f'{resource_uri}#{uuid.uuid4().hex[:8]}',
        artifact_kind='resource',
        kind='resource',
        title=f'产物 {resource_uri}',
        resource_uri=resource_uri,
        source_kind='app_write',
        action='create',
        status='active',
    )
    session.add(artifact)
    await session.flush()
    session.add(
        HasnArtifactContributions(
            contribution_id=f'con_{uuid.uuid4().hex[:20]}',
            artifact_id=artifact.artifact_id,
            owner_hasn_id=owner,
            agent_hasn_id=agent,
            action='create',
            source_kind='app_write',
            idempotency_key=f'test:{uuid.uuid4().hex}',
        )
    )
    await session.flush()
    return artifact


async def test_knowledge_container_adapter_registered() -> None:
    """A-C2：知识库容器 adapter 已注册且元数据齐全（缺 title_column → 总览行显示裸 URI）。"""
    adapter = project_linkage_registry.get('knowledge/kbs')
    assert adapter is not None, 'knowledge/kbs adapter 未注册'
    assert adapter.is_container is True
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.id_is_uuid is False, 'kb.id 是 bigserial，不能按 UUID 解析'
    assert adapter.owner_column == 'owner_id'
    assert adapter.app_id == 'knowledge'
    assert adapter.title_column == 'name'
    assert adapter.related_resource_uris is not None, '缺子产物钩子 → 库内文档不进项目产物流'
    assert adapter in project_linkage_registry.container_adapters()


async def test_link_unlink_kb_via_registry(session) -> None:
    """A-C2：link/unlink 经注册表唯一收口落挂靠列；摘错项目 409；重复摘除幂等。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    project = await _seed_project(session, owner)
    other_project = await _seed_project(session, owner, name='另一个项目')
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()

    uri = f'hasn://knowledge/kbs/{kb.id}'
    result = await project_linkage_registry.link(session, owner=owner, resource_uri=uri, project_id=project.id)
    assert result['linked'] is True and result['changed'] is True
    await session.refresh(kb)
    assert kb.platform_project_id == project.id

    # 摘错项目 → 409（防误摘别的项目下的资源）
    with pytest.raises(errors.ConflictError):
        await project_linkage_registry.unlink(
            session, owner=owner, resource_uri=uri, project_id=other_project.id
        )

    unlinked = await project_linkage_registry.unlink(session, owner=owner, resource_uri=uri, project_id=project.id)
    assert unlinked['unlinked'] is True and unlinked['changed'] is True
    await session.refresh(kb)
    assert kb.platform_project_id is None

    # 已摘除后重放保持幂等（changed=False）
    again = await project_linkage_registry.unlink(session, owner=owner, resource_uri=uri)
    assert again['unlinked'] is True and again['changed'] is False


async def test_link_cross_owner_and_deleted_kb_404(session) -> None:
    """A-C2：跨 owner 挂不进（404）；已软删的库也挂不进（404，不复活 tombstone）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    intruder = f'h_other_{tag}'
    project = await _seed_project(session, owner)
    kb = _kb_row(owner, tag)
    deleted_kb = _kb_row(owner, f'{tag}-del')
    deleted_kb.deleted_time = timezone.now()
    session.add_all([kb, deleted_kb])
    await session.flush()

    with pytest.raises(errors.NotFoundError):
        await project_linkage_registry.link(
            session, owner=intruder, resource_uri=f'hasn://knowledge/kbs/{kb.id}', project_id=project.id
        )
    with pytest.raises(errors.NotFoundError):
        await project_linkage_registry.link(
            session, owner=owner, resource_uri=f'hasn://knowledge/kbs/{deleted_kb.id}', project_id=project.id
        )


async def test_project_artifact_flow_includes_kb_documents(session) -> None:
    """A-C4：挂靠库 → 产物流并集含「库产物 + 库内文档产物」；摘出后即时消失（读时派生）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    agent = f'a_{tag}'
    project = await _seed_project(session, owner)
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()
    doc = _doc_row(owner, kb.id, tag)
    session.add(doc)
    await session.flush()

    kb_uri = f'hasn://knowledge/kbs/{kb.id}'
    doc_uri = f'hasn://knowledge/documents/{doc.id}'
    await _seed_resource_artifact(session, owner=owner, agent=agent, resource_uri=kb_uri)
    await _seed_resource_artifact(session, owner=owner, agent=agent, resource_uri=doc_uri)

    # 未挂靠：两条产物都不属于该项目
    before = await project_service.project_artifact_flow(session, owner=owner, project_id=project.id)
    assert not [row for row in before['items'] if row.get('resource_uri') in {kb_uri, doc_uri}]

    await project_linkage_registry.link(session, owner=owner, resource_uri=kb_uri, project_id=project.id)
    after_flow = await project_service.project_artifact_flow(session, owner=owner, project_id=project.id)
    after = {row.get('resource_uri') for row in after_flow['items']}
    assert kb_uri in after, '挂靠后库本体产物应进并集'
    assert doc_uri in after, '缺 related_resource_uris 钩子 → 库内文档产物进不了项目产物流'

    # 挂靠资源区同时列出该库（title 取库名，不是裸 URI）
    linked = await project_linkage_registry.list_linked_resources(session, owner=owner, project_id=project.id)
    assert any(item['resource_uri'] == kb_uri and item['title'] == kb.name for item in linked)

    await project_linkage_registry.unlink(session, owner=owner, resource_uri=kb_uri, project_id=project.id)
    restored_flow = await project_service.project_artifact_flow(session, owner=owner, project_id=project.id)
    restored = {row.get('resource_uri') for row in restored_flow['items']}
    assert kb_uri not in restored and doc_uri not in restored, '摘出后并集应即时不含（读时派生不回填）'


async def test_deleted_kb_not_in_linked_resources(session) -> None:
    """A-C2：软删的库即使残留挂靠值也不进挂靠资源区/并集读（knowledge 软删走 deleted_time）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    project = await _seed_project(session, owner)
    kb = _kb_row(owner, tag, project_id=project.id)
    kb.deleted_time = timezone.now()
    session.add(kb)
    await session.flush()

    linked = await project_linkage_registry.list_linked_resources(session, owner=owner, project_id=project.id)
    assert not [item for item in linked if item['resource_uri'].endswith(f'/{kb.id}')]


async def test_list_kbs_read_scope_and_project_echo(session) -> None:
    """A-C3 / doc38 §5.6：读侧缺省不收窄、传参才过滤，且每行回带 platform_project_id。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    project = await _seed_project(session, owner)
    attached = _kb_row(owner, f'{tag}-in', project_id=project.id)
    free = _kb_row(owner, f'{tag}-out')
    session.add_all([attached, free])
    await session.flush()

    every = await knowledge_service.list_kbs(session, owner)
    ids = {row['id'] for row in every}
    assert {attached.id, free.id} <= ids, '缺省必须列全部（未挂靠的长期库不能被默认收窄掉）'
    echoed = {row['id']: row['platform_project_id'] for row in every}
    assert echoed[attached.id] == str(project.id)
    assert echoed[free.id] is None

    scoped = await knowledge_service.list_kbs(session, owner, platform_project_id=str(project.id))
    assert {row['id'] for row in scoped} == {attached.id}

    accessible = await knowledge_service.list_accessible_kbs(
        session, subject=Subject.human(owner), platform_project_id=str(project.id)
    )
    assert {row['id'] for row in accessible} == {attached.id}
    assert await knowledge_service.list_kbs(session, owner, platform_project_id=str(uuid.uuid4())) == []

    with pytest.raises(errors.RequestError):
        await knowledge_service.list_kbs(session, owner, platform_project_id='不是-uuid')


async def test_tool_list_datasets_scope_and_echo(session) -> None:
    """A-C3：工具面同口径——缺省列全部并回带归属；传项目才收窄。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    agent = _agent(owner, f'a_{tag}')
    project = await _seed_project(session, owner)
    attached = _kb_row(owner, f'{tag}-in', project_id=project.id)
    free = _kb_row(owner, f'{tag}-out')
    session.add_all([attached, free])
    await session.flush()

    every = await tool_handlers.handle_knowledge_list_datasets(session, agent, {})
    rows = {row['id']: row['platform_project_id'] for row in every['datasets']}
    assert {attached.id, free.id} <= set(rows)
    assert rows[attached.id] == str(project.id) and rows[free.id] is None

    scoped = await tool_handlers.handle_knowledge_list_datasets(
        session, agent, {'platform_project_id': str(project.id)}
    )
    assert {row['id'] for row in scoped['datasets']} == {attached.id}


async def test_tool_search_empty_project_scope_is_honest(session) -> None:
    """A-C3：按项目收窄且该项目无可达库时如实回空，绝不退化成全库检索（零 fake）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    agent = _agent(owner, f'a_{tag}')
    session.add(_kb_row(owner, tag))
    await session.flush()

    result = await tool_handlers.handle_knowledge_search(
        session, agent, {'query': '随便问点什么', 'platform_project_id': str(uuid.uuid4())}
    )
    assert result == {'chunks': [], 'total': 0, 'kb_count': 0}


async def test_create_kb_project_ownership_guard(session) -> None:
    """A-C7：建库挂项目前必过归属校验——非本主人项目 404、非法 UUID 400、空则不挂。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    stranger = f'h_other_{tag}'
    foreign = await _seed_project(session, stranger, name='别人的项目')
    mine = await _seed_project(session, owner)

    resolve = knowledge_service._resolve_owned_project_id
    assert await resolve(session, owner_id=owner, platform_project_id=None) is None
    assert await resolve(session, owner_id=owner, platform_project_id='   ') is None
    assert await resolve(session, owner_id=owner, platform_project_id=str(mine.id)) == mine.id

    with pytest.raises(errors.NotFoundError):
        await resolve(session, owner_id=owner, platform_project_id=str(foreign.id))
    with pytest.raises(errors.RequestError):
        await resolve(session, owner_id=owner, platform_project_id='not-a-uuid')
