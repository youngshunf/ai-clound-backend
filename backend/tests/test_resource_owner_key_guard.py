"""G6 §S5-2 守卫：门后委托方法的 owner-key 不得被「调用者身份」直接喂入。

统一资源权限门（G6，doc32/doc33）的核心接缝：门判权后，必须把**门判出的资源真实 owner**
喂给 owner-keyed 的委托方法；**绝不能**把调用者身份（分身自己的 `agent.owner_hasn_id` /
`_resolve_owner(...)` 结果）直接当 owner-key——那正是 S1 缺陷（分享场景下按调用者主人 key
隔离，会把「库主人 A 的资源」按「调用分身的主人 B」去查，越权或查不到）。

S5-2 第一步已把这类「门后委托方法」的 owner 形参统一改名 `resource_owner_id`（见
`knowledge_service.py`），给守卫一个一致的**词法信号**。本守卫 AST 静态扫描 `service/` 与
`tool_handlers` 层的调用点：凡把 `agent.owner_hasn_id` / `subject.owner_hasn_id` /
`_resolve_owner(...)` 结果直接绑定到某方法的 `resource_owner_id` 形参 → 反模式，命中且不在
`_KNOWN_DEBT` 白名单即红。

**这是防退化 ratchet（非补历史欠债）**：S1/S2 已修好现网真 bug，当前基线本就干净
（handler 一律经 `_resource_owner()` 局部变量 `owner` 委托，从不直传 `agent.owner_hasn_id`）。
守卫的价值在于：日后谁再写 `get_document(db, agent.owner_hasn_id, doc_id)` 这类绕门的调用即变红。

**不扫 caller-owner 语义方法**：`create_kb` / `list_kbs` / `search` / `get_agent_grant` /
`resolve_agent_visible_kbs` / 审计等保留 `owner_id` 形参——它们按调用者主人 key 直查本就正确，
不进 registry（registry 只收有 `resource_owner_id` 形参的方法），故 `create_kb(db,
agent.owner_hasn_id, ...)` 等合法调用**不被** flag。

两条守卫（对齐 S3-2/S7 的「只减不增」ratchet 形态）：
- `test_no_caller_identity_bound_to_resource_owner_key`：除 `_KNOWN_DEBT` 外，无任何调用点把
  调用者身份直绑 `resource_owner_id` 形参。
- `test_no_stale_resource_owner_key_debt`：`_KNOWN_DEBT` 里若有项已不再命中（对应调用已修）→
  必须删除，防白名单虚高掩盖新漂移。
另有 `test_guard_is_not_vacuous`：断言 registry 非空且含已知改名方法，防「rename 被回退→守卫
静默全绿」的假保证。
"""

from __future__ import annotations

import ast

from pathlib import Path
from typing import NamedTuple

# 仓库根：本文件在 backend/tests/ 下，parents[2] = 仓库根，parents[1] = backend。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / 'backend' / 'app'

# 被视为「调用者身份 owner」的表达式：基对象名（agent/subject）取 .owner_hasn_id。
_CALLER_IDENTITY_BASES = frozenset({'agent', 'subject'})
# 被视为「调用者身份 owner」的解析调用名。
_RESOLVE_OWNER_CALLEES = frozenset({'_resolve_owner'})

# 尚未收敛的真实欠债（relpath, method, owner_expr）。**只减不增**：修好一处调用即从这里删。
# 当前为空——S1/S2 已把现网 owner-key 误用修净，本守卫是纯防退化 ratchet。
_KNOWN_DEBT: frozenset[tuple[str, str, str]] = frozenset()


class Finding(NamedTuple):
    relpath: str
    lineno: int
    method: str
    owner_expr: str

    def key(self) -> tuple[str, str, str]:
        return (self.relpath, self.method, self.owner_expr)


def _iter_service_files() -> list[Path]:
    """service/ 与 tool_handlers 层的全部 .py（路径含 `service` 目录段即算）。"""
    return sorted(p for p in _APP_DIR.rglob('*.py') if 'service' in p.relative_to(_APP_DIR).parts)


def _resource_owner_param_index(func: ast.AsyncFunctionDef | ast.FunctionDef) -> int | None:
    """`resource_owner_id` 在「调用时位置实参」里的下标（丢弃 self/cls），无则 None。"""
    params = [a.arg for a in (func.args.posonlyargs + func.args.args)]
    if params and params[0] in ('self', 'cls'):
        params = params[1:]
    return params.index('resource_owner_id') if 'resource_owner_id' in params else None


def _build_registry(trees: dict[Path, ast.Module]) -> dict[str, set[int]]:
    """方法名 → 其 `resource_owner_id` 形参在调用点的位置下标集合。"""
    registry: dict[str, set[int]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                idx = _resource_owner_param_index(node)
                if idx is not None:
                    registry.setdefault(node.name, set()).add(idx)
    return registry


def _called_method_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _bound_owner_exprs(call: ast.Call, indices: set[int]) -> list[ast.expr]:
    """取绑定到 `resource_owner_id` 形参的实参表达式（关键字优先，否则按位置下标）。"""
    exprs: list[ast.expr] = [kw.value for kw in call.keywords if kw.arg == 'resource_owner_id']
    if not exprs:
        exprs.extend(
            call.args[idx] for idx in indices if idx < len(call.args) and not isinstance(call.args[idx], ast.Starred)
        )
    return exprs


def _is_caller_identity_owner(expr: ast.expr) -> bool:
    # agent.owner_hasn_id / subject.owner_hasn_id
    if (
        isinstance(expr, ast.Attribute)
        and expr.attr == 'owner_hasn_id'
        and isinstance(expr.value, ast.Name)
        and expr.value.id in _CALLER_IDENTITY_BASES
    ):
        return True
    # _resolve_owner(...) 调用结果
    if isinstance(expr, ast.Call):
        callee = _called_method_name(expr.func)
        if callee in _RESOLVE_OWNER_CALLEES:
            return True
    return False


def _collect_findings() -> tuple[list[Finding], dict[str, set[int]]]:
    files = _iter_service_files()
    trees = {p: ast.parse(p.read_text(encoding='utf-8')) for p in files}
    registry = _build_registry(trees)

    findings: list[Finding] = []
    for path, tree in trees.items():
        relpath = str(path.relative_to(_REPO_ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method = _called_method_name(node.func)
            if method is None or method not in registry:
                continue
            findings.extend(
                Finding(relpath, node.lineno, method, ast.unparse(expr))
                for expr in _bound_owner_exprs(node, registry[method])
                if _is_caller_identity_owner(expr)
            )
    return findings, registry


def test_no_caller_identity_bound_to_resource_owner_key() -> None:
    """无任何调用点把调用者身份直绑门后委托方法的 `resource_owner_id` 形参（白名单除外）。"""
    findings, _ = _collect_findings()
    offending = [f for f in findings if f.key() not in _KNOWN_DEBT]
    assert not offending, (
        '发现「调用者身份」被直接喂给门后委托方法的 resource_owner_id 形参（绕过 G6 门、'
        '按调用者主人 key 隔离资源，即 S1 缺陷形态）。应改喂门判出的资源真实 owner '
        '（_resource_owner() 的 owner / authorized.owner_hasn_id / kb.owner_id）：\n'
        + '\n'.join(f'  {f.relpath}:{f.lineno}  {f.method}(..., {f.owner_expr}, ...)' for f in offending)
    )


def test_no_stale_resource_owner_key_debt() -> None:
    """`_KNOWN_DEBT` 里的项必须仍真实命中——已修好的要删掉，防白名单虚高掩盖新漂移。"""
    findings, _ = _collect_findings()
    live_keys = {f.key() for f in findings}
    stale = [entry for entry in _KNOWN_DEBT if entry not in live_keys]
    assert not stale, f'_KNOWN_DEBT 存在陈旧项（对应调用已不再命中，应从白名单删除）：{stale}'


def test_guard_is_not_vacuous() -> None:
    """防「rename 被回退→registry 空→守卫静默全绿」的假保证。"""
    _, registry = _collect_findings()
    assert registry, 'registry 为空：未发现任何带 resource_owner_id 形参的方法（S5-2 改名是否被回退？）'
    # 锚定几个已改名的 knowledge 门后委托方法，确保词法信号真实存在。
    for anchor in ('get_document', 'delete_kb', '_get_kb', 'list_documents'):
        assert anchor in registry, f'预期 {anchor} 应有 resource_owner_id 形参，registry 却缺失（改名回退？）'
