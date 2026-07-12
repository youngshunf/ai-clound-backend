"""G6 S3-2 声明完整度守卫（doc33 §S3-2）。

不变量：**任何工具入参命中「已注册资源适配器的 id 别名」，就必须声明 `resource_access`**，
否则登记进白名单——真例外 `_TRUE_EXCEPTIONS`（刻意按父/兄弟参判、永久豁免）或已知欠债
`_KNOWN_DEBT`（暂缓补、目标清零）。守卫零 mock，只对真实注册表求值。

覆盖两类工具面：
- 平台 BaseTool（`mcp/tools/*`，如 deck）：读 `tool.input_schema` + `getattr(tool, 'resource_access')`；
- builtin manifest 工具（`hasn_<app>/manifest.py`，如 knowledge）：读 capability 的 input_schema + `resource_access`。

只对**已注册适配器**的 id 别名求值——未接入的应用其资源 id 尚不在别名集，天然不触发（随 S3-1
逐应用注册 adapter，别名集自动扩大，守卫覆盖面随之铺开）。这样守卫既锁死「新工具漏声明即红」
（验收矩阵场景⑦），又允许 S3-1 增量接入：接入一应用即从 `_KNOWN_DEBT` 删除其条目。
"""

from __future__ import annotations

from typing import Any

# import 即注册：保证 registered_types() 在守卫求值时已含 knowledge/deck 三类资源（S2-6 已接入的）。
import backend.app.hasn_deck.service.resource_adapter  # noqa: F401
import backend.app.hasn_knowledge.service.resource_adapter  # noqa: F401

from backend.app.hasn.service.ai_native_app_registry import (
    AINativeAppRegistry,
    _capability_props_by_tool_id,
)
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.mcp.server import HasnCloudMcpServer

# ── 两段白名单（对齐本仓 `KNOWN_NON_ENVELOPE` 的「真例外 / 已知欠债」双段范式）────────────
#
# 真例外（`_TRUE_EXCEPTIONS`）：`(工具名, 入参名)` 对——该入参虽命中某注册适配器别名，但工具**刻意**
# 不按它判权，而是按同资源族的另一入参（父链/兄弟）判，且更严更正确。**永久豁免**，每条附理由。
_TRUE_EXCEPTION_REASONS: dict[tuple[str, str], str] = {
    # deck 页级操作按 `page_id`(=deck_page)判——门据 page_id 反查**真实所属 deck** 再判，正是为防
    # 「传我有权的 deck_id + 别人的 page_id」混淆代理；`deck_id` 是兄弟入参，按它判反而重开该攻击面。
    ('hasn.deck.page.edit', 'deck_id'): 'deck 页级按 page_id 判所属 deck（防混淆代理），deck_id 兄弟参不判',
    ('hasn.deck.page.delete', 'deck_id'): 'deck 页级按 page_id 判所属 deck（防混淆代理），deck_id 兄弟参不判',
    # knowledge：目录无独立 share（authorize_folder 即委托 authorize_kb，纯继承库档位），且 service
    # 层 `_validate_folder_for_kb` 强校验 folder.kb_id==kb_id（跨库注入已堵）。这些工具已按 kb_id/doc_id
    # 判库级/文档级 editor；folder_id 仅为库内子目录定位符（多为可选），门不重复判、按父库档位即可。
    ('knowledge.list_documents', 'folder_id'): '已按 kb_id 判 viewer；folder 继承库档位、仅库内过滤',
    ('knowledge.upload_document', 'folder_id'): '已按 kb_id 判 editor；folder 继承库档位、service 校验∈kb',
    ('knowledge.write_doc', 'folder_id'): '已按 kb_id/doc_id 判 editor；folder 继承库档位、service 校验∈kb',
    ('knowledge.move_document', 'folder_id'): '已按 doc_id 判 editor；目标 folder service 校验∈同库',
}
_TRUE_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(_TRUE_EXCEPTION_REASONS)

# 已知欠债（`_KNOWN_DEBT`）：`(工具名, 入参名)` 对——该入参已被某注册适配器识别，但工具**尚未**声明
# resource_access（真漏门，只是暂缓补）。目标清零；每随 S3-1 接入一应用即从这里删对应条目。
# 空集 = 已接入应用（knowledge/deck）G6 声明齐、无欠债。
_KNOWN_DEBT: frozenset[tuple[str, str]] = frozenset()

# 白名单并集：真例外 ∪ 已知欠债，供守卫从违规集中扣除。
_WHITELIST: frozenset[tuple[str, str]] = _TRUE_EXCEPTIONS | _KNOWN_DEBT


def _alias_params() -> set[str]:
    """全部**已注册**适配器的 id 别名并集（如 {kb_id, doc_id, folder_id, deck_id, page_id}）。"""
    params: set[str] = set()
    for rtype in resource_kind_registry.registered_types():
        params.update(resource_kind_registry.get(rtype).id_param_aliases)
    return params


def _declared_params(resource_access: Any) -> set[str]:
    """从工具的 resource_access 声明里取已覆盖的入参名集合（形状非法则视作空）。"""
    if not isinstance(resource_access, list):
        return set()
    return {d['param'] for d in resource_access if isinstance(d, dict) and 'param' in d}


def _platform_violations(alias: set[str]) -> set[tuple[str, str]]:
    """平台 BaseTool 面：input_schema 里命中别名却未声明的 `(工具名, 入参)`。"""
    server = HasnCloudMcpServer()
    out: set[tuple[str, str]] = set()
    for tool in server.tool_registry.get_all_tools():
        if getattr(tool, 'source', 'platform') == 'external':
            continue
        props = (getattr(tool, 'input_schema', None) or {}).get('properties', {})
        declared = _declared_params(getattr(tool, 'resource_access', None))
        for param in props:
            if param in alias and param not in declared:
                out.add((tool.name, param))
    return out


def _manifest_violations(alias: set[str]) -> set[tuple[str, str]]:
    """builtin manifest 面：capability.input_schema 里命中别名却未声明的 `(工具id, 入参)`。"""
    registry = AINativeAppRegistry()
    out: set[tuple[str, str]] = set()
    for app in registry.list_builtin_apps():
        app_id = app.get('app_id') or app.get('id')
        if not app_id:
            continue
        manifest = registry.get_builtin_manifest(app_id)
        cap_props = _capability_props_by_tool_id(manifest)
        for tool in manifest.get('tools', []) or []:
            tool_id = tool.get('tool_id') or tool.get('id') or tool.get('name', '')
            props = cap_props.get(tool_id) or (tool.get('input_schema') or {}).get('properties', {}) or {}
            declared = _declared_params(tool.get('resource_access'))
            for param in props:
                if param in alias and param not in declared:
                    out.add((tool_id, param))
    return out


def _all_violations() -> set[tuple[str, str]]:
    alias = _alias_params()
    return _platform_violations(alias) | _manifest_violations(alias)


def test_registered_adapters_have_aliases() -> None:
    """前置：至少注册了 knowledge/deck 适配器，别名集非空（否则守卫空转、假绿）。"""
    alias = _alias_params()
    assert 'kb_id' in alias
    assert 'deck_id' in alias


def test_no_undeclared_adapter_matched_tool_params() -> None:
    """任何命中已注册适配器别名的工具入参，必已声明 resource_access，或登记进真例外/已知欠债白名单。"""
    undeclared = _all_violations() - _WHITELIST
    assert not undeclared, (
        '这些工具收了适配器可识别的资源 id 入参却未声明 resource_access（G6 漏门）——'
        f'补声明，或（真不判）登记 _TRUE_EXCEPTIONS、（暂缓补）登记 _KNOWN_DEBT：{sorted(undeclared)}'
    )


def test_whitelist_has_no_stale_entries() -> None:
    """白名单（真例外+已知欠债）不许悬空：已声明/已消失的条目必须删除，防白名单腐化。"""
    stale = _WHITELIST - _all_violations()
    assert not stale, (
        f'这些白名单条目已不再是违规（已声明或工具已删），请从 _TRUE_EXCEPTIONS / _KNOWN_DEBT 删除：{sorted(stale)}'
    )
