"""DIAG-P3b：`hasn.diag.*` 工具真实 execute（wrapper→service→PG）进程内 E2E（零 mock）。

暴露/G1 判定由 tests/mcp/test_diag_tools_p3b.py 覆盖；本文件证明**工具体真的调对了 service**：
用 ingest_errors 播一个 issue（真 PG），再逐个调 6 个 diag 工具的 `.execute()`（运维分身 ctx），
断言读回一致、写类真流转状态。工具内部各开自己的 `async_db_session[.begin()]`（读/写），
故写类会真提交——用全局唯一 fingerprint 避免跨用例碰撞。需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from sqlalchemy import text

from backend.app.hasn_diag.service.error_report_service import IngestEvent, ingest_errors
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.diag import DIAG_TOOLS
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_TOOLS = {t.name: t for t in DIAG_TOOLS}
_T0 = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)  # 固定 occurred_at（error_report NOT NULL）
# 本文件工具体真提交（各自开 session）→ 播下的行会留库，污染断言「全量 open issue」的 keyset 测试。
# 四张 diag 表（schema=hasn_diag）都以 fingerprint 归类，用例结束按 fingerprint 精确清场（finally 兜底，异常也清）。
_DIAG_TABLES = ('error_issue_event', 'error_issue_seen', 'error_issue', 'error_report')


async def _purge(*fingerprints: str) -> None:
    async with async_db_session.begin() as db:
        for table in _DIAG_TABLES:
            await db.execute(
                text(f'DELETE FROM hasn_diag.{table} WHERE fingerprint = ANY(:fps)'), {'fps': list(fingerprints)}
            )


def _operator_ctx() -> AgentContext:
    return AgentContext(
        hasn_id='a_diag_ops',
        owner_id=0,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_diag_ops',
        session_uuid='amk_diag_ops',
    )


async def _seed_issue(fingerprint: str, *, owner: str = 'h_seed', node: str = 'n_seed') -> None:
    """真 PG 播一个 issue（一条 occurrence），已提交。"""
    async with async_db_session.begin() as db:
        await ingest_errors(
            db,
            owner_hasn_id=owner,
            node_id=node,
            app_version='1.0.0',
            platform='darwin',
            events=[
                IngestEvent(
                    local_event_id=uuid4().hex[:12],
                    source='daemon',
                    severity='error',
                    fingerprint=fingerprint,
                    dedup_key=uuid4().hex,
                    error_class='NullPointer',
                    message='空指针解引用',
                    location='backend/foo.py:mod',
                    context={},
                    occurred_at=_T0,
                    suppressed_count=0,
                )
            ],
        )


async def test_read_tools_execute_against_real_service() -> None:
    """stats/list_issues/get_issue/list_occurrences 真读回播下的 issue。"""
    fp = uuid4().hex
    await _seed_issue(fp)
    ctx = _operator_ctx()
    try:
        stats = await _TOOLS['hasn.diag.stats'].execute(ctx, {})
        assert stats['total_issues'] >= 1
        assert stats['by_status'].get('open', 0) >= 1

        listed = await _TOOLS['hasn.diag.list_issues'].execute(ctx, {'status': 'open', 'limit': 100})
        assert any(it['fingerprint'] == fp for it in listed['items']), 'list_issues 应含播下的 fp'

        detail = await _TOOLS['hasn.diag.get_issue'].execute(ctx, {'fingerprint': fp})
        assert detail['fingerprint'] == fp
        assert detail['status'] == 'open'
        assert detail['occurrence_count'] == 1
        assert 'recent_occurrences' in detail and 'events' in detail

        occ = await _TOOLS['hasn.diag.list_occurrences'].execute(ctx, {'fingerprint': fp, 'limit': 10})
        assert len(occ['items']) == 1
        assert occ['items'][0]['message'] == '空指针解引用'
    finally:
        await _purge(fp)


async def test_update_issue_execute_flows_to_investigating() -> None:
    """update_issue：挂 issue/PR 链接 → 状态流转 investigating + 写事件（actor=运维分身）。"""
    fp = uuid4().hex
    await _seed_issue(fp)
    ctx = _operator_ctx()
    try:
        result = await _TOOLS['hasn.diag.update_issue'].execute(
            ctx, {'fingerprint': fp, 'issue_url': 'https://github.com/x/y/issues/1', 'note': '开查'}
        )
        assert result['status'] == 'investigating'
        assert result['issue_url'] == 'https://github.com/x/y/issues/1'
        # 写事件留痕：actor 为运维分身 hasn_id
        assert any(
            ev['actor_hasn_id'] == 'a_diag_ops' and ev['to_status'] == 'investigating' for ev in result['events']
        )
    finally:
        await _purge(fp)


async def test_resolve_issue_execute_closes_with_result() -> None:
    """resolve_issue：code_fix 结案 resolved + 必填 fixed_in_version。"""
    fp = uuid4().hex
    await _seed_issue(fp)
    ctx = _operator_ctx()
    try:
        result = await _TOOLS['hasn.diag.resolve_issue'].execute(
            ctx,
            {
                'fingerprint': fp,
                'status': 'resolved',
                'resolution_type': 'code_fix',
                'resolution_note': '修了空指针',
                'fixed_in_version': '1.2.3',
            },
        )
        assert result['status'] == 'resolved'
        assert result['resolution_type'] == 'code_fix'
        assert result['fixed_in_version'] == '1.2.3'
        assert result['resolved_by'] == 'a_diag_ops'
    finally:
        await _purge(fp)


async def test_resolve_issue_execute_validates_required_fields() -> None:
    """§6 必填校验经工具体传导：code_fix 缺 fixed_in_version → RequestError。"""
    from backend.common.exception import errors

    fp = uuid4().hex
    await _seed_issue(fp)
    ctx = _operator_ctx()
    try:
        with pytest.raises(errors.RequestError):
            await _TOOLS['hasn.diag.resolve_issue'].execute(
                ctx,
                {'fingerprint': fp, 'status': 'resolved', 'resolution_type': 'code_fix', 'resolution_note': '忘了版本'},
            )
    finally:
        await _purge(fp)
