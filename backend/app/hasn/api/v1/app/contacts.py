"""
HASN 联系人 & 好友请求 API
对应设计文档: 07-API设计.md §三
阶段二新增: 权限矩阵 API (trust-level / permissions / effective-permissions)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.crud.crud_user import user_dao
from backend.app.hasn.constants import (
    ERR_TRUST_LEVEL_INVALID,
    IronLawViolation,
    compute_effective_permissions,
    validate_against_iron_laws,
    validate_relation_constraints,
)
from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao
from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.schema.hasn_contacts_business import (
    TRUST_LEVEL_LABELS,
    AgentPeerOut,
    HasnContactListResp,
    HasnContactOut,
    HasnContactPeerOut,
    HasnContactRequestOut,
    HasnContactRequestReq,
    HasnContactRespondReq,
    HasnPermissionsReq,
    HasnTrustLevelReq,
)
from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn.service.hasn_contacts_service import ContactRequestError, HasnContactsService
from backend.app.hasn.service.ws_router import ws_router
from backend.common.response.response_code import CustomResponse
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter(prefix='/contacts', tags=['HASN Contacts'])


def _peer_display_name(peer_info, *, peer_type: str) -> str:
    if peer_type == 'human':
        return getattr(peer_info, 'nickname', None) or getattr(peer_info, 'name', '') or ''
    return getattr(peer_info, 'display_name', None) or getattr(peer_info, 'name', '') or ''


async def _resolve_peer_user_profile(db, peer_info, *, peer_type: str):
    if peer_type != 'human':
        return None
    user_id = getattr(peer_info, 'user_id', None)
    if not user_id:
        return None
    return await user_dao.get(db, user_id)


async def _push_contact_event(target_hasn_id: str, payload: dict) -> None:
    try:
        await ws_router.push_message_to(target_hasn_id, payload)
    except Exception:
        return


def _agent_peer_out(agent) -> HasnContactPeerOut:
    """把一个 HasnAgents 行整形成 type='agent' 的 peer 输出（列表/请求/连接事件复用）。"""
    return HasnContactPeerOut(
        hasn_id=agent.hasn_id,
        star_id=getattr(agent, 'star_id', '') or '',
        name=_peer_display_name(agent, peer_type='agent'),
        type='agent',
        avatar=getattr(agent, 'avatar', None),
    )


@router.post('/request', summary='发送好友请求')
async def send_contact_request(
    obj_in: HasnContactRequestReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """发送好友请求 (social 关系)。

    请求落独立的 hasn_contact_requests 表，通过后才在 hasn_contacts 建边（见 ADR 2026-05-30）。
    两类目标：
    - human 唤星号 → 加好友（owner 级，加人）；目标即审批人本人。
    - agent 唤星号 → 请求把好友的『分身』加为联系人（agent 级）；审批人=分身主人，
      不再坍缩成主人、也不因主人已是好友而拦截。
    校验：无 connected 关系 + 无 pending 请求 + 未被对方拉黑。
    单一实现在 HasnContactsService.request_contact（人端与 Agent 平台工具共用，杜绝两份漂移）。
    """
    hasn_id = auth.get('effective_id', auth['hasn_id'])

    # 单一实现：解析目标(human/agent) + 校验 + 落 pending 请求 + 推审批方事件，全在 service。
    # （Agent 平台工具 hasn.contact.request 复用同一 HasnContactsService.request_contact。）
    try:
        result = await HasnContactsService.request_contact(
            db,
            requester_hasn_id=hasn_id,
            target=obj_in.target_star_id,
            message=obj_in.message,
            add_source=obj_in.add_source,
        )
    except ContactRequestError as e:
        return response_base.fail(res=CustomResponse(code=400, msg=e.msg))

    return response_base.success(
        data=HasnContactRequestOut(
            request_id=result['request_id'],
            status=result['status'],
            relation_type=result['relation_type'],
            created_at=result['created_at'],
            channel_source=result['channel_source'],
            add_source=result['add_source'],
            target=HasnContactPeerOut(**result['target']),
            message=result['message'],
        ).model_dump()
    )


@router.get('/requests', summary='获取待处理好友请求')
async def list_pending_requests(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
    direction: Annotated[str, Query(description='received=收到的, sent=自己发出的')] = 'received',
) -> ResponseModel:
    """获取待处理好友请求列表。

    - direction=received (默认): 我收到的待处理请求, 每条带 from_peer (发起方)
    - direction=sent: 我已发出但对方还没处理的请求, 每条带 target (目标方)
    """
    if direction not in ('received', 'sent'):
        raise HTTPException(status_code=422, detail='direction 必须是 received 或 sent')

    hasn_id = auth.get('effective_id', auth['hasn_id'])

    if direction == 'received':
        requests = await hasn_contact_requests_dao.get_received_pending(db, hasn_id)
        items = []
        for req in requests:
            sender = await hasn_humans_dao.get_by_hasn_id(db, req.from_id)
            if sender:
                from_peer = HasnContactPeerOut(
                    hasn_id=sender.hasn_id,
                    star_id=sender.star_id,
                    name=_peer_display_name(sender, peer_type='human'),
                    type='human',
                )
            else:
                # from_id 解析失败用 stub 占位, 不抛 500 (INV-15)
                from_peer = HasnContactPeerOut(
                    hasn_id=req.from_id,
                    star_id='',
                    name='',
                    type='human',
                )
            # agent 目标的请求：审批方收件箱要能渲染「请求联系的 AI分身」。
            target = None
            if req.to_type == 'agent':
                agent = await hasn_agents_dao.get_by_hasn_id(db, req.to_id)
                target = (
                    _agent_peer_out(agent)
                    if agent
                    else HasnContactPeerOut(hasn_id=req.to_id, star_id='', name='', type='agent')
                )
            items.append(
                HasnContactRequestOut(
                    request_id=req.id,
                    status=req.status,
                    created_at=req.created_time,
                    channel_source=req.channel_source,
                    add_source=req.add_source,
                    from_peer=from_peer,
                    target=target,
                    message=req.message or '',
                )
            )
        return response_base.success(data=[i.model_dump() for i in items])

    # direction == 'sent'：target 可能是 human，也可能是好友的『分身』(agent)
    requests = await hasn_contact_requests_dao.get_sent_pending(db, hasn_id)
    items = []
    for req in requests:
        if req.to_type == 'agent':
            agent = await hasn_agents_dao.get_by_hasn_id(db, req.to_id)
            target = (
                _agent_peer_out(agent)
                if agent
                else HasnContactPeerOut(hasn_id=req.to_id, star_id='', name='', type='agent')
            )
        else:
            target_human = await hasn_humans_dao.get_by_hasn_id(db, req.to_id)
            if target_human:
                target = HasnContactPeerOut(
                    hasn_id=target_human.hasn_id,
                    star_id=target_human.star_id,
                    name=_peer_display_name(target_human, peer_type='human'),
                    type='human',
                )
            else:
                target = HasnContactPeerOut(
                    hasn_id=req.to_id,
                    star_id='',
                    name='',
                    type='human',
                )
        items.append(
            HasnContactRequestOut(
                request_id=req.id,
                status=req.status,
                created_at=req.created_time,
                channel_source=req.channel_source,
                add_source=req.add_source,
                target=target,
                message=req.message or '',
            )
        )
    return response_base.success(data=[i.model_dump() for i in items])


@router.put('/requests/{request_id}/respond', summary='回应好友请求')
async def respond_to_request(
    request_id: int,
    obj_in: HasnContactRespondReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """回应好友请求：accept / reject（审批人）/ withdraw（发起方）。

    accept 时通过 UPSERT 在 hasn_contacts 建双向 connected 边（兜过历史 archived 行），
    并把请求标记 accepted、回填 resulting_contact_id（审计链）。
    """
    hasn_id = auth.get('effective_id', auth['hasn_id'])
    req = await hasn_contact_requests_dao.get(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail='好友请求不存在')
    if req.status != 'pending':
        return response_base.fail(res=CustomResponse(code=400, msg=f'该请求已处理 (status={req.status})'))

    trust = req.requested_trust_level or 2

    if obj_in.action == 'accept':
        if req.to_owner_id != hasn_id:
            raise HTTPException(status_code=403, detail='只有被请求方可以接受该请求')

        # agent 目标：只建『请求方 → 分身』单向 agent 边（分身回复依赖主人↔主人 trust，已≥2，
        # 无需反向 agent 边）。信任等级沿用请求时落库的『与主人一致』值。
        if req.to_type == 'agent':
            forward = await hasn_contacts_dao.upsert_connected(
                db, owner_id=req.from_id, peer_id=req.to_id, peer_type='agent',
                relation_type=req.relation_type, trust_level=trust,
                peer_owner_id=req.to_owner_id, channel_source=req.channel_source or 'manual',
                add_source=req.add_source, request_message=req.message,
            )
            await hasn_contact_requests_dao.mark_accepted(
                db, request_id, decided_by=hasn_id, resulting_contact_id=forward.id,
            )
            await db.commit()

            agent = await hasn_agents_dao.get_by_hasn_id(db, req.to_id)
            peer = (
                _agent_peer_out(agent)
                if agent
                else HasnContactPeerOut(hasn_id=req.to_id, star_id='', name='', type='agent')
            )
            await _push_contact_event(
                req.from_id,
                {
                    'method': 'hasn.contact.connected',
                    'params': {
                        'owner_id': req.from_id,
                        'request_id': request_id,
                        'peer': peer.model_dump(),
                        'trust_level': trust,
                    },
                },
            )
            return response_base.success(data={'status': 'connected', 'trust_level': trust})

        # UPSERT 双向边：发起方→目标、目标→发起方，均 connected
        forward = await hasn_contacts_dao.upsert_connected(
            db, owner_id=req.from_id, peer_id=req.to_id, peer_type='human',
            relation_type=req.relation_type, trust_level=trust,
            peer_owner_id=req.to_id, channel_source=req.channel_source or 'manual',
            add_source=req.add_source, request_message=req.message,
        )
        await hasn_contacts_dao.upsert_connected(
            db, owner_id=req.to_id, peer_id=req.from_id, peer_type='human',
            relation_type=req.relation_type, trust_level=trust,
            peer_owner_id=req.from_id, channel_source=req.channel_source or 'manual',
            request_message=req.message,
        )
        await hasn_contact_requests_dao.mark_accepted(
            db, request_id, decided_by=hasn_id, resulting_contact_id=forward.id,
        )
        await db.commit()

        acceptor = await hasn_humans_dao.get_by_hasn_id(db, req.to_id)
        peer = HasnContactPeerOut(
            hasn_id=req.to_id,
            star_id=getattr(acceptor, 'star_id', ''),
            name=_peer_display_name(acceptor, peer_type='human') if acceptor else '',
            type='human',
        )
        await _push_contact_event(
            req.from_id,
            {
                'method': 'hasn.contact.connected',
                'params': {
                    'owner_id': req.from_id,
                    'request_id': request_id,
                    'peer': peer.model_dump(),
                    'trust_level': trust,
                },
            },
        )
        return response_base.success(data={'status': 'connected', 'trust_level': trust})

    if obj_in.action == 'reject':
        if req.to_owner_id != hasn_id:
            raise HTTPException(status_code=403, detail='只有被请求方可以拒绝该请求')
        await hasn_contact_requests_dao.mark_rejected(db, request_id, decided_by=hasn_id)
        await db.commit()
        return response_base.success(data={'status': 'rejected'})

    if obj_in.action == 'withdraw':
        if req.from_id != hasn_id:
            raise HTTPException(status_code=403, detail='只有发起方可以撤回该请求')
        await hasn_contact_requests_dao.mark_withdrawn(db, request_id, decided_by=hasn_id)
        await db.commit()
        return response_base.success(data={'status': 'withdrawn'})

    return response_base.fail(res=CustomResponse(code=400, msg='action 必须是 accept / reject / withdraw'))


@router.get('', summary='联系人列表')
async def list_contacts(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
    relation_type: Annotated[str | None, Query(description='关系类型筛选；不传则返回全部联系人')] = None,
) -> ResponseModel:
    hasn_id = auth.get('effective_id', auth['hasn_id'])
    contacts = await hasn_contacts_dao.list_contacts(db, hasn_id, relation_type=relation_type)

    items = []
    for c in contacts:
        # 查 peer 信息（使用 hasn_id）
        peer_info = await hasn_humans_dao.get_by_hasn_id(db, c.peer_id)
        if not peer_info:
            peer_info = await hasn_agents_dao.get_by_hasn_id(db, c.peer_id)
        if not peer_info:
            continue
        # agent 联系人需带「主人摘要」：列表第二行展示这个分身归属谁（头像 + 昵称）。
        owner_peer: HasnContactPeerOut | None = None
        if c.peer_type == 'agent':
            peer_owner_id = c.peer_owner_id or getattr(peer_info, 'owner_id', None)
            if peer_owner_id == hasn_id:
                continue
            if peer_owner_id:
                owner_human = await hasn_humans_dao.get_by_hasn_id(db, peer_owner_id)
                if owner_human:
                    owner_peer = HasnContactPeerOut(
                        hasn_id=owner_human.hasn_id,
                        star_id=owner_human.star_id or '',
                        name=owner_human.nickname or owner_human.star_id or owner_human.hasn_id,
                        type='human',
                        avatar=getattr(owner_human, 'avatar', None),
                    )

        # 阶段二: 查询 human 联系人名下的 Agent 列表（含实时在线状态）。
        # 与详情构造共用 HasnContactsService.fetch_owned_agents_with_status，
        # 列表/详情同一份 owned_agents 定义 + 同源 online_status（修复列表路径
        # 此前漏 JOIN 运行时上报、头像无在线状态点的根因）。
        owned_agents: list[AgentPeerOut] = []
        if c.peer_type == 'human':
            agent_dicts = await HasnContactsService.fetch_owned_agents_with_status(db, c.peer_id)
            owned_agents.extend(
                AgentPeerOut(
                    hasn_id=ag['hasn_id'],
                    star_id=ag['star_id'],
                    name=ag['name'],
                    agent_name=ag['agent_name'],
                    avatar=ag.get('avatar'),
                    type=ag.get('type') or 'desktop',
                    role=ag.get('role') or 'specialist',
                    description=ag.get('description'),
                    bio=ag.get('bio'),
                    online_status=ag.get('online_status') or 'offline',
                    last_seen_at=ag.get('last_seen_at'),
                )
                for ag in agent_dicts
            )

        # HasnHumans 使用 nickname，HasnAgents 使用 display_name
        peer_name = peer_info.nickname if c.peer_type == 'human' else peer_info.display_name
        peer_user = await _resolve_peer_user_profile(db, peer_info, peer_type=c.peer_type)
        items.append(
            HasnContactOut(
                id=c.id,
                peer=HasnContactPeerOut(
                    hasn_id=peer_info.hasn_id,
                    star_id=peer_info.star_id,
                    name=peer_name,
                    type=c.peer_type,
                    avatar=getattr(peer_info, 'avatar', None),
                ),
                relation_type=c.relation_type,
                trust_level=c.trust_level,
                trust_level_label=TRUST_LEVEL_LABELS.get(c.trust_level, ''),
                channel_source=c.channel_source,
                add_source=c.add_source,
                nickname=c.nickname,
                bio=getattr(peer_user, 'bio', None),
                gender=getattr(peer_user, 'gender', None),
                province=getattr(peer_user, 'province', None),
                city=getattr(peer_user, 'city', None),
                district=getattr(peer_user, 'district', None),
                tags=c.tags,
                subscription=c.subscription,
                status=c.status,
                owned_agents=owned_agents,
                custom_permissions=c.custom_permissions or {},
                scope=c.scope,
                connected_at=str(c.connected_at) if c.connected_at else None,
                last_interaction_at=str(c.last_interaction_at) if c.last_interaction_at else None,
                # Phase 1 US-002: 补齐 contacts 业务字段
                interaction_count=c.interaction_count or 0,
                request_message=c.request_message,
                auto_expire=str(c.auto_expire) if c.auto_expire else None,
                peer_owner_id=c.peer_owner_id,
                owner=owner_peer,
            )
        )

    return response_base.success(data=HasnContactListResp(total=len(items), items=items).model_dump())


# ─── 阶段二: 权限矩阵 API ───────────────────────────────


@router.put('/{contact_id}/trust-level', summary='修改信任等级')
async def update_trust_level(
    contact_id: int,
    obj_in: HasnTrustLevelReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """
    修改联系人信任等级 (0-5)。
    铁律校验: trust_level=5 仅限自己的 Agent (peer_type='agent')
    """
    hasn_id = auth.get('effective_id', auth['hasn_id'])
    contact = await hasn_contacts_dao.get(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail='联系人不存在')
    if contact.owner_id != hasn_id:
        raise HTTPException(status_code=403, detail='无权修改此联系人')

    # 协议级约束 (Core/02 §7.4.1, Core/04 §1.4)
    # - 非 social 关系不得设置 trust_level=5（Owner 仅 social）
    # - service 关系不存在 Stranger 状态（trust_level=1）
    try:
        validate_relation_constraints(contact.relation_type, obj_in.trust_level)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={'code': ERR_TRUST_LEVEL_INVALID, 'msg': str(e)},
        ) from e

    # 铁律 2: trust_level=5 仅限自己的 Agent
    if obj_in.trust_level == 5:
        if contact.peer_type != 'agent':
            raise HTTPException(status_code=400, detail='trust_level=5 (所有者) 仅限自己的 Agent')
        # 进一步校验：是否真的是自己的 Agent
        agent = await hasn_agents_dao.get_by_hasn_id(db, contact.peer_id)
        if not agent or agent.owner_id != hasn_id:
            raise HTTPException(status_code=403, detail='只能将自己名下的 Agent 设为所有者等级')

    contact.trust_level = obj_in.trust_level
    await db.commit()

    return response_base.success(
        data={
            'contact_id': contact_id,
            'trust_level': obj_in.trust_level,
            'trust_level_label': TRUST_LEVEL_LABELS.get(obj_in.trust_level, ''),
        }
    )


@router.put('/{contact_id}/permissions', summary='自定义权限覆盖')
async def update_permissions(
    contact_id: int,
    obj_in: HasnPermissionsReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """
    覆盖特定联系人的权限（叠加在默认矩阵之上）。
    系统会对所有覆盖项进行铁律冲突校验，违反任一铁律则拒绝整个请求。
    """
    hasn_id = auth.get('effective_id', auth['hasn_id'])
    contact = await hasn_contacts_dao.get(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail='联系人不存在')
    if contact.owner_id != hasn_id:
        raise HTTPException(status_code=403, detail='无权修改此联系人')

    # 铁律冲突校验
    try:
        validate_against_iron_laws(
            relation_type=contact.relation_type,
            permissions=obj_in.permissions,
            peer_type=contact.peer_type,
            trust_level=contact.trust_level,
        )
    except IronLawViolation as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # 合并写入（保留未涉及的已有覆盖项）
    existing = contact.custom_permissions or {}
    existing.update(obj_in.permissions)
    contact.custom_permissions = existing
    await db.commit()

    return response_base.success(
        data={
            'contact_id': contact_id,
            'custom_permissions': contact.custom_permissions,
        }
    )


@router.get('/{contact_id}/effective-permissions', summary='有效权限')
async def get_effective_permissions(
    contact_id: int,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    """
    返回合并后的有效权限（默认矩阵 + custom_permissions 覆盖）。
    可用于前端在发起行为前检查权限状态。
    """
    hasn_id = auth.get('effective_id', auth['hasn_id'])
    contact = await hasn_contacts_dao.get(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail='联系人不存在')
    if contact.owner_id != hasn_id:
        raise HTTPException(status_code=403, detail='无权查询此联系人')

    effective = compute_effective_permissions(
        relation_type=contact.relation_type,
        trust_level=contact.trust_level,
        custom_permissions=contact.custom_permissions,
    )

    return response_base.success(
        data={
            'contact_id': contact_id,
            'relation_type': contact.relation_type,
            'trust_level': contact.trust_level,
            'trust_level_label': TRUST_LEVEL_LABELS.get(contact.trust_level, ''),
            'effective_permissions': effective,
        }
    )
