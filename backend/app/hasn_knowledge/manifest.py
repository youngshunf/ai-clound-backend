"""知识库（app_id=knowledge）AI-Native manifest——标准云端应用形态。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.4：
- 工具回归 manifest 声明的 App 工具（transport=`gateway_internal`，handler 落 knowledge service），
  本地 Runtime（hasn-mcp manifest 加载 → BackendGateway::for_agent）与云端托管 Runtime
  （云端 MCP streamable）同一套工具、同一套权限、同一处审计；
- `hasn.knowledge.commit_document` 退役（上传即自动解析，两步契约是 RAGFlow 实现细节泄漏，D6）；
- 渐进式暴露不变：`tools/list` 只回 bootstrap，按需发现。

RAGFlow 是云端服务身后的内部处理后端（纯实现细节），manifest/工具面零 RAGFlow 字样。
"""

from __future__ import annotations


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    scopes: list[str],
    properties: dict,
    required: list[str],
    risk_level: str,
    page_rank: int,
    tags: list[str],
    confirm: bool = False,
) -> dict:
    return {
        'capability_id': f'knowledge.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'knowledge.{name}',
        'mcp_name': f'hasn.knowledge.{name}',
        'required_scopes': scopes,
        'workspace_roles': ['owner', 'admin', 'member'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        'human_confirmation': {'required': confirm},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


def _tool(
    *,
    name: str,
    scopes: list[str],
    risk_level: str,
    idempotent: bool,
    resource_access: list[dict] | None = None,
) -> dict:
    tool = {
        'tool_id': f'knowledge.{name}',
        'mcp_name': f'hasn.knowledge.{name}',
        'transport': 'gateway_internal',
        'handler': f'knowledge.{name}',
        'required_scopes': scopes,
        'risk_level': risk_level,
        'idempotent': idempotent,
    }
    # G6 资源权限门声明（doc33 S1-3/S2-6）：门在统一派发管线里按 `resolve_effective_permission` 内核
    # 判「这个 agent 能不能动这个资源实例」；param 须存在于对应 capability 的 input_schema.properties。
    # 未声明的工具（search/list_datasets/create_kb——无资源实例入参）门零介入。
    if resource_access is not None:
        tool['resource_access'] = resource_access
    return tool


KNOWLEDGE_AI_NATIVE_MANIFEST = {
    'app_id': 'knowledge',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'knowledge': '知识库（库/文档/检索/问答）'},
    # 2.3.0：原生文档 5000 字上限 + 文档深链 hasn://knowledge/documents/{id} 保存时强校验 + 新增 check_links 预检工具。
    'version': '2.3.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    # 资源描述符（doc31 §2，RC-P6/doc31-A）：知识库 → hasn://knowledge/kbs/{kb_id}，应用内路由打开。
    # KBDISP 派发的整理会话 origin_ref=resource:knowledge:{kb_id}（kb_id 即云端权威 id），完成即出
    # 「知识库整理好了」卡 + 登记应用资源产物到会话资源栏。单类资源（不声明 ref_type）。
    'resources': [
        {
            'resource_kind': 'knowledge.base',
            'uri_domain': 'knowledge/kbs',  # → hasn://knowledge/kbs/{kb_id}（doc08 §3 已登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/knowledge/kbs/:id'},
            'card': {'verb': '知识库', 'action_label': '打开知识库'},
            'artifact_kind': 'dataset',
        },
        {
            # 文档（doc31 register-on-write）：分身建/改的**每篇文档**都是独立产物，须能在工作会话资源栏
            # 单独看见并打开——只登记库、不登记文档，主人就只知道「动过某个库」、不知道产出了什么。
            'resource_kind': 'knowledge.document',
            # 注意：URI 域名是 `documents`、webui 内部路由段却是 `docs`（既定，勿"对齐"改坏深链）。
            'uri_domain': 'knowledge/documents',  # → hasn://knowledge/documents/{doc_id}
            'open': {'mode': 'internal_route', 'route_template': '/apps/knowledge/docs/:id'},
            'card': {'verb': '文档', 'action_label': '打开文档'},
            'artifact_kind': 'document',
        },
        # ⚠️ 两条 descriptor 都**不**声明 `ref_type`——knowledge 保持「单资源模式」：
        # KBDISP 派发的整理会话 origin_ref=`resource:knowledge:{kb_id}` 是整段作 id 的历史形状，
        # 一旦任一条声明 ref_type，整个 app 进多资源模式、该 origin_ref 会解析失败（完成卡丢资源入口）。
        # register-on-write 显式传 descriptor（不走 ref_type 解析），故无需声明；`resources[0]`=库，
        # 单资源模式回落到它，与 KBDISP 现状一致。**顺序不可调换**。
    ],
    # 通知发布能力声明保留：索引完成/失败可经服务号通知 owner（P2 接线）。
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '知识库',
        }
    },
    'capabilities': [
        _cap(
            name='search',
            title='检索知识库',
            description=(
                '检索主人可见知识库的资料，返回命中片段与来源。每个片段自带 document_id + document_uri'
                '（hasn://knowledge/documents/{id}）：片段只是索引摘要、常被截断，若不足以准确作答，'
                '据此调 fetch_doc 拉全文，别靠反复检索凑答案。'
            ),
            scopes=['knowledge:read'],
            properties={
                'query': {'type': 'string', 'minLength': 1},
                'kb_ids': {'type': ['array', 'null'], 'items': {'type': 'integer'}},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                'similarity_threshold': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1},
            },
            required=['query'],
            risk_level='low',
            page_rank=10,
            tags=['knowledge', 'search', 'read'],
        ),
        _cap(
            name='list_datasets',
            title='列出知识库',
            description='列出分身可访问的知识库（含文档数/分块数）',
            scopes=['knowledge:read'],
            properties={},
            required=[],
            risk_level='low',
            page_rank=11,
            tags=['knowledge', 'read'],
        ),
        _cap(
            name='create_kb',
            title='新建知识库',
            description=(
                '替主人新建一个知识库（库归主人所有；返回 kb_id，可随即向其上传/写文档）。'
                'cover_asset_uri 为必填封面：优先用素材搜索工具(hasn.stock.search/download)配图，'
                '其次生图工具(hasn.image.generate)，或据库主题自画一张 SVG，上传得 hasn://asset/ 后传入'
            ),
            scopes=['knowledge:write'],
            properties={
                'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                'description': {'type': ['string', 'null'], 'maxLength': 500},
                'cover_asset_uri': {
                    'type': 'string',
                    'pattern': '^hasn://asset/',
                    'description': '封面资产 hasn://asset/（必填；优先素材搜索配图→生图→自画SVG）',
                },
            },
            required=['name', 'cover_asset_uri'],
            risk_level='medium',
            page_rank=9,
            tags=['knowledge', 'kb', 'write'],
        ),
        _cap(
            name='update_kb',
            title='修改知识库',
            description=(
                '改主人某个知识库的库名 / 描述 / 封面（不动库内文档与索引）。'
                '常用于建库后补一张封面：先用素材搜索/生图/自画 SVG 得 hasn://asset/，再传 cover_asset_uri'
            ),
            scopes=['knowledge:write'],
            properties={
                'kb_id': {'type': 'integer'},
                'name': {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 100},
                'description': {'type': ['string', 'null'], 'maxLength': 500},
                'cover_asset_uri': {
                    'type': ['string', 'null'],
                    'description': '封面资产 hasn://asset/（空串=清空封面；省略=不改）',
                },
            },
            required=['kb_id'],
            risk_level='medium',
            page_rank=10,
            tags=['knowledge', 'kb', 'write'],
        ),
        _cap(
            name='delete_kb',
            title='删除知识库',
            description='删除主人的整个知识库（级联删除其全部文档与目录，不可恢复）',
            scopes=['knowledge:write'],
            properties={'kb_id': {'type': 'integer'}},
            required=['kb_id'],
            risk_level='high',
            page_rank=28,
            tags=['knowledge', 'kb', 'write', 'destructive'],
        ),
        _cap(
            name='fetch_doc',
            title='读取文档',
            description=(
                '读取知识库文档解析后的完整文本（原生文档返回正文，文件返回分块文本）。'
                'doc_id 来自 search 片段的 document_id / 深链 hasn://knowledge/documents/{id}。'
                '需要完整、准确的上下文时用它取全文——这不费额外检索，别为省 token 只用片段猜答案。'
            ),
            scopes=['knowledge:read'],
            properties={'doc_id': {'type': 'integer'}},
            required=['doc_id'],
            risk_level='low',
            page_rank=12,
            tags=['knowledge', 'read'],
        ),
        _cap(
            name='list_documents',
            title='列出文档',
            description='列出某知识库的文档（可按目录 folder_id 过滤：省略=全库，传 0=库根，>0=指定目录）',
            scopes=['knowledge:read'],
            properties={
                'kb_id': {'type': 'integer'},
                'folder_id': {'type': ['integer', 'null']},
            },
            required=['kb_id'],
            risk_level='low',
            page_rank=14,
            tags=['knowledge', 'doc', 'read'],
        ),
        _cap(
            name='upload_document',
            title='上传文档',
            description=(
                '向主人的知识库上传文档并自动建立索引。二选一：content_text(纯文本内容→一律落可编辑的原生文档，'
                '知识库原生优先、能不落 file 就不落；超5000字不再回落 file 而是拒绝，请拆成多篇原生文档 + 深链 '
                'hasn://knowledge/documents/{doc_id} 互连) 或 asset_uri(已在私有桶的真实二进制文件如 PDF/docx/图片 → 落 file 文档由引擎切块承载)'
            ),
            scopes=['knowledge:upload'],
            properties={
                'kb_id': {'type': 'integer'},
                'title': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                'content_text': {'type': ['string', 'null'], 'minLength': 1},
                'asset_uri': {'type': ['string', 'null'], 'pattern': '^hasn://asset/'},
                'folder_id': {'type': ['integer', 'null']},
            },
            required=['kb_id', 'title'],
            risk_level='medium',
            page_rank=20,
            tags=['knowledge', 'upload', 'write'],
        ),
        _cap(
            name='delete_document',
            title='删除文档',
            description='删除主人知识库中的一篇文档（含其索引副本，不可恢复）',
            scopes=['knowledge:write'],
            properties={'doc_id': {'type': 'integer'}},
            required=['doc_id'],
            risk_level='medium',
            page_rank=26,
            tags=['knowledge', 'doc', 'write', 'destructive'],
        ),
        _cap(
            name='write_doc',
            title='写原生文档',
            description=(
                '创建或更新知识库原生文档（Markdown，保存即重建索引，返回 doc_id）。'
                '正文上限 5000 字：超出请拆成多篇更聚焦的文档，并在正文里用深链 '
                '[标题](hasn://knowledge/documents/{目标doc_id}) 互相关联（点击可跳转到同库文档）。'
                '深链只能指向同一知识库内已存在的文档，保存时会强制校验，链接到不存在/其它库的文档会被拒绝；'
                '写前可用 hasn.knowledge.check_links 预检'
            ),
            scopes=['knowledge:write'],
            properties={
                'kb_id': {'type': ['integer', 'null']},
                'doc_id': {'type': ['integer', 'null']},
                'title': {'type': ['string', 'null'], 'maxLength': 200},
                'content': {'type': ['string', 'null']},
                'folder_id': {'type': ['integer', 'null']},
            },
            required=[],
            risk_level='medium',
            page_rank=21,
            tags=['knowledge', 'doc', 'write'],
        ),
        _cap(
            name='check_links',
            title='校验文档深链',
            description=(
                '写前预检正文里的文档深链 hasn://knowledge/documents/{doc_id} 是否合法（只校验、不落库）。'
                '传 kb_id（新建文档前）或 doc_id（更新既有文档前）定位目标库，逐条判定 ok / not_found(不存在或已删) / '
                'cross_kb(属其它库)。与 write_doc/upload_document 保存时的强校验同一套判据——预检 valid 即保存不会被拒。'
            ),
            scopes=['knowledge:read'],
            properties={
                'kb_id': {'type': ['integer', 'null']},
                'doc_id': {'type': ['integer', 'null']},
                'content': {'type': 'string'},
            },
            required=['content'],
            risk_level='low',
            page_rank=14,
            tags=['knowledge', 'doc', 'read'],
        ),
        _cap(
            name='move_document',
            title='移动文档到目录',
            description='把一篇已存在的文档移进指定目录(folder_id)或移回库根(move_to_root，二选一)；只改归属、不改内容、不重建索引',
            scopes=['knowledge:write'],
            properties={
                'doc_id': {'type': 'integer'},
                'folder_id': {'type': ['integer', 'null']},
                'move_to_root': {'type': 'boolean'},
            },
            required=['doc_id'],
            risk_level='low',
            page_rank=25,
            tags=['knowledge', 'doc', 'folder', 'write'],
        ),
        _cap(
            name='list_folders',
            title='列出目录',
            description='列出某知识库的目录树（平铺，按 parent_id 组树；含每个目录的 id/name/parent_id）',
            scopes=['knowledge:read'],
            properties={'kb_id': {'type': 'integer'}},
            required=['kb_id'],
            risk_level='low',
            page_rank=13,
            tags=['knowledge', 'folder', 'read'],
        ),
        _cap(
            name='create_folder',
            title='新建目录',
            description='在知识库里新建目录（可指定父目录 parent_id，空=库根；同层不可重名）',
            scopes=['knowledge:write'],
            properties={
                'kb_id': {'type': 'integer'},
                'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                'parent_id': {'type': ['integer', 'null']},
            },
            required=['kb_id', 'name'],
            risk_level='low',
            page_rank=22,
            tags=['knowledge', 'folder', 'write'],
        ),
        _cap(
            name='update_folder',
            title='重命名/移动目录',
            description='重命名目录(name)或移动到另一父目录(parent_id)/移到库根(move_to_root)；拒绝成环与同层重名',
            scopes=['knowledge:write'],
            properties={
                'folder_id': {'type': 'integer'},
                'name': {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 100},
                'parent_id': {'type': ['integer', 'null']},
                'move_to_root': {'type': 'boolean'},
            },
            required=['folder_id'],
            risk_level='low',
            page_rank=23,
            tags=['knowledge', 'folder', 'write'],
        ),
        _cap(
            name='delete_folder',
            title='删除目录',
            description='删除空目录（含子目录或文档时如实拒绝，不级联删除）',
            scopes=['knowledge:write'],
            properties={'folder_id': {'type': 'integer'}},
            required=['folder_id'],
            risk_level='low',
            page_rank=24,
            tags=['knowledge', 'folder', 'write'],
        ),
    ],
    'tools': [
        # search/list_datasets/create_kb 无资源实例入参（跨库检索 / 列全部 / 新建），门零介入，不声明。
        _tool(name='search', scopes=['knowledge:read'], risk_level='low', idempotent=True),
        _tool(name='list_datasets', scopes=['knowledge:read'], risk_level='low', idempotent=True),
        _tool(name='create_kb', scopes=['knowledge:write'], risk_level='medium', idempotent=False),
        # update_kb 改既有库的库名/描述/封面（如建库后补封面）；门按 kb_id manager 档判权（与 delete_kb/owner 更新一致）。
        _tool(
            name='update_kb',
            scopes=['knowledge:write'],
            risk_level='medium',
            idempotent=False,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'manager'}],
        ),
        _tool(
            name='delete_kb',
            scopes=['knowledge:write'],
            risk_level='high',
            idempotent=False,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'manager'}],
        ),
        _tool(
            name='fetch_doc',
            scopes=['knowledge:read'],
            risk_level='low',
            idempotent=True,
            resource_access=[{'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'viewer'}],
        ),
        _tool(
            name='list_documents',
            scopes=['knowledge:read'],
            risk_level='low',
            idempotent=True,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'viewer'}],
        ),
        _tool(
            name='upload_document',
            scopes=['knowledge:upload'],
            risk_level='medium',
            idempotent=False,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'editor'}],
        ),
        _tool(
            name='delete_document',
            scopes=['knowledge:write'],
            risk_level='medium',
            idempotent=False,
            resource_access=[{'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'editor'}],
        ),
        # write_doc 可传 doc_id（更新既有文档）或 kb_id（在库内新建）；两者皆 optional（required:false），
        # 门只对**实际传入**的那个判权（缺省参跳过），editor 档。
        _tool(
            name='write_doc',
            scopes=['knowledge:write'],
            risk_level='medium',
            idempotent=False,
            resource_access=[
                {'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'editor', 'required': False},
                {'param': 'kb_id', 'type': 'knowledge', 'need': 'editor', 'required': False},
            ],
        ),
        # check_links 只读预检深链：传 kb_id（新建前）或 doc_id（更新前）定位库；两者皆 optional，
        # 门只对**实际传入**的那个判权，viewer 档（只校验、不落库）。
        _tool(
            name='check_links',
            scopes=['knowledge:read'],
            risk_level='low',
            idempotent=True,
            resource_access=[
                {'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'viewer', 'required': False},
                {'param': 'kb_id', 'type': 'knowledge', 'need': 'viewer', 'required': False},
            ],
        ),
        _tool(
            name='move_document',
            scopes=['knowledge:write'],
            risk_level='low',
            idempotent=True,
            resource_access=[{'param': 'doc_id', 'type': 'knowledge_doc', 'need': 'editor'}],
        ),
        _tool(
            name='list_folders',
            scopes=['knowledge:read'],
            risk_level='low',
            idempotent=True,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'viewer'}],
        ),
        _tool(
            name='create_folder',
            scopes=['knowledge:write'],
            risk_level='low',
            idempotent=False,
            resource_access=[{'param': 'kb_id', 'type': 'knowledge', 'need': 'editor'}],
        ),
        _tool(
            name='update_folder',
            scopes=['knowledge:write'],
            risk_level='low',
            idempotent=False,
            resource_access=[{'param': 'folder_id', 'type': 'knowledge_folder', 'need': 'editor'}],
        ),
        _tool(
            name='delete_folder',
            scopes=['knowledge:write'],
            risk_level='low',
            idempotent=False,
            resource_access=[{'param': 'folder_id', 'type': 'knowledge_folder', 'need': 'editor'}],
        ),
    ],
    'events': [],
    'reverse_invoke': {'supported': False},
    'audit': {
        'fields': [
            'trace_id',
            'workspace',
            'app_id',
            'agent_hasn_id',
            'owner_hasn_id',
            'session_uuid',
            'tool_id',
            'required_scopes',
            'decision',
        ]
    },
}
