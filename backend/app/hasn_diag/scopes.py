"""错误诊断（diag，模块 21 doc21）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/21-错误诊断与可观测性/00-错误日志自动上云与Agent分析设计.md §8；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据（中文 label / domain / risk / 描述）。

⚠️ 特权口径（唯一与普通应用不同处）：diag:* 命中 `platform_scopes.PRIVILEGED_SCOPE_PREFIXES`
（跨切面 G1 平台特权门·doc18 U2）——仅经 Admin 授予表拿到 diag:* 的「平台运维分析师」分身
可见/可调，普通分身发现面隐身。出厂 Allow（无人值守运维，声明 Ask 会死锁 §10 循环）；
possession 由 G1 把关、不靠三态。两 scope 另登记进 `platform_scopes.PRIVILEGED_SCOPES`
安全白名单（守卫强制）——那是跨切面的特权注册表，与本文件的展示元数据是两个正交关注点。
"""

from __future__ import annotations

DIAG_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'diag:read:all': {
        'label_zh': '读平台错误全量',
        'label_en': 'Read all platform errors',
        'domain': 'diag',
        'risk': 'medium',
        'description': (
            '跨 owner 读取平台错误 issue 列表/详情/occurrence/统计（平台运维特权，非普通分身）'
        ),
        'description_en': 'Read platform error issues, details, occurrences, and stats across owners (platform-ops privilege, not a regular agent)',
    },
    'diag:manage': {
        'label_zh': '管理平台错误 issue',
        'label_en': 'Manage platform error issues',
        'domain': 'diag',
        'risk': 'high',
        'description': (
            '改 issue 状态（investigating/resolved/skipped/wontfix）、挂 issue/PR 链接'
            '（平台运维特权，写审计留痕）'
        ),
        'description_en': 'Change issue status (investigating/resolved/skipped/wontfix) and attach issue/PR links (platform-ops privilege, audited writes)',
    },
}
