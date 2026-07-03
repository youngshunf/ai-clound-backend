"""语义化版本比较（用于 resolved issue 的版本感知重开）。

doc21 §5.2：occurrence 的 app_version 比 fixed_in_version 新 → 重开。版本号形如
`1.2.3` / `2026.07.01`；解析为整数元组按序比较，任一段非数字则退化为字符串比较。
无法证明「更新」（如 app_version 缺失）时保守判定为不更新，避免误重开死循环。
"""

from __future__ import annotations


def _parse(version: str) -> tuple:
    parts: list[int | str] = []
    for seg in version.replace('-', '.').split('.'):
        seg = seg.strip()
        if not seg:
            continue
        parts.append(int(seg) if seg.isdigit() else seg)
    return tuple(parts)


def is_newer(candidate: str | None, baseline: str | None) -> bool:
    """`candidate` 是否严格新于 `baseline`。

    - baseline 为空 → 视为「未标注修复版本」，任何 candidate 都算新（触发重开）；
    - candidate 为空 → 无法证明更新，返回 False（保守不重开）。
    """
    if not baseline:
        return True
    if not candidate:
        return False
    ca, cb = _parse(candidate), _parse(baseline)
    try:
        return ca > cb
    except TypeError:
        # 数字段与字符串段混比 → 退化为原始字符串比较
        return candidate > baseline
