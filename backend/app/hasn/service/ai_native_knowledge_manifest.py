from __future__ import annotations

# knowledge 域 scope 展示元数据（16-doc D-v3-3：app 域 scope 元数据随应用声明落地，
# 由 app/mcp/scopes.py 聚合）。判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。
KNOWLEDGE_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'knowledge:read': {'label_zh': '检索知识库', 'domain': 'knowledge', 'risk': 'low', 'description': '检索当前工作空间的知识库资料'},
    'knowledge:upload': {'label_zh': '上传知识库文档', 'domain': 'knowledge', 'risk': 'medium', 'description': '向当前工作空间的知识库上传文档（按主人授权与库白名单）'},
    'knowledge:write': {'label_zh': '解析/建库写入', 'domain': 'knowledge', 'risk': 'medium', 'description': '触发文档解析入库、新建数据集（写入知识库结构）'},
    'knowledge:grant': {'label_zh': '代主人改授权', 'domain': 'knowledge', 'risk': 'high', 'description': '代主人调整知识库访问授权（预留，当前不开放）'},
}

KNOWLEDGE_AI_NATIVE_MANIFEST = {
    'app_id': 'knowledge',
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    # 通知发布能力声明（统一通知设计 §7 / P5）：知识库可在导入/索引完成等时机
    # 经 Agent JWT 通道发通知给主人，落知识库服务号会话。
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '知识库',
        }
    },
    'capabilities': [
        {
            'capability_id': 'knowledge.search.capability',
            'name': '检索知识库',
            'description': '检索当前工作空间的知识库资料',
            'tool_id': 'knowledge.search',
            'mcp_name': 'hasn.knowledge.search',
            'required_scopes': ['knowledge:read'],
            'workspace_roles': ['owner', 'admin', 'member'],
            'input_schema': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'minLength': 1},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                    'dataset_id': {'type': ['string', 'null']},
                },
                'required': ['query'],
                'additionalProperties': False,
            },
            'output_schema': {'type': 'object'},
            'risk_level': 'low',
            'human_confirmation': {'required': False},
            'result_writeback': ['audit', 'agent_message'],
            'discovery': {
                'exposure': 'on_demand',
                'summary': '检索当前工作空间的知识库资料',
                'tags': ['knowledge', 'search', 'read'],
                'schema_visibility': 'authorized_agents',
                'default_page_rank': 10,
            },
        }
    ],
    # RF-CLOUD：knowledge 不再是**可调用的 cloud 工具**（设计 §4.5 方案1 / §9.1）。
    # `hasn.knowledge.search` 已改为 hasn-node 本地 Platform 工具（RF-MCP）：Agent 经
    # daemon 进程内 KnowledgeGateway 直连 RagFlow 数据面，云端只发凭证、不中转检索。
    # 上面的 `capabilities` 声明保留——供 read-through 能力发现 + 权限（knowledge:read）。
    'tools': [],
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
