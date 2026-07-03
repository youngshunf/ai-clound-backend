"""hasn_diag 错误上行/聚合/状态机 进程内真实 PG E2E（doc21 §5–§8，零 mock）。

直调权威 service（`error_report_service` / `error_issue_service`），真实本地 PostgreSQL(15432)，
每个用例一条事务、结束 rollback 不污染库。需要：export DATABASE_PORT=15432。

覆盖：
- ① 幂等落库：同 (node_id, dedup_key) 重放 → 第二次 deduped、occurrence_count 不重复涨；
- ② 聚合 + suppressed_count + 严重度升级 + first/last_seen 单调；
- ③ affected 计数：跨 2 owner / 2 node 去重累加，同主体重复只算一次；
- ④ 回归重开三态：resolved（版本感知）/ skipped（snooze 到期）/ wontfix（永不）；
- ⑤ owner 只读隔离：list_reports_by_owner 只回本人 occurrence；
- ⑥ 状态机：resolve_issue 必填校验 + 非法流转报错 + update_issue 挂链接转 investigating；
- ⑦ list_issues keyset 分页：limit + next_cursor 顺游标翻页、last_seen DESC。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.app.hasn_diag.service import error_issue_service
from backend.app.hasn_diag.service.error_report_service import IngestEvent, ingest_errors
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_T0 = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)


def _fp() -> str:
    """全局唯一 fingerprint（避开 error_issue 上的 uq_error_issue_fp 跨用例碰撞）。"""
    return uuid4().hex


def _ev(
    *,
    fingerprint: str,
    severity: str = 'error',
    source: str = 'daemon',
    dedup_key: str | None = None,
    message: str = '空指针解引用',
    occurred_at: datetime | None = None,
    suppressed_count: int = 0,
    agent_hasn_id: str | None = None,
) -> IngestEvent:
    return IngestEvent(
        local_event_id=uuid4().hex[:12],
        source=source,
        severity=severity,
        fingerprint=fingerprint,
        dedup_key=dedup_key or uuid4().hex,
        error_class='NullPointer',
        message=message,
        location='backend/foo.py:mod',
        context={'agent_hasn_id': agent_hasn_id} if agent_hasn_id else {},
        occurred_at=occurred_at or _T0,
        suppressed_count=suppressed_count,
    )


async def _ingest(db, *, owner, node, events, app_version=None, platform='darwin') -> list[dict]:  # noqa: ANN001
    return await ingest_errors(
        db,
        owner_hasn_id=owner,
        node_id=node,
        app_version=app_version,
        platform=platform,
        events=events,
    )


async def test_idempotent_dedup_no_double_count() -> None:
    """① 同 (node, dedup_key) 重放：首次 accepted、重放 deduped，occurrence_count 只 1。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            dk = uuid4().hex
            r1 = await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp, dedup_key=dk)])
            assert r1[0]['accepted'] and not r1[0]['deduped'], '首次应 accepted'
            r2 = await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp, dedup_key=dk)])
            assert r2[0]['deduped'] and not r2[0]['accepted'], '重放应 deduped'
            issue = await error_issue_service.get_issue(db, fingerprint=fp)
            assert issue['occurrence_count'] == 1, '去重后计数不重复涨'
            assert issue['affected_node_count'] == 1
            assert issue['affected_owner_count'] == 1
        finally:
            await db.rollback()


async def test_aggregate_suppressed_severity_and_seen_window() -> None:
    """② 聚合：occurrence_count 含 suppressed_count；severity 升级；first/last_seen 单调外扩。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            t1 = _T0
            t2 = _T0 + timedelta(hours=2)
            t0 = _T0 - timedelta(hours=3)
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp, severity='warn', occurred_at=t1)])
            await _ingest(
                db, owner='h_o1', node='n1',
                events=[_ev(fingerprint=fp, severity='error', occurred_at=t2, suppressed_count=3)],
            )
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp, severity='warn', occurred_at=t0)])
            issue = await error_issue_service.get_issue(db, fingerprint=fp)
            # 1（首）+ (1+3)（含抑制）+ 1（末）= 6
            assert issue['occurrence_count'] == 6, f'计数应 6，实际 {issue["occurrence_count"]}'
            assert issue['severity'] == 'error', 'warn→error 升级；不降级'
            assert issue['first_seen_at'] == t0, 'first_seen 取最早'
            assert issue['last_seen_at'] == t2, 'last_seen 取最晚'
        finally:
            await db.rollback()


async def test_affected_counts_dedup_across_owner_and_node() -> None:
    """③ affected：2 owner / 2 node 去重累加，同主体重复不重复计。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)])
            await _ingest(db, owner='h_o2', node='n1', events=[_ev(fingerprint=fp)])  # 新 owner，旧 node
            await _ingest(db, owner='h_o1', node='n2', events=[_ev(fingerprint=fp)])  # 旧 owner，新 node
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)])  # 全旧主体
            issue = await error_issue_service.get_issue(db, fingerprint=fp)
            assert issue['affected_owner_count'] == 2, 'owner o1/o2 → 2'
            assert issue['affected_node_count'] == 2, 'node n1/n2 → 2'
            assert issue['occurrence_count'] == 4, '4 条物理 occurrence'
        finally:
            await db.rollback()


async def test_reopen_resolved_is_version_aware() -> None:
    """④a resolved：occurrence 版本新于 fixed_in_version → 自动重开；旧版本不重开。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)], app_version='1.0.0')
            await error_issue_service.resolve_issue(
                db, fingerprint=fp, actor_hasn_id='h_ops', status='resolved',
                resolution_type='code_fix', resolution_note='已修', fixed_in_version='1.0.0',
            )
            # 旧版本 occurrence（0.9.0）不应重开
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)], app_version='0.9.0')
            assert (await error_issue_service.get_issue(db, fingerprint=fp))['status'] == 'resolved', '旧版本不重开'
            # 新版本 occurrence（1.1.0）应重开到 open + 落 auto-reopen 事件
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)], app_version='1.1.0')
            issue = await error_issue_service.get_issue(db, fingerprint=fp)
            assert issue['status'] == 'open', '新版本复现应自动重开'
            actors = [e['actor_hasn_id'] for e in issue['events']]
            assert 'system:auto-reopen' in actors, '自动重开应留审计事件'
        finally:
            await db.rollback()


async def test_reopen_skipped_snooze_and_wontfix_never() -> None:
    """④b skipped：snooze 到期才重开；④c wontfix：永不重开。"""
    async with async_db_session() as db:
        try:
            # skipped + snooze 已到期（snooze_until 早于新 occurrence）→ 重开
            fp_s = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp_s, occurred_at=_T0)])
            await error_issue_service.resolve_issue(
                db, fingerprint=fp_s, actor_hasn_id='h_ops', status='skipped',
                resolution_type='cannot_reproduce', resolution_note='暂缓',
                snooze_until=_T0 + timedelta(days=1),
            )
            # occurrence 晚于 snooze_until → 重开
            await _ingest(
                db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp_s, occurred_at=_T0 + timedelta(days=2))]
            )
            issue_s = await error_issue_service.get_issue(db, fingerprint=fp_s)
            assert issue_s['status'] == 'open', 'snooze 到期重开'

            # skipped + 无限期 snooze（snooze_until=None）→ 不重开
            fp_i = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp_i, occurred_at=_T0)])
            await error_issue_service.resolve_issue(
                db, fingerprint=fp_i, actor_hasn_id='h_ops', status='skipped',
                resolution_type='not_a_bug', resolution_note='无限期忽略', snooze_until=None,
            )
            await _ingest(
                db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp_i, occurred_at=_T0 + timedelta(days=99))]
            )
            issue_i = await error_issue_service.get_issue(db, fingerprint=fp_i)
            assert issue_i['status'] == 'skipped', '无限期 snooze 不重开'

            # wontfix → 永不重开
            fp_w = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp_w, occurred_at=_T0)])
            await error_issue_service.resolve_issue(
                db, fingerprint=fp_w, actor_hasn_id='h_ops', status='wontfix',
                resolution_type='external', resolution_note='上游问题',
            )
            await _ingest(
                db, owner='h_o1', node='n1',
                events=[_ev(fingerprint=fp_w, occurred_at=_T0 + timedelta(days=99))], app_version='99.0',
            )
            issue_w = await error_issue_service.get_issue(db, fingerprint=fp_w)
            assert issue_w['status'] == 'wontfix', 'wontfix 永不自动重开'
            assert issue_w['occurrence_count'] == 2, 'wontfix 仍照常计数'
        finally:
            await db.rollback()


async def test_owner_read_isolation() -> None:
    """⑤ owner 只读隔离：list_reports_by_owner 只回本人的 occurrence。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            o1 = f'h_{uuid4().hex[:12]}'
            o2 = f'h_{uuid4().hex[:12]}'
            # 项目里已按 owner 过滤（不回显 owner_hasn_id），用各自专属 node 区分归属。
            await _ingest(db, owner=o1, node=f'n1_{o1}', events=[_ev(fingerprint=fp), _ev(fingerprint=fp)])
            await _ingest(db, owner=o2, node=f'n2_{o2}', events=[_ev(fingerprint=fp)])
            p1 = await error_issue_service.list_reports_by_owner(db, owner_hasn_id=o1)
            p2 = await error_issue_service.list_reports_by_owner(db, owner_hasn_id=o2)
            assert len(p1['items']) == 2 and all(r['node_id'] == f'n1_{o1}' for r in p1['items']), 'o1 只见自己 2 条'
            assert len(p2['items']) == 1 and p2['items'][0]['node_id'] == f'n2_{o2}', 'o2 只见自己 1 条'
        finally:
            await db.rollback()


async def test_state_machine_validation_and_update_links() -> None:
    """⑥ 状态机：必填校验 + 非法流转 + update_issue 挂链接转 investigating。"""
    async with async_db_session() as db:
        try:
            fp = _fp()
            await _ingest(db, owner='h_o1', node='n1', events=[_ev(fingerprint=fp)])

            # code_fix 缺 fixed_in_version → 报错
            with pytest.raises(errors.RequestError):
                await error_issue_service.resolve_issue(
                    db, fingerprint=fp, actor_hasn_id='h_ops', status='resolved',
                    resolution_type='code_fix', resolution_note='缺版本',
                )
            # duplicate 缺 duplicate_of_fingerprint → 报错
            with pytest.raises(errors.RequestError):
                await error_issue_service.resolve_issue(
                    db, fingerprint=fp, actor_hasn_id='h_ops', status='resolved',
                    resolution_type='duplicate', resolution_note='缺重复指向',
                )
            # 空 resolution_note → 报错
            with pytest.raises(errors.RequestError):
                await error_issue_service.resolve_issue(
                    db, fingerprint=fp, actor_hasn_id='h_ops', status='wontfix',
                    resolution_type='external', resolution_note='   ',
                )

            # update_issue：open → investigating + 挂 issue/pr 链接
            detail = await error_issue_service.update_issue(
                db, fingerprint=fp, actor_hasn_id='h_ops',
                issue_url='https://git/issues/1', pr_url='https://git/pr/2', note='建了 issue',
            )
            assert detail['status'] == 'investigating'
            assert detail['issue_url'] == 'https://git/issues/1'
            assert detail['pr_url'] == 'https://git/pr/2'

            # 结案 resolved（investigating → resolved 合法）
            done = await error_issue_service.resolve_issue(
                db, fingerprint=fp, actor_hasn_id='h_ops', status='resolved',
                resolution_type='code_fix', resolution_note='修好了', fixed_in_version='2.0.0',
            )
            assert done['status'] == 'resolved'
            # 非法流转：resolved → skipped（resolved 只能回 open/investigating）
            with pytest.raises(errors.RequestError):
                await error_issue_service.resolve_issue(
                    db, fingerprint=fp, actor_hasn_id='h_ops', status='skipped',
                    resolution_type='not_a_bug', resolution_note='非法',
                )
        finally:
            await db.rollback()


async def test_list_issues_keyset_pagination() -> None:
    """⑦ list_issues：limit + next_cursor 顺游标翻页，last_seen DESC。"""
    async with async_db_session() as db:
        try:
            # 5 个 open issue，last_seen 递增（t0..t4）；DESC 排序应 t4→t0
            fps = []
            for i in range(5):
                fp = _fp()
                fps.append(fp)
                await _ingest(
                    db, owner='h_o1', node='n1',
                    events=[_ev(fingerprint=fp, occurred_at=_T0 + timedelta(minutes=i))],
                )
            page1 = await error_issue_service.list_issues(db, status='open', limit=2)
            assert len(page1['items']) == 2 and page1['next_cursor']
            page2 = await error_issue_service.list_issues(db, status='open', limit=2, cursor=page1['next_cursor'])
            assert len(page2['items']) == 2 and page2['next_cursor']
            page3 = await error_issue_service.list_issues(db, status='open', limit=2, cursor=page2['next_cursor'])
            assert len(page3['items']) == 1 and page3['next_cursor'] is None, '末页无 next_cursor'
            seen = [r['fingerprint'] for r in page1['items'] + page2['items'] + page3['items']]
            assert set(seen) == set(fps), '三页并集 = 全部 5 个，无重无漏'
            # last_seen 全局 DESC
            last_seens = [r['last_seen_at'] for r in page1['items'] + page2['items'] + page3['items']]
            assert last_seens == sorted(last_seens, reverse=True), 'last_seen DESC'
        finally:
            await db.rollback()
