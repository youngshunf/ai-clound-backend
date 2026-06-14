"""P5 — local_tool App 云端 manifest required_scopes ⟷ 本地 hasn-mcp capability_scopes() 跨仓对齐。

零漂移（16-doc D-v3-3）：local_tool App（deck/publish/hasn_task）的工具落地在 hasn-node
`crates/hasn-mcp`，其 owner 三态 override 的能力键由本地 `capability_scopes()` 声明，
云端 manifest 的 `required_scopes` 必须与之一致——否则 owner 在 webui 设的 allow/ask/deny
会落到一个本地不认识（或缺失）的键，授权静默失效。

跨仓契约常量 `EXPECTED_MANAGEMENT_SCOPES` 是两端的共同事实源：
- **本地侧**已被 hasn-mcp 单测钉死（`crates/hasn-mcp/src/{deck,publish,task,workflow}.rs`
  的 `write_tools_are_allow_*` / `write_tools_declare_scope_allow_*` 断言
  `capability_scopes() == [对应键]`）。
- **云端侧**由本测试钉死：manifest 里非 `:read` 的 required_scopes 集合必须等于契约。

任一仓改了某 App 的管理能力键而没同步另一仓 → 对应仓测试红。

> 注意：`:read` 类是云端 manifest 的声明语义（审计/可见性），本地读类工具
> `capability_scopes()` 一律为空（owner 不单独 gate 只读），故对齐只比**管理类**键。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_deck.manifest import DECK_AI_NATIVE_MANIFEST
from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST
from backend.app.hasn_publish.manifest import PUBLISH_AI_NATIVE_MANIFEST

# 跨仓契约：local_tool App → 本地 hasn-mcp capability_scopes() 声明的管理能力键集合。
# hasn_task 同时含 task.rs（task:manage/task:run）与 workflow.rs（workflow:manage/workflow:run）。
EXPECTED_MANAGEMENT_SCOPES: dict[str, set[str]] = {
    'deck': {'deck:manage'},
    'publish': {'publish:write'},
    'hasn_task': {'task:manage', 'task:run', 'workflow:manage', 'workflow:run'},
}

_MANIFESTS = {
    'deck': DECK_AI_NATIVE_MANIFEST,
    'publish': PUBLISH_AI_NATIVE_MANIFEST,
    'hasn_task': HASN_TASK_AI_NATIVE_MANIFEST,
}


def _management_scopes(manifest: dict) -> set[str]:
    """manifest 里所有非 `:read` 的 required_scopes（= 本地 capability_scopes() 应声明的管理键）。"""
    out: set[str] = set()
    for cap in manifest.get('capabilities', []):
        for scope in cap.get('required_scopes') or []:
            if not scope.endswith(':read'):
                out.add(scope)
    return out


@pytest.mark.parametrize('app_id', sorted(EXPECTED_MANAGEMENT_SCOPES))
def test_manifest_management_scopes_match_local_capability_scopes(app_id: str) -> None:
    """云端 manifest 管理类 required_scopes 集合 == 本地 hasn-mcp capability_scopes() 契约。"""
    assert _management_scopes(_MANIFESTS[app_id]) == EXPECTED_MANAGEMENT_SCOPES[app_id]


def test_management_scopes_registered_in_catalog() -> None:
    """每个管理键都在 scope catalog 登记（catalog 中文化/分组不漏键）。"""
    from backend.app.mcp.scopes import SCOPE_CATALOG

    for keys in EXPECTED_MANAGEMENT_SCOPES.values():
        for key in keys:
            assert key in SCOPE_CATALOG, f'{key} 未在 SCOPE_CATALOG 登记'
