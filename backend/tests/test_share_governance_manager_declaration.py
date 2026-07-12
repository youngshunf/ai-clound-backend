"""G6 §15 / S7 共享治理门守卫（doc32 §15·doc33 §S5）。

四个共享治理动作（list_shares / add_share / revoke_share / set_visibility）必须统一经
`need=manager` 的资源门 `DependsResourceAccess('<res>', 'manager', '<id_param>')` 收进门——
杜绝「人类面接了、分身面漏了 / A 应用有门、B 应用忘了」这类跨面 / 跨应用漂移（正是本缺陷
[[feedback_hasn_uri_must_use_cloud_authoritative_id]] 同源的「一处对、另一处忘」形态）。

内省装配后的全部路由，凡路径以 `/shares` 或 `/visibility` 结尾者即共享治理动作。守卫两条
（只减不增 ratchet）：

- `test_share_governance_routes_declare_manager_gate`：除 `_KNOWN_DEBT`（尚未接入 G6 的存量
  应用）外，每条共享治理路由必须挂 `need=manager` 的 G6 门。
- `test_no_stale_share_governance_debt`：`_KNOWN_DEBT` 里若有路由其实已挂门 → 说明该迁的已迁，
  必须把它从欠债名单删掉（防欠债名单虚高、掩盖新漂移）。

deck 是 S7 试点，已收进门（不在欠债名单，`test_deck_share_routes_are_enforced` 额外锁定）。
knowledge / hasn_studio / designsystem / publish 尚未铺开（G6-S3），暂列欠债；随其逐条接入
G6 从名单移除。守卫按**路由 name**（register_app 全局唯一）核对，rename / re-path 会让路由以
「新的未知共享路由」重新出现 → 触发第一条守卫，逼迫补门或显式登记欠债。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.plugin.core import build_final_router

# 尚未接入 G6 统一资源门的存量共享治理路由（按路由 name）。**只减不增**：
# 某应用接入 G6 并挂上 need=manager 门后，把它对应的 name 从这里删掉
# （否则 test_no_stale_share_governance_debt 会红）。deck 不在此列——它是 S7 试点、已收进门。
_KNOWN_DEBT: frozenset[str] = frozenset({
    # designsystem（design-systems/{id}/shares）
    'app_list_shares',
    'app_share',
    # hasn_studio（projects/{id}/shares + artifacts/{id}/shares）
    'list_project_shares',
    'add_project_share',
    'revoke_project_share',
    'list_artifact_shares',
    'add_artifact_share',
    'revoke_artifact_share',
    # knowledge（kbs/{id}/shares + kbs/{id}/visibility + documents/{id}/shares）
    'knowledge_app_list_shares',
    'knowledge_app_add_share',
    'knowledge_app_revoke_share',
    'knowledge_app_set_visibility',
    'knowledge_app_list_doc_shares',
    'knowledge_app_add_doc_share',
    'knowledge_app_revoke_doc_share',
    # publish（sites/{id}/visibility，app + agent 两面）
    'publish_app_set_visibility',
    'publish_agent_set_visibility',
})


def _g6_gate_meta(route: APIRoute) -> dict | None:
    """取路由级依赖里挂的 G6 资源门元数据（`DependsResourceAccess` 在闭包上打的
    `_g6_resource_access`）；无门返回 None。owner / agent 两支线均已打此标记。
    """
    for dep in route.dependant.dependencies or []:
        meta = getattr(getattr(dep, 'call', None), '_g6_resource_access', None)
        if meta is not None:
            return meta
    return None


def _share_governance_routes() -> list[tuple[str, str, str, dict | None]]:
    """内省全部路由，返回共享治理动作 `(name, 'METHOD,...', path, gate_meta)` 列表。"""
    router = build_final_router()
    rows: list[tuple[str, str, str, dict | None]] = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.path.endswith('/shares') or route.path.endswith('/visibility')):
            continue
        methods = ','.join(sorted(m for m in (route.methods or set()) if m not in ('HEAD', 'OPTIONS')))
        rows.append((route.name, methods, route.path, _g6_gate_meta(route)))
    return rows


def test_share_governance_routes_declare_manager_gate() -> None:
    """非欠债的共享治理路由必须挂 need=manager 的 G6 门（S7 收进门·防漂移）。"""
    offenders: list[str] = []
    for name, methods, path, gate in _share_governance_routes():
        if name in _KNOWN_DEBT:
            continue
        if gate is None or gate.get('need') != 'manager':
            got = 'None' if gate is None else repr(gate.get('need'))
            offenders.append(f'{methods} {path} (name={name}) → need={got}，应为 manager')
    assert not offenders, (
        '以下共享治理路由未收进 need=manager 的统一资源门（S7 §15）：\n'
        + '\n'.join(offenders)
        + "\n修法：给路由加 dependencies=[..., DependsResourceAccess('<res>', 'manager', '<id_param>')]；"
        '若确属尚未接入 G6 的存量应用，把 name 临时加入 _KNOWN_DEBT（只减不增）。'
    )


def test_no_stale_share_governance_debt() -> None:
    """_KNOWN_DEBT 里已挂 need=manager 门的路由必须移除（只减不增，防欠债名单虚高）。"""
    by_name = {name: gate for name, _m, _p, gate in _share_governance_routes()}
    healed: list[str] = []
    for name in sorted(_KNOWN_DEBT):
        gate = by_name.get(name)
        if gate is not None and gate.get('need') == 'manager':
            healed.append(name)
    assert not healed, '以下路由已挂 need=manager 的 G6 门，应从 _KNOWN_DEBT 移除（该迁的已迁）：\n' + '\n'.join(healed)


def test_deck_share_routes_are_enforced() -> None:
    """deck 是 S7 试点：四个共享治理动作必须已收进门、且不在欠债名单。"""
    deck = [(name, gate) for name, _m, path, gate in _share_governance_routes() if '/deck/app/' in path]
    assert deck, '未发现 deck 共享治理路由（deck.py 是否注册？）'
    for name, gate in deck:
        assert name not in _KNOWN_DEBT, f'deck 路由 {name} 不应在欠债名单（deck 已收进门）'
        assert gate is not None and gate.get('need') == 'manager', f'deck 路由 {name} 缺 need=manager 的 G6 门'
