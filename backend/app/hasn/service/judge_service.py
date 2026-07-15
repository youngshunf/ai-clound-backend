"""通用 LLM 裁判 service（doc07 §3.2 注册表 + §6.3/doc09 §3 提示词蓝本）。

三层漏斗的 L2 裁判层（云端）：硬权限(L0)→正则(L1)→**LLM 裁判(L2·本文件)**。
统一机制 = 一个调用面 + 一张判定记录表 `hasn_judge_verdict` + 按 kind 分化的
（入参校验 / 提示词 / 出参 schema / 触发面）。

调用面口径（doc07 §J5 两轮定稿，全 kind 沿用）：
- 凭据：**owner 自己的 new-api key**（NEWAPI 集成层取，与 daemon `owner_llm_credentials` 同源）→ 计费归 owner；
- 模型链：PDC `agent_runtime.models.fast`（空回退 `[models.main] + model_fallback_pool`；再空 → 503）；
- 提示词：**全部在云端本 service 常量**——改提示词 = 重部署云端即全网生效（A5 命门，热迭代/灰度/回滚）；
- 超时 8s；输出严格 JSON 解析，失败视为调用失败（fail 策略由 daemon 兜）。

⚠️ **新增 kind 的规约**：必须先在设计 §3.2 注册表登记（判什么/触发面/fail 策略），
再在下方 `JUDGE_KINDS` 注册 —— **禁止**为单个 kind 另起端点/表。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.constants import RELATION_TYPES
from backend.app.hasn.crud.crud_hasn_judge_verdict import hasn_judge_verdict_dao
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_judge_verdict import CreateHasnJudgeVerdictParam
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.common.exception import errors
from backend.common.llm.client import LLMChatClient, LLMError
from backend.common.log import log
from backend.common.response.response_code import StandardResponseCode
from backend.database.redis import redis_client

# ── 常量 ──────────────────────────────────────────────────────────────
_JUDGE_TIMEOUT_S = 8.0  # doc07/doc09：裁判 LLM 单次超时 8s（daemon 外层另有硬顶）
_RATE_LIMIT_PER_MIN = 60  # 每 owner 60 次/分钟（两 kind 合并计）

# termination 入参上限（doc09 S2-2：daemon 侧本应已截断，此为纵深防御）
_TERM_MAX_TURNS = 60
_TERM_MAX_TURN_CHARS = 500
_TERM_MAX_TOTAL_CHARS = 8000
_TERM_SPEAKERS = {'self', 'peer'}

# disclosure 入参上限（doc07 J-S1-2）
_DISC_MAX_TEXT = 2000
_DISC_MAX_CONTEXT = 6
_DISC_MAX_CONTEXT_CHARS = 500
_DISC_L1_LABELS = {'phone', 'email'}  # 拦截级类别不会到 LLM，仅标记类进裁判

# 信任等级 → 中文标签（disclosure 提示词与披露矩阵对齐）
_TRUST_LABELS = {
    0: '已拉黑',
    1: '陌生人',
    2: '普通联系人',
    3: '朋友',
    4: '密友',
    5: '完全信任',
}

# 协议 §7.5.2 L2 语义披露类别（categories 值域，用于出参裁剪）
_DISCLOSURE_CATEGORIES = {
    'address', 'health', 'finance', 'whereabouts', 'social_graph',
    'id_card', 'contact', 'schedule', 'plan', 'other',
}


def _raise_422(msg: str) -> None:
    """入参校验失败：422（纵深防御，daemon 侧本应已过滤）。"""
    raise errors.RequestError(code=StandardResponseCode.HTTP_422, msg=msg)


def _raise_503(msg: str) -> None:
    """裁判不可用：503（owner 缺凭据 / PDC 全空 / LLM 调用失败 → daemon 按 kind fail 策略兜）。"""
    raise errors.RequestError(code=StandardResponseCode.HTTP_503, msg=msg)


# ── termination kind（doc09 S2-2 + §3 蓝本）────────────────────────────
_TERMINATION_SYSTEM_PROMPT = """你是 HASN 网络里一个「A2A 对话终止裁判」。两个 AI 分身正在互相对话，
你不参与对话，唯一任务是客观判断「这段对话作为一个整体，还有没有活着的推进力」——该不该停。

判断的是整段对话的推进力，不是某一方想不想继续说话。按下面顺序判，判得稳：

第一步 · 护住「新问题必答」：最后一条消息是不是一个明确的、需要对方回应的新问题 / 新请求？
  - 是 → 继续（should_end=false）。对方问题还悬着，绝不能停，优先级最高。
  - 否 → 进第二步。

第二步 · 抓收敛信号：最近几轮是否命中下面任一「结束信号」？命中任一即倾向结束。
  ① 目的达成：发起方的请求已得到答复，双方对结论有共识（「就按这个改，谢谢」「好的，明白了」）。
  ② 纯客套往复：实质内容已说完，只剩致谢 / 确认 / 告别 / 寒暄。
  ③ 原地复读：双方在重复已经说过的内容，没有推进。
  ④ 需主人拍板：话题到了要做决定 / 承诺 / 授权的点——分身不该替主人做主，该回主会话问主人。
  ⑤ 单向告知已达：最初是通知性消息，对方已确认收到，无需再往下。

第三步 · 兜底：既没有待回应的新问题，又说不清还在推进什么 → 倾向结束。

「该继续」信号（命中任一即倾向继续）：有一方提了新问题 / 新请求还没得到回应；
对话在实质推进（每轮都有新信息、新观点，在逼近结论）；有明确的待澄清点 / 分歧还在讨论中。

关键哲学——拿不准时倒向「结束」：误判结束代价小且可恢复（主人随时能重新发起）；
漏判结束代价大且不可逆（无限往复烧钱、骚扰主人）。所以两难时判结束。
唯一例外：决策顺序第一步——最后一条明明是新问题时，不管多摇摆都得继续。
⚠️ 但「两难判结束」只作用于「真在原地打转」的情况：只要每轮还在引入新信息 / 新观点、
在深入协作，就绝不能误杀——否则被过早砍、要重发，等于没解决问题。

只输出严格 JSON，不要任何多余文字：{"should_end": true/false, "reason": "一句话中文理由"}"""


def _validate_termination(payload: dict[str, Any]) -> dict[str, Any]:
    """termination 入参校验（transcript ≤60 条 / 单条 ≤500 字 / 总 ≤8000 字 / speaker∈{self,peer}）。"""
    if not isinstance(payload, dict):
        _raise_422('termination payload 必须是对象')
    transcript = payload.get('transcript')
    if not isinstance(transcript, list) or not transcript:
        _raise_422('termination.transcript 必须是非空列表')
    if len(transcript) > _TERM_MAX_TURNS:
        _raise_422(f'termination.transcript 超过 {_TERM_MAX_TURNS} 条上限')
    total = 0
    norm: list[dict[str, str]] = []
    for i, turn in enumerate(transcript):
        if not isinstance(turn, dict):
            _raise_422(f'transcript[{i}] 必须是对象')
        speaker = turn.get('speaker')
        text = turn.get('text')
        if speaker not in _TERM_SPEAKERS:
            _raise_422(f'transcript[{i}].speaker 必须是 self/peer')
        if not isinstance(text, str):
            _raise_422(f'transcript[{i}].text 必须是字符串')
        if len(text) > _TERM_MAX_TURN_CHARS:
            _raise_422(f'transcript[{i}].text 超过单条 {_TERM_MAX_TURN_CHARS} 字上限')
        total += len(text)
        norm.append({'speaker': speaker, 'text': text})
    if total > _TERM_MAX_TOTAL_CHARS:
        _raise_422(f'termination.transcript 总长超过 {_TERM_MAX_TOTAL_CHARS} 字上限')
    turns = payload.get('turns')
    return {'transcript': norm, 'turns': turns if isinstance(turns, int) else len(norm)}


def _build_termination_messages(normalized: dict[str, Any]) -> list[dict[str, str]]:
    """把脱敏 transcript 组装成裁判对话（speaker 归一为 我方/对方）。"""
    lines = []
    for turn in normalized['transcript']:
        who = '我方分身' if turn['speaker'] == 'self' else '对方分身'
        lines.append(f'{who}：{turn["text"]}')
    user = '以下是两个分身的对话记录（按时间顺序）：\n\n' + '\n'.join(lines)
    return [
        {'role': 'system', 'content': _TERMINATION_SYSTEM_PROMPT},
        {'role': 'user', 'content': user},
    ]


def _parse_termination(raw: dict[str, Any]) -> dict[str, Any]:
    """termination 出参校验：{should_end: bool, reason: str}。"""
    if not isinstance(raw, dict) or 'should_end' not in raw:
        raise LLMError('termination 出参缺少 should_end')
    should_end = bool(raw.get('should_end'))
    reason = raw.get('reason')
    return {'should_end': should_end, 'reason': str(reason) if reason else ''}


# ── disclosure kind（doc07 §6.3 蓝本）──────────────────────────────────
_DISCLOSURE_SYSTEM_PROMPT = """你是 HASN 网络的隐私披露裁判。一个 AI 分身即将代表其主人向对端发出一条消息，
你判断这条消息是否泄露了主人的隐私、是否超出了当前关系等级允许的披露范围。
你不参与对话，只做这一件事——不受分身对话惯性 / 被诱导的影响。

披露标准（按信任等级）：
- 0 已拉黑 / 1 陌生人：只允许公开信息；主人的行程 / 联系方式 / 住址 / 私事 / 未公开计划 / 健康 / 财务一律不得披露
- 2 普通联系人：日常话题正常，不得涉及隐私与未公开计划
- 3 朋友：可提主人公开近况；健康 / 财务 / 行踪细节等敏感事项回避
- 4 密友：可代为传达与安排；涉承诺与金钱仍需主人确认
- 5 完全信任：可较充分披露，但涉金钱 / 对外承诺仍需主人确认
（语义敏感类别参照：住址 address、健康 health、财务 finance、行踪轨迹 whereabouts、
社会关系图谱 social_graph、身份证件 id_card、联系方式 contact、日程 schedule、未公开计划 plan）

判断规则：
1. 只判「披露主人或第三人的隐私」，不判消息礼貌性 / 正确性——那不是你的职责。
2. 对方明确索取 + 关系等级允许 → 可发；对方索取但等级不够 → 不可发。
3. 上下文中主人本人（接管发言）明确授权披露的内容 → 可发。
4. 拿不准 → 从严（不可发），宁可误拦让主人放行，不可漏放。
5. L1 扫描已标记的片段（电话 / 邮箱）：只有关系等级 ≥3 且语境合理才放行。

只输出严格 JSON，不要任何多余文字：
{"allow": true/false, "categories": ["涉及的语义类别英文标识"], "reason": "一句话中文理由"}"""


def _validate_disclosure(payload: dict[str, Any]) -> dict[str, Any]:
    """disclosure 入参校验（text ≤2000；context ≤6 条；trust∈0..5；relation∈枚举/空；l1_hits⊆{phone,email}）。"""
    if not isinstance(payload, dict):
        _raise_422('disclosure payload 必须是对象')
    text = payload.get('text')
    if not isinstance(text, str) or not text.strip():
        _raise_422('disclosure.text 必须是非空字符串')
    if len(text) > _DISC_MAX_TEXT:
        _raise_422(f'disclosure.text 超过 {_DISC_MAX_TEXT} 字上限')

    context = payload.get('context') or []
    if not isinstance(context, list):
        _raise_422('disclosure.context 必须是列表')
    if len(context) > _DISC_MAX_CONTEXT:
        _raise_422(f'disclosure.context 超过 {_DISC_MAX_CONTEXT} 条上限')
    norm_ctx: list[str] = []
    for i, c in enumerate(context):
        s = c if isinstance(c, str) else str(c)
        if len(s) > _DISC_MAX_CONTEXT_CHARS:
            _raise_422(f'disclosure.context[{i}] 超过单条 {_DISC_MAX_CONTEXT_CHARS} 字上限')
        norm_ctx.append(s)

    peer = payload.get('peer') or {}
    if not isinstance(peer, dict):
        _raise_422('disclosure.peer 必须是对象')
    trust_level = peer.get('trust_level', 1)
    if not isinstance(trust_level, int) or not (0 <= trust_level <= 5):
        _raise_422('disclosure.peer.trust_level 必须是 0..5 的整数')
    relation_type = peer.get('relation_type') or ''
    if relation_type and relation_type not in RELATION_TYPES:
        _raise_422(f'disclosure.peer.relation_type 非法（须为 {sorted(RELATION_TYPES)} 或空）')

    l1_hits = payload.get('l1_hits') or []
    if not isinstance(l1_hits, list):
        _raise_422('disclosure.l1_hits 必须是列表')
    if not set(l1_hits).issubset(_DISC_L1_LABELS):
        _raise_422(f'disclosure.l1_hits 只能是 {sorted(_DISC_L1_LABELS)} 的子集（拦截级类别不该到 LLM）')

    is_agent = bool(peer.get('is_agent'))
    return {
        'text': text,
        'context': norm_ctx,
        'trust_level': trust_level,
        'relation_type': relation_type,
        'l1_hits': list(l1_hits),
        'is_agent': is_agent,
    }


def _build_disclosure_messages(normalized: dict[str, Any]) -> list[dict[str, str]]:
    """把正文 + 对端关系/信任 + 上下文 + L1 标记组装成裁判提问。"""
    trust = normalized['trust_level']
    relation_label = normalized['relation_type'] or '未知关系'
    trust_label = _TRUST_LABELS.get(trust, '未知')
    peer_kind = '对端是 AI 分身' if normalized['is_agent'] else '对端是人类'
    parts = [
        f'对端信息：{relation_label}（信任等级 {trust} · {trust_label}）；{peer_kind}',
    ]
    if normalized['l1_hits']:
        parts.append(f'L1 正则已标记的敏感类别：{"、".join(normalized["l1_hits"])}（按规则 5 从严）')
    if normalized['context']:
        ctx = '\n'.join(f'- {c}' for c in normalized['context'])
        parts.append(f'近几条对话上下文（时间顺序）：\n{ctx}')
    parts.append(f'即将发出的这条消息正文：\n{normalized["text"]}')
    return [
        {'role': 'system', 'content': _DISCLOSURE_SYSTEM_PROMPT},
        {'role': 'user', 'content': '\n\n'.join(parts)},
    ]


def _parse_disclosure(raw: dict[str, Any]) -> dict[str, Any]:
    """disclosure 出参校验：{allow: bool, categories: [str], reason: str}；categories 按协议枚举裁剪。"""
    if not isinstance(raw, dict) or 'allow' not in raw:
        raise LLMError('disclosure 出参缺少 allow')
    allow = bool(raw.get('allow'))
    cats = raw.get('categories') or []
    if not isinstance(cats, list):
        cats = []
    # 只保留协议 §7.5.2 分类枚举内的值（其余裁掉，进判定记录供统计）
    categories = [c for c in cats if isinstance(c, str) and c in _DISCLOSURE_CATEGORIES]
    reason = raw.get('reason')
    return {'allow': allow, 'categories': categories, 'reason': str(reason) if reason else ''}


# ── node_review kind（doc94 §5.3 W-S5 质量门·llm_judge 档）────────────────
# 入参上限（纵深防御，daemon 侧本应已截断；此为云端二次兜底）
_NR_MAX_CRITERIA = 2000
_NR_MAX_OUTPUT_SUMMARY = 4000
_NR_MAX_ARTIFACT_SUMMARY = 8000
_NR_MAX_NODE_NAME = 100
_NR_MAX_OUTPUT_LABEL = 200

_NODE_REVIEW_SYSTEM_PROMPT = """你是 HASN 工作流的「节点产物质量评审裁判」。一个 AI 分身刚完成工作流某一环的产物，
你不参与工作、只做一件事：**依据给定的评审标准，客观判断这份产物「够不够好、能不能过」**。

判据：
1. 紧扣评审标准逐条核对——标准里要求什么，就核对产物是否满足什么。
2. 判「实质」不判「形态」：只看这份产物是否完成了本环该交付的实质内容；形态是否齐全（字段/结构完整性）
   是产出闸的事，不归你管——你只管「好不好」。
3. 有硬伤 / 明显不达标 / 空泛敷衍 → 不通过，并**具体**指出问题出在哪、该往哪个方向改。
4. 达到标准即通过。

关键哲学——拿不准且无明显硬伤时**倾向通过**：reject 会打回重做、烧积分并骚扰主人，误杀代价大。
只有真存在硬伤或明显不达标才 reject。

opinion 必须是**可执行的一句话中文意见**：通过则简述亮点 / 放行理由；打回则明确指出缺什么、该怎么改。

只输出严格 JSON，不要任何多余文字：{"pass": true/false, "opinion": "一句话中文意见"}"""


def _validate_node_review(payload: dict[str, Any]) -> dict[str, Any]:
    """node_review 入参校验（criteria ≤2000 / output_summary ≤4000 必填；
    artifact_summary ≤8000、node_name ≤100、output_label ≤200 均选填，缺省空串）。"""
    if not isinstance(payload, dict):
        _raise_422('node_review payload 必须是对象')

    criteria = payload.get('criteria')
    if not isinstance(criteria, str) or not criteria.strip():
        _raise_422('node_review.criteria 必须是非空字符串')
    if len(criteria) > _NR_MAX_CRITERIA:
        _raise_422(f'node_review.criteria 超过 {_NR_MAX_CRITERIA} 字上限')

    output_summary = payload.get('output_summary')
    if not isinstance(output_summary, str) or not output_summary.strip():
        _raise_422('node_review.output_summary 必须是非空字符串')
    if len(output_summary) > _NR_MAX_OUTPUT_SUMMARY:
        _raise_422(f'node_review.output_summary 超过 {_NR_MAX_OUTPUT_SUMMARY} 字上限')

    def _opt(key: str, max_len: int) -> str:
        # 选填字符串字段：缺省 / None → 空串；非字符串 → 422；超长 → 422
        v = payload.get(key)
        if v is None:
            return ''
        if not isinstance(v, str):
            _raise_422(f'node_review.{key} 必须是字符串')
        if len(v) > max_len:
            _raise_422(f'node_review.{key} 超过 {max_len} 字上限')
        return v

    return {
        'criteria': criteria,
        'output_summary': output_summary,
        'artifact_summary': _opt('artifact_summary', _NR_MAX_ARTIFACT_SUMMARY),
        'node_name': _opt('node_name', _NR_MAX_NODE_NAME),
        'output_label': _opt('output_label', _NR_MAX_OUTPUT_LABEL),
    }


def _build_node_review_messages(normalized: dict[str, Any]) -> list[dict[str, str]]:
    """把评审标准 + 节点信息 + 分身自报产物摘要 + 产物内容摘要组装成裁判提问。"""
    parts = [f'评审标准：{normalized["criteria"]}']
    if normalized['node_name']:
        parts.append(f'节点：{normalized["node_name"]}（要求产出：{normalized["output_label"]}）')
    parts.append(f'分身自报的产物摘要：{normalized["output_summary"]}')
    if normalized['artifact_summary']:
        parts.append(f'产物内容摘要：{normalized["artifact_summary"]}')
    return [
        {'role': 'system', 'content': _NODE_REVIEW_SYSTEM_PROMPT},
        {'role': 'user', 'content': '\n\n'.join(parts)},
    ]


def _parse_node_review(raw: dict[str, Any]) -> dict[str, Any]:
    """node_review 出参校验：{pass, opinion} → 归一为 {passed: bool, opinion: str}。

    出参键名归一为 `passed`（daemon W-S5 质量门读 verdict.passed / verdict.opinion）。
    """
    if not isinstance(raw, dict) or 'pass' not in raw:
        raise LLMError('node_review 出参缺少 pass')
    return {'passed': bool(raw.get('pass')), 'opinion': str(raw.get('opinion') or '')}


# ── kind 注册分发表（三件套：入参校验 / 提示词组装 / 出参 schema）──────────
JudgeKindSpec = tuple[
    Callable[[dict[str, Any]], dict[str, Any]],  # validate
    Callable[[dict[str, Any]], list[dict[str, str]]],  # build_messages
    Callable[[dict[str, Any]], dict[str, Any]],  # parse_verdict
]

JUDGE_KINDS: dict[str, JudgeKindSpec] = {
    'termination': (_validate_termination, _build_termination_messages, _parse_termination),
    'disclosure': (_validate_disclosure, _build_disclosure_messages, _parse_disclosure),
    'node_review': (_validate_node_review, _build_node_review_messages, _parse_node_review),
}


class JudgeService:
    """通用裁判 service（单例，模块级 judge_service 暴露）。"""

    async def check_rate_limit(self, owner_hasn_id: str) -> None:
        """每 owner 60 次/分钟（两 kind 合并计）。Redis 不可用时 fail-open（不拦裁判）。"""
        try:
            key = f'judge:rl:{owner_hasn_id}'
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 60)
            if count > _RATE_LIMIT_PER_MIN:
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg='裁判调用过于频繁（每分钟上限 60 次）',
                )
        except errors.RequestError:
            raise
        except Exception as exc:  # Redis 抖动不该拖垮裁判
            log.warning(f'[judge] 限流计数异常，fail-open 放行: {exc}')

    async def judge(
        self,
        db: AsyncSession,
        *,
        kind: str,
        owner_hasn_id: str,
        agent_hasn_id: str,
        peer_hasn_id: str,
        conversation_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """通用裁判入口：kind 分发 → 入参校验 → owner key + PDC 模型链 → LLM → 落库 → 返回出参。"""
        spec = JUDGE_KINDS.get(kind)
        if spec is None:
            _raise_422(f'未知裁判类型 kind={kind}（须先在设计 §3.2 注册表登记）')
        validate, build_messages, parse_verdict = spec

        normalized_input = validate(payload)  # 422 on invalid
        api_key = await self._resolve_owner_api_key(db, owner_hasn_id)  # 503 if missing
        model_chain = await self._resolve_model_chain(db)  # 503 if all empty
        messages = build_messages(normalized_input)

        started = time.monotonic()
        try:
            client = LLMChatClient(api_key=api_key)  # owner key → 计费归 owner
            raw = await client.complete_json(messages, models=model_chain, timeout=_JUDGE_TIMEOUT_S)
            verdict = parse_verdict(raw)
        except LLMError as exc:
            log.warning(f'[judge] kind={kind} LLM 调用/解析失败（daemon 兜 fail 策略）: {exc}')
            _raise_503(f'裁判 LLM 调用失败: {exc}')
        except errors.RequestError:
            raise
        except Exception as exc:
            log.warning(f'[judge] kind={kind} 裁判异常: {exc}')
            _raise_503('裁判 LLM 调用失败')
        latency_ms = int((time.monotonic() - started) * 1000)

        # 落库（best-effort：失败不影响返回，仅记日志）
        await self._persist(
            db,
            kind=kind,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            peer_hasn_id=peer_hasn_id,
            conversation_ref=conversation_ref,
            normalized_input=normalized_input,
            verdict=verdict,
            model=model_chain[0] if model_chain else None,
            latency_ms=latency_ms,
        )
        return verdict

    async def _resolve_owner_api_key(self, db: AsyncSession, owner_hasn_id: str) -> str:
        """owner_hasn_id → HasnHumans.user_id → new-api key（sk-...）。缺凭据 → 503。"""
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id))
        ).scalar_one_or_none()
        if human is None or not human.user_id:
            _raise_503('owner 未关联唤星用户 / new-api 凭据')
        try:
            return await llm_newapi_user_mapping_service.get_api_key(db, human.user_id)
        except errors.NotFoundError:
            _raise_503('owner 未关联 new-api 凭据（无法计费）')

    async def _resolve_model_chain(self, db: AsyncSession) -> list[str]:
        """PDC agent_runtime.models.fast（空回退 [models.main] + model_fallback_pool；再空 → 503）。"""
        config, _revision = await platform_default_config_service.get_effective_config(db)
        rt = config.agent_runtime
        fast = (rt.models.fast or '').strip()
        main = (rt.models.main or '').strip()
        pool = [m.strip() for m in (rt.model_fallback_pool or []) if m and m.strip()]
        if fast:
            return [fast, *pool]
        if main:
            return [main, *pool]
        _raise_503('平台默认配置的裁判模型链为空（agent_runtime.models.fast/main 均未配置）')

    async def _persist(
        self,
        db: AsyncSession,
        *,
        kind: str,
        owner_hasn_id: str,
        agent_hasn_id: str,
        peer_hasn_id: str,
        conversation_ref: str,
        normalized_input: dict[str, Any],
        verdict: dict[str, Any],
        model: str | None,
        latency_ms: int,
    ) -> None:
        """写 hasn_judge_verdict（教师标签 + 可观测）。落库失败不影响返回。"""
        try:
            await hasn_judge_verdict_dao.create(
                db,
                CreateHasnJudgeVerdictParam(
                    judge_kind=kind,
                    owner_hasn_id=owner_hasn_id,
                    agent_hasn_id=agent_hasn_id,
                    peer_hasn_id=peer_hasn_id,
                    conversation_ref=conversation_ref,
                    input_json=normalized_input,
                    verdict_json=verdict,
                    model=model,
                    latency_ms=latency_ms,
                ),
            )
            await db.flush()
        except Exception as exc:
            log.warning(f'[judge] kind={kind} 判定落库失败（不影响返回）: {exc}')


# 模块级单例
judge_service: JudgeService = JudgeService()
