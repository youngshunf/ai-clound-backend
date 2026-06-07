"""Presenton 演示文稿 AI-Native 内置 manifest + WorkbenchApp 声明。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/12-Presenton演示文稿应用接入设计.md
  §7.2（WorkbenchApp）/ §7.3（AI-Native manifest，方案 A：tools[] 置空）/ §8.2（11 工具映射表）。

embedded_desktop AI-Native 应用：UI 与工具数据面都在本地（hasn-node sidecar + hasn-mcp
source=Local），云端 manifest 只承载**发现/权限元数据/审计字段**控制面记录。

⚠️ 方案 A（§7.3 critical）：`tools[]` **置空数组**——embedded_desktop 本地工具数据面不经
云端 Runtime Gateway（走本地 hasn-mcp `source=Local`），故不进 `tools[]`（自造 transport 会静默
过 validate_manifest 变潜伏炸弹）。工具靠 hasn-mcp `built_in_presentation_tools()` 注册、bootstrap 发现。
`capabilities[]` 不参与 `_dispatch_tool` 路由，安全承载发现/权限元数据。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp


_AUDIT_FIELDS = [
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


def _read_cap(
    *,
    capability_id: str,
    name: str,
    description: str,
    tool: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """读类能力（presentation:read，低风险，无需确认）。"""
    return {
        'capability_id': capability_id,
        'name': name,
        'description': description,
        'tool_id': tool,
        'mcp_name': f'hasn.{tool}',
        'required_scopes': ['presentation:read'],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'low',
        'human_confirmation': {'required': False},
        # D5：审计经 for_agent 上云（§11.5，上报链路 P4 新建；本地 AuthorizationAudit 始终先产生）。
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


# ── generate：完整生成 + 内部导出（§7.3 完整示例）──
_GENERATE_CAP = {
    'capability_id': 'presentation.generate.capability',
    'name': '生成演示文稿',
    'description': '根据主题/文档同步生成完整演示文稿并导出（消耗 LLM 额度并写入本机文件）',
    'tool_id': 'presentation.generate',
    'mcp_name': 'hasn.presentation.generate',
    'required_scopes': ['presentation:generate'],
    'workspace_roles': ['owner'],
    'input_schema': {
        'type': 'object',
        'properties': {
            'content': {'type': 'string', 'minLength': 1, 'description': '演示主题或正文（必填）'},
            'instructions': {'type': ['string', 'null'], 'description': '额外生成指令'},
            'n_slides': {
                'type': ['integer', 'null'],
                'minimum': 1,
                'description': '页数；省略=自动判定（勿传 0/负数，<=0 后端 400）',
            },
            'language': {'type': ['string', 'null'], 'description': '语言；省略=自动'},
            'template': {
                'type': 'string',
                'enum': ['general', 'modern', 'standard', 'swift'],
                'default': 'general',
                'description': '模板，或 custom-<uuid>；非法值 400',
            },
            'include_table_of_contents': {
                'type': 'boolean',
                'default': False,
                'description': '含目录页时 n_slides 须>=3',
            },
            'include_title_slide': {'type': 'boolean', 'default': True},
            'files': {
                'type': ['array', 'null'],
                'items': {'type': 'string'},
                'description': 'RAG 文档路径（来自 upload_file 返回值）',
            },
            'export_as': {
                'type': 'string',
                'enum': ['pptx', 'pdf'],
                'default': 'pptx',
                'description': '导出格式（导出在本端点内部完成）',
            },
        },
        'required': ['content'],
        'additionalProperties': False,
    },
    'output_schema': {
        'type': 'object',
        'properties': {
            'presentation_id': {'type': 'string'},
            'path': {'type': 'string', 'description': '导出文件本机绝对路径'},
            'edit_path': {'type': 'string'},
        },
    },
    'risk_level': 'high',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['agent_message', 'workspace_notification', 'audit'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '根据主题/文档一键生成完整演示文稿',
        'tags': ['presentation', 'ppt', 'generate'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 20,
    },
}

_GENERATE_ASYNC_CAP = {
    'capability_id': 'presentation.generate_async.capability',
    'name': '异步生成演示文稿',
    'description': '异步生成（长任务），立即返回 task_id，配套 get_status 轮询',
    'tool_id': 'presentation.generate_async',
    'mcp_name': 'hasn.presentation.generate_async',
    'required_scopes': ['presentation:generate'],
    'workspace_roles': ['owner'],
    'input_schema': dict(_GENERATE_CAP['input_schema']),
    'output_schema': {
        'type': 'object',
        'properties': {'task_id': {'type': 'string'}, 'status': {'type': 'string'}},
    },
    'risk_level': 'high',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['agent_message', 'workspace_notification', 'audit'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '异步生成演示文稿（长任务）',
        'tags': ['presentation', 'ppt', 'generate', 'async'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 21,
    },
}

_OUTLINE_CAP = {
    'capability_id': 'presentation.outline.capability',
    'name': '生成演示大纲',
    'description': '根据主题生成大纲并建草稿（不导出，消耗 LLM）',
    'tool_id': 'presentation.outline',
    'mcp_name': 'hasn.presentation.outline',
    'required_scopes': ['presentation:create'],
    'workspace_roles': ['owner'],
    'input_schema': {
        'type': 'object',
        'properties': {
            'content': {'type': 'string', 'minLength': 1, 'description': '演示主题或正文（必填）'},
            'n_slides': {'type': ['integer', 'null'], 'minimum': 1, 'description': '页数；省略=自动'},
            'language': {'type': ['string', 'null']},
        },
        'required': ['content'],
        'additionalProperties': False,
    },
    'output_schema': {'type': 'object'},
    'risk_level': 'medium',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['audit', 'agent_message'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '生成演示大纲草稿',
        'tags': ['presentation', 'outline', 'create'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 22,
    },
}


def _edit_like_cap(
    *, capability_id: str, name: str, description: str, tool: str, page_rank: int, tags: list[str]
) -> dict:
    """edit/derive：按页改内容 + 触发重导出（高风险，写文件）。"""
    return {
        'capability_id': capability_id,
        'name': name,
        'description': description,
        'tool_id': tool,
        'mcp_name': f'hasn.{tool}',
        'required_scopes': ['presentation:generate'],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': {
                'presentation_id': {'type': 'string', 'minLength': 1, 'description': '目标演示文稿 id'},
                'slides': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'index': {'type': 'integer', 'minimum': 0},
                            'content': {'type': 'string'},
                        },
                        'required': ['index', 'content'],
                        'additionalProperties': False,
                    },
                    'description': '按页改动；重导出为另一格式时可传空/最小改动',
                },
                'export_as': {'type': 'string', 'enum': ['pptx', 'pdf'], 'default': 'pptx'},
            },
            'required': ['presentation_id'],
            'additionalProperties': False,
        },
        'output_schema': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}, 'edit_path': {'type': 'string'}},
        },
        'risk_level': 'high',
        'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
        'result_writeback': ['agent_message', 'workspace_notification', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


_DELETE_CAP = {
    'capability_id': 'presentation.delete.capability',
    'name': '删除演示文稿',
    'description': '删除指定演示文稿（破坏性）',
    'tool_id': 'presentation.delete',
    'mcp_name': 'hasn.presentation.delete',
    'required_scopes': ['presentation:manage'],
    'workspace_roles': ['owner'],
    'input_schema': {
        'type': 'object',
        'properties': {'presentation_id': {'type': 'string', 'minLength': 1}},
        'required': ['presentation_id'],
        'additionalProperties': False,
    },
    'output_schema': {'type': 'object'},
    'risk_level': 'high',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['audit', 'agent_message'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '删除演示文稿',
        'tags': ['presentation', 'delete', 'manage'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 27,
    },
}

_UPLOAD_FILE_CAP = {
    'capability_id': 'presentation.upload_file.capability',
    'name': '上传 RAG 文档',
    'description': '上传文档供生成时作 RAG 参考（入参须净化，§11.2）',
    'tool_id': 'presentation.upload_file',
    'mcp_name': 'hasn.presentation.upload_file',
    'required_scopes': ['presentation:manage'],
    'workspace_roles': ['owner'],
    'input_schema': {
        'type': 'object',
        'properties': {
            'filename': {'type': 'string', 'minLength': 1, 'description': '文件名（仅 basename，拒 .. 与绝对路径）'},
            'content_base64': {'type': 'string', 'description': '文件内容 base64'},
        },
        'required': ['filename', 'content_base64'],
        'additionalProperties': False,
    },
    'output_schema': {'type': 'object', 'properties': {'paths': {'type': 'array', 'items': {'type': 'string'}}}},
    'risk_level': 'high',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['audit', 'agent_message'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '上传 RAG 文档',
        'tags': ['presentation', 'upload', 'manage'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 28,
    },
}

# ── image.generate：D4 通道②，唯一不映射 Presenton 端点（直连 new-api 图像 API）──
_IMAGE_GENERATE_CAP = {
    'capability_id': 'image.generate.capability',
    'name': '生成图片',
    'description': '直连唤星 new-api 图像 API 生成图片（D4 通道②，与 Presenton 内部自动配图并存；消耗 owner 配额）',
    'tool_id': 'image.generate',
    'mcp_name': 'hasn.image.generate',
    'required_scopes': ['image:generate'],
    'workspace_roles': ['owner'],
    'input_schema': {
        'type': 'object',
        'properties': {
            'prompt': {'type': 'string', 'minLength': 1, 'description': '图片描述（必填）'},
            'size': {'type': ['string', 'null'], 'description': '尺寸如 1024x1024'},
            'n': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 4, 'description': '张数'},
            'style': {'type': ['string', 'null']},
        },
        'required': ['prompt'],
        'additionalProperties': False,
    },
    'output_schema': {'type': 'object', 'properties': {'images': {'type': 'array', 'items': {'type': 'string'}}}},
    'risk_level': 'medium',
    'human_confirmation': {'required': 'policy', 'policy_ref': 'owner_tristate'},
    'result_writeback': ['audit', 'agent_message'],
    'discovery': {
        'exposure': 'on_demand',
        'summary': '生成图片（new-api，独立于 Presenton）',
        'tags': ['image', 'generate'],
        'schema_visibility': 'authorized_agents',
        'default_page_rank': 30,
    },
}


PRESENTATION_AI_NATIVE_MANIFEST = {
    'app_id': 'presentation',
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'embedded_desktop',
    'transport_mode': 'hybrid',
    # 通知发布能力声明（统一通知设计 §7）：PPT 生成完成等时机经 Agent JWT 通道发通知给主人。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '演示文稿',
        }
    },
    'capabilities': [
        _GENERATE_CAP,
        _GENERATE_ASYNC_CAP,
        _read_cap(
            capability_id='presentation.get_status.capability',
            name='查询异步任务状态',
            description='查询异步生成任务的状态与结果',
            tool='presentation.get_status',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=23,
            tags=['presentation', 'status', 'read'],
        ),
        _OUTLINE_CAP,
        _edit_like_cap(
            capability_id='presentation.edit.capability',
            name='编辑演示文稿',
            description='按页改内容并触发重导出（写文件）；重导出为另一格式亦走此工具',
            tool='presentation.edit',
            page_rank=24,
            tags=['presentation', 'edit', 'export'],
        ),
        _edit_like_cap(
            capability_id='presentation.derive.capability',
            name='派生演示文稿',
            description='基于现有演示文稿派生新副本并导出',
            tool='presentation.derive',
            page_rank=25,
            tags=['presentation', 'derive', 'export'],
        ),
        _read_cap(
            capability_id='presentation.list.capability',
            name='列出演示文稿',
            description='列出全部演示文稿',
            tool='presentation.list',
            properties={},
            required=[],
            page_rank=26,
            tags=['presentation', 'list', 'read'],
        ),
        _read_cap(
            capability_id='presentation.get.capability',
            name='获取演示文稿',
            description='获取单个演示文稿（含 slides）',
            tool='presentation.get',
            properties={'presentation_id': {'type': 'string', 'minLength': 1}},
            required=['presentation_id'],
            page_rank=29,
            tags=['presentation', 'get', 'read'],
        ),
        _DELETE_CAP,
        _UPLOAD_FILE_CAP,
        _IMAGE_GENERATE_CAP,
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_presentation_workbench_app() -> WorkbenchApp:
    """构造 presentation 的 WorkbenchApp（§7.2）。延迟导入避免循环依赖。"""
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

    return WorkbenchApp(
        id='presentation',
        name='演示文稿',
        icon='presentation',
        description='用自然语言或文档一键生成、编辑并导出演示文稿',
        scope=('personal',),
        entry_route='/workbench/apps/presentation',
        install_policy='auto',
        collaboration_mode='none',
        requires_role=None,
        execution_mode='embedded_desktop',
        # 启动方式（设计 12 §6.1）：new_window = 工作台点击在独立 OS 窗口打开（推荐）。
        # WebUI 据此用原生 WebviewWindow 载 window_url。window_url 指向 daemon 的「加载外壳」
        # (loading shell) 而非直接 ui/upload：外壳开窗即出现品牌 spinner、内嵌 iframe 加载真实 UI
        # 并触发 sidecar 冷启，避免冷启期间窗口白屏空等（外壳由 daemon domains/apps loading_shell 服务）。
        ui_kind='new_window',
        window_url='/api/v1/apps/presentation/loading',
        window_origin='loopback',
    )
