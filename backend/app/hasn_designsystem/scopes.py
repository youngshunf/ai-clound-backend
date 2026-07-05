"""designsystem（自研设计系统生成应用）scope 展示元数据。

设计事实源：设计 §7（安全/权限）；16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，
由 `app/mcp/scopes.py` 聚合）。判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（P4 hasn-mcp `crates/hasn-mcp/src/designsystem.rs`）：
- 写类工具（save/import/compile/derive 等改 owner 资料的）统一 `designsystem:write`（出厂 Allow，owner 三态可覆盖）；
- 发布/分享类（publish/share）统一 `designsystem:publish`（按需，默认 Ask）；
- 读类工具（list/get/validate/extract 纯函数）无 required_scopes（不在此登记，避免假闸门）。
"""

from __future__ import annotations

DESIGNSYSTEM_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'designsystem:write': {
        'label_zh': '管理设计系统',
        'label_en': 'Manage design system',
        'domain': 'designsystem',
        'risk': 'medium',
        'description': '以 Agent 身份建/改/存主人的设计系统与版本（owner 隔离；读类与确定性纯函数无需授权）',
        'description_en': "Create, edit, and save the owner's design systems and versions as the agent (owner-isolated; reads and deterministic pure functions need no authorization)",
    },
    'designsystem:publish': {
        'label_zh': '发布/分享设计系统',
        'label_en': 'Publish/share design system',
        'domain': 'designsystem',
        'risk': 'medium',
        'description': '把设计系统分享给指定 HASN 身份或发布为官方库（按需授权，默认需主人确认）',
        'description_en': 'Share a design system with a specific HASN identity or publish it as an official library (authorized on demand; owner confirmation required by default)',
    },
}
