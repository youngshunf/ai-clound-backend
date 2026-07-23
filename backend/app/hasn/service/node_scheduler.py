"""HASN 云端节点调度器 (v4.0 统一节点架构)

负责在分布式的云端计算节点池中，为特定的 Agent 挑选最优宿主节点。
并在需要时触发 PROVISION_AGENT 消息定向下发给该云端节点。
"""

import json

from typing import Any
from backend.app.hasn_im.adapters.routing.redis_presence_store import (
    CLIENT_CONN_KEY,
    PUSH_PREFIX,
)
from backend.database.redis import redis_client


class NodeSchedulerService:
    """提供云端节点的负载均衡和分配能力"""

    async def get_all_active_nodes(self) -> list[dict[str, Any]]:
        """获取所有存活节点的状态信息"""
        nodes_raw = await redis_client.hgetall(CLIENT_CONN_KEY)
        nodes = []
        for node_id, data_str in nodes_raw.items():
            if data_str:
                try:
                    info = json.loads(data_str)
                    info['node_id'] = node_id.decode('utf-8') if isinstance(node_id, bytes) else node_id
                    nodes.append(info)
                except Exception:
                    pass
        return nodes

    async def select_optimal_node(self, required_capacity: int = 1, node_type: str = "cloud") -> str | None:
        """选出对于 `node_type` 类型节点中，最优（负载最低或容量最大的）的一个"""
        active_nodes = await self.get_all_active_nodes()

        # 过滤类型相同的节点
        candidates = [n for n in active_nodes if n.get('node_type') == node_type]
        if not candidates:
            return None

        # 简单策略：选择 capacity 最大或按时间最早连接的。
        # TODO Phase 5: 增加动态负载考量 (结合当前 AGENT_NODE_KEY 数量)
        candidates.sort(key=lambda x: x.get('capacity', 0), reverse=True)

        return candidates[0]['node_id']

    async def provision_agent_to_node(self, agent_hasn_id: str, owner_id: str, node_id: str, config: dict[str, Any]) -> bool:
        """向特定节点发送 PROVISION_AGENT 指令，使其挂载该 Agent"""
        msg = {
            "type": "provision_agent",
            "data": {
                "agent_hasn_id": agent_hasn_id,
                "owner_id": owner_id,
                "config": config
            }
        }
        msg_str = json.dumps(msg)

        # 送入目标节点的 Push 队列
        await redis_client.rpush(f"{PUSH_PREFIX}:{node_id}", msg_str)
        # 防止过长堆积
        await redis_client.expire(f"{PUSH_PREFIX}:{node_id}", 3600)
        return True


node_scheduler = NodeSchedulerService()
