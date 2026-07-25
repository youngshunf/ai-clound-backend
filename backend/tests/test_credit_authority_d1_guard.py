"""doc94 D1 防复发守卫：删掉的东西不许悄悄长回来。

这一轮事故的形态是「云端有第二套余额账本，并反向覆盖 NewAPI」。代码删干净只是一次性的，
真正的风险是半年后有人为了图方便再加一个 `CreditTransaction` 或一个 `quota_to_credits`，
于是双权威悄悄复活。所以把这些标识做成**静态断言**，出现即红。

归档 SQL 与迁移历史豁免（那是历史事实，必须留下）；运行时代码、当前初始化 SQL、
菜单与测试**不豁免**。
"""

from __future__ import annotations

import pathlib
import re

#: 业务目录中出现即失败的标识（doc94 D1「防复发守卫」原文）
FORBIDDEN_IDENTIFIERS = (
    'UserCreditBalance',
    'CreditTransaction',
    'last_synced_used_quota',
    'NEWAPI_QUOTA_PER_DOLLAR',
    'credits_to_quota',
    'quota_to_credits',
    'subscription/purchase',
    'quota/deduct',
)

_BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: 扫描范围：运行时代码 + 当前初始化 SQL + 测试
_SCAN_ROOTS = (
    _BACKEND / 'app',
    _BACKEND / 'core',
    _BACKEND / 'scripts',
    _BACKEND / 'tests',
    _BACKEND / 'sql',
)

#: 豁免：历史迁移、归档、缓存产物，以及本守卫文件自身（它必须写出这些字符串）
_EXEMPT_PATTERNS = (
    re.compile(r'/migrations/'),
    re.compile(r'/archive/'),
    re.compile(r'/__pycache__/'),
    re.compile(r'test_credit_authority_d1_guard\.py$'),
)


def _is_exempt(path: pathlib.Path) -> bool:
    text = str(path)
    return any(pattern.search(text) for pattern in _EXEMPT_PATTERNS)


def _scan() -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix not in {'.py', '.sql'} or _is_exempt(path):
                continue
            try:
                content = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            for identifier in FORBIDDEN_IDENTIFIERS:
                if identifier in content:
                    hits.setdefault(identifier, []).append(str(path.relative_to(_BACKEND)))
    return hits


def test_retired_credit_identifiers_do_not_come_back() -> None:
    """被 D1 删除的标识不得出现在运行时代码、当前初始化 SQL 与测试中。

    命中任何一个都说明双权威在复活：要么有人重建了云端余额账本，要么有人
    又在云端做起了 quota↔credit 换算。两者都会让「余额」重新变成两个数。
    """
    hits = _scan()
    assert not hits, (
        'doc94 D1 已删除的积分标识重新出现——双权威正在复活：\n'
        + '\n'.join(f'  {identifier}: {", ".join(sorted(set(paths)))}' for identifier, paths in sorted(hits.items()))
    )


def test_guard_actually_scans_something() -> None:
    """守卫自身的自检：扫描范围必须真的存在，否则「全绿」只是因为什么都没扫。"""
    scanned = [root for root in _SCAN_ROOTS if root.exists()]
    assert len(scanned) == len(_SCAN_ROOTS), f'扫描根目录缺失: {[str(r) for r in _SCAN_ROOTS if not r.exists()]}'
