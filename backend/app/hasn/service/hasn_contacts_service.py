from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_contacts import hasn_contacts_dao
from backend.app.hasn.constants import TRUST_LEVEL_LABELS
from backend.app.hasn.model import HasnContacts
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.schema.hasn_contacts import CreateHasnContactsParam, DeleteHasnContactsParam, UpdateHasnContactsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


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
                peer_info = {
                    "hasn_id": agent.hasn_id,
                    "star_id": agent.star_id,
                    "name": agent.display_name,
                    "type": "agent",
                    "avatar": agent.avatar,
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
        owned_agents: list[dict[str, Any]] = []
        for agent in agents:
            owned_agents.append(
                {
                    "hasn_id": agent.hasn_id,
                    "star_id": agent.star_id,
                    "name": agent.display_name,
                    "agent_name": agent.agent_name,
                    "avatar": agent.avatar,
                    "type": agent.type,
                    "role": agent.role,
                    "description": agent.description,
                    "bio": agent.bio,
                    "online_status": "online" if online_map.get(agent.hasn_id) else "offline",
                    "last_seen_at": (
                        agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None
                    ),
                }
            )
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


hasn_contacts_service: HasnContactsService = HasnContactsService()
