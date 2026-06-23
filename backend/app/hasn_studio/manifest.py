"""唤星统一视频引擎接入应用（studio，源自 OpenMontage，模块 14 doc22）AI-Native catalog + 内置 manifest。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/22-OpenMontage统一视频引擎(云服务·工具即服务)选型设计.md
§3（cloud-brokered 架构 / §3.1 数据模型）/§3.6（分享协作全复用）；实施/22（P0–P9 施工清单）。

studio 是 **cloud-brokered** AI-Native 应用（对齐 creator/finance/quant，非 reel/film 的 local_tool）：
- 分身经**云端 MCP** 调 `hasn.studio.*`（Agent MCP Key → `/api/v1/mcp/streamable` → `app_tool_loader` 投影成
  AppTool → `ai_native_runtime_gateway`（transport=gateway_internal）→ 进程内直调云端 handler →
  studio_service（落 hasn_studio PG）→ montage_engine_provider → 内网 REST 调引擎服务
  （huanxing-apps/montage-engine-service）跑真渲染/出片）。
- 产品数据权威全在唤星 PG（不变量 #4）；引擎服务无产品表，只持渲染运行态（crash-only 可重跑）。

⚠️ 本期 P2 只做**云端数据层 + 目录/scope/manifest 骨架**（4 表 hasn_studio.* + catalog 行 + 5 scope 铸造）。
  分身工具面 `hasn.studio.*`（read/write 出厂 allow；render/export/share 出厂 ask）随 P3 service + 云端 handler
  落地，本期 manifest **不暴露 tools/capabilities**（避免声明指向尚不存在的 gateway 内部 handler，零 fake）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

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


STUDIO_AI_NATIVE_MANIFEST = {
    'app_id': 'studio',
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'cloud',
    # cloud-brokered 工具模型（对齐 creator/finance/quant）：工具数据面经 gateway_internal 进程内直调云端 handler →
    # studio_service（落 hasn_studio PG）→ montage_engine_provider → montage-engine-service，不经本地
    # hasn-mcp / daemon Agent 代理（视频渲染/出片是集中托管的云端业务，算力按量计费要集中审计/计费）。
    'transport_mode': 'cloud',
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '视频引擎',
        }
    },
    # P2 骨架：工具面随 P3 service + 云端 handler 落地（read/write 出厂 allow；render/export/share 出厂 ask）。
    'capabilities': [],
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_studio_app() -> App:
    """studio App（cloud-brokered / 自建视频工作台 /apps/studio / 按需安装）。

    - ``execution_mode='cloud'``：分身工具经云端 MCP → 云端后端 Broker → 引擎服务（非本地工具、非 sidecar iframe）。
    - ``install_policy='manual'``：统一视频引擎是专业能力，按需装、不自动挂载到工作台（对齐 creator/finance/quant）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)``：个人模式（分享协作走 resource_share，§3.6 全复用）。
    - ``entry_route='/apps/studio'``：自建视频工作台（项目/管线/素材/成品库，随 webui 落地）。
    - ``default_agent_type`` 由 catalog DB 行承载（content_operator「内容运营官」），不在 App dataclass。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='studio',
        name='视频引擎',
        icon='brand-studio',
        description='统一视频引擎工作台——主人挑管线、派分身出片（脚本→分镜→配音→合成），成品库一键管理'
        '（cloud-brokered，算力按量计费）。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/studio',
        install_policy='manual',
        execution_mode='cloud',
        ui_kind=None,
    )
