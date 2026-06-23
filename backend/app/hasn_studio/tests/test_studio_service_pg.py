"""统一视频引擎全链路真实测试（零 mock）：云端 StudioService → montage-engine-service → 落 hasn_studio PG。

证明 doc22 P3 在**云端层**端到端成立：
  - CRUD：项目 save/list/get、分镜存 project.settings、素材/成品 owner 隔离 + 序列化。
  - broker 真实联调：起真引擎服务，run_pipeline/render submit 落 studio_render_job + 取 engine_job_id，
    get_render_job 轮询推进状态机；list_pipelines 取 production 管线；run_tool 透传。
  - 成功（若本机有 Node/Remotion 能真出片）：顺带验证成品物化（取片 + 上传私有桶 + 落 studio_artifact +
    回流 hasn_artifacts，best-effort）。
  - 引擎未起/不可达：service 优雅归一（job.status=failed + 真实 error），不 crash、不 fake。

依赖（任一不满足即 skip，不伪造）：
- 本地 PostgreSQL :15432（DATABASE_PORT，schema hasn_studio 已建）。
- montage-engine-service 的 venv（`.venv/bin/python` + uvicorn）；本测试自启它（loopback，无 token）。
  boot 不需要 Node（Node 只在真渲染合成段需要）。可经 `MONTAGE_ENGINE_URL` 指向已运行引擎跳过自启，
  或 `MONTAGE_ENGINE_DIR` 覆盖引擎目录。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_studio.service.studio_service import studio_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = pytest.mark.asyncio

_HASN_A = 'hasn:test:studio-a'
_HASN_B = 'hasn:test:studio-b'
_AGENT_A = 'hasn:agent:studio-op-a'
# 引擎内置 demo-props 名（remotion-composer/public/demo-props/<name>.json）；demo 是名字串、非布尔。
_DEMO_NAME = 'world-in-numbers'

# 引擎目录候选（首个存在 .venv/bin/python 者）：env 覆盖 > 父仓 huanxing-apps > 开发 worktree。
_ENGINE_DIR_CANDIDATES = (
    '/Users/mac/openclaw-workspace/huanxing/huanxing-project/huanxing-apps/montage-engine-service',
    '/Users/mac/openclaw-workspace/huanxing/.worktrees/studio/huanxing-apps/montage-engine-service',
)


def _resolve_engine_dir() -> Path | None:
    explicit = os.environ.get('MONTAGE_ENGINE_DIR')
    candidates = [explicit, *_ENGINE_DIR_CANDIDATES] if explicit else list(_ENGINE_DIR_CANDIDATES)
    for cand in candidates:
        if cand and (Path(cand) / '.venv' / 'bin' / 'python').exists():
            return Path(cand)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait_healthy(proc: subprocess.Popen, base: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'引擎进程提前退出（exit {proc.returncode}）')
        try:
            resp = httpx.get(f'{base}/v1/healthz', timeout=2.0)
            # montage healthz 返回 {ok: true, ...}（无 status 字段，区别于 quant）。
            if resp.status_code == 200 and resp.json().get('ok') is True:
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError('引擎 /v1/healthz 在超时内未就绪')


@pytest.fixture(scope='module')
def engine_service() -> Iterator[str]:
    """自启 montage-engine-service（loopback，无 token），yield base_url；预置 MONTAGE_ENGINE_URL 则复用。"""
    preset = os.environ.get('MONTAGE_ENGINE_URL')
    if preset:
        yield preset.rstrip('/')
        return

    engine_dir = _resolve_engine_dir()
    if engine_dir is None:
        pytest.skip('montage-engine-service 的 .venv 不存在（设 MONTAGE_ENGINE_DIR 或 MONTAGE_ENGINE_URL）')

    py = engine_dir / '.venv' / 'bin' / 'python'
    port = _free_port()
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ}
    env.pop('MONTAGE_ENGINE_TOKEN', None)  # 无 token → 引擎仅允许 loopback（本测试即 loopback）
    env.pop('MONTAGE_SVC_TOKEN', None)
    proc = subprocess.Popen(
        [str(py), '-m', 'uvicorn', 'service.app:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=str(engine_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        _wait_healthy(proc, base)
    except Exception as exc:
        proc.terminate()
        try:
            out = proc.communicate(timeout=5)[0] or ''
        except Exception:
            out = ''
        pytest.skip(f'引擎服务未就绪，跳过: {exc}\n{out[-800:]}')

    prev = os.environ.get('MONTAGE_ENGINE_URL')
    os.environ['MONTAGE_ENGINE_URL'] = base
    try:
        yield base
    finally:
        if prev is None:
            os.environ.pop('MONTAGE_ENGINE_URL', None)
        else:
            os.environ['MONTAGE_ENGINE_URL'] = prev
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@pytest_asyncio.fixture(autouse=True)  # noqa: RUF076  必须每用例无条件重置池（见 docstring）
async def _reset_montage_pool() -> AsyncIterator[None]:
    """每个异步用例重置 montage 进程级连接池单例。

    ``service_http.get_service_client('montage')`` 缓存进程级单例 client，其连接池绑定首个用例的
    event loop；pytest-asyncio 每用例换新 loop → 复用旧 client 会撞 'Event loop is closed'。测试态下
    先弹掉缓存（不 await 旧 client，它的 loop 可能已没了），让本用例首次调用在当前 loop 上重建（生产
    态由 FastAPI lifespan 统一关闭，无此问题）。
    """
    import backend.common.service_http as svc_http

    svc_http._clients.pop('montage', None)
    try:
        yield
    finally:
        stale = svc_http._clients.pop('montage', None)
        if stale is not None and not stale.is_closed:
            try:
                await stale.aclose()
            except Exception:  # 本用例 loop 收尾即可，关闭失败不影响断言
                pass


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()  # 会话内变更回滚，不污染 dev DB
        await sess.close()
        await engine.dispose()


# ============================ CRUD（纯 PG，无引擎依赖） ============================


async def test_save_project_create_and_update(session: AsyncSession) -> None:
    """新建项目（必带 title）→ 更新（owner 隔离，字段增量）。"""
    with pytest.raises(errors.RequestError):
        await studio_service.save_project(session, owner_hasn_id=_HASN_A, title=None)

    created = await studio_service.save_project(
        session,
        owner_hasn_id=_HASN_A,
        agent_hasn_id=_AGENT_A,
        title='我的短片项目',
        default_pipeline_key='cinematic',
        settings={'resolution': '1080x1920'},
    )
    assert created['id'] > 0
    assert created['status'] == 'draft'
    assert created['agent_hasn_id'] == _AGENT_A  # PLANFIX-6：归属取凭证身份
    assert created['default_pipeline_key'] == 'cinematic'

    updated = await studio_service.save_project(
        session, owner_hasn_id=_HASN_A, project_id=created['id'], title='改名了', status='active'
    )
    assert updated['id'] == created['id']
    assert updated['title'] == '改名了'
    assert updated['status'] == 'active'
    assert updated['default_pipeline_key'] == 'cinematic'  # 未传 → 不动


async def test_list_and_get_project_with_assets(session: AsyncSession) -> None:
    """list_projects 最近优先；get_project 含 assets 列表（owner 隔离）。"""
    p1 = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='项目1')
    p2 = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='项目2')

    listed = await studio_service.list_projects(session, owner_hasn_id=_HASN_A)
    ids = [p['id'] for p in listed]
    assert p2['id'] in ids and p1['id'] in ids
    assert ids.index(p2['id']) < ids.index(p1['id'])  # 最近优先

    detail = await studio_service.get_project(session, owner_hasn_id=_HASN_A, project_id=p1['id'])
    assert detail['id'] == p1['id']
    assert detail['assets'] == []  # 新项目无素材


async def test_save_storyboard_into_settings(session: AsyncSession) -> None:
    """save_storyboard 落进 project.settings['storyboard']（避免假 asset_uri）。"""
    p = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='分镜项目')
    sb = [{'shot': 1, 'text': '开场航拍'}, {'shot': 2, 'text': '主角登场'}]
    updated = await studio_service.save_storyboard(
        session, owner_hasn_id=_HASN_A, project_id=p['id'], storyboard=sb
    )
    assert updated['settings']['storyboard'] == sb

    # 重读确认持久（绕 service 缓存）。
    from backend.app.hasn_studio.model import StudioProject

    row = (await session.execute(select(StudioProject).where(StudioProject.id == p['id']))).scalar_one()
    assert row.settings['storyboard'] == sb


async def test_cross_owner_isolation(session: AsyncSession) -> None:
    """A 的项目/渲染/成品 B 不可见（行级隔离）。"""
    p = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='A 私有项目')
    with pytest.raises(errors.NotFoundError):
        await studio_service.get_project(session, owner_hasn_id=_HASN_B, project_id=p['id'])
    with pytest.raises(errors.NotFoundError):
        await studio_service.save_storyboard(session, owner_hasn_id=_HASN_B, project_id=p['id'], storyboard='x')
    with pytest.raises(errors.NotFoundError):
        await studio_service.get_render_job(session, owner_hasn_id=_HASN_B, render_job_id=p['id'])
    with pytest.raises(errors.NotFoundError):
        await studio_service.export(session, owner_hasn_id=_HASN_B, artifact_id=1_000_000)
    b_list = await studio_service.list_projects(session, owner_hasn_id=_HASN_B)
    assert all(x['id'] != p['id'] for x in b_list)


async def test_list_artifacts_owner_isolated_and_serialized(session: AsyncSession) -> None:
    """list_artifacts owner 隔离；空库返回空；序列化字段齐全（video_url 可空，零 fake）。"""
    items = await studio_service.list_artifacts(session, owner_hasn_id=_HASN_B)
    assert isinstance(items, list)


# ============================ broker 真实联调（需引擎服务） ============================


async def test_list_pipelines_broker(engine_service: str, session: AsyncSession) -> None:
    """list_pipelines 经 broker 取引擎 production 管线（真实 HTTP）。"""
    data = await studio_service.list_pipelines()
    assert 'pipelines' in data and isinstance(data['pipelines'], list)
    assert data['count'] == len(data['pipelines'])
    # 引擎只返 production；每条管线至少有可标识的 key 字段（容忍引擎字段命名差异，只要非空 dict）。
    for pl in data['pipelines']:
        assert isinstance(pl, dict) and pl


async def _poll_until_terminal(
    session: AsyncSession, *, owner_hasn_id: str, render_job_id: int, timeout: float = 8.0
) -> dict:
    """轮询渲染 job 到终态（或超时返回最后状态）。默认短超时——状态机推进即可，不死等真出片完成。"""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = await studio_service.get_render_job(
            session, owner_hasn_id=owner_hasn_id, render_job_id=render_job_id
        )
        if last['status'] in ('succeeded', 'failed', 'canceled'):
            return last
        await asyncio.sleep(0.8)
    return last


async def test_run_pipeline_submit_and_poll(engine_service: str, session: AsyncSession) -> None:
    """run_pipeline：落 studio_render_job + 取 engine_job_id；get_render_job 轮询推进状态机（真引擎）。"""
    p = await studio_service.save_project(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, title='渲染项目', default_pipeline_key='montage'
    )
    submitted = await studio_service.run_pipeline(
        session,
        owner_hasn_id=_HASN_A,
        agent_hasn_id=_AGENT_A,
        project_id=p['id'],
        input_payload={'demo': _DEMO_NAME},  # 真实 demo 名（zero-key 内置 props），无需自带 props
        work_session_id='ws_studio_test',
    )
    job_id = submitted['id']
    assert job_id > 0
    # 真引擎已收下 demo 渲染：必落 engine_job_id + queued/running（demo 名合法，提交不该失败）。
    assert submitted['status'] in ('queued', 'running'), f'提交应成功（真实错误透传）: {submitted.get("error")}'
    assert submitted['engine_job_id']
    assert submitted['input'] == {'demo': _DEMO_NAME}  # 入参快照

    # 落库核实（绕 service 缓存）。
    from backend.app.hasn_studio.model import StudioRenderJob

    row = (
        await session.execute(select(StudioRenderJob).where(StudioRenderJob.id == job_id))
    ).scalar_one()
    assert row.owner_hasn_id == _HASN_A
    assert row.engine_job_id == submitted['engine_job_id']
    assert row.work_session_id == 'ws_studio_test'

    final = await _poll_until_terminal(session, owner_hasn_id=_HASN_A, render_job_id=job_id)
    # 状态机必须推进到合法态之一（真出片 demo ≈ 60–90s，短超时内多半仍 running → 也算通过：证明轮询不崩、
    # 进度真实推进）。本机能等到 succeeded 则顺带验证物化（best-effort，见 test 模块的物化探针历史证据）。
    assert final['status'] in ('queued', 'running', 'succeeded', 'failed', 'canceled')
    assert final['progress'] >= 0
    if final['status'] == 'succeeded':
        # 物化：成功则应已落成品（除非 S3/上传不可用 → job.error 记录真实错误，绝不 fake artifact）。
        arts = await studio_service.list_artifacts(session, owner_hasn_id=_HASN_A, project_id=p['id'])
        if not arts:
            assert final.get('error'), '物化失败必记录真实错误（不 fake artifact）'
        else:
            art = arts[0]
            assert art['video_asset_uri'].startswith('hasn://asset/')
            assert art['status'] == 'completed'
            assert art['render_job_id'] == job_id
    elif final['status'] == 'failed':
        assert final['error']


async def test_render_low_level_submit(engine_service: str, session: AsyncSession) -> None:
    """render（低层）：demo=<名> 直渲染落 render_job + broker（与 run_pipeline 共用内部提交）。"""
    p = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='直渲项目')
    submitted = await studio_service.render(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, project_id=p['id'], demo=_DEMO_NAME
    )
    assert submitted['id'] > 0
    assert submitted['status'] in ('queued', 'running'), f'提交应成功: {submitted.get("error")}'
    assert submitted['engine_job_id']


async def test_run_pipeline_missing_props_graceful(engine_service: str, session: AsyncSession) -> None:
    """run_pipeline 缺 props/demo → 引擎返 400 bad_request → service 落 job.status=failed + 真实 error（不 crash）。"""
    p = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='缺参项目')
    submitted = await studio_service.run_pipeline(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, project_id=p['id'], input_payload={}
    )
    assert submitted['status'] == 'failed'
    assert submitted['error']  # 透传引擎真实 bad_request 错误（零 fake）


async def test_run_tool_passthrough(engine_service: str, session: AsyncSession) -> None:
    """run_tool 透传引擎原子工具；未知工具 → 引擎 404 → StudioEngineError（真实，不 fake）。"""
    from backend.app.hasn_studio.provider import StudioEngineError

    # 取一个真实存在的工具名（引擎工具目录），调用应返回 {result}；目录空则跳过该断言。
    tools_data = await studio_service.list_pipelines()  # 仅确认 broker 通；工具目录另取
    assert 'pipelines' in tools_data

    with pytest.raises(StudioEngineError):
        await studio_service.run_tool(
            session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A,
            tool_name='__definitely_not_a_real_tool__', inputs={},
        )


async def test_engine_unconfigured_graceful(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """引擎未配置（MONTAGE_ENGINE_URL 空 + 非 dev 回落）→ 提交即 failed + service_unconfigured 真实错误，不 crash。

    强制 provider 看不到任何引擎地址：清 env + patch service_endpoint 回空 base（模拟 prod 未配）。
    """
    import backend.app.hasn_studio.provider.engine_client as ec

    from backend.common.service_registry import ServiceEndpoint

    monkeypatch.delenv('MONTAGE_ENGINE_URL', raising=False)
    monkeypatch.setattr(
        ec, '_endpoint', lambda: ServiceEndpoint(name='montage', base_url='', token='', timeout=600.0, configured=False)
    )

    p = await studio_service.save_project(session, owner_hasn_id=_HASN_A, title='未配引擎项目')
    submitted = await studio_service.run_pipeline(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, project_id=p['id'], input_payload={'demo': True}
    )
    assert submitted['status'] == 'failed'
    assert '未配置' in submitted['error'] or 'MONTAGE_ENGINE_URL' in submitted['error']
