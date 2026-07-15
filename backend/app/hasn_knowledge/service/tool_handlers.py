"""knowledge App 工具 handlers（gateway_internal，注册于 ai_native_runtime_gateway internal_handlers）。

签名约定：async (db, agent: AgentTokenPayload, input_payload: dict) -> dict。
- 维度①（scope 三态）由云端 Gateway 单点强制，handler 不重复实现；
- 维度②（kb 白名单）+ 行级 owner 隔离在此/或 service 内强制；
- Agent 身份恒取自 JWT claims（agent.agent_hasn_id / agent.owner_hasn_id），绝不读 payload 身份字段。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.3/§2.4/§3。
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any

from backend.app.hasn_knowledge.service import resource_adapter as _resource_adapter  # noqa: F401  # G6 使用点注册兜底
from backend.app.hasn_knowledge.service.error_adapter import to_http_error
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.app.mcp.context import get_authorized_resource, get_current_work_session_id
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

logger = logging.getLogger(__name__)


async def _register_knowledge_artifact(
    db: AsyncSession,
    agent: AgentTokenPayload,
    *,
    resource_kind: str,
    server_id: int,
    title: str,
    source_tool: str,
) -> None:
    """register-on-write（doc31 产物自动登记铁律）：分身**每个知识库写点**都登记进 `hasn_artifacts`。

    治「分身建了库、写了文档，工作会话资源栏 / 分身产物 tab 却什么都看不到」——主人只知道分身"动过"，
    不知道产出了什么。库与文档各是独立产物（`resource_kind` 二选一），文档逐篇可见、可单独打开。

    - `session_id` 经 `get_current_work_session_id()` 取：本 handler 走 **AI-Native 应用工具面**
      （`ai_native_runtime_gateway` 分发，只收 `AgentTokenPayload`、拿不到 `AgentContext`），故只能经
      ContextVar 通道拿系统注入的 `_hasn_session_id`。None = 主会话直调，产物仍凭 resource_uri 进产物 tab；
    - `server_id` = 云端权威 int id（知识库无本地 ULID，Core-08：本地 id 永不上 URI）；
    - 幂等 UPSERT（键 `(agent, dispatch_id, resource_uri)`）——反复写不重复登记、会话归属只进不退。

    best-effort：登记失败**绝不**拖垮知识库写本身（写已在同一事务，抛出会连累落库）。
    """
    try:
        from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
        from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

        descriptor = ai_native_app_registry.resource_descriptor('knowledge', resource_kind)
        if descriptor is None:
            logger.warning('[knowledge] 缺 %s 资源描述符，跳过产物登记', resource_kind)
            return
        await HasnArtifactsService.record_app_resource_artifact(
            db,
            descriptor=descriptor,
            server_id=str(server_id),
            session_id=get_current_work_session_id(),
            agent_hasn_id=agent.agent_hasn_id,
            owner_hasn_id=agent.owner_hasn_id,
            title=title,
            source_tool=source_tool,
        )
    except Exception as e:
        logger.warning('[knowledge] register-on-write 登记 hasn_artifacts 失败（非致命）: %s', e)


def _resource_owner(param: str, agent: AgentTokenPayload) -> tuple[str, bool]:
    """取「该资源实例的权威 owner」+「G6 门是否已判过」。

    G6 门（MCP 直连面 / daemon 代理面）判过 → 经 ContextVar 拿到已判权资源，其 `owner_hasn_id` 是资源
    **真实 owner**（分享场景下即库主人 A，非调用分身的主人 B）；handler 用它委托 `owner_id` keyed 旧方法
    （文档/目录行 owner_id 恒等所属库 owner，被分享者新建行也随库主人归属，见 knowledge_service §复用纪律）。

    门没跑（knowledge agent REST 面 S5 待接，门只在两个 MCP 分发入口生效）→ 回落分身主人 + 由调用方
    补跑 `_assert_kb_reachable` 兜底可达性（= G6 前既有行为，零回归）。返回 (owner_hasn_id, gate_ran)。
    """
    authorized = get_authorized_resource(param)
    if authorized is not None:
        return authorized.owner_hasn_id, True
    return agent.owner_hasn_id, False


async def handle_knowledge_search(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.search：检索主人可见知识库（维度② 在 service 内裁剪）。"""
    kb_ids_raw = input_payload.get('kb_ids')
    kb_ids = [int(i) for i in kb_ids_raw] if kb_ids_raw else None
    try:
        return await knowledge_service.search(
            db,
            agent.owner_hasn_id,
            question=str(input_payload['query']),
            kb_ids=kb_ids,
            top_k=min(int(input_payload.get('limit') or 8), 50),
            similarity_threshold=input_payload.get('similarity_threshold'),
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc


async def handle_knowledge_list_datasets(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_datasets：列出分身可达的知识库（维度② 裁剪后）。"""
    try:
        kbs = await knowledge_service.resolve_agent_visible_kbs(db, agent.owner_hasn_id, agent.agent_hasn_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {
        'datasets': [
            {
                'id': kb.id,
                'name': kb.name,
                'description': kb.description,
                'document_count': kb.document_count,
                'chunk_count': kb.chunk_count,
            }
            for kb in kbs
        ]
    }


async def _assert_kb_reachable(db: AsyncSession, agent: AgentTokenPayload, kb_id: int) -> None:
    try:
        visible = await knowledge_service.resolve_agent_visible_kbs(db, agent.owner_hasn_id, agent.agent_hasn_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    if kb_id not in {kb.id for kb in visible}:
        raise errors.ForbiddenError(msg='knowledge_grant_denied: 该知识库不在分身可达范围内')


async def handle_knowledge_fetch_doc(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.fetch_doc：返回解析后文本（native=PG 正文；file=引擎分块文本），非二进制。"""
    doc_id = int(input_payload['doc_id'])
    owner, gate_ran = _resource_owner('doc_id', agent)
    doc = await knowledge_service.get_document(db, owner, doc_id)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    if doc['kind'] == 'native':
        return doc
    try:
        full = await knowledge_service.fetch_file_doc_text(db, owner, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return full


async def handle_knowledge_upload_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.upload_document：content_text(纯文本) 或 asset_uri(已在私有桶的真实文件) 二选一上传并索引。

    - content_text（纯文本内容）→ **一律落原生文档**（可编辑/有版本/Markdown 渲染/在线预览最好/正文逐字保留，
      且同样推进引擎索引可检索）。知识库铁律「原生优先，能不落 file 就不落」：正文超 5000 字**不再自动回落 file**，
      而是如实拒绝，逼分身拆成多篇更聚焦的原生文档 + 深链 hasn://knowledge/documents/{doc_id} 互连
      （file 编辑成本高，原生才可编辑）。
    - asset_uri(hasn://asset/...) → 取桶字节建 file 文档副本（真实二进制文件，如 PDF/docx/图片——
      这类才是 file 文档的正当来源；资产须属同一主人，越权如实拒）。
    """
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    title = str(input_payload['title']).strip() or 'untitled'
    folder_id = input_payload.get('folder_id')
    folder_id_int = int(folder_id) if folder_id is not None else None
    content_text = input_payload.get('content_text')
    asset_uri = input_payload.get('asset_uri')
    if bool(content_text) == bool(asset_uri):
        raise errors.RequestError(msg='content_text 与 asset_uri 必须二选一')
    try:
        if asset_uri:
            result = await knowledge_service.upload_asset_document(
                db,
                owner,
                kb_id,
                asset_uri=str(asset_uri),
                title=title,
                folder_id=folder_id_int,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
        else:
            # 原生优先（知识库铁律）：纯文本内容一律落原生文档，与 write_doc 语义一致。
            # 超 5000 字**不再自动回落 file**——create_native_document 内的 _validate_native_content
            # 会如实拒绝并引导拆成多篇 + 深链互连（file 编辑成本高，原生才可编辑）。
            result = await knowledge_service.create_native_document(
                db,
                owner,
                kb_id,
                title=title,
                content=str(content_text),
                folder_id=folder_id_int,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
            # 不回显整段正文（避免灌爆分身上下文），与 write_doc 出参对齐。
            result.pop('content', None)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await _register_knowledge_artifact(
        db,
        agent,
        resource_kind='knowledge.document',
        server_id=int(result['id']),
        title=title,
        source_tool='hasn.knowledge.upload_document',
    )
    return result


# ---------- 知识库（kb）+ 文档：分身替主人维护库与文档（建库/删库/列文档/删文档）----------


async def handle_knowledge_create_kb(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.create_kb：替主人新建知识库（库归主人 owner_hasn_id 所有；inherit 默认即可达）。

    封面为必填（manifest required）：分身建库须先配好一张封面资产（优先素材搜索 → 其次生图 → 兜底自画 SVG，
    都落私有桶得 hasn://asset），再随建库入参 `cover_asset_uri` 一并落库；列表卡据此展示封面。
    """
    name = str(input_payload['name']).strip()
    if not name:
        raise errors.RequestError(msg='知识库名称不能为空')
    cover_asset_uri = str(input_payload.get('cover_asset_uri') or '').strip()
    if not cover_asset_uri.startswith('hasn://asset/'):
        raise errors.RequestError(
            msg='封面为必填：请先用素材搜索/生图工具配一张图（或据主题自画 SVG）落桶，'
            '再以 hasn://asset/{id} 作为 cover_asset_uri 传入'
        )
    description = input_payload.get('description')
    try:
        kb = await knowledge_service.create_kb(
            db,
            agent.owner_hasn_id,
            name=name,
            description=str(description).strip() if description else None,
            cover_asset_uri=cover_asset_uri,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await _register_knowledge_artifact(
        db,
        agent,
        resource_kind='knowledge.base',
        server_id=int(kb['id']),
        title=name,
        source_tool='hasn.knowledge.create_kb',
    )
    return kb


async def handle_knowledge_update_kb(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.update_kb：改主人既有库的库名/描述/封面（典型：建库后补一张封面）；G6 门 manager 档判权后改。

    派发建库时 daemon 会先建好一个无封面的空库交给分身，分身配好封面后经此工具 `cover_asset_uri` 回填。
    只改传入的字段（None=不动），至少要给其一，否则空更新如实拒。
    """
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    name = input_payload.get('name')
    description = input_payload.get('description')
    cover_asset_uri = input_payload.get('cover_asset_uri')
    if name is None and description is None and cover_asset_uri is None:
        raise errors.RequestError(msg='name / description / cover_asset_uri 至少提供其一')
    try:
        kb = await knowledge_service.update_kb(
            db,
            owner,
            kb_id,
            name=str(name).strip() if name is not None else None,
            description=str(description) if description is not None else None,
            cover_asset_uri=str(cover_asset_uri) if cover_asset_uri is not None else None,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    await _register_knowledge_artifact(
        db,
        agent,
        resource_kind='knowledge.base',
        server_id=kb_id,
        title=str(kb.get('name') or '').strip() or '知识库',
        source_tool='hasn.knowledge.update_kb',
    )
    return kb


async def handle_knowledge_delete_kb(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_kb：删主人的整库（级联删文档/目录）；G6 门 manager 档判权后删。"""
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    try:
        await knowledge_service.delete_kb(db, owner, kb_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {'deleted': True, 'kb_id': kb_id}


async def handle_knowledge_list_documents(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_documents：列某可达知识库的文档（folder_id 省略=全库 / 0=库根 / >0=指定目录）。"""
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    folder_id = input_payload.get('folder_id')
    docs = await knowledge_service.list_documents(
        db,
        owner,
        kb_id,
        folder_id=int(folder_id) if folder_id is not None else None,
    )
    return {'documents': docs}


async def handle_knowledge_delete_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_document：删主人知识库中的一篇文档（G6 门 knowledge_doc/editor 档判权）。"""
    doc_id = int(input_payload['doc_id'])
    owner, gate_ran = _resource_owner('doc_id', agent)
    doc = await knowledge_service.get_document(db, owner, doc_id)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    try:
        await knowledge_service.delete_document(db, owner, doc_id)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    return {'deleted': True, 'doc_id': doc_id}


async def handle_knowledge_move_document(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.move_document：把一篇已存在文档移进目录 / 移回库根（native 与 file 均可）。

    只改文档所属目录，不改标题/正文、不重建索引；`folder_id`（移进该目录）与 `move_to_root`（移回库根）
    二选一必须给其一。按 doc_id 反查所属 kb 做可达性闸门（维度②越权如实拒）。
    """
    doc_id = int(input_payload['doc_id'])
    owner, gate_ran = _resource_owner('doc_id', agent)
    doc = await knowledge_service.get_document(db, owner, doc_id)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, int(doc['kb_id']))
    folder_id = input_payload.get('folder_id')
    move_to_root = bool(input_payload.get('move_to_root'))
    if folder_id is None and not move_to_root:
        raise errors.RequestError(msg='folder_id 与 move_to_root 必须提供其一（移进目录或移回库根）')
    try:
        result = await knowledge_service.update_native_document(
            db,
            owner,
            doc_id,
            folder_id=int(folder_id) if folder_id is not None else None,
            move_to_root=move_to_root,
            source='agent',
            agent_hasn_id=agent.agent_hasn_id,
        )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    # 移动是纯归属变更，不回传正文（native 文档 _document_dict 会带 content，剔除避免噪声）。
    result.pop('content', None)
    # 移动也登记：KBDISP 整理会话的主要动作就是把文档归位，不登记则整理会话资源栏空空如也
    # （幂等 UPSERT，同一文档反复动只一条 active 行）。
    await _register_knowledge_artifact(
        db,
        agent,
        resource_kind='knowledge.document',
        server_id=doc_id,
        title=str(result.get('name') or doc.get('name') or '').strip() or '文档',
        source_tool='hasn.knowledge.move_document',
    )
    return {'moved': True, **result}


# ---------- 目录（folder）：分身帮主人维护知识库目录树（全套 CRUD）----------


async def handle_knowledge_list_folders(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.list_folders：列某可达知识库的目录树（平铺，按 parent_id 组树）。"""
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    folders = await knowledge_service.list_folders(db, owner, kb_id)
    return {'folders': folders}


async def handle_knowledge_create_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.create_folder：在可达知识库里新建目录。"""
    kb_id = int(input_payload['kb_id'])
    owner, gate_ran = _resource_owner('kb_id', agent)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, kb_id)
    parent_id = input_payload.get('parent_id')
    return await knowledge_service.create_folder(
        db,
        owner,
        kb_id,
        name=str(input_payload['name']),
        parent_id=int(parent_id) if parent_id is not None else None,
    )


async def handle_knowledge_update_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.update_folder：重命名/移动目录（G6 门 knowledge_folder/editor 档判权）。"""
    folder_id = int(input_payload['folder_id'])
    owner, gate_ran = _resource_owner('folder_id', agent)
    folder = await knowledge_service.get_folder(db, owner, folder_id)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, int(folder['kb_id']))
    parent_id = input_payload.get('parent_id')
    return await knowledge_service.update_folder(
        db,
        owner,
        folder_id,
        name=input_payload.get('name'),
        parent_id=int(parent_id) if parent_id is not None else None,
        move_to_root=bool(input_payload.get('move_to_root')),
    )


async def handle_knowledge_delete_folder(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.delete_folder：删空目录（非空如实拒）；G6 门 knowledge_folder/editor 档判权。"""
    folder_id = int(input_payload['folder_id'])
    owner, gate_ran = _resource_owner('folder_id', agent)
    folder = await knowledge_service.get_folder(db, owner, folder_id)
    if not gate_ran:
        await _assert_kb_reachable(db, agent, int(folder['kb_id']))
    await knowledge_service.delete_folder(db, owner, folder_id)
    return {'deleted': True, 'folder_id': folder_id}


async def handle_knowledge_write_doc(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.write_doc：创建/更新原生文档（D9）；返回 doc_id。

    传 doc_id → 更新既有文档（G6 门按 `doc_id`=knowledge_doc/editor 判权）；否则传 kb_id →
    在库内新建（门按 `kb_id`=knowledge/editor 判权）。两参在声明里均 required=False，门只判实际传入的那个。
    """
    doc_id = input_payload.get('doc_id')
    try:
        if doc_id is not None:
            owner, gate_ran = _resource_owner('doc_id', agent)
            doc = await knowledge_service.get_document(db, owner, int(doc_id))
            if not gate_ran:
                await _assert_kb_reachable(db, agent, int(doc['kb_id']))
            result = await knowledge_service.update_native_document(
                db,
                owner,
                int(doc_id),
                title=input_payload.get('title'),
                content=input_payload.get('content'),
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
        else:
            kb_id = int(input_payload['kb_id'])
            owner, gate_ran = _resource_owner('kb_id', agent)
            if not gate_ran:
                await _assert_kb_reachable(db, agent, kb_id)
            folder_id = input_payload.get('folder_id')
            result = await knowledge_service.create_native_document(
                db,
                owner,
                kb_id,
                title=str(input_payload['title']),
                content=str(input_payload['content']),
                folder_id=int(folder_id) if folder_id is not None else None,
                source='agent',
                agent_hasn_id=agent.agent_hasn_id,
            )
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
    result.pop('content', None)
    await _register_knowledge_artifact(
        db,
        agent,
        resource_kind='knowledge.document',
        server_id=int(result['id']),
        title=str(result.get('name') or '').strip() or '文档',
        source_tool='hasn.knowledge.write_doc',
    )
    return {'doc_id': result['id'], **result}


async def handle_knowledge_check_links(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """knowledge.check_links：写前预检正文里的文档深链是否合法（只校验、不落库）。

    传 kb_id → 按该库校验（新建文档前用）；传 doc_id → 按目标文档所属库校验（更新既有文档前用）。
    返回每条深链判定（ok / not_found 不存在或已删 / cross_kb 属其它库）+ 汇总 valid，
    与 write_doc/upload_document 保存时的强校验同一套判据——预检通过即保存不会被拒。
    """
    content = str(input_payload.get('content') or '')
    doc_id = input_payload.get('doc_id')
    try:
        if doc_id is not None:
            owner, gate_ran = _resource_owner('doc_id', agent)
            doc = await knowledge_service.get_document(db, owner, int(doc_id))
            kb_id = int(doc['kb_id'])
            if not gate_ran:
                await _assert_kb_reachable(db, agent, kb_id)
        else:
            kb_id = int(input_payload['kb_id'])
            owner, gate_ran = _resource_owner('kb_id', agent)
            if not gate_ran:
                await _assert_kb_reachable(db, agent, kb_id)
        return await knowledge_service.check_document_links(db, owner, kb_id, content)
    except KnowledgeProviderError as exc:
        raise to_http_error(exc) from exc
