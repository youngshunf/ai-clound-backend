"""NodeSessionGateway / NodeBindingView / PresenceQuery 契约（P1-01）。

在内存适配器上覆盖三类关键红线：

1. 代际冲突：旧 `connection_id` 不可写就绪/续期；
2. TTL 失效：过期后节点在线判定与续期都应失败；
3. Agent 就绪与跨 worker 断开：未就绪不可在线，跨 worker `disconnect_node`
   仍需清理共享 presence。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from backend.app.hasn_im.ports import (
    AgentSessionResult,
    NodeBindingView,
    NodeRegistration,
    NodeSessionGateway,
    NodeSessionResult,
    OwnerBindingRef,
    OwnerBindingResult,
    OnlinePresence,
    PresenceQuery,
)
from backend.app.hasn_im.ports.presence_query import OnlinePresence as PresenceRecord
from backend.app.hasn_im.ports.node_session_gateway import (
    AgentSessionResult as GatewayAgentSessionResult,
    NodeRegistration as GatewayNodeRegistration,
    NodeSessionGateway as GatewayNodeSessionGateway,
    NodeSessionResult as GatewayNodeSessionResult,
    OwnerBindingResult as GatewayOwnerBindingResult,
)
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


class _ManualClock:
    def __init__(self, start_ts: float) -> None:
        self._now = start_ts

    def now(self) -> float:
        return self._now

    def tick(self, seconds: float) -> None:
        self._now += seconds


@dataclass
class _NodeState:
    node_id: str
    node_type: str
    capacity: int
    connection_id: str
    alive_until: float
    ready_connection_id: str | None = None


@dataclass
class _SharedRoutingStore:
    ttl_seconds: int = 90
    node_states: dict[str, _NodeState] = field(default_factory=dict)
    owner_nodes: dict[str, set[str]] = field(default_factory=dict)
    entity_nodes: dict[str, str] = field(default_factory=dict)
    node_entities: dict[str, set[str]] = field(default_factory=dict)
    agent_ready: set[str] = field(default_factory=set)

    def create_or_update_node(
        self,
        node_id: str,
        node_type: str,
        capacity: int,
        now: float,
    ) -> _NodeState:
        state = _NodeState(
            node_id=node_id,
            node_type=node_type,
            capacity=capacity,
            connection_id=uuid.uuid4().hex,
            alive_until=now + self.ttl_seconds,
        )
        self.node_states[node_id] = state
        self.node_entities.setdefault(node_id, set())
        self.owner_nodes.setdefault(node_id, set())
        return state

    def is_node_alive(self, node_id: str, now: float) -> bool:
        state = self.node_states.get(node_id)
        return state is not None and state.alive_until > now


class _MemoryNodeBindingView(NodeBindingView):
    """内存版本 NodeBindingView，用于契约测试。"""

    def __init__(self, bindings: dict[tuple[str, str], OwnerBindingRef]):
        self._bindings = bindings

    async def get_active_owner_binding(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingRef | None:
        return self._bindings.get((node_id, owner_id))

    async def list_active_owner_bindings(self, *, node_id: str) -> list[OwnerBindingRef]:
        return [binding for (nid, _), binding in self._bindings.items() if nid == node_id]


class _InMemoryNodeSessionGateway(NodeSessionGateway, PresenceQuery):
    """契约验证用的共享状态 gateway，不依赖 Redis/PG。"""

    def __init__(
        self,
        *,
        store: _SharedRoutingStore,
        binding_view: NodeBindingView,
        clock: _ManualClock,
        local_nodes: set[str] | None = None,
    ) -> None:
        self._store = store
        self._binding_view = binding_view
        self._clock = clock
        self._local_nodes = local_nodes or set()

    async def register_node(
        self,
        *,
        node_id: str,
        node_type: str,
        capacity: int = 1,
    ) -> NodeRegistration:
        state = self._store.create_or_update_node(node_id, node_type, capacity, self._clock.now())
        return GatewayNodeRegistration(
            node_id=node_id,
            connection_id=state.connection_id,
            node_type=node_type,
            capacity=capacity,
        )

    async def mark_node_ready(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        state = self._store.node_states.get(node_id)
        if not state or state.connection_id != connection_id:
            return False
        if not self._store.is_node_alive(node_id, self._clock.now()):
            return False
        state.ready_connection_id = connection_id
        return True

    async def refresh_node_presence(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        state = self._store.node_states.get(node_id)
        if not state or state.connection_id != connection_id:
            return False
        if not self._store.is_node_alive(node_id, self._clock.now()):
            return False
        state.alive_until = self._clock.now() + self._store.ttl_seconds
        return True

    async def unregister_node(
        self,
        *,
        node_id: str,
        connection_id: str,
    ) -> bool:
        state = self._store.node_states.get(node_id)
        if not state or state.connection_id != connection_id:
            return False
        await self._clear_node(node_id)
        return True

    async def add_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingResult:
        binding = await self._binding_view.get_active_owner_binding(node_id=node_id, owner_id=owner_id)
        if binding is None:
            return GatewayOwnerBindingResult(
                accepted=False,
                binding_id=None,
                owner_id=owner_id,
                reason='owner binding not found or inactive',
            )
        self._store.owner_nodes.setdefault(node_id, set()).add(owner_id)
        self._store.owner_nodes[node_id].add(owner_id)
        self._store.entity_nodes[owner_id] = node_id
        self._store.node_entities.setdefault(node_id, set()).add(owner_id)
        return GatewayOwnerBindingResult(accepted=True, binding_id=binding.binding_id, owner_id=owner_id)

    async def renew_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> OwnerBindingResult:
        return await self.add_owner(node_id=node_id, owner_id=owner_id)

    async def remove_owner(
        self,
        *,
        node_id: str,
        owner_id: str,
    ) -> NodeSessionResult:
        await self.unregister_entity_route(node_id=node_id, hasn_id=owner_id)
        self._store.owner_nodes.get(node_id, set()).discard(owner_id)
        return GatewayNodeSessionResult(accepted=True)

    async def list_owners(self, *, node_id: str) -> list[str]:
        return sorted(self._store.owner_nodes.get(node_id, set()))

    async def add_agent_presence(
        self,
        *,
        node_id: str,
        owner_id: str,
        agent_id: str,
    ) -> AgentSessionResult:
        if owner_id not in self._store.owner_nodes.get(node_id, set()):
            return GatewayAgentSessionResult(
                accepted=False,
                reason='owner 未绑定到当前 node',
                agent_id=agent_id,
                entity_node=None,
            )
        self._store.entity_nodes[agent_id] = node_id
        self._store.node_entities.setdefault(node_id, set()).add(agent_id)
        return GatewayAgentSessionResult(accepted=True, agent_id=agent_id, entity_node=node_id)

    async def remove_agent_presence(
        self,
        *,
        node_id: str,
        agent_id: str,
    ) -> NodeSessionResult:
        await self.unregister_entity_route(node_id=node_id, hasn_id=agent_id)
        self._store.agent_ready.discard(agent_id)
        return GatewayNodeSessionResult(accepted=True)

    async def set_agent_readiness(
        self,
        *,
        agent_id: str,
        online_status: str,
        health_status: str | None,
    ) -> str | None:
        if agent_id not in self._store.entity_nodes:
            return None
        now_ready = online_status == 'online' and health_status == 'ok'
        was_ready = agent_id in self._store.agent_ready
        if now_ready:
            self._store.agent_ready.add(agent_id)
            return 'online' if not was_ready else None
        if was_ready:
            self._store.agent_ready.remove(agent_id)
            return 'offline'
        return None

    async def unregister_entity_route(self, *, node_id: str, hasn_id: str) -> None:
        if self._store.entity_nodes.get(hasn_id) != node_id:
            return
        self._store.entity_nodes.pop(hasn_id, None)
        self._store.node_entities.get(node_id, set()).discard(hasn_id)
        self._store.owner_nodes.get(node_id, set()).discard(hasn_id)
        self._store.agent_ready.discard(hasn_id)

    async def disconnect_node(self, *, node_id: str) -> bool:
        has_local = node_id in self._local_nodes
        await self._clear_node(node_id)
        return has_local

    async def is_human_online(self, owner_hasn_id: str) -> bool:
        node_id = self._store.entity_nodes.get(owner_hasn_id)
        return await self.is_node_online(node_id=node_id)

    async def is_agent_online(self, agent_hasn_id: str) -> bool:
        node_id = self._store.entity_nodes.get(agent_hasn_id)
        if not node_id:
            return False
        if agent_hasn_id not in self._store.agent_ready:
            return False
        return await self.is_node_online(node_id=node_id)

    async def is_node_online(self, node_id: str | None) -> bool:
        if not node_id:
            return False
        return self._store.is_node_alive(node_id, self._clock.now())

    async def get_entity_node(self, hasn_id: str) -> str | None:
        return self._store.entity_nodes.get(hasn_id)

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for hasn_id in entity_ids:
            if hasn_id.startswith('h_'):
                result[hasn_id] = await self.is_human_online(hasn_id)
            elif hasn_id.startswith('a_'):
                result[hasn_id] = await self.is_agent_online(hasn_id)
            else:
                result[hasn_id] = False
        return result

    async def get_online_presence(self, entity_ids: list[str]) -> dict[str, OnlinePresence]:
        online_map = await self.get_online_map(entity_ids)
        return {
            hasn_id: PresenceRecord(
                hasn_id=hasn_id,
                is_online=online_map[hasn_id],
                node_id=self._store.entity_nodes.get(hasn_id),
            )
            for hasn_id in entity_ids
        }

    async def _clear_node(self, node_id: str) -> None:
        state = self._store.node_states.pop(node_id, None)
        if not state:
            return
        for hasn_id in list(self._store.node_entities.get(node_id, set())):
            await self.unregister_entity_route(node_id=node_id, hasn_id=hasn_id)
        self._store.node_entities.pop(node_id, None)
        self._store.owner_nodes.pop(node_id, None)


async def test_is_node_session_gateway():
    """结构化子类型检查：`_InMemoryNodeSessionGateway` 同时满足 NodeSession + Presence 端口。"""
    gateway = _InMemoryNodeSessionGateway(
        store=_SharedRoutingStore(),
        binding_view=_MemoryNodeBindingView({}),
        clock=_ManualClock(start_ts=timezone.now().timestamp()),
    )
    assert isinstance(gateway, GatewayNodeSessionGateway)
    assert isinstance(gateway, PresenceQuery)


async def _seed_active_binding(binding_map: dict[tuple[str, str], OwnerBindingRef], node_id: str, owner_id: str) -> None:
    binding_map[(node_id, owner_id)] = OwnerBindingRef(
        node_id=node_id,
        owner_id=owner_id,
        binding_id=f'ob_{uuid.uuid4().hex[:12]}',
        status='active',
        expires_at=timezone.now().replace(microsecond=0),
    )


async def test_generation_conflict_rejects_stale_operations():
    """旧代际 connection_id 无法 mark/refresh。"""
    binding_map: dict[tuple[str, str], OwnerBindingRef] = {}
    clock = _ManualClock(start_ts=timezone.now().timestamp())
    store = _SharedRoutingStore()
    gateway = _InMemoryNodeSessionGateway(
        store=store,
        binding_view=_MemoryNodeBindingView(binding_map),
        clock=clock,
    )
    owner = f'h_{uuid.uuid4().hex[:20]}'
    await _seed_active_binding(binding_map, 'n_conflict', owner)

    first = await gateway.register_node(node_id='n_conflict', node_type='desktop')
    second = await gateway.register_node(node_id='n_conflict', node_type='desktop')

    assert await gateway.mark_node_ready(node_id='n_conflict', connection_id=first.connection_id) is False
    assert await gateway.refresh_node_presence(node_id='n_conflict', connection_id=first.connection_id) is False
    assert await gateway.mark_node_ready(node_id='n_conflict', connection_id=second.connection_id) is True


async def test_ttl_expiry_blocks_presence_and_refresh():
    """TTL 到期后不可续期，presence 判定为离线。"""
    clock = _ManualClock(start_ts=timezone.now().timestamp())
    store = _SharedRoutingStore()
    gateway = _InMemoryNodeSessionGateway(
        store=store,
        binding_view=_MemoryNodeBindingView({}),
        clock=clock,
    )
    reg = await gateway.register_node(node_id='n_ttl', node_type='desktop')
    assert await gateway.refresh_node_presence(node_id='n_ttl', connection_id=reg.connection_id) is True

    clock.tick(store.ttl_seconds + 1)
    assert await gateway.refresh_node_presence(node_id='n_ttl', connection_id=reg.connection_id) is False
    assert await gateway.is_node_online(node_id='n_ttl') is False


async def test_agent_not_ready_then_ready_then_offline():
    """未就绪时 agent 在线态 false，就绪后 true，再下降线后 false。"""
    clock = _ManualClock(start_ts=timezone.now().timestamp())
    binding_map: dict[tuple[str, str], OwnerBindingRef] = {}
    store = _SharedRoutingStore()
    binding_view = _MemoryNodeBindingView(binding_map)
    gateway = _InMemoryNodeSessionGateway(
        store=store,
        binding_view=binding_view,
        clock=clock,
    )
    owner = f'h_{uuid.uuid4().hex[:20]}'
    agent = f'a_{uuid.uuid4().hex[:20]}'
    await _seed_active_binding(binding_map, 'n_ready', owner)

    reg = await gateway.register_node(node_id='n_ready', node_type='desktop')
    await gateway.mark_node_ready(node_id='n_ready', connection_id=reg.connection_id)
    await gateway.add_owner(node_id='n_ready', owner_id=owner)
    await gateway.add_agent_presence(node_id='n_ready', owner_id=owner, agent_id=agent)

    assert await gateway.is_agent_online(agent) is False
    assert await gateway.set_agent_readiness(agent_id=agent, online_status='online', health_status='ok') == 'online'
    assert await gateway.is_agent_online(agent) is True
    assert await gateway.set_agent_readiness(agent_id=agent, online_status='degraded', health_status='ok') == 'offline'
    assert await gateway.is_agent_online(agent) is False


async def test_cross_worker_disconnect_clears_shared_presence_state():
    """跨 worker 断开只要共用存储，即时清共享路由。"""
    clock = _ManualClock(start_ts=timezone.now().timestamp())
    binding_map: dict[tuple[str, str], OwnerBindingRef] = {}
    store = _SharedRoutingStore()
    await _seed_active_binding(binding_map, 'n_cross', 'h_owner')
    worker_local = _InMemoryNodeSessionGateway(
        store=store,
        binding_view=_MemoryNodeBindingView(binding_map),
        clock=clock,
        local_nodes={'n_cross'},
    )
    worker_remote = _InMemoryNodeSessionGateway(
        store=store,
        binding_view=_MemoryNodeBindingView(binding_map),
        clock=clock,
        local_nodes=set(),
    )

    reg = await worker_local.register_node(node_id='n_cross', node_type='desktop')
    await worker_local.mark_node_ready(node_id='n_cross', connection_id=reg.connection_id)
    assert await worker_local.add_owner(node_id='n_cross', owner_id='h_owner') is not None
    assert await worker_local.disconnect_node(node_id='n_cross') is True
    assert await worker_remote.disconnect_node(node_id='n_cross') is False
    assert await worker_remote.is_human_online('h_owner') is False
    assert await worker_local.is_node_online(node_id='n_cross') is False
