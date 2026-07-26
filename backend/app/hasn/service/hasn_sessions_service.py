import json

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.app.hasn.crud.crud_hasn_sessions import hasn_sessions_dao
from backend.app.hasn.model import HasnSessions
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.schema.hasn_sessions import (
    CreateHasnSessionsParam,
    DeleteHasnSessionsParam,
    UpdateHasnSessionsParam,
)
from backend.app.hasn.service.hasn_conversations_service import hasn_conversations_service
from backend.app.hasn_im.application.errors import ImSendRejected
from backend.app.hasn_im.application.provider import get_im_gateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    DeliveryState,
    EnsureDirectConversationCommand,
    SendMessageCommand,
    SendMessageResult,
    ServicePrincipal,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


class HasnSessionsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSessions:
        """
        获取HASN 会话分层 - 逻辑会话

        :param db: 数据库会话
        :param pk: HASN 会话分层 - 逻辑会话 ID
        :return:
        """
        hasn_sessions = await hasn_sessions_dao.get(db, pk)
        if not hasn_sessions:
            raise errors.NotFoundError(msg='HASN 会话分层 - 逻辑会话不存在')
        return hasn_sessions

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 会话分层 - 逻辑会话列表

        :param db: 数据库会话
        :return:
        """
        hasn_sessions_select = await hasn_sessions_dao.get_select()
        return await paging_data(db, hasn_sessions_select)

    @staticmethod
    async def get_list_by_owner(
        db: AsyncSession,
        owner_id: str,
        *,
        session_kind: str | None = None,
        session_scope: str | None = None,
        session_status: str | None = None,
        hasn_id: str | None = None,
        project_id: UUID | str | None = None,
        origin_type: str | None = None,
        origin_ref: str | None = None,
    ) -> dict[str, Any]:
        """获取当前 owner 可见的工作会话投影列表。"""
        stmt = select(HasnSessions).where(HasnSessions.owner_id == owner_id)
        stmt = _apply_csv_filter(stmt, HasnSessions.session_kind, session_kind)
        stmt = _apply_csv_filter(stmt, HasnSessions.session_scope, session_scope)
        stmt = _apply_csv_filter(stmt, HasnSessions.session_status, session_status)
        if hasn_id:
            stmt = stmt.where(HasnSessions.hasn_id == hasn_id)
        if project_id is not None:
            stmt = stmt.where(HasnSessions.project_id == project_id)
        if origin_type:
            stmt = stmt.where(HasnSessions.origin_type == origin_type)
        if origin_ref:
            stmt = stmt.where(HasnSessions.origin_ref == origin_ref)
        stmt = stmt.order_by(HasnSessions.updated_time.desc().nullslast(), HasnSessions.created_time.desc())
        return await paging_data(db, stmt)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSessions]:
        """
        获取所有HASN 会话分层 - 逻辑会话

        :param db: 数据库会话
        :return:
        """
        hasn_sessions_list = await hasn_sessions_dao.get_all(db)
        return hasn_sessions_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSessionsParam) -> None:
        """
        创建HASN 会话分层 - 逻辑会话

        :param db: 数据库会话
        :param obj: 创建HASN 会话分层 - 逻辑会话参数
        :return:
        """
        await hasn_sessions_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSessionsParam) -> int:
        """
        更新HASN 会话分层 - 逻辑会话

        :param db: 数据库会话
        :param pk: HASN 会话分层 - 逻辑会话 ID
        :param obj: 更新HASN 会话分层 - 逻辑会话参数
        :return:
        """
        count = await hasn_sessions_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSessionsParam) -> int:
        """
        删除HASN 会话分层 - 逻辑会话

        :param db: 数据库会话
        :param obj: HASN 会话分层 - 逻辑会话 ID 列表
        :return:
        """
        count = await hasn_sessions_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def upsert(*, db: AsyncSession, session_data: dict, owner_id: str | None = None) -> HasnSessions:
        """
        创建或更新 Session（幂等操作）

        :param db: 数据库会话
        :param session_data: Session 数据
        :param owner_id: 当前认证 owner
        :return:
        """
        _validate_cloud_session_payload(session_data, owner_id)
        # origin_type 归一：仅在 payload 显式携带该字段时纠偏（不破坏部分更新语义），
        # 防止 daemon 漂移值触发 chk_origin_type CheckViolationError 中断同步。
        if 'origin_type' in session_data:
            session_data = {**session_data, 'origin_type': _normalize_origin_type(session_data['origin_type'])}
        session_id = session_data.get('session_id')

        # 查询是否已存在
        stmt = select(HasnSessions).where(HasnSessions.session_id == session_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if owner_id is not None and existing.owner_id != owner_id:
                raise errors.ForbiddenError(msg='无权修改该 Session')
            # 更新现有 Session
            for key, value in session_data.items():
                if key == 'project_id' and value is None:
                    continue
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.updated_time = timezone.now()
            await db.flush()
            return existing
        # 创建新 Session
        new_session = HasnSessions(**session_data)
        db.add(new_session)
        await db.flush()
        return new_session

    @staticmethod
    async def update_summary(
        *, db: AsyncSession, session_id: str, summary_data: dict, owner_id: str | None = None
    ) -> HasnSessions:
        """
        更新 Session 摘要

        :param db: 数据库会话
        :param session_id: Session ID
        :param summary_data: 摘要数据
        :return:
        """
        session = await HasnSessionsService.get_by_session_id(db=db, session_id=session_id, owner_id=owner_id)

        # 更新摘要和最后消息时间
        session.summary_checkpoint_json = summary_data.get('summary_checkpoint_json', session.summary_checkpoint_json)
        session.last_message_at = summary_data.get('last_message_at', session.last_message_at)
        session.updated_time = timezone.now()

        await db.flush()
        return session

    @staticmethod
    async def close_session(
        *, db: AsyncSession, session_id: str, close_data: dict, owner_id: str | None = None
    ) -> HasnSessions:
        """
        关闭 Session

        :param db: 数据库会话
        :param session_id: Session ID
        :param close_data: 关闭数据
        :return:
        """
        session = await HasnSessionsService.get_by_session_id(db=db, session_id=session_id, owner_id=owner_id)

        # 更新状态和关闭时间
        session.session_status = close_data.get('session_status', 'completed')
        session.closed_at = timezone.now()
        session.updated_time = timezone.now()

        await db.flush()
        return session

    @staticmethod
    async def get_by_session_id(*, db: AsyncSession, session_id: str, owner_id: str | None = None) -> HasnSessions:
        """
        根据 session_id 获取 Session

        :param db: 数据库会话
        :param session_id: Session ID
        :return:
        """
        stmt = select(HasnSessions).where(HasnSessions.session_id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            raise errors.NotFoundError(msg='Session 不存在')
        if owner_id is not None and session.owner_id != owner_id:
            raise errors.ForbiddenError(msg='无权访问该 Session')

        return session

    @staticmethod
    async def try_get_by_session_id(
        *, db: AsyncSession, session_id: str, owner_id: str | None = None
    ) -> HasnSessions | None:
        """容错版 get_by_session_id：云端不存在该 Session 时返回 None（不抛 404）。

        work-session 是 hasn-node 本地实体（本地 `sessions` 表，scope=summary_only），
        云端 `hasn_sessions` 不保证有对应行——结果投影不应因此 404。仅在找到却归属
        不符时仍抛 403。
        """
        stmt = select(HasnSessions).where(HasnSessions.session_id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if session is None:
            return None
        if owner_id is not None and session.owner_id != owner_id:
            raise errors.ForbiddenError(msg='无权访问该 Session')

        return session

    @staticmethod
    async def list_messages(
        *,
        db: AsyncSession,
        owner_id: str,
        session_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """查询云端允许展示的工作会话投影消息。"""
        await HasnSessionsService.get_by_session_id(db=db, session_id=session_id, owner_id=owner_id)
        offset = max(page - 1, 0) * page_size
        result = await db.execute(
            sa.text(
                """
                SELECT id,
                       conversation_id::text AS conversation_id,
                       from_id,
                       to_id,
                       content,
                       context,
                       client_message_id,
                       created_time
                FROM public.hasn_messages
                WHERE owner_id = :owner_id
                  AND session_id = :session_id
                ORDER BY id ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            {
                'owner_id': owner_id,
                'session_id': session_id,
                'limit': page_size,
                'offset': offset,
            },
        )
        rows = list(result.mappings().all())
        return {'messages': [dict(row) for row in rows], 'total': len(rows), 'page': page, 'page_size': page_size}

    @staticmethod
    async def project_work_session_result(
        *,
        db: AsyncSession,
        owner_id: str,
        session_id: str,
        projection_data: dict[str, Any],
    ) -> dict[str, Any]:
        """幂等写入工作会话结果摘要消息。

        work-session 是 hasn-node 本地实体（本地 `sessions` 表，scope=summary_only），
        云端 `hasn_sessions` 不保证有对应行。因此**不要求**云端先存在 session：缺失时
        直接用 projection_data 自包含字段（agent_id/origin/title/summary/deep_link）写入
        主会话卡片消息，并跳过云端 session 回写。鉴权仍由 owner JWT +
        `_assert_projection_conversation_owned`（会话归属校验）保证。
        """
        session = await HasnSessionsService.try_get_by_session_id(db=db, session_id=session_id, owner_id=owner_id)
        agent_id = str(projection_data.get('agent_id') or (session.hasn_id if session else ''))
        if not agent_id:
            raise errors.RequestError(msg='缺少 Agent ID，无法投影工作会话结果')
        if session is not None and projection_data.get('agent_id') and projection_data['agent_id'] != session.hasn_id:
            raise errors.ForbiddenError(msg='投影 Agent 与 Session 不匹配')

        conversation_id = await _resolve_projection_conversation(
            db=db,
            owner_id=owner_id,
            agent_id=agent_id,
            projection_data=projection_data,
        )
        dedupe_key = _projection_dedupe_key(session_id, projection_data)

        existing = await _find_projection_message(db, owner_id=owner_id, dedupe_key=dedupe_key)
        if existing:
            return {
                'result_message_id': str(existing['id']),
                'conversation_id': str(existing['conversation_id']),
                'dedupe_key': dedupe_key,
                'created': False,
            }

        title = (session.title if session and session.title else None) or projection_data.get('title') or session_id
        origin_type = _normalize_origin_type(
            (session.origin_type if session else None) or projection_data.get('origin_type') or 'task_run'
        )
        origin_ref = (session.origin_ref if session else None) or projection_data.get('origin_ref') or ''
        content_json = _projection_content_json(
            session_id=session_id,
            agent_id=agent_id,
            origin_type=origin_type,
            origin_ref=origin_ref,
            projection_data=projection_data,
        )
        content_card = _projection_card_body(session_id=session_id, title=title, content_json=content_json)
        validate_card_message_body(content_card)

        # R2-02：完成卡也是会话消息，必须与普通消息共用同一权威取号入口。
        # 取号和 INSERT 处于同一事务，后续写入失败时序号增量随事务一起回滚。
        conversation_seq = await hasn_conversations_dao.allocate_seq(db, conversation_id)
        if conversation_seq is None:
            raise ValueError(f'allocate_seq 失败：会话 {conversation_id} 不存在，无法分配 conversation_seq')

        result = await db.execute(
            sa.text(
                """
                INSERT INTO public.hasn_messages (
                    conversation_id,
                    conversation_seq,
                    owner_id,
                    hasn_id,
                    from_id,
                    sender_hasn_id,
                    from_type,
                    to_id,
                    recipient_hasn_id,
                    to_type,
                    content_type,
                    content,
                    process_blocks,
                    msg_type,
                    status,
                    priority,
                    local_id,
                    client_message_id,
                    mention_all,
                    context,
                    sync_status,
                    delivery_status,
                    dispatch_status,
                    server_received_at,
                    created_time
                ) VALUES (
                    CAST(:conversation_id AS uuid),
                    :conversation_seq,
                    :owner_id,
                    :hasn_id,
                    :from_id,
                    :sender_hasn_id,
                    2,
                    :to_id,
                    :recipient_hasn_id,
                    1,
                    5,
                    CAST(:content AS jsonb),
                    CAST(:process_blocks AS jsonb),
                    'work_session_result',
                    1,
                    'normal',
                    :local_id,
                    :client_message_id,
                    false,
                    CAST(:context AS jsonb),
                    'pending',
                    'delivered',
                    'not_required',
                    now(),
                    now()
                )
                RETURNING id
                """
            ),
            {
                'conversation_id': str(conversation_id),
                'conversation_seq': conversation_seq,
                'owner_id': owner_id,
                'hasn_id': owner_id,
                'from_id': agent_id,
                'sender_hasn_id': agent_id,
                'to_id': owner_id,
                'recipient_hasn_id': owner_id,
                'content': json.dumps(content_card, ensure_ascii=False, sort_keys=True, default=str),
                'process_blocks': '[]',
                'local_id': dedupe_key,
                'client_message_id': dedupe_key,
                'context': json.dumps(
                    {
                        'projection_kind': 'work_session_result_summary',
                        'session_id': session_id,
                        'dedupe_key': dedupe_key,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        )
        row = result.mappings().one()
        result_message_id = str(row['id'])
        if session is not None:
            _record_projection_on_session(
                session=session,
                result_message_id=result_message_id,
                conversation_id=str(conversation_id),
                content_json=content_json,
            )
        # RC-P8：应用资源会话（deck/reel/…）完成投影的**同处**登记 hasn_artifacts——让分身产出的
        # 应用资源自动出现在「工作会话资源栏 / 分身产物 tab」。与完成卡同链路（同 descriptor、同
        # 云端权威 uri_id），幂等（重复投影不重复登记）。非应用资源（普通任务会话）→ resolved 为 None，跳过。
        resolved = _resolve_app_resource_projection(content_json)
        if resolved is not None:
            from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

            descriptor, _app_id, uri_id = resolved
            await HasnArtifactsService.record_app_resource_artifact(
                db,
                descriptor=descriptor,
                server_id=uri_id,
                session_id=session_id,
                agent_hasn_id=agent_id,
                owner_hasn_id=owner_id,
                title=title,
                summary=content_json.get('summary') or None,
            )
        await db.flush()
        return {
            'result_message_id': result_message_id,
            'conversation_id': str(conversation_id),
            'dedupe_key': dedupe_key,
            'created': True,
        }

    @staticmethod
    async def list_work_session_summaries(
        *,
        db: AsyncSession,
        owner_id: str,
        project_id: UUID | str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列 owner 名下（**所有设备**）工作会话摘要——主会话跨会话感知用（doc13 决策 D）。

        只取 ``summary_only``（工作会话云端同步范围）的 session，按末次活跃倒序。
        返回精简摘要供 daemon 归并进「最近会话」digest（跨设备那一路），正文/逐条 events
        不在此——分身要看细节走 ``hasn.worksession.get`` 下钻。owner 隔离（``WHERE owner_id``）。
        """
        capped = max(1, min(int(limit or 20), 100))
        stmt = (
            select(HasnSessions)
            .where(
                HasnSessions.owner_id == owner_id,
                HasnSessions.session_scope == 'summary_only',
            )
            .order_by(
                sa.func.coalesce(HasnSessions.last_message_at, HasnSessions.updated_time).desc().nullslast(),
            )
            .limit(capped)
        )
        if project_id is not None:
            stmt = stmt.where(HasnSessions.project_id == project_id)
        rows = (await db.execute(stmt)).scalars().all()
        return [_work_session_summary_row(session) for session in rows]

    @staticmethod
    async def get_work_session_summary(
        *,
        db: AsyncSession,
        owner_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """取单个工作会话的云端摘要（跨设备下钻用，doc13 §6.5）。

        云端不保证有此 session（work-session 是 hasn-node 本地实体）——缺失返回 None（daemon
        会据此走本地或诚实标注跨设备无逐条 events）。owner 归属不符时仍抛 403（try_get 语义）。
        返回精简摘要 + 完整 summary 文本（无逐条 events，跨设备只摘要）。
        """
        session = await HasnSessionsService.try_get_by_session_id(db=db, session_id=session_id, owner_id=owner_id)
        if session is None:
            return None
        checkpoint = dict(session.summary_checkpoint_json or {})
        row = _work_session_summary_row(session)
        row['summary'] = str(checkpoint.get('summary') or '')
        row['deep_link'] = checkpoint.get('deep_link')
        return row


hasn_sessions_service: HasnSessionsService = HasnSessionsService()


# ─────────────────────────────────────────────────────────────────────────────
# 工作会话摘要投影（doc13 主会话跨会话感知·决策 D 跨设备读端点）
# ─────────────────────────────────────────────────────────────────────────────

# 云端源头预览上限（防御纵深，doc13 决策 G）——真正的硬截断在 daemon 侧做（≈60 全角），
# 这里只避免把整条几千 token 的摘要原样返给 daemon（list 预览本就不该是全文）。
_SUMMARY_PREVIEW_CAP = 200

# origin_ref 形如 ``resource:<app>:<id>``（AppCollab doc21 §D3）——取中间的 <app> 段作应用标识。
_RESOURCE_ORIGIN_PREFIX = 'resource:'

# 云端 session_status（active/waiting_for_user/completed/error/cancelled）→ digest 状态词表
# （running/waiting_for_user/completed/failed）。云端只有粗粒度，daemon 有本地则用本地细状态。
_CLOUD_STATUS_MAP = {
    'active': 'running',
    'waiting_for_user': 'waiting',
    'completed': 'completed',
    'error': 'failed',
    'cancelled': 'cancelled',
}


def _epoch_ms(value: datetime | None) -> int:
    """datetime → epoch 毫秒；None → 0（daemon 排序键容忍 0）。"""
    if value is None:
        return 0
    return int(value.timestamp() * 1000)


def _app_from_origin(origin_type: str | None, origin_ref: str | None) -> str:
    """从 origin_ref/origin_type 推 app 标识：``resource:<app>:<id>`` → <app>；否则回落 origin_type。"""
    if origin_ref and origin_ref.startswith(_RESOURCE_ORIGIN_PREFIX):
        parts = origin_ref[len(_RESOURCE_ORIGIN_PREFIX) :].split(':', 1)
        if parts and parts[0].strip():
            return parts[0].strip()
    return origin_type or ''


def _map_cloud_status(session_status: str | None) -> str:
    """云端粗粒度状态归一到 digest 词表；未知原样透出。"""
    if not session_status:
        return 'running'
    return _CLOUD_STATUS_MAP.get(session_status, session_status)


def _work_session_summary_row(session: HasnSessions) -> dict[str, Any]:
    """把一行 HasnSessions 投影成 digest 用的精简工作会话摘要。"""
    checkpoint = session.summary_checkpoint_json or {}
    preview = str(checkpoint.get('summary') or '')
    if len(preview) > _SUMMARY_PREVIEW_CAP:
        preview = preview[:_SUMMARY_PREVIEW_CAP] + '…'
    return {
        'session_id': session.session_id,
        'agent_id': session.hasn_id,
        'topic': session.title or '',
        'app': _app_from_origin(session.origin_type, session.origin_ref),
        'origin_type': session.origin_type,
        'origin_ref': session.origin_ref,
        'project_id': str(session.project_id) if session.project_id is not None else None,
        'status': _map_cloud_status(session.session_status),
        'summary_preview': preview,
        'last_active': _epoch_ms(session.last_message_at or session.updated_time),
    }


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def _apply_csv_filter(stmt: Any, column: Any, value: str | None) -> Any:
    values = _split_csv(value)
    if not values:
        return stmt
    if len(values) == 1:
        return stmt.where(column == values[0])
    return stmt.where(column.in_(values))


# 与 backend/sql/hasn/hasn_sessions.sql 的 chk_origin_type 约束保持一致（白名单必须同步两处）。
# 云端是自身约束的权威：daemon 端任何**真正未知**的漂移值都在入库前归一回落，绝不能让单个
# 非法枚举触发 CheckViolationError 把整批工作会话 summary 同步 500 掉、令 daemon 无限重试（doc16 B 阶段）。
# manual（派发型工作会话）/copilot（会议副驾会话）是 daemon 合法产出的会话来源，已随
# 2026-06-30 迁移加入 chk_origin_type 白名单，按云端权威**原样保留**（不再误当漂移值压成 'system'）。
_ALLOWED_ORIGIN_TYPES: frozenset[str] = frozenset(
    {
        'ui',
        'scheduler',
        'task_run',
        'workflow_run',
        'external_app',
        'api',
        'system',
        'app',
        'manual',
        'copilot',
    }
)
_DEFAULT_ORIGIN_TYPE = 'system'


def _normalize_origin_type(value: str | None) -> str:
    """将 origin_type 归一到 chk_origin_type 白名单；未知/空值回落到 'system'。"""
    if value and value in _ALLOWED_ORIGIN_TYPES:
        return value
    return _DEFAULT_ORIGIN_TYPE


def _validate_cloud_session_payload(session_data: dict[str, Any], owner_id: str | None) -> None:
    request_owner_id = session_data.get('owner_id')
    if owner_id is not None and request_owner_id != owner_id:
        raise errors.ForbiddenError(msg='Session owner 与当前用户不一致')
    if session_data.get('session_scope') == 'local_only':
        raise errors.RequestError(msg='local_only Session 不允许同步到云端')


async def _resolve_projection_conversation(
    *,
    db: AsyncSession,
    owner_id: str,
    agent_id: str,
    projection_data: dict[str, Any],
) -> str:
    explicit_id = projection_data.get('target_conversation_id') or projection_data.get('source_conversation_id')
    if explicit_id:
        await _assert_projection_conversation_owned(
            db=db,
            owner_id=owner_id,
            agent_id=agent_id,
            conversation_id=str(explicit_id),
        )
        return str(explicit_id)

    conversation = await hasn_conversations_service.ensure_conversation(
        db=db,
        caller_hasn_id=owner_id,
        peer_hasn_id=agent_id,
        relation_type='social',
    )
    return str(conversation.id)


async def _assert_projection_conversation_owned(
    *,
    db: AsyncSession,
    owner_id: str,
    agent_id: str,
    conversation_id: str,
) -> None:
    result = await db.execute(
        sa.text(
            """
            SELECT id
            FROM public.hasn_conversations
            WHERE id = CAST(:conversation_id AS uuid)
              AND type = 'direct'
              AND (
                    (participant_a_id = :owner_id AND participant_b_id = :agent_id)
                 OR (participant_a_id = :agent_id AND participant_b_id = :owner_id)
              )
            LIMIT 1
            """
        ),
        {'conversation_id': conversation_id, 'owner_id': owner_id, 'agent_id': agent_id},
    )
    if not result.mappings().first():
        raise errors.ForbiddenError(msg='无权投影到该会话')


async def _find_projection_message(db: AsyncSession, *, owner_id: str, dedupe_key: str) -> dict[str, Any] | None:
    result = await db.execute(
        sa.text(
            """
            SELECT id, conversation_id::text AS conversation_id
            FROM public.hasn_messages
            WHERE owner_id = :owner_id
              AND client_message_id = :dedupe_key
            ORDER BY id ASC
            LIMIT 1
            """
        ),
        {'owner_id': owner_id, 'dedupe_key': dedupe_key},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def _projection_dedupe_key(session_id: str, projection_data: dict[str, Any]) -> str:
    milestone_id = projection_data.get('milestone_id')
    if milestone_id:
        return f'work_session_result:{session_id}:milestone:{milestone_id}'
    return f'work_session_result:{session_id}:final'


def _projection_content_json(
    *,
    session_id: str,
    agent_id: str,
    origin_type: str | None,
    origin_ref: str | None,
    projection_data: dict[str, Any],
) -> dict[str, Any]:
    deep_link = projection_data.get('deep_link') or f'hasn://tasks/sessions/{session_id}'
    return {
        'projection_kind': 'work_session_result_summary',
        'session_id': session_id,
        'agent_id': agent_id,
        'origin_type': origin_type,
        'origin_ref': origin_ref,
        # 云端权威 deck id（daemon 据本地 deck 的 server_id 解析后随投影上传，见 hasn-node
        # `domains/task/task_sessions.rs`）。deck 完成卡的 `hasn://deck/{id}` 一律优先用它，
        # 绝不用 origin_ref 里的设备本地 ULID（本地 id 跨设备/分享后对端解析不开）。
        'deck_server_id': projection_data.get('deck_server_id'),
        'task_id': projection_data.get('task_id'),
        'task_run_id': projection_data.get('task_run_id'),
        'workflow_run_id': projection_data.get('workflow_run_id'),
        'external_app_id': projection_data.get('external_app_id'),
        'status': projection_data.get('status') or 'success',
        'summary': projection_data.get('summary') or '',
        'deep_link': deep_link,
        'completion_reason': projection_data.get('completion_reason') or 'manual',
        'dedupe_key': _projection_dedupe_key(session_id, projection_data),
    }


_DECK_ORIGIN_PREFIX = 'resource:deck:'
# RC-P3：应用资源工作会话统一以 `resource:{app}:{local}` 声明 origin_ref。`resource:` 前缀本身即
# 「这是某 AI-Native 应用产出的资源」信号（等价 origin_type=='app'），完成卡据此泛化组卡。
_APP_RESOURCE_ORIGIN_PREFIX = 'resource:'


def _parse_app_origin_ref(origin_ref: str | None) -> tuple[str, str] | None:
    """拆 `origin_ref = resource:{app_id}:{local_ref}` → `(app_id, local_ref)`；其它/空 → None。

    与 daemon 侧 `domains/app_resource.rs::parse_resource_origin_ref` 对称：只认 `resource:` 前缀，
    按**首个**冒号切 app_id 与其余（local_ref 允许含冒号，覆盖将来带命名空间的本地 id）。
    local_ref 即 webui `hasn://{域}/{id}` 需要的那个 id——调用方优先用云端权威 `{app}_server_id`，
    未上云才回退本地 local_ref（本地 id 跨设备/分享后对端解析不开，见 Core-08 URI 第二原则）。
    """
    if not origin_ref or not origin_ref.startswith(_APP_RESOURCE_ORIGIN_PREFIX):
        return None
    rest = origin_ref[len(_APP_RESOURCE_ORIGIN_PREFIX) :]
    app_id, sep, local_ref = rest.partition(':')
    if not sep:
        return None
    app_id = app_id.strip()
    local_ref = local_ref.strip()
    if not app_id or not local_ref:
        return None
    return app_id, local_ref


def _resolve_app_resource_projection(content_json: dict[str, Any]) -> tuple[Any, str, str] | None:
    """据投影 content_json 判定是否应用资源会话，命中则返回 `(descriptor, app_id, uri_id)`（RC-P3/RC-P8 共用）。

    仅当 `origin_ref=resource:{app}:{local}` 且该 app 在 manifest.resources[] 声明了 descriptor 才算应用资源；
    未声明 → None（完成卡回落通用工作会话卡、且不登记应用资源产物）。`uri_id` 一律**优先云端权威
    `{app}_server_id`**，未上云才回退本地 local_ref（Core-08 URI 第二原则：本地 id 换设备/分享后对端解析不开）。
    """
    app_resource = _parse_app_origin_ref(content_json.get('origin_ref'))
    if app_resource is None:
        return None
    app_id, local_ref = app_resource
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    # doc31-A：多资源应用（plan：目标/计划）据 local_ref 子类段选 descriptor 并剥前缀取 id；
    # 单资源应用（deck/reel/design/knowledge）整段 local_ref 作 id。无匹配 descriptor → None（回落通用卡）。
    descriptor, resolved_ref = ai_native_app_registry.resolve_resource_descriptor(app_id, local_ref)
    if descriptor is None or resolved_ref is None:
        return None
    uri_id = str(content_json.get(f'{app_id}_server_id') or resolved_ref)
    return descriptor, app_id, uri_id


def build_generic_resource_card(
    *,
    descriptor: Any,
    app_id: str,
    session_id: str,
    uri_id: str,
    content_json: dict[str, Any],
) -> dict[str, Any]:
    """据资源描述符（RC-P0 `ResourceDescriptor`）泛化组「{verb}做好了」完成卡（doc31 §2，RC-P3）。

    终结 deck 专属硬编码：任何声明了 `manifest.resources[]` 的 AI-Native 应用，其工作会话完成后
    云端据 descriptor 统一组卡（标题「{card.verb}做好了」、主按钮 `card.action_label`、深链
    `hasn://{uri_domain}/{uri_id}`），新应用零改代码即出卡。分身不自己发卡，杜绝「发错/忘发」。

    - `uri_id` = **云端权威 server_id**（调用方 `_projection_card_body` 优先取 `{app}_server_id`，
      未上云才回退本地 local_ref）——跨设备/分享后对端据云端 id 读穿云端 ACL 打开，不依赖设备私有本地 id。
    - deck 喂其 descriptor 时逐字节等价旧 `_projection_deck_card`（RC-P3 回归断言）。
    """
    verb = descriptor.card.verb
    action_label = descriptor.card.action_label
    uri_domain = descriptor.uri_domain
    deep_link = f'hasn://{uri_domain}/{uri_id}'
    return {
        'schema_version': 'hasn.card/0.1',
        'title': f'{verb}做好了',
        'description': content_json.get('summary') or f'{verb}已经做好了，点开看看吧。',
        'source': {
            'kind': 'app',
            'id': app_id,
            'display_name': verb,
            'verified': True,
        },
        'resource': {
            'type': 'app.resource',
            'id': uri_id,
            'app_id': app_id,
            'uri': deep_link,
            'access': {
                'visibility': 'recipient',
                'readable_by': ['human'],
                'required_scopes': [],
            },
            'metadata': {
                'agent_id': content_json.get('agent_id'),
                'origin_type': content_json.get('origin_type'),
                'origin_ref': content_json.get('origin_ref'),
                'dedupe_key': content_json.get('dedupe_key'),
                'session_id': session_id,
            },
        },
        'primary_action': {
            'label': action_label,
            'action_id': f'open_{app_id}',
            'kind': 'open_uri',
            'uri': deep_link,
            'event': {
                'event_type': f'{app_id}.opened',
                'payload': {f'{app_id}_id': uri_id, 'session_id': session_id},
            },
            'style': 'primary',
        },
        'metadata': {
            'projection_kind': 'work_session_result_summary',
            'legacy_content_json': content_json,
        },
    }


def _deck_resource_descriptor() -> Any:
    """取 deck 的资源描述符（RC-P0 声明在 deck manifest.resources[]），查不到返 None。

    正常路径必然命中（registry 同步读 builtin manifest）。**不再兜底手写一份等价 descriptor**
    （doc36 §8.3）：registry 查不到 = deck manifest 的 `resources[]` 坏了，此时该 fail loud——调用方
    warn + 不发卡，而不是拿一份手写副本假装正常。那份硬编码副本正是「漂移被掩盖」的机制：manifest
    改了、手写副本没跟着改，卡片仍照旧出，问题被藏起来直到某天两者矛盾才炸。
    """
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    return ai_native_app_registry.resource_descriptor('deck', 'deck.presentation')


def _projection_deck_card(
    *, descriptor: Any, session_id: str, deck_id: str, content_json: dict[str, Any]
) -> dict[str, Any]:
    """演示文稿工作会话完成 → 给主人发的「打开演示文稿」卡（云端权威组卡）。

    RC-P3 起走 `build_generic_resource_card`（deck 只是首个试点），逐字节等价旧硬编码卡。
    保留本薄封装供 `emit_deck_completion_card`（主会话 finalize 路径）复用；descriptor 由调用方
    解析并保证非空后传入（doc36 §8.3：无硬编码兜底，缺声明时由调用方 fail loud）。
    """
    return build_generic_resource_card(
        descriptor=descriptor,
        app_id='deck',
        session_id=session_id,
        uri_id=deck_id,
        content_json=content_json,
    )


def _actor_principal(hasn_id: str) -> ServicePrincipal:
    """按 hasn_id 推断发送方身份，供卡片投递 path 构造 principal。"""
    return ServicePrincipal(
        canonical_sender=hasn_id,
        actor_kind=ActorKind.AGENT if hasn_id.startswith('a_') else ActorKind.HUMAN,
    )


async def _send_card_to_owner_via_im(
    db: AsyncSession,
    *,
    sender_id: str,
    owner_id: str,
    card: dict[str, Any],
    local_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """走 ImGateway 发送分身→主人的卡片；失败返回 route_message 风格字典。"""
    gateway = get_im_gateway()
    principal = _actor_principal(sender_id)

    conv_ref = await gateway.ensure_direct_conversation(
        EnsureDirectConversationCommand(peer_hasn_id=owner_id, relation_type='social'),
        principal,
    )
    try:
        send_result = await gateway.send_message(
            SendMessageCommand(
                conversation_id=conv_ref.conversation_id,
                content=card,
                content_type=5,
                msg_type='card',
                idempotency_key=local_id,
                context=context,
            ),
            principal,
        )
    except ImSendRejected as exc:
        return {
            'error': True,
            'code': exc.code,
            'message': str(exc),
            'conversation_id': str(conv_ref.conversation_id),
        }

    status = 'sent'
    if send_result.delivery_state == DeliveryState.SUPPRESSED:
        status = 'suppressed'
    elif send_result.delivery_state == DeliveryState.PENDING_POLICY:
        status = 'pending_confirmation'

    return {
        'error': False,
        'msg_id': send_result.message_id,
        'conversation_id': send_result.conversation_id,
        'status': status,
        'deduped': send_result.deduped,
        'relation': send_result.relation,
        'pending_request_id': send_result.pending_request_id,
        'reason': send_result.suppress_reason,
        'local_id': local_id,
    }


async def emit_deck_completion_card(
    db: AsyncSession,
    *,
    owner_id: str,
    agent_id: str,
    deck_id: str,
    title: str = '',
    summary: str = '',
) -> dict[str, Any]:
    """演示文稿收尾 → 云端组「打开演示文稿」卡，走 ``ImGateway`` 从**分身→主人**投递。

    「分身做完就自动发一张卡」的落地点（不管在哪里发起——主会话直做 / 派发工作会话皆然）：
    分身写完最后一页调 ``hasn.deck.finalize``，云端**首次** draft/generating→ready 转换时调本函数。
    **分身不自己发卡**——由云端据 deck 完成事件统一组卡，杜绝「发错/忘发」。

    - 复用 ``ImGateway``（分身→自己主人始终放行）：自带会话解析 + 落库 + push_to_owner +
      daemon 镜像，与分身平时回复主人完全同一条可靠通道（在线即推、离线重连 sync_pull 补达）。
    - 深链 ``hasn://deck/{deck_id}``，``deck_id`` = **云端权威 deck id**（主会话路径 deck 本就
      建在云端，入参即云端 id，无需本地↔云端映射；跨设备/分享后对端据云端 id 读穿 ACL 打开）。
    - 幂等：``local_id=deck_complete:{deck_id}`` 命中 ``hasn_messages`` 唯一索引则不二次投递，
      叠加 finalize 的状态守卫 → 双保只发一次。
    """
    # doc36 §8.3：先解析 descriptor，查不到即 fail loud（warn + 不发卡）——registry 无 deck
    # descriptor = manifest.resources[] 坏了，绝不拿硬编码副本假装正常（那会掩盖漂移）。
    descriptor = _deck_resource_descriptor()
    if descriptor is None:
        log.warning(
            'deck manifest 缺 resources[] 声明（registry 查不到 deck.presentation descriptor），'
            '跳过演示文稿完成卡投递（doc36 §8.3 fail loud）；deck_id=%s',
            deck_id,
        )
        return {}
    summary_text = summary.strip() if summary else ''
    if not summary_text:
        summary_text = f'《{title}》已经做好了，点开看看吧。' if title else ''
    content_json = {
        'projection_kind': 'deck_completion',
        'agent_id': agent_id,
        'origin_type': 'app',
        'origin_ref': f'{_DECK_ORIGIN_PREFIX}{deck_id}',
        'deck_server_id': deck_id,
        'summary': summary_text,
        'deep_link': f'hasn://deck/{deck_id}',
    }
    card = _projection_deck_card(
        descriptor=descriptor, session_id=f'deck-{deck_id}', deck_id=str(deck_id), content_json=content_json
    )
    validate_card_message_body(card)

    from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

    # RC-P8：主会话直建 deck（无工作会话）的产出登记 —— 让主会话做的 deck 也进「分身产物 tab」。
    # session_id=None（非工作会话，产物凭 resource_uri 归位）；与工作会话投影同一幂等键
    # (agent, deck:{deck_id}, hasn://deck/{deck_id})，两条路径重复触发不重复登记。
    await HasnArtifactsService.record_app_resource_artifact(
        db,
        descriptor=descriptor,
        server_id=str(deck_id),
        session_id=None,
        agent_hasn_id=agent_id,
        owner_hasn_id=owner_id,
        title=title or '演示文稿',
        summary=summary_text or None,
    )

    return await _send_card_to_owner_via_im(
        db,
        sender_id=agent_id,
        owner_id=owner_id,
        card=card,
        local_id=f'deck_complete:{deck_id}',
        context={'projection_kind': 'deck_completion', 'deck_id': str(deck_id)},
    )


def _designsystem_resource_descriptor() -> Any:
    """取 designsystem 的资源描述符（RC-P0 声明在 designsystem manifest.resources[]），查不到返 None。

    正常路径必然命中（registry 同步读 builtin manifest）。**不再兜底手写一份等价 descriptor**
    （doc36 §8.3，对齐 `_deck_resource_descriptor`）：registry 查不到 = designsystem manifest 坏了，
    调用方 warn + 不发卡。旧兜底还踩过 `{'window':'designsystem'}` 与 ResourceWindow∈{deck,design}
    矛盾即 ValidationError 的坑——正是「手写副本与真 manifest 漂移」的活教训，一并删除。
    """
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    return ai_native_app_registry.resource_descriptor('designsystem', 'designsystem.spec')


async def emit_designsystem_completion_card(
    db: AsyncSession,
    *,
    owner_id: str,
    agent_id: str,
    design_system_id: str,
    title: str = '',
    summary: str = '',
) -> dict[str, Any]:
    """设计系统写满必填字段 → 云端组「打开设计系统」卡，走 ``ImGateway`` 从**分身→主人**投递。

    完全对齐 ``emit_deck_completion_card``（福仔「像 deck 那样」）——不再走 notification_service.emit
    的汇报面通道（依赖 OwnerLoopback 守卫 + 部署状态，脆弱），改用 deck 同一条可靠会话通道：

    - 复用 ``ImGateway``（分身→自己主人始终放行）：自带会话解析 + 落库 + push_to_owner +
      daemon 镜像，与分身平时回复主人完全同一条通道（在线即推、离线重连 sync_pull 补达）。
    - 深链 ``hasn://designsystem/{design_system_id}``，``design_system_id`` = **云端权威 id**
      （设计系统本就建在云端，入参即云端 id；跨设备/分享后对端据云端 id 读穿 ACL 打开，
      符合「本地 ID 永不上 URI」铁律）。
    - 幂等：``local_id=designsystem_complete:{id}`` 命中 ``hasn_messages`` 唯一索引则不二次投递
      —— 完成信号可被多次 save 触发（自愈补发首次投递失败的卡），local_id 保证只发一次。
    - **产物登记不在此处**：由 ``hasn.designsystem.save`` 工具每次 save 都 register-on-write
      （带 work_session_id），故完成卡只管发卡、不重复登记（与 deck 略异：designsystem 的 save
      恒经工具，无「主会话直建、无工具」路径，故此处省登记）。
    """
    # doc36 §8.3：查不到 descriptor 即 fail loud（warn + 不发卡），不硬编码兜底。
    descriptor = _designsystem_resource_descriptor()
    if descriptor is None:
        log.warning(
            'designsystem manifest 缺 resources[] 声明（registry 查不到 designsystem.spec descriptor），'
            '跳过设计系统完成卡投递（doc36 §8.3 fail loud）；design_system_id=%s',
            design_system_id,
        )
        return {}
    summary_text = summary.strip() if summary else ''
    if not summary_text:
        summary_text = f'《{title}》已经做好了，点开看看吧。' if title else ''
    content_json = {
        'projection_kind': 'designsystem_completion',
        'agent_id': agent_id,
        'origin_type': 'app',
        'origin_ref': f'resource:designsystem:{design_system_id}',
        'designsystem_server_id': design_system_id,
        'summary': summary_text,
        'deep_link': f'hasn://designsystem/{design_system_id}',
    }
    card = build_generic_resource_card(
        descriptor=descriptor,
        app_id='designsystem',
        session_id=f'designsystem-{design_system_id}',
        uri_id=str(design_system_id),
        content_json=content_json,
    )
    validate_card_message_body(card)

    return await _send_card_to_owner_via_im(
        db,
        sender_id=agent_id,
        owner_id=owner_id,
        card=card,
        local_id=f'designsystem_complete:{design_system_id}',
        context={
            'projection_kind': 'designsystem_completion',
            'designsystem_id': str(design_system_id),
        },
    )


def _projection_card_body(*, session_id: str, title: str, content_json: dict[str, Any]) -> dict[str, Any]:
    # RC-P3：应用资源会话（origin_ref=resource:{app}:{local}）→ 据 descriptor 泛化组「{verb}做好了」卡
    # （分身不自己发卡，去 deck 特例）。判别器用 origin_ref 拆 app_id/local_ref，卡里 `hasn://{域}/{id}`
    # 的 id **一律优先用云端权威 `{app}_server_id`**——本地 id 跨设备/分享后对端解析不开（福仔「分享给
    # 别人根本打不开」的根因）。仅当资源尚未上云（无 server_id）才回退本地 local_ref：此时资源不在云端、
    # 根本无法分享，唯一消费者是 owner 本机，本地 id 恰好能解析。未声明 descriptor 的应用 → 回落通用卡。
    resolved = _resolve_app_resource_projection(content_json)
    if resolved is not None:
        descriptor, app_id, uri_id = resolved
        return build_generic_resource_card(
            descriptor=descriptor,
            app_id=app_id,
            session_id=session_id,
            uri_id=uri_id,
            content_json=content_json,
        )
    task_id = content_json.get('task_id')
    task_run_id = content_json.get('task_run_id')
    event_payload = {
        'session_id': session_id,
    }
    if task_id is not None:
        event_payload['task_id'] = task_id
    if task_run_id is not None:
        event_payload['task_run_id'] = task_run_id

    fields = [
        {'label': '状态', 'value': str(content_json.get('status') or 'success')},
        {'label': '完成原因', 'value': str(content_json.get('completion_reason') or 'manual')},
    ]
    if task_id is not None:
        fields.append({'label': '任务 ID', 'value': str(task_id)})
    if task_run_id is not None:
        fields.append({'label': '任务执行 ID', 'value': str(task_run_id)})

    return {
        'schema_version': 'hasn.card/0.1',
        'title': f'工作会话「{title}」已完成',
        'description': content_json.get('summary') or '工作会话已完成。',
        'source': {
            'kind': 'task',
            'id': str(task_id or content_json.get('workflow_run_id') or session_id),
            'display_name': '任务系统',
            'verified': True,
        },
        'resource': {
            'type': 'task_session',
            'id': session_id,
            'app_id': 'tasks',
            'uri': content_json.get('deep_link') or f'hasn://tasks/sessions/{session_id}',
            'access': {
                'visibility': 'recipient',
                'readable_by': ['human'],
                'required_scopes': [],
            },
            'metadata': {
                'agent_id': content_json.get('agent_id'),
                'origin_type': content_json.get('origin_type'),
                'origin_ref': content_json.get('origin_ref'),
                'dedupe_key': content_json.get('dedupe_key'),
            },
        },
        'fields': fields,
        'primary_action': {
            'label': '查看任务',
            'action_id': 'open_task_session',
            'kind': 'open_uri',
            'uri': content_json.get('deep_link') or f'hasn://tasks/sessions/{session_id}',
            'event': {
                'event_type': 'task.summary.opened',
                'payload': event_payload,
            },
            'style': 'primary',
        },
        'metadata': {
            'projection_kind': 'work_session_result_summary',
            'legacy_content_json': content_json,
        },
    }


def _record_projection_on_session(
    *,
    session: HasnSessions,
    result_message_id: str,
    conversation_id: str,
    content_json: dict[str, Any],
) -> None:
    checkpoint = dict(session.summary_checkpoint_json or {})
    checkpoint.update({
        'summary': content_json.get('summary'),
        'status': content_json.get('status'),
        'result_message_id': result_message_id,
        'projection_conversation_id': conversation_id,
        'deep_link': content_json.get('deep_link'),
        'completion_reason': content_json.get('completion_reason'),
        'dedupe_key': content_json.get('dedupe_key'),
    })
    session.summary_checkpoint_json = checkpoint
    session.last_message_id = result_message_id
    session.last_message_at = timezone.now()
    session.updated_time = timezone.now()
