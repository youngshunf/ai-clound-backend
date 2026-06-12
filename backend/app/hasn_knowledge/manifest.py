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


def _tool(*, name: str, scopes: list[str], risk_level: str, idempotent: bool) -> dict:
    return {
        'tool_id': f'knowledge.{name}',
        'mcp_name': f'hasn.knowledge.{name}',
        'transport': 'gateway_internal',
        'handler': f'knowledge.{name}',
        'required_scopes': scopes,
        'risk_level': risk_level,
        'idempotent': idempotent,
    }


KNOWLEDGE_AI_NATIVE_MANIFEST = {
    'app_id': 'knowledge',
    'version': '2.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
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
            description='检索主人可见知识库的资料（返回命中片段与来源）',
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
            name='fetch_doc',
            title='读取文档',
            description='读取知识库文档解析后的文本内容（原生文档返回正文，文件返回分块文本）',
            scopes=['knowledge:read'],
            properties={'doc_id': {'type': 'integer'}},
            required=['doc_id'],
            risk_level='low',
            page_rank=12,
            tags=['knowledge', 'read'],
        ),
        _cap(
            name='upload_document',
            title='上传文档',
            description='以文本内容向主人的知识库上传文档并自动建立索引',
            scopes=['knowledge:upload'],
            properties={
                'kb_id': {'type': 'integer'},
                'title': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                'content_text': {'type': 'string', 'minLength': 1},
                'folder_id': {'type': ['integer', 'null']},
            },
            required=['kb_id', 'title', 'content_text'],
            risk_level='medium',
            page_rank=20,
            tags=['knowledge', 'upload', 'write'],
        ),
        _cap(
            name='write_doc',
            title='写原生文档',
            description='创建或更新知识库原生文档（Markdown，保存即重建索引，返回 doc_id）',
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
    ],
    'tools': [
        _tool(name='search', scopes=['knowledge:read'], risk_level='low', idempotent=True),
        _tool(name='list_datasets', scopes=['knowledge:read'], risk_level='low', idempotent=True),
        _tool(name='fetch_doc', scopes=['knowledge:read'], risk_level='low', idempotent=True),
        _tool(name='upload_document', scopes=['knowledge:upload'], risk_level='medium', idempotent=False),
        _tool(name='write_doc', scopes=['knowledge:write'], risk_level='medium', idempotent=False),
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
