"""
通用 SocketIO 事件处理 + HASN 消息路由
"""
from backend.common.log import log
from backend.common.socketio.server import sio


async def task_notification(msg: str) -> None:
    """任务通知"""
    await sio.emit('task_notification', {'msg': msg})


# ================= HASN 消息路由 =================
# 对应设计文档: 02-通信协议.md §三 WebSocket 协议
# 字段已对齐 06-数据模型: hasn_messages 使用 from_id / conversation_id / SMALLINT 类型

@sio.on('hasn_message')
async def handle_hasn_message(sid, data: dict) -> None:
    """
    处理 HASN 上行消息 (客户端 → 服务器)
    对应设计文档 cmd=SEND: {to, content, metadata}
    """
    import json

    from backend.app.hasn_core.crud.crud_conversation import crud_hasn_conversation
    from backend.app.hasn_core.crud.crud_message import crud_hasn_message
    from backend.app.hasn_core.service.route_guard import route_guard
    from backend.app.hasn_core.service.ws_router import ws_router
    from backend.database.db import async_db_session
    from backend.database.redis import redis_client

    from_id = data.get('from_id')       # 发送者 hasn_id (h_xxx / a_xxx)
    to_id = data.get('to_id')           # 接收者 hasn_id
    content = data.get('content', '')
    content_type = data.get('content_type', 1)  # 1=text (SMALLINT)

    if not all([from_id, to_id, content]):
        await sio.emit('hasn_error', {
            'code': 2002,
            'message': '缺少必要字段: from_id, to_id, content',
        }, to=sid)
        return

    log.info(f"[HASN WS] 上行消息: {from_id} -> {to_id}")

    async with async_db_session() as db:
        # 1. 守门人权限校验
        # Agent 发消息时用 owner_id 做权限检查（好友关系在 human 之间）
        perm_from = from_id
        perm_to = to_id
        if from_id.startswith('a_'):
            from backend.app.hasn_core.crud.crud_agent import crud_hasn_agent
            agent = await crud_hasn_agent.get_by_id(db, from_id)
            if agent and agent.owner_id:
                perm_from = agent.owner_id
        if to_id.startswith('a_'):
            from backend.app.hasn_core.crud.crud_agent import crud_hasn_agent as ca
            target_agent = await ca.get_by_id(db, to_id)
            if target_agent and target_agent.owner_id:
                perm_to = target_agent.owner_id

        # 同一 owner 的 Agent 互发 → 直接放行
        if perm_from == perm_to:
            passed = True
        else:
            passed = await route_guard.check_permission(db, perm_from, perm_to)
        if not passed:
            log.warning(f"[HASN WS] 拦截: {from_id} -> {to_id}")
            await sio.emit('hasn_error', {
                'code': 2001,
                'message': '权限不足: 你和对方不是联系人',
            }, to=sid)
            return

        # 2. 获取/创建会话
        conv = await crud_hasn_conversation.get_or_create_direct(db, from_id, to_id)

        # 3. 判断发送者类型: h_ 开头=human(1), a_ 开头=agent(2)
        from_type = 1 if from_id.startswith('h_') else 2

        # 4. 消息持久化 (对齐 hasn_messages 表结构)
        msg_obj = await crud_hasn_message.create(
            db,
            conversation_id=conv.id,
            from_id=from_id,
            from_type=from_type,
            content_type=content_type,
            content=content,
        )

        # 5. 更新会话最后消息
        await crud_hasn_conversation.update_last_message(
            db, conv.id, content[:200])

        await db.commit()
        # flush 后才能拿到自增 id
        await db.refresh(msg_obj)

        # 6. 构造下行消息体 (对齐设计文档 cmd=MESSAGE)
        payload = {
            'cmd': 'MESSAGE',
            'message': {
                'id': msg_obj.id,
                'conversation_id': conv.id,
                'from_id': from_id,
                'from_type': from_type,
                'content_type': content_type,
                'content': content,
                'status': 1,
                'created_at': str(msg_obj.created_time) if msg_obj.created_time else None,
            }
        }

        # 7. 推送给接收方
        target_sid = await ws_router.get_client_sid(to_id)
        if target_sid:
            if target_sid.startswith("native:"):
                # 原生 WebSocket 客户端 → 写入 Redis 推送队列
                await redis_client.rpush(f"hasn:push:{to_id}", json.dumps(payload))
                log.info(f"[HASN WS] 在线推送(NativeWS): {from_id} -> {to_id}")
            else:
                # Socket.IO 客户端 → 直接 emit
                log.info(f"[HASN WS] 在线推送(SocketIO): {from_id} -> {to_id} (sid={target_sid})")
                await sio.emit('hasn_message_push', payload, to=target_sid)
        else:
            # 离线: 推入 Redis 离线队列 (对齐设计文档 Redis §3.1)
            offline_key = f"hasn:offline:{to_id}"
            await redis_client.rpush(offline_key, json.dumps(payload))
            await redis_client.expire(offline_key, 7 * 86400)  # 7天TTL
            log.info(f"[HASN WS] 离线入队: {from_id} -> {to_id}")

        # 8. ACK 回执发送方 (对齐设计文档 cmd=ACK)
        await sio.emit('hasn_message_ack', {
            'cmd': 'ACK',
            'msg_id': msg_obj.id,
            'conversation_id': conv.id,
            'status': 'sent',
        }, to=sid)


@sio.on('hasn_read')
async def handle_hasn_read(sid, data: dict) -> None:
    """
    已读回执 (对应设计文档 cmd=READ)
    """
    from backend.app.hasn_core.crud.crud_message import crud_hasn_message
    from backend.database.db import async_db_session

    conversation_id = data.get('conversation_id')
    last_msg_id = data.get('last_msg_id')

    if not conversation_id or not last_msg_id:
        return

    async with async_db_session() as db:
        await crud_hasn_message.mark_read(db, conversation_id, last_msg_id)
        await db.commit()


@sio.on('hasn_ping')
async def handle_hasn_ping(sid, data: dict) -> None:
    """心跳 (对应设计文档 cmd=PING/PONG)"""
    await sio.emit('hasn_pong', {'ts': data.get('ts')}, to=sid)
