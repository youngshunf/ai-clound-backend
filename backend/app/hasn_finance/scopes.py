"""金融数据（hasn_finance，app_id=finance）scope 展示元数据。

设计：docs/hasn-node设计文档/14-AI-Native应用平台/24-金融数据源(akshare)行情与投研应用接入设计.md §4C.1。
判定真相是工具 required_scopes + 三态 mode；本模块只承载**展示元数据**（中文 label / domain / risk / 描述）。

只读数据源——**单 scope `finance:read`，risk=low**（无下单/无真钱/无写类）。出厂 Allow。
"""

from __future__ import annotations

FINANCE_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'finance:read': {
        'label_zh': '查询行情与投研数据',
        'domain': 'finance',
        'risk': 'low',
        'description': '读 A股/港美股/基金/期货/债券/指数行情与 K 线、个股资金流/财务/基本面、龙虎榜、宏观指标（只读数据源，无下单、不构成投资建议）',
    },
}
