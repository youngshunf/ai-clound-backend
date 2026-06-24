"""knowledge 域模型纯 PG 测试（真实本地 PostgreSQL，不触发引擎）。

覆盖：目录树（同层同名拒/环检测/非空拒删）、维度② grant 三态、owner 行级隔离。
引擎路径（建库/上传/检索）见 test_knowledge_real_e2e.py（真实 RAGFlow）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn.model.hasn_assets import HasnAssets
from backend.app.hasn_knowledge.model import Document, Kb
from backend.app.hasn_knowledge.service import tool_handlers
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


def _agent(owner_id: str, agent_id: str, scopes: list[str] | None = None) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='测试分身',
        owner_hasn_id=owner_id,
        owner_user_id=1,
        scopes=scopes or ['knowledge:read', 'knowledge:write', 'knowledge:upload'],
        session_uuid=f's-{agent_id}',
        expire_time=timezone.now(),
    )


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


async def test_folder_tree_rules(session) -> None:
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


async def test_agent_grant_three_modes(session) -> None:
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


async def test_owner_row_isolation(session) -> None:
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


async def test_native_version_bookkeeping_without_engine(session) -> None:
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


async def test_empty_native_document_is_not_indexed(session) -> None:
    """空白原生文档（如「新建文档」初始态）不推引擎、不误报索引失败：直接 parsed/0 chunks。

    回归：旧版无条件推空正文给 RAGFlow，引擎解析空文档必失败 → UI 一建文档就「正在重新
    索引…」隔会儿「索引失败」。修复后空正文不触引擎（无 ragflow_document_id 可删），纯 PG 置 parsed。
    """
    tag = uuid.uuid4().hex[:8]
    owner = f'h_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()

    # 空正文（「无标题文档」+ 空白正文）→ 不触引擎、直接就绪。
    blank = await knowledge_service.create_native_document(
        session, owner, kb.id, title='无标题文档', content='', source='ui'
    )
    assert blank['parse_status'] == 'parsed'
    assert blank['chunk_count'] == 0
    doc = await session.get(Document, blank['id'])
    assert doc.ragflow_document_id is None  # 从未推过引擎

    # 仅空白（空格/换行）也视为空，同样不触引擎。
    whitespace = await knowledge_service.create_native_document(
        session, owner, kb.id, title='占位', content='  \n\t ', source='ui'
    )
    assert whitespace['parse_status'] == 'parsed'
    assert whitespace['chunk_count'] == 0


async def test_agent_folder_crud_via_handlers(session) -> None:
    """分身经工具 handler 维护目录：建/列/重命名/移动/删（全套 CRUD，inherit 全可达）。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = f'h_test_{tag}', f'a_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()
    a = _agent(owner, agent)

    created = await tool_handlers.handle_knowledge_create_folder(session, a, {'kb_id': kb.id, 'name': '甲'})
    assert created['name'] == '甲'
    assert created['parent_id'] is None

    child = await tool_handlers.handle_knowledge_create_folder(
        session, a, {'kb_id': kb.id, 'name': '乙', 'parent_id': created['id']}
    )
    assert child['parent_id'] == created['id']

    listed = await tool_handlers.handle_knowledge_list_folders(session, a, {'kb_id': kb.id})
    assert {f['name'] for f in listed['folders']} >= {'甲', '乙'}

    renamed = await tool_handlers.handle_knowledge_update_folder(
        session, a, {'folder_id': created['id'], 'name': '甲改'}
    )
    assert renamed['name'] == '甲改'

    moved = await tool_handlers.handle_knowledge_update_folder(
        session, a, {'folder_id': child['id'], 'move_to_root': True}
    )
    assert moved['parent_id'] is None

    deleted = await tool_handlers.handle_knowledge_delete_folder(session, a, {'folder_id': child['id']})
    assert deleted['deleted'] is True
    # 非空目录拒删（甲改 仍是占位无文档但无子——这里删空的 甲改 应成功）
    await tool_handlers.handle_knowledge_delete_folder(session, a, {'folder_id': created['id']})


async def test_agent_folder_ops_gated_by_grant(session) -> None:
    """目录工具走维度② 可达性闸门：restricted 白名单外的库一律拒（建/列都拒）。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = f'h_test_{tag}', f'a_test_{tag}'
    kb1, kb2 = _kb_row(owner, f'{tag}1'), _kb_row(owner, f'{tag}2')
    session.add_all([kb1, kb2])
    await session.flush()
    await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[kb1.id])
    a = _agent(owner, agent)

    # kb1 可达：建目录成功
    ok = await tool_handlers.handle_knowledge_create_folder(session, a, {'kb_id': kb1.id, 'name': '甲'})
    assert ok['name'] == '甲'
    # kb2 不可达：建目录 / 列目录都拒
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_create_folder(session, a, {'kb_id': kb2.id, 'name': '甲'})
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_list_folders(session, a, {'kb_id': kb2.id})


async def test_agent_list_documents_via_handler(session) -> None:
    """分身经 list_documents 工具列文档（纯 PG：直插 Document 行，不触引擎）。

    folder_id 省略=全库 / 0=库根 / >0=指定目录（与 service 语义一致）。
    """
    tag = uuid.uuid4().hex[:8]
    owner, agent = f'h_test_{tag}', f'a_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()
    a = _agent(owner, agent)

    folder = await knowledge_service.create_folder(session, owner, kb.id, name='归档', parent_id=None)
    # 库根一篇（folder_id=None，库根哨兵 0 在 service 里映射为 folder_id IS NULL）+ 目录里一篇（直插，绕过引擎）。
    session.add_all(
        [
            Document(
                kb_id=kb.id, folder_id=None, owner_id=owner, kind='native', name='根文档',
                size_bytes=0, mime_type='text/markdown', content='根', asset_uri=None, current_version=1,
                ragflow_document_id=None, parse_status='parsed', parse_error=None, chunk_count=0,
                source='ui', agent_hasn_id=None,
            ),
            Document(
                kb_id=kb.id, folder_id=folder['id'], owner_id=owner, kind='native', name='归档文档',
                size_bytes=0, mime_type='text/markdown', content='档', asset_uri=None, current_version=1,
                ragflow_document_id=None, parse_status='parsed', parse_error=None, chunk_count=0,
                source='ui', agent_hasn_id=None,
            ),
        ]
    )
    await session.flush()

    # 省略 folder_id：全库两篇都见。
    all_docs = await tool_handlers.handle_knowledge_list_documents(session, a, {'kb_id': kb.id})
    assert {d['name'] for d in all_docs['documents']} == {'根文档', '归档文档'}
    # 指定目录：只见该目录那篇。
    in_folder = await tool_handlers.handle_knowledge_list_documents(
        session, a, {'kb_id': kb.id, 'folder_id': folder['id']}
    )
    assert {d['name'] for d in in_folder['documents']} == {'归档文档'}
    # 库根（folder_id=0）：只见根那篇。
    in_root = await tool_handlers.handle_knowledge_list_documents(session, a, {'kb_id': kb.id, 'folder_id': 0})
    assert {d['name'] for d in in_root['documents']} == {'根文档'}


async def test_agent_kb_doc_ops_gated_by_grant(session) -> None:
    """kb/文档写工具走维度② 可达性闸门：restricted 白名单外的库一律拒（删库/列文档/删文档）。

    闸门在调引擎前抛 ForbiddenError，故无需真实 RAGFlow（happy path 见真机 E2E）。
    """
    tag = uuid.uuid4().hex[:8]
    owner, agent = f'h_test_{tag}', f'a_test_{tag}'
    kb1, kb2 = _kb_row(owner, f'{tag}1'), _kb_row(owner, f'{tag}2')
    session.add_all([kb1, kb2])
    await session.flush()
    # kb2 不在白名单（仅 kb1 可达）。
    await knowledge_service.put_agent_grant(session, owner, agent, mode='restricted', kb_ids=[kb1.id])
    a = _agent(owner, agent)

    # kb2 直插一篇文档（用于验删文档反查 kb 的闸门）。
    session.add(
        Document(
            kb_id=kb2.id, folder_id=None, owner_id=owner, kind='native', name='不可达文档',
            size_bytes=0, mime_type='text/markdown', content='x', asset_uri=None, current_version=1,
            ragflow_document_id=None, parse_status='parsed', parse_error=None, chunk_count=0,
            source='ui', agent_hasn_id=None,
        )
    )
    await session.flush()
    doc2 = (await knowledge_service.list_documents(session, owner, kb2.id))[0]

    # 不可达库：列文档 / 删库 / 删其文档 全拒（且在触引擎前）。
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_list_documents(session, a, {'kb_id': kb2.id})
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_delete_kb(session, a, {'kb_id': kb2.id})
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_delete_document(session, a, {'doc_id': doc2['id']})

    # 可达库：列文档放行（kb1 暂无文档 → 空列表，不抛）。
    ok = await tool_handlers.handle_knowledge_list_documents(session, a, {'kb_id': kb1.id})
    assert ok['documents'] == []


async def test_agent_create_kb_rejects_blank_name(session) -> None:
    """create_kb 空名（纯空白）在触引擎前如实拒（RequestError），不产出空库。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = f'h_test_{tag}', f'a_test_{tag}'
    a = _agent(owner, agent)
    with pytest.raises(errors.RequestError):
        await tool_handlers.handle_knowledge_create_kb(session, a, {'name': '   '})


async def test_agent_upload_asset_rejections(session) -> None:
    """asset_uri 上传的纯逻辑闸门：二选一约束 + 资产不存在 + 越权他人资产（happy path 走真机 E2E）。"""
    tag = uuid.uuid4().hex[:8]
    owner, other, agent = f'h_test_{tag}', f'h_other_{tag}', f'a_test_{tag}'
    kb = _kb_row(owner, tag)
    session.add(kb)
    await session.flush()
    a = _agent(owner, agent)

    # 二选一：都不给 → 拒
    with pytest.raises(errors.RequestError):
        await tool_handlers.handle_knowledge_upload_document(session, a, {'kb_id': kb.id, 'title': 'x'})
    # 二选一：都给 → 拒
    with pytest.raises(errors.RequestError):
        await tool_handlers.handle_knowledge_upload_document(
            session, a, {'kb_id': kb.id, 'title': 'x', 'content_text': 'y', 'asset_uri': 'hasn://asset/whatever'}
        )
    # 资产不存在 → 按「不存在」
    with pytest.raises(errors.NotFoundError):
        await tool_handlers.handle_knowledge_upload_document(
            session, a, {'kb_id': kb.id, 'title': 'x', 'asset_uri': 'hasn://asset/nonexist'}
        )
    # 别人主人名下的资产 → 越权拒（防把别人的文件塞进自己库）
    asset = HasnAssets(
        asset_id=f'ast_{tag}', owner_hasn_id=other, access='private', storage_id=1,
        object_key='k', kind='file', mime='application/pdf', size_bytes=10,
    )
    session.add(asset)
    await session.flush()
    with pytest.raises(errors.ForbiddenError):
        await tool_handlers.handle_knowledge_upload_document(
            session, a, {'kb_id': kb.id, 'title': 'x', 'asset_uri': f'hasn://asset/ast_{tag}'}
        )
