"""错误事件上行落库服务（doc21 §7）。

幂等落库 = 照抄 `messages:sync` 范式：每事件在 SAVEPOINT 内落 error_report，
`(node_id, dedup_key)` 唯一约束撞键 → deduped（跳过 issue 更新）；accepted 事件
按 fingerprint upsert error_issue（occurrence_count 累进含 suppressed_count、
affected 计数经 error_issue_seen 去重累加、按状态分号的版本感知/snooze 回归重开）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.hasn_diag.service.version_compare import is_newer
from backend.common.log import log

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

# 自动重开哨兵 actor（§5.2/§6）
AUTO_REOPEN_ACTOR = 'system:auto-reopen'

_ACTIVE_STATUSES = {'open', 'investigating'}


@dataclass(frozen=True)
class IngestEvent:
    """单条上行错误事件（服务端已从 JWT 注入 owner_hasn_id，不信客户端）。"""

    local_event_id: str
    source: str
    severity: str
    fingerprint: str
    dedup_key: str
    error_class: str | None
    message: str
    location: str | None
    context: dict
    occurred_at: datetime
    suppressed_count: int


async def ingest_errors(
    db: AsyncSession,
    *,
    owner_hasn_id: str | None,
    node_id: str,
    app_version: str | None,
    platform: str | None,
    events: list[IngestEvent],
) -> list[dict]:
    """逐事件幂等落库并回显 `{local_event_id, accepted, deduped}`。

    单事件在 SAVEPOINT 内隔离：撞 `(node_id, dedup_key)` → deduped；其余异常 →
    回滚该事件、标 accepted=False deduped=False，不拖垮整批（外层事务仍在）。
    """
    results: list[dict] = []
    for ev in events:
        accepted = False
        deduped = False
        try:
            inserted = await _persist_report(
                db,
                owner_hasn_id=owner_hasn_id,
                node_id=node_id,
                app_version=app_version,
                platform=platform,
                event=ev,
            )
            if inserted:
                accepted = True
                await _apply_occurrence(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    node_id=node_id,
                    app_version=app_version,
                    event=ev,
                )
            else:
                deduped = True
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                # 并发上行抢先落了同一 (node_id, dedup_key)：savepoint 已回滚，回 deduped。
                deduped = True
            else:
                # 非唯一键的完整性冲突（如 CHECK 约束拒收）绝不能伪装成 deduped——
                # daemon 收到 200 即标 pushed，事件会被静默永久丢失（2026-07-05 曾因
                # source CHECK 未放行 webui，把前端错误证据整批无声吞掉）。响亮记日志、
                # 回 accepted=False，让丢失可见可查。
                log.error(f'diag 错误事件落库被完整性约束拒收（event={ev.local_event_id} source={ev.source}）: {exc}')

        results.append(
            {'local_event_id': ev.local_event_id, 'accepted': accepted, 'deduped': deduped}
        )
    return results


def _is_unique_violation(exc: IntegrityError) -> bool:
    """是否唯一键冲突（PG SQLSTATE 23505）——只有它才允许当 deduped。

    asyncpg 经 SQLAlchemy 包装后原始异常在 `exc.orig`（sqlstate/pgcode 属性名随驱动
    适配层不同），取不到码时退化匹配异常文本，宁可放行 dedup 误判也不 500 整批。
    """
    orig = getattr(exc, 'orig', None)
    for attr in ('sqlstate', 'pgcode'):
        code = getattr(orig, attr, None)
        if code:
            return str(code) == '23505'
    text_repr = f'{exc} {orig}'
    return 'UniqueViolation' in text_repr or 'duplicate key' in text_repr


async def _persist_report(
    db: AsyncSession,
    *,
    owner_hasn_id: str | None,
    node_id: str,
    app_version: str | None,
    platform: str | None,
    event: IngestEvent,
) -> bool:
    """在 SAVEPOINT 内插 error_report；撞唯一键 → 返回 False（deduped）。"""
    async with db.begin_nested():
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_diag.error_report (
                        node_id, owner_hasn_id, agent_hasn_id, source, severity,
                        fingerprint, dedup_key, error_class, message, location,
                        context_json, suppressed_count, app_version, platform, occurred_at
                    ) VALUES (
                        :node_id, :owner_hasn_id, :agent_hasn_id, :source, :severity,
                        :fingerprint, :dedup_key, :error_class, :message, :location,
                        CAST(:context_json AS jsonb), :suppressed_count, :app_version,
                        :platform, :occurred_at
                    )
                    ON CONFLICT (node_id, dedup_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    'node_id': node_id,
                    'owner_hasn_id': owner_hasn_id,
                    'agent_hasn_id': event.context.get('agent_hasn_id')
                    if isinstance(event.context, dict)
                    else None,
                    'source': event.source,
                    'severity': event.severity,
                    'fingerprint': event.fingerprint,
                    'dedup_key': event.dedup_key,
                    'error_class': event.error_class,
                    'message': event.message,
                    'location': event.location,
                    'context_json': _json_dumps(event.context),
                    'suppressed_count': max(0, event.suppressed_count),
                    'app_version': app_version,
                    'platform': platform,
                    'occurred_at': event.occurred_at,
                },
            )
        ).first()
        return row is not None


async def _apply_occurrence(
    db: AsyncSession,
    *,
    owner_hasn_id: str | None,
    node_id: str,
    app_version: str | None,
    event: IngestEvent,
) -> None:
    """把一条 accepted occurrence 汇进 error_issue：计数 + affected + 回归重开。"""
    delta = 1 + max(0, event.suppressed_count)
    occurred_at = event.occurred_at
    async with db.begin_nested():
        issue = (
            await db.execute(
                text(
                    """
                    SELECT status, fixed_in_version, snooze_until, severity
                    FROM hasn_diag.error_issue
                    WHERE fingerprint = :fp
                    FOR UPDATE
                    """
                ),
                {'fp': event.fingerprint},
            )
        ).mappings().first()

        if issue is None:
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_diag.error_issue (
                        fingerprint, title, source, severity, status,
                        occurrence_count, first_seen_at, last_seen_at
                    ) VALUES (
                        :fp, :title, :source, :severity, 'open',
                        :delta, :occurred_at, :occurred_at
                    )
                    ON CONFLICT (fingerprint) DO NOTHING
                    """
                ),
                {
                    'fp': event.fingerprint,
                    'title': (event.message or event.fingerprint)[:256],
                    'source': event.source,
                    'severity': event.severity,
                    'delta': delta,
                    'occurred_at': occurred_at,
                },
            )
        else:
            reopen_to = _reopen_status(
                current_status=issue['status'],
                fixed_in_version=issue['fixed_in_version'],
                snooze_until=issue['snooze_until'],
                app_version=app_version,
                occurred_at=occurred_at,
            )
            await db.execute(
                text(
                    """
                    UPDATE hasn_diag.error_issue
                    SET occurrence_count = occurrence_count + :delta,
                        last_seen_at = GREATEST(last_seen_at, :occurred_at),
                        first_seen_at = LEAST(first_seen_at, :occurred_at),
                        severity = CASE
                            WHEN :sev_rank > 0 THEN :severity ELSE severity END,
                        status = COALESCE(:reopen_to, status),
                        updated_time = now()
                    WHERE fingerprint = :fp
                    """
                ),
                {
                    'fp': event.fingerprint,
                    'delta': delta,
                    'occurred_at': occurred_at,
                    'severity': event.severity,
                    'sev_rank': _severity_escalates(issue['severity'], event.severity),
                    'reopen_to': reopen_to,
                },
            )
            if reopen_to is not None:
                await _write_event(
                    db,
                    fingerprint=event.fingerprint,
                    actor=AUTO_REOPEN_ACTOR,
                    from_status=issue['status'],
                    to_status=reopen_to,
                    note=f'occurrence 触发自动重开（原状态 {issue["status"]}，'
                    f'app_version={app_version or "?"}）',
                )

        await _bump_affected(db, event.fingerprint, 'owner', owner_hasn_id)
        await _bump_affected(db, event.fingerprint, 'node', node_id)


def _reopen_status(
    *,
    current_status: str,
    fixed_in_version: str | None,
    snooze_until: datetime | None,
    app_version: str | None,
    occurred_at: datetime,
) -> str | None:
    """按状态分号判定是否自动重开（返回 'open' 或 None）。"""
    if current_status in _ACTIVE_STATUSES:
        return None
    if current_status == 'resolved':
        # 版本感知：occurrence 版本新于 fixed_in_version（或修复未标版本）→ 重开。
        return 'open' if is_newer(app_version, fixed_in_version) else None
    if current_status == 'skipped':
        # snooze 到期才重开；snooze_until 为空 = 无限期，不自动重开。
        if snooze_until is not None and snooze_until <= occurred_at:
            return 'open'
        return None
    # wontfix：永不自动重开（照常计数）。
    return None


def _severity_escalates(current: str, incoming: str) -> int:
    """incoming 是否比 current 更严重（1=升级，0=不变）。"""
    rank = {'warn': 0, 'error': 1, 'critical': 2}
    return 1 if rank.get(incoming, 0) > rank.get(current, 0) else 0


async def _bump_affected(
    db: AsyncSession, fingerprint: str, subject_type: str, subject_id: str | None
) -> None:
    """error_issue_seen INSERT ON CONFLICT DO NOTHING；新插入才 affected_*_count += 1。"""
    if not subject_id:
        return
    inserted = (
        await db.execute(
            text(
                """
                INSERT INTO hasn_diag.error_issue_seen (fingerprint, subject_type, subject_id)
                VALUES (:fp, :st, :sid)
                ON CONFLICT (fingerprint, subject_type, subject_id) DO NOTHING
                RETURNING id
                """
            ),
            {'fp': fingerprint, 'st': subject_type, 'sid': subject_id},
        )
    ).first()
    if inserted is None:
        return
    column = 'affected_owner_count' if subject_type == 'owner' else 'affected_node_count'
    await db.execute(
        text(
            f'UPDATE hasn_diag.error_issue SET {column} = {column} + 1 WHERE fingerprint = :fp'
        ),
        {'fp': fingerprint},
    )


async def _write_event(
    db: AsyncSession,
    *,
    fingerprint: str,
    actor: str,
    from_status: str | None,
    to_status: str,
    note: str | None,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO hasn_diag.error_issue_event (fingerprint, actor_hasn_id, from_status, to_status, note)
            VALUES (:fp, :actor, :from_status, :to_status, :note)
            """
        ),
        {
            'fp': fingerprint,
            'actor': actor,
            'from_status': from_status,
            'to_status': to_status,
            'note': note,
        },
    )


def _json_dumps(value: dict) -> str:
    import json

    if not isinstance(value, dict):
        value = {}
    return json.dumps(value, ensure_ascii=False, default=str)


# 供 issue 管理服务复用的审计写入
write_issue_event = _write_event
