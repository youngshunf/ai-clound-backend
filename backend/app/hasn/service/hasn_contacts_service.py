from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.constants import TRUST_LEVEL_LABELS
from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.crud.crud_hasn_contact_requests import hasn_contact_requests_dao
from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model import HasnContacts
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contact_requests import HasnContactRequests
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_contacts import (
    CreateHasnContactsParam,
    DeleteHasnContactsParam,
    UpdateHasnContactsParam,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

# 好友请求未响应过期阈值（天）：pending 超此天数由 celery beat 兜底置 expired（B7）。
CONTACT_REQUEST_EXPIRE_DAYS = 30


class ContactRequestError(Exception):
    """好友请求业务校验失败（自加/已是好友/被拉黑/已有待处理等，维度② 不可达类）。

    单一实现 `HasnContactsService.request_contact` 在校验失败时抛出：
    - 人端 owner 端点捕获后转 `response_base.fail(code=400)`（保持原 200+code 信封语义）；
    - Agent 平台工具 `hasn.contact.request` 捕获后转 `{'ok': False, 'error': msg}`。
    """

    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        self.msg = msg


class HasnContactsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> dict[str, Any]:
        """
        获取HASN 联系人关系（包含 peer 信息和 owned_agents）

        :param db: 数据库会话
        :param pk: HASN 联系人关系 ID
        :return:
        """
        hasn_contacts = await hasn_contacts_dao.get(db, pk)
        if not hasn_contacts:
            raise errors.NotFoundError(msg='HASN 联系人关系不存在')

        # 构造完整的联系人信息（包含 peer 和 owned_agents）
        return await HasnContactsService._build_contact_detail(db, hasn_contacts)

    @staticmethod
    async def _build_contact_detail(db: AsyncSession, contact: HasnContacts) -> dict[str, Any]:
        """
        构造完整的联系人详情（包含 peer 信息和 owned_agents）

        :param db: 数据库会话
        :param contact: 联系人关系记录
        :return: 完整的联系人信息字典
        """
        # 基础联系人信息
        result = {
            "id": contact.id,
            "owner_id": contact.owner_id,
            "peer_id": contact.peer_id,
            "peer_owner_id": contact.peer_owner_id,
            "peer_type": contact.peer_type,
            "relation_type": contact.relation_type,
            "trust_level": contact.trust_level,
            "trust_level_label": HasnContactsService._get_trust_level_label(contact.trust_level),
            "scope": contact.scope,
            "custom_permissions": contact.custom_permissions,
            "nickname": contact.nickname,
            "tags": contact.tags,
            "subscription": contact.subscription,
            "channel_source": contact.channel_source,
            "status": contact.status,
            "request_message": contact.request_message,
            "auto_expire": contact.auto_expire.isoformat() if contact.auto_expire else None,
            "connected_at": contact.connected_at.isoformat() if contact.connected_at else None,
            "last_interaction_at": contact.last_interaction_at.isoformat() if contact.last_interaction_at else None,
            "interaction_count": contact.interaction_count,
            "created_time": contact.created_time.isoformat() if contact.created_time else None,
            "updated_time": contact.updated_time.isoformat() if contact.updated_time else None,
        }

        # 获取 peer 信息（对方的详细信息）
        peer_info = None
        if contact.peer_type == "human":
            human_result = await db.execute(
                select(HasnHumans).where(HasnHumans.hasn_id == contact.peer_id)
            )
            human = human_result.scalar_one_or_none()
            if human:
                peer_info = {
                    "hasn_id": human.hasn_id,
                    "star_id": human.star_id,
                    "name": human.nickname,
                    "type": "human",
                    "avatar": human.avatar,
                }
        elif contact.peer_type == "agent":
            agent_result = await db.execute(
                select(HasnAgents).where(HasnAgents.hasn_id == contact.peer_id)
            )
            agent = agent_result.scalar_one_or_none()
            if agent:
                # 联系人本身就是「他人的分身」（直接加分身为联系人）时，peer 也必须带
                # **实时在线态**——与 owned_agents 同源（Redis presence + 节点存活 + 就绪键，
                # 走 get_online_map）。此前 peer 分支从不回填 online_status，导致跨主人/跨设备
                # 看好友的分身永远显示离线、输入框被禁发（webui useSenderLabels 对无 presence
                # 的 agent peer 一律按 offline 灰点）。
                from backend.app.hasn.service.ws_router import ws_router

                online_map = await ws_router.get_online_map([agent.hasn_id])
                peer_info = {
                    "hasn_id": agent.hasn_id,
                    "star_id": agent.star_id,
                    "name": agent.display_name,
                    "type": "agent",
                    "avatar": agent.avatar,
                    "online_status": "online" if online_map.get(agent.hasn_id) else "offline",
                }

        result["peer"] = peer_info

        owned_agents: list[dict[str, Any]] = []
        if contact.peer_type == "human":
            owned_agents = await HasnContactsService.fetch_owned_agents_with_status(
                db, contact.peer_id
            )

        result["owned_agents"] = owned_agents

        return result

    @staticmethod
    async def fetch_owned_agents_with_status(
        db: AsyncSession, peer_id: str
    ) -> list[dict[str, Any]]:
        """
        查询某个 human 名下、对社交可见的 active Agent 及其实时在线状态。

        在线状态取自 **Redis presence**（`ws_router.get_online_map`，叠加节点存活
        心跳 node_alive 门控）——与 `sync_agents` 同源、断线即离线。**不再**读持久列
        `HasnAgents.online_status`：该列由心跳写、断线不清零，agent 非优雅退出后会
        永远停在 online（僵尸在线）；P3 的 TTL 僵尸回收只对 Redis presence 生效。
        持久 `last_heartbeat_at` 仅作「最后已知时间」展示，不再当在线权威。
        描述用 HasnAgents.description（agent 的角色介绍，bio 多为空）。
        联系人**列表**端点与**详情**构造共用本方法，保证「TA 的 AI 分身」在
        列表与详情看到的 Agent 集合、在线状态、描述一致（避免 split-brain）。

        :param db: 数据库会话
        :param peer_id: 联系人（human）的 hasn_id
        :return: owned_agents 字典列表
        """
        from backend.app.hasn.service.ws_router import ws_router

        agents_result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.owner_id == peer_id,
                HasnAgents.status == "active",
                HasnAgents.social_enabled.is_(True),
                HasnAgents.deleted_at.is_(None),
            )
        )
        agents = list(agents_result.scalars().all())
        # 实时在线：Redis presence + node_alive 门控（僵尸节点判离线）。
        online_map = await ws_router.get_online_map([a.hasn_id for a in agents])
        owned_agents: list[dict[str, Any]] = [{
                    "hasn_id": agent.hasn_id,
                    "star_id": agent.star_id,
                    "name": agent.display_name,
                    "agent_name": agent.agent_name,
                    "avatar": agent.avatar,
                    "type": agent.type,
                    "role": agent.role,
                    "profession": agent.profession,
                    "description": agent.description,
                    "bio": agent.bio,
                    "online_status": "online" if online_map.get(agent.hasn_id) else "offline",
                    "last_seen_at": (
                        agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None
                    ),
                } for agent in agents]
        return owned_agents

    @staticmethod
    def _get_trust_level_label(trust_level: int) -> str:
        """获取信任等级标签"""
        return TRUST_LEVEL_LABELS.get(trust_level, "未知")

    @staticmethod
    async def get_list(
        db: AsyncSession,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """
        获取HASN 联系人关系列表（包含 peer 信息和 owned_agents）

        :param db: 数据库会话
        :param user_id: 当提供时，仅返回该平台用户（sys_user.id）所对应 hasn_humans.hasn_id
            拥有（contacts.owner_id）的记录。不同 user_id 返回不同集合；
            找不到对应 Human 时返回空集合（避免权限泄露）。
        :return:
        """
        hasn_contacts_select = await hasn_contacts_dao.get_select()
        if user_id is not None:
            owner_ids_subq = select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id)
            hasn_contacts_select = hasn_contacts_select.where(
                HasnContacts.owner_id.in_(owner_ids_subq)
            )

        page_data = await paging_data(db, hasn_contacts_select)

        # 为每个联系人构造完整信息
        items = []
        for contact in page_data["items"]:
            detail = await HasnContactsService._build_contact_detail(db, contact)
            items.append(detail)

        page_data["items"] = items
        return page_data

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnContacts]:
        """
        获取所有HASN 联系人关系

        :param db: 数据库会话
        :return:
        """
        hasn_contactss = await hasn_contacts_dao.get_all(db)
        return hasn_contactss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnContactsParam) -> None:
        """
        创建HASN 联系人关系

        :param db: 数据库会话
        :param obj: 创建HASN 联系人关系参数
        :return:
        """
        await hasn_contacts_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnContactsParam) -> int:
        """
        更新HASN 联系人关系

        :param db: 数据库会话
        :param pk: HASN 联系人关系 ID
        :param obj: 更新HASN 联系人关系参数
        :return:
        """
        count = await hasn_contacts_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnContactsParam) -> int:
        """
        删除HASN 联系人关系

        :param db: 数据库会话
        :param obj: HASN 联系人关系 ID 列表
        :return:
        """
        count = await hasn_contacts_dao.delete(db, obj.pks)
        return count

    # ─── 好友请求（single source of truth：人端 owner 端点与 Agent 平台工具共用）───

    @staticmethod
    def _peer_name(entity: Any, *, peer_type: str) -> str:
        if peer_type == 'human':
            return getattr(entity, 'nickname', None) or getattr(entity, 'name', '') or ''
        return getattr(entity, 'display_name', None) or getattr(entity, 'name', '') or ''

    @staticmethod
    async def _resolve_contact_target(db: AsyncSession, target: str) -> tuple[Any, str | None]:
        """把唤星号或 HASN ID 解析成 (实体, 'human'|'agent')；解析失败返回 (None, None)。

        - `a_*` / 含 `#` → agent；`h_*` / 其它 → human。
        - 同时支持唤星号（人端 UI 传）与 HASN ID（Agent 经 contact.search/user.search 拿到）。
        """
        t = (target or '').strip()
        if t.startswith('a_'):
            agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=t)
            if agent:
                return agent, 'agent'
        elif t.startswith('h_'):
            human = await hasn_humans_dao.get_by_hasn_id(db, hasn_id=t)
            if human:
                return human, 'human'
        elif '#' in t:
            agent = await hasn_agents_dao.get_by_star_id(db, t)
            if agent:
                return agent, 'agent'
        else:
            human = await hasn_humans_dao.get_by_star_id(db, t)
            if human:
                return human, 'human'
        return None, None

    @staticmethod
    def _request_out(req: Any, *, to_type: str, target: dict[str, Any], message: str | None) -> dict[str, Any]:
        return {
            'request_id': req.id,
            'status': 'pending',
            'relation_type': 'social',
            'created_at': req.created_time,
            'channel_source': req.channel_source,
            'add_source': req.add_source,
            'to_type': to_type,
            'target': target,
            'message': message or '',
        }

    @staticmethod
    async def _push_request_received(
        db: AsyncSession, requester_hasn_id: str, to_owner_id: str, req: Any, target_peer: dict, message: str | None,
    ) -> None:
        """给审批方（被加人 / 分身主人）推 hasn.contact.request_received（best-effort，失败不阻塞）。"""
        from backend.app.hasn.service.ws_router import ws_router

        requester = await hasn_humans_dao.get_by_hasn_id(db, requester_hasn_id)
        from_peer = {
            'hasn_id': requester_hasn_id,
            'star_id': getattr(requester, 'star_id', '') or '',
            'name': HasnContactsService._peer_name(requester, peer_type='human') if requester else '',
            'type': 'human',
        }
        try:
            await ws_router.push_message_to(
                to_owner_id,
                {
                    'method': 'hasn.contact.request_received',
                    'params': {
                        'owner_id': to_owner_id,
                        'request_id': req.id,
                        'from_peer': from_peer,
                        'target': target_peer,
                        'message': message or '',
                    },
                },
            )
        except Exception:
            return

    @staticmethod
    async def request_contact(
        db: AsyncSession,
        *,
        requester_hasn_id: str,
        target: str,
        message: str | None = None,
        add_source: str = 'other',
    ) -> dict[str, Any]:
        """发起一条 social 好友请求（人端 owner 与 Agent 代主人加好友的唯一实现）。

        - `target` 接受唤星号（human 唤星号 / agent `xxx#yyy`）或 HASN ID（h_*/a_*）。
        - 请求落独立的 hasn_contact_requests 表，通过后才在 hasn_contacts 建边（ADR 2026-05-30）。
        - human 目标：审批人=对方本人；agent 目标：审批人=分身主人（不因主人已是好友而拦截）。
        - 业务校验失败抛 ContactRequestError；成功返回 dict（见 _request_out）。
        """
        entity, kind = await HasnContactsService._resolve_contact_target(db, target)
        if not entity:
            raise ContactRequestError(f'目标 {target} 不存在')
        if kind == 'agent':
            return await HasnContactsService._request_agent_contact(
                db, requester_hasn_id=requester_hasn_id, agent=entity, message=message, add_source=add_source,
            )
        return await HasnContactsService._request_human_contact(
            db, requester_hasn_id=requester_hasn_id, human=entity, message=message, add_source=add_source,
        )

    @staticmethod
    async def _request_human_contact(
        db: AsyncSession, *, requester_hasn_id: str, human: Any, message: str | None, add_source: str,
    ) -> dict[str, Any]:
        to_id = human.hasn_id
        if to_id == requester_hasn_id:
            raise ContactRequestError('不能添加自己为好友')
        existing = await hasn_contacts_dao.get_relation(db, requester_hasn_id, to_id, 'social')
        if existing and existing.status == 'connected':
            raise ContactRequestError('你们已经是好友')
        reverse = await hasn_contacts_dao.get_relation(db, to_id, requester_hasn_id, 'social')
        if reverse and reverse.trust_level == 0:
            raise ContactRequestError('无法向对方发送好友请求')
        pending = await hasn_contact_requests_dao.get_active_pending(db, requester_hasn_id, to_id, 'social')
        if pending:
            raise ContactRequestError('已有待处理的好友请求')
        req = await hasn_contact_requests_dao.create_request(
            db,
            from_id=requester_hasn_id,
            to_id=to_id,
            to_owner_id=to_id,
            relation_type='social',
            requested_trust_level=2,
            message=message,
            channel_source='manual',
            add_source=add_source,
        )
        await db.commit()
        target_peer = {
            'hasn_id': to_id,
            'star_id': getattr(human, 'star_id', '') or '',
            'name': HasnContactsService._peer_name(human, peer_type='human'),
            'type': 'human',
        }
        await HasnContactsService._push_request_received(db, requester_hasn_id, to_id, req, target_peer, message)
        return HasnContactsService._request_out(req, to_type='human', target=target_peer, message=message)

    @staticmethod
    async def _request_agent_contact(
        db: AsyncSession, *, requester_hasn_id: str, agent: Any, message: str | None, add_source: str,
    ) -> dict[str, Any]:
        """请求把好友的『分身』加为联系人（agent 目标，审批人=分身主人）。

        与 human 目标的本质区别：目标保持分身本体（to_type='agent'、to_id=分身 hasn_id），
        信任等级与『请求方↔主人』一致；主人是好友是前置而非冲突。已有待处理 → 幂等返回。
        """
        agent_id = agent.hasn_id
        owner_id = getattr(agent, 'owner_id', None) or agent_id
        if owner_id == requester_hasn_id:
            raise ContactRequestError('不能添加自己的分身')
        existing = await hasn_contacts_dao.get_relation(db, requester_hasn_id, agent_id, 'social')
        if existing and existing.status == 'connected':
            raise ContactRequestError('你已添加该分身')
        reverse = await hasn_contacts_dao.get_relation(db, owner_id, requester_hasn_id, 'social')
        if reverse and reverse.trust_level == 0:
            raise ContactRequestError('无法发送请求')
        target_peer = {
            'hasn_id': agent_id,
            'star_id': getattr(agent, 'star_id', '') or '',
            'name': HasnContactsService._peer_name(agent, peer_type='agent'),
            'type': 'agent',
            'avatar': getattr(agent, 'avatar', None),
        }
        pending = await hasn_contact_requests_dao.get_active_pending(db, requester_hasn_id, agent_id, 'social')
        if pending:
            # 幂等返回（Option A 重试 / 兜底重发安全）
            return HasnContactsService._request_out(
                pending, to_type='agent', target=target_peer, message=pending.message or '',
            )
        owner_relation = await hasn_contacts_dao.get_relation(db, requester_hasn_id, owner_id, 'social')
        trust_level = owner_relation.trust_level if owner_relation else 2
        req = await hasn_contact_requests_dao.create_request(
            db,
            from_id=requester_hasn_id,
            to_id=agent_id,
            to_owner_id=owner_id,
            to_type='agent',
            relation_type='social',
            requested_trust_level=trust_level,
            message=message,
            channel_source='manual',
            add_source=add_source,
        )
        await db.commit()
        await HasnContactsService._push_request_received(db, requester_hasn_id, owner_id, req, target_peer, message)
        return HasnContactsService._request_out(req, to_type='agent', target=target_peer, message=message)

    # ─── 删除联系人（D4·hasn.relation.remove 云端权威实现·修 B5）───

    @staticmethod
    async def _push_relation_removed(target_hasn_id: str, actor_hasn_id: str) -> None:
        """给对方推一条**中性**「关系已解除」事件（best-effort，失败不阻塞）。

        D4 铁律：不暴露「被删除/被拉黑」等贬损细节——只告知关系已解除、需重新建立。
        对端 daemon/webui 据此清本地策展行 + 会话标不可达（daemon/webui 侧留后续切片）。
        """
        from backend.app.hasn.service.ws_router import ws_router

        try:
            await ws_router.push_message_to(
                target_hasn_id,
                {
                    'method': 'hasn.contact.removed',
                    'params': {
                        'owner_id': target_hasn_id,
                        'peer_id': actor_hasn_id,
                        # 中性文案：只说关系已解除，不暴露是被删还是被拉黑
                        'reason': 'relation_dissolved',
                        'message': '你们的联系人关系已解除',
                    },
                },
            )
        except Exception:
            return

    @staticmethod
    async def remove_contact(
        db: AsyncSession, *, owner_id: str, contact: HasnContacts,
    ) -> dict[str, Any]:
        """删除联系人：单方发起的双向解除（D4·hasn.relation.remove 云端权威实现·修 B5）。

        三步语义（云端权威边彻底解除，纠正历史「daemon 只删本地策展行、云端边残留」）：
        ① 双向删边：删 owner→peer 与 peer→owner 两条同 relation_type 关系行；
        ② 会话不删但标不可达：把两人 direct 会话标 unreachable（保留历史消息）；
        ③ 中性通知对方「关系已解除」（不暴露贬损细节）。
        返回 {deleted_edges, conversations_marked, peer_id, notified}。
        """
        peer_id = contact.peer_id
        relation_type = contact.relation_type or 'social'
        # 对方归属人：分身 peer 通知其主人，human peer 通知本人（peer_owner_id 为空则回落 peer_id）
        notify_target = contact.peer_owner_id or peer_id

        deleted = await hasn_contacts_dao.delete_relation_bidirectional(
            db, owner_id, peer_id, relation_type,
        )
        marked = await hasn_conversations_dao.mark_direct_unreachable(db, owner_id, peer_id)
        await db.commit()

        # ③ 中性通知对方（放在提交后，通知失败不回滚已解除的关系）
        notified = False
        if notify_target and notify_target != owner_id:
            await HasnContactsService._push_relation_removed(notify_target, owner_id)
            notified = True

        return {
            'deleted_edges': deleted,
            'conversations_marked': marked,
            'peer_id': peer_id,
            'notified': notified,
        }

    # ─── 关系生命周期过期兜底（B7·celery beat 每日调）───

    @staticmethod
    async def sweep_expired_contact_requests(db: AsyncSession) -> int:
        """把创建超 30 天仍 pending 的好友请求置 expired（B7·celery beat 每日兜底）。

        幂等、批量：只收敛存量 pending（已 accepted/rejected/withdrawn/expired 的行被 status
        过滤跳过），重复执行安全。created_time 早于 cutoff 才过期（在 Python 侧判定，便于单测）。
        置 expired 同时回填 decided_at=now（审计：何时兜底过期；无 decided_by，系统兜底无决策人）。
        返回置 expired 的条数。
        """
        now = timezone.now()
        cutoff_ts = now.timestamp() - CONTACT_REQUEST_EXPIRE_DAYS * 86400
        rows = (
            await db.execute(
                select(HasnContactRequests).where(HasnContactRequests.status == 'pending')
            )
        ).scalars().all()
        count = 0
        for req in rows:
            if req.created_time and req.created_time.timestamp() < cutoff_ts:
                req.status = 'expired'
                req.decided_at = now
                count += 1
        if count:
            await db.flush()
        return count

    @staticmethod
    async def sweep_expired_auto_expire_contacts(db: AsyncSession) -> int:
        """把 auto_expire 已过且仍 connected 的联系人置 archived（B7·到期自动断·铁律5b）。

        service 类关系「到期自动断」的兜底清理（当前 auto_expire 多为协议预留、少有落值）。
        幂等、批量：只收敛 connected 且 auto_expire<now 的行（已 archived/blocked 跳过）；
        auto_expire<now 在 Python 侧判定，便于单测。返回置 archived 的条数。
        """
        now = timezone.now()
        rows = (
            await db.execute(
                select(HasnContacts).where(
                    HasnContacts.status == 'connected',
                    HasnContacts.auto_expire.isnot(None),
                )
            )
        ).scalars().all()
        count = 0
        for c in rows:
            if c.auto_expire and c.auto_expire < now:
                c.status = 'archived'
                count += 1
        if count:
            await db.flush()
        return count

    @staticmethod
    async def sweep_expired_relation_lifecycle(db: AsyncSession) -> dict[str, int]:
        """关系生命周期过期总兜底（B7）：好友请求过期 + 联系人 auto_expire 到期，一次提交。

        返回 {'requests_expired': n, 'contacts_expired': m}。
        """
        requests_expired = await HasnContactsService.sweep_expired_contact_requests(db)
        contacts_expired = await HasnContactsService.sweep_expired_auto_expire_contacts(db)
        if requests_expired or contacts_expired:
            await db.commit()
        log.info(
            f'[contact_lifecycle_sweep] requests_expired={requests_expired} '
            f'contacts_expired={contacts_expired}'
        )
        return {'requests_expired': requests_expired, 'contacts_expired': contacts_expired}


hasn_contacts_service: HasnContactsService = HasnContactsService()
