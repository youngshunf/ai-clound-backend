"""金融投研（hasn_finance，app_id=finance）AI-Native 内置 manifest + App 声明。

事实源：docs/hasn-node设计文档/金融投研与量化交易/02-分身工具面与swarm编排设计.md。

当前形态：
- `execution_mode='local_tool'`、`transport_mode='local'`：最终 44 个 `hasn.finance.*` 工具由
  hasn-node 本地 MCP 与 Owner 隔离的 Vibe-Trading 引擎执行，云端 manifest 不重复声明 tools。
- 六类产物继续由云端表、严格 `:sync` 与 register-on-write 承接。
- `project_aware=true`、`project_required=false`：支持可选项目归集，不选项目也能完整使用。

旧 14 个云端只读 handler 在 F8 退役前仅服务旧客户端，不再进入新工具发现面。
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

_SCOPE_READ = 'finance:read'

# 资源描述符（doc31 §2 / 05 §1.1 权威表）：finance 是**多资源**应用——六类产物各占一个
# `hasn://finance/<域>/{id}` 域。**本模块必须声明 `ref_type`**（有意识的决策，05 §1.2 第 4 条）：
# `ref_type` 是 opt-in 的——任一 descriptor 声明它，整个 app 进多资源模式，此后 register-on-write 的
# `origin_ref` 恒为 `resource:finance:{ref_type}:{server_id}`（如 resource:finance:strategy:42），
# 派发工作会话据 `origin_ref` 的 `ref_type` 段反查是哪类资源。判据是**要不要按业务对象反查**：流程 B/C
# 要支持「从策略详情页 / 影子账户详情页直接派分身协作」，那条链路正靠 ref_type 段选中本 descriptor。
# URI 只准由 `ResourceDescriptor.build_uri()` 拼（全仓唯一拼接点），别处不得再写 f'hasn://finance/...'。
_FINANCE_RESOURCES = [
    {
        'resource_kind': 'finance.research_report',
        'ref_type': 'research',  # origin_ref=resource:finance:research:{id}
        'uri_domain': 'finance/reports',  # → hasn://finance/reports/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/reports/:id'},
        'card': {'verb': '投研报告', 'action_label': '打开报告'},
        'artifact_kind': 'resource',
    },
    {
        'resource_kind': 'finance.strategy',
        'ref_type': 'strategy',  # origin_ref=resource:finance:strategy:{id}
        'uri_domain': 'finance/strategies',  # → hasn://finance/strategies/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/strategies/:id'},
        'card': {'verb': '策略', 'action_label': '打开策略'},
        'artifact_kind': 'resource',
    },
    {
        'resource_kind': 'finance.backtest_report',
        'ref_type': 'backtest',  # origin_ref=resource:finance:backtest:{id}
        'uri_domain': 'finance/backtests',  # → hasn://finance/backtests/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/backtests/:id'},
        'card': {'verb': '回测报告', 'action_label': '打开回测'},
        'artifact_kind': 'resource',
    },
    {
        'resource_kind': 'finance.trade_review',
        'ref_type': 'review',  # origin_ref=resource:finance:review:{id}
        'uri_domain': 'finance/reviews',  # → hasn://finance/reviews/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/reviews/:id'},
        'card': {'verb': '复盘报告', 'action_label': '打开复盘'},
        'artifact_kind': 'resource',
    },
    {
        'resource_kind': 'finance.shadow_account',
        'ref_type': 'shadow',  # origin_ref=resource:finance:shadow:{id}
        'uri_domain': 'finance/shadow',  # → hasn://finance/shadow/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/shadow/:id'},
        'card': {'verb': '影子账户', 'action_label': '打开账户'},
        'artifact_kind': 'resource',
    },
    {
        'resource_kind': 'finance.watch_briefing',
        'ref_type': 'briefing',  # origin_ref=resource:finance:briefing:{id}
        'uri_domain': 'finance/briefings',  # → hasn://finance/briefings/{id}
        'open': {'mode': 'internal_route', 'route_template': '/apps/finance/briefings/:id'},
        'card': {'verb': '盯盘简报', 'action_label': '打开简报'},
        'artifact_kind': 'resource',
    },
]

# 通用历史 K 线入参（A股/港股/美股/指数共用骨架）。
_KLINE_PROPS = {
    'symbol': {'type': 'string', 'minLength': 1, 'description': '标的代码（A股 6 位如 600519 / 港股 5 位如 00700 / 美股如 105.AAPL / 指数如 000001）'},
    'period': {'type': 'string', 'enum': ['daily', 'weekly', 'monthly'], 'default': 'daily', 'description': 'K 线周期'},
    'start_date': {'type': 'string', 'description': '起始日 YYYYMMDD（默认 20240101）'},
    'end_date': {'type': 'string', 'description': '结束日 YYYYMMDD（默认至今）'},
    'adjust': {'type': 'string', 'enum': ['', 'qfq', 'hfq'], 'default': 'qfq', 'description': '复权：前复权 qfq / 后复权 hfq / 不复权 \'\''},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'description': '返回行数上限（默认 300）'},
}


def _cap(
    *,
    name: str,
    mcp_suffix: str,
    title: str,
    description: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.finance.* 只读能力声明（全部 finance:read，出厂 Allow 免确认）。

    ``name`` 是 capability/tool_id 用的扁平标识（点号转下划线）；``mcp_suffix`` 是 ``hasn.finance.<suffix>``。
    """
    return {
        'capability_id': f'hasn_finance.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_finance.{name}',
        'mcp_name': f'hasn.finance.{mcp_suffix}',
        'required_scopes': [_SCOPE_READ],
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

    ``handler`` = ``finance.<flat_name>``（tool_id 去 ``hasn_finance.`` 前缀换 ``finance.``），
    对应 `ai_native_runtime_gateway._internal_handlers()` 注册表键 → `finance_tool_handlers.handle_<name>`。
    14 工具全只读（仅 finance:read）→ `idempotent=True`（可安全重试）。
    """
    return {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_finance.', 'finance.', 1),
        'required_scopes': list(cap.get('required_scopes') or []),
        'risk_level': cap['risk_level'],
        'idempotent': True,
    }


# 14 个只读取数工具（设计 §4，签名 S0 已核实 akshare 1.18.64）。顺序即 tools[] 顺序。
_CAPABILITIES = [
    _cap(
        name='stock_quote_history',
        mcp_suffix='stock.quote_history',
        title='A股历史K线',
        description='查 A 股个股历史 K 线（日/周/月，前复权/后复权）。服务端历史增量持久化，省上游。',
        properties=dict(_KLINE_PROPS),
        required=['symbol'],
        page_rank=10,
        tags=['finance', 'stock', 'kline', 'a'],
    ),
    _cap(
        name='stock_realtime',
        mcp_suffix='stock.realtime',
        title='A股实时行情',
        description='查 A 股实时行情（现价/涨跌幅/量额等）。**必须按 symbols 过滤**（逗号分隔多个代码），不支持全市场拉取。',
        properties={
            'symbols': {'type': 'string', 'minLength': 1, 'description': '标的代码，逗号分隔（如 600519,000001）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'description': '返回行数上限'},
        },
        required=['symbols'],
        page_rank=11,
        tags=['finance', 'stock', 'realtime', 'a'],
    ),
    _cap(
        name='stock_info',
        mcp_suffix='stock.info',
        title='个股基本面/简介',
        description='查 A 股个股基本面信息（行业/总股本/流通股/总市值/上市日期等）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': 'A 股代码（如 600519）'},
        },
        required=['symbol'],
        page_rank=12,
        tags=['finance', 'stock', 'info', 'a'],
    ),
    _cap(
        name='stock_fund_flow',
        mcp_suffix='stock.fund_flow',
        title='个股资金流向',
        description='查 A 股个股资金流向（主力/超大单/大单净流入等）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': 'A 股代码（如 600519）'},
            'market': {'type': 'string', 'enum': ['sh', 'sz', 'bj'], 'default': 'sh', 'description': '市场：沪 sh / 深 sz / 北 bj'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=13,
        tags=['finance', 'stock', 'fund_flow', 'a'],
    ),
    _cap(
        name='stock_billboard',
        mcp_suffix='stock.billboard',
        title='龙虎榜',
        description='查指定日期区间龙虎榜明细（上榜个股/营业部/买卖额）。',
        properties={
            'start_date': {'type': 'string', 'description': '起始日 YYYYMMDD'},
            'end_date': {'type': 'string', 'description': '结束日 YYYYMMDD'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['start_date', 'end_date'],
        page_rank=14,
        tags=['finance', 'stock', 'billboard'],
    ),
    _cap(
        name='stock_financial',
        mcp_suffix='stock.financial',
        title='财务摘要',
        description='查 A 股个股财务摘要（营收/净利润/每股收益/ROE 等关键指标历史）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': 'A 股代码（如 600519）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=15,
        tags=['finance', 'stock', 'financial'],
    ),
    _cap(
        name='hk_quote_history',
        mcp_suffix='hk.quote_history',
        title='港股历史K线',
        description='查港股历史 K 线（日/周/月，复权）。',
        properties=dict(_KLINE_PROPS, symbol={'type': 'string', 'minLength': 1, 'description': '港股代码 5 位（如 00700）'}),
        required=['symbol'],
        page_rank=16,
        tags=['finance', 'stock', 'kline', 'hk'],
    ),
    _cap(
        name='us_quote_history',
        mcp_suffix='us.quote_history',
        title='美股历史K线',
        description='查美股历史 K 线（日/周/月，复权）。',
        properties=dict(_KLINE_PROPS, symbol={'type': 'string', 'minLength': 1, 'description': '美股代码（如 105.AAPL）'}),
        required=['symbol'],
        page_rank=17,
        tags=['finance', 'stock', 'kline', 'us'],
    ),
    _cap(
        name='index_quote_history',
        mcp_suffix='index.quote_history',
        title='指数历史K线',
        description='查指数历史 K 线（上证/深证/创业板等，日/周/月）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': '指数代码（如 000001 上证指数）'},
            'period': _KLINE_PROPS['period'],
            'start_date': _KLINE_PROPS['start_date'],
            'end_date': _KLINE_PROPS['end_date'],
            'limit': _KLINE_PROPS['limit'],
        },
        required=['symbol'],
        page_rank=18,
        tags=['finance', 'index', 'kline'],
    ),
    _cap(
        name='fund_nav_history',
        mcp_suffix='fund.nav_history',
        title='基金历史净值',
        description='查开放式基金历史净值走势（单位净值/累计净值）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': '基金代码（如 000001）'},
            'indicator': {'type': 'string', 'default': '单位净值走势', 'description': '指标（默认 单位净值走势）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=19,
        tags=['finance', 'fund', 'nav'],
    ),
    _cap(
        name='fund_position',
        mcp_suffix='fund.position',
        title='基金持仓',
        description='查基金持仓明细（前十大重仓股/占比）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': '基金代码（如 000001）'},
            'date': {'type': 'string', 'description': '报告年份（如 2024）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=20,
        tags=['finance', 'fund', 'position'],
    ),
    _cap(
        name='futures_quote_history',
        mcp_suffix='futures.quote_history',
        title='期货历史行情',
        description='查期货合约历史日线行情（开高低收/成交量/持仓量）。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': '期货合约代码（如 V2501）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=21,
        tags=['finance', 'futures', 'kline'],
    ),
    _cap(
        name='macro_indicator',
        mcp_suffix='macro.indicator',
        title='宏观指标',
        description='查中国宏观经济指标（CPI/PPI/GDP/PMI）。按 indicator 路由。',
        properties={
            'indicator': {'type': 'string', 'enum': ['cpi', 'ppi', 'gdp', 'pmi'], 'default': 'cpi', 'description': '指标：cpi/ppi/gdp/pmi'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['indicator'],
        page_rank=22,
        tags=['finance', 'macro'],
    ),
    _cap(
        name='bond_quote_history',
        mcp_suffix='bond.quote_history',
        title='债券历史行情',
        description='查沪深债券历史日线行情。',
        properties={
            'symbol': {'type': 'string', 'minLength': 1, 'description': '债券代码（如 sh010107）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        },
        required=['symbol'],
        page_rank=23,
        tags=['finance', 'bond', 'kline'],
    ),
]


FINANCE_AI_NATIVE_MANIFEST = {
    'app_id': 'finance',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'finance': '金融投研（数据、专家团队、策略回测、交易复盘与盯盘简报）'},
    'version': '2.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'project_aware': True,
    'project_required': False,
    # 六类产物资源描述符（05 §1.1）：完成卡 / 工作会话资源栏 / URI 解析 / 详情跳转全从这份声明派生。
    'resources': _FINANCE_RESOURCES,
    'execution_mode': 'local_tool',
    # 方案 A：工具数据面在 hasn-node 本地 MCP，经 daemon owner 隔离引擎执行；云端不重复暴露 tools。
    'transport_mode': 'local',
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '金融投研',
        }
    },
    'capabilities': [],
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_finance_app() -> App:
    """构造 finance 的 App（catalog seed 源 + 工作台入口）。

    UI 为 webui 原生路由（内联导航至 ``/apps/finance``，同 knowledge/community）。
    execution_mode=local_tool（本地 Vibe-Trading 引擎）；install_policy=manual（按需安装引擎）。
    项目档位为可选挂靠：支持策略/影子账户容器与六类产物归集，不选择项目也可使用。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='finance',
        name='金融投研',
        icon='brand-finance',
        description='本地优先的金融投研工作台：行情与宏观、专家团队、策略回测、交易复盘、自选盯盘一体化，研究结果不构成投资建议。',
        scope=('personal', 'enterprise'),
        collaboration_mode='workspace_shared',
        entry_route='/apps/finance',
        install_policy='manual',
        execution_mode='local_tool',
        project_aware=True,
        project_required=False,
    )
