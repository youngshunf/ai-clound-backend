"""HASN 统一节点 WebSocket 端点.

v2.1 简化认证：Bearer Token / OwnerKey + X-Node-Id
- 连接时自动 upsert hasn_nodes + 自动绑定第一个 Owner
- JWT 只在握手时验证一次，连接生命周期由 Owner Binding 租约管理

现行控制平面：
- add_owner / remove_owner / renew_owner / list_owners
- add_agent / remove_agent
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.security.utils import get_authorization_scheme_param

from backend.app.hasn.service import geoip_service
from backend.app.hasn.service.hasn_auth import authenticate_ws_connection
from backend.app.hasn.service.hasn_nodes_service import hasn_nodes_service
from backend.app.hasn_im.application.ws_node_runtime import ws_node_runtime
from backend.app.hasn_im.protocol.frame import (
    FrameDecodeError,
    build_error_frame,
    parse_inbound,
)
from backend.app.hasn_im.protocol import version_gate
from backend.app.hasn_im.protocol.frame import build_frame as _frame
from backend.app.hasn_im.protocol.handler_registry import (
    HANDLER_SPECS,
    build_handler_args,
    resolve_handler_spec,
)
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

log = logging.getLogger(__name__)

router = APIRouter()


__all__ = ['router', 'hasn_node_websocket']

# 协议帧编解码/校验已提为无 DB 纯模块 `hasn_im.protocol.frame`（R1-09 协议层纯化）；
# 本文件经 import 消费（`_frame`/`_response` 为其 build_frame/build_response 的别名）。
# 发送超时（backpressure 有界刷新窗口）与入站 frame size 上限已抽为配置项（R1-09），
# 在调用点读 `settings.*`（模块 import 期不固化，便于测试/演练覆盖）。


def _is_connection_closed(exc: Exception) -> bool:
    """判断异常是否为「连接已被关闭后又尝试发送」的良性竞态。

    登出场景：daemon 调 logout_device → 云端 disconnect_node 主动 `ws.close(4002)`
    关闭连接；但 `_recv_loop` 可能恰好还在处理该连接上最后一帧（如 hasn.ping），
    随后的 `_send_json` 会抛 Starlette 的
    `RuntimeError('Cannot call "send" once a close message has been sent.')`。
    这是**预期的关闭竞态**、连接本就要断，属良性断开——不应记 ERROR，更不该在
    except 里再 `_send_error` 二次往已关连接写、造成第二条 ERROR。
    """
    if isinstance(exc, WebSocketDisconnect):
        return True
    if isinstance(exc, RuntimeError):
        text = str(exc)
        return (
            'once a close message has been sent' in text
            or 'WebSocket is not connected' in text
            or 'unexpected ASGI message' in text
        )
    return False


def _client_ip(websocket: WebSocket) -> str | None:
    """从 WS 取真实客户端 IP：优先反代头，回退 socket peer。"""
    xff = websocket.headers.get('x-forwarded-for')
    if xff:
        # 形如 "client, proxy1, proxy2"，取第一跳
        first = xff.split(',')[0].strip()
        if first:
            return first
    real = websocket.headers.get('x-real-ip')
    if real and real.strip():
        return real.strip()
    client = websocket.client
    return client.host if client else None


async def _backfill_node_metadata(websocket: WebSocket, node_id: str) -> None:
    """握手后回填设备元数据：客户端 IP + ip2region 归属地 + OS/app 版本。

    全程非致命：任何异常只 warn，不影响连接与消息收发（零 Mock，归属地缺失留空）。
    """
    try:
        client_ip = _client_ip(websocket)
        ip_location = geoip_service.lookup_location(client_ip)
        device_platform = websocket.headers.get('X-Node-Platform')
        app_version = websocket.headers.get('X-App-Version')
        if not (client_ip or device_platform or app_version):
            return
        async with async_db_session() as db:
            await hasn_nodes_service.update_runtime_metadata(
                db=db,
                node_id=node_id,
                ip_address=client_ip,
                ip_location=ip_location,
                device_platform=device_platform,
                app_version=app_version,
            )
            await db.commit()
    except Exception as e:
        log.warning(f'[HASN] 节点元数据回填失败 (非致命): {e}')


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    """限时发送控制帧，超时按连接失败处理并进入外层清理。

    超时窗口 = `settings.HASN_WS_SEND_TIMEOUT_SECS`（backpressure 上界，R1-09 配置化）。
    """
    await asyncio.wait_for(websocket.send_json(payload), timeout=settings.HASN_WS_SEND_TIMEOUT_SECS)


async def _send_offline_messages(websocket: WebSocket, entity_ids: list[str]) -> None:
    """成功交给 WebSocket transport 后才确认离线队列前缀。"""
    messages, claims = await ws_node_runtime.claim_offline_messages(entity_ids)
    if not messages:
        return
    await _send_json(
        websocket,
        _frame('hasn.node.offline_messages', {'messages': messages}),
    )
    if claims:
        await ws_node_runtime.ack_offline_messages(claims)


@router.websocket('/ws/node')
async def hasn_node_websocket(  # noqa: C901
    websocket: WebSocket,
) -> None:
    """HASN 统一节点 WebSocket 端点（所有节点类型共用）

    认证方式（v2.1）：
      - Authorization: Bearer <jwt_access_token>  — 桌面端/Web端
      - Authorization: OwnerKey hasn_ok_xxx        — SDK/第三方接入
    节点标识：
      - X-Node-Id: n_xxx                           — 客户端设备指纹派生
      - X-Node-Name: macOS 15.3 (aarch64)          — 可选，设备描述
    """

    # 1. 认证：接受 Bearer / OwnerKey + X-Node-Id
    try:
        authorization = websocket.headers.get('Authorization')
        if not authorization:
            await websocket.close(code=4001, reason='缺少认证头 Authorization')
            return
        scheme, credentials = get_authorization_scheme_param(authorization)
        if scheme.lower() not in ('bearer', 'ownerkey'):
            await websocket.close(code=4001, reason=f'不支持的认证方式: {scheme}，请使用 Bearer 或 OwnerKey')
            return

        # 读取 node_id（客户端设备指纹派生）。Core/05 §5.1: node_id MUST 由设备指纹
        # 派生，服务端禁止用进程内地址凭空伪造——缺失 X-Node-Id（且无显式 node_id query）
        # 直接拒连，避免伪造的临时 node_id 被当真身份注册节点 / 落入 binding 参与路由。
        node_id = websocket.headers.get('X-Node-Id') or websocket.query_params.get('node_id')
        if not node_id:
            await websocket.close(
                code=4001,
                reason='缺少 X-Node-Id 节点标识（Core/05 §5.1：node_id 必须由设备指纹派生）',
            )
            return
        node_name = websocket.headers.get('X-Node-Name')

        # R2-10 掉队客户端闸（§8.3-2）：旧事件切换后，低于阈值的 daemon 必须被闸住、不能继续收发
        # 旧事件。在 accept 之前据 X-App-Version 头判定；低于 settings.HASN_WS_MIN_CLIENT_VERSION
        # 即以 4003(UPGRADE_REQUIRED) 拒连——错误码/reason 供 D3 识别并出「需要升级」引导、停重连风暴。
        # 阈值空（默认）= 闸关，不影响本地测试（对齐「本地测试通过最后才生产部署」）。
        client_app_version = websocket.headers.get('X-App-Version')
        if version_gate.is_below_minimum(client_app_version, settings.HASN_WS_MIN_CLIENT_VERSION):
            log.info(
                '[HASN] WS 版本闸拒连: node=%s client=%s min=%s',
                node_id,
                client_app_version,
                settings.HASN_WS_MIN_CLIENT_VERSION,
            )
            await websocket.close(
                code=version_gate.UPGRADE_REQUIRED_CLOSE_CODE,
                reason=version_gate.build_upgrade_required_reason(
                    settings.HASN_WS_MIN_CLIENT_VERSION, client_app_version
                ),
            )
            return

        auth = await authenticate_ws_connection(scheme, credentials, node_id, node_name)
    except Exception as e:
        log.warning(f'[HASN] WS 认证失败: {e}')
        await websocket.close(code=4001, reason=str(e))
        return

    node_id = auth['node_id']
    node_type = auth.get('node_type', 'desktop')
    capacity = auth.get('capacity', 1)
    owner_hasn_id = auth.get('owner_hasn_id')

    await websocket.accept()

    # 2. 注册节点在线
    connection_id = await ws_node_runtime.register_node(
        node_id=node_id,
        node_type=node_type,
        ws=websocket,
        capacity=capacity,
    )

    # 2.5 回填设备元数据（IP/归属地/OS/版本），用于设备管理页（非致命）
    await _backfill_node_metadata(websocket, node_id)

    # 3. 自动绑定第一个 Owner（建连时一步完成）
    auto_bound_owner = False
    if owner_hasn_id:
        try:
            async with async_db_session() as db:
                bind_result = await ws_node_runtime.add_owner(
                    node_id=node_id,
                    owner_id=owner_hasn_id,
                    owner_proof={
                        'type': auth['auth_profile'],
                        'credential': '__ws_auto_bind__',  # 内部哨兵值，已过认证
                    },
                    db=db,
                    skip_proof_verify=True,  # 已在 authenticate_ws_connection 中验证
                )
                await db.commit()
            auto_bound_owner = bind_result.get('accepted', False)
        except Exception as e:
            log.warning(f'[HASN] 自动绑定 Owner 失败 (非致命): {e}')

    try:
        # 4. 发送 hasn.connected（新增 owner_id 字段）
        connected_params = {
            'node_id': node_id,
            'node_type': node_type,
            'capacity': capacity,
            'connection_id': connection_id,
            'server_time': timezone.now().isoformat(),
            'supported_versions': ['hasn/0.2'],
            'extensions': [
                'capability',
                'discovery',
                'trade',
                'screening',
                'health',
                'constellation',
                'bridge',
            ],
        }
        if owner_hasn_id and auto_bound_owner:
            connected_params['owner_id'] = owner_hasn_id
            connected_params['owner_count'] = 1

        # 配置/目录 revision 握手对账（doc02-07）：daemon 据此追平离线期间错过的
        # builtin_catalog / common_skills / platform_config 变更（重连即对账，
        # 无需重新登录）。读 Redis 缓存，cheap；失败非致命（不影响连接建立）。
        try:
            from backend.app.hasn.service.sync_invalidate_service import get_all_revisions

            async with async_db_session() as db:
                connected_params['revisions'] = await get_all_revisions(db)
        except Exception as e:  # revision 握手非致命
            log.warning(f'[HASN] 计算 sync revisions 失败 (非致命): {e}')

        await _send_json(websocket, _frame('hasn.connected', connected_params))

        # 5. 自动推送离线消息（Owner 已绑定）
        if owner_hasn_id and auto_bound_owner:
            await _send_offline_messages(websocket, [owner_hasn_id])

        # hasn.connected 必须是 daemon 收到的首帧；到这里才开放业务投递并排空持久队列。
        if not await ws_node_runtime.mark_node_ready(node_id=node_id, connection_id=connection_id):
            await websocket.close(code=1000, reason='superseded connection')
            return

        # 6. 双向收发循环（传入已绑定的 owner）
        initial_entities = {owner_hasn_id} if (owner_hasn_id and auto_bound_owner) else set()
        await _recv_loop(websocket, node_id, connection_id, initial_entities)

    except WebSocketDisconnect:
        log.info(f'节点断开: {node_id} (type={node_type})')
    except Exception as e:
        # 连接关闭竞态（如登出时服务端主动 close 后仍有一帧在途）属良性断开，
        # 记 info 即可——真正的服务端故障才记 ERROR（warn/error 分级铁律）。
        if _is_connection_closed(e):
            log.info(f'节点连接关闭竞态（良性）: {node_id} (type={node_type})')
        else:
            log.error(f'WebSocket 异常: {node_id} - {e}')
    finally:
        # 7. 清理：注销节点 + 清理所有实体（best-effort，绝不让清理异常冒泡出 ASGI handler）
        try:
            await ws_node_runtime.unregister_node(node_id=node_id, connection_id=connection_id)
        except Exception as e:
            log.warning(f'[HASN] 节点清理失败 (非致命): {node_id} - {e}')


async def _recv_loop(  # noqa: C901
    websocket: WebSocket,
    node_id: str,
    connection_id: str,
    initial_entities: set[str] | None = None,
) -> None:
    """处理节点上行消息"""
    # 记录当前 node 的活跃主体（bound owners + online agents），用于 from_id 校验
    active_entities: set[str] = set(initial_entities or ())

    while True:
        raw = await websocket.receive_text()
        try:
            # frame size 硬闸随配置生效（默认 0=不限）：超限帧显式回 2005、continue 不断连。
            method, params, req_id = parse_inbound(raw, max_bytes=settings.HASN_WS_MAX_INBOUND_FRAME_BYTES)
        except FrameDecodeError as exc:
            await _send_error(websocket, exc.code, exc.message)
            continue

        try:
            # table-driven 分派（R1-09 协议层纯化）：方法 → handler 的参数形状由纯模块
            # handler_registry.HANDLER_SPECS 声明，本地 _HANDLERS 绑定实现。未登记方法显式
            # 回 9001（不静默丢弃）；仅 may_close_loop 方法（hasn.ping superseded）返回真值
            # 时关闭收发循环。
            spec = resolve_handler_spec(method)
            if spec is None:
                await _send_error(websocket, 9001, f'未知方法: {method}')
                continue
            args = build_handler_args(
                spec,
                websocket=websocket,
                node_id=node_id,
                connection_id=connection_id,
                params=params,
                active_entities=active_entities,
                req_id=req_id,
            )
            result = await _HANDLERS[method](*args)
            if spec.may_close_loop and result is True:
                return

        except Exception as e:
            # 连接已被关闭（登出竞态等）→ 良性断开：直接结束收发循环，
            # 不记 ERROR，也不再 `_send_error`（会往已关连接二次写、产生第二条 ERROR）。
            if _is_connection_closed(e):
                log.info(f'节点连接已关闭，结束收发循环: {node_id} (method={method})')
                return
            log.error(f'处理命令 {method} 异常: {e}', exc_info=True)
            # 回送错误帧本身也可能因连接已关而抛——用 close-race 判据吞掉，避免二次 fault。
            try:
                await _send_error(websocket, 9001, '服务器内部错误')
            except Exception as send_err:
                if _is_connection_closed(send_err):
                    return
                raise


# ─── 命令处理器 ───


async def _handle_add_owner(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    async with async_db_session() as db:
        result = await ws_node_runtime.add_owner(
            node_id=node_id,
            owner_id=params.get('owner_id', ''),
            owner_proof=params.get('owner_proof', {}),
            db=db,
        )
        await db.commit()
    owner_id = result.get('owner_id', '')
    if result.get('accepted') and owner_id:
        active_entities.add(owner_id)
        await _send_offline_messages(websocket, [owner_id])
    await _send_json(websocket, _frame('hasn.node.add_owner_ack', result))


async def _handle_remove_owner(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    owner_id = params.get('owner_id', '')
    async with async_db_session() as db:
        result = await ws_node_runtime.remove_owner(node_id=node_id, owner_id=owner_id, db=db)
        await db.commit()
    active_entities.discard(owner_id)
    await _send_json(websocket, _frame('hasn.node.remove_owner_ack', result))


async def _handle_renew_owner(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    async with async_db_session() as db:
        result = await ws_node_runtime.renew_owner(
            node_id=node_id,
            owner_id=params.get('owner_id', ''),
            owner_proof=params.get('owner_proof', {}),
            db=db,
        )
        await db.commit()
    await _send_json(websocket, _frame('hasn.node.renew_owner_ack', result))


async def _handle_list_owners(websocket: WebSocket, node_id: str) -> None:
    async with async_db_session() as db:
        result = await ws_node_runtime.list_owners(node_id=node_id, db=db)
    await _send_json(websocket, _frame('hasn.node.list_owners_ack', result))


async def _push_contacts_presence_invalidation(
    agent_id: str, agent_owner_id: str, transition: str
) -> None:
    """分身在线态翻转（上线/下线）→ 向「能在通讯录里看到该分身」的 owner 们 push 联系人失效。

    受众 = 直接把该分身加为好友的 owner（``peer_id=agent_id`` 且 ``peer_type='agent'``）
    ∪ 加了该分身主人为好友、在 owned_agents 里看到它的 owner（``peer_id=agent_owner_id`` 且
    ``peer_type='human'``），限 ``status='connected'``。逐 owner ``bump_owner(KIND_CONTACTS)``——
    daemon 收到即回源刷新联系人镜像（云端重算实时 presence）+ nudge webui 重拉，好友分身的在线
    圆点即时翻绿/变灰。治「跨主人/跨设备看好友分身在线态长时间不刷新」。

    best-effort：整体吞错只 warn；presence 是「显示口径」不是「投递口径」，push 失败靠联系人
    列表下次重刷 / local_first 后台刷新兜底，绝不影响消息路由。
    """
    if not agent_id:
        return
    try:
        from sqlalchemy import text

        from backend.app.hasn.service import sync_invalidate_service

        async with async_db_session() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT DISTINCT owner_id
                        FROM hasn_contacts
                        WHERE status = 'connected'
                          AND (
                            (peer_id = :agent_id AND peer_type = 'agent')
                            OR (peer_id = :agent_owner_id AND peer_type = 'human')
                          )
                        """
                    ),
                    {'agent_id': agent_id, 'agent_owner_id': agent_owner_id or ''},
                )
            ).all()
            audience = {row[0] for row in rows if row[0]}
            for owner_id in audience:
                try:
                    await sync_invalidate_service.bump_owner(
                        sync_invalidate_service.KIND_CONTACTS, db, owner_id
                    )
                except Exception as exc:  # 单个 owner push 失败不影响其它
                    log.warning(
                        'contacts presence invalidate failed owner=%s agent=%s: %s',
                        owner_id,
                        agent_id,
                        exc,
                    )
        if audience:
            log.info(
                'contacts presence invalidate agent=%s transition=%s owners=%d',
                agent_id,
                transition,
                len(audience),
            )
    except Exception as exc:  # 拉受众失败——best-effort，绝不影响节点心跳路径
        log.warning('contacts presence invalidation task failed agent=%s: %s', agent_id, exc)


async def _handle_add_agent(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    async with async_db_session() as db:
        result = await ws_node_runtime.add_agent_presence(
            node_id=node_id,
            agent_id=params.get('agent_id', ''),
            owner_id=params.get('owner_id', ''),
            db=db,
        )
        await db.commit()
        # 折叠心跳：重认领帧若携带 online_status，则顺手把运行时健康写入持久列
        # （取代原 per-agent HTTP /heartbeat——节点级 WS 已鉴权，无需 Agent JWT；
        # 见 daemon spawn_binding_heartbeat_worker）。健康写入失败绝不拖垮路由
        # 注册这条关键路径，故单独 try/except。
        agent_id = params.get('agent_id', '')
        if result.get('accepted') and agent_id and params.get('online_status'):
            try:
                from backend.app.hasn.schema.hasn_agents import AgentHeartbeatRequest
                from backend.app.hasn.service.hasn_agents_service import (
                    agent_profile_service,
                )

                await agent_profile_service.update_heartbeat(
                    db,
                    agent_id,
                    AgentHeartbeatRequest(
                        node_id=node_id,
                        online_status=str(params.get('online_status')),
                        health_status=params.get('health_status'),
                        last_heartbeat_at=int(params.get('last_heartbeat_at') or 0),
                    ),
                    user_id=None,
                )
            except Exception as exc:
                log.warning('fold-heartbeat persist failed for agent %s: %s', agent_id, exc)
            # 在线语义收紧：按心跳携带的 online_status+health_status 写/删 agent 就绪键
            # （online+ok 才写；degraded/offline 删）。就绪键是对外「在线」判定的第三维，
            # 不影响路由；失败不拖垮路由注册，单独 try/except。
            try:
                transition = await ws_node_runtime.set_agent_readiness(
                    agent_id,
                    str(params.get('online_status')),
                    params.get('health_status'),
                )
                # 就绪键在线态**真翻转**（上线/下线）时，异步向「能在通讯录里看到该分身」的
                # owner 们 push 联系人失效——好友分身的在线圆点即时翻绿/变灰（presence→contacts
                # WSPUSH）。fire-and-forget：绝不阻塞节点心跳这条热路径；失败不拖垮路由注册。
                if transition is not None:
                    asyncio.create_task(
                        _push_contacts_presence_invalidation(
                            agent_id, params.get('owner_id', ''), transition
                        )
                    )
            except Exception as exc:
                log.warning('set agent readiness failed for agent %s: %s', agent_id, exc)
    if result.get('accepted') and agent_id:
        active_entities.add(agent_id)
        await _send_offline_messages(websocket, [agent_id])
    await _send_json(websocket, _frame('hasn.node.add_agent_ack', result))


async def _handle_remove_agent(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    agent_id = params.get('agent_id', '')
    result = await ws_node_runtime.remove_agent_presence(node_id=node_id, agent_id=agent_id)
    active_entities.discard(agent_id)
    await _send_json(websocket, _frame('hasn.node.remove_agent_ack', result))


async def _handle_agent_register(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
    req_id: str | None,
) -> None:
    """处理 hasn.agent.register（通过 WS 创建新 Agent）"""
    from backend.app.hasn.service.hasn_auth import register_hasn_agent

    # 确定 owner_id：显式指定 or 从已上报的 Human 推断
    owner_id = params.get('owner_id', '')
    if not owner_id:
        humans = [eid for eid in active_entities if eid.startswith('h_')]
        if len(humans) == 1:
            owner_id = humans[0]
        elif len(humans) == 0:
            await _send_error(websocket, 8007, '未上报任何 Human 实体，无法确定 owner_id')
            return
        else:
            await _send_error(websocket, 8008, '多个 Human 实体在线，必须显式指定 owner_id')
            return

    agent_name = params.get('agent_name', '')
    display_name = params.get('display_name', '')

    if not agent_name or not display_name:
        await _send_error(websocket, 2002, '缺少必填参数 agent_name 或 display_name')
        return

    async with async_db_session() as db:
        try:
            result = await register_hasn_agent(
                db=db,
                owner_hasn_id=owner_id,
                agent_name=agent_name,
                display_name=display_name,
                agent_type=params.get('agent_type', 'local'),
                role=params.get('role', 'specialist'),
                description=params.get('description'),
                capabilities=params.get('capabilities'),
            )
            await db.commit()
        except Exception as e:
            await _send_error(websocket, 9001, f'Agent 注册失败: {e}')
            return

    ack_params = {
        'hasn_id': result['agent'].hasn_id,
        'star_id': result['agent'].star_id,
        'already_exists': result['already_exists'],
    }
    if result.get('agent_key'):
        ack_params['agent_key'] = result['agent_key']

    await _send_json(websocket, _frame('hasn.agent.register_ack', ack_params))


async def _handle_agent_deregister(
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
    req_id: str | None,
) -> None:
    """处理 hasn.agent.deregister（永久删除 Agent）"""
    hasn_id = params.get('hasn_id', '')
    if not hasn_id:
        await _send_error(websocket, 2002, '缺少 hasn_id')
        return

    # 先下线
    await ws_node_runtime.unregister_entity_route(node_id=node_id, hasn_id=hasn_id)
    active_entities.discard(hasn_id)

    # DB 标记删除
    from sqlalchemy import update

    from backend.app.hasn.model.hasn_agents import HasnAgents

    async with async_db_session() as db:
        await db.execute(update(HasnAgents).where(HasnAgents.hasn_id == hasn_id).values(status='deleted'))
        await db.commit()

    await _send_json(
        websocket,
        _frame(
            'hasn.agent.deregister_ack',
            {
                'hasn_id': hasn_id,
                'success': True,
            },
        ),
    )


async def _handle_send(  # noqa: C901
    websocket: WebSocket,
    node_id: str,
    params: dict,
    active_entities: set[str],
) -> None:
    """处理 hasn.message.send"""
    from_id = params.get('from_id', '')
    to_target = params.get('to', '')
    content = params.get('content', {})
    local_id = params.get('local_id')
    msg_type = params.get('type', 'message')
    # daemon 把群 @提及（mentions/mention_all）等随帧元数据放在 context 里，必须整体透传给
    # route_message——群分支据此持久化 mentions 并随 envelope 扇出，是 mention_only 派发闸
    # 的权威数据载体。此前这里只摘了 reply_to、context 本体被丢弃，导致跨节点分身在
    # mention_only 群里永远收不到 @（发送侧 daemon 本地唤醒路径掩盖了此断点，doc10 GS0 修复）。
    context = params.get('context') if isinstance(params.get('context'), dict) else None
    reply_to_id = (context or {}).get('reply_to')

    # 校验 from_id 合法性：必须是已上报的实体
    if from_id not in active_entities:
        await _send_error(websocket, 8006, f'未授权的 from_id: {from_id}（不在已上报实体中）')
        return

    if not to_target:
        await _send_error(websocket, 2002, '缺少目标地址 (to)')
        return

    # 处理 content 格式
    content_type = 1  # 默认文本
    if isinstance(content, str):
        content = {'text': content}
    elif isinstance(content, dict):
        ct = content.get('content_type', 'text')
        # 完整映射 content_type 字符串 -> 整数码。此前只认 text/image，
        # 卡片(card)/文件/语音/json 全部落到默认 1(文本)，导致跨 owner 分享的
        # 卡片在 wire 上被当文本投递，收件端存成文本、渲染空气泡。
        if isinstance(ct, int):
            content_type = ct
        else:
            content_type = {
                'text': 1,
                'image': 2,
                'file': 3,
                'voice': 4,
                'card': 5,
                'json': 6,
            }.get(str(ct), 1)
        # 提取 body
        if 'body' in content:
            content = content['body']

    # 路由消息
    async with async_db_session() as db:
        result = await ws_node_runtime.route_message(
            db=db,
            from_id=from_id,
            to_target=to_target,
            content=content,
            content_type=content_type,
            msg_type=msg_type,
            local_id=local_id,
            reply_to_id=reply_to_id,
            context=context,
            # doc02 §3.8：消息级设备 meta 由 Server 从认证上下文自动填——即本 WS 连接
            # 已认证的节点 ID（不可伪造，发送方不能自报设备）。
            origin_node_id=node_id,
        )

    if result.get('error'):
        await _send_error(websocket, result.get('code', 9001), result.get('message', ''))
        return

    # 发送 ACK
    await _send_json(
        websocket,
        _frame(
            'hasn.message.ack',
            {
                'msg_id': result['msg_id'],
                'conversation_id': result['conversation_id'],
                'local_id': local_id,
                'status': 'sent',
                'timestamp': timezone.now().isoformat(),
            },
        ),
    )

    # 出站重发命中幂等去重：该消息首发时已落库并投递、且已给发送方其它端做过多端同步。
    # 这里只需补回 ACK（让发送端把出站队列里这条标记已达、停止重发），**不可**再次多端同步，
    # 否则发送方其它设备会收到重复的 self_sent 回显。
    if result.get('deduped'):
        return

    # 多端同步：推送给发送方的其他节点（跨 worker 经投递总线，跳过本发送节点）
    # 找出 from_id 对应的 owner（如果是 Agent，找 owner 的其他节点）
    sync_target = from_id if from_id.startswith('h_') else None
    if not sync_target:
        # Agent 发送，找 owner 的节点
        for eid in active_entities:
            if eid.startswith('h_'):
                sync_target = eid
                break

    if sync_target:
        sender_payload = _frame(
            'hasn.message.received',
            {
                'to_id': sync_target,
                'message': {
                    'id': result['msg_id'],
                    'conversation_id': result['conversation_id'],
                    'from_id': from_id,
                    'from_type': 1 if from_id.startswith('h_') else 2,
                    'to_id': to_target,
                    'to_type': 1 if to_target.startswith('h_') else 2,
                    'content_type': content_type,
                    'content': content,
                    'msg_type': msg_type,
                    'status': 1,
                    'local_id': local_id,
                    'self_sent': True,
                    'created_time': timezone.now().isoformat(),
                },
            },
        )
        await ws_node_runtime.push_self_sync(owner_id=sync_target, payload=sender_payload, exclude_node=node_id)


async def _handle_read(params: dict, active_entities: set[str]) -> None:
    """处理 hasn.message.read"""
    conversation_id = params.get('conversation_id', '')
    last_msg_id = params.get('last_msg_id', 0)

    if not conversation_id:
        return

    # 用已上报实体中的第一个 Human 作为 reader
    reader = next((eid for eid in active_entities if eid.startswith('h_')), '')
    if not reader:
        return

    async with async_db_session() as db:
        await ws_node_runtime.mark_read(db, reader=reader, conversation_id=conversation_id, last_msg_id=last_msg_id)


async def _handle_typing(params: dict, active_entities: set[str]) -> None:
    """处理 hasn.typing"""
    to_id = params.get('to_id', '')
    conversation_id = params.get('conversation_id', '')

    if not to_id:
        return

    # 用已上报的第一个 Human 作为 from_id
    from_id = params.get('from_id', '')
    if not from_id:
        from_id = next((eid for eid in active_entities if eid.startswith('h_')), '')

    typing_payload = _frame(
        'hasn.typing',
        {
            'from_id': from_id,
            'conversation_id': conversation_id,
        },
    )
    await ws_node_runtime.push_message_to(to_id=to_id, payload=typing_payload)


async def _handle_ping(
    websocket: WebSocket,
    node_id: str,
    connection_id: str,
    params: dict,
) -> bool:
    """处理 hasn.ping（P3 应用层心跳续期节点存活 TTL，根治僵尸 presence）。

    返回 True 表示本连接已被更新连接顶替（superseded）、已 `ws.close`，收发循环应结束；
    正常续期回 hasn.pong 后返回 False（继续收发）。返回值仅对 may_close_loop 方法有意义。
    """
    if not await ws_node_runtime.refresh_node_presence(node_id=node_id, connection_id=connection_id):
        await websocket.close(code=1000, reason='superseded connection')
        return True
    await _send_json(websocket, _frame('hasn.pong', {'ts': params.get('ts')}))
    return False


async def _send_error(websocket: WebSocket, code: int, message: str) -> None:
    """发送错误帧"""
    await _send_json(websocket, build_error_frame(code, message))


# 方法 → 本地 handler 实现绑定表（table-driven 分派消费；参数形状由 handler_registry 的
# HANDLER_SPECS 声明，本表只绑定「method → _handle_* 实现」）。启动期不变量：绑定表键集必须
# 与 registry 完全一致——漏接 / 错接方法即在 import 期炸，与 handler_registry 内
# `HANDLER_SPECS == KNOWN_METHODS` 断言形成双向守卫。
_HANDLERS: dict[str, Any] = {
    'hasn.node.add_owner': _handle_add_owner,
    'hasn.node.remove_owner': _handle_remove_owner,
    'hasn.node.renew_owner': _handle_renew_owner,
    'hasn.node.list_owners': _handle_list_owners,
    'hasn.node.add_agent': _handle_add_agent,
    'hasn.node.remove_agent': _handle_remove_agent,
    'hasn.agent.register': _handle_agent_register,
    'hasn.agent.deregister': _handle_agent_deregister,
    'hasn.message.send': _handle_send,
    'hasn.message.read': _handle_read,
    'hasn.typing': _handle_typing,
    'hasn.ping': _handle_ping,
}

assert set(_HANDLERS) == set(HANDLER_SPECS), (
    '_HANDLERS 与 HANDLER_SPECS 键集不一致：'
    f'仅在绑定表={set(_HANDLERS) - set(HANDLER_SPECS)}，'
    f'仅在 registry={set(HANDLER_SPECS) - set(_HANDLERS)}'
)
