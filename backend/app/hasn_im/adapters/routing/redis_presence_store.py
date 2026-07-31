"""IM 遗留 Redis 持久层键与脚本（routing 收口兼容层）。"""

import json

from backend.database.redis import redis_client


# Redis 键
NODE_CONN_KEY = 'hasn:node_conn'
NODE_GENERATION_KEY = 'hasn:node_generation'
ENTITY_NODE_KEY = 'hasn:entity_node'
NODE_ENTITIES_PREFIX = 'hasn:node_entities'
USER_NODES_PREFIX = 'hasn:user_nodes'
PUSH_PREFIX = 'hasn:push'
OFFLINE_PREFIX = 'hasn:offline'
OFFLINE_TTL = 7 * 86400  # 7 天
NODE_ALIVE_PREFIX = 'hasn:node_alive'
NODE_PRESENCE_TTL_SECS = 90
AGENT_READY_PREFIX = 'hasn:agent_ready'

# 兼容别名（遗留调用方过渡期引用）
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

# 单个实体的离线队列上限。离线帧全部是 durable_sync（PostgreSQL sync/history 可恢复），
# 因此超出上限时丢最旧的一条比让 Redis 无界增长安全；被裁剪的条目会显式告警。
OFFLINE_MAX_LENGTH = 1000

# 入队、裁剪与续期必须原子完成：先前实现是 RPUSH + EXPIRE 两条命令且每次写入都续期，
# 对「长期离线但持续收帧」的实体等于永不过期且无长度上限。
# KEYS[1]=离线队列 ARGV[1]=帧 ARGV[2]=TTL 秒 ARGV[3]=最大长度
# 返回被裁剪掉的条目数。
_ENQUEUE_OFFLINE_SCRIPT = """
local length = redis.call('RPUSH', KEYS[1], ARGV[1])
local max_length = tonumber(ARGV[3])
local trimmed = 0
if max_length > 0 and length > max_length then
    trimmed = length - max_length
    redis.call('LTRIM', KEYS[1], trimmed, -1)
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return trimmed
"""


def node_entities_key(node_id: str) -> str:
    return f'{NODE_ENTITIES_PREFIX}:{node_id}'


def offline_key(hasn_id: str) -> str:
    return f'{OFFLINE_PREFIX}:{hasn_id}'


def node_alive_key(node_id: str) -> str:
    return f'{NODE_ALIVE_PREFIX}:{node_id}'


def agent_ready_key(agent_id: str) -> str:
    return f'{AGENT_READY_PREFIX}:{agent_id}'


def get_node_type_and_capacity(raw: str | bytes | None) -> dict[str, str | int] | None:
    """解析 node_conn 映射中的 JSON，返回 node_type/capacity 字典。"""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    node_type = data.get('node_type')
    capacity = data.get('capacity')
    return {
        'node_type': node_type if isinstance(node_type, str) else '',
        'capacity': int(capacity) if isinstance(capacity, int | str) else 1,
    }


async def set_node_presence(
    node_id: str,
    node_type: str,
    capacity: int,
    connection_id: str,
    *,
    connected_at: str,
    ttl_secs: int = NODE_PRESENCE_TTL_SECS,
) -> None:
    """写入节点元数据和存活心跳（与旧逻辑等价）。"""
    payload = json.dumps({
        'node_type': node_type,
        'capacity': capacity,
        'connected_at': connected_at,
        'connection_id': connection_id,
    })
    await redis_client.hset(NODE_GENERATION_KEY, node_id, connection_id)
    await redis_client.hset(NODE_CONN_KEY, node_id, payload)
    await redis_client.set(node_alive_key(node_id), '1', ex=ttl_secs)


async def refresh_presence_if_current(node_id: str, connection_id: str, ttl_secs: int = NODE_PRESENCE_TTL_SECS) -> bool:
    """仅当前代际可延长节点 TTL。"""
    refreshed = await redis_client.eval(
        _REFRESH_PRESENCE_IF_CURRENT_SCRIPT,
        2,
        NODE_GENERATION_KEY,
        node_alive_key(node_id),
        node_id,
        connection_id,
        ttl_secs,
    )
    return bool(refreshed)


async def unregister_node_if_current(node_id: str, connection_id: str) -> bool:
    """仅当前代际才清理 presence 与实体映射。"""
    removed = await redis_client.eval(
        _UNREGISTER_NODE_IF_CURRENT_SCRIPT,
        5,
        NODE_GENERATION_KEY,
        NODE_CONN_KEY,
        node_entities_key(node_id),
        ENTITY_NODE_KEY,
        node_alive_key(node_id),
        node_id,
        connection_id,
        USER_NODES_PREFIX,
    )
    return bool(removed)


def decode_offline_message(raw: str | bytes) -> dict | None:
    """解析离线队列帧；畸形或非对象 JSON 不参与补推。"""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


async def ack_offline_prefix(key: str, messages: list[str]) -> None:
    """按领取时前缀一致性确认离线消息。"""
    if not messages:
        return
    await redis_client.eval(
        _ACK_OFFLINE_PREFIX_SCRIPT,
        1,
        key,
        *messages,
    )


__all__ = [
    'AGENT_CLIENT_KEY',
    'AGENT_NODE_KEY',
    'AGENT_READY_PREFIX',
    'CLIENT_CONN_KEY',
    'OFFLINE_PREFIX',
    'OFFLINE_TTL',
    'NODE_ALIVE_PREFIX',
    'NODE_CONN_KEY',
    'NODE_ENTITIES_PREFIX',
    'NODE_GENERATION_KEY',
    'NODE_PRESENCE_TTL_SECS',
    'PUSH_PREFIX',
    'USER_CLIENTS_PREFIX',
    'USER_NODES_PREFIX',
    'ENTITY_NODE_KEY',
    '_ACK_OFFLINE_PREFIX_SCRIPT',
    '_REFRESH_PRESENCE_IF_CURRENT_SCRIPT',
    '_UNREGISTER_NODE_IF_CURRENT_SCRIPT',
    'ack_offline_prefix',
    'decode_offline_message',
    'get_node_type_and_capacity',
    'node_alive_key',
    'node_entities_key',
    'offline_key',
    'agent_ready_key',
    'refresh_presence_if_current',
    'set_node_presence',
    'unregister_node_if_current',
]
