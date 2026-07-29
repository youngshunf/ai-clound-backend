"""会议副驾 v5 云端会议结果域业务服务（meetings 表族，owner 硬隔离）。

会议是 owner 私有的一等结果资源（§6.0.7）：`meetings` 主档 + `meeting_transcript_segments`
转写记录定稿 + `meeting_minutes` 纪要版本。daemon 经 Owner JWT 通道（`domains/copilot/cloud.rs`）
起会建行 / 改字段 / segments 幂等上推 / 纪要写入 / 媒体升格 / 分享 / 删除，云端权威落库后
daemon 以本表 id（UUID）为身份键做 local_first 镜像回填（`meetings_mirror.rs`）。

owner 硬隔离：所有查询强制 `owner_hasn_id == <jwt owner>`——owner 绝不从请求体读，由 API 层
`_resolve_owner` 解析登录主人。会议对象序列化字段名与 daemon `meetings_mirror::from_cloud`
精确对齐（participants/shared_media/stats 数组/对象、started_at/ended_at/duration_ms 整数）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import delete, select

from backend.app.hasn.model import HasnResourceShare
from backend.app.hasn_core import HasnAgents
from backend.app.hasn_copilot.model import (
    MeetingEnhancementRevisions,
    MeetingMinutes,
    MeetingTranscriptSegments,
    Meetings,
)

# 触发会议资源适配器注册（分享建行 fail-closed 依赖 resource_type='meeting' 已注册）。
# 该 import 经 authz.resource_registry 先完整初始化 authz 包，也为分享方法里对
# resource_share_service 的惰性 import 铺好加载顺序（避免 resource_share_service ↔ authz 冷启动循环）。
from backend.app.hasn_copilot.service import resource_adapter as _resource_adapter  # noqa: F401
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 会议共享的通用资源类型（与 resource_adapter.resource_type、hasn_resource_share.resource_type 同串）。
_RESOURCE_TYPE = 'meeting'
# 会议状态 / 场景 / 纪要状态字典（与 DDL COMMENT 一致）。
_STATUSES = ('active', 'ended', 'finalized')
_SCENES = ('meeting', 'interview', 'call', 'lecture')
_MINUTES_STATES = ('none', 'queued', 'ready', 'failed')
# 分享权限档位：对外 view/edit/manage 与内部 resource_share viewer/editor/manager 的双向映射。
_PERMISSION_ALIASES = {
    'view': 'viewer',
    'viewer': 'viewer',
    'edit': 'editor',
    'editor': 'editor',
    'manage': 'manager',
    'manager': 'manager',
}
# PATCH 可改字段白名单（其余请求键忽略，防越权改 owner/id 等）。
_PATCHABLE = frozenset({
    'title',
    'scene',
    'status',
    'record_version',
    'participants_json',
    'minutes_state',
    'minutes_version',
    'node_id',
    'ended_at',
    'duration_ms',
    'stats_json',
    'speaker_annotation_revision',
    'agent_hasn_id',
})


def _to_uuid(value: str | UUID) -> UUID | None:
    """会议/资源 id 串转 UUID；畸形返回 None（调用方据此 404，绝不冒 500）。"""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _meeting_dict(m: Meetings) -> dict[str, Any]:
    """会议对象序列化——字段名与 daemon `meetings_mirror::from_cloud` 精确对齐。

    participants/shared_media 是数组、stats 是对象；started_at/ended_at/duration_ms 是整数（unix 秒/毫秒）。
    id 一律 UUID 字符串（hasn://meeting/{id} 的 {id} 段）。
    """
    return {
        'id': str(m.id),
        'owner_hasn_id': m.owner_hasn_id,
        'enterprise_id': m.enterprise_id,
        'agent_hasn_id': m.agent_hasn_id,
        'session_id': m.session_id,
        'node_id': m.node_id,
        'title': m.title,
        'scene': m.scene,
        'status': m.status,
        'record_version': m.record_version,
        'realtime_revision_id': str(m.realtime_revision_id),
        'preferred_enhancement_revision_id': (
            str(m.preferred_enhancement_revision_id) if m.preferred_enhancement_revision_id else None
        ),
        'speaker_annotation_revision': m.speaker_annotation_revision,
        'minutes_state': m.minutes_state,
        'minutes_version': m.minutes_version,
        'participants': m.participants_json or [],
        'shared_media': m.shared_media_json or [],
        'stats': m.stats_json or {},
        'started_at': m.started_at,
        'ended_at': m.ended_at,
        'duration_ms': m.duration_ms,
        'created_time': m.created_time,
        'updated_time': m.updated_time,
    }


def _segment_dict(s: MeetingTranscriptSegments) -> dict[str, Any]:
    return {
        'id': str(s.id),
        'meeting_id': str(s.meeting_id),
        'record_version': s.record_version,
        'seq': s.seq,
        'track': s.track,
        'speaker_label': s.speaker_label,
        'speaker_source': s.speaker_source,
        'text': s.text,
        'started_ms': s.started_ms,
        'ended_ms': s.ended_ms,
    }


def _minutes_dict(mi: MeetingMinutes) -> dict[str, Any]:
    return {
        'id': str(mi.id),
        'meeting_id': str(mi.meeting_id),
        'version': mi.version,
        'body_md': mi.body_md,
        'record_view_version': mi.record_view_version,
        'summary_turn_id': mi.summary_turn_id,
        'created_time': mi.created_time,
        'updated_time': mi.updated_time,
    }


async def _revision_state(
    db: AsyncSession,
    *,
    meeting: Meetings,
    owner_hasn_id: str,
) -> dict[str, Any]:
    """惰性导入候选服务，避免会议服务与候选服务形成模块初始化环。"""
    from backend.app.hasn_copilot.service.meeting_enhancement_revisions_service import (
        meeting_enhancement_revisions_service,
    )

    return await meeting_enhancement_revisions_service.get_revision_state(
        db,
        meeting=meeting,
        owner_hasn_id=owner_hasn_id,
    )


def _grantee_type_of(hasn_id: str) -> str:
    """按 hasn_id 前缀推断被授予对象类型：a_* → agent，其余 → human（联系人本人）。"""
    return 'agent' if hasn_id.startswith('a_') else 'human'


class MeetingsService:
    """会议结果域 owner-scoped 服务。所有方法第一参数 db，owner_hasn_id 为归属隔离键。"""

    # ---------- 归属校验 / 取行 ----------

    @staticmethod
    async def _validate_agent_owner(db: AsyncSession, *, owner_hasn_id: str, agent_id: str) -> None:
        """校验 agent_id 是本 owner 名下分身；不是则 404（不泄露他人分身是否存在，同 deck/copilot）。"""
        agent = (
            await db.execute(
                select(HasnAgents.id).where(
                    HasnAgents.hasn_id == agent_id,
                    HasnAgents.owner_id == owner_hasn_id,
                )
            )
        ).first()
        if agent is None:
            raise errors.NotFoundError(msg='指定的协作分身不存在或不属于你')

    @staticmethod
    async def _get_by_session(db: AsyncSession, *, owner_hasn_id: str, session_id: str) -> Meetings | None:
        return (
            await db.execute(
                select(Meetings).where(
                    Meetings.owner_hasn_id == owner_hasn_id,
                    Meetings.session_id == session_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _get_owned(db: AsyncSession, *, owner_hasn_id: str, meeting_id: str) -> Meetings:
        """按权威 id 取本 owner 的会议行；畸形 / 不存在 / 非本人一律 404（owner 隔离边界，不泄露存在性）。"""
        mid = _to_uuid(meeting_id)
        if mid is None:
            raise errors.NotFoundError(msg='会议不存在')
        meeting = (
            await db.execute(select(Meetings).where(Meetings.id == mid, Meetings.owner_hasn_id == owner_hasn_id))
        ).scalar_one_or_none()
        if meeting is None:
            raise errors.NotFoundError(msg='会议不存在')
        return meeting

    # ---------- 起会（按 session_id upsert）----------

    @staticmethod
    async def create_meeting(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        session_id: str,
        agent_hasn_id: str | None = None,
        title: str | None = None,
        scene: str | None = None,
        node_id: str | None = None,
        started_at: int | None = None,
    ) -> dict[str, Any]:
        """起会建行：按 (owner, session_id) upsert——已存在则返回既有行，不重复建（离线起会联网补建幂等）。"""
        if not session_id:
            raise errors.RequestError(msg='session_id 不能为空')
        if scene is not None and scene not in _SCENES:
            raise errors.RequestError(msg='非法场景')
        existing = await MeetingsService._get_by_session(db, owner_hasn_id=owner_hasn_id, session_id=session_id)
        if existing is not None:
            return _meeting_dict(existing)  # 幂等：已存在返回既有行
        if agent_hasn_id:
            await MeetingsService._validate_agent_owner(db, owner_hasn_id=owner_hasn_id, agent_id=agent_hasn_id)
        meeting = Meetings(
            owner_hasn_id=owner_hasn_id,
            session_id=session_id,
            agent_hasn_id=agent_hasn_id,
            title=title or '',
            scene=scene,
            node_id=node_id,
            started_at=started_at,
            status='active',
        )
        db.add(meeting)
        await db.flush()
        return _meeting_dict(meeting)

    # ---------- 列表 / 详情 ----------

    @staticmethod
    async def list_meetings(
        db: AsyncSession, *, owner_hasn_id: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """owner 私有会议列表（按 updated_time DESC，对齐 idx_meetings_owner_updated）。"""
        base = select(Meetings).where(Meetings.owner_hasn_id == owner_hasn_id)
        rows = (
            (
                await db.execute(
                    base
                    .order_by(Meetings.updated_time.desc().nullslast(), Meetings.created_time.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = len((await db.execute(select(Meetings.id).where(Meetings.owner_hasn_id == owner_hasn_id))).all())
        return {'items': [_meeting_dict(m) for m in rows], 'total': total}

    @staticmethod
    async def get_detail(db: AsyncSession, *, owner_hasn_id: str, meeting_id: str) -> dict[str, Any]:
        """会议详情（owner 全量）：meeting + 当前定稿 segments + 全部纪要版本 + relation/my_permission。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        segments = (
            (
                await db.execute(
                    select(MeetingTranscriptSegments)
                    .where(
                        MeetingTranscriptSegments.meeting_id == meeting.id,
                        MeetingTranscriptSegments.record_version == meeting.record_version,
                    )
                    .order_by(MeetingTranscriptSegments.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        minutes = (
            (
                await db.execute(
                    select(MeetingMinutes)
                    .where(MeetingMinutes.meeting_id == meeting.id)
                    .order_by(MeetingMinutes.version.desc())
                )
            )
            .scalars()
            .all()
        )
        return {
            'meeting': _meeting_dict(meeting),
            'segments': [_segment_dict(s) for s in segments],
            'minutes': [_minutes_dict(m) for m in minutes],
            'revision_state': await _revision_state(db, meeting=meeting, owner_hasn_id=owner_hasn_id),
            'relation': 'owner',
            'my_permission': 'manage',
        }

    # ---------- 改字段 ----------

    @staticmethod
    async def patch_meeting(
        db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """改会议字段（仅 _PATCHABLE 白名单子集）。scene/status/minutes_state 做字典校验。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        if 'scene' in patch and patch['scene'] is not None and patch['scene'] not in _SCENES:
            raise errors.RequestError(msg='非法场景')
        if 'status' in patch and patch['status'] is not None and patch['status'] not in _STATUSES:
            raise errors.RequestError(msg='非法会议状态')
        if (
            'minutes_state' in patch
            and patch['minutes_state'] is not None
            and patch['minutes_state'] not in _MINUTES_STATES
        ):
            raise errors.RequestError(msg='非法纪要状态')
        if patch.get('agent_hasn_id'):
            await MeetingsService._validate_agent_owner(
                db, owner_hasn_id=owner_hasn_id, agent_id=patch['agent_hasn_id']
            )
        for key, value in patch.items():
            if key not in _PATCHABLE or value is None:
                continue
            # 请求体用 participants_json/stats_json 键，映射到同名列。
            setattr(meeting, key, value)
        meeting.updated_time = timezone.now()
        await db.flush()
        return _meeting_dict(meeting)

    # ---------- 转写定稿幂等上推 ----------

    @staticmethod
    async def put_segments(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str,
        record_version: int,
        segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """转写定稿幂等上推：按 (meeting_id, record_version, seq) UPSERT 段，把 meetings.record_version 提到该版本。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        # 取本版本已有段（幂等 upsert：命中改、缺则插）。
        existing_rows = (
            (
                await db.execute(
                    select(MeetingTranscriptSegments).where(
                        MeetingTranscriptSegments.meeting_id == meeting.id,
                        MeetingTranscriptSegments.record_version == record_version,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_seq = {row.seq: row for row in existing_rows}
        for seg in segments:
            seq = int(seg.get('seq', 0))
            row = by_seq.get(seq)
            if row is None:
                row = MeetingTranscriptSegments(
                    meeting_id=meeting.id,
                    record_version=record_version,
                    seq=seq,
                    track=seg.get('track'),
                    speaker_label=seg.get('speaker_label'),
                    speaker_source=seg.get('speaker_source'),
                    text=seg.get('text') or '',
                    started_ms=int(seg.get('started_ms', 0)),
                    ended_ms=seg.get('ended_ms'),
                )
                db.add(row)
                by_seq[seq] = row
            else:
                row.track = seg.get('track')
                row.speaker_label = seg.get('speaker_label')
                row.speaker_source = seg.get('speaker_source')
                row.text = seg.get('text') or ''
                row.started_ms = int(seg.get('started_ms', 0))
                row.ended_ms = seg.get('ended_ms')
                row.updated_time = timezone.now()
        # 把 meetings.record_version 提到该 record_version（迟到补全后向前推进）。
        meeting.record_version = record_version
        meeting.updated_time = timezone.now()
        await db.flush()
        return {'meeting_id': str(meeting.id), 'record_version': record_version, 'segment_count': len(segments)}

    # ---------- 纪要写入 ----------

    @staticmethod
    async def write_minutes(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        meeting_id: str,
        version: int,
        body_md: str,
        record_view_version: int | None = None,
        summary_turn_id: str | None = None,
    ) -> dict[str, Any]:
        """纪要写入（幂等 version）：按 (meeting_id, version) UPSERT，
        并置 meetings.minutes_state=ready、minutes_version=version。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        existing = (
            await db.execute(
                select(MeetingMinutes).where(
                    MeetingMinutes.meeting_id == meeting.id,
                    MeetingMinutes.version == version,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                MeetingMinutes(
                    meeting_id=meeting.id,
                    version=version,
                    body_md=body_md,
                    record_view_version=record_view_version,
                    summary_turn_id=summary_turn_id,
                )
            )
        else:
            existing.body_md = body_md
            existing.record_view_version = record_view_version
            existing.summary_turn_id = summary_turn_id
            existing.updated_time = timezone.now()
        meeting.minutes_state = 'ready'
        meeting.minutes_version = version
        meeting.updated_time = timezone.now()
        await db.flush()
        return _meeting_dict(meeting)

    # ---------- 升格媒体 ----------

    @staticmethod
    async def add_media(
        db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, media: dict[str, Any]
    ) -> dict[str, Any]:
        """升格媒体 upsert 到 shared_media_json（幂等键 sha256+kind：同键替换，新增追加；无 media_id 则生成）。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        sha256 = media.get('sha256')
        kind = media.get('kind')
        if not sha256 or not kind:
            raise errors.RequestError(msg='升格媒体缺少 sha256 或 kind')
        entry = dict(media)
        entry['media_id'] = media.get('media_id') or f'md_{uuid.uuid4().hex}'
        media_list = list(meeting.shared_media_json or [])
        replaced = False
        new_list: list[dict[str, Any]] = []
        for item in media_list:
            if item.get('sha256') == sha256 and item.get('kind') == kind:
                # 同键替换：沿用旧 media_id（稳定引用），覆盖其余字段。
                entry['media_id'] = item.get('media_id') or entry['media_id']
                new_list.append(entry)
                replaced = True
            else:
                new_list.append(item)
        if not replaced:
            new_list.append(entry)
        meeting.shared_media_json = new_list  # 整体重赋新对象，ORM 据此检测 JSONB 变更
        meeting.updated_time = timezone.now()
        await db.flush()
        return _meeting_dict(meeting)

    @staticmethod
    async def delete_media(db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, media_id: str) -> dict[str, Any]:
        """撤销单件升格：从 shared_media_json 删除该 media_id 条目。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        media_list = list(meeting.shared_media_json or [])
        meeting.shared_media_json = [item for item in media_list if item.get('media_id') != media_id]
        meeting.updated_time = timezone.now()
        await db.flush()
        return _meeting_dict(meeting)

    # ---------- 分享（通用 resource_share） ----------

    @staticmethod
    async def share_meeting(
        db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, grantee_hasn_id: str, permission: str | None = None
    ) -> dict[str, Any]:
        """分享给联系人：走通用 hasn_resource_share 建行（resource_type='meeting'，照抄 deck 分享）。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        if not grantee_hasn_id:
            raise errors.RequestError(msg='grantee_hasn_id 不能为空')
        internal_perm = _PERMISSION_ALIASES.get((permission or 'view').lower())
        if internal_perm is None:
            raise errors.RequestError(msg='非法权限档（view/edit/manage）')
        # 惰性 import：打破 resource_share_service ↔ authz 的模块级循环依赖（详见文件顶部注释）。
        from backend.app.hasn.service.resource_share_service import resource_share_service

        await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(meeting.id),
            owner_hasn_id=meeting.owner_hasn_id,
            grantee_type=_grantee_type_of(grantee_hasn_id),
            grantee_id=grantee_hasn_id,
            permission=internal_perm,
            granted_by=owner_hasn_id,
        )
        return {'shared': True, 'grantee_hasn_id': grantee_hasn_id, 'permission': internal_perm}

    @staticmethod
    async def share_revoke(
        db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, grantee_hasn_id: str
    ) -> dict[str, Any]:
        """撤销联系人访问：删对应 resource_share 行（status→revoked）。"""
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        if not grantee_hasn_id:
            raise errors.RequestError(msg='grantee_hasn_id 不能为空')
        # 惰性 import：打破 resource_share_service ↔ authz 的模块级循环依赖（详见文件顶部注释）。
        from backend.app.hasn.service.resource_share_service import resource_share_service

        ok = await resource_share_service.revoke_share(
            db,
            resource_type=_RESOURCE_TYPE,
            resource_id=str(meeting.id),
            grantee_type=_grantee_type_of(grantee_hasn_id),
            grantee_id=grantee_hasn_id,
        )
        return {'revoked': ok, 'grantee_hasn_id': grantee_hasn_id}

    # ---------- 删除 ----------

    @staticmethod
    async def delete_meeting(
        db: AsyncSession, *, owner_hasn_id: str, meeting_id: str, scope: str = 'all'
    ) -> dict[str, Any]:
        """整场删除。scope=all：删 meetings 行 + segments + minutes + resource_share 行；
        scope=local_media：云端只需成功返回（本机媒体由 daemon 删，不动云端结果）。"""
        if scope not in ('all', 'local_media'):
            raise errors.RequestError(msg='非法删除范围（all/local_media）')
        if scope == 'local_media':
            # 云端结果保留；本机媒体由 daemon 侧删除。仍校验归属以维持 owner 隔离一致性。
            await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
            return {'deleted': True, 'scope': scope}
        meeting = await MeetingsService._get_owned(db, owner_hasn_id=owner_hasn_id, meeting_id=meeting_id)
        mid = meeting.id
        await db.execute(delete(MeetingTranscriptSegments).where(MeetingTranscriptSegments.meeting_id == mid))
        await db.execute(delete(MeetingMinutes).where(MeetingMinutes.meeting_id == mid))
        await db.execute(delete(MeetingEnhancementRevisions).where(MeetingEnhancementRevisions.meeting_id == mid))
        await db.execute(
            delete(HasnResourceShare).where(
                HasnResourceShare.resource_type == _RESOURCE_TYPE,
                HasnResourceShare.resource_id == str(mid),
            )
        )
        await db.delete(meeting)
        await db.flush()
        return {'deleted': True, 'scope': scope}


meetings_service = MeetingsService()
