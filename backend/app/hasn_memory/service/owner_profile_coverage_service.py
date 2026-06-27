"""主人画像完整度判定服务（「了解主人」功能 D1：结构化维度表 + LLM 打分）。

读 owner_memory.content 全文 → 统一 LLM 客户端对 5 个画像维度逐项打分 → upsert
owner_profile_coverage → 派生 all_sufficient（驱动首页入口显隐）+ next_dimensions（驱动
"缺什么采访什么"）。

- 维度固定 5 个（interests/work/residence/goals/life_plan），不擅自增减。
- LLM 走统一客户端 backend.common.llm.llm_client（禁止散落 httpx）；未配置/失败时诚实降级
  （保留旧行，不覆盖成空），绝不造假 sufficient。
- evidence_version 防陈旧：判定记录所依据的 owner_memory.version，落后则惰性重判。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §5
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_memory.crud.crud_owner_profile_coverage import owner_profile_coverage_dao
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.common.llm import LLMError, llm_client
from backend.common.log import log
from backend.utils.timezone import timezone

# 5 个画像维度（固定枚举，福仔拍板；顺序即对外展示顺序）。
PROFILE_DIMENSIONS: tuple[str, ...] = ('interests', 'work', 'residence', 'goals', 'life_plan')
DIMENSION_LABELS: dict[str, str] = {
    'interests': '兴趣爱好',
    'work': '工作情况',
    'residence': '居住地址',
    'goals': '近期目标',
    'life_plan': '人生规划',
}
DIMENSION_INTENT: dict[str, str] = {
    'interests': '平时喜欢做什么、关注什么领域、怎么放松',
    'work': '做什么职业/行业、当前在忙什么、职业诉求',
    'residence': '常驻城市/区域（粗粒度即可，不要门牌/精确坐标）',
    'goals': '最近 1–3 个月想达成什么',
    'life_plan': '更长期的人生方向、价值观、想成为什么样的人',
}
_VALID_STATUS = {'missing', 'partial', 'sufficient'}

_COVERAGE_PROMPT = (
    '你是主人画像完整度评估助手。下面给你一段「主人画像」文本（分身长期观察主人后合并出的 USER.md）。'
    '请评估分身对主人以下 5 个维度的了解是否「够用」（够用 = 分身能据此为主人行动/规划/服务）：\n'
    '- interests（兴趣爱好）：平时喜欢做什么、关注什么领域、怎么放松\n'
    '- work（工作情况）：做什么职业/行业、当前在忙什么、职业诉求\n'
    '- residence（居住地址）：常驻城市/区域（粗粒度即可）\n'
    '- goals（近期目标）：最近 1–3 个月想达成什么\n'
    '- life_plan（人生规划）：更长期的人生方向、价值观、想成为什么样的人\n\n'
    '每个维度给出：\n'
    '- status：missing（几乎无信息）/ partial（有一些但不够支撑分身行动）/ sufficient（关键要素齐备、具体、可操作）\n'
    '- confidence：0~1 的小数，你对该判定的把握\n'
    '- summary：用一句话概括该维度已知信息（面向主人；没有信息则空字符串）\n'
    '- missing_hint：该维度还缺什么、下一步该问主人什么（已 sufficient 则空字符串）\n\n'
    '严格只输出一个 JSON 对象，键为 5 个维度英文 key，值为含上述 4 字段的对象。不要任何额外解释或 markdown。\n'
    '示例：{"interests":{"status":"partial","confidence":0.6,"summary":"喜欢摄影和徒步","missing_hint":"关注的具体领域不清楚"},'
    '"work":{"status":"missing","confidence":0.9,"summary":"","missing_hint":"完全不知道职业与行业"}}'
)

_MAX_MEMORY_CHARS = 6000  # 截断送入 LLM，控成本


class OwnerProfileCoverageService:
    """主人画像完整度判定与读取。"""

    @staticmethod
    def _empty_dimension(dimension: str) -> dict[str, Any]:
        return {
            'dimension': dimension,
            'label': DIMENSION_LABELS[dimension],
            'status': 'missing',
            'confidence': 0.0,
            'summary': None,
            'missing_hint': f'还不了解你的{DIMENSION_LABELS[dimension]}（{DIMENSION_INTENT[dimension]}）',
        }

    async def get_coverage(self, db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """读取某主人的完整度聚合结果（缺行补 missing 默认态；不触发判定）。"""
        rows = await owner_profile_coverage_dao.get_by_owner(db, owner_id)
        by_dim = {r.dimension: r for r in rows}
        memory = await owner_memory_service.get_owner_memory(db, owner_id=owner_id)
        memory_version = int(memory.get('version') or 0)

        dimensions: list[dict[str, Any]] = []
        for dim in PROFILE_DIMENSIONS:
            row = by_dim.get(dim)
            if row is None:
                dimensions.append(self._empty_dimension(dim))
                continue
            dimensions.append(
                {
                    'dimension': dim,
                    'label': DIMENSION_LABELS[dim],
                    'status': row.status if row.status in _VALID_STATUS else 'missing',
                    'confidence': float(row.confidence or 0),
                    'summary': row.summary or None,
                    'missing_hint': row.missing_hint or None,
                }
            )

        next_dimensions = [d['dimension'] for d in dimensions if d['status'] != 'sufficient']
        sufficient_count = len(PROFILE_DIMENSIONS) - len(next_dimensions)
        return {
            'dimensions': dimensions,
            'all_sufficient': len(next_dimensions) == 0,
            'sufficient_count': sufficient_count,
            'total': len(PROFILE_DIMENSIONS),
            'next_dimensions': next_dimensions,
            'memory_version': memory_version,
        }

    async def assess(self, db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """对照 owner_memory 全文用 LLM 给 5 维度打分并 upsert；返回最新聚合结果。

        - owner_memory 为空 → 5 维全 missing（evidence_version=当前版本），不调 LLM。
        - LLM 未配置或失败 → 不覆盖已有行（诚实降级），仅返回当前聚合。
        """
        memory = await owner_memory_service.get_owner_memory(db, owner_id=owner_id)
        content = (memory.get('content') or '').strip()
        version = int(memory.get('version') or 0)
        now = timezone.now()

        if not content:
            for dim in PROFILE_DIMENSIONS:
                await owner_profile_coverage_dao.upsert(
                    db,
                    owner_id=owner_id,
                    dimension=dim,
                    status='missing',
                    confidence=Decimal('0'),
                    summary=None,
                    missing_hint=self._empty_dimension(dim)['missing_hint'],
                    evidence_version=version,
                    assessed_time=now,
                )
            await db.commit()
            return await self.get_coverage(db, owner_id=owner_id)

        if not llm_client.is_configured:
            log.warning('owner_profile_coverage: LLM 网关未配置，跳过判定（保留现状，诚实不造假）')
            return await self.get_coverage(db, owner_id=owner_id)

        try:
            scored = await llm_client.complete_json(
                [
                    {'role': 'system', 'content': _COVERAGE_PROMPT},
                    {'role': 'user', 'content': content[:_MAX_MEMORY_CHARS]},
                ],
                max_tokens=800,
                temperature=0,
            )
        except LLMError as exc:
            log.warning('owner_profile_coverage: LLM 判定失败，保留现状 owner=%s err=%r', owner_id, exc)
            return await self.get_coverage(db, owner_id=owner_id)

        for dim in PROFILE_DIMENSIONS:
            item = scored.get(dim) if isinstance(scored, dict) else None
            status, confidence, summary, missing_hint = self._coerce_item(dim, item)
            await owner_profile_coverage_dao.upsert(
                db,
                owner_id=owner_id,
                dimension=dim,
                status=status,
                confidence=confidence,
                summary=summary,
                missing_hint=missing_hint,
                evidence_version=version,
                assessed_time=now,
            )
        await db.commit()
        log.info('owner_profile_coverage: 已为 owner=%s 重判 5 维度（memory v%d）', owner_id, version)
        return await self.get_coverage(db, owner_id=owner_id)

    async def assess_if_stale(self, db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """惰性判定：owner_memory 版本领先已落判定版本（或维度行不全/从未判定）时重判，否则直接读。"""
        memory = await owner_memory_service.get_owner_memory(db, owner_id=owner_id)
        version = int(memory.get('version') or 0)
        rows = await owner_profile_coverage_dao.get_by_owner(db, owner_id)
        min_version = await owner_profile_coverage_dao.min_evidence_version(db, owner_id)
        if min_version is None or len(rows) < len(PROFILE_DIMENSIONS) or int(min_version) < version:
            return await self.assess(db, owner_id=owner_id)
        return await self.get_coverage(db, owner_id=owner_id)

    @staticmethod
    def _coerce_item(dimension: str, item: Any) -> tuple[str, Decimal, str | None, str | None]:
        """把 LLM 单维度输出收敛为合法 (status, confidence, summary, missing_hint)。"""
        if not isinstance(item, dict):
            return 'missing', Decimal('0'), None, None
        status = str(item.get('status') or '').strip().lower()
        if status not in _VALID_STATUS:
            status = 'missing'
        try:
            conf = Decimal(str(item.get('confidence', 0)))
        except (ValueError, ArithmeticError):
            conf = Decimal('0')
        conf = max(Decimal('0'), min(Decimal('1'), conf))
        summary = (str(item.get('summary')).strip() or None) if item.get('summary') else None
        missing_hint = (str(item.get('missing_hint')).strip() or None) if item.get('missing_hint') else None
        return status, conf, summary, missing_hint


owner_profile_coverage_service = OwnerProfileCoverageService()
