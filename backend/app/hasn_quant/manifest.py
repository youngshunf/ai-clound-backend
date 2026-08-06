"""唤星量化交易引擎接入应用（quant，源自 NautilusTrader，模块 14 doc23）AI-Native catalog + 内置 manifest。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/23-NautilusTrader量化交易引擎(云服务·工具即服务)接入设计.md
§3（cloud-brokered 架构）/§6（分身工具面 hasn.quant.*）/§7（桌面端 UI/UX /apps/quant）。

quant 是 **cloud-brokered** AI-Native 应用（对齐 creator/finance，非 reel/film 的 local_tool）：
- 分身经**云端 MCP** 调 `hasn.quant.*`（Agent MCP Key → `/api/v1/mcp/streamable` → `app_tool_loader` 投影成
  AppTool → `ai_native_runtime_gateway`（transport=gateway_internal）→ 进程内直调
  `app/mcp/apps/quant/quant_tool_handlers.py` → `quant_service`（落 hasn_quant PG）→ `quant_engine_provider`
  → 内网 REST 调引擎服务（hasn-apps/quant-engine-service）跑真回测）。
- 产品数据权威全在唤星 PG（不变量 #3）；引擎服务只持运行态（Redis），不存产品数据。

⚠️ 本期（P0–P5 回测研究平台，零资金风险）只暴露 5 个回测/读/存工具（出厂 allow）：
  save_strategy（quant:write）/ list_strategies·get_strategy·get_backtest（quant:read）/ backtest（quant:backtest）。
  实盘线（deploy_live/submit_order/resume，出厂 ask，scope quant:trade/quant:deploy，P6+）受 P0-闸1 产品/法务
  硬闸（做不做实盘资金托管）+ 真钱 gated，本期**不接实盘执行**、不在 manifest 暴露。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

_SCOPE_READ = 'quant:read'
_SCOPE_WRITE = 'quant:write'
_SCOPE_BACKTEST = 'quant:backtest'

# 引擎内置数据集（quant-engine-service 合成行情，§6；回测无需上传数据，分身按调性选场景）。
# ⚠️ 须与引擎 CATALOG（真相源）+ webui DATASETS 同源：
#   hasn-apps/quant-engine-service/service/datasets.py::CATALOG
#   hasn-node/webui/src/pages/apps/quant/QuantWorkbenchPage.tsx::DATASETS
# 多标的（ETH/BTC/ADA）× 多形态（震荡/趋势/波动/反转/横盘/暴涨暴跌/牛熊）。仍是合成数据，仅供策略管线验证。
_DATASETS = [
    # ETH（历史键，向后兼容已有回测）
    'synthetic-oscillator-eth',
    'synthetic-uptrend-eth',
    'synthetic-downtrend-eth',
    'synthetic-volatile-eth',
    # BTC
    'synthetic-uptrend-btc',
    'synthetic-oscillator-btc',
    'synthetic-reversal-btc',
    'synthetic-spike-crash-btc',
    # ADA
    'synthetic-sideways-ada',
    'synthetic-bull-bear-ada',
]


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    scope: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.quant.* 能力声明（回测/读/存，出厂 allow 免确认）。

    ``name`` 既是 capability/tool_id 的扁平标识，也是 ``hasn.quant.<name>`` 的后缀（本期工具名无层级）。
    本期 5 工具 risk_level 均 low（回测沙箱 + 不动钱）、human_confirmation.required=False（出厂 Allow）。
    """
    return {
        'capability_id': f'hasn_quant.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_quant.{name}',
        'mcp_name': f'hasn.quant.{name}',
        'required_scopes': [scope],
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
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


def _tool_from_cap(cap: dict) -> dict:
    """从 capability 派生 `tools[]`（transport=gateway_internal，云端进程内直调 handler）。

    ``handler`` = ``quant.<flat_name>``（tool_id 去 ``hasn_quant.`` 前缀换 ``quant.``），对应
    `ai_native_runtime_gateway._internal_handlers()` 注册表键 → `quant_tool_handlers.handle_<name>`。
    幂等性按 scope 推导：纯读（quant:read）可安全重试 → idempotent=True；写/提交回测 → False（每次产生新行/新 job）。
    """
    scopes = list(cap.get('required_scopes') or [])
    return {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_quant.', 'quant.', 1),
        'required_scopes': scopes,
        'risk_level': cap['risk_level'],
        'idempotent': scopes == [_SCOPE_READ],
    }


# 5 个回测线工具（设计 §6）。顺序即 tools[] 顺序。
_CAPABILITIES = [
    _cap(
        name='save_strategy',
        title='保存/更新量化策略',
        description='保存或更新一个量化策略（代码 + 参数 + 标的）。新建必带 name 且二选一提供 code(+strategy_class) '
        '或 builtin_strategy；传 strategy_id 则按 owner 隔离更新（version 自增）。只存不跑、不动钱。',
        scope=_SCOPE_WRITE,
        properties={
            'strategy_id': {'type': 'integer', 'minimum': 1, 'description': '更新已存策略时传其 id；新建则不传'},
            'name': {'type': 'string', 'minLength': 1, 'description': '策略名称（新建必填）'},
            'description': {'type': 'string', 'description': '策略说明（可选）'},
            'code': {'type': 'string', 'description': '策略 Python 代码（自写策略必填，与 builtin_strategy 二选一）'},
            'strategy_class': {'type': 'string', 'description': 'code 中策略类名（自写策略须与 code 配套）'},
            'builtin_strategy': {
                'type': 'string',
                'description': '内置策略键（如 ema_cross_long_only；与 code 二选一，免写代码直接回测）',
            },
            'params': {'type': 'object', 'description': '策略参数（如 {"fast_ema_period":10,"slow_ema_period":20}）'},
            'instrument_ids': {'type': 'array', 'items': {'type': 'string'}, 'description': '标的代码列表（可选）'},
            'venue': {'type': 'string', 'description': '场所标识（可选；实盘线 P6+ 才生效）'},
        },
        required=[],
        page_rank=10,
        tags=['quant', 'strategy', 'write'],
    ),
    _cap(
        name='list_strategies',
        title='列出我的量化策略',
        description='列出当前主人名下的全部量化策略（owner 隔离，含状态/版本/最近一次回测 id）。只读。',
        scope=_SCOPE_READ,
        properties={},
        required=[],
        page_rank=11,
        tags=['quant', 'strategy', 'read'],
    ),
    _cap(
        name='get_strategy',
        title='查看量化策略详情',
        description='按 strategy_id 读取一个量化策略详情（代码 + 参数 + 标的 + 状态，owner 隔离）。只读。',
        scope=_SCOPE_READ,
        properties={
            'strategy_id': {'type': 'integer', 'minimum': 1, 'description': '策略 id'},
        },
        required=['strategy_id'],
        page_rank=12,
        tags=['quant', 'strategy', 'read'],
    ),
    _cap(
        name='backtest',
        title='提交策略回测',
        description='提交一次历史回测（job 式：立即返回 queued/running，用 get_backtest 轮询绩效）。策略来源：'
        'strategy_id（载已存策略）或内联 builtin_strategy / strategy_code+strategy_class。只花算力、不动钱，可随便迭代。',
        scope=_SCOPE_BACKTEST,
        properties={
            'strategy_id': {'type': 'integer', 'minimum': 1, 'description': '回测已存策略时传其 id（与内联三选一）'},
            'builtin_strategy': {'type': 'string', 'description': '内置策略键（如 ema_cross_long_only，免代码回测）'},
            'strategy_code': {'type': 'string', 'description': '内联策略 Python 代码（与 strategy_class 配套）'},
            'strategy_class': {'type': 'string', 'description': '内联 strategy_code 中的策略类名'},
            'dataset': {
                'type': 'string',
                'enum': list(_DATASETS),
                'default': 'synthetic-oscillator-eth',
                'description': '回测数据集（引擎内置合成行情，多标的 ETH/BTC/ADA × 多形态：'
                '震荡/上升/下降/高波动/趋势反转/横盘整理/暴涨暴跌/牛熊切换）',
            },
            'params': {'type': 'object', 'description': '覆盖策略参数（可选）'},
            'starting_balance': {'type': 'number', 'exclusiveMinimum': 0, 'default': 1000000, 'description': '初始资金'},
            'trade_size': {'type': 'number', 'exclusiveMinimum': 0, 'description': '单笔下单量（可选）'},
            'fast_ema_period': {'type': 'integer', 'minimum': 1, 'description': '快线周期（ema 策略，可选）'},
            'slow_ema_period': {'type': 'integer', 'minimum': 1, 'description': '慢线周期（ema 策略，可选）'},
        },
        required=[],
        page_rank=13,
        tags=['quant', 'backtest'],
    ),
    _cap(
        name='get_backtest',
        title='读取回测结果',
        description='按 backtest_id 读取一次回测的状态与绩效（含惰性轮询引擎落绩效：pnl/回撤/夏普/胜率/资金曲线，'
        'owner 隔离）。失败如实透传引擎真实错误，绝不臆造绩效（零 fake）。只读。',
        scope=_SCOPE_READ,
        properties={
            'backtest_id': {'type': 'integer', 'minimum': 1, 'description': '回测任务 id（backtest 返回）'},
        },
        required=['backtest_id'],
        page_rank=14,
        tags=['quant', 'backtest', 'read'],
    ),
]


QUANT_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'quant',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'quant': '量化交易（策略回测/数据集/结果）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'project_aware': False,
    'project_required': False,
    'project_integration': 'artifact_only',
    'execution_mode': 'cloud',
    # cloud-brokered 工具模型（对齐 creator/finance）：工具数据面经 gateway_internal 进程内直调云端 handler →
    # quant_service（落 hasn_quant PG）→ quant_engine_provider → quant-engine-service，不经本地 hasn-mcp / daemon
    # Agent 代理（量化的回测/绩效是集中托管的云端业务，真钱要集中审计/风控/计费）。
    'transport_mode': 'cloud',
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '量化交易',
        }
    },
    # 资源描述符（doc31 §2，RC-P6）：量化策略回测 → hasn://quant/strategies/{server_id}，应用内详情路由打开
    # （RC-P6 补 webui /apps/quant/strategies/:id 详情页）。
    'resources': [
        {
            'resource_kind': 'quant.strategy',
            'uri_domain': 'quant/strategies',  # → hasn://quant/strategies/{server_id}（doc08 §3 登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/quant/strategies/:id'},
            'card': {'verb': '策略回测', 'action_label': '打开回测'},
            'artifact_kind': 'resource',
        },
        # doc35 A3 补：回测报告此前**从来没有 descriptor**——分身跑完回测，这份成果没有 URI、没有
        # 完成卡、登记也无从挂靠，于是 hub README 只好教模板作者绕道「假装落 knowledge 出 dataset」。
        # 绕路的根因不是「登记不可靠」，是这个资源压根没声明过。
        #
        # 用 entry_query 而非 internal_route：回测没有独立详情页，它在工作台页由 `?backtest=` 选中
        # 呈现（webui QuantWorkbenchPage 的 BacktestResultPanel）。照 strategy 写成 /:id 路由会造出
        # 一条点开是 404 的深链。
        {
            'resource_kind': 'quant.backtest',
            'uri_domain': 'quant/backtests',  # → hasn://quant/backtests/{server_id}
            'open': {'mode': 'entry_query', 'entry_route': '/apps/quant', 'query_key': 'backtest'},
            'card': {'verb': '回测报告', 'action_label': '查看回测报告'},
            'artifact_kind': 'resource',
        },
    ],
    'capabilities': _CAPABILITIES,
    'tools': [_tool_from_cap(cap) for cap in _CAPABILITIES],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_quant_app() -> App:
    """quant App（cloud-brokered / 自建量化工作台 /apps/quant / 按需安装）。

    - ``execution_mode='cloud'``：分身工具经云端 MCP → 云端后端 Broker → 引擎服务（非本地工具、非 sidecar iframe）。
    - ``install_policy='manual'``：量化是专业能力，按需装、不自动挂载到工作台（对齐 growth/creator）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)``：个人模式（企业双模归属 §4.9 远期）。
    - ``entry_route='/apps/quant'``：自建量化工作台（策略库 + 回测；实盘线 P6+ 环境就绪才显，§7.1）。
    - ``default_agent_type`` 由 catalog DB 行承载（analyst「金融理财专家」，量化回测专属工作会话提示词），不在 App dataclass。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='quant',
        name='量化交易',
        icon='brand-quant',
        description='把你的交易想法交给分身写成策略、跑历史回测、出绩效报告、反复打磨——回测阶段不碰真钱，验证成熟再上实盘。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/quant',
        install_policy='manual',
        execution_mode='cloud',
        ui_kind=None,
    )
