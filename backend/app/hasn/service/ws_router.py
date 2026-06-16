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

logger = logging.getLogger(__name__)

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.redis import redis_client
from backend.utils.timezone import timezone

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service.hasn_auth import verify_owner_proof
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service

# Redis 键
NODE_CONN_KEY = 'hasn:node_conn'
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

# 兼容别名（旧代码过渡期引用）
AGENT_NODE_KEY = ENTITY_NODE_KEY
CLIENT_CONN_KEY = NODE_CONN_KEY
USER_CLIENTS_PREFIX = USER_NODES_PREFIX
AGENT_CLIENT_KEY = ENTITY_NODE_KEY


class WsRouterService:
    """WebSocket 连接路由管理（统一实体模型）"""

    # ─── 节点连接管理 ───

    async def register_node(
        self,
        node_id: str,
        node_type: str,
        ws: WebSocket,
        capacity: int = 1,
    ) -> None:
        """注册节点在线（不绑定任何用户身份）"""
        conn_info = json.dumps({
            'node_type': node_type,
            'capacity': capacity,
            'connected_at': timezone.now().isoformat(),
        })
        await redis_client.hset(NODE_CONN_KEY, node_id, conn_info)
        # P3：写节点存活键（带 TTL），由 hasn.ping 心跳续期；过期即视为离线。
        await redis_client.set(
            f'{NODE_ALIVE_PREFIX}:{node_id}', '1', ex=NODE_PRESENCE_TTL_SECS
        )

        # 存储 WebSocket 引用（进程内，用于直接推送）
        _ws_connections[node_id] = ws

    async def refresh_node_presence(self, node_id: str) -> None:
        """应用层心跳续期节点存活 TTL（hasn.ping 触发）。

        无条件续期：能发 ping 即说明该连接活着。死设备不会发 ping，其键自然过期。
        """
        await redis_client.set(
            f'{NODE_ALIVE_PREFIX}:{node_id}', '1', ex=NODE_PRESENCE_TTL_SECS
        )

    async def unregister_node(self, node_id: str) -> None:
        """注销节点，清理所有实体绑定。

        断连清理是 best-effort：redis 不可用 / 连接 transport 已关闭（断连与 event-loop
        拆除并发、或池中陈旧连接）时只记 warning，绝不抛出——否则异常会冒泡出 WS
        handler 的 finally，触发 "Application callable raised an exception"。漏清的
        presence 键带 TTL（NODE_ALIVE_PREFIX）会自然过期自愈；进程内 WS 引用无论如何都移除。
        """
        try:
            # 拿到该 Node 上的所有实体
            entity_ids = await redis_client.smembers(f'{NODE_ENTITIES_PREFIX}:{node_id}')

            for hasn_id in entity_ids:
                # 从统一路由表移除
                existing = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
                if existing == node_id:
                    await redis_client.hdel(ENTITY_NODE_KEY, hasn_id)

                # 如果是 Human，从 user_nodes 移除
                if hasn_id.startswith('h_'):
                    await redis_client.srem(f'{USER_NODES_PREFIX}:{hasn_id}', node_id)

            # 清理 Node 实体集合
            await redis_client.delete(f'{NODE_ENTITIES_PREFIX}:{node_id}')
            # 清理 Node 连接记录
            await redis_client.hdel(NODE_CONN_KEY, node_id)
            # 清理节点存活键（P3）
            await redis_client.delete(f'{NODE_ALIVE_PREFIX}:{node_id}')
        except Exception as e:  # noqa: BLE001 - 清理尽力而为，redis 故障不得炸断连路径
            logger.warning(f'[HASN] 注销节点 redis 清理失败 (非致命，TTL 自愈): {node_id} - {e}')
        finally:
            # 移除 WebSocket 引用（进程内，无论 redis 是否可用都要做）
            _ws_connections.pop(node_id, None)

    # ─── 现行控制平面：Owner Binding / Agent Presence ───

    async def add_owner(
        self,
        node_id: str,
        owner_id: str,
        owner_proof: dict,
        db: AsyncSession,
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
        result = await db.execute(
            select(HasnAgents).where(HasnAgents.hasn_id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent:
            agent.node_id = node_id

        return {'agent_id': agent_id, 'accepted': True}

    async def remove_agent_presence(self, node_id: str, agent_id: str) -> dict[str, Any]:
        await self.unregister_entity_route(node_id, agent_id)
        return {'agent_id': agent_id, 'accepted': True}

    async def _validate_agent(
        self, node_id: str, hasn_id: str, entity: dict, db: AsyncSession,
    ) -> dict | None:
        """校验 Agent 实体。返回 None 表示通过。"""
        owner_id = entity.get('owner_id', '')

        # 查询 Agent 记录
        result = await db.execute(
            select(HasnAgents).where(HasnAgents.hasn_id == hasn_id)
        )
        agent = result.scalar_one_or_none()

        if not agent:
            return {'hasn_id': hasn_id, 'reason': 'Agent 不存在'}
        if agent.owner_id != owner_id:
            return {'hasn_id': hasn_id, 'reason': f'owner_id 不匹配 (期望 {agent.owner_id})'}
        if agent.status != 'active':
            return {'hasn_id': hasn_id, 'reason': 'Agent 已停用'}

        # 检查是否已被其他节点上报
        existing_node = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if existing_node and existing_node != node_id:
            # 检查旧节点是否还在线（WS 连接是否存活）
            old_ws = _ws_connections.get(existing_node)
            if old_ws is None:
                # 旧节点已断开（非优雅关闭导致 Redis 残留），自动接管
                logger.warning(
                    f'Agent {hasn_id} 的旧节点 {existing_node} 已离线，'
                    f'允许新节点 {node_id} 接管'
                )
                await self.unregister_entity_route(existing_node, hasn_id)
            else:
                return {'hasn_id': hasn_id, 'reason': f'已在节点 {existing_node} 上运行'}

        return None

    async def _register_entity(self, node_id: str, hasn_id: str, is_human: bool) -> None:
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
        else:
            return await self._push_to_entity(target_hasn_id, payload_json)

    async def broadcast_sync_invalidate(
        self, kind: str, revision: str, owner_id: str | None = None
    ) -> int:
        """向在线节点推送 ``hasn.sync.invalidate``（配置/目录变更信号，doc02-07）。

        - ``owner_id=None`` → 全部在线节点（全局 kind：builtin_catalog/common_skills/
          platform_config）；指定 → 仅该 owner 的在线节点（owner 定向 kind，如 agents）。
        - 单 worker 部署下 ``_ws_connections`` 持有全部连接，遍历即完整覆盖。
        - **不入离线队列**：invalidate 是幂等「去拉最新」信号，离线节点靠重连
          ``hasn.connected`` 握手对账追平；单个连接发送失败也不影响其它。
        - 返回成功 push 的节点数。
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
        else:
            node_ids = set(_ws_connections.keys())

        pushed = 0
        for nid in node_ids:
            ws = _ws_connections.get(nid)
            if ws is None:
                continue
            try:
                await ws.send_text(payload_json)
                pushed += 1
            except Exception:  # 单连接失败不影响其它；离线靠握手对账
                pass
        return pushed

    async def push_to_owner_excluding_agent_node(
        self, owner_id: str, agent_id: str, payload: dict
    ) -> bool:
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

    async def _push_to_human(
        self, hasn_id: str, payload_json: str, exclude_nodes: set[str] | None = None
    ) -> bool:
        """Human 消息 → 广播所有在线节点（exclude_nodes 跳过已经由其它路由收到本消息的节点）"""
        node_ids = await redis_client.smembers(f'{USER_NODES_PREFIX}:{hasn_id}')
        if exclude_nodes:
            node_ids = {nid for nid in node_ids if nid not in exclude_nodes}
        pushed = False

        for nid in node_ids:
            ws = _ws_connections.get(nid)
            if ws:
                try:
                    await ws.send_text(payload_json)
                    pushed = True
                    continue
                except Exception:
                    pass
            # 回退到 Redis 队列
            await redis_client.rpush(f'{PUSH_PREFIX}:{nid}', payload_json)
            pushed = True

        if not pushed:
            await self._enqueue_offline(hasn_id, payload_json)

        return pushed

    async def _push_to_entity(self, hasn_id: str, payload_json: str) -> bool:
        """Agent/通用实体消息 → 查统一路由表"""
        node_id = await redis_client.hget(ENTITY_NODE_KEY, hasn_id)
        if node_id:
            ws = _ws_connections.get(node_id)
            if ws:
                try:
                    await ws.send_text(payload_json)
                    return True
                except Exception:
                    pass
            # WS 不可用 → Redis 队列
            await redis_client.rpush(f'{PUSH_PREFIX}:{node_id}', payload_json)
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

    async def get_offline_messages(
        self,
        entity_ids: list[str],
    ) -> list[dict]:
        """获取并清理离线消息（所有已上报实体）"""
        all_msgs = []

        for eid in entity_ids:
            key = f'{OFFLINE_PREFIX}:{eid}'
            msgs = await redis_client.lrange(key, 0, -1)
            for raw in msgs:
                try:
                    all_msgs.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass
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
        return await self._node_alive(node)

    async def _node_alive(self, node_id: str) -> bool:
        """节点存活键（心跳 TTL）是否仍在。"""
        return bool(await redis_client.exists(f'{NODE_ALIVE_PREFIX}:{node_id}'))

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
        await self.unregister_node(node_id)
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
        else:
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
        result: dict[str, bool] = {}
        for eid, node in zip(entity_ids, nodes):
            result[eid] = bool(node) and await self._node_alive(node)
        return result


# 进程内 WebSocket 连接引用（node_id → WebSocket）
_ws_connections: dict[str, WebSocket] = {}

# 全局单例
ws_router: WsRouterService = WsRouterService()
