"""knowledge 域模型纯 PG 测试（真实本地 PostgreSQL，不触发引擎）。

覆盖：目录树（同层同名拒/环检测/非空拒删）、维度② grant 三态、owner 行级隔离。
引擎路径（建库/上传/检索）见 test_knowledge_real_e2e.py（真实 RAGFlow）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn_knowledge.model import Document, Folder, Kb
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.exception import errors

pytestmark = pytest.mark.asyncio


def _kb_row(owner_id: str, tag: str) -> Kb:
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
    )


async def test_folder_tree_rules(session):
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()

    root_a = await knowledge_service.create_folder(session, owner, kb.id, name='甲', parent_id=None)
    child = await knowledge_service.create_folder(session, owner, kb.id, name='乙', parent_id=root_a['id'])
    grandchild = await knowledge_service.create_folder(session, owner, kb.id, name='丙', parent_id=child['id'])

    # 同层同名拒绝
    with pytest.raises(errors.ConflictError):
        await knowledge_service.create_folder(session, owner, kb.id, name='甲', parent_id=None)
    # 不同层同名允许
    await knowledge_service.create_folder(session, owner, kb.id, name='甲', parent_id=child['id'])

    # 环检测：甲 移到 丙（自己的子孙）下 → 拒
    with pytest.raises(errors.RequestError):
        await knowledge_service.update_folder(session, owner, root_a['id'], parent_id=grandchild['id'])
    # 移到自身 → 拒
    with pytest.raises(errors.RequestError):
        await knowledge_service.update_folder(session, owner, root_a['id'], parent_id=root_a['id'])

    # 非空目录拒删（含子目录）
    with pytest.raises(errors.ConflictError):
        await knowledge_service.delete_folder(session, owner, child['id'])
    # 含文档拒删
    session.add(
        Document(
            kb_id=kb.id, folder_id=grandchild['id'], owner_id=owner, kind='native', name='占位',
            size_bytes=0, mime_type='text/markdown', content='x', asset_uri=None, current_version=1,
            ragflow_document_id=None, parse_status='parsed', parse_error=None, chunk_count=0,
            source='ui', agent_hasn_id=None,
        )
    )
    await session.flush()
    with pytest.raises(errors.ConflictError):
        await knowledge_service.delete_folder(session, owner, grandchild['id'])

    # 空目录可删；删后同名可重建（软删除外唯一）
    empty = await knowledge_service.create_folder(session, owner, kb.id, name='空目录', parent_id=None)
    await knowledge_service.delete_folder(session, owner, empty['id'])
    await knowledge_service.create_folder(session, owner, kb.id, name='空目录', parent_id=None)

    # 树读取
    folders = await knowledge_service.list_folders(session, owner, kb.id)
    assert {f['name'] for f in folders} >= {'甲', '乙', '丙', '空目录'}


async def test_agent_grant_three_modes(session):
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    agent = f'a_test_{tag}'
    kb1, kb2 = _kb_row(owner, f'{tag}1'), _kb_row(owner, f'{tag}2')
    session.add_all([kb1, kb2])
    await session.flush()

    # 无 grant 行 = inherit（全部库）
    visible = await knowledge_service.resolve_agent_visible_kbs(session, owner, agent)
    assert {kb.id for kb in visible} == {kb1.id, kb2.id}

    # restricted 白名单裁剪
    await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[kb1.id])
    visible = await knowledge_service.resolve_agent_visible_kbs(session, owner, agent)
    assert {kb.id for kb in visible} == {kb1.id}

    # restricted 空白名单 = 交集空即拒
    await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[])
    with pytest.raises(KnowledgeProviderError) as exc_info:
        await knowledge_service.resolve_agent_visible_kbs(session, owner, agent)
    assert exc_info.value.code == 'knowledge_grant_denied'

    # denied 拒
    await knowledge_service.put_agent_grant(session, owner, agent, mode='denied', kb_ids=[])
    with pytest.raises(KnowledgeProviderError) as exc_info:
        await knowledge_service.resolve_agent_visible_kbs(session, owner, agent)
    assert exc_info.value.code == 'knowledge_grant_denied'

    # 白名单不接受不属于 owner 的库
    with pytest.raises(errors.RequestError):
        await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[999999999])

    # 回到 inherit
    await knowledge_service.put_agent_grant(session, owner, agent, mode='inherit', kb_ids=[])
    visible = await knowledge_service.resolve_agent_visible_kbs(session, owner, agent)
    assert len(visible) == 2


async def test_owner_row_isolation(session):
    tag = uuid.uuid4().hex[:8]
    owner_a, owner_b = f'h_a_{tag}', f'h_b_{tag}'
    kb = _kb_row(owner_a, tag)
    session.add(kb)
    await session.flush()

    # B 看不到 A 的库；按「不存在」处理
    assert await knowledge_service.list_kbs(session, owner_b) == []
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.list_folders(session, owner_b, kb.id)
    with pytest.raises(errors.NotFoundError):
        await knowledge_service.list_documents(session, owner_b, kb.id)

    # A 自己可见
    kbs = await knowledge_service.list_kbs(session, owner_a)
    assert [k['id'] for k in kbs] == [kb.id]


async def test_native_version_bookkeeping_without_engine(session):
    """版本记账（纯 PG 断言；引擎同步状态由真实 E2E 验证，这里只看 PG 权威侧不丢正文）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()

    doc = await knowledge_service.create_native_document(
        session, owner, kb.id, title='笔记', content='第一版正文', source='ui'
    )
    assert doc['current_version'] == 1
    assert doc['content'] == '第一版正文'

    updated = await knowledge_service.update_native_document(
        session, owner, doc['id'], content='第二版正文', source='ui'
    )
    assert updated['current_version'] == 2

    versions = await knowledge_service.list_versions(session, owner, doc['id'])
    assert [v['version_no'] for v in versions] == [2, 1]

    restored = await knowledge_service.restore_version(session, owner, doc['id'], 1)
    assert restored['current_version'] == 3
    assert restored['content'] == '第一版正文'

    # 仅移动目录不产生新版本
    folder = await knowledge_service.create_folder(session, owner, kb.id, name='归档', parent_id=None)
    moved = await knowledge_service.update_native_document(session, owner, doc['id'], folder_id=folder['id'])
    assert moved['current_version'] == 3
    assert moved['folder_id'] == folder['id']
