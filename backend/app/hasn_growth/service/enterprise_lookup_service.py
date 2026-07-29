"""企业数据读穿式中台（doc09 §3/§6.1 · GROWTH-QCC-4）。

`hasn.growth.lookup_company / search_companies / enrich_company` 三高层工具的业务实现：
**查公共池命中即返回（省 qcc 调用费 + 秒回）→ 未命中调通用网关 `call_system_tool(hasn.ext.qcc_*.*)`
→ 结构化拆分公共事实与 Owner 私有 PII → 返回带 `lead_contact_id`**。

职责边界（doc10 §10 铁律）：qcc 的**连接 / 凭据 / 代理 / 配额全归通用 MCP 网关**
（`external_mcp_gateway.call_system_tool`，架构A：system-origin 平台 key，绕 per-agent binding，
配额按 caller owner 归因）；本模块只做**业务层二次加工**——查池 / 结构化映射 / 入池 / 带 lead_id 返回，
**绝不**自拼 qcc HTTP、绝不碰 qcc token（doc09 §3 v2.1）。

入池复用统一隐私写入引擎（**单一实现，避免去重/脱敏漂移**）：
- 池查询 → `lead_pool_query_service.query_pool`（公共池 pool_visibility=public 维度检索）；
- 核心企业画像入池 → `lead_ingestion_privacy_service.upsert`（公共事实去重 + Owner PII 私有化）；
- 用户引用 → `_grant_refs`（让发起 owner「拥有」入池线索，统一池众包语义）；
- 字段归一 → `enterprise_record_to_structured`（qcc 多键 → cleaner 认得的 structured_payload）+ `clean_raw_record`。

qcc 登记结果只将可共享企业事实落核心列，原始载荷只生成带密钥指纹；其他富化维度继续按
dimension 级 TTL 读穿，但必须经过公共投影后才可进入 `contact.meta_data`。

> live qcc 受基础设施门控（需平台 Bearer + 出站可达 agent.qcc.com，runbook 实施/100）；
> 本服务的查池命中路径、结构化映射纯函数、入池写穿（经真实 stub qcc MCP server）均零 mock 可测。
"""

from __future__ import annotations

import json

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from backend.app.external_mcp.service.gateway_service import external_mcp_gateway
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.app.hasn_growth.service.cleaner_service import clean_raw_record
from backend.app.hasn_growth.service.enterprise_info_client import (
    enterprise_record_to_structured,
    extract_records,
)
from backend.app.hasn_growth.service.funnel_service import _lead_to_dict
from backend.app.hasn_growth.service.industry_tagging_service import IndustryTaggingService
from backend.app.hasn_growth.service.lead_ingestion_privacy_service import (
    PrivateLeadWrite,
    lead_ingestion_privacy_service,
)
from backend.app.hasn_growth.service.lead_pool_query_service import lead_pool_query_service
from backend.app.hasn_growth.service.pii_keyring import require_growth_pii_keyring
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_growth.model import LeadContact

# qcc 6 namespace（runbook 实施/100 §1，对齐 qcc_seed.QCC_SERVERS）。
_QCC_COMPANY_NS = 'qcc_company'

# qcc_company 核心工具 canonical 名（runbook §1 真实联调，130+ 工具中的主工具）。
# call_system_tool → _ensure_canonical_cached 会按 canonical 解析 raw_name；qcc 改名 → 诚实 re-probe 后
# SCHEMA_HASH_MISMATCH（不静默兜底）。运维可经管理面核对自省缓存里的真实工具名。
_TOOL_REGISTRATION = f'hasn.ext.{_QCC_COMPANY_NS}.get_company_registration_info'
_TOOL_SEARCH = f'hasn.ext.{_QCC_COMPANY_NS}.get_company_by_query'

# qcc 全部工具的锚点入参名（企业名/信用代码查询锚点）——真实自省 schema 一律 `searchKey`
# （company/risk/ipr/operation/executive 各 server 工具 required 都含 `searchKey`；executive 类工具
# 另需 `personName` 等，本中台只供锚点，额外必填参数由该维度工具自身约束，超出锚点的不在此组装）。
_QCC_ARG_SEARCH_KEY = 'searchKey'

# enrich 维度 → qcc namespace（具体 qcc 工具由 namespace 自省缓存动态解析，不硬编码各维度 raw 名——
# runbook 仅文档化 qcc_company 工具名；risk/ipr/operation/executive/history 各 server 工具名以真实自省为准）。
_ENRICH_NAMESPACE = {
    'risk': 'qcc_risk',
    'ipr': 'qcc_ipr',
    'operation': 'qcc_operation',
    'executive': 'qcc_executive',
    'history': 'qcc_history',
}

# 富化缓存默认时效（小时）：dimension 级 read-through TTL（doc09 §3.3）。命中且未过期 → 不重复调 qcc。
_DEFAULT_ENRICH_TTL_HOURS = 24 * 7
_SEARCH_HARD_CAP = 20
_PUBLIC_REGISTRATION_FIELDS = frozenset({
    'City',
    'Industry',
    'Name',
    'Province',
    'business_status',
    'creditCode',
    'establishDate',
    'regNo',
    'status',
    'unified_social_credit_code',
})


def _qcc_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 `call_system_tool` 归一返回里防御式抽出企业记录列表（structured 优先，回落 text JSON）。

    通用网关 `_normalize_call_result` 返回 `{ok, is_error, text, content, structured, raw}`：
    - `structured`（structuredContent）是 qcc 结构化结果首选；
    - 缺则 `text`（content[].text 拼接）尝试 JSON 解析。
    再经 `extract_records` 兼容多容器形状抽企业记录（同 enterprise_info_client 防御式解析）。
    """
    if not result or result.get('is_error'):
        return []
    payload: Any = result.get('structured')
    if payload is None:
        text = result.get('text')
        if text:
            import json

            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                payload = None
    if payload is None:
        return []
    return extract_records(payload)


async def _call_qcc(
    *, tool_name: str, arguments: dict[str, Any], owner_hasn_id: str, agent_hasn_id: str | None, trace_id: str | None
) -> dict[str, Any]:
    """经通用网关以平台身份调一次 qcc system-origin 工具（架构A，绕 binding，配额归 caller owner）。"""
    return await external_mcp_gateway.call_system_tool(
        owner_hasn_id=owner_hasn_id,
        tool_name=tool_name,
        arguments=arguments,
        agent_hasn_id=agent_hasn_id,
        trace_id=trace_id,
    )


class EnterpriseLookupService:
    """读穿式企业数据中台（查池 → qcc → 入池 → 带 lead_id）。"""

    async def _ingest_record(
        self, db: AsyncSession, *, record: dict[str, Any], user_id: int, keyword: str
    ) -> LeadContact | None:
        """单条 qcc 企业记录拆为公共登记事实与 Owner 私有 PII，再建立 owner 引用。

        返回入池/命中复用的 `LeadContact`（公司名和联系人名均空时拒绝入池）。
        """
        if not settings.GROWTH_PII_NEW_WRITE_ENABLED:
            raise errors.ConflictError(
                msg='联系人 PII 新写尚未启用',
                data={'error_code': 'GROWTH_PII_NEW_WRITE_DISABLED'},
            )
        keyring = require_growth_pii_keyring()
        structured = enterprise_record_to_structured(record)
        raw_dict: dict[str, Any] = {
            'structured_payload': structured,
            'source_type': 'qcc',
            'keyword': keyword,
            'metadata': {'qcc_source': 'registration'},
        }
        # 经统一清洗器归一（email/phone/domain 标准化 + 评分）。company-only 也入池——这里只拒
        # 「公司名+联系人名全空」的真空壳，不靠 cleaned.accepted（accepted 的 email/phone 门是网爬线索语义，
        # 工商企业数据天然可无对外联系方式，不应据此拒入池）。
        cleaned = clean_raw_record(raw_dict, country_hint='CN')
        code = await IndustryTaggingService(db).normalize(
            raw_industry=cleaned.industry, company_name=cleaned.company_name
        )
        if code:
            cleaned.industry = code
        if not ((cleaned.company_name or '').strip() or (cleaned.contact_name or '').strip()):
            return None
        result = await lead_ingestion_privacy_service.upsert(
            db,
            keyring=keyring,
            write=PrivateLeadWrite(
                user_id=user_id,
                pool_visibility='public',
                company_name=cleaned.company_name,
                contact_name=cleaned.contact_name,
                email=cleaned.email,
                phone=cleaned.phone,
                address=cleaned.address,
                website=cleaned.website,
                domain=cleaned.domain,
                country=cleaned.country,
                region=cleaned.region,
                city=cleaned.city,
                industry=cleaned.industry,
                source_type='qcc',
                source_url=None,
                lawful_basis='public_business_source',
                source_ref='qcc:registration',
                retention_until=timezone.now() + timedelta(days=365),
                confidence_score=Decimal(str(cleaned.system_score)),
                public_metadata={},
            ),
        )
        contact = result.contact
        # 只保留可共享登记字段名和带密钥指纹；原始法人与渠道值不得进入公共 JSONB。
        meta = dict(contact.meta_data or {})
        qcc_meta = dict(meta.get('qcc') or {})
        qcc_meta.pop('registration', None)
        qcc_meta['registration_fields'] = sorted(
            str(field)
            for field in record
            if str(field) in _PUBLIC_REGISTRATION_FIELDS
        )
        serialized_record = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        )
        qcc_meta['registration_fingerprint'] = (
            f'v{keyring.active_hmac_version}:'
            f'{keyring.hmac_for("qcc_registration", serialized_record)}'
        )
        qcc_meta['fetched_at'] = timezone.now().isoformat()
        meta['qcc'] = qcc_meta
        contact.meta_data = meta
        await db.flush()
        # 统一池众包语义：为发起 owner 建引用（幂等），使其「拥有」该线索。
        await lead_automation_business_service._grant_refs(db, user_id=user_id, contact_ids={contact.id}, source='qcc')
        return contact

    async def lookup_company(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        agent_hasn_id: str | None,
        query: str,
        reveal_pii: bool = False,
        force_refresh: bool = False,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """按企业名/信用代码取全画像：查池命中即返回（省钱秒回）→ 未命中调 qcc registration → 入池 → 带 lead_id。

        返回 ``{from_pool, lead}``：``from_pool=True`` 即池命中（零 qcc 调用），``lead`` 含 ``lead_contact_id``。
        """
        query = (query or '').strip()
        if not query:
            raise ValueError('query 不能为空（企业名/统一社会信用代码）')

        # 1) 查公共池（命中即返回，零采集成本）。
        if not force_refresh:
            hits = await lead_pool_query_service.query_pool(db, keyword=query, limit=5)
            best = _best_company_match(hits, query)
            if best is not None:
                # 命中也为 owner 建引用（统一池：拥有=引用），返回带 lead_id。
                await lead_automation_business_service._grant_refs(
                    db, user_id=user_id, contact_ids={best.id}, source='qcc'
                )
                return {'from_pool': True, 'lead': _lead_to_dict(best, reveal_pii=reveal_pii)}

        # 2) 未命中 → 调通用网关 qcc registration（平台 key 由网关持有，不下发分身）。
        result = await _call_qcc(
            tool_name=_TOOL_REGISTRATION,
            arguments={_QCC_ARG_SEARCH_KEY: query},
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            trace_id=trace_id,
        )
        records = _qcc_records(result)
        if not records:
            return {'from_pool': False, 'lead': None, 'reason': 'qcc 无匹配企业'}

        # 3) 结构化全量入池（取首条最匹配）→ 4) 返回带 lead_id。
        contact = await self._ingest_record(db, record=records[0], user_id=user_id, keyword=query)
        if contact is None:
            return {'from_pool': False, 'lead': None, 'reason': 'qcc 返回缺企业名（空壳不入池）'}
        return {'from_pool': False, 'lead': _lead_to_dict(contact, reveal_pii=reveal_pii)}

    async def search_companies(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        agent_hasn_id: str | None,
        query: str | None = None,
        industry: str | None = None,
        region: str | None = None,
        city: str | None = None,
        limit: int = 5,
        reveal_pii: bool = False,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """按关键词/行业/地域找企业：先查池（条件匹配）→ 不足时调 qcc get_company_by_query 补 → 入池 → 带 lead_id 列表。

        返回 ``{from_pool, fetched, leads}``：``from_pool`` 池命中数、``fetched`` 经 qcc 新入池数。
        """
        n = max(1, min(int(limit), _SEARCH_HARD_CAP))
        # 1) 先查公共池（ICP/条件匹配）。
        pool_hits = await lead_pool_query_service.query_pool(
            db, industry=industry, region=region, city=city, keyword=query, limit=n
        )
        leads_by_id: dict[int, LeadContact] = {c.id: c for c in pool_hits[:n]}
        for cid in list(leads_by_id):
            await lead_automation_business_service._grant_refs(db, user_id=user_id, contact_ids={cid}, source='qcc')
        from_pool = len(leads_by_id)

        # 2) 不足 n → 调 qcc get_company_by_query 补（≤5 候选，消歧锚定型）。
        fetched = 0
        gap = n - len(leads_by_id)
        search_term = ' '.join(p for p in (query, industry, region, city) if p and p.strip()).strip()
        if gap > 0 and search_term:
            result = await _call_qcc(
                tool_name=_TOOL_SEARCH,
                arguments={_QCC_ARG_SEARCH_KEY: search_term},
                owner_hasn_id=owner_hasn_id,
                agent_hasn_id=agent_hasn_id,
                trace_id=trace_id,
            )
            for record in _qcc_records(result)[:gap]:
                contact = await self._ingest_record(db, record=record, user_id=user_id, keyword=search_term)
                if contact is not None and contact.id not in leads_by_id:
                    leads_by_id[contact.id] = contact
                    fetched += 1

        leads = [_lead_to_dict(c, reveal_pii=reveal_pii) for c in leads_by_id.values()]
        return {'from_pool': from_pool, 'fetched': fetched, 'requested': n, 'leads': leads}

    async def enrich_company(
        self,
        db: AsyncSession,
        *,
        lead_contact_id: int,
        user_id: int,
        owner_hasn_id: str,
        agent_hasn_id: str | None,
        dimensions: list[str],
        tool: str | None = None,
        ttl_hours: int = _DEFAULT_ENRICH_TTL_HOURS,
        force_refresh: bool = False,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """按维度深度富化（风险/知识产权/经营/高管/变更历史…）：查 meta 维度缓存（TTL 内命中即返回省钱）→
        未命中/过期调对应 qcc namespace 工具 → 入 ``contact.meta_data['enrichment'][dimension]`` 保真 → 返回。

        ``dimensions``：``risk``/``ipr``/``operation``/``executive``/``history`` 子集；``tool`` 可显式指定
        canonical 工具名覆盖默认（按 namespace 自省缓存动态解析首个工具）。返回每维度 ``{cached, summary, ...}``。
        """
        from sqlalchemy import select

        from backend.app.hasn_growth.model import LeadContact, LeadRef

        contact = (await db.execute(select(LeadContact).where(LeadContact.id == lead_contact_id))).scalar_one_or_none()
        if contact is None:
            raise ValueError(f'线索不存在: lead_contact_id={lead_contact_id}')
        # owner 须拥有该线索（lead_ref 引用）才可富化（防越权刷别 owner 的线索消耗本 owner 配额前的边界）。
        owns = (
            await db.execute(
                select(LeadRef.id).where(LeadRef.user_id == user_id, LeadRef.lead_contact_id == lead_contact_id)
            )
        ).scalar_one_or_none()
        if owns is None:
            raise ValueError('无权富化该线索（请先 lookup/search 获取该线索）')

        anchor = (contact.company_name or '').strip() or (contact.meta_data or {}).get('qcc', {}).get(
            'registration', {}
        ).get('unified_social_credit_code', '')
        if not anchor:
            raise ValueError('该线索缺企业名/信用代码锚点，无法富化')

        meta = dict(contact.meta_data or {})
        enrichment = dict(meta.get('enrichment') or {})
        out: dict[str, Any] = {}
        now = timezone.now()
        changed = False
        for dim in dimensions:
            ns = _ENRICH_NAMESPACE.get(dim)
            if ns is None:
                out[dim] = {'error': f'未知维度: {dim}（支持 {sorted(_ENRICH_NAMESPACE)}）'}
                continue
            cached = enrichment.get(dim)
            if cached and not force_refresh and _is_fresh(cached.get('fetched_at'), ttl_hours=ttl_hours):
                out[dim] = {'cached': True, 'summary': cached.get('summary'), 'fetched_at': cached.get('fetched_at')}
                continue
            tool_name = tool if (tool and dim == dimensions[0]) else await _resolve_namespace_tool(ns)
            if not tool_name:
                out[dim] = {'error': f'{ns} 无可用 qcc 工具（需先自省该 server）'}
                continue
            result = await _call_qcc(
                tool_name=tool_name,
                arguments={_QCC_ARG_SEARCH_KEY: anchor},
                owner_hasn_id=owner_hasn_id,
                agent_hasn_id=agent_hasn_id,
                trace_id=trace_id,
            )
            serialized_result = json.dumps(
                {
                    'structured': result.get('structured'),
                    'text': result.get('text'),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(',', ':'),
                default=str,
            )
            entry = {
                'summary': _summarize(result),
                'result_count': _enrichment_result_count(result),
                'result_fingerprint': (
                    f'v{require_growth_pii_keyring().active_hmac_version}:'
                    f'{require_growth_pii_keyring().hmac_for(f"qcc_enrichment:{dim}", serialized_result)}'
                ),
                'source': 'qcc',
                'tool': tool_name,
                'fetched_at': now.isoformat(),
            }
            enrichment[dim] = entry
            changed = True
            out[dim] = {'cached': False, 'summary': entry['summary'], 'fetched_at': entry['fetched_at']}

        if changed:
            meta['enrichment'] = enrichment
            contact.meta_data = meta
            await db.flush()
            log.info(f'[EnterpriseEnrich] lead={lead_contact_id} 富化维度 {[d for d in out if "error" not in out[d]]}')
        return {'lead_contact_id': lead_contact_id, 'company_name': contact.company_name, 'dimensions': out}


def _best_company_match(hits: list[LeadContact], query: str) -> LeadContact | None:
    """池命中里取最匹配企业：优先公司名包含 query 的，按置信度倒序；都不含则不算命中（避免误返不相关行）。"""
    q = query.strip().lower()
    named = [c for c in hits if c.company_name and q in c.company_name.lower()]
    pool = named or [c for c in hits if c.company_name and c.company_name.lower() in q]
    if not pool:
        return None
    return max(pool, key=lambda c: float(c.confidence_score) if c.confidence_score is not None else 0.0)


def _is_fresh(fetched_at: str | None, *, ttl_hours: int) -> bool:
    """维度缓存是否在 TTL 内（doc09 §3.3 dimension 级 read-through 时效）。解析失败 → 视为过期（保守重取）。"""
    if not fetched_at:
        return False
    from datetime import datetime, timedelta

    try:
        ts = datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        return False
    return (timezone.now() - ts) < timedelta(hours=max(0, ttl_hours))


def _summarize(result: dict[str, Any]) -> str | None:
    """只返回派生统计摘要，绝不复制 QCC 原始文本。"""
    count = _enrichment_result_count(result)
    return f'已获取 {count} 条结果' if count is not None else None


def _enrichment_result_count(result: dict[str, Any]) -> int | None:
    """从常见结构化容器派生条目数；无法判定时不猜测。"""
    payload = result.get('structured')
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in ('data', 'items', 'records', 'result', 'results'):
        nested = payload.get(key)
        if isinstance(nested, list):
            return len(nested)
    return 1 if payload else 0


async def _resolve_namespace_tool(namespace: str) -> str | None:
    """从某 system-origin namespace 的自省缓存动态解析首个可用 canonical 工具名（无缓存/非 system → None）。

    避免硬编码各 qcc server 的 raw 工具名（runbook 仅文档化 qcc_company 工具）；以真实自省为准。
    """
    tools = await external_mcp_gateway.list_system_tools(namespace)
    return tools[0]['name'] if tools else None


enterprise_lookup_service = EnterpriseLookupService()
