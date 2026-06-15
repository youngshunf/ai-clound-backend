"""创作运营（hasn_creator，app_id=creator）scope 展示元数据。

设计事实源：docs/自媒体创作运营/00-自媒体创作运营全链路AI-Native应用设计.md §6.1；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。

判定真相是工具 required_scopes + 三态 mode；本模块只承载**展示元数据**（中文 label / domain /
risk / 描述）。三个 scope（设计 §6.1）：read（读）/ manage（写漏斗对象与创作）/ publish（请求
发布，对外动作可被主人单独关死，不与普通写混桶）。媒体生成/配音/落桶复用平台级 image/voice/asset
scope，不在本应用重造。
"""

from __future__ import annotations

CREATOR_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'creator:read': {
        'label_zh': '查看创作数据',
        'domain': 'creator',
        'risk': 'low',
        'description': '读项目/画像/账号/选题/内容/阶段产出/发布数据/爆款库/复盘总览（list/get/search/overview 类）',
    },
    'creator:manage': {
        'label_zh': '管理创作流程',
        'domain': 'creator',
        'risk': 'medium',
        'description': '写创作对象：建项目、设/辅助定位画像、加账号、记竞品、生成选题、建/改内容（状态机）、保存阶段产出、回填数据、沉淀洞察',
    },
    'creator:publish': {
        'label_zh': '请求发布内容',
        'domain': 'creator',
        'risk': 'high',
        'description': '请求把内容发布到平台账号（进审核队列，默认 pending_review 等主人审；hasn.creator.publish.submit）',
    },
}
