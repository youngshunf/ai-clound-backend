"""唤星量化交易引擎接入应用（quant，源自 NautilusTrader，模块 14 doc23）AI-Native catalog 注册。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/23-NautilusTrader量化交易引擎(云服务·工具即服务)接入设计.md
§3（cloud-brokered 架构）/§6（分身工具面 hasn.quant.*）/§7（桌面端 UI/UX /apps/quant）。

quant 是 **cloud-brokered** AI-Native 应用（对齐 creator/community/task，非 reel/film 的 local_tool）：
- 分身经**云端 MCP** 调 `hasn.quant.*`（提交回测/读绩效/存策略）→ 云端后端 QuantService（唯一 Broker）
  → 内网 REST 调引擎服务（huanxing-apps/quant-engine-service）跑真回测 → 落 hasn_quant PG → WSPUSH/MCP 返回。
- 产品数据权威全在唤星 PG（不变量 #3）；引擎服务只持运行态（Redis），不存产品数据。

⚠️ 本期（P0–P5 回测研究平台，零资金风险）：catalog 注册 + 回测线工具（backtest/get_backtest/save_strategy/
list_strategies/get_strategy，出厂 allow）。实盘线（deploy_live/submit_order/resume，出厂 ask，P6+）受
P0-闸1 产品/法务硬闸（做不做实盘资金托管）+ 真钱 gated，本期不接实盘执行。

⚠️ MCP 能力清单（capabilities/handlers/gateway 注册）在 `app/mcp/apps/quant/`（QUANT-P3 待落），
本文件只承载 catalog 的 App 行（工作台展示 + entry_route + install_policy + execution_mode）。

⚠️ 接线待办（QUANT-P2 收尾，被并发 finance 会话占用的共享文件阻塞，待其落库后补）：
  `app/hasn/service/app_catalog_registry.py` 的 `AppCatalogRegistry.default()` 加一行
  `registry.register(build_quant_app())`；`app/mcp/scopes.py` 聚合 `QUANT_SCOPE_CATALOG`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App


def build_quant_app() -> App:
    """quant App（cloud-brokered / 自建量化工作台 /apps/quant / 按需安装）。

    - ``execution_mode='cloud'``：分身工具经云端 MCP → 云端后端 Broker → 引擎服务（非本地工具、非 sidecar iframe）。
    - ``install_policy='manual'``：量化是专业能力，按需装、不自动挂载到工作台（对齐 growth/creator）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)``：个人模式（企业双模归属 §4.9 远期）。
    - ``entry_route='/apps/quant'``：自建量化工作台（策略库 + 回测；实盘线 P6+ 环境就绪才显，§7.1）。
    - ``default_agent_type`` 由 catalog DB 行承载（quant_trader「量化交易官」），不在 App dataclass。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='quant',
        name='量化交易',
        icon='brand-quant',
        description='AI 量化研究工作台——分身写策略、云端跑回测、出绩效报告、迭代优化（回测零资金风险；实盘真钱强闸）。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/quant',
        install_policy='manual',
        execution_mode='cloud',
        ui_kind=None,
    )
