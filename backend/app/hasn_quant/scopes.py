"""quant（NautilusTrader 量化交易引擎接入应用，模块 14 doc23）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/23-NautilusTrader量化交易引擎(云服务·工具即服务)接入设计.md §6；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

三态出厂默认（doc23 §6，贴「出厂默认是唯一真相」+「花钱/外发出厂即 ask」）：
- 回测 / 读 / 存策略 = **allow**（安全沙箱 + 廉价 + 不动钱，分身随便迭代）；
- 降风险动作（pause/stop/cancel_all）= **allow**（随时能止损，不卡审批）；
- 加真钱风险动作（deploy_live/resume/submit_order）= **ask**（走审批卡链路，主人裁决；P6+）。

⚠️ cloud-brokered：工具注册在**云端 MCP**（对齐 creator/community/task），非本地工具。
本表是云端侧契约源；daemon `crates/.../quant` 侧（P4 待落）须与 required_scopes 对齐（跨仓零漂移守卫）。

凭据管理（venue key 增删）= **owner 经 webui** 的动作，无对应 MCP 写工具，不进本 scope 表。
"""

from __future__ import annotations

QUANT_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'quant:read': {
        'label_zh': '查看量化策略与回测',
        'domain': 'quant',
        'risk': 'low',
        'description': '以 Agent 身份读取量化策略、回测任务/绩效、（实盘）部署/持仓/盈亏（只读，owner 隔离）',
    },
    'quant:backtest': {
        'label_zh': '发起回测',
        'domain': 'quant',
        'risk': 'low',
        'description': '提交策略回测（job 式，沙箱内跑历史数据出绩效）：只花算力、不动钱，分身可随便迭代',
    },
    'quant:write': {
        'label_zh': '保存/更新策略',
        'domain': 'quant',
        'risk': 'low',
        'description': '保存/更新量化策略代码 + 参数 + 标的（不动钱，AI 生成策略走沙箱执行）',
    },
    # —— 以下为实盘线（P6+，真钱强闸）——
    'quant:trade': {
        'label_zh': '实盘交易控制',
        'domain': 'quant',
        'risk': 'high',
        'default_mode': 'ask',
        'description': '实盘交易动作（手动下单/恢复运行=动真钱需主人确认；暂停/停止/撤所有单=降风险可放行，工具级裁定）',
    },
    'quant:deploy': {
        'label_zh': '部署实盘',
        'domain': 'quant',
        'risk': 'high',
        'default_mode': 'ask',
        'description': '把策略部署到实盘/模拟盘（live=动真钱，强制审批卡 + 二次确认 + 资金敞口提示）',
    },
}
