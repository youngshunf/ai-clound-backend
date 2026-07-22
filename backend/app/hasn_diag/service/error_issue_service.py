"""错误问题（error_issue）读取与状态管理服务（doc21 §6/§8）。

本阶段（P1）只做 service 层：供后续 `hasn.diag.*` 云端 MCP 工具（P3b）与 pytest 调用，
不注册任何工具/HTTP 路由。list 类一律 keyset 分页（limit 默认 20 上限 100），消费方是
LLM，不设上限会撑爆分身上下文。状态流转按 §5.2 状态机校验，非法流转报错。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from backend.app.hasn_diag.service.error_report_service import write_issue_event
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# §5.2 状态机：各状态可迁往的目标集合（含手动重开 terminal→open/investigating）。
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    'open': {'investigating', 'resolved', 'skipped', 'wontfix'},
    'investigating': {'open', 'investigating', 'resolved', 'skipped', 'wontfix'},
    'resolved': {'open', 'investigating'},
    'skipped': {'open', 'investigating'},
    'wontfix': {'open', 'investigating'},
}
_RESOLVE_STATUSES = {'resolved', 'skipped', 'wontfix'}
_RESOLUTION_TYPES = {'code_fix', 'config_fix', 'duplicate', 'not_a_bug', 'external', 'cannot_reproduce'}


def _clamp_limit(limit: int | None) -> int:
    if not limit or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def _encode_cursor(last_seen_at: datetime, issue_id: int) -> str:
    return f'{last_seen_at.isoformat()}|{issue_id}'


def _decode_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        ts_raw, id_raw = cursor.rsplit('|', 1)
        return datetime.fromisoformat(ts_raw), int(id_raw)
    except (ValueError, TypeError):
        raise errors.RequestError(msg='非法分页游标')


def _issue_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'fingerprint': row['fingerprint'],
        'title': row['title'],
        'source': row['source'],
        'severity': row['severity'],
        'status': row['status'],
        'occurrence_count': row['occurrence_count'],
        'affected_owner_count': row['affected_owner_count'],
        'affected_node_count': row['affected_node_count'],
        'first_seen_at': row['first_seen_at'],
        'last_seen_at': row['last_seen_at'],
        'resolution_type': row['resolution_type'],
        'resolution_note': row['resolution_note'],
        'fixed_in_version': row['fixed_in_version'],
        'snooze_until': row['snooze_until'],
        'issue_url': row['issue_url'],
        'pr_url': row['pr_url'],
        'resolved_by': row['resolved_by'],
        'resolved_at': row['resolved_at'],
    }


async def list_issues(
    db: AsyncSession,
    *,
    status: str | None = 'open',
    source: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    stale_days: int | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    fingerprints: Collection[str] | None = None,
) -> dict:
    """按 fingerprint 的问题清单，keyset 分页（last_seen_at DESC, id DESC）。

    - status 默认 'open'（含自动重开的）；显式传 None 则不过滤状态。
    - stale_days 配合 status='investigating' 做孤儿回扫：只留 last_seen_at 早于阈值的。
    - fingerprints 非空时仅查询指定问题；空集合直接返回空页。
    """
    n = _clamp_limit(limit)
    clauses = []
    params: dict = {'limit': n + 1}
    if status:
        clauses.append('status = :status')
        params['status'] = status
    if source:
        clauses.append('source = :source')
        params['source'] = source
    if severity:
        clauses.append('severity = :severity')
        params['severity'] = severity
    if since:
        clauses.append('last_seen_at >= :since')
        params['since'] = since
    if stale_days and stale_days > 0:
        clauses.append("updated_time < now() - make_interval(days => :stale_days)")
        params['stale_days'] = stale_days
    if fingerprints is not None:
        if not fingerprints:
            return {'items': [], 'next_cursor': None}
        clauses.append('fingerprint = ANY(:fingerprints)')
        params['fingerprints'] = list(fingerprints)
    decoded = _decode_cursor(cursor)
    if decoded:
        params['cur_ts'], params['cur_id'] = decoded
        clauses.append('(last_seen_at < :cur_ts OR (last_seen_at = :cur_ts AND id < :cur_id))')
    where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT id, fingerprint, title, source, severity, status,
                           occurrence_count, affected_owner_count, affected_node_count,
                           first_seen_at, last_seen_at, resolution_type, resolution_note,
                           fixed_in_version, snooze_until, issue_url, pr_url,
                           resolved_by, resolved_at
                    FROM hasn_diag.error_issue
                    {where}
                    ORDER BY last_seen_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > n
    page = rows[:n]
    next_cursor = _encode_cursor(page[-1]['last_seen_at'], page[-1]['id']) if has_more else None
    return {'items': [_issue_row(dict(row)) for row in page], 'next_cursor': next_cursor}


async def get_issue(db: AsyncSession, *, fingerprint: str, occurrence_limit: int = 10) -> dict:
    """单 fingerprint 详情 + 最近 occurrence + 处理事件流。"""
    issue = (
        (
            await db.execute(
                text(
                    """
                    SELECT id, fingerprint, title, source, severity, status,
                           occurrence_count, affected_owner_count, affected_node_count,
                           first_seen_at, last_seen_at, resolution_type, resolution_note,
                           fixed_in_version, snooze_until, issue_url, pr_url,
                           resolved_by, resolved_at, duplicate_of_fingerprint
                    FROM hasn_diag.error_issue WHERE fingerprint = :fp
                    """
                ),
                {'fp': fingerprint},
            )
        )
        .mappings()
        .first()
    )
    if issue is None:
        raise errors.NotFoundError(msg='错误问题不存在')
    occurrences = await list_occurrences(db, fingerprint=fingerprint, limit=occurrence_limit)
    events = (
        (
            await db.execute(
                text(
                    """
                    SELECT actor_hasn_id, from_status, to_status, note, created_time
                    FROM hasn_diag.error_issue_event
                    WHERE fingerprint = :fp
                    ORDER BY created_time DESC, id DESC
                    LIMIT 50
                    """
                ),
                {'fp': fingerprint},
            )
        )
        .mappings()
        .all()
    )
    detail = _issue_row(dict(issue))
    detail['duplicate_of_fingerprint'] = issue['duplicate_of_fingerprint']
    detail['recent_occurrences'] = occurrences['items']
    detail['events'] = [dict(e) for e in events]
    return detail


async def list_occurrences(
    db: AsyncSession, *, fingerprint: str, limit: int | None = None, cursor: str | None = None
) -> dict:
    """原始 occurrence（深挖用），keyset 分页（occurred_at DESC, id DESC）。"""
    n = _clamp_limit(limit)
    params: dict = {'fp': fingerprint, 'limit': n + 1}
    extra = ''
    decoded = _decode_cursor(cursor)
    if decoded:
        params['cur_ts'], params['cur_id'] = decoded
        extra = 'AND (occurred_at < :cur_ts OR (occurred_at = :cur_ts AND id < :cur_id))'
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT id, node_id, owner_hasn_id, agent_hasn_id, source, severity,
                           error_class, message, location, context_json, suppressed_count,
                           app_version, platform, occurred_at
                    FROM hasn_diag.error_report
                    WHERE fingerprint = :fp {extra}
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > n
    page = rows[:n]
    next_cursor = _encode_cursor(page[-1]['occurred_at'], page[-1]['id']) if has_more else None
    return {'items': [dict(r) for r in page], 'next_cursor': next_cursor}


async def stats(db: AsyncSession) -> dict:
    """按状态 / 严重度 / 来源聚合计数（运维鸟瞰）。"""
    by_status = (
        await db.execute(
            text('SELECT status, count(*) AS n FROM hasn_diag.error_issue GROUP BY status')
        )
    ).mappings().all()
    by_severity = (
        await db.execute(
            text('SELECT severity, count(*) AS n FROM hasn_diag.error_issue GROUP BY severity')
        )
    ).mappings().all()
    by_source = (
        await db.execute(
            text('SELECT source, count(*) AS n FROM hasn_diag.error_issue GROUP BY source')
        )
    ).mappings().all()
    total = (
        await db.execute(text('SELECT count(*) AS n FROM hasn_diag.error_issue'))
    ).scalar_one()
    return {
        'total_issues': total,
        'by_status': {r['status']: r['n'] for r in by_status},
        'by_severity': {r['severity']: r['n'] for r in by_severity},
        'by_source': {r['source']: r['n'] for r in by_source},
    }


async def _load_status(db: AsyncSession, fingerprint: str) -> str:
    status = (
        await db.execute(
            text('SELECT status FROM hasn_diag.error_issue WHERE fingerprint = :fp FOR UPDATE'),
            {'fp': fingerprint},
        )
    ).scalar_one_or_none()
    if status is None:
        raise errors.NotFoundError(msg='错误问题不存在')
    return status


def _check_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise errors.RequestError(msg=f'非法状态流转：{current} → {target}')


async def update_issue(
    db: AsyncSession,
    *,
    fingerprint: str,
    actor_hasn_id: str,
    issue_url: str | None = None,
    pr_url: str | None = None,
    note: str | None = None,
) -> dict:
    """挂 issue/PR 链接并流转到 investigating（fixer 建 issue/PR 后调用）。"""
    current = await _load_status(db, fingerprint)
    _check_transition(current, 'investigating')
    await db.execute(
        text(
            """
            UPDATE hasn_diag.error_issue
            SET status = 'investigating',
                issue_url = COALESCE(:issue_url, issue_url),
                pr_url = COALESCE(:pr_url, pr_url),
                updated_time = now()
            WHERE fingerprint = :fp
            """
        ),
        {'fp': fingerprint, 'issue_url': issue_url, 'pr_url': pr_url},
    )
    await write_issue_event(
        db,
        fingerprint=fingerprint,
        actor=actor_hasn_id,
        from_status=current,
        to_status='investigating',
        note=note,
    )
    return await get_issue(db, fingerprint=fingerprint)


async def resolve_issue(
    db: AsyncSession,
    *,
    fingerprint: str,
    actor_hasn_id: str,
    status: str,
    resolution_type: str,
    resolution_note: str,
    fixed_in_version: str | None = None,
    duplicate_of_fingerprint: str | None = None,
    snooze_until: datetime | None = None,
) -> dict:
    """结案（resolved/skipped/wontfix）+ 处理结果（§6 必填校验）。"""
    if status not in _RESOLVE_STATUSES:
        raise errors.RequestError(msg=f'resolve_issue 只接受 {_RESOLVE_STATUSES}，收到 {status}')
    if resolution_type not in _RESOLUTION_TYPES:
        raise errors.RequestError(msg=f'非法 resolution_type：{resolution_type}')
    if not (resolution_note and resolution_note.strip()):
        raise errors.RequestError(msg='resolution_note 必填（resolved/skipped/wontfix）')
    if resolution_type == 'code_fix' and not fixed_in_version:
        raise errors.RequestError(msg='resolution_type=code_fix 必填 fixed_in_version')
    if resolution_type == 'duplicate' and not duplicate_of_fingerprint:
        raise errors.RequestError(msg='resolution_type=duplicate 必填 duplicate_of_fingerprint')

    current = await _load_status(db, fingerprint)
    _check_transition(current, status)
    await db.execute(
        text(
            """
            UPDATE hasn_diag.error_issue
            SET status = :status,
                resolution_type = :resolution_type,
                resolution_note = :resolution_note,
                fixed_in_version = :fixed_in_version,
                duplicate_of_fingerprint = :duplicate_of_fingerprint,
                snooze_until = :snooze_until,
                resolved_by = :actor,
                resolved_at = now(),
                updated_time = now()
            WHERE fingerprint = :fp
            """
        ),
        {
            'fp': fingerprint,
            'status': status,
            'resolution_type': resolution_type,
            'resolution_note': resolution_note,
            'fixed_in_version': fixed_in_version,
            'duplicate_of_fingerprint': duplicate_of_fingerprint,
            'snooze_until': snooze_until,
            'actor': actor_hasn_id,
        },
    )
    await write_issue_event(
        db,
        fingerprint=fingerprint,
        actor=actor_hasn_id,
        from_status=current,
        to_status=status,
        note=resolution_note,
    )
    return await get_issue(db, fingerprint=fingerprint)


async def list_reports_by_owner(
    db: AsyncSession, *, owner_hasn_id: str, limit: int | None = None, cursor: str | None = None
) -> dict:
    """owner 自己设备的原始错误 occurrence（owner 隔离读，§8.1）。"""
    n = _clamp_limit(limit)
    params: dict = {'owner': owner_hasn_id, 'limit': n + 1}
    extra = ''
    decoded = _decode_cursor(cursor)
    if decoded:
        params['cur_ts'], params['cur_id'] = decoded
        extra = 'AND (occurred_at < :cur_ts OR (occurred_at = :cur_ts AND id < :cur_id))'
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT id, node_id, agent_hasn_id, source, severity, fingerprint,
                           error_class, message, location, suppressed_count,
                           app_version, platform, occurred_at
                    FROM hasn_diag.error_report
                    WHERE owner_hasn_id = :owner {extra}
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > n
    page = rows[:n]
    next_cursor = _encode_cursor(page[-1]['occurred_at'], page[-1]['id']) if has_more else None
    return {'items': [dict(r) for r in page], 'next_cursor': next_cursor}
