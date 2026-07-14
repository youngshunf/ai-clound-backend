"""HASN 群组编排 service（建群 / 群管理 / 发言规则 / 分身准则 / 拉分身邀请）。

不同于 codegen 生成的 `hasn_group_members_service`（裸 CRUD），本 service 承载
**建群与群管理的业务编排**：分配协议层 `group_id`（g:NNNNNN）、以 type=group 写
`hasn_conversations` 主表、seed `hasn_group_members`、维护 member_count、判角色权限。

doc10 群聊发言规则演进（GS1）：
- `effective_agent_policy` 派生生效策略（多分身群 free→mention_only 强制降级）；
- `allow_member_invite_agent` 拉分身发起闸 + 非主人拉分身邀请确认流程（§3.2）；
- `agent_charter` 分身群内发言准则（仅分身主人可读写，序列化白名单）；
- 主人可替自己的分身撤出本群。

事实源：docs/hasn-node设计文档/03-Runtime调度/10-群聊发言规则与分身群内准则设计.md。
权威性：云端是群结构唯一权威；daemon/webui 只做镜像与展示。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_group_agent_invites import HasnGroupAgentInvites
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception import errors
from backend.utils.timezone import timezone

VALID_AGENT_POLICY = {'free', 'mention_only', 'silent', 'no_agent'}
# 群加入策略：invite_only=邀请制（默认，须群主/管理员拉人）；open=自由加入（任何人可自助入群）；
# approval=审批制（自助申请、待通过——完整审批产品化不在 doc22 范围，暂等同 invite_only 的「需审批」回执）。
VALID_JOIN_POLICY = {'invite_only', 'open', 'approval'}
# 允许 hasn.group.join 工具直接自助入群的策略集合（其余策略回「需邀请/审批」，零 fake）。
_OPEN_JOIN_POLICIES = {'open'}
_GROUP_ID_BASE = 500000
_MAX_MEMBERS_DEFAULT = 200
_ADMIN_ROLES = ('owner', 'admin')
# 群列表里每个群附带的头像预览成员上限（前 N 个成员，供 WebUI 拼九宫格群头像）。
# 取 9 与详情名册一致：>4 人时列表与详情都拼 3×3 九宫格、显示同一批前 9 个成员（同序）。
_AVATAR_PREVIEW_LIMIT = 9
# 分身群内发言准则长度上限（服务层校验，fail fast；前端 textarea 带计数）。
CHARTER_MAX_LEN = 4000
# 群披露档合法档位（doc08 §3.4 RT2.5·D9）：2 普通朋友 / 3 好友 / 4 密友，默认 2。
GROUP_TRUST_LEVELS = (2, 3, 4)
# 拉分身邀请状态集合
INVITE_PENDING = 'pending'
INVITE_ACCEPTED = 'accepted'
INVITE_DECLINED = 'declined'
INVITE_EXPIRED = 'expired'
INVITE_CANCELLED = 'cancelled'
# 邀请 7 天未处理自动过期（读时惰性判定 + celery sweep 双保险）
_INVITE_EXPIRE_DAYS = 7


def effective_agent_policy(agent_policy: str, agent_count: int) -> str:
    """派生「生效发言策略」（doc10 §2.1，纯函数）。

    - `free` 且群内分身数 > 1 → 强制降级 `mention_only`（多分身必须 @ 才回复）；
    - 其余（含 silent/no_agent，本就比 mention_only 更严）原样生效。

    不改写存储值：第二个分身退群后自动恢复群主原设的 free（自动恢复自由发言）。
    """
    if agent_policy == 'free' and agent_count > 1:
        return 'mention_only'
    return agent_policy


def _agent_member_count(roster: list[HasnGroupMembers]) -> int:
    """名册中 member_type=='agent' 的成员数（含 muted，见决策 #3：禁言是临时态不参与状态机）。"""
    return sum(1 for m in roster if m.member_type == 'agent')


class HasnGroupService:
    """群组建/管编排（云端权威）。"""

    # ─── id 分配 ───
    @staticmethod
    async def _alloc_group_id(db: AsyncSession) -> str:
        """分配 g:NNNNNN（取已有最大数字 +1，基线 500000）。

        可移植 PG/SQLite；建群低频，事务内 max+1 足够。并发极端下靠
        `hasn_conversations` 唯一性兜底（如需更强可后续上 Postgres 序列）。
        """
        rows = await db.execute(
            select(HasnConversations.group_id).where(HasnConversations.group_id.like('g:%'))
        )
        mx = _GROUP_ID_BASE
        for (gid,) in rows.all():
            try:
                mx = max(mx, int(str(gid).split(':', 1)[1]))
            except (ValueError, IndexError):
                continue
        return f'g:{mx + 1}'

    # ─── 成员元信息解析 ───
    @staticmethod
    async def _resolve_meta(db: AsyncSession, hasn_id: str) -> tuple[str, str, str]:
        """返回 (member_type, member_name, member_star_id)。查不到则降级用 hasn_id。"""
        if hasn_id.startswith('a_'):
            agent = (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
            ).scalar_one_or_none()
            if agent:
                return 'agent', agent.display_name or hasn_id, agent.star_id or ''
            return 'agent', hasn_id, ''
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if human:
            return 'human', human.nickname or hasn_id, human.star_id or ''
        return 'human', hasn_id, ''

    @staticmethod
    async def _agent_owner_id(db: AsyncSession, agent_hasn_id: str) -> str | None:
        """分身主人 hasn_id；非 agent / 查不到返回 None（按需查 HasnAgents.owner_id）。"""
        if not agent_hasn_id.startswith('a_'):
            return None
        owner = (
            await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == agent_hasn_id))
        ).scalar_one_or_none()
        return owner or None

    @staticmethod
    async def _build_owner_map(db: AsyncSession, members: list[HasnGroupMembers]) -> dict[str, str]:
        """名册中 agent 成员 → 主人 hasn_id 映射（单次批量查，供 charter 白名单判定）。"""
        agent_ids = [m.member_id for m in members if m.member_type == 'agent']
        if not agent_ids:
            return {}
        rows = (
            await db.execute(
                select(HasnAgents.hasn_id, HasnAgents.owner_id).where(HasnAgents.hasn_id.in_(agent_ids))
            )
        ).all()
        return {hid: oid for hid, oid in rows if hid and oid}

    @staticmethod
    async def _build_owner_name_map(db: AsyncSession, owner_ids: set[str]) -> dict[str, str]:
        """主人 hasn_id → 昵称（doc18 S1：群名册分身行标注「主人:昵称·h_id」用）。单次批量查 HasnHumans。"""
        ids = [oid for oid in owner_ids if oid]
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(HasnHumans.hasn_id, HasnHumans.nickname).where(HasnHumans.hasn_id.in_(ids))
            )
        ).all()
        return {hid: name for hid, name in rows if hid and name}

    @staticmethod
    async def _resolve_display_name(db: AsyncSession, hasn_id: str) -> str:
        """人类昵称 / 分身显示名（卡片文案用），查不到降级 hasn_id。"""
        if hasn_id.startswith('a_'):
            name = (
                await db.execute(select(HasnAgents.display_name).where(HasnAgents.hasn_id == hasn_id))
            ).scalar_one_or_none()
        else:
            name = (
                await db.execute(select(HasnHumans.nickname).where(HasnHumans.hasn_id == hasn_id))
            ).scalar_one_or_none()
        return name or hasn_id

    # ─── 序列化 ───
    @staticmethod
    def _member_to_dict(
        m: HasnGroupMembers,
        *,
        charter_visible: bool = False,
        owner_hasn_id: str | None = None,
        owner_name: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            'hasn_id': m.member_id,
            'member_type': m.member_type,
            'display_name': m.member_name,
            'star_id': m.member_star_id,
            'role': m.role,
            'muted': m.muted,
            'joined_at': m.joined_at.isoformat() if m.joined_at else None,
        }
        # doc18 S1：分身成员回填主人 hasn_id + 主人昵称，供 daemon 群名册标注「分身·id·主人:昵称·h_id」，
        # 打通「群里发言的分身 = 之前 A2A 对端分身的主人」身份链（记忆 subject_id 锚点取名册主人 h_id）。
        # 主人 h_id 与分身 hasn_id 一样只作内部身份标识，daemon 侧群内须知已扩为禁止发主人 id 进群。
        if m.member_type == 'agent':
            if owner_hasn_id:
                data['owner_hasn_id'] = owner_hasn_id
            if owner_name:
                data['owner_name'] = owner_name
        # owner 私有字段白名单（隐私边界，doc10 §4.2 + doc08 §3.4）：仅当该行是 actor 名下分身才回填
        # 准则（charter）与群披露档（agent_group_trust_level），其余一律剥离——档位设置本身属主人隐私。
        if charter_visible:
            data['agent_charter'] = m.agent_charter
            data['charter_updated_time'] = (
                m.charter_updated_time.isoformat() if m.charter_updated_time else None
            )
            data['agent_group_trust_level'] = m.agent_group_trust_level
        return data

    @classmethod
    def _group_to_dict(
        cls,
        conv: HasnConversations,
        members: list[HasnGroupMembers] | None = None,
        *,
        actor_hasn_id: str | None = None,
        owner_map: dict[str, str] | None = None,
        owner_name_map: dict[str, str] | None = None,
        agent_member_count: int | None = None,
        pending_invites: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if members is not None:
            agent_count = _agent_member_count(members)
        else:
            agent_count = agent_member_count if agent_member_count is not None else 0
        data: dict[str, Any] = {
            'group_id': conv.group_id,
            'conversation_id': str(conv.id),
            'title': conv.group_name,
            'avatar_url': conv.group_avatar_url,
            'owner_id': conv.group_owner_id,
            'agent_policy': conv.agent_policy,
            # doc10：生效发言策略 + 分身成员数 + 拉分身开关（原字段 agent_policy 不动，兼容旧端）
            'agent_policy_effective': effective_agent_policy(conv.agent_policy, agent_count),
            'agent_member_count': agent_count,
            'allow_member_invite_agent': bool(conv.allow_member_invite_agent),
            'join_policy': conv.join_policy,
            'max_members': conv.max_members,
            'member_count': conv.member_count,
            'status': conv.status,
        }
        if members is not None:
            om = owner_map or {}
            onm = owner_name_map or {}
            data['members'] = [
                cls._member_to_dict(
                    m,
                    charter_visible=(
                        m.member_type == 'agent'
                        and actor_hasn_id is not None
                        and om.get(m.member_id) == actor_hasn_id
                    ),
                    owner_hasn_id=om.get(m.member_id),
                    owner_name=onm.get(om.get(m.member_id, '')),
                )
                for m in members
            ]
        if pending_invites is not None:
            data['pending_agent_invites'] = pending_invites
        return data

    # ─── 内部 helper ───
    @staticmethod
    async def _get_group_or_404(db: AsyncSession, group_id: str) -> HasnConversations:
        conv = (
            await db.execute(
                select(HasnConversations).where(
                    HasnConversations.type == 'group',
                    HasnConversations.group_id == group_id,
                )
            )
        ).scalar_one_or_none()
        if not conv or conv.status == 'disbanded':
            raise errors.NotFoundError(msg='群组不存在或已解散')
        return conv

    @staticmethod
    async def _load_members(db: AsyncSession, conv_id: Any) -> list[HasnGroupMembers]:
        # 按入群时间升序：与列表 members_preview 同序，保证列表/详情群头像宫格成员一致。
        return list(
            (
                await db.execute(
                    select(HasnGroupMembers)
                    .where(HasnGroupMembers.conversation_id == conv_id)
                    .order_by(HasnGroupMembers.joined_at.asc())
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _role_of(members: list[HasnGroupMembers], hasn_id: str) -> str | None:
        for m in members:
            if m.member_id == hasn_id:
                return m.role
        return None

    @classmethod
    async def _add_member_row(
        cls,
        db: AsyncSession,
        conv_id: Any,
        hasn_id: str,
        *,
        role: str,
        invited_by: str | None,
        now: Any,
        roster: list[HasnGroupMembers],
    ) -> HasnGroupMembers:
        mtype, mname, mstar = await cls._resolve_meta(db, hasn_id)
        row = HasnGroupMembers(
            conversation_id=conv_id,
            member_id=hasn_id,
            member_type=mtype,
            member_star_id=mstar,
            member_name=mname,
            role=role,
            muted=False,
            joined_at=now,
            invited_by=invited_by,
        )
        db.add(row)
        roster.append(row)
        return row

    # ─── doc10：拉分身邀请（§3.2）───
    @classmethod
    async def _expire_stale_invites(cls, db: AsyncSession, conv_id: Any) -> None:
        """读时惰性判定：把该群超 7 天未处理的 pending 邀请置 expired。"""
        now = timezone.now()
        cutoff = now.timestamp() - _INVITE_EXPIRE_DAYS * 86400
        rows = (
            await db.execute(
                select(HasnGroupAgentInvites).where(
                    HasnGroupAgentInvites.conversation_id == conv_id,
                    HasnGroupAgentInvites.status == INVITE_PENDING,
                )
            )
        ).scalars().all()
        for inv in rows:
            if inv.created_time and inv.created_time.timestamp() < cutoff:
                inv.status = INVITE_EXPIRED
                inv.resolved_time = now

    @classmethod
    async def _pending_invites(cls, db: AsyncSession, conv_id: Any) -> list[dict[str, Any]]:
        """该群 pending 邀请列表（供群详情成员区展示；已先做惰性过期）。"""
        rows = (
            await db.execute(
                select(HasnGroupAgentInvites)
                .where(
                    HasnGroupAgentInvites.conversation_id == conv_id,
                    HasnGroupAgentInvites.status == INVITE_PENDING,
                )
                .order_by(HasnGroupAgentInvites.created_time.asc())
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for inv in rows:
            agent_name = await cls._resolve_display_name(db, inv.agent_hasn_id)
            owner_name = await cls._resolve_display_name(db, inv.agent_owner_id)
            inviter_name = await cls._resolve_display_name(db, inv.inviter_id)
            out.append({
                'invite_id': inv.id,
                'agent_hasn_id': inv.agent_hasn_id,
                'agent_name': agent_name,
                'agent_owner_id': inv.agent_owner_id,
                'agent_owner_name': owner_name,
                'inviter_id': inv.inviter_id,
                'inviter_name': inviter_name,
                'status': inv.status,
                'created_time': inv.created_time.isoformat() if inv.created_time else None,
            })
        return out

    @classmethod
    async def _notify_group_members(cls, db: AsyncSession, members: list[HasnGroupMembers]) -> None:
        """群设置/成员/生效发言策略变更后，bump KIND_GROUPS 给全体成员 owner（best-effort）。

        daemon 收到即 nudge webui 重拉群详情、刷新「生效发言规则」徽标。charter 变更不走此路径
        （隐私：准则仅主人可见，见 daemon 侧 session 水位重建）。
        """
        from backend.app.hasn.service import sync_invalidate_service

        owners: set[str] = set()
        for m in members:
            if m.member_id.startswith('h_'):
                owners.add(m.member_id)
            elif m.member_type == 'agent':
                o = await cls._agent_owner_id(db, m.member_id)
                if o:
                    owners.add(o)
        for owner in owners:
            try:
                await sync_invalidate_service.bump_owner(sync_invalidate_service.KIND_GROUPS, db, owner)
            except Exception:  # noqa: BLE001 - 广播 best-effort，绝不拖垮群操作主流程
                pass

    @classmethod
    async def _send_agent_invite_card(
        cls,
        *,
        invite_id: int,
        group_public_id: str,
        group_name: str,
        agent_hasn_id: str,
        agent_name: str,
        agent_owner_id: str,
        inviter_id: str,
        inviter_name: str,
    ) -> None:
        """向分身主人发拉分身确认卡片（复用既有卡片 action/resolved 审批范式）。

        经 route_message(content_type=5) 投递（含跨设备 sync_event + WS 推送）。daemon 收到
        卡片 action（group_agent_invite.accepted/declined）→ 代理云端 accept/decline 端点。
        best-effort：投递失败不回滚邀请（邀请行是权威，主人可从群详情 pending 列表处理）。
        """
        from backend.app.hasn.service import message_router
        from backend.database.db import async_db_session

        card = {
            'schema_version': 'hasn.card/0.1',
            'title': f'{inviter_name} 想把你的分身 {agent_name} 拉进群 {group_name}',
            'description': f'同意后，你的分身「{agent_name}」将作为群成员加入「{group_name}」。',
            'source': {'kind': 'system', 'display_name': '群邀请'},
            'resource': {
                'type': 'group',
                'id': str(invite_id),
                'uri': f'hasn://groups/{group_public_id}',
                'title': group_name,
                'metadata': {
                    'invite_id': invite_id,
                    'group_id': group_public_id,
                    'agent_hasn_id': agent_hasn_id,
                },
            },
            'fields': [
                {'label': '群聊', 'value': group_name},
                {'label': '你的分身', 'value': agent_name},
                {'label': '邀请人', 'value': inviter_name},
            ],
            'primary_action': {
                'label': '同意',
                'action_id': 'accept',
                'kind': 'emit_event',
                'style': 'primary',
                'event': {
                    'event_type': 'group_agent_invite.accepted',
                    'payload': {'invite_id': invite_id, 'group_id': group_public_id},
                },
            },
            'actions': [
                {
                    'label': '拒绝',
                    'action_id': 'decline',
                    'kind': 'emit_event',
                    'style': 'danger',
                    'event': {
                        'event_type': 'group_agent_invite.declined',
                        'payload': {'invite_id': invite_id, 'group_id': group_public_id},
                    },
                },
            ],
        }
        try:
            async with async_db_session() as card_db:
                await message_router.route_message(
                    card_db,
                    from_id=inviter_id,
                    to_target=agent_owner_id,
                    content=card,
                    content_type=5,
                    msg_type='message',
                )
        except Exception:  # noqa: BLE001 - 卡片投递 best-effort，不拖垮邀请落库
            pass

    # ─── 对外编排 ───
    @classmethod
    async def create_group(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        title: str,
        members: list[dict[str, Any]] | None = None,
        agent_policy: str = 'free',
        avatar_url: str | None = None,
        join_policy: str = 'invite_only',
    ) -> dict[str, Any]:
        """建群：分配 g:NNNNNN + 写 type=group 会话 + seed 成员（创建者 role=owner）。"""
        title = (title or '').strip()
        if not 1 <= len(title) <= 80:
            raise errors.RequestError(msg='群名称需 1..80 字')
        if agent_policy not in VALID_AGENT_POLICY:
            raise errors.RequestError(msg=f'agent_policy 非法: {agent_policy}')
        if join_policy not in VALID_JOIN_POLICY:
            raise errors.RequestError(msg=f'join_policy 非法: {join_policy}')

        member_ids: list[str] = []
        seen: set[str] = {owner_hasn_id}
        for m in members or []:
            mid = (m.get('hasn_id') or '').strip()
            if mid and mid not in seen:
                seen.add(mid)
                member_ids.append(mid)

        now = timezone.now()
        group_id = await cls._alloc_group_id(db)
        owner_type, _, _ = await cls._resolve_meta(db, owner_hasn_id)

        conv = HasnConversations(
            type='group',
            group_id=group_id,
            group_name=title,
            group_owner_id=owner_hasn_id,
            group_avatar_url=avatar_url,
            agent_policy=agent_policy,
            join_policy=join_policy,
            max_members=_MAX_MEMBERS_DEFAULT,
            allow_invite=True,
            allow_member_invite_agent=True,
            mute_all=False,
            participant_a_id=owner_hasn_id,
            participant_a_type=owner_type,
            relation_type='social',
            status='active',
            member_count=0,
        )
        db.add(conv)
        await db.flush()

        roster: list[HasnGroupMembers] = []
        await cls._add_member_row(
            db, conv.id, owner_hasn_id, role='owner', invited_by=None, now=now, roster=roster
        )
        for mid in member_ids:
            await cls._add_member_row(
                db, conv.id, mid, role='member', invited_by=owner_hasn_id, now=now, roster=roster
            )
        conv.member_count = len(roster)
        await db.flush()
        return cls._group_to_dict(conv, roster)

    @classmethod
    async def list_my_groups(cls, db: AsyncSession, *, hasn_id: str) -> list[dict[str, Any]]:
        """列出 hasn_id 作为成员的活跃群（摘要 + 头像预览名册，不含完整名册）。

        每个群附带 `members_preview`（按入群时间取前 `_AVATAR_PREVIEW_LIMIT` 个成员），
        供 WebUI 在消息列表拼九宫格群头像；完整名册仍需走 `get_group_detail`。
        另附 `agent_member_count`/`agent_policy_effective`——供列表徽标不必进详情即知生效规则。
        """
        conv_ids = (
            (
                await db.execute(
                    select(HasnGroupMembers.conversation_id).where(
                        HasnGroupMembers.member_id == hasn_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not conv_ids:
            return []
        convs = (
            (
                await db.execute(
                    select(HasnConversations).where(
                        HasnConversations.id.in_(conv_ids),
                        HasnConversations.type == 'group',
                        HasnConversations.status == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        # 单次批量取所有群成员（避免逐群 N+1），按入群时间升序便于取前几个作头像。
        active_ids = [c.id for c in convs]
        preview_by_conv: dict[Any, list[HasnGroupMembers]] = {}
        agent_count_by_conv: dict[Any, int] = {}
        if active_ids:
            roster_rows = (
                (
                    await db.execute(
                        select(HasnGroupMembers)
                        .where(HasnGroupMembers.conversation_id.in_(active_ids))
                        .order_by(HasnGroupMembers.joined_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            for m in roster_rows:
                bucket = preview_by_conv.setdefault(m.conversation_id, [])
                if len(bucket) < _AVATAR_PREVIEW_LIMIT:
                    bucket.append(m)
                # 全量统计分身成员数（不受预览上限截断），供生效策略派生。
                if m.member_type == 'agent':
                    agent_count_by_conv[m.conversation_id] = agent_count_by_conv.get(m.conversation_id, 0) + 1
        result: list[dict[str, Any]] = []
        for c in convs:
            data = cls._group_to_dict(c, agent_member_count=agent_count_by_conv.get(c.id, 0))
            data['members_preview'] = [
                cls._member_to_dict(m) for m in preview_by_conv.get(c.id, [])
            ]
            result.append(data)
        return result

    @classmethod
    async def get_group_detail(
        cls, db: AsyncSession, *, hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        """群详情 + 名册。要求 hasn_id 是群成员。

        charter 白名单：名册行仅当「该行是 actor 名下分身」才回填 agent_charter；
        附 agent_policy_effective/agent_member_count + pending 拉分身邀请列表。
        """
        conv = await cls._get_group_or_404(db, group_id)
        members = await cls._load_members(db, conv.id)
        if cls._role_of(members, hasn_id) is None:
            raise errors.ForbiddenError(msg='非群成员，无权查看')
        owner_map = await cls._build_owner_map(db, members)
        owner_name_map = await cls._build_owner_name_map(db, set(owner_map.values()))
        await cls._expire_stale_invites(db, conv.id)
        pending = await cls._pending_invites(db, conv.id)
        return cls._group_to_dict(
            conv,
            members,
            actor_hasn_id=hasn_id,
            owner_map=owner_map,
            owner_name_map=owner_name_map,
            pending_invites=pending,
        )

    @classmethod
    async def add_members(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """加成员（任意群成员可加人）。

        拉分身两道叠加闸（doc10 §3.1/§3.2）：
        - ① 发起闸：待加成员含 agent 且 `allow_member_invite_agent=false` 且 actor 非 owner/admin → 403；
        - ② 主人同意分叉：发起人 ≠ 分身主人 → 不插名册，落 pending 邀请 + 发主人确认卡；发起人 = 主人 → 即时入群。
        拉人类成员完全不受影响（维持「任意成员可拉人」现状）。
        """
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        if cls._role_of(roster, actor_hasn_id) is None:
            raise errors.ForbiddenError(msg='非群成员，无权加人')
        actor_is_admin = cls._role_of(roster, actor_hasn_id) in _ADMIN_ROLES
        allow_flag = bool(conv.allow_member_invite_agent)
        existing_ids = {m.member_id for m in roster}
        now = timezone.now()
        added: list[str] = []
        invited: list[dict[str, Any]] = []

        for m in members or []:
            mid = (m.get('hasn_id') or '').strip()
            if not mid or mid in existing_ids:
                continue  # 空 / 已是成员 → 幂等跳过
            if mid.startswith('a_'):
                # ① 发起闸
                if not allow_flag and not actor_is_admin:
                    raise errors.ForbiddenError(msg='群主已限制普通成员添加分身，请联系群主或管理员')
                agent_owner = await cls._agent_owner_id(db, mid)
                # ② 主人同意分叉：非主人发起（含群主/管理员）一律走邀请确认
                if agent_owner and agent_owner != actor_hasn_id:
                    # 幂等：已有 pending 邀请 → 跳过（不重复发起）
                    dup = (
                        await db.execute(
                            select(HasnGroupAgentInvites).where(
                                HasnGroupAgentInvites.conversation_id == conv.id,
                                HasnGroupAgentInvites.agent_hasn_id == mid,
                                HasnGroupAgentInvites.status == INVITE_PENDING,
                            )
                        )
                    ).scalar_one_or_none()
                    if dup is not None:
                        continue
                    inv = HasnGroupAgentInvites(
                        conversation_id=conv.id,
                        group_id=conv.group_id,
                        agent_hasn_id=mid,
                        agent_owner_id=agent_owner,
                        inviter_id=actor_hasn_id,
                        status=INVITE_PENDING,
                    )
                    db.add(inv)
                    await db.flush()
                    agent_name = await cls._resolve_display_name(db, mid)
                    inviter_name = await cls._resolve_display_name(db, actor_hasn_id)
                    await cls._send_agent_invite_card(
                        invite_id=inv.id,
                        group_public_id=conv.group_id,
                        group_name=conv.group_name or conv.group_id,
                        agent_hasn_id=mid,
                        agent_name=agent_name,
                        agent_owner_id=agent_owner,
                        inviter_id=actor_hasn_id,
                        inviter_name=inviter_name,
                    )
                    invited.append({'agent_hasn_id': mid, 'invite_id': inv.id, 'agent_owner_id': agent_owner})
                    continue
                # 分身主人本人拉自己的分身（或无主人的历史 agent）→ 即时入群
            existing_ids.add(mid)
            await cls._add_member_row(
                db, conv.id, mid, role='member', invited_by=actor_hasn_id, now=now, roster=roster
            )
            added.append(mid)

        conv.member_count = len(roster)
        await db.flush()
        if added:  # 成员变更可能翻转生效策略 → 广播刷新
            await cls._notify_group_members(db, roster)
        data = cls._group_to_dict(conv, roster)
        data['invited_agents'] = invited  # 走邀请流程、未即时入群的分身
        return data

    @classmethod
    async def remove_member(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str, member_id: str
    ) -> dict[str, Any]:
        """踢人（owner/admin）/ 自己退群 / 主人替自己的分身退群。群主不可被移除。"""
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        actor_role = cls._role_of(roster, actor_hasn_id)
        if actor_role is None:
            raise errors.ForbiddenError(msg='非群成员')
        target = next((m for m in roster if m.member_id == member_id), None)
        if not target:
            raise errors.NotFoundError(msg='成员不存在')
        if member_id == conv.group_owner_id:
            raise errors.RequestError(msg='群主不可被移除，请先转让或解散群')
        # 放行分支：自己退群 / owner-admin 踢人 / 主人替自己的分身撤出本群（doc10 §5.1）
        if actor_hasn_id != member_id and actor_role not in _ADMIN_ROLES:
            allowed = False
            if target.member_type == 'agent':
                target_owner = await cls._agent_owner_id(db, member_id)
                allowed = target_owner == actor_hasn_id
            if not allowed:
                raise errors.ForbiddenError(msg='仅群主/管理员可移除成员')
        await db.delete(target)
        conv.member_count = max(0, (conv.member_count or 1) - 1)
        await db.flush()
        # 减员可能恢复生效策略（2→1 分身回到 free）→ 广播刷新（含被移除成员 owner，故用移除前 roster）
        await cls._notify_group_members(db, roster)
        return {'group_id': group_id, 'removed': member_id, 'member_count': conv.member_count}

    @classmethod
    async def update_group(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        title: str | None = None,
        avatar_url: str | None = None,
        agent_policy: str | None = None,
        join_policy: str | None = None,
        allow_member_invite_agent: bool | None = None,
    ) -> dict[str, Any]:
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        if cls._role_of(roster, actor_hasn_id) not in _ADMIN_ROLES:
            raise errors.ForbiddenError(msg='仅群主/管理员可改群设置')
        changed = False
        if title is not None:
            t = title.strip()
            if not 1 <= len(t) <= 80:
                raise errors.RequestError(msg='群名称需 1..80 字')
            conv.group_name = t
        if avatar_url is not None:
            conv.group_avatar_url = avatar_url
        if agent_policy is not None:
            if agent_policy not in VALID_AGENT_POLICY:
                raise errors.RequestError(msg=f'agent_policy 非法: {agent_policy}')
            # 多分身群设置闸（doc10 §2.2）：目标 free 且当前分身数 > 1 → 拒绝
            if agent_policy == 'free' and _agent_member_count(roster) > 1:
                raise errors.RequestError(msg='群内有多个分身时，分身必须被 @ 才会回复，无法设置为自由发言')
            conv.agent_policy = agent_policy
            changed = True
        if join_policy is not None:
            if join_policy not in VALID_JOIN_POLICY:
                raise errors.RequestError(msg=f'join_policy 非法: {join_policy}')
            conv.join_policy = join_policy
        if allow_member_invite_agent is not None:
            conv.allow_member_invite_agent = bool(allow_member_invite_agent)
            changed = True
        await db.flush()
        if changed:  # 发言策略 / 拉分身开关变更 → 广播刷新
            await cls._notify_group_members(db, roster)
        return cls._group_to_dict(conv, roster)

    # ─── doc10：分身群内发言准则（charter）───
    @classmethod
    async def set_agent_charter(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        agent_hasn_id: str,
        charter: str | None,
    ) -> dict[str, Any]:
        """写/清分身本群发言准则（doc10 §4.3）。

        校验：actor 是该分身主人 + 分身是本群成员 + 长度 ≤ 4000。null/空串 = 清除。
        """
        conv = await cls._get_group_or_404(db, group_id)
        owner = await cls._agent_owner_id(db, agent_hasn_id)
        if owner != actor_hasn_id:
            raise errors.ForbiddenError(msg='只有分身的主人才能设置其发言准则')
        member = (
            await db.execute(
                select(HasnGroupMembers).where(
                    HasnGroupMembers.conversation_id == conv.id,
                    HasnGroupMembers.member_id == agent_hasn_id,
                    HasnGroupMembers.member_type == 'agent',
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise errors.NotFoundError(msg='该分身不在本群')
        normalized = (charter or '').strip()
        if len(normalized) > CHARTER_MAX_LEN:
            raise errors.RequestError(msg=f'发言准则不能超过 {CHARTER_MAX_LEN} 字')
        member.agent_charter = normalized or None
        member.charter_updated_time = timezone.now()
        await db.flush()
        return {
            'group_id': group_id,
            'agent_hasn_id': agent_hasn_id,
            'agent_charter': member.agent_charter,
            'charter_updated_time': (
                member.charter_updated_time.isoformat() if member.charter_updated_time else None
            ),
        }

    # ─── doc08 §3.4 RT2.5：分身群内披露档（group trust level）───
    @classmethod
    async def set_agent_group_trust_level(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        group_id: str,
        agent_hasn_id: str,
        trust_level: int,
    ) -> dict[str, Any]:
        """设分身本群披露档（doc08 §3.4·D9）。

        校验：actor 是该分身主人 + 分身是本群成员 + 档位 ∈ {2,3,4}。
        权限与拦截策略与 social 同档一致（语义复用 Core/04 信任等级 2/3/4）。
        """
        conv = await cls._get_group_or_404(db, group_id)
        owner = await cls._agent_owner_id(db, agent_hasn_id)
        if owner != actor_hasn_id:
            raise errors.ForbiddenError(msg='只有分身的主人才能设置其群内披露档')
        member = (
            await db.execute(
                select(HasnGroupMembers).where(
                    HasnGroupMembers.conversation_id == conv.id,
                    HasnGroupMembers.member_id == agent_hasn_id,
                    HasnGroupMembers.member_type == 'agent',
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise errors.NotFoundError(msg='该分身不在本群')
        if trust_level not in GROUP_TRUST_LEVELS:
            raise errors.RequestError(msg=f'披露档位非法，仅支持 {GROUP_TRUST_LEVELS}（普通朋友/好友/密友）')
        member.agent_group_trust_level = trust_level
        await db.flush()
        return {
            'group_id': group_id,
            'agent_hasn_id': agent_hasn_id,
            'agent_group_trust_level': member.agent_group_trust_level,
        }

    # ─── doc10：拉分身邀请裁决 ───
    @classmethod
    async def _get_invite_or_404(
        cls, db: AsyncSession, conv_id: Any, invite_id: int
    ) -> HasnGroupAgentInvites:
        inv = (
            await db.execute(
                select(HasnGroupAgentInvites).where(
                    HasnGroupAgentInvites.id == invite_id,
                    HasnGroupAgentInvites.conversation_id == conv_id,
                )
            )
        ).scalar_one_or_none()
        if inv is None:
            raise errors.NotFoundError(msg='邀请不存在')
        return inv

    @classmethod
    async def accept_agent_invite(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str, invite_id: int
    ) -> dict[str, Any]:
        """分身主人同意邀请 → 插名册 + 广播（含 effective 翻转）。仅 agent_owner 可 accept。"""
        conv = await cls._get_group_or_404(db, group_id)
        inv = await cls._get_invite_or_404(db, conv.id, invite_id)
        if inv.agent_owner_id != actor_hasn_id:
            raise errors.ForbiddenError(msg='只有分身的主人才能处理此邀请')
        if inv.status != INVITE_PENDING:
            raise errors.RequestError(msg=f'邀请已处理（{inv.status}）')
        now = timezone.now()
        if inv.created_time and inv.created_time.timestamp() < now.timestamp() - _INVITE_EXPIRE_DAYS * 86400:
            inv.status = INVITE_EXPIRED
            inv.resolved_time = now
            raise errors.RequestError(msg='邀请已过期，请重新发起')
        roster = await cls._load_members(db, conv.id)
        if cls._role_of(roster, inv.agent_hasn_id) is None:
            await cls._add_member_row(
                db, conv.id, inv.agent_hasn_id, role='member', invited_by=inv.inviter_id, now=now, roster=roster
            )
            conv.member_count = len(roster)
        inv.status = INVITE_ACCEPTED
        inv.resolved_time = now
        await db.flush()
        await cls._notify_group_members(db, roster)
        return {'group_id': group_id, 'invite_id': invite_id, 'status': INVITE_ACCEPTED, 'joined': True}

    @classmethod
    async def decline_agent_invite(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str, invite_id: int
    ) -> dict[str, Any]:
        """分身主人拒绝邀请。仅 agent_owner 可 decline。"""
        conv = await cls._get_group_or_404(db, group_id)
        inv = await cls._get_invite_or_404(db, conv.id, invite_id)
        if inv.agent_owner_id != actor_hasn_id:
            raise errors.ForbiddenError(msg='只有分身的主人才能处理此邀请')
        if inv.status != INVITE_PENDING:
            raise errors.RequestError(msg=f'邀请已处理（{inv.status}）')
        inv.status = INVITE_DECLINED
        inv.resolved_time = timezone.now()
        await db.flush()
        return {'group_id': group_id, 'invite_id': invite_id, 'status': INVITE_DECLINED, 'joined': False}

    @classmethod
    async def cancel_agent_invite(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str, invite_id: int
    ) -> dict[str, Any]:
        """发起人取消自己发出的 pending 邀请。仅 inviter 可取消。"""
        conv = await cls._get_group_or_404(db, group_id)
        inv = await cls._get_invite_or_404(db, conv.id, invite_id)
        if inv.inviter_id != actor_hasn_id:
            raise errors.ForbiddenError(msg='只有发起人才能取消此邀请')
        if inv.status != INVITE_PENDING:
            raise errors.RequestError(msg=f'邀请已处理（{inv.status}）')
        inv.status = INVITE_CANCELLED
        inv.resolved_time = timezone.now()
        await db.flush()
        return {'group_id': group_id, 'invite_id': invite_id, 'status': INVITE_CANCELLED}

    @classmethod
    async def sweep_expired_invites(cls, db: AsyncSession) -> int:
        """全局 sweep：把所有超 7 天未处理的 pending 邀请置 expired（celery beat 兜底）。返回处理数。"""
        now = timezone.now()
        cutoff = now.timestamp() - _INVITE_EXPIRE_DAYS * 86400
        rows = (
            await db.execute(
                select(HasnGroupAgentInvites).where(HasnGroupAgentInvites.status == INVITE_PENDING)
            )
        ).scalars().all()
        count = 0
        for inv in rows:
            if inv.created_time and inv.created_time.timestamp() < cutoff:
                inv.status = INVITE_EXPIRED
                inv.resolved_time = now
                count += 1
        if count:
            await db.flush()
        return count

    # ─── doc22 群名片：公开元信息 + 自助入群 ───
    @classmethod
    async def get_group_public_meta(
        cls, db: AsyncSession, *, viewer_hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        """群公开元信息（**非成员亦可读**，供群名片预览页 doc22 §6.2）。

        只返回群名 / 头像 / 人数 / 加入策略等公开字段，**不含完整名册**（名册须成员方可见）。
        额外附 `is_member`/`my_role`：供预览页对 viewer 分叉「加入群聊 / 进入群聊」按钮。
        """
        conv = await cls._get_group_or_404(db, group_id)
        members = await cls._load_members(db, conv.id)
        my_role = cls._role_of(members, viewer_hasn_id)
        return {
            'group_id': conv.group_id,
            'title': conv.group_name,
            'avatar_url': conv.group_avatar_url,
            'member_count': conv.member_count,
            'join_policy': conv.join_policy,
            'agent_policy': conv.agent_policy,
            'agent_policy_effective': effective_agent_policy(conv.agent_policy, _agent_member_count(members)),
            'agent_member_count': _agent_member_count(members),
            'status': conv.status,
            'is_member': my_role is not None,
            'my_role': my_role,
        }

    @classmethod
    async def join_group(
        cls, db: AsyncSession, *, applicant_hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        """自助入群（doc22 §6.5 · hasn.group.join 底层）——**尊重群加入策略 + 拉分身发起闸**。

        - 已是成员 → `already_member`（幂等）；
        - 分身自助入群（applicant=a_）视同「其主人以普通成员身份拉分身」：`allow_member_invite_agent=false`
          且主人非 owner/admin → 拒绝（doc10 §3.1）；
        - `open` 策略 → 直接加为成员（`joined`）；
        - 其余（`invite_only`/`approval`）→ 如实回 `needs_approval`。
        """
        conv = await cls._get_group_or_404(db, group_id)
        roster = await cls._load_members(db, conv.id)
        existing_role = cls._role_of(roster, applicant_hasn_id)
        if existing_role is not None:
            return {
                'group_id': group_id,
                'status': 'already_member',
                'joined': True,
                'member_count': conv.member_count,
                'role': existing_role,
            }
        # 拉分身发起闸：分身自助入群 = 其主人以普通成员身份拉分身
        if applicant_hasn_id.startswith('a_') and not conv.allow_member_invite_agent:
            owner = await cls._agent_owner_id(db, applicant_hasn_id)
            owner_role = cls._role_of(roster, owner) if owner else None
            if owner_role not in _ADMIN_ROLES:
                raise errors.ForbiddenError(msg='群主已限制普通成员添加分身，请联系群主或管理员')
        if conv.join_policy in _OPEN_JOIN_POLICIES:
            now = timezone.now()
            await cls._add_member_row(
                db, conv.id, applicant_hasn_id, role='member', invited_by=None, now=now, roster=roster
            )
            conv.member_count = len(roster)
            await db.flush()
            await cls._notify_group_members(db, roster)
            return {
                'group_id': group_id,
                'status': 'joined',
                'joined': True,
                'member_count': conv.member_count,
                'role': 'member',
            }
        return {
            'group_id': group_id,
            'status': 'needs_approval',
            'joined': False,
            'join_policy': conv.join_policy,
            'message': '该群为邀请制，需群主/管理员邀请或审批后加入',
        }

    @classmethod
    async def disband_group(
        cls, db: AsyncSession, *, actor_hasn_id: str, group_id: str
    ) -> dict[str, Any]:
        conv = await cls._get_group_or_404(db, group_id)
        if actor_hasn_id != conv.group_owner_id:
            raise errors.ForbiddenError(msg='仅群主可解散群')
        conv.status = 'disbanded'
        await db.flush()
        return {'group_id': group_id, 'status': 'disbanded'}


hasn_group_service: HasnGroupService = HasnGroupService()
