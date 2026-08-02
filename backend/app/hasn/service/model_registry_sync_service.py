"""模型注册表同步服务（new-api 供事实、云端补语义）。

## 为什么数据源是 `/api/pricing` 而不是模型注册表 `/api/models/`

2026-08-02 实测生产网关 `llm.dcfuture.cn` 的模型注册表是**空的**（`total=0`）——运营只配渠道、
从不在注册表里登记模型，于是既有的「自动获取模型」端点一直返回 0 个模型，一切退回手输，
而手输就会写错名字（线上视频全线 503 的根因之一）。定价表由渠道聚合而来，实测 64 条，
是当前唯一真实反映「网关上有哪些模型」的来源。

## 同步语义：只覆盖 new-api 那几列，绝不删行

- **新模型**：插入，`capability='unclassified'` + `agent_visible=false`。未标注就不下发——
  能力类别猜不出来，猜错会让分身把文生图请求发给 TTS 模型（零 fake：不确定就不下发）。
- **既有模型**：只覆盖成本/分组/供应商/`last_synced_time`，**人工标注列一律不动**。
- **本轮没出现的**：标 `upstream_status='missing'` 但**保留行**——渠道临时下线是常态，
  删了人工标注就得重标。`missing` 的不参与下发，并在 Admin 高亮。
- **拉不到就整轮放弃**：绝不把现有行统统标成 `missing`，那会让下发凭空清空。

设计事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §4
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.newapi.client import newapi_admin_client
from backend.common.log import log
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 合法能力类别（与建表 SQL 的字典注释一致）。`unclassified` 是未标注态，不下发。
CAPABILITIES: tuple[str, ...] = (
    'chat',
    'vision',
    'image_generate',
    'image_edit',
    'tts',
    'stt',
    'video',
    'embedding',
    'rerank',
    'unclassified',
)

# 输入要求表的三态取值（`inputs` 里每个键只允许这三个）。
INPUT_REQUIREMENTS: tuple[str, ...] = ('required', 'optional', 'unsupported')

# 价格档位取值（人工覆盖列用；自动算出来的档位不入库）。
COST_TIERS: tuple[str, ...] = ('economy', 'standard', 'premium')

# 入参方言（见 doc19 渠道方言矩阵）。
DIALECTS: tuple[str, ...] = ('openai', 'ali')

# 质量档。
QUALITIES: tuple[str, ...] = ('draft', 'standard', 'high')

# `relative_cost` 的精度：与建表 numeric(12,4) 对齐。**必须先量化再比较**，否则
# Decimal('0.8572') != 0.8572（float 不精确），每轮同步都会误判成「变了」。
_COST_QUANTUM = Decimal('0.0001')

# new-api 行里已被单独建列的键，其余原样进 `cost_extra` 留档。
_PROMOTED_PRICING_KEYS = frozenset({'model_name', 'model_ratio', 'enable_groups', 'vendor_id'})


@dataclass(slots=True)
class ModelRegistrySyncReport:
    """一轮同步的真实结果（Admin 与定时任务都据此汇报）。"""

    created: int
    """本轮新出现、已插入的模型数（capability 待标注）。"""
    updated: int
    """既有模型里 new-api 那几列**确有变化**的数量（只刷新同步时间不计入）。"""
    missing: int
    """本轮网关上没出现、已标 `missing` 的模型数（行仍保留）。"""
    upstream_total: int
    """本轮网关返回的模型总数。"""
    unclassified: int
    """当前仍待标注 capability 的模型数（运营待办量）。"""

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _decimal_or_none(value: Any) -> Decimal | None:
    """把 new-api 的倍率转成与列精度一致的 Decimal；不是数字则 `None`（不编默认值）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        return Decimal(str(value)).quantize(_COST_QUANTUM)
    except (InvalidOperation, ValueError):
        return None


def _enable_groups_of(row: dict[str, Any]) -> list[str]:
    """取可用分组列表（非列表或含非字符串项时如实降级为空，不猜）。"""
    groups = row.get('enable_groups')
    if not isinstance(groups, list):
        return []
    return [str(g) for g in groups if isinstance(g, str) and g.strip()]


def _cost_extra_of(row: dict[str, Any]) -> dict[str, Any]:
    """把 new-api 行里没被提升成列的计费字段原样留档（不下发，仅运维核对）。"""
    return {key: value for key, value in row.items() if key not in _PROMOTED_PRICING_KEYS}


@dataclass(slots=True)
class _UpstreamColumns:
    """一行 new-api 事实映射出的**同步器权威列**（人工标注列不在其中）。"""

    vendor_name: str | None
    relative_cost: Decimal | None
    cost_extra: dict[str, Any]
    enable_groups: list[str]


def _upstream_columns_of(row: dict[str, Any], vendors: dict[int, str]) -> _UpstreamColumns:
    """把一行定价数据折成同步器要覆盖的那几列。"""
    vendor_id = row.get('vendor_id')
    return _UpstreamColumns(
        vendor_name=vendors.get(vendor_id) if isinstance(vendor_id, int) else None,
        relative_cost=_decimal_or_none(row.get('model_ratio')),
        cost_extra=_cost_extra_of(row),
        enable_groups=_enable_groups_of(row),
    )


# 能力建议的模型名关键词（**只作建议，绝不自动生效**）。顺序即优先级：
# `tongyi-embedding-vision-*` 同时含 embedding 与 vision，必须先判 embedding。
_CAPABILITY_NAME_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('rerank', ('rerank',)),
    ('embedding', ('embedding', 'embed-')),
    ('video', ('-i2v', '-t2v', 'i2v-', 't2v-', 'video')),
    ('stt', ('asr', 'whisper', 'speech-to-text')),
    ('tts', ('tts', 'cosyvoice', 'cosyoice', 'text-to-speech')),
    ('image_generate', ('image', 'dall-e', 'flux', 'midjourney')),
    ('vision', ('-vl-', '-vl', 'vision')),
)

# new-api `supported_endpoint_types` 到能力的确定性映射（结构化信号优先于名字猜）。
# 2026-08-02 生产实测 64 行全是 `['openai']`，这条路当前拿不到信息——留着是因为
# 一旦运营把端点类型配细，它就是比名字更硬的依据。
_CAPABILITY_ENDPOINT_HINTS: dict[str, str] = {
    'image_generation': 'image_generate',
    'image_edit': 'image_edit',
    'audio_speech': 'tts',
    'audio_transcription': 'stt',
    'video_generation': 'video',
    'embeddings': 'embedding',
    'rerank': 'rerank',
}


def suggest_capability(model_name: str, endpoint_types: Any = None) -> str:
    """给运营一个 capability **建议值**（只读，Admin 一键采纳，绝不自动写入）。

    为什么不自动生效：靠模型名猜在我们自己的网关上已被证伪——`happyhorse-1.1-i2v` 看不出是
    阿里通义万相，`agnes-video-v2.0` 与 `agnes-image-2.1-flash` 只差一个词。猜错会让分身把
    文生图请求发给 TTS 模型，所以不确定就是 `unclassified`，宁可不下发（零 fake）。

    判定顺序：**结构化信号（`supported_endpoint_types`）优先**，名字关键词兜底，
    都判不出来时按 `chat`——网关上绝大多数是对话模型，且 chat 是最不会造成静默错发的一档。
    """
    if isinstance(endpoint_types, list):
        for endpoint in endpoint_types:
            hit = _CAPABILITY_ENDPOINT_HINTS.get(str(endpoint).strip().lower())
            if hit:
                return hit
    lowered = model_name.strip().lower()
    for capability, needles in _CAPABILITY_NAME_HINTS:
        if any(needle in lowered for needle in needles):
            return capability
    return 'chat'


class ModelRegistrySyncService:
    """把 new-api 定价表同步进模型注册表（upsert，绝不删行）。"""

    async def sync(self, db: AsyncSession) -> ModelRegistrySyncReport:
        """跑一轮同步并返回真实报告。

        new-api 不可达时**直接抛** `NewApiError` 由调用方如实报错——绝不吞掉异常再把现有行
        统统标成 `missing`（那等于一次网络抖动就把全平台模型下发清空）。

        不在此 commit：Admin 走 `CurrentSessionTransaction` 自动提交，定时任务自己管事务。
        """
        rows, vendors = await newapi_admin_client.get_pricing_catalog()
        now = timezone.now()

        existing: dict[str, HasnModelRegistry] = {
            row.model_name: row for row in (await db.execute(sa.select(HasnModelRegistry))).scalars()
        }
        seen: set[str] = set()
        created = 0
        updated = 0

        for raw in rows:
            name = str(raw.get('model_name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            upstream = _upstream_columns_of(raw, vendors)
            current = existing.get(name)
            if current is None:
                # 新模型：只填 new-api 那几列，语义列留待人工标注（未标注不下发）。
                db.add(
                    HasnModelRegistry(
                        model_name=name,
                        capability='unclassified',
                        inputs={},
                        dialect=None,
                        quality=None,
                        scenario=None,
                        agent_visible=False,
                        sort_order=0,
                        vendor_name=upstream.vendor_name,
                        relative_cost=upstream.relative_cost,
                        cost_extra=upstream.cost_extra,
                        cost_tier_override=None,
                        enable_groups=upstream.enable_groups,
                        upstream_status='active',
                        last_synced_time=now,
                    )
                )
                created += 1
                continue
            # 既有模型：只覆盖 new-api 那几列，人工标注列一律不动。
            changed = (
                current.vendor_name != upstream.vendor_name
                or current.relative_cost != upstream.relative_cost
                or current.cost_extra != upstream.cost_extra
                or current.enable_groups != upstream.enable_groups
            )
            current.vendor_name = upstream.vendor_name
            current.relative_cost = upstream.relative_cost
            current.cost_extra = upstream.cost_extra
            current.enable_groups = upstream.enable_groups
            if current.upstream_status != 'active':
                current.upstream_status = 'active'  # 渠道回来了
                changed = True
            current.last_synced_time = now  # 只刷新同步时间不算「变了」（否则每轮都报 updated）
            if changed:
                updated += 1

        missing = 0
        for name, current in existing.items():
            if name in seen:
                continue
            missing += 1
            if current.upstream_status != 'missing':
                current.upstream_status = 'missing'

        await db.flush()
        unclassified = int(
            (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(HasnModelRegistry)
                    .where(HasnModelRegistry.capability == 'unclassified')
                )
            ).scalar_one()
        )
        report = ModelRegistrySyncReport(
            created=created,
            updated=updated,
            missing=missing,
            upstream_total=len(seen),
            unclassified=unclassified,
        )
        log.info(
            f'[model-registry] 同步完成：网关 {report.upstream_total} 个模型，'
            f'新增 {report.created}、更新 {report.updated}、消失 {report.missing}、'
            f'待标注 {report.unclassified}'
        )
        return report


model_registry_sync_service = ModelRegistrySyncService()
