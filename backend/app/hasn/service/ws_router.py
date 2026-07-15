"""HASN WebSocket 连接管理服务

现行模型：
- Node 先建立物理连接
- Owner 通过 add_owner 建立在线路由资格
- Agent 通过 add_agent 建立 Presence

Redis 数据结构：
  hasn:node_conn                HASH  node_id → JSON{node_type, capacity, connected_at}
  hasn:entity_node              HASH  a_xxx → node_id  (Agent 定向路由)
  hasn:node_entities:{node_id}  SET   {hasn_id, ...}   (Node 上的在线实体；active owner + online agent)
  hasn:user_nodes:{hasn_id}     SET   {node_id, ...}   (Owner 的所有在线节点，用于广播)
  hasn:push:{node_id}           LIST  待推消息队列
  hasn:offline:{hasn_id}        LIST  (7天 TTL)
"""

import json
import logging

from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.hasn_auth import verify_owner_proof
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.app.hasn.service.ws_delivery_bus import ws_delivery_bus
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

# Redis 键
NODE_CONN_KEY = 'hasn:node_conn'
NODE_GENERATION_KEY = 'hasn:node_generation'
ENTITY_NODE_KEY = 'hasn:entity_node'
NODE_ENTITIES_PREFIX = 'hasn:node_entities'
USER_NODES_PREFIX = 'hasn:user_nodes'
PUSH_PREFIX = 'hasn:push'
OFFLINE_PREFIX = 'hasn:offline'
OFFLINE_TTL = 7 * 86400  # 7 天

# 节点存活心跳键（P3 僵尸回收）：每节点一个带 TTL 的 string 键，注册时写、
# 应用层 hasn.ping 心跳续期；过期（非优雅退出，无续期）即视为离线 → 其 agent
# 一并判离线，供他机接管。注意 NODE_CONN_KEY 是单 hash（无法对单字段设 TTL），
# 故另用 per-node alive 键承载 TTL。daemon 心跳间隔 30s，TTL 90s 留 3 次丢包余量。
NODE_ALIVE_PREFIX = 'hasn:node_alive'
NODE_PRESENCE_TTL_SECS = 90

# Agent 运行时就绪键（在线语义收紧）：每个 agent 一个带 TTL 的 string 键，
# **仅当** daemon 心跳报 online_status=online ∧ health_status=ok（= 协议权威「在线」：
# owner 在线 + runtime 已启动连接、可收消息）才写；report degraded/offline 立即删。
# 「在线」判定 = 路由表命中(ENTITY_NODE_KEY) ∧ 节点存活(NODE_ALIVE) ∧ **本键在**。
# 修「启动后显示在线但 runtime 未就绪、发消息报错」——路由注册 ≠ 网关就绪，
# 旧 is_agent_online 只看路由+节点存活、漏了网关就绪这维，会谎报在线。
# TTL 同节点存活键（90s，daemon 心跳 30s 留 3 次丢包余量）：心跳停 → 自然过期判未就绪。
# 注意：**不**参与消息路由（_push_to_entity 仍直读 ENTITY_NODE_KEY），只收紧对外显示的在线态。
AGENT_READY_PREFIX = 'hasn:agent_ready'

# 兼容别名（旧代码过渡期引用）
AGENT_NODE_KEY = ENTITY_NODE_KEY
CLIENT_CONN_KEY = NODE_CONN_KEY
USER_CLIENTS_PREFIX = USER_NODES_PREFIX
AGENT_CLIENT_KEY = ENTITY_NODE_KEY

_REFRESH_PRESENCE_IF_CURRENT_SCRIPT = """
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then
    return 0
end
redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
return 1
"""

_UNREGISTER_NODE_IF_CURRENT_SCRIPT = """
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then
    return 0
end

local entity_ids = redis.call('SMEMBERS', KEYS[3])
for _, hasn_id in ipairs(entity_ids) do
    if redis.call('HGET', KEYS[4], hasn_id) == ARGV[1] then
        redis.call('HDEL', KEYS[4], hasn_id)
    end
    if string.sub(hasn_id, 1, 2) == 'h_' then
        redis.call('SREM', ARGV[3] .. ':' .. hasn_id, ARGV[1])
    end
end

redis.call('DEL', KEYS[3])
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('DEL', KEYS[5])
redis.call('HDEL', KEYS[1], ARGV[1])
return 1
"""

_ACK_OFFLINE_PREFIX_SCRIPT = """
for index = 1, #ARGV do
    if redis.call('LINDEX', KEYS[1], index - 1) ~= ARGV[index] then
        return 0
    end
end
redis.call('LTRIM', KEYS[1], #ARGV, -1)
return #ARGV
"""


def _decode_offline_message(raw: str | bytes) -> dict | None:
    """解析离线队列帧；畸形或非对象 JSON 不参与补推。"""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class WsRouterService:
    """WebSocket 连接路由管理（统一实体模型）"""

    # ─── 节点连接管理 ───

    async def register_node(
        self,
        node_id: str,
        node_type: str,
        ws: WebSocket,
        capacity: int = 1,
    ) -> str:
        """注册节点在线（不绑定任何用户身份）"""
        connection_id = uuid4().hex
        conn_info = json.dumps({
            'node_type': node_type,
            'capacity': capacity,
            'connected_at': timezone.now().isoformat(),
            'connection_id': connection_id,
        })
        # 代际先写：旧连接的原子清理要么发生在此之前并完整结束，要么看到新代际后 no-op。
        await redis_client.hset(NODE_GENERATION_KEY, node_id, connection_id)
        await redis_client.hset(NODE_CONN_KEY, node_id, conn_info)
        # P3：写节点存活键（带 TTL），由 hasn.ping 心跳续期；过期即视为离线。
        await redis_client.set(f'{NODE_ALIVE_PREFIX}:{node_id}', '1', ex=NODE_PRESENCE_TTL_SECS)

        # 存储 WebSocket 引用（进程内，用于直接推送）
        _ws_connections[node_id] = ws
        _ws_connection_ids[node_id] = connection_id
        _ws_ready_connection_ids.pop(node_id, None)
        return connection_id

    async def mark_node_ready(self, node_id: str, connection_id: str) -> bool:
        """握手首帧发出后开放业务投递，并立即排空断线窗口的持久队列。"""
        if _ws_connection_ids.get(node_id) != connection_id:
            return False
        current = await redis_client.hget(NODE_GENERATION_KEY, node_id)
        if current != connection_id:
            return False
        _ws_ready_connection_ids[node_id] = connection_id
        await ws_delivery_bus.drain_node(node_id)
        return True

    async def refresh_node_presence(self, node_id: str, connection_id: str) -> bool:
        """应用层心跳续期节点存活 TTL（hasn.ping 触发）。

        仅当前连接代际可续期。旧 socket 即使迟到发 ping，也不能制造在线假象。
        """
        refreshed = await redis_client.eval(
            _REFRESH_PRESENCE_IF_CURRENT_SCRIPT,
            2,
            NODE_GENERATION_KEY,
            f'{NODE_ALIVE_PREFIX}:{node_id}',
            node_id,
            connection_id,
            NODE_PRESENCE_TTL_SECS,
        )
        return bool(refreshed)

    async def unregister_node(self, node_id: str, connection_id: str) -> bool:
        """注销节点，清理所有实体绑定。

        断连清理是 best-effort：redis 不可用 / 连接 transport 已关闭（断连与 event-loop
        拆除并发、或池中陈旧连接）时只记 warning，绝不抛出——否则异常会冒泡出 WS
        handler 的 finally，触发 "Application callable raised an exception"。漏清的
        presence 键带 TTL（NODE_ALIVE_PREFIX）会自然过期自愈；进程内 WS 引用无论如何都移除。
        """
        removed = False
        try:
            removed = bool(
                await redis_client.eval(
                    _UNREGISTER_NODE_IF_CURRENT_SCRIPT,
                    5,
                    NODE_GENERATION_KEY,
                    NODE_CONN_KEY,
                    f'{NODE_ENTITIES_PREFIX}:{node_id}',
                    ENTITY_NODE_KEY,
                    f'{NODE_ALIVE_PREFIX}:{node_id}',
                    node_id,
                    connection_id,
                    USER_NODES_PREFIX,
                )
            )
        except Exception as e:
            logger.warning(f'[HASN] 注销节点 redis 清理失败 (非致命，TTL 自愈): {node_id} - {e}')
        finally:
            # 本地引用同样按代际清理，旧 handler 不得弹掉已覆盖的新 socket。
            if _ws_connection_ids.get(node_id) == connection_id:
                _ws_connections.pop(node_id, None)
                _ws_connection_ids.pop(node_id, None)
                _ws_ready_connection_ids.pop(node_id, None)
        return removed

    # ─── 现行控制平面：Owner Binding / Agent Presence ───

    async def add_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict,
        db: AsyncSession,
        *,
        skip_proof_verify: bool = False,
    ) -> dict[str, Any]:
        if skip_proof_verify:
            # 已在 WS 握手的 authenticate_ws_connection 中验证通过
            proof = {
                'auth_profile': owner_proof.get('type', 'bearer_token'),
                'scopes': {'bind_owner': True, 'register_agent': True},
                'expires_at': timezone.now() + timedelta(days=7),
            }
        else:
            proof = await verify_owner_proof(owner_id, owner_proof, node_id, db)
        binding = await hasn_node_bindings_service.add_owner_binding(
            db=db,
            node_id=node_id,
            owner_id=owner_id,
            auth_profile=proof['auth_profile'],
            scopes=proof['scopes'],
            expires_at=proof['expires_at'],
        )
        await self._register_entity(node_id, owner_id, is_human=True)
        return {
            'binding_id': binding.binding_id,
            'owner_id': owner_id,
            'accepted': True,
            'scopes': binding.scopes,
            'expires_at': binding.expires_at.isoformat() if binding.expires_at else None,
        }

    async def renew_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict,
        db: AsyncSession,
    ) -> dict[str, Any]:
        proof = await verify_owner_proof(owner_id, owner_proof, node_id, db)
        try:
            binding = await hasn_node_bindings_service.renew_owner_binding(
                db=db,
                node_id=node_id,
                owner_id=owner_id,
                expires_at=proof['expires_at'],
            )
        except Exception:
            # Fallback to add_owner if the binding does not exist
            binding = await hasn_node_bindings_service.add_owner_binding(
                db=db,
                node_id=node_id,
                owner_id=owner_id,
                auth_profile=proof['auth_profile'],
                scopes=proof['scopes'],
                expires_at=proof['expires_at'],
            )
            await self._register_entity(node_id, owner_id, is_human=True)
        return {
            'binding_id': binding.binding_id,
            'owner_id': owner_id,
            'accepted': True,
            'expires_at': binding.expires_at.isoformat() if binding.expires_at else None,
        }

    async def remove_owner(
        self,
        node_id: str,
        owner_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        removed = await hasn_node_bindings_service.remove_owner_binding(
            db=db,
            node_id=node_id,
            owner_id=owner_id,
        )
        # 移除 human 路由
        await self.unregister_entity_route(node_id, owner_id)
        # 下线该 owner 在本节点上的 agent
        entity_ids = await redis_client.smembers(f'{NODE_ENTITIES_PREFIX}:{node_id}')
        for hasn_id in entity_ids:
            if not str(hasn_id).startswith('a_'):
                continue
            result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
            agent = result.scalar_one_or_none()
            if agent and agent.owner_id == owner_id:
                await self.unregister_entity_route(node_id, hasn_id)
        return {'owner_id': owner_id, 'accepted': bool(removed)}

    async def list_owners(self, node_id: str, db: AsyncSession) -> dict[str, Any]:
        bindings = await hasn_node_bindings_service.list_active_bindings(db=db, node_id=node_id)
        return {
            'owners': [
                {
                    'binding_id': b.binding_id,
                    'owner_id': b.owner_id,
                    'status': b.status,
                    'expires_at': b.expires_at.isoformat() if b.expires_at else None,
                }
                for b in bindings
            ]
        }

    async def add_agent_presence(
        self,
        node_id: str,
        agent_id: str,
        owner_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        binding = await hasn_node_bindings_service.get_active_binding(db=db, node_id=node_id, owner_id=owner_id)
        if not binding:
            return {'agent_id': agent_id, 'accepted': False, 'reason': 'owner 未绑定到当前 node'}

        err = await self._validate_agent(node_id, agent_id, {'owner_id': owner_id}, db)
        if err:
            return {'agent_id': agent_id, 'accepted': False, 'reason': err['reason']}
        await self._register_entity(node_id, agent_id, is_human=False)

        # 更新 hasn_agents 表的 node_id 字段
        result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == agent_id))
        agent = result.scalar_one_or_none()
        if agent:
            agent.node_id = node_id

        return {'agent_id': agent_id, 'accepted': True}

    async def remove_agent_presence(self, node_id: str, agent_id: str) -> dict[str, Any]:
        await self.unregister_entity_route(node_id, agent_id)
        # 优雅下线：连路由带就绪键一并清，避免残留就绪键把刚离线的 agent 误判在线
        # （虽然就绪键有 TTL 会自然过期，但主动清更即时）。
        await self._clear_agent_readiness(agent_id)
        return {'agent_id': agent_id, 'accepted': True}

    async def set_agent_readiness(
        self, agent_id: str, online_status: str, health_status: str | None
    ) -> str | None:
        """按 daemon 心跳携带的运行时健康写/删 agent 就绪键（在线语义收紧）。

        仅当 ``online_status == 'online' and health_status == 'ok'``（协议权威「在线」：
        owner 在线 + runtime 已启动连接、可收消息）才写带 TTL 的就绪键；
        report ``degraded``/``offline``（runtime 未就绪 / 连接中）立即删键 → 对外显示为
        离线/连接中，而不再谎报在线。写就绪键**绝不**影响消息路由（见键注释）。

        返回**在线态翻转标记**，供调用方按需触发「联系人在线圆点实时刷新」（presence→contacts
        WSPUSH）：就绪键从无到有返回 ``'online'``、从有到无返回 ``'offline'``、无翻转（普通心跳
        keepalive / 一直离线）返回 ``None``。只在**真翻转**时返回非 None——避免每拍心跳都扇出失效。
        TTL 自然过期导致的下线不经此函数（无翻转事件可挂），靠联系人列表下次重刷 / local_first
        后台刷新兜底。
        """
        if not agent_id:
            return None
        key = f'{AGENT_READY_PREFIX}:{agent_id}'
        now_ready = online_status == 'online' and health_status == 'ok'
        was_ready = bool(await redis_client.exists(key))
        if now_ready:
            # 一直在线的普通心跳只刷新 TTL（keepalive），不算翻转；从离线转在线才算。
            await redis_client.set(key, '1', ex=NODE_PRESENCE_TTL_SECS)
            return None if was_ready else 'online'
        # runtime 未就绪：删键。仅当此前在线（was_ready）才算「转离线」翻转。
        if was_ready:
            await redis_client.delete(key)
            return 'offline'
        return None

    async def _clear_agent_readiness(self, agent_id: str) -> None:
        if agent_id:
            await redis_client.delete(f'{AGENT_READY_PREFIX}:{agent_id}')

    async def _validate_agent(
        self,
        node_id: str,
        hasn_id: str,
        entity: dict,
        db: AsyncSession,
    ) -> dict | None:
        """校验 Agent 实体。返回 None 表示通过。"""
        owner_id = entity.get('owner_id', '')

        # 查询 Agent 记录
        result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
        agent = result.scalar_one_or_none()

        if not agent:
            return {'hasn_id': hasn_id, 'reason': 'Agent 不存在'}
        if agent.owner_id != owner_id:
            return {'hasn_id': hasn_id, 'reason': f'owner_id 不匹配 (期望 {agent.owner_id})'}
        if agent.status != 'active':
            return {'hasn_id': hasn_id, 'reason': 'Agent 已停用'}

        # 检查是否已被其他节点上报——接管权威改由 Redis 就绪判据裁定（不再看 per-worker WS 连接）。
        existing_node = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if existing_node and existing_node != node_id:
            # 旧持有者必须「节点心跳存活(_node_alive) 且 该 agent runtime 已就绪(_agent_ready)」
            # 才算真正在服务、值得保护，才拒绝新节点接管；否则（节点已死 / runtime 降级未就绪）
            # 一律放行新节点接管，并释放其残留路由。
            #
            # Why 换判据：旧实现用 `_ws_connections`（仅当前 worker 进程内可见的连接）——多 worker
            # 部署下看不全，且**降级持有者的 WS 仍连着** → 误判「在服务」→ 挡住健康设备接管 →
            # 消息路由卡死在降级设备上黑洞（换设备接不了管、旧机又收不动）。就绪键(agent_ready)
            # 才是「runtime 真能收消息」的权威信号，与 `is_agent_online` 的三闸门同源。
            holder_serving = await self._node_alive(existing_node) and await self._agent_ready(hasn_id)
            if holder_serving:
                return {'hasn_id': hasn_id, 'reason': f'已在节点 {existing_node} 上运行'}
            # 旧持有者非就绪（死节点 / 降级 runtime / 残留路由）→ 释放路由，允许新节点接管
            logger.warning(
                f'Agent {hasn_id} 的旧节点 {existing_node} 未在就绪服务'
                f'（node_alive/agent_ready 缺失），允许新节点 {node_id} 接管'
            )
            await self.unregister_entity_route(existing_node, hasn_id)

        return None

    async def _register_entity(self, node_id: str, hasn_id: str, *, is_human: bool) -> None:
        """将实体注册到路由表"""
        # 统一路由表
        await redis_client.hset(ENTITY_NODE_KEY, hasn_id, node_id)
        # Node 实体集合
        await redis_client.sadd(f'{NODE_ENTITIES_PREFIX}:{node_id}', hasn_id)
        # Human 额外维护多节点集合（用于广播）
        if is_human:
            await redis_client.sadd(f'{USER_NODES_PREFIX}:{hasn_id}', node_id)

    async def unregister_entity_route(self, node_id: str, hasn_id: str) -> None:
        """内部帮助函数：从路由表移除单个在线实体"""
        existing = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if existing == node_id:
            await redis_client.hdel(ENTITY_NODE_KEY, hasn_id)
            await redis_client.srem(f'{NODE_ENTITIES_PREFIX}:{node_id}', hasn_id)
            if hasn_id.startswith('h_'):
                await redis_client.srem(f'{USER_NODES_PREFIX}:{hasn_id}', node_id)

    # ─── 消息推送 ───

    async def push_message_to(self, target_hasn_id: str, payload: dict) -> bool:
        """
        统一消息推送入口

        返回 True 表示在线推送成功，False 表示进入离线队列
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        target_type = 'human' if target_hasn_id.startswith('h_') else 'agent'

        if target_type == 'human':
            return await self._push_to_human(target_hasn_id, payload_json)
        return await self._push_to_entity(target_hasn_id, payload_json)

    async def broadcast_sync_invalidate(self, kind: str, revision: str, owner_id: str | None = None) -> int:
        """向在线节点推送 ``hasn.sync.invalidate``（配置/目录变更信号，doc02-07）。

        - ``owner_id=None`` → 全部在线节点（全局 kind：builtin_catalog/common_skills/
          platform_config）；指定 → 仅该 owner 的在线节点（owner 定向 kind，如 agents）。
        - 跨 worker 经投递总线 fan-out（owner 定向逐节点、全局走 broadcast），
          多 worker 部署下也能覆盖落在其它 worker 的连接。
        - **不入离线队列**：invalidate 是幂等「去拉最新」信号，离线节点靠重连
          ``hasn.connected`` 握手对账追平；单个连接发送失败也不影响其它。
        - 返回在线节点数（best-effort 计数）。
        """
        payload_json = json.dumps(
            {
                'hasn': 'hasn/0.2',
                'method': 'hasn.sync.invalidate',
                'params': {'kind': kind, 'revision': revision},
            },
            ensure_ascii=False,
        )

        if owner_id:
            node_ids = await redis_client.smembers(f'{USER_NODES_PREFIX}:{owner_id}')
            for nid in node_ids:
                await self._send_or_publish(nid, payload_json)
            return len(node_ids)

        # 全局：跨 worker 广播给所有在线 node（每个 worker 下发其本地全部连接）
        await ws_delivery_bus.publish_broadcast(payload_json)
        return await redis_client.hlen(NODE_CONN_KEY)

    async def push_to_owner_excluding_agent_node(self, owner_id: str, agent_id: str, payload: dict) -> bool:
        """Owner 透明 fanout：把「发给 Agent 的消息」也投给 Agent 主人的在线节点，
        但**跳过 Agent 实体当前所在的节点**。

        该节点已通过 `_push_to_entity(agent_id)` 收到本消息；而 Agent 通常就跑在
        主人的 daemon 上（entity_node[agent] == user_nodes[owner] 中的同一节点），
        若不排除，同一 daemon 会把同一条消息收到两遍 → 镜像两次、派发 runtime
        两次（表现为「发一条、收两条回复」）。多端时主人的其它节点仍照常收到。
        """
        agent_node = await redis_client.hget(ENTITY_NODE_KEY, agent_id)
        exclude = {agent_node} if agent_node else None
        payload_json = json.dumps(payload, ensure_ascii=False)
        return await self._push_to_human(owner_id, payload_json, exclude)

    async def push_to_owner(self, owner_id: str, payload: dict) -> bool:
        """把 payload 投给某 owner 的**全部**在线节点（不排除任何节点）。

        用于 owner_copy 旁观**出站**补投：本主人自有分身经云端 `message.send` 主动发给
        外部方的消息，发送方 daemon **没有本地 echo**（工具直发不落本地，回复才有本地
        echo）。要让主人在旁观线程里看到自家分身发出的这条，须把消息也投给主人自己的
        节点。本地分身通常就跑在主人 daemon 上——那个节点正是「缺这条」的节点，故与
        `push_to_owner_excluding_agent_node` **相反**，**不排除** agent 所在节点。
        离线时 `_push_to_human` 会入离线队列，主人重连后照常补投。
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        return await self._push_to_human(owner_id, payload_json, None)

    async def _send_or_publish(self, node_id: str, payload_json: str) -> bool:
        """投给某 node：连接在本 worker 直发；否则经投递总线交给持有它的 worker。

        多 worker（``--workers N``）部署下 ``_ws_connections`` 只含**本进程** accept 的
        连接，本地 miss **不代表离线**——必须经 Redis pub/sub fan-out 让真正持有该连接的
        worker 下发。这替换了旧的 ``rpush hasn:push:{node_id}``（一个无消费者的死队列，
        是「多 worker 完全收不到消息」的根因）。
        """
        ws = _ws_connections.get(node_id)
        connection_id = _ws_connection_ids.get(node_id)
        ready_id = _ws_ready_connection_ids.get(node_id)
        current_id = (
            await redis_client.hget(NODE_GENERATION_KEY, node_id)
            if ws is not None and connection_id and ready_id == connection_id
            else None
        )
        if (
            ws is not None
            and connection_id == ready_id == current_id
            and await ws_delivery_bus._safe_send(ws, payload_json)
        ):
            return True
        return await ws_delivery_bus.publish_to_node(node_id, payload_json)

    async def _push_to_human(self, hasn_id: str, payload_json: str, exclude_nodes: set[str] | None = None) -> bool:
        """Human 消息 → 投所有在线节点（exclude_nodes 跳过已由其它路由收到本消息的节点）。

        在线判定以 Redis presence（``user_nodes``）为权威：有在线节点即逐个
        ``_send_or_publish``（跨 worker 经投递总线）；无在线节点才入离线队列。
        """
        node_ids = await redis_client.smembers(f'{USER_NODES_PREFIX}:{hasn_id}')
        if exclude_nodes:
            node_ids = {nid for nid in node_ids if nid not in exclude_nodes}

        if not node_ids:
            await self._enqueue_offline(hasn_id, payload_json)
            return False

        delivered = False
        for nid in node_ids:
            delivered = await self._send_or_publish(nid, payload_json) or delivered
        if not delivered:
            await self._enqueue_offline(hasn_id, payload_json)
        return delivered

    async def push_self_sync(self, owner_id: str, payload: dict, exclude_node: str) -> None:
        """多端自同步：把发送方自己的消息回显给该 owner 的**其它**在线节点
        （跳过发送节点 ``exclude_node``）。

        仅在线 best-effort，**不入离线队列**：发送端本端已有该消息、且消息已落库，
        离线补推会造成重复 self_sent 回显。跨 worker 经投递总线。
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        node_ids = await redis_client.smembers(f'{USER_NODES_PREFIX}:{owner_id}')
        for nid in node_ids:
            if nid != exclude_node:
                await self._send_or_publish(nid, payload_json)

    async def _push_to_entity(self, hasn_id: str, payload_json: str) -> bool:
        """Agent/通用实体消息 → 查统一路由表（跨 worker 经投递总线）"""
        node_id = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if node_id and await self._send_or_publish(node_id, payload_json):
            return True

        # 离线
        await self._enqueue_offline(hasn_id, payload_json)
        return False

    async def _enqueue_offline(self, hasn_id: str, payload_json: str) -> None:
        """消息入离线队列"""
        key = f'{OFFLINE_PREFIX}:{hasn_id}'
        await redis_client.rpush(key, payload_json)
        await redis_client.expire(key, OFFLINE_TTL)

    # ─── 离线消息补推 ───

    async def claim_offline_messages(
        self,
        entity_ids: list[str],
    ) -> tuple[list[dict], dict[str, list[str]]]:
        """读取待补推消息但暂不删除，返回帧内容和用于成功确认的原始队列前缀。"""
        all_msgs: list[dict] = []
        claims: dict[str, list[str]] = {}
        for entity_id in entity_ids:
            key = f'{OFFLINE_PREFIX}:{entity_id}'
            raw_messages = list(await redis_client.lrange(key, 0, -1))
            if not raw_messages:
                continue
            claims[key] = raw_messages
            all_msgs.extend(message for raw in raw_messages if (message := _decode_offline_message(raw)))
        all_msgs.sort(key=lambda message: message.get('created_time', ''))
        return all_msgs, claims

    async def ack_offline_messages(self, claims: dict[str, list[str]]) -> None:
        """发送成功后仅删除领取时看到的相同前缀，保留并发新入队消息。"""
        for key, raw_messages in claims.items():
            if not raw_messages:
                continue
            await redis_client.eval(
                _ACK_OFFLINE_PREFIX_SCRIPT,
                1,
                key,
                *raw_messages,
            )

    async def get_offline_messages(
        self,
        entity_ids: list[str],
    ) -> list[dict]:
        """获取并清理离线消息（所有已上报实体）"""
        all_msgs = []

        for eid in entity_ids:
            key = f'{OFFLINE_PREFIX}:{eid}'
            msgs = await redis_client.lrange(key, 0, -1)
            all_msgs.extend(message for raw in msgs if (message := _decode_offline_message(raw)))
            if msgs:
                await redis_client.delete(key)

        # 按时间排序
        all_msgs.sort(key=lambda m: m.get('created_time', ''))
        return all_msgs

    # ─── 在线状态查询 ───

    async def is_human_online(self, hasn_id: str) -> bool:
        nodes = await redis_client.smembers(f'{USER_NODES_PREFIX}:{hasn_id}')
        # P3：至少一个节点存活心跳未过期才算在线，僵尸节点不再让 owner 误判在线。
        for node in nodes:
            if await self._node_alive(node):
                return True
        return False

    async def is_agent_online(self, hasn_id: str) -> bool:
        node = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if node is None:
            return False
        # P3：路由表指向的节点必须仍有存活心跳，否则是僵尸路由（节点已非优雅退出）→ 判离线。
        if not await self._node_alive(node):
            return False
        # 在线语义收紧：路由命中 + 节点存活 还不够，runtime 网关须**已就绪**
        # （心跳报 online+ok 才写就绪键）。否则是「连接中/未就绪」→ 判离线，
        # 杜绝「启动后显示在线、发消息却报 runtime 未就绪」。
        return await self._agent_ready(hasn_id)

    async def _node_alive(self, node_id: str) -> bool:
        """节点存活键（心跳 TTL）是否仍在。"""
        return bool(await redis_client.exists(f'{NODE_ALIVE_PREFIX}:{node_id}'))

    async def _agent_ready(self, hasn_id: str) -> bool:
        """agent 运行时就绪键（心跳 online+ok 才写、TTL 续期）是否仍在。"""
        return bool(await redis_client.exists(f'{AGENT_READY_PREFIX}:{hasn_id}'))

    async def get_entity_node(self, hasn_id: str) -> str | None:
        """返回实体（Agent/Human）当前所在节点 id；不在线返回 None。

        用于设备管理页把「名下在线 Agent」归到其所在设备卡。
        """
        return await redis_client.hget(ENTITY_NODE_KEY, hasn_id)

    async def is_node_online(self, node_id: str) -> bool:
        """节点是否在线：以**存活心跳键**为准（P3）。

        心跳过期（非优雅退出，无续期）→ 离线，即便 NODE_CONN_KEY 仍残留（僵尸）。
        """
        return await self._node_alive(node_id)

    async def disconnect_node(self, node_id: str) -> bool:
        """主动断开某节点：清理路由 presence（释放其 agent 供他机接管）+
        关闭其 WS（若连接落在本 worker 进程内）。

        返回 True 表示本进程内确有该连接并已尝试关闭；多 worker 部署下连接可能在
        其它 worker，此处仅清共享 presence，物理 socket 关闭由该 worker 的收发循环
        在下一次 IO 失败 / 重认证时完成。
        """
        ws = _ws_connections.get(node_id)
        connection_id = (
            _ws_connection_ids.get(node_id) if ws is not None else await redis_client.hget(NODE_GENERATION_KEY, node_id)
        )
        if connection_id:
            await self.unregister_node(node_id, connection_id)
        if ws is not None:
            try:
                await ws.close(code=4002, reason='remote logout')
            except Exception:
                pass
            return True
        return False

    async def get_entity_status(self, hasn_id: str) -> str:
        if hasn_id.startswith('h_'):
            return 'online' if await self.is_human_online(hasn_id) else 'offline'
        return 'online' if await self.is_agent_online(hasn_id) else 'offline'

    async def get_online_map(self, entity_ids: list[str]) -> dict[str, bool]:
        """批量查 agent 实时在线状态（Redis presence ENTITY_NODE_KEY）。

        ENTITY_NODE_KEY 由 daemon 上线时 add_agent 写入、断线时 unregister_node 清除，
        因此这是权威的「当前是否被某节点在线持有」判定。**不要**用持久列
        HasnAgents.online_status —— 它只在心跳时写、断线不清零，会过期误判。
        """
        if not entity_ids:
            return {}
        nodes = await redis_client.hmget(ENTITY_NODE_KEY, entity_ids)
        # P3：路由表命中后还需该节点存活心跳未过期，否则是僵尸路由 → 判离线。
        # 在线语义收紧：再叠加 agent 就绪键（心跳 online+ok 才写），批量 MGET 取。
        ready_flags = await redis_client.mget([f'{AGENT_READY_PREFIX}:{eid}' for eid in entity_ids])
        result: dict[str, bool] = {}
        for eid, node, ready in zip(entity_ids, nodes, ready_flags):
            result[eid] = bool(node) and bool(ready) and await self._node_alive(node)
        return result


# 进程内 WebSocket 连接引用与代际。拆成两个表以保持既有投递代码的 WebSocket 值契约。
_ws_connections: dict[str, WebSocket] = {}
_ws_connection_ids: dict[str, str] = {}
_ws_ready_connection_ids: dict[str, str] = {}

# 全局单例
ws_router: WsRouterService = WsRouterService()
