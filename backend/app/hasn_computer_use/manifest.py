"""computer_use（分身 GUI 桌面控制 · Computer Use，模块 23 V2）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/23-分身桌面控制Computer-Use/02-分身GUI桌面控制接入设计V2-hasn-mcp统一接入与能力型应用.md
  （§3.1 三 scope 表 / §3.3-3.4 安全语义 / §4.4.1 能力型应用 HExt-08 §4.4.1）
- 16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

computer_use 是 **能力型 AI-Native 应用**（HExt-08 §4.4.1）：B 路线·hasn-mcp 统一接入——cua-driver 由
daemon 经 `LocalMcpManager` 常驻托管，`hasn.computer.*` 本地工具经 daemon `ComputerBroker` 落到真实桌面，
**所有本地 runtime（Hermes/Claude/Codex）通用**。形态同 designsystem/imagelab（Tool-First·local_tool）：
无独立 sidecar 云端列、无常驻云端进程。

⚠️ 方案 A（同 designsystem/imagelab）：`tools[]` **置空数组**——`hasn.computer.*` 工具数据面在本地
hasn-mcp（`crates/hasn-mcp/src/computer/tools.rs`，`source=Platform`/`execution_location=Local`），
经 daemon 托管的 cua-driver 落桌面，**不经云端 Runtime Gateway `_dispatch_tool`**。故不进 `tools[]`
（自造 transport 会静默过 validate_manifest 变潜伏炸弹）；`capabilities[]` 只承载发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（`crates/hasn-mcp/src/computer/tools.rs` `capability_scopes()` + `scopes.py`；
16 工具 / 6 scope）：
- 窗口级截图/观察（capture/list_apps/wait）→ `computer_use:capture`（出厂 Allow）；
- 全屏截图（capture_screen）→ `computer_use:capture_screen`（出厂 Ask——可能框进其它 App 隐私内容）；
- 控制动作（click/double_click/right_click/type/key/scroll/drag/set_value/focus_app）→ `computer_use:control`
  （出厂 Ask；`scroll` 仅改视口，工具粒度出厂 Allow，属 scope 内例外——manifest 里 scroll 的
  `human_confirmation.required=False`，其余控制动作 True）；
- 启动/打开 App（launch_app）→ `computer_use:launch_app`（出厂 Allow，risk low——不抢焦点、非破坏）；
- 强退 App（kill_app）→ `computer_use:kill_app`（出厂 Ask，risk high——破坏性）；
- 浏览器页面自动化（page）→ `computer_use:browser`（出厂 Ask，risk high）。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**（本地工具驱动 +
原生 webui UI，非 cloud 执行、非重 sidecar），同 designsystem/imagelab。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 designsystem/imagelab）。
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

# scope 常量（与 hasn-node crates/hasn-mcp/src/computer/tools.rs 六档一字不差）。
_CAPTURE_SCOPE = 'computer_use:capture'
_CAPTURE_SCREEN_SCOPE = 'computer_use:capture_screen'
_CONTROL_SCOPE = 'computer_use:control'
_LAUNCH_APP_SCOPE = 'computer_use:launch_app'
_KILL_APP_SCOPE = 'computer_use:kill_app'
_BROWSER_SCOPE = 'computer_use:browser'


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    scope: str,
    ask: bool,
    risk: str,
    page_rank: int,
    tags: list[str],
) -> dict:
    """构造一个桌面控制能力声明（发现/权限元数据控制面记录）。

    - `required_scopes`：与本地 `computer/tools.rs` `capability_scopes()` 对齐的单一 scope；
    - `human_confirmation.required=ask`：与本地工具 `default_capability_mode()` 对齐（Ask=True / Allow=False，
      含 `scroll` 出厂 Allow 例外）；
    - 工具数据面在本地（方案 A），故不进 `tools[]`，只作发现元数据；入参 schema 权威在本地工具，这里给最小占位。
    """
    return {
        'capability_id': f'computer_use.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'computer_use.{name}',
        'mcp_name': f'hasn.computer.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {'type': 'object', 'additionalProperties': True},
        'output_schema': {'type': 'object'},
        'risk_level': risk,
        'human_confirmation': {'required': ask},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


COMPUTER_USE_AI_NATIVE_MANIFEST = {
    'app_id': 'computer_use',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'computer_use': '桌面控制（截屏观察 + 点击/输入/拖拽驱动 GUI）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    # 通知发布能力声明（统一通知设计）：完成/派发卡经 Agent JWT 通道发卡给主人。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '桌面控制',
        }
    },
    'capabilities': [
        # —— 截屏与观察（computer_use:capture，出厂 Allow）——
        _cap(
            name='capture',
            title='截取窗口',
            description='截取指定 App 窗口并标注可交互元素编号（SOM），返回带编号截图供后续按编号精准操作。',
            scope=_CAPTURE_SCOPE,
            ask=False,
            risk='low',
            page_rank=10,
            tags=['computer_use', 'capture', 'observe'],
        ),
        _cap(
            name='capture_screen',
            title='全屏截图',
            description='截取整个屏幕（可能框进其它 App 的隐私内容），比窗口级截图敏感，默认需主人确认。',
            scope=_CAPTURE_SCREEN_SCOPE,
            ask=True,
            risk='medium',
            page_rank=11,
            tags=['computer_use', 'capture_screen', 'fullscreen'],
        ),
        _cap(
            name='list_apps',
            title='列出运行中的 App',
            description='列出当前运行的可见 App（App 名/窗口标题），供选择要操作的目标窗口。',
            scope=_CAPTURE_SCOPE,
            ask=False,
            risk='low',
            page_rank=12,
            tags=['computer_use', 'list_apps', 'observe'],
        ),
        _cap(
            name='wait',
            title='等待界面就绪',
            description='等待指定毫秒（界面动画/加载完成后再继续观察或操作）。',
            scope=_CAPTURE_SCOPE,
            ask=False,
            risk='low',
            page_rank=13,
            tags=['computer_use', 'wait'],
        ),
        # —— 控制动作（computer_use:control，出厂 Ask；scroll 例外 Allow）——
        _cap(
            name='click',
            title='单击',
            description='单击某元素（按元素编号或坐标）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=14,
            tags=['computer_use', 'control', 'click'],
        ),
        _cap(
            name='double_click',
            title='双击',
            description='双击某元素。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=15,
            tags=['computer_use', 'control', 'double_click'],
        ),
        _cap(
            name='right_click',
            title='右键单击',
            description='右键单击某元素（打开上下文菜单）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=16,
            tags=['computer_use', 'control', 'right_click'],
        ),
        _cap(
            name='type',
            title='键入文本',
            description='在当前焦点处键入文本（危险 shell 模式如 curl|bash、sudo rm -rf 会被工具层硬拦）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=17,
            tags=['computer_use', 'control', 'type'],
        ),
        _cap(
            name='key',
            title='发送按键',
            description='发送按键 / 快捷键（破坏性快捷键组合会被工具层硬拦）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=18,
            tags=['computer_use', 'control', 'key'],
        ),
        _cap(
            name='scroll',
            title='滚动视口',
            description='滚动视口（仅改视口不改数据，出厂放行以降审批疲劳；scope 内例外）。',
            scope=_CONTROL_SCOPE,
            ask=False,
            risk='low',
            page_rank=19,
            tags=['computer_use', 'control', 'scroll'],
        ),
        _cap(
            name='drag',
            title='拖拽',
            description='从起点拖拽到终点（元素或坐标）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=20,
            tags=['computer_use', 'control', 'drag'],
        ),
        _cap(
            name='set_value',
            title='设置元素值',
            description='直接设置元素的值（表单批量填写优先于逐字 type）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=21,
            tags=['computer_use', 'control', 'set_value'],
        ),
        _cap(
            name='focus_app',
            title='前台化 App',
            description='前台化目标 App（动手前确认目标软件在前台）。',
            scope=_CONTROL_SCOPE,
            ask=True,
            risk='high',
            page_rank=22,
            tags=['computer_use', 'control', 'focus_app'],
        ),
        # —— 启动/关闭 App 与浏览器自动化（各自独立 scope；launch_app 低风险出厂 Allow，kill/page 出厂 Ask）——
        _cap(
            name='launch_app',
            title='启动/打开 App',
            description='启动或打开桌面 App（可带打开目标文件/URL、可为浏览器开调试端口供后续页面自动化）；'
            '后台启动不抢焦点、非破坏，出厂放行。',
            scope=_LAUNCH_APP_SCOPE,
            ask=False,
            risk='low',
            page_rank=23,
            tags=['computer_use', 'launch_app'],
        ),
        _cap(
            name='kill_app',
            title='关闭/强退 App',
            description='强制退出桌面 App（破坏性——未保存内容可能丢失），默认逐次审批。',
            scope=_KILL_APP_SCOPE,
            ask=True,
            risk='high',
            page_rank=24,
            tags=['computer_use', 'kill_app'],
        ),
        _cap(
            name='page',
            title='浏览器页面自动化',
            description='驱动浏览器页面——跳转/取文本/查元素/执行 JS（在已开浏览器里操作网页），默认逐次审批。',
            scope=_BROWSER_SCOPE,
            ask=True,
            risk='high',
            page_rank=25,
            tags=['computer_use', 'browser', 'page'],
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Platform/Local，经 daemon 托管 cua-driver 落桌面）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_computer_use_app() -> App:
    """computer_use App（local_tool / 内联路由 / 非自动挂载）。

    - ``install_policy='manual'``：能力型应用按需装（cua-driver 引擎随桌面端下发 + macOS TCC 授权），
      非默认挂载；注册到 app_catalog_registry 是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门：
      ``workspace_scope ⊆ scope`` 且 ``collaboration_mode`` 相等）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui（非 embedded sidecar）。

    延迟导入 App 避免循环依赖（app_catalog_registry 模块加载即 default() 反向引用本模块）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='computer_use',
        name='桌面控制',
        icon='brand-computer-use',
        description='让分身看懂屏幕、代你操作电脑——截屏观察、点击输入、拖拽填单，把重复的 GUI 活儿交给分身。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/computer-use',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
