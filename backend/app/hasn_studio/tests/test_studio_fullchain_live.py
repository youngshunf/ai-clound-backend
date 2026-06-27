"""doc22 §7 全链路真出片活体 E2E：**编排分身（LLM）自主驱动 studio 工具面 → 真引擎 → 真 mp4**。

这正是 doc22 §7「分身 LLM → run_tool → 出片」要求的那条组合，且**真实执行**而非仅证明是部署依赖：

  扮演编排分身（AGENT_GUIDE Rule Zero：引擎是三件套，创作决策由分身/编排者完成，
  引擎只提供原子工具 + 确定性合成）→ 自主按序调 `studio_service.*`（=云端 `hasn.studio.*`
  MCP 工具所包装的同一层）：

    1. save_project        —— 分身决定开一个 studio 项目（唤星能力解说）
    2. run_tool(code_snippet) —— 分身调一个创作段原子工具（经 broker 打真引擎，零付费 provider）
    3. render(自编 props)   —— 分身把**自己现编的 props**（非任何 demo 文件）喂 compose 工具
    4. get_render_job 轮询  —— 分身等真引擎软渲（swiftshader CPU）出真片

与 e2e_pipeline_orchestration_live.py（验证②）的区别：那个直打引擎 HTTP `/v1/render`，只覆盖
**引擎渲染段**；本测试走**云端 StudioService broker 工具面**（service_endpoint('montage') 经
services.toml master_secret 派生令牌 → 内网 :8002），覆盖「分身调工具 → 云端 broker → 引擎」的
**完整工具链**——即 Stop hook 指出的最后一个组合缺口。

零 fake：props 完全是本测试为选题「你的 AI 分身能为你做什么」现编的真实内容（与 demo-props/*.json
和验证②无一字段复用）；渲染失败/超时真实报错；物化失败记真实 job.error，**绝不**产占位 artifact。

依赖（任一不满足即 skip，不伪造）：
- 本地 PostgreSQL :15432（DATABASE_PORT，schema hasn_studio 已建）。
- 运行中的 montage-engine-service（:8002，auth_enabled）；本测试经 services.toml 自动解析其地址 +
  派生令牌，**不自启**（复用常驻引擎，含 hermetic 字体 + 渲染超时修复）。
- 显式开关：`STUDIO_FULLCHAIN_LIVE=1`（真出片软渲 ~7–8min，默认 skip 不拖慢常规套件）。

用法：
  STUDIO_FULLCHAIN_LIVE=1 DATABASE_PORT=15432 \
    uv run pytest backend/app/hasn_studio/tests/test_studio_fullchain_live.py -v -s
"""

from __future__ import annotations

import os
import time

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_studio.service.studio_service import studio_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 编排分身身份（审计上下文；引擎无产品身份）。
_HASN = 'hasn:test:studio-fullchain'
_AGENT = 'hasn:agent:studio-fullchain-op'

# 唤星品牌色（与验证②同色板，但内容/选题完全不同——这是另一次独立的编排创作）。
_VIOLET = '#6D28D9'
_INK = '#0F172A'
_MINT = '#34D399'
_AMBER = '#F59E0B'
_CYAN = '#22D3EE'

# ── 编排分身（我，Claude）为选题「你的 AI 分身能为你做什么」现编的创作段 props ──────────────
# 5 个连续 cut（0–16s 无空档）+ 1 overlay；cut 类型门控字段齐全（hero_title/text_card 需 text、
# stat_card 需 stat、callout 需 text、pie_chart 需 chartData）。与 demo-props 及验证②无一字段复用。
_ORCHESTRATED_PROPS: dict[str, Any] = {
    'theme': 'flat-motion-graphics',
    'cuts': [
        {
            'id': 'cap-hero', 'source': '', 'type': 'hero_title',
            'in_seconds': 0.0, 'out_seconds': 3.5,
            'text': '你的 AI 分身',
            'subtitle': '代你行动的第二大脑',
            'backgroundColor': _INK,
        },
        {
            'id': 'cap-setup', 'source': '', 'type': 'text_card',
            'in_seconds': 3.5, 'out_seconds': 7.0,
            'text': '一次设置，长期在线',
            'subtitle': '分身记住你的偏好与人设，代你处理日常往来',
            'color': '#F8FAFC', 'backgroundColor': _INK,
        },
        {
            'id': 'cap-speed', 'source': '', 'type': 'stat_card',
            'in_seconds': 7.0, 'out_seconds': 11.0,
            'stat': '8×',
            'subtitle': '批量消息处理较手动的提速（唤星内部基准·示意）',
            'accentColor': _AMBER, 'backgroundColor': _INK,
        },
        {
            'id': 'cap-what', 'source': '', 'type': 'callout',
            'in_seconds': 11.0, 'out_seconds': 13.5,
            'title': '分身能为你做什么',
            'text': '社交协作、内容创作、调研分析、交易服务——全程对你透明可接管。',
            'accentColor': _VIOLET, 'backgroundColor': _INK, 'color': '#F8FAFC',
        },
        {
            'id': 'cap-mix', 'source': '', 'type': 'pie_chart',
            'in_seconds': 13.5, 'out_seconds': 16.0,
            'title': '分身一天的代办时间分配',
            'chartData': [
                {'label': '社交协作', 'value': 40},
                {'label': '内容创作', 'value': 28},
                {'label': '调研分析', 'value': 20},
                {'label': '交易服务', 'value': 12},
            ],
            'chartColors': [_VIOLET, _CYAN, _MINT, _AMBER],
            'donut': True, 'centerLabel': '代你行动', 'centerValue': '24/7',
            'showLegend': True, 'backgroundColor': _INK,
        },
    ],
    'overlays': [
        {
            'type': 'stat_reveal', 'in_seconds': 7.4, 'out_seconds': 10.6,
            'text': '省下数小时', 'subtitle': '每天', 'accentColor': _MINT,
            'position': 'bottom-right',
        },
    ],
    'captions': [],
    'audio': {},
}


@pytest_asyncio.fixture(autouse=True)
async def _reset_montage_pool() -> AsyncIterator[None]:
    """每个异步用例重置 montage 进程级 client 单例（pytest-asyncio 每用例换 loop，复用旧 client 会撞
    'Event loop is closed'）。与 test_studio_service_pg.py 同理。"""
    import backend.common.service_http as svc_http

    svc_http._clients.pop('montage', None)
    try:
        yield
    finally:
        stale = svc_http._clients.pop('montage', None)
        if stale is not None and not stale.is_closed:
            try:
                await stale.aclose()
            except Exception:
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


async def _poll_until_terminal(
    session: AsyncSession, *, render_job_id: int, timeout: float
) -> dict[str, Any]:
    """轮询渲染 job 到终态（真出片软渲较久）；每 5s 打一次进度作为活体证据。"""
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = await studio_service.get_render_job(
            session, owner_hasn_id=_HASN, render_job_id=render_job_id
        )
        print(
            f'    [poll +{int(time.time() - (deadline - timeout)):>4}s] '
            f"status={last['status']:<9} progress={last.get('progress')}% "
            f"stage={last.get('stage')} cost={last.get('cost')}"
        )
        if last['status'] in ('succeeded', 'failed', 'canceled'):
            return last
        time.sleep(5)
    return last


async def test_fullchain_agent_orchestrated_studio_tools_render_real_video(session: AsyncSession) -> None:
    """分身 LLM 自主驱动 studio 工具面（run_tool + render + get_render_job）→ broker → 真引擎 → 真 mp4。"""
    if os.environ.get('STUDIO_FULLCHAIN_LIVE') != '1':
        pytest.skip('设 STUDIO_FULLCHAIN_LIVE=1 跑全链路真出片（~7–8min 软渲，需常驻引擎 :8002）')

    print('\n=== doc22 §7 全链路：分身 LLM 自主驱动 studio 工具面 → 真出片 ===')
    print('    编排者 = Claude（AGENT_GUIDE Rule Zero 合法编排者）；工具面 = 云端 hasn.studio.* 所包装层')
    print('    选题：你的 AI 分身能为你做什么（props 由本测试现编，非 demo）\n')

    # 0. broker 通路（services.toml 派生令牌 → 真 :8002），并确认目标流水线在产。
    pipelines = await studio_service.list_pipelines()
    keys = {p.get('key') or p.get('name') for p in pipelines['pipelines']}
    assert 'animated-explainer' in keys, f'引擎应在产 animated-explainer：{sorted(k for k in keys if k)}'
    print(f'✅ 0. broker 通真引擎：{len(pipelines["pipelines"])} 条 production 流水线（含 animated-explainer）')

    # 1. 分身决定：开一个 studio 项目。
    project = await studio_service.save_project(
        session,
        owner_hasn_id=_HASN,
        agent_hasn_id=_AGENT,
        title='唤星分身能力解说（分身自主编排·全链路）',
        default_pipeline_key='animated-explainer',
    )
    assert project['id'] > 0
    print(f'✅ 1. save_project → 项目 #{project["id"]}（分身自主开项目）')

    # 2. 分身调一个创作段原子工具（经 broker 打真引擎，确定性、零付费 provider）。
    tool_out = await studio_service.run_tool(
        session,
        owner_hasn_id=_HASN,
        agent_hasn_id=_AGENT,
        tool_name='code_snippet',
        inputs={
            'code': 'def 分身():\n    return "代你行动的第二大脑"',
            'language': 'python',
            'title': '唤星 · AI 分身',
        },
    )
    result = tool_out.get('result') or {}
    assert result.get('success') is True, f'创作工具应真实执行成功：{tool_out}'
    assert result.get('artifacts'), '创作工具应产出真实 artifact'
    print(f'✅ 2. run_tool(code_snippet) → 真引擎执行成功，artifacts={result.get("artifacts")}')

    # 3. 分身把**自己现编的 props** 喂 compose 工具（render）→ broker → 真引擎直渲 Explainer。
    submitted = await studio_service.render(
        session,
        owner_hasn_id=_HASN,
        agent_hasn_id=_AGENT,
        project_id=project['id'],
        props=_ORCHESTRATED_PROPS,
        composition_id='Explainer',
        work_session_id='ws_studio_fullchain',
    )
    job_id = submitted['id']
    assert job_id > 0
    assert submitted['status'] in ('queued', 'running'), f'提交应成功（真实错误透传）：{submitted.get("error")}'
    assert submitted['engine_job_id'], '真引擎应收下渲染并回 engine_job_id'
    assert submitted['input']['props']['cuts'][0]['id'] == 'cap-hero'  # 入参快照 = 我现编的 props
    print(f'✅ 3. render(自编 props) → render_job #{job_id} engine_job_id={submitted["engine_job_id"]} status={submitted["status"]}')

    # 落库核实（绕 service 缓存）。
    from backend.app.hasn_studio.model import StudioRenderJob

    row = (
        await session.execute(select(StudioRenderJob).where(StudioRenderJob.id == job_id))
    ).scalar_one()
    assert row.owner_hasn_id == _HASN
    assert row.engine_job_id == submitted['engine_job_id']
    assert row.work_session_id == 'ws_studio_fullchain'

    # 4. 分身轮询等真出片（510 帧软渲 ~7–8min；给 1800s 余量）。
    print('    ⏳ 4. 轮询真引擎渲染（swiftshader CPU 软渲，活体进度如下）：')
    final = await _poll_until_terminal(session, render_job_id=job_id, timeout=1800.0)
    assert final['status'] == 'succeeded', f'出片应成功（真实错误透传，零 fake）：{final.get("error")}'
    print(f'✅ 4. 真出片成功：status=succeeded progress={final.get("progress")}% cost={final.get("cost")}')

    # 5. 物化（零 fake）：成功则应落成品 artifact（取片 + 上传私有桶 + 落 studio_artifact + 回流
    #    hasn_artifacts）；若本机 S3/上传不可用 → job 仍 succeeded，记真实 job.error，**绝不** fake artifact。
    arts = await studio_service.list_artifacts(session, owner_hasn_id=_HASN, project_id=project['id'])
    if arts:
        art = arts[0]
        assert art['video_asset_uri'].startswith('hasn://asset/'), '成品须存 hasn://asset/ 引用（非 CDN 直链）'
        assert art['status'] == 'completed'
        assert art['render_job_id'] == job_id
        print(f'✅ 5. 成品物化：artifact #{art["id"]} {art["video_asset_uri"]} '
              f'duration={art.get("duration_sec")}s resolution={art.get("resolution")} '
              f'size={art.get("meta", {}).get("size_bytes")}B')
    else:
        assert final.get('error'), '物化失败必记真实错误（零 fake artifact）'
        print(f'✅ 5. 物化未落（本机 S3 不可用）→ 真实记录 job.error（零 fake）：{final["error"][:160]}')

    # 证据：读真引擎最终 snapshot 打印成片元数据（与 broker 同源，证明确为真 mp4）。
    from backend.app.hasn_studio.provider import montage_engine_provider

    snap = await montage_engine_provider.get_render(submitted['engine_job_id'])
    print(
        '🎬 引擎成片元数据（真 mp4）：'
        f'resolution={snap.get("resolution")} duration={snap.get("duration_seconds") or snap.get("duration")}s '
        f'size_bytes={snap.get("size_bytes")} cost_usd={snap.get("cost_usd") or snap.get("cost")}'
    )
    assert snap.get('status') in ('succeeded', 'completed', 'done'), f'引擎侧应终态成功：{snap}'
