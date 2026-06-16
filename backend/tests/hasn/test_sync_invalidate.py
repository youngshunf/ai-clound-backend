"""WSPUSH-M1：配置/目录变更 WS 主动推送通道基础（设计 doc02-07）。

覆盖：
  - compute_builtin_catalog_revision：空稳定、内容指纹、顺序无关、per-row revision 变化即变。
  - ws_router.broadcast_sync_invalidate：全局 fan-out 全部在线连接 + owner 定向只推该 owner。
  - get_all_revisions：命中缓存直接返回、miss 重算并回填缓存。

纯 fake（redis/ws/db），不依赖真实 PG/Redis——与 test_ws_router_and_route_guard.py 同构。
"""

from __future__ import annotations

import json

from typing import Any

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, Any] = {}
        self.sets: dict[str, set[Any]] = {}

    async def get(self, key: str) -> Any:
        return self.strings.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.strings[key] = value

    async def smembers(self, key: str) -> set[Any]:
        return set(self.sets.get(key, set()))


class FakeWS:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def send_text(self, value: str) -> None:
        if self.fail:
            raise RuntimeError('ws closed')
        self.sent.append(value)


class RowsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeDb:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)

    async def execute(self, stmt: Any) -> Any:
        assert self.results, 'unexpected extra execute'
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_compute_builtin_catalog_revision_stable_and_changes() -> None:
    from backend.app.hasn.service import sync_invalidate_service as svc

    # 空目录 → 稳定指纹
    empty = await svc.compute_builtin_catalog_revision(FakeDb([RowsResult([])]))
    assert empty == svc.EMPTY_BUILTIN_CATALOG_REVISION

    # 内容相同（顺序无关）→ 同一 revision
    r1 = await svc.compute_builtin_catalog_revision(FakeDb([RowsResult([('a', 1), ('b', 2)])]))
    r2 = await svc.compute_builtin_catalog_revision(FakeDb([RowsResult([('b', 2), ('a', 1)])]))
    assert r1 == r2
    assert r1 != svc.EMPTY_BUILTIN_CATALOG_REVISION

    # 任一行 per-row revision 变化 → 指纹变
    r3 = await svc.compute_builtin_catalog_revision(FakeDb([RowsResult([('a', 1), ('b', 3)])]))
    assert r3 != r1


@pytest.mark.asyncio
async def test_broadcast_sync_invalidate_global_and_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ws_router as module

    redis = FakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    module._ws_connections.clear()

    ws_a = FakeWS()
    ws_b = FakeWS()
    module._ws_connections['node-a'] = ws_a
    module._ws_connections['node-b'] = ws_b
    router = module.WsRouterService()

    # 全局广播 → 两个在线节点都收到
    pushed = await router.broadcast_sync_invalidate('builtin_catalog', 'rev1')
    assert pushed == 2
    frame = json.loads(ws_a.sent[-1])
    assert frame['method'] == 'hasn.sync.invalidate'
    assert frame['params'] == {'kind': 'builtin_catalog', 'revision': 'rev1'}

    # owner 定向 → 仅该 owner 的在线节点（node-a），node-b 不再收到新帧
    redis.sets[f'{module.USER_NODES_PREFIX}:h_o'] = {'node-a'}
    pushed_owner = await router.broadcast_sync_invalidate('agents', 'rev2', owner_id='h_o')
    assert pushed_owner == 1
    assert json.loads(ws_a.sent[-1])['params']['revision'] == 'rev2'
    assert json.loads(ws_b.sent[-1])['params']['revision'] == 'rev1'  # 未被重复推送

    # 单连接发送失败不影响计数其余（这里只有 node-a，失败即 0）
    module._ws_connections.clear()
    module._ws_connections['node-fail'] = FakeWS(fail=True)
    assert await router.broadcast_sync_invalidate('common_skills', 'rev3') == 0

    module._ws_connections.clear()


@pytest.mark.asyncio
async def test_get_all_revisions_cache_then_compute(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import sync_invalidate_service as svc

    redis = FakeRedis()
    monkeypatch.setattr(svc, 'redis_client', redis)

    # 预置两类入缓存；builtin_catalog 缓存 miss → 从 db 重算并回填
    redis.strings[f'{svc.REV_PREFIX}:{svc.KIND_COMMON_SKILLS}'] = 'cs_cached'
    redis.strings[f'{svc.REV_PREFIX}:{svc.KIND_PLATFORM_CONFIG}'] = 'pc_cached'

    revs = await svc.get_all_revisions(FakeDb([RowsResult([('a', 1)])]))
    assert revs[svc.KIND_COMMON_SKILLS] == 'cs_cached'
    assert revs[svc.KIND_PLATFORM_CONFIG] == 'pc_cached'
    assert revs[svc.KIND_BUILTIN_CATALOG] != svc.EMPTY_BUILTIN_CATALOG_REVISION
    # 回填缓存
    assert redis.strings[f'{svc.REV_PREFIX}:{svc.KIND_BUILTIN_CATALOG}'] == revs[svc.KIND_BUILTIN_CATALOG]
