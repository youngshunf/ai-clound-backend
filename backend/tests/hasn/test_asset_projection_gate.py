"""资产投影门（asset_projection·doc32 §14）平台层通用守卫——与任何具体应用（deck）无关。

守卫门本体的**分发 / 短路 / 交集**逻辑，证明它经 `ResourceKindRegistry` 泛化、不含 deck 硬编码：
- `resource_ref` 空 / 语法非 `{type}:{id}` / 类型未注册 → 空集（无从判权，§14.4）；
- adapter 未实现 `collect_asset_ids`（纯文本类无内嵌私有资产的资源）→ 空集（门对它天然 no-op，§14.3）；
- **交集不变量**：产出恒 = 请求集 ∩ 该资源引用全集——借一个**合法** ref 也签不出它没引用的任意 asset；
- 有效档 < viewer（资源不存在 → none）→ 空集（存在性隐藏，§14.7）。

零 mock：注册的是 `ResourceKindAdapter` Protocol 的**真实极简实现**，判权走真实
`resource_gate.effective_permission`。owner 主体在判权内核 owner_grant 处直接返回 manager、
`load_meta` 返回 None 的分支都在触库前返回，故本守卫**不需 PG**（`db` 恒不被解引用，传 None）。
真实 PG 的「资源存在但无权→none→空」「deck 三场景 + 越权 asset 丢弃」由
`test_deck_share_asset_resolve.py` 端到端覆盖，本文件只锁平台门的机械分发逻辑。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.service.authz import asset_projection
from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn.service.authz.subject import Subject

pytestmark = pytest.mark.asyncio

# db 恒不被解引用（见模块 docstring）：所有分支都在触库前返回，用哨兵表意「此处不该碰库」。
_NO_DB = None


class _FakeAssetAdapter:
    """带 `collect_asset_ids` 的可配置资源适配器：owner / 引用资产全集 / 是否存在均运行时可调。"""

    resource_type = 'ap_test_proj'
    id_param_aliases = ('proj_id',)

    def __init__(self) -> None:
        self.owner_hasn_id = 'h_owner'
        self.assets: set[str] = set()
        self.meta_exists = True

    async def load_meta(self, db, resource_id: str) -> ResourceMeta | None:
        if not self.meta_exists:
            return None
        return ResourceMeta(
            resource_id=resource_id,
            owner_hasn_id=self.owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=None,
        )

    async def collect_asset_ids(self, db, resource_id: str) -> set[str]:
        return set(self.assets)


class _NoCollectAdapter:
    """不实现 `collect_asset_ids` 的适配器（纯文本类资源）：门在钩子探测处即短路，load_meta 不该被触及。"""

    resource_type = 'ap_test_text'
    id_param_aliases = ('text_id',)

    async def load_meta(self, db, resource_id: str) -> ResourceMeta | None:
        raise AssertionError('无 collect_asset_ids 的类型，门应在探测钩子处短路，绝不调 load_meta')


@pytest.fixture
def adapters():
    """注册两个测试适配器进全局 registry，测试后精确摘除（不污染进程级单例）。"""
    proj = _FakeAssetAdapter()
    text = _NoCollectAdapter()
    for a in (proj, text):
        resource_kind_registry.register(a)
    try:
        yield proj
    finally:
        for rtype in ('ap_test_proj', 'ap_test_text'):
            resource_kind_registry._adapters.pop(rtype, None)


_SUBJECT = Subject.human('h_owner')
_REQUESTED = {'as_1', 'as_2', 'as_stranger'}


@pytest.mark.parametrize(
    'ref',
    [
        None,  # 空
        '',  # 空串
        'ap_test_proj',  # 无冒号
        ':1',  # 空 type
        'ap_test_proj:',  # 空 id
    ],
)
async def test_empty_or_malformed_ref_returns_empty(adapters, ref) -> None:
    """resource_ref 空 / 语法非 {type}:{id} → 空集（触库前返回，无从判权）。"""
    assert await asset_projection.readable_asset_ids(_NO_DB, _SUBJECT, ref, _REQUESTED) == set()


async def test_unregistered_type_returns_empty(adapters) -> None:
    """类型未注册 adapter → 空集（不抛 500，投影门无从判权即隐身）。"""
    assert await asset_projection.readable_asset_ids(_NO_DB, _SUBJECT, 'nope_xyz:1', _REQUESTED) == set()


async def test_adapter_without_collect_is_noop(adapters) -> None:
    """已注册但未实现 collect_asset_ids 的类型 → 空集（门对纯文本类 no-op，§14.3）；load_meta 不被触及。"""
    assert await asset_projection.readable_asset_ids(_NO_DB, _SUBJECT, 'ap_test_text:1', _REQUESTED) == set()


async def test_intersection_invariant_for_owner(adapters) -> None:
    """交集不变量：owner（eff=manager≥viewer）也只得「请求 ∩ 资源引用全集」，越权 stranger 丢弃、未请求资产不签。"""
    proj = adapters
    proj.owner_hasn_id = _SUBJECT.hasn_id  # owner_grant → manager（内核触库前短路）
    proj.assets = {'as_1', 'as_2', 'as_only_referenced'}  # 引用全集含一个未被请求的资产
    got = await asset_projection.readable_asset_ids(_NO_DB, _SUBJECT, 'ap_test_proj:1', _REQUESTED)
    # 请求集 {as_1, as_2, as_stranger} ∩ 引用集 {as_1, as_2, as_only_referenced} = {as_1, as_2}
    assert got == {'as_1', 'as_2'}


async def test_missing_resource_returns_empty(adapters) -> None:
    """资源不存在（load_meta→None）→ effective_permission 得 none < viewer → 空集（存在性隐藏）。"""
    proj = adapters
    proj.meta_exists = False
    proj.assets = {'as_1', 'as_2'}
    assert await asset_projection.readable_asset_ids(_NO_DB, _SUBJECT, 'ap_test_proj:1', _REQUESTED) == set()
