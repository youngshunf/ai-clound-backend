"""唤星会议副驾应用（copilot，对外「会议副驾」；模块「桌面端潜行会议副驾」）工作台入口 manifest。

设计事实源：
- docs/hasn-node设计文档/桌面端潜行会议副驾/01-桌面端潜行会议副驾总体设计.md
- docs/hasn-node设计文档/桌面端潜行会议副驾/实施/01-分阶段实施计划与施工清单.md（P2 云端 / P6 webui 三载体）

会议副驾是 **工作会话驱动** 的 AI-Native 应用：分身边听会议/通话边给要点、可追问的问题、
待办与易错点，会后产出结构化纪要落产物。与 deck/film 不同——**它没有 Agent 调用的 ``hasn.*``
MCP 工具**（实时转写/建议/纪要都在工作会话里由分身完成，hasn-node ``domains/copilot`` 的
工作会话 + 投影卡片 + 本地镜像 + 产物本地优先存储驱动），故 **不注册 AI-Native tool manifest、
不铸 scope**（同 knowledge/community 这类「无 Agent 工具」的应用，只需 App 工作台入口）。

⚠️ execution_mode='local_tool' / ui_kind=None（原生 webui 页，模式 A）：数据与工作会话在本地
daemon（``domains/copilot`` 本地镜像 + 产物本地优先存储），非云端执行、非 sidecar iframe。
注册到 ``app_catalog_registry`` → ``ensure_catalog_seeded`` 幂等播种 ``hasn_app_catalog``
（status=published）→ 工作台应用网格出现「会议副驾」入口（点进 ``/apps/copilot``
载体 B 工作台页：会议历史 / 产物库 / 设置）。

⚠️ install_policy='manual'：会议副驾的完整实时副驾能力（隐身悬浮窗 + 系统/麦克风双轨采集）依赖
桌面端原生层就绪，不强制自动挂载到侧栏；工作台目录始终可见可进入（回看历史会话/产物/设置），
原生采集随桌面端能力探测渐进解锁。

⚠️ default_agent_type='assistant'（AppCollab doc21 §4.3）：2026-07-12 内置分身收敛后，
打开会议副驾默认由 owner 名下的「全能助理」承接；专用 ``meeting-copilot`` 模板仍可从市场
按需创建，但不再作为内置分身——见 ``app_catalog_service._CATALOG_AGENT_DEFAULTS`` /
``resolve_default_agent_for_app``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App


COPILOT_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'copilot',
    'domain_summary': {'copilot': '会议副驾（实时建议、会议纪要与待办）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'project_aware': False,
    'project_required': False,
    'project_integration': 'artifact_only',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    'resources': [],
    'capabilities': [],
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': []},
}


def build_copilot_app() -> App:
    """会议副驾 App（local_tool / 原生 webui 路由 / 非自动挂载）。

    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui 工作台页（模式 A），
      数据在本地 daemon ``domains/copilot``（工作会话 + 投影 + 产物本地优先）。
    - **无 AI-Native tool manifest、无 scope**：会议副驾走工作会话派发，分身不调 ``hasn.*`` 工具
      （同 knowledge/community 无 Agent 工具的应用）。
    - ``install_policy='manual'``：完整实时副驾依赖桌面端原生隐身/音频，不强制挂载；
      工作台目录可见可进入（载体 B 回看历史会话/产物/设置）。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='copilot',
        name='会议副驾',
        icon='brand-copilot',
        description='开会、通话时边听边给要点、追问与待办，会后自动产出结构化纪要——克制不刷屏，只在你需要时出现。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/copilot',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
