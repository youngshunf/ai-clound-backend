from __future__ import annotations

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
