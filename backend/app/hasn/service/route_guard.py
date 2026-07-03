"""
HASN 守门人核心服务 — 消息权限拦截
对应设计文档: 02-通信协议.md §4.1 / 03-权限与隐私.md
"""
import json

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.common.log import log
from backend.database.redis import redis_client


class RouteGuardService:

    CACHE_PREFIX = "hasn:rel"
    CACHE_TTL = 600  # 10分钟，对齐设计文档 Redis §3.3

    @classmethod
    async def check_permission(
        cls,
        db: AsyncSession,
        sender_id: str,
        receiver_id: str,
        relation_type: str = 'social',
    ) -> bool:
        """
        守门人核心校验: 检查发件人是否有权限给收件人发消息。

        三维权限检查逻辑 (对齐设计文档):
          1. 查缓存 → 命中则直接判断
          2. 查 DB (双向: A→B 或 B→A)
          3. trust_level=0 (blocked) → 拦截
          4. 无关系 → 拦截陌生人 (MVP 默认策略)
          5. 有关系且 trust_level>=1 → 放行
        """
        # 1. 查 Redis 缓存
        cache_key = f"{cls.CACHE_PREFIX}:{sender_id}:{receiver_id}"
        cached = await redis_client.hget(cache_key, relation_type)
        if cached:
            try:
                data = json.loads(cached if isinstance(cached, str) else cached.decode())
                trust = data.get('trust_level', 0)
                status = data.get('status', '')
                if status == 'blocked' or trust == 0:
                    log.warning(f"[HASN Guard] 缓存命中-拦截: {sender_id} -> {receiver_id} (blocked)")
                    return False
                if status == 'connected' and trust >= 1:
                    return True
            except Exception:
                pass

        # 2. 查 DB — 双向查找
        relation = (await db.execute(
            select(HasnContacts)
            .where(HasnContacts.relation_type == relation_type)
            .where(
                or_(
                    and_(HasnContacts.owner_id == sender_id, HasnContacts.peer_id == receiver_id),
                    and_(HasnContacts.owner_id == receiver_id, HasnContacts.peer_id == sender_id),
                )
            )
        )).scalars().first()

        if not relation:
            log.warning(f"[HASN Guard] 无关系-拦截: {sender_id} -> {receiver_id}")
            return False

        # 3. 写入缓存
        cache_data = json.dumps({
            'trust_level': relation.trust_level,
            'status': relation.status,
        })
        await redis_client.hset(cache_key, relation_type, cache_data)
        await redis_client.expire(cache_key, cls.CACHE_TTL)

        # 反向也缓存
        reverse_key = f"{cls.CACHE_PREFIX}:{receiver_id}:{sender_id}"
        await redis_client.hset(reverse_key, relation_type, cache_data)
        await redis_client.expire(reverse_key, cls.CACHE_TTL)

        # 4. 判断
        if relation.status == 'blocked' or relation.trust_level == 0:
            log.warning(f"[HASN Guard] DB命中-拦截: {sender_id} -> {receiver_id} (blocked/level=0)")
            return False

        if relation.status != 'connected':
            log.warning(f"[HASN Guard] DB命中-拦截: {sender_id} -> {receiver_id} (status={relation.status})")
            return False

        return True

    @classmethod
    async def invalidate_cache(cls, id_a: str, id_b: str) -> None:
        """权限变更时主动失效缓存"""
        await redis_client.delete(f"{cls.CACHE_PREFIX}:{id_a}:{id_b}")
        await redis_client.delete(f"{cls.CACHE_PREFIX}:{id_b}:{id_a}")


route_guard = RouteGuardService()
