"""会议副驾（潜行会议副驾）云端数据底座业务服务（P2）。

owner 硬隔离：`copilot_session` / `copilot_preference` 一律以 `owner_hasn_id == <jwt owner>` 过滤，
**不引入** deck 的 resource_share / 企业可见 / ACL 复杂度——副驾数据是 owner 私有的实时会议元数据。

核心业务（设计事实源 §8.4.2 / §8.5）：
- **session_id 客户端生成 + upsert**：daemon 端生成 `session_id`（任务工作会话 id，UNIQUE），
  支持离线起会后联网 upsert（按 session_id 命中则更新、否则插入），避免离线对象联网后产生孤儿/重复行。
- **协作分身 owner 校验**：写入 `bound_agent_id` / `default_agent_id` 前校验该分身归本 owner（同 deck）。
- **bind-only-if-unbound（§8.5.1）**：首次绑定（preference.default_agent_id 为空）→ 选定分身后
  ① 写本次 session.bound_agent_id ② 回写 preference.default_agent_id 作为今后默认；
  后续新会话 bound_agent_id 默认取 preference.default_agent_id。
- **response_mode session vs preference 独立（§8.4.2）**：改 session.response_mode 不回写
  preference.default_response_mode（会内临时档 vs 全局长效默认，二者解耦）。
- **projection 回填**：结束时回填 projection_conversation_id / projection_message_id（投影卡片落点）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from backend.app.hasn_core import identity
from backend.app.hasn_copilot.model import CopilotPreference, CopilotSession
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 应答模式字典（§8.4.2）：会话级与 owner 默认共用。
_RESPONSE_MODES = ('auto', 'manual', 'transcribe_only')
# 场景字典（§8.4.2）。
_SCENES = ('meeting', 'interview', 'call', 'lecture')
# 会话生命周期。
_STATUSES = ('active', 'ended')


def _session_dict(s: CopilotSession) -> dict[str, Any]:
    return {
        'id': s.id,
        'owner_hasn_id': s.owner_hasn_id,
        'session_id': s.session_id,
        'bound_agent_id': s.bound_agent_id,
        'title': s.title,
        'scene': s.scene,
        'response_mode': s.response_mode,
        'status': s.status,
        'source_config': s.source_config,
        'projection_conversation_id': str(s.projection_conversation_id) if s.projection_conversation_id else None,
        'projection_message_id': str(s.projection_message_id) if s.projection_message_id else None,
        'started_time': s.started_time,
        'ended_time': s.ended_time,
        'created_time': s.created_time,
        'updated_time': s.updated_time,
    }


def _preference_dict(p: CopilotPreference) -> dict[str, Any]:
    return {
        'owner_hasn_id': p.owner_hasn_id,
        'default_agent_id': p.default_agent_id,
        'default_response_mode': p.default_response_mode,
        'auto_summary': p.auto_summary,
        'created_time': p.created_time,
        'updated_time': p.updated_time,
    }


def _to_uuid(value: str | UUID | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise errors.RequestError(msg='非法 UUID 值') from exc


class CopilotService:
    """会议副驾 owner-scoped 服务。所有访问方法第一参数 db，owner_hasn_id 为归属隔离键。"""

    # ---------- 协作分身 owner 校验（§8.5，同 deck） ----------

    @staticmethod
    async def _validate_agent_owner(db: AsyncSession, *, owner_hasn_id: str, agent_id: str) -> None:
        """校验 agent_id 是本 owner 名下分身；不是则拒绝（404 不泄露他人分身是否存在）。"""
        agent = await identity.agent_owned_by(
            db, hasn_id=agent_id, owner_hasn_id=owner_hasn_id, require_active=False
        )
        if agent is None:
            raise errors.NotFoundError(msg='指定的协作分身不存在或不属于你')

    @staticmethod
    def _check_response_mode(mode: str | None) -> None:
        if mode is not None and mode not in _RESPONSE_MODES:
            raise errors.RequestError(msg='非法应答模式')

    @staticmethod
    def _check_scene(scene: str | None) -> None:
        if scene is not None and scene not in _SCENES:
            raise errors.RequestError(msg='非法场景')

    # ---------- preference（owner 级单行） ----------

    @staticmethod
    async def _get_preference_row(db: AsyncSession, owner_hasn_id: str) -> CopilotPreference | None:
        return (
            await db.execute(select(CopilotPreference).where(CopilotPreference.owner_hasn_id == owner_hasn_id))
        ).scalar_one_or_none()

    @staticmethod
    async def get_preference(db: AsyncSession, *, owner_hasn_id: str) -> dict[str, Any]:
        """取 owner 副驾偏好；无行则返回出厂默认（不落库，读路径零副作用）。"""
        pref = await CopilotService._get_preference_row(db, owner_hasn_id)
        if pref is None:
            return {
                'owner_hasn_id': owner_hasn_id,
                'default_agent_id': None,
                'default_response_mode': 'manual',
                'auto_summary': True,
                'created_time': None,
                'updated_time': None,
            }
        return _preference_dict(pref)

    @staticmethod
    async def _ensure_preference_row(db: AsyncSession, owner_hasn_id: str) -> CopilotPreference:
        pref = await CopilotService._get_preference_row(db, owner_hasn_id)
        if pref is None:
            pref = CopilotPreference(owner_hasn_id=owner_hasn_id)
            db.add(pref)
            await db.flush()
        return pref

    @staticmethod
    async def update_preference(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        default_agent_id: str | None = None,
        default_response_mode: str | None = None,
        auto_summary: bool | None = None,
    ) -> dict[str, Any]:
        """更新 owner 副驾偏好（upsert 单行）。default_agent_id 写入前校验归属。

        这是「改默认」的权威入口——改 preference.default_response_mode 影响今后默认（§8.4.2），
        与某场 session.response_mode 的会内临时切换无关。
        """
        CopilotService._check_response_mode(default_response_mode)
        if default_agent_id is not None:
            await CopilotService._validate_agent_owner(db, owner_hasn_id=owner_hasn_id, agent_id=default_agent_id)
        pref = await CopilotService._ensure_preference_row(db, owner_hasn_id)
        if default_agent_id is not None:
            pref.default_agent_id = default_agent_id
        if default_response_mode is not None:
            pref.default_response_mode = default_response_mode
        if auto_summary is not None:
            pref.auto_summary = auto_summary
        pref.updated_time = timezone.now()
        await db.flush()
        return _preference_dict(pref)

    @staticmethod
    async def rebind_default_agent(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_id: str,
        also_session_id: str | None = None,
    ) -> dict[str, Any]:
        """改绑默认协作分身（§8.5.1 改绑路径，二次确认后由 daemon 调）。

        校验该分身归本 owner → 写 preference.default_agent_id；可选同时改某场 session 的 bound_agent_id。
        """
        await CopilotService._validate_agent_owner(db, owner_hasn_id=owner_hasn_id, agent_id=agent_id)
        pref = await CopilotService._ensure_preference_row(db, owner_hasn_id)
        pref.default_agent_id = agent_id
        pref.updated_time = timezone.now()
        result: dict[str, Any] = {'preference': _preference_dict(pref)}
        if also_session_id is not None:
            session = await CopilotService._get_session_row(db, owner_hasn_id=owner_hasn_id, session_id=also_session_id)
            if session is None:
                raise errors.NotFoundError(msg='副驾会话不存在')
            session.bound_agent_id = agent_id
            session.updated_time = timezone.now()
            result['session'] = _session_dict(session)
        await db.flush()
        return result

    # ---------- session（owner 隔离 + session_id upsert） ----------

    @staticmethod
    async def _get_session_row(db: AsyncSession, *, owner_hasn_id: str, session_id: str) -> CopilotSession | None:
        return (
            await db.execute(
                select(CopilotSession).where(
                    CopilotSession.session_id == session_id,
                    CopilotSession.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def upsert_session(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        session_id: str,
        bound_agent_id: str | None = None,
        title: str | None = None,
        scene: str | None = None,
        response_mode: str | None = None,
        status: str | None = None,
        source_config: dict | None = None,
        started_time: Any = None,
    ) -> dict[str, Any]:
        """按 session_id upsert 副驾会话（客户端生成 session_id，离线起会联网补登）。

        - 命中（owner + session_id）→ 更新提供的字段；否则插入新行。
        - bind-only-if-unbound（§8.5.1）：
          * 显式传 bound_agent_id → 校验归属并用之；
          * 未传且本行尚未绑定 → 默认取 preference.default_agent_id；
          * 首次为本 owner 确定绑定分身（preference.default_agent_id 为空）→ 回写 preference.default_agent_id。
        """
        CopilotService._check_response_mode(response_mode)
        CopilotService._check_scene(scene)
        if status is not None and status not in _STATUSES:
            raise errors.RequestError(msg='非法会话状态')

        # 解析待绑定分身：显式 > 既有 > preference 默认。
        pref = await CopilotService._get_preference_row(db, owner_hasn_id)
        existing = await CopilotService._get_session_row(db, owner_hasn_id=owner_hasn_id, session_id=session_id)

        resolved_agent: str | None
        if bound_agent_id is not None:
            await CopilotService._validate_agent_owner(db, owner_hasn_id=owner_hasn_id, agent_id=bound_agent_id)
            resolved_agent = bound_agent_id
        elif existing is not None and existing.bound_agent_id:
            resolved_agent = existing.bound_agent_id
        else:
            resolved_agent = pref.default_agent_id if pref else None

        if existing is None:
            session = CopilotSession(
                owner_hasn_id=owner_hasn_id,
                session_id=session_id,
                bound_agent_id=resolved_agent,
                title=title or '',
                scene=scene or 'meeting',
                response_mode=response_mode or (pref.default_response_mode if pref else 'manual'),
                status=status or 'active',
                source_config=source_config if source_config is not None else {},
                started_time=started_time,
            )
            db.add(session)
        else:
            session = existing
            session.bound_agent_id = resolved_agent
            if title is not None:
                session.title = title
            if scene is not None:
                session.scene = scene
            if response_mode is not None:
                session.response_mode = response_mode
            if status is not None:
                session.status = status
            if source_config is not None:
                session.source_config = source_config
            if started_time is not None:
                session.started_time = started_time
            session.updated_time = timezone.now()

        # bind-only-if-unbound：本 owner 首次确定绑定分身 → 回写默认。
        if resolved_agent and (pref is None or not pref.default_agent_id):
            pref = await CopilotService._ensure_preference_row(db, owner_hasn_id)
            pref.default_agent_id = resolved_agent
            pref.updated_time = timezone.now()

        await db.flush()
        return _session_dict(session)

    @staticmethod
    async def get_session(db: AsyncSession, *, owner_hasn_id: str, session_id: str) -> dict[str, Any]:
        session = await CopilotService._get_session_row(db, owner_hasn_id=owner_hasn_id, session_id=session_id)
        if session is None:
            raise errors.NotFoundError(msg='副驾会话不存在')
        return _session_dict(session)

    @staticmethod
    async def list_sessions(
        db: AsyncSession, *, owner_hasn_id: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """owner 私有副驾会话列表（按 created_time DESC，对齐 idx_copilot_session_owner）。"""
        base = select(CopilotSession).where(CopilotSession.owner_hasn_id == owner_hasn_id)
        rows = (
            (
                await db.execute(
                    base
                    .order_by(CopilotSession.created_time.desc(), CopilotSession.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = (await db.execute(select(CopilotSession.id).where(CopilotSession.owner_hasn_id == owner_hasn_id))).all()
        return {'items': [_session_dict(s) for s in rows], 'total': len(total)}

    @staticmethod
    async def update_session(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        session_id: str,
        bound_agent_id: str | None = None,
        title: str | None = None,
        scene: str | None = None,
        response_mode: str | None = None,
        status: str | None = None,
        source_config: dict | None = None,
    ) -> dict[str, Any]:
        """更新某场会话（owner 隔离）。改 response_mode 仅改本场（§8.4.2，**不回写** preference.default）。

        改 session.bound_agent_id 前校验归属；这是「会内临时切」语义，不影响 owner 默认。
        """
        CopilotService._check_response_mode(response_mode)
        CopilotService._check_scene(scene)
        if status is not None and status not in _STATUSES:
            raise errors.RequestError(msg='非法会话状态')
        session = await CopilotService._get_session_row(db, owner_hasn_id=owner_hasn_id, session_id=session_id)
        if session is None:
            raise errors.NotFoundError(msg='副驾会话不存在')
        if bound_agent_id is not None:
            await CopilotService._validate_agent_owner(db, owner_hasn_id=owner_hasn_id, agent_id=bound_agent_id)
            session.bound_agent_id = bound_agent_id
        if title is not None:
            session.title = title
        if scene is not None:
            session.scene = scene
        if response_mode is not None:
            session.response_mode = response_mode  # 仅本场，绝不回写 preference.default_response_mode
        if status is not None:
            session.status = status
        if source_config is not None:
            session.source_config = source_config
        session.updated_time = timezone.now()
        await db.flush()
        return _session_dict(session)

    @staticmethod
    async def set_projection(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        session_id: str,
        projection_conversation_id: str | UUID,
        projection_message_id: str | UUID,
        end_session: bool = True,
    ) -> dict[str, Any]:
        """结束投影回填（§8.4.2）：记录完成时投影卡片落在主会话哪条消息，便于「点卡片导航回工作会话」。

        默认同时把会话置 ended + 写 ended_time（投影即会议完成）。
        """
        session = await CopilotService._get_session_row(db, owner_hasn_id=owner_hasn_id, session_id=session_id)
        if session is None:
            raise errors.NotFoundError(msg='副驾会话不存在')
        session.projection_conversation_id = _to_uuid(projection_conversation_id)
        session.projection_message_id = _to_uuid(projection_message_id)
        if end_session:
            session.status = 'ended'
            if session.ended_time is None:
                session.ended_time = timezone.now()
        session.updated_time = timezone.now()
        await db.flush()
        return _session_dict(session)


copilot_service = CopilotService()
