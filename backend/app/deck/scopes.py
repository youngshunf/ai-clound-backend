"""deck（自研演示文稿系统）scope 展示元数据。

设计事实源：模块 17 §6；16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，
由 `app/mcp/scopes.py` 聚合）。判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

写类 8 工具统一 `deck:manage`（出厂 Allow，owner 三态可覆盖）；读类 4 工具无 required_scopes
（不在此登记，避免假闸门）。
"""

from __future__ import annotations

DECK_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'deck:manage': {
        'label_zh': '管理演示文稿',
        'domain': 'deck',
        'risk': 'medium',
        'description': '以 Agent 身份建/改/删主人的演示文稿、页与大纲（owner 隔离；读类无需授权）',
    },
}
