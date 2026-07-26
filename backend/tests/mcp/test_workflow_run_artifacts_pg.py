"""平台工具 · hasn.workflow.run_artifacts 真实 PostgreSQL 测试（零 mock，doc36 §6.2 · U5b）。

`run_artifacts` 是「场景成果总览」的基石：汇总节点跑本工具，一次取回本次 run **全节点**的产出清单，
每条产物带 uri，供分身编织总览 / webui 渲染资源栏。本测试用真库真行钉死它的契约：

- **零入参反查**：不传 workflow_run_uuid 时，据当前会话（`ctx.session_id`，即汇总节点自己的
  work_session_id）反查所属 run —— 这是分身实际调用的默认路径。
- **拓扑序**：节点按 graph_snapshot 依赖拓扑序返回（parent 先于 child，tiebreak=声明序），
  不是 created_time；同一 run 两次查询顺序稳定。
- **只取 current 版本**：节点 artifacts JSON 的 `is_current` —— 缺省视为 current，显式 False 剔除。
- **owner 隔离**：run.owner_id ≠ 调用者 → NotFound（与 artifact.get 一致），别人的 run 打不开。
- **产物投影**：uri = hasn_artifacts.resource_uri（§4.1 命名约定），带 title/resource_kind/source_app_id/created_time；
  已删（status=deleted）/ 跨户的产物自然缺席，不造假。
- **显式入参路径**：传 workflow_run_uuid 直取，不依赖会话。

seed 走 U5a 的真实写者 `workflow_sync_service.sync_node_runs`（run + node_run 落云端）+ ORM 落
hasn_artifacts，与生产写路径同源。读走 agent 实际调用的 `_WorkflowTool.execute`（自开会话，故 seed
必须先 commit）。需活体 DB（本地 15432）：

    DATABASE_PORT=15432 pytest backend/tests/mcp/test_workflow_run_artifacts_pg.py

无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_task.schema.workflow_sync import (
    WorkflowNodeRunsSyncRequest,
    WorkflowNodeRunUpstream,
    WorkflowRunUpstream,
)
from backend.app.hasn_task.service.workflow_sync_service import workflow_sync_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.workflow import WORKFLOW_TOOLS
from backend.common.exception import errors

pytestmark = pytest.mark.asyncio


def _tool(name: str):
    for t in WORKFLOW_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'workflow 工具未注册: {name}')


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _ctx(owner: str, *, session_id: str | None = None, agent: str = 'a_run_arts') -> AgentContext:
    ctx = AgentContext(
        hasn_id=agent,
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='汇总分身',
        owner_hasn_id=owner,
        session_uuid='amk_run_arts',
    )
    # 零入参反查靠此字段（server.call_tool 从 _hasn_session_id 剥离后灌入）
    ctx.session_id = session_id
    return ctx


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


def _art_entry(artifact_id: str, is_current: bool | None = None) -> dict:
    """节点 artifacts JSON 一条：daemon 上行的 [{artifact_id,is_current,...}] 形状。"""
    entry: dict = {'artifact_id': artifact_id}
    if is_current is not None:
        entry['is_current'] = is_current
    return entry


def _seed_artifact(owner: str, artifact_id: str, *, status: str = 'active') -> HasnArtifacts:
    """按权威投影字段登记一条 hasn_artifacts（uri = resource_uri）。"""
    return HasnArtifacts(
        artifact_id=artifact_id,
        agent_hasn_id=f'hasnAgent_{_uid()}',
        owner_hasn_id=owner,
        artifact_key=f'workflow-run-artifacts:{artifact_id}',
        kind='resource',
        resource_kind='knowledge.base',
        title=f'产物-{artifact_id}',
        resource_uri=f'hasn://knowledge/{artifact_id}',
        source_app_id='knowledge',
        source_kind='app_write',
        status=status,
    )


@pytest.mark.asyncio(loop_scope='module')
async def test_run_artifacts_real_db() -> None:
    """真实 PG：seed 三节点 run（research/compete→summary）+ 产物 → 六路契约断言 → 清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.database.db import async_db_session

    owner = f'h_ra_{_uid()}'
    other_owner = f'h_ra_other_{_uid()}'
    run_uuid = f'wfr_{_uid()}'
    wf = f'wf_{_uid()}'
    ws_summary = f'ws_sum_{_uid()}'

    # 产物 id（cloud 权威 art_<ulid>）
    art_r_cur = f'art_rcur_{_uid()}'   # research current，应出现
    art_r_old = f'art_rold_{_uid()}'   # research is_current=False，节点级剔除
    art_r_del = f'art_rdel_{_uid()}'   # research current 但 hasn_artifacts.status=deleted，投影级剔除
    art_c_cur = f'art_ccur_{_uid()}'   # compete 无 is_current 键 → 视为 current，应出现

    # 拓扑：research、compete 都指向 summary → 顺序 [research, compete, summary]（tiebreak 声明序）
    snapshot = {
        'nodes': [{'node_key': 'research'}, {'node_key': 'compete'}, {'node_key': 'summary'}],
        'edges': [['research', 'summary'], ['compete', 'summary']],
    }

    # ── seed（.begin() 提交，供工具的独立会话可见）─────────────────────────────
    async with async_db_session.begin() as db:
        res = await workflow_sync_service.sync_node_runs(
            db,
            WorkflowNodeRunsSyncRequest(
                runs=[
                    WorkflowRunUpstream(
                        workflow_run_uuid=run_uuid,
                        workflow_uuid=wf,
                        dedupe_key=f'{wf}:1',
                        status='running',
                        graph_snapshot=snapshot,
                    )
                ],
                node_runs=[
                    WorkflowNodeRunUpstream(
                        node_run_uuid=f'ndr_r_{_uid()}',
                        workflow_run_uuid=run_uuid,
                        workflow_uuid=wf,
                        node_key='research',
                        status='done',
                        work_session_id=f'ws_research_{_uid()}',
                        artifacts=[
                            _art_entry(art_r_cur, is_current=True),
                            _art_entry(art_r_old, is_current=False),
                            _art_entry(art_r_del, is_current=True),
                        ],
                        output_summary='调研完成',
                    ),
                    WorkflowNodeRunUpstream(
                        node_run_uuid=f'ndr_c_{_uid()}',
                        workflow_run_uuid=run_uuid,
                        workflow_uuid=wf,
                        node_key='compete',
                        status='done',
                        work_session_id=f'ws_compete_{_uid()}',
                        artifacts=[_art_entry(art_c_cur)],  # 无 is_current 键 → 视为 current
                        output_summary='竞品完成',
                    ),
                    WorkflowNodeRunUpstream(
                        node_run_uuid=f'ndr_s_{_uid()}',
                        workflow_run_uuid=run_uuid,
                        workflow_uuid=wf,
                        node_key='summary',
                        status='running',
                        work_session_id=ws_summary,  # 汇总节点自己的会话 = 反查依据
                        artifacts=[],
                        output_summary=None,
                    ),
                ],
            ),
            owner_id=owner,
        )
        assert res.rejected == []
        assert (res.accepted_runs, res.accepted_node_runs) == (1, 3)

        db.add(_seed_artifact(owner, art_r_cur))
        db.add(_seed_artifact(owner, art_r_old))
        db.add(_seed_artifact(owner, art_r_del, status='deleted'))  # 投影级应被剔除
        db.add(_seed_artifact(owner, art_c_cur))

    tool = _tool('hasn.workflow.run_artifacts')
    try:
        # ① 零入参反查：汇总节点会话 → 命中本 run
        out = await tool.execute(_ctx(owner, session_id=ws_summary), {})
        assert out['workflow_run_uuid'] == run_uuid

        nodes = out['nodes']
        # ② 拓扑序 + 声明序 tiebreak
        assert [n['node_key'] for n in nodes] == ['research', 'compete', 'summary']

        by_key = {n['node_key']: n for n in nodes}
        # ③ 节点态透出
        assert by_key['research']['status'] == 'done'
        assert by_key['research']['output_summary'] == '调研完成'
        assert by_key['summary']['status'] == 'running'

        # ④ 只取 current + 投影级剔除已删：research 仅剩 art_r_cur
        r_arts = by_key['research']['artifacts']
        assert [a['artifact_id'] for a in r_arts] == [art_r_cur]
        # ⑤ 产物投影字段（uri = resource_uri）
        a0 = r_arts[0]
        assert a0['uri'] == f'hasn://knowledge/{art_r_cur}'
        assert a0['resource_kind'] == 'knowledge.base'
        assert a0['source_app_id'] == 'knowledge'
        assert a0['title'] == f'产物-{art_r_cur}'
        assert a0['created_time'] is not None
        # compete 无 is_current 键 → 视为 current，命中
        assert [a['artifact_id'] for a in by_key['compete']['artifacts']] == [art_c_cur]
        # summary 无产物
        assert by_key['summary']['artifacts'] == []
        # 未截断 → 不带 artifacts_truncated 标记
        assert 'artifacts_truncated' not in by_key['research']

        # ⑥ 显式入参路径（不依赖会话）：无 session 的 ctx 传 run_uuid 直取
        out2 = await tool.execute(_ctx(owner, session_id=None), {'workflow_run_uuid': run_uuid})
        assert out2['workflow_run_uuid'] == run_uuid
        assert [n['node_key'] for n in out2['nodes']] == ['research', 'compete', 'summary']

        # ⑦ owner 隔离：别人拿 run_uuid 打不开
        with pytest.raises(errors.NotFoundError):
            await tool.execute(_ctx(other_owner, session_id=None), {'workflow_run_uuid': run_uuid})

        # ⑧ 零入参但无会话上下文 → RequestError（诚实报错，不空转）
        with pytest.raises(errors.RequestError):
            await tool.execute(_ctx(owner, session_id=None), {})

        # ⑨ 会话查无 run → NotFound
        with pytest.raises(errors.NotFoundError):
            await tool.execute(_ctx(owner, session_id=f'ws_ghost_{_uid()}'), {})
    finally:
        async with async_db_session.begin() as db:
            await db.execute(
                text('DELETE FROM public.hasn_artifacts WHERE owner_hasn_id = :o'), {'o': owner}
            )
            await db.execute(
                text('DELETE FROM hasn_task.workflow_node_run WHERE owner_id = :o'), {'o': owner}
            )
            await db.execute(
                text('DELETE FROM hasn_task.workflow_run WHERE owner_id = :o'), {'o': owner}
            )
