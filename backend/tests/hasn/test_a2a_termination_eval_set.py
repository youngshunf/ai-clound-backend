"""S5-2：A2A 自然对话终止裁判离线评测集（doc09 §6 S5-2）。

两层（与 disclosure 评测集 `test_disclosure_eval_set.py` 同构）：
- **结构 always-on**（无 LLM，随每次提交跑）：评测集规模 / 该停·不该停正反例覆盖 / 判定信号谱系覆盖 /
  每条输入都能过 `_validate_termination` 入参校验（守住契约不漂移，纵深 422 不误伤评测样本）。
- **活体打分**（infra-gated）：仅当 `A2A_TERMINATION_EVAL_LIVE=1` + `JUDGE_LIVE_OWNER`（dev 库里有
  new-api 凭据的 owner）+ 网关可达时，逐条打真实云端 termination 裁判端点（owner key + PDC fast），算两类错误率：
    · 漏判空转 false-continue（expect_end=True 却判 should_end=False）——**代价大且不可逆（无限往复烧钱/骚扰主人），阈值更严**；
    · 误判早停 false-stop（expect_end=False 却判 should_end=True）——主人随时能重发救回，阈值宽松。
  与云端 `_TERMINATION_SYSTEM_PROMPT` 同一蓝本，随提示词改动回归。冷启动小规模，上线后用
  `hasn_judge_verdict` 判定表持续扩充真实语料。

数据集：`data/a2a_termination_eval_set.jsonl`（人工标注，每行一条，脱敏化拟真 A2A 语料）。
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib

import pytest

from backend.app.hasn.service.judge_service import _validate_termination

_DATA = pathlib.Path(__file__).parent / 'data' / 'a2a_termination_eval_set.jsonl'
# 该停信号谱系（expect_end=True）：目的达成/纯客套/原地复读/需主人拍板/单向告知已达/兜底无推进。
_END_SIGNALS = {'purpose_met', 'pleasantry', 'repeat', 'needs_owner', 'notify_acked', 'fallback'}
# 不该停信号谱系（expect_end=False）：新问题必答/实质推进/待澄清分歧/新请求未答。
_CONTINUE_SIGNALS = {'new_question', 'substantive', 'pending_disagreement', 'new_request'}


def _load_cases() -> list[dict]:
    """读 JSONL 评测集（`//` 注释行与空行跳过）。"""
    cases: list[dict] = []
    for raw in _DATA.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        cases.append(json.loads(line))
    return cases


CASES = _load_cases()


def _payload(case: dict) -> dict:
    """评测样本 → termination 裁判入参（转录 + 轮数）。"""
    return {'transcript': case['transcript'], 'turns': case.get('turns', len(case['transcript']))}


# ── 结构 always-on（无 LLM）────────────────────────────────────
def test_eval_set_size_and_signals() -> None:
    """规模下限 + 该停/不该停正反例覆盖 + 判定信号谱系覆盖（结束+继续两侧都要有样本）。"""
    assert len(CASES) >= 20, '冷启动评测集下限 20 条'
    labels = {c['expect_end'] for c in CASES}
    assert labels == {True, False}, '需该停(true)/不该停(false)正反例都有'
    signals = {c['signal'] for c in CASES}
    # 该停侧至少覆盖 4 类结束信号，不该停侧至少覆盖 3 类继续信号（谱系够宽才守得住提示词各分支）。
    end_hit = signals & _END_SIGNALS
    cont_hit = signals & _CONTINUE_SIGNALS
    assert len(end_hit) >= 4, f'结束信号谱系覆盖不足，仅 {sorted(end_hit)}'
    assert len(cont_hit) >= 3, f'继续信号谱系覆盖不足，仅 {sorted(cont_hit)}'
    assert signals <= (_END_SIGNALS | _CONTINUE_SIGNALS), f'出现未登记信号: {sorted(signals - (_END_SIGNALS | _CONTINUE_SIGNALS))}'


def test_eval_set_ids_unique() -> None:
    ids = [c['id'] for c in CASES]
    assert len(ids) == len(set(ids)), '评测用例 id 需唯一'


def test_eval_set_signal_matches_label() -> None:
    """信号与标签必须自洽：结束信号 ⇒ expect_end=True；继续信号 ⇒ expect_end=False（防标注自相矛盾）。"""
    for c in CASES:
        sig = c['signal']
        if sig in _END_SIGNALS:
            assert c['expect_end'] is True, f"{c['id']} 结束信号 {sig} 却标 expect_end=False"
        elif sig in _CONTINUE_SIGNALS:
            assert c['expect_end'] is False, f"{c['id']} 继续信号 {sig} 却标 expect_end=True"


def test_eval_set_payloads_pass_termination_validation() -> None:
    """每条评测输入都能过 termination 入参校验（守契约；纵深 422 不误伤评测样本）。"""
    for c in CASES:
        norm = _validate_termination(_payload(c))  # 非法即 raise
        assert isinstance(c['expect_end'], bool), f"{c['id']} expect_end 必须是 bool"
        assert norm['transcript'], f"{c['id']} 转录不能为空"
        # 说话人只允许 self/peer（_validate_termination 已校验，这里再断言最后一条存在便于活体定位）。
        assert norm['transcript'][-1]['speaker'] in ('self', 'peer')


# ── 活体打分（infra-gated）──────────────────────────────────────
@pytest.mark.asyncio
async def test_a2a_termination_live_scoring() -> None:
    """真实云端 termination 裁判打分：算漏判空转 / 误判早停两类错误率（漏判空转阈值更严）。"""
    live_owner = os.getenv('JUDGE_LIVE_OWNER', '').strip()
    if os.getenv('A2A_TERMINATION_EVAL_LIVE') != '1' or not live_owner:
        pytest.skip(
            '离线评测集：设 A2A_TERMINATION_EVAL_LIVE=1 + JUDGE_LIVE_OWNER=<dev 库有 new-api 凭据的 owner> '
            '+ 网关可达后跑活体打分（两类错误率）',
        )

    import httpx
    from fastapi import FastAPI
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from starlette_context.middleware import ContextMiddleware
    from starlette_context.plugins import RequestIdPlugin

    from backend.app.hasn.api.v1.app.judge import router as judge_router
    from backend.app.hasn.service.hasn_auth import hasn_auth
    from backend.common.exception.exception_handler import register_exception
    from backend.common.security.jwt import DependsJwtAuth
    from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001 —— 连不上库就跳过，不算失败
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()

    # 最小 app 挂真实 judge 路由；owner 身份注入为 JUDGE_LIVE_OWNER（其 new-api key 归属计费）。
    app = FastAPI()
    app.include_router(judge_router, prefix='/api/v1/hasn/app')
    register_exception(app)
    app.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])

    async def _yield_session():
        yield session

    async def _auth_inject():
        return {'hasn_id': live_owner, 'star_id': 's_eval', 'user_id': 0, 'auth_type': 'jwt'}

    app.dependency_overrides[get_db] = _yield_session
    app.dependency_overrides[get_db_transaction] = _yield_session
    app.dependency_overrides[DependsJwtAuth.dependency] = lambda: 'eval-token'
    app.dependency_overrides[hasn_auth] = _auth_inject

    false_continue: list[str] = []  # 漏判空转（严重）：该停却判继续
    false_stop: list[str] = []  # 误判早停（次要）：该继续却判停
    failures: list[tuple[str, int, str]] = []
    pending_cases = CASES
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://eval'
    ) as client:
        for attempt in range(2):
            failures = []
            retry_cases: list[dict] = []
            for c in pending_cases:
                r = await client.post(
                    '/api/v1/hasn/app/judge/termination',
                    json={
                        'agent_hasn_id': f"a_eval_{c['id']}",
                        'peer_hasn_id': f"a_peer_{c['id']}",
                        'conversation_ref': f"eval_{c['id']}",
                        'payload': _payload(c),
                    },
                )
                if r.status_code != 200:
                    failures.append((c['id'], r.status_code, r.text[:120]))
                    retry_cases.append(c)
                    continue
                should_end = bool((r.json().get('data') or {}).get('should_end'))
                if c['expect_end'] and not should_end:
                    false_continue.append(c['id'])
                elif not c['expect_end'] and should_end:
                    false_stop.append(c['id'])
            if not retry_cases or attempt == 1:
                break
            # 免费活体通道可能短时 429；仅重试未评分样本，第二次仍失败就硬失败。
            await asyncio.sleep(10)
            pending_cases = retry_cases

    await session.rollback()
    await session.close()
    await engine.dispose()

    scored = len(CASES) - len(failures)
    assert scored >= 1, f'评测未打到分（全部报错）：{failures[:3]}'
    assert not failures, f'真实裁判评测存在未评分样本，不得以部分结果软通过：{failures}'
    n_end = sum(1 for c in CASES if c['expect_end'])
    n_cont = sum(1 for c in CASES if not c['expect_end'])
    miss_rate = len(false_continue) / n_end if n_end else 0.0
    overcut_rate = len(false_stop) / n_cont if n_cont else 0.0

    # 打分报告（-s 可见）——漏判/误判逐条列 id 便于回看提示词。
    print(
        f'\n[a2a-termination-eval] 打分 {scored}/{len(CASES)}（{len(failures)} 报错）\n'
        f'  漏判空转 false-continue(权重高) {len(false_continue)}/{n_end} = {miss_rate:.1%} → {false_continue}\n'
        f'  误判早停 false-stop           {len(false_stop)}/{n_cont} = {overcut_rate:.1%} → {false_stop}\n'
        f'  报错: {failures}'
    )

    # 漏判空转代价大且不可逆（无限往复烧钱、骚扰主人）→ 阈值更严；误判早停可主人重发救回 → 阈值宽松。
    assert miss_rate <= 0.15, f'漏判空转率过高 {miss_rate:.1%}（{false_continue}）——提示词需从严收敛'
    assert overcut_rate <= 0.30, f'误判早停率过高 {overcut_rate:.1%}（{false_stop}）——提示词过激砍杀真推进'
