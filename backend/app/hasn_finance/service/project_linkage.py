"""金融投研两容器（策略 / 影子账户）的平台项目挂靠 adapter 注册（doc38 §4 层2 / 05 §4）。

import 即把两个容器级 LinkageAdapter 注册进 project_linkage_registry：
- `finance/strategies` → Strategy.platform_project_id（长生命周期容器：建→回测→迭代跨数周）
- `finance/shadow` → ShadowAccount.platform_project_id（复盘容器：季季对比的连续体）

`domain` 必须与各自 ResourceDescriptor.uri_domain 完全一致；两者 id 均为整型主键
（id_is_uuid=False），is_container=True 参与项目总览并集读反查。项目=视角，非权限边界/挂载点/
容器接管（doc38 三铁律）。由 ai_native_app_registry 在 import 链上加载。
"""

from __future__ import annotations

from backend.app.hasn_finance.model.shadow_account import ShadowAccount
from backend.app.hasn_finance.model.strategy import Strategy
from backend.app.hasn_project.service.project_linkage_registry import LinkageAdapter, project_linkage_registry

# 策略容器：长生命周期，项目总览要能看到「沉淀了哪些策略」（doc38 §4 层2 表）
project_linkage_registry.register(
    LinkageAdapter(
        domain='finance/strategies',
        model=Strategy,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
    )
)

# 影子账户容器：复盘的季季连续体，可整体挂进改进类项目（doc38 §4 层2 表）
project_linkage_registry.register(
    LinkageAdapter(
        domain='finance/shadow',
        model=ShadowAccount,
        id_column='id',
        owner_column='owner_id',
        attach_column='platform_project_id',
        id_is_uuid=False,
        is_container=True,
    )
)
