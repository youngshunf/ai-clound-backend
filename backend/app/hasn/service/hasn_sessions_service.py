import json

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_sessions import hasn_sessions_dao
from backend.app.hasn.model import HasnSessions
from backend.app.hasn.schema.hasn_sessions import (
    CreateHasnSessionsParam,
    DeleteHasnSessionsParam,
    UpdateHasnSessionsParam,
)
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service.hasn_conversations_service import hasn_conversations_service
from backend.common.exception import errors
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
        origin_type = (session.origin_type if session else None) or projection_data.get('origin_type') or 'task_run'
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
        result = await db.execute(
            sa.text(
                """
                INSERT INTO public.hasn_messages (
                    conversation_id,
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
        await db.flush()
        return {
            'result_message_id': result_message_id,
            'conversation_id': str(conversation_id),
            'dedupe_key': dedupe_key,
            'created': True,
        }


hasn_sessions_service: HasnSessionsService = HasnSessionsService()


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


def _projection_card_body(*, session_id: str, title: str, content_json: dict[str, Any]) -> dict[str, Any]:
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
