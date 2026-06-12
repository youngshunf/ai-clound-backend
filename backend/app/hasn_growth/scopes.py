"""获客（hasn_growth，app_id=growth）scope 展示元数据。

设计事实源：docs/AI自动获客任务系统/07-获客营销全链路AI-Native应用设计.md §3.4 + §10.2；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。

判定真相是工具 required_scopes + 三态 mode；本模块只承载**展示元数据**（中文 label / domain /
risk / 描述）。触达单独成 scope（`growth:outreach`）：对外动作可被主人单独关死，不与普通写混桶。
`growth:pii` 是**升级 scope**——读类工具默认脱敏（§10.2），仅持 pii 才回明文，故不进任何工具的
required_scopes 硬清单，只作为服务层条件判定的能力键登记于此。
"""

from __future__ import annotations

HASN_GROWTH_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'growth:read': {'label_zh': '查看获客数据', 'domain': 'growth', 'risk': 'low', 'description': '读线索/客户/商机/活动时间线/漏斗报表（search/get/list/timeline/funnel/status 类，PII 默认脱敏）'},
    'growth:manage': {'label_zh': '管理获客漏斗', 'domain': 'growth', 'risk': 'medium', 'description': '写漏斗对象：线索晋级/不合格、更新画像、立/推商机、记活动、成交登记（qualify/dismiss/update_profile/log/opportunity.*/deal.close）'},
    'growth:outreach': {'label_zh': '发起对外触达', 'domain': 'growth', 'risk': 'high', 'description': '请求发送对外触达（进审批队列，默认 pending_approval；hasn.growth.outreach.send）'},
    'growth:collect': {'label_zh': '驱动线索采集', 'domain': 'growth', 'risk': 'medium', 'description': '按 playbook/关键词发起采集任务（hasn.growth.collect.start）'},
    'growth:pii': {'label_zh': '查看获客明文联系方式', 'domain': 'growth', 'risk': 'high', 'description': '升级 scope：解除读类工具的 PII 脱敏，返回明文邮箱/电话（§10.2，默认不授予）'},
}
