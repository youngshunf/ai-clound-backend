"""Agent 三态能力授权判定（维度①）。

设计事实源：docs/hasn-node设计文档/MCP统一工具体系/13-Agent权限模型与工具目录设计.md §3.1。

两个正交维度，本模块只管 **维度① 能力授权**（owner→agent，三态 allow/ask/deny，
默认全 allow、统一适用所有工具）。**维度② 对象可达性**（能否给某人发消息，由关系/
信任运行时判定）不在这里，由工具内 `message_router.check_relation_permission` 返回结果。
"""

from __future__ import annotations

from typing import Any

MODE_ALLOW = 'allow'
MODE_ASK = 'ask'
MODE_DENY = 'deny'

VALID_MODES = (MODE_ALLOW, MODE_ASK, MODE_DENY)

# 限制度排序：deny > ask > allow。聚合一个工具的多个 scope 时取最严。
_RESTRICTIVENESS = {MODE_ALLOW: 0, MODE_ASK: 1, MODE_DENY: 2}


def _coerce_mode(value: Any) -> str:
    """把任意值收敛成合法三态；非法/缺失一律落 allow（默认全开）。"""
    if isinstance(value, str) and value in VALID_MODES:
        return value
    return MODE_ALLOW


def narrower(left: str, right: str) -> str:
    """取两个三态里**更严**的那一档（保守序 `deny > ask > allow`）。

    这是「只收紧不放宽」的算子，与 hasn-node `session_scope.rs::narrower` 逐条同款：
    派发级 scope 收紧（doc20-tools D-2）的最终生效值 = `narrower(Agent 三态, 派发覆盖)`。
    非法/缺失值经 `_coerce_mode` 落 allow——取 max 故它永远放宽不了对面那一档。
    这里只暴露既有的限制度排序，**不新建第二套判定规则**。
    """
    return max(_coerce_mode(left), _coerce_mode(right), key=lambda mode: _RESTRICTIVENESS[mode])


def resolve_capability_mode(default_mode: str, capability_modes: dict | None, key: str) -> str:
    """返回单个能力 key 的三态：'allow' | 'ask' | 'deny'。

    override 优先（capability_modes[key]）；缺省落 default_mode（默认 allow）。
    社交工具的对象可达性不走这里（维度②）。
    """
    default = _coerce_mode(default_mode)
    if capability_modes:
        override = capability_modes.get(key)
        if override is not None:
            return _coerce_mode(override)
    return default


def resolve_tool_mode(
    default_mode: str,
    capability_modes: dict | None,
    *,
    tool_name: str,
    required_scopes: list[str],
) -> str:
    """聚合一个工具的有效三态：取「工具名 override」与「各 required_scope」中最严的一档。

    - capability_modes 的 key 既可以是 scope（如 ``message:send``），也可以是工具
      canonical 名（如 ``hasn.ext.foo.delete``，见 13-doc §5.2）。
    - 无 required_scopes 的工具（理论上恒可用）：仅看工具名 override，否则 default。
    """
    keys: list[str] = [tool_name, *(required_scopes or [])]
    modes = [resolve_capability_mode(default_mode, capability_modes, key) for key in keys]
    if not modes:
        return _coerce_mode(default_mode)
    return max(modes, key=lambda m: _RESTRICTIVENESS[m])
