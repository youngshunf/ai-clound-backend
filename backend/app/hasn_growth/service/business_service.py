from __future__ import annotations

import json

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model import (
    LeadAuditLog,
    LeadCollectionJob,
    LeadContact,
    LeadContactSource,
    LeadExportBatch,
    LeadExportItem,
    LeadFirecrawlRequest,
    LeadRawRecord,
    LeadRef,
    LeadRejectedRecord,
    LeadSourceConfig,
)
from backend.app.hasn_growth.service.cleaner_service import clean_raw_record
from backend.app.hasn_growth.service.contact_privacy_service import (
    ensure_growth_pii_key_write_fence,
)
from backend.app.hasn_growth.service.export_service import build_csv_export
from backend.app.hasn_growth.service.firecrawl_client import DEFAULT_FIRECRAWL_BASE_URL, FirecrawlClient
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.industry_tagging_service import IndustryTaggingService
from backend.app.hasn_growth.service.lead_ingestion_privacy_service import (
    PrivateLeadWrite,
    lead_ingestion_privacy_service,
)
from backend.app.hasn_growth.service.llm_extractor import LeadLLMExtractor, build_default_extractor
from backend.app.hasn_growth.service.metering_service import growth_metering_service
from backend.app.hasn_growth.service.pii import mask_contact_fields, redact_pii_value
from backend.app.hasn_growth.service.pii_keyring import (
    GrowthPiiKeyring,
    require_growth_pii_keyring,
)
from backend.app.hasn_growth.service.project_lead_compatibility_service import (
    project_lead_compatibility_service,
)
from backend.app.hasn_growth.service.provider_registry import CrawlRequest, CrawledItem, get_provider
from backend.app.hasn_growth.service.url_dedup_service import UrlDedupService
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_growth.schema.business import CreateLeadJobParam


class LeadAutomationBusinessService:
    def __init__(
        self,
        firecrawl_client: FirecrawlClient | None = None,
        llm_extractor: LeadLLMExtractor | None = None,
    ) -> None:
        self.firecrawl_client = firecrawl_client or FirecrawlClient(
            base_url=settings.FIRECRAWL_BASE_URL or DEFAULT_FIRECRAWL_BASE_URL,
            api_key=settings.FIRECRAWL_API_KEY or None,
        )
        # 方案 A：firecrawl 只抓 markdown，结构化提取由后端 LLM 完成（未配置则为 None，退正则兜底）。
        self.llm_extractor = llm_extractor or build_default_extractor()

    async def create_job(self, db: AsyncSession, obj: CreateLeadJobParam) -> dict[str, Any]:
        # 统一线索池：采集结果恒进公共池；job.user_id 记「谁发起的采集」（collect→主人 / backfill→请求者 / 系统→None），
        # run_job 跑完为发起者建 lead_ref（用户引用），线索同时供众包复用。
        keyword = _sanitize_collection_keyword(obj.keyword)
        request_config = _sanitize_collection_config(obj.request_config)
        job = LeadCollectionJob(
            job_no=f'LAJ{datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")}',
            keyword=keyword,
            source_types=obj.source_types,
            user_id=obj.user_id,
            status='pending',
            max_pages=obj.max_pages,
            max_results=obj.max_results,
            request_config=request_config,
            meta_data={},
        )
        db.add(job)
        await db.flush()
        return model_to_dict(job)

    async def _ingest_crawled_item(
        self,
        db: AsyncSession,
        *,
        job: LeadCollectionJob,
        item: CrawledItem,
        keyring: GrowthPiiKeyring,
        tagger: IndustryTaggingService,
    ) -> dict[str, Any]:
        """清洗单条采集结果后只持久化公共投影，PII 立即写入 Owner 私有表。"""
        raw_dict: dict[str, Any] = {
            'job_id': job.id,
            'source_type': item.source_type,
            'source_url': item.source_url,
            'domain': _domain(item.source_url),
            'title': item.title,
            'markdown': item.markdown,
            'raw_text': item.raw_text,
            'raw_html': item.raw_html,
            'raw_payload': item.raw_payload,
            'structured_payload': item.structured_payload,
            'llm_confidence': item.llm_confidence,
            'extract_mode': item.extract_mode,
            'metadata': dict(item.metadata),
        }
        cleaned = clean_raw_record(
            raw_dict,
            min_contact_fields=_contact_field_requirements(job.request_config or {}),
            country_hint=(job.request_config or {}).get('country_hint', 'CN'),
        )
        pii_fields, pii_values = _collection_pii(item=item, cleaned=cleaned)
        source_url = _safe_source_url(item.source_url, sensitive_values=pii_values)
        content_fingerprint = _hmac_fingerprint(
            keyring,
            namespace='raw_record',
            values=[
                item.source_url,
                item.title,
                item.markdown,
                item.raw_text,
                item.raw_html,
                item.raw_payload,
                item.structured_payload,
            ],
        )
        safe_metadata = _safe_collection_metadata(
            keyring,
            item=item,
            pii_fields=pii_fields,
            pii_values=pii_values,
        )
        firecrawl_request = LeadFirecrawlRequest(
            job_id=job.id,
            source_type=item.source_type,
            endpoint='/v1/extract' if item.extract_mode == 'extract' else '/v1/scrape',
            target_url=source_url,
            request_payload=_safe_request_payload(
                keyring,
                keyword=job.keyword,
                config=job.request_config or {},
            ),
            extract_mode=item.extract_mode or 'scrape_json',
            llm_schema_version=_safe_token(item.metadata.get('llm_schema_version'), max_length=64),
            llm_prompt_version=_safe_token(item.metadata.get('llm_prompt_version'), max_length=64),
            status='succeeded',
            attempt_count=_safe_positive_int(item.metadata.get('attempt_count'), default=1),
            result_count=1,
            meta_data=safe_metadata,
        )
        db.add(firecrawl_request)
        await db.flush()

        duplicate_raw = await db.scalar(
            sa.select(LeadRawRecord.id).where(
                LeadRawRecord.job_id == job.id,
                LeadRawRecord.content_hash == content_fingerprint,
            )
        )
        if duplicate_raw is not None:
            job.duplicate_count += 1
            return {
                'created': False,
                'rejected': False,
                'duplicate': True,
                'raw_record_id': int(duplicate_raw),
                'contact_id': None,
            }

        raw_record = LeadRawRecord(
            job_id=job.id,
            firecrawl_request_id=firecrawl_request.id,
            source_type=item.source_type,
            source_url=source_url,
            domain=_domain(source_url),
            title=_safe_public_text(cleaned.company_name),
            markdown=None,
            raw_text=None,
            raw_html=None,
            raw_payload=None,
            structured_payload=None,
            llm_confidence=Decimal(str(item.llm_confidence)) if item.llm_confidence is not None else None,
            system_score=Decimal(str(cleaned.system_score)),
            content_hash=content_fingerprint,
            normalization_version=f'privacy-v1-hmac-v{keyring.active_hmac_version}',
            status='cleaned' if cleaned.accepted else 'invalid',
            meta_data=safe_metadata,
        )
        db.add(raw_record)
        await db.flush()
        job.raw_count += 1

        if not cleaned.accepted:
            await self._persist_rejected(
                db,
                job_id=job.id,
                raw_record_id=raw_record.id,
                firecrawl_request_id=firecrawl_request.id,
                source_type=item.source_type,
                source_url=source_url,
                reason=cleaned.rejected_reason or 'missing_contact',
                keyring=keyring,
                pii_fields=pii_fields,
                pii_values=pii_values,
            )
            job.invalid_count += 1
            return {
                'created': False,
                'rejected': True,
                'duplicate': False,
                'raw_record_id': raw_record.id,
                'contact_id': None,
            }

        industry_code = await tagger.normalize(
            raw_industry=cleaned.industry,
            company_name=cleaned.company_name,
        )
        if industry_code:
            cleaned.industry = industry_code
        if not (cleaned.company_name or '').strip() and (
            job.user_id is None or not (cleaned.contact_name or '').strip()
        ):
            await self._persist_rejected(
                db,
                job_id=job.id,
                raw_record_id=raw_record.id,
                firecrawl_request_id=firecrawl_request.id,
                source_type=item.source_type,
                source_url=source_url,
                reason='missing_name',
                keyring=keyring,
                pii_fields=pii_fields,
                pii_values=pii_values,
            )
            raw_record.status = 'invalid'
            job.invalid_count += 1
            return {
                'created': False,
                'rejected': True,
                'duplicate': False,
                'raw_record_id': raw_record.id,
                'contact_id': None,
            }

        write_result = await lead_ingestion_privacy_service.upsert(
            db,
            keyring=keyring,
            write=PrivateLeadWrite(
                user_id=job.user_id,
                pool_visibility='public',
                company_name=cleaned.company_name,
                contact_name=cleaned.contact_name,
                email=cleaned.email,
                phone=cleaned.phone,
                address=cleaned.address,
                website=_safe_source_url(cleaned.website, sensitive_values=pii_values),
                domain=cleaned.domain,
                country=cleaned.country,
                region=cleaned.region,
                city=cleaned.city,
                industry=cleaned.industry,
                source_type=item.source_type,
                source_url=source_url,
                lawful_basis='public_business_source',
                source_ref=f'raw_record:{raw_record.id}',
                retention_until=timezone.now() + timedelta(days=365),
                confidence_score=Decimal(str(cleaned.system_score)),
                public_metadata={
                    'llm_schema_version': safe_metadata.get('llm_schema_version'),
                    'llm_prompt_version': safe_metadata.get('llm_prompt_version'),
                    'source_fingerprint': safe_metadata['source_fingerprint'],
                },
            ),
        )
        contact = write_result.contact
        db.add(
            LeadContactSource(
                lead_contact_id=contact.id,
                raw_record_id=raw_record.id,
                firecrawl_request_id=firecrawl_request.id,
                source_type=item.source_type,
                source_url=source_url,
                match_dimension=write_result.match_dimension,
                meta_data={
                    'source_fingerprint': safe_metadata['source_fingerprint'],
                    'pii_fields': pii_fields,
                },
            )
        )
        if write_result.created:
            job.valid_count += 1
        else:
            raw_record.status = 'duplicate'
            job.duplicate_count += 1
        return {
            'created': write_result.created,
            'rejected': False,
            'duplicate': not write_result.created,
            'raw_record_id': raw_record.id,
            'contact_id': contact.id,
        }

    async def run_job(  # ruff: ignore[complex-structure]
        self, db: AsyncSession, job_id: int, *, user_id: int | None = None, admin: bool = False
    ) -> dict[str, Any]:
        job = await db.get(LeadCollectionJob, job_id)
        if job is None:
            raise errors.NotFoundError(msg='采集任务不存在')
        if not admin and user_id is not None and job.user_id is not None and job.user_id != user_id:
            raise errors.ForbiddenError(msg='无权执行该采集任务')
        if not settings.GROWTH_PII_NEW_WRITE_ENABLED:
            raise errors.ConflictError(
                msg='联系人 PII 新写尚未启用',
                data={'error_code': 'GROWTH_PII_NEW_WRITE_DISABLED'},
            )
        keyring = require_growth_pii_keyring()
        await ensure_growth_pii_key_write_fence(db, keyring=keyring)
        job.status = 'running'
        job.started_at = datetime.now(UTC)
        # 2.2 行业标准化打标器（job 级缓存字典；规则优先，配了 new-api 网关才走 LLM 兜底）。
        tagger = IndustryTaggingService(db, enable_llm=self.llm_extractor is not None)
        request_count = 0
        # 统一线索池：本 job 涉及的池线索 id（新建+命中复用），跑完为发起者 job.user_id 批量建 lead_ref（众包+引用）。
        acquired_contact_ids: set[int] = set()
        for source_type in _as_list(job.source_types):
            if job.valid_count >= job.max_results:
                break  # ④ 精确配额：已凑够目标有效线索数，停止后续数据源（省抓取成本）
            request_count += 1
            dedup = UrlDedupService(db, job_id=job.id, source_type=source_type)
            try:
                provider = get_provider(source_type)
            except Exception as exc:
                await self._persist_rejected(
                    db,
                    job_id=job.id,
                    source_type=source_type,
                    reason='firecrawl_failed',
                    error_type=type(exc).__name__,
                )
                job.firecrawl_failed_count += 1
                continue

            # 2.4 真流式收口：逐条抓取消费，每抓**下一条前**查"够 N 没"，够了即停 provider（不再多抓·
            # 省 firecrawl/LLM 成本，补爬精确到条零冗余·doc93 2.4）。抓取异常按数据源粒度兜底，
            # 已抓到的有效线索保留（区别一次性 crawl 的整源 all-or-nothing）。
            stream = provider.crawl_stream(
                CrawlRequest(
                    job_id=job.id,
                    keyword=job.keyword,
                    source_type=source_type,
                    user_id=job.user_id,
                    max_pages=job.max_pages,
                    max_results=job.max_results,
                    config=job.request_config or {},
                ),
                firecrawl_client=self.firecrawl_client,
                llm_extractor=self.llm_extractor,
                dedup=dedup,
                should_continue=lambda: job.valid_count < job.max_results,
            )
            job.firecrawl_success_count += 1  # 乐观计数：抓取成功（首条抓取即异常则在 except 回退）
            while True:
                if job.valid_count >= job.max_results:
                    break  # ④ 已凑够目标有效线索数，停止处理/抓取剩余项（generator 不再被拉取 → 停抓）
                try:
                    item = await stream.__anext__()
                except StopAsyncIteration:
                    break  # 候选 URL 抓完
                except Exception as exc:  # 本数据源抓取失败（firecrawl/LLM）→ 回退乐观计数、记失败、跳过该源
                    job.firecrawl_success_count -= 1
                    job.firecrawl_failed_count += 1
                    await self._persist_rejected(
                        db,
                        job_id=job.id,
                        source_type=source_type,
                        reason='firecrawl_failed',
                        error_type=type(exc).__name__,
                    )
                    break
                job.total_found += 1
                # ② 登记到已抓 URL 池（outcome 据是否取到正文；lead_yield 在新增有效线索后回填）
                await dedup.register(
                    item.source_url or '',
                    outcome='succeeded' if (item.markdown or '').strip() else 'empty',
                )
                outcome = await self._ingest_crawled_item(
                    db,
                    job=job,
                    item=item,
                    keyring=keyring,
                    tagger=tagger,
                )
                if outcome['contact_id'] is not None:
                    acquired_contact_ids.add(int(outcome['contact_id']))
                if outcome['created']:
                    await dedup.bump_lead_yield(item.source_url or '')

        job.finished_at = datetime.now(UTC)
        job.status = _final_status(job, request_count)
        # 统一线索池：采集结果已入公共池；为发起者建用户引用（owner 经 lead_ref 拥有·线索同时众包复用）。
        # 系统采集（job.user_id 空）只入池不建引用。
        if job.user_id and acquired_contact_ids:
            await self._grant_refs(db, user_id=job.user_id, contact_ids=acquired_contact_ids, source='collect')
        await db.flush()

        # 计量上报（G7：采集按量计积分，获客只报量不自建账本；best-effort 不阻断）。
        await growth_metering_service.report_crawl_usage(
            db,
            user_id=getattr(job, 'user_id', None) or user_id,
            job_id=job.id,
            success_count=job.firecrawl_success_count,
        )
        # M6 通知卡片：新线索批次落库 → 提醒发起者去筛（有发起者 + 有新增有效线索时）。
        if job.user_id and job.valid_count > 0:
            owner_hasn_id = (
                await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == job.user_id))
            ).scalar_one_or_none()
            if owner_hasn_id:
                await growth_notification_service.leads_collected_batch(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    job_id=job.id,
                    new_count=job.valid_count,
                )
        return model_to_dict(job)

    async def get_job(
        self, db: AsyncSession, *, job_id: int, user_id: int | None = None, admin: bool = False
    ) -> dict[str, Any]:
        job = await db.get(LeadCollectionJob, job_id)
        if job is None:
            raise errors.NotFoundError(msg='采集任务不存在')
        if not admin and job.user_id is not None and job.user_id != user_id:
            raise errors.ForbiddenError(msg='无权访问该采集任务')
        return model_to_dict(job)

    async def list_rejected(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        job_id: int | None = None,
        admin: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(LeadRejectedRecord).order_by(LeadRejectedRecord.id.desc())
        if job_id is not None:
            stmt = stmt.where(LeadRejectedRecord.job_id == job_id)
        if not admin:
            # 统一线索池：用户只看自己发起的采集任务的拒绝记录（job.user_id = 发起者）。
            visible_jobs = sa.select(LeadCollectionJob.id).where(LeadCollectionJob.user_id == user_id)
            stmt = stmt.where(LeadRejectedRecord.job_id.in_(visible_jobs))
        rows = [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]
        return [mask_contact_fields(row, reveal=False) for row in rows]

    async def list_audit_logs(
        self,
        db: AsyncSession,
        *,
        event_type: str | None = None,
        actor_user_id: int | None = None,
        target_table: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = sa.select(LeadAuditLog).order_by(LeadAuditLog.id.desc()).limit(max(1, min(limit, 500)))
        if event_type:
            stmt = stmt.where(LeadAuditLog.event_type == event_type)
        if actor_user_id is not None:
            stmt = stmt.where(LeadAuditLog.actor_user_id == actor_user_id)
        if target_table:
            stmt = stmt.where(LeadAuditLog.target_table == target_table)
        return [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]

    async def list_contacts(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        admin: bool = False,
        masked: bool = True,
    ) -> list[dict[str, Any]]:
        # 统一线索池：非 admin 返回「该用户引用的线索」（lead_ref JOIN contact），用户级状态取自 ref；
        # admin 返回全公共池（池级 status）。已忽略（dismissed）的引用不出现在用户列表。
        if admin:
            stmt = sa.select(LeadContact).order_by(LeadContact.id.desc())
            rows = [model_to_dict(row) for row in (await db.execute(stmt)).scalars().all()]
        else:
            join_stmt = (
                sa
                .select(LeadContact, LeadRef)
                .join(LeadRef, LeadRef.lead_contact_id == LeadContact.id)
                .where(LeadRef.user_id == user_id, LeadRef.status != 'dismissed')
                .order_by(LeadContact.id.desc())
            )
            rows = []
            for contact, ref in (await db.execute(join_stmt)).all():
                data = model_to_dict(contact)
                data['status'] = ref.status  # 用户级状态覆盖池级（new/qualified）
                data['ref_source'] = ref.source
                rows.append(data)
        return [mask_contact_fields(row, reveal=False) for row in rows]

    async def update_blacklist(self, db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
        source_type = str(payload.get('source_type') or 'public_web')
        name = str(payload.get('name') or 'default')
        config = await db.scalar(
            sa.select(LeadSourceConfig).where(
                LeadSourceConfig.source_type == source_type, LeadSourceConfig.name == name
            )
        )
        if config is None:
            config = LeadSourceConfig(
                source_type=source_type,
                name=name,
                enabled=True,
                firecrawl_options={},
                min_contact_fields=['email', 'phone'],
                persist_raw_html=False,
                max_html_bytes=524288,
                domain_blacklist=[],
                country_blacklist=['DE', 'FR', 'IT', 'NL', 'ES'],
                rate_limit_per_minute=60,
                concurrency=3,
                meta_data={},
            )
            db.add(config)
            await db.flush()
        config.domain_blacklist = payload.get('domain_blacklist') or config.domain_blacklist or []
        config.country_blacklist = payload.get('country_blacklist') or config.country_blacklist or []
        db.add(
            LeadAuditLog(
                event_type='config_change',
                actor_role='admin',
                target_table='lead_source_config',
                target_count=1,
                target_ref=f'{source_type}:{name}',
                payload={
                    'source_type': source_type,
                    'name': name,
                    'domain_blacklist_count': len(config.domain_blacklist or []),
                    'country_blacklist_count': len(config.country_blacklist or []),
                },
                result='success',
            )
        )
        await db.flush()
        return model_to_dict(config)

    async def export_contacts(
        self, db: AsyncSession, *, user_id: int, filter_payload: dict | None = None
    ) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        start = datetime(today.year, today.month, today.day, tzinfo=UTC)
        export_count = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(LeadExportBatch)
            .where(LeadExportBatch.user_id == user_id, LeadExportBatch.created_time >= start)
        )
        if (export_count or 0) >= 3:
            raise ValueError('daily export limit exceeded')
        rows = await self.list_contacts(db, user_id=user_id, admin=False, masked=True)
        batch_no = f'LEX{datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")}'
        result = build_csv_export(
            rows[:5000], batch_no=batch_no, user_id=user_id, filter_payload=filter_payload or {}, now=datetime.now(UTC)
        )
        batch = LeadExportBatch(
            batch_no=batch_no,
            user_id=user_id,
            filter_payload=filter_payload or {},
            format='csv',
            total_count=result.batch['total_count'],
            file_sha256=result.batch['file_sha256'],
            status='succeeded',
            started_at=result.batch['started_at'],
            finished_at=result.batch['finished_at'],
        )
        db.add(batch)
        await db.flush()
        for item in result.items:
            db.add(
                LeadExportItem(
                    batch_id=batch.id,
                    lead_contact_id=item['lead_contact_id'],
                    lead_no=item['lead_no'],
                    snapshot=item['snapshot'],
                )
            )
        # 统一线索池：导出是用户私有动作，不再把共享池行标 status='exported'（一人导出不应影响他人视图）。
        db.add(
            LeadAuditLog(
                event_type='export',
                actor_user_id=user_id,
                actor_role='app',
                target_table='lead_export_batch',
                target_count=result.batch['total_count'],
                target_ref=batch_no,
                payload=result.audit_log['payload'],
                result='success',
            )
        )
        await db.flush()
        return {'batch': model_to_dict(batch), 'items': result.items, 'csv': result.csv_text}

    async def archive_expired(self, db: AsyncSession) -> int:
        result = await db.execute(
            sa.select(LeadContact).where(
                LeadContact.archived_at <= datetime.now(UTC),
                LeadContact.status.notin_(['contacted', 'exported']),
            )
        )
        contacts = result.scalars().all()
        for contact in contacts:
            contact.status = 'archived'
            contact.email = None
            contact.phone = None
            contact.email_normalized = None
            contact.phone_normalized = None
        if contacts:
            db.add(
                LeadAuditLog(
                    event_type='archive_run',
                    actor_role='system',
                    target_table='lead_contact',
                    target_count=len(contacts),
                    payload={'archived_count': len(contacts)},
                    result='success',
                )
            )
        await db.flush()
        return len(contacts)

    async def extend_retention(self, db: AsyncSession, *, contact_id: int) -> dict[str, Any]:
        from datetime import timedelta

        contact = await db.get(LeadContact, contact_id)
        if contact is None:
            raise errors.NotFoundError(msg='线索不存在')
        contact.archived_at = datetime.now(UTC) + timedelta(days=548)
        db.add(
            LeadAuditLog(
                event_type='config_change',
                actor_role='admin',
                target_table='lead_contact',
                target_count=1,
                target_ref=contact.lead_no,
                payload={'action': 'extend_retention', 'contact_id': contact_id},
                result='success',
            )
        )
        await db.flush()
        return model_to_dict(contact)

    async def dsr_delete_by_email(
        self, db: AsyncSession, *, emails: list[str], request_id: str | None = None
    ) -> dict[str, Any]:
        from backend.app.hasn_growth.service.cleaner_service import normalize_email

        normalized = [email for email in (normalize_email(email) for email in emails) if email]
        contacts = (
            (await db.execute(sa.select(LeadContact).where(LeadContact.email_normalized.in_(normalized))))
            .scalars()
            .all()
        )
        for contact in contacts:
            contact.email = None
            contact.email_normalized = None
        audit = LeadAuditLog(
            event_type='dsr_delete_email',
            actor_role='admin',
            target_table='lead_contact',
            target_count=len(contacts),
            payload={'request_id': request_id, 'target_emails_sha256': [_sha256(value) for value in normalized]},
            result='success',
        )
        db.add(audit)
        await db.flush()
        return model_to_dict(audit)

    async def dsr_delete_by_phone(
        self,
        db: AsyncSession,
        *,
        phones: list[str],
        country_hint: str = 'CN',
        request_id: str | None = None,
    ) -> dict[str, Any]:
        from backend.app.hasn_growth.service.cleaner_service import normalize_phone

        normalized = [
            phone for phone in (normalize_phone(phone, country_hint=country_hint) for phone in phones) if phone
        ]
        contacts = (
            (await db.execute(sa.select(LeadContact).where(LeadContact.phone_normalized.in_(normalized))))
            .scalars()
            .all()
        )
        for contact in contacts:
            contact.phone = None
            contact.phone_normalized = None
        audit = LeadAuditLog(
            event_type='dsr_delete_phone',
            actor_role='admin',
            target_table='lead_contact',
            target_count=len(contacts),
            payload={'request_id': request_id, 'target_phones_sha256': [_sha256(value) for value in normalized]},
            result='success',
        )
        db.add(audit)
        await db.flush()
        return model_to_dict(audit)

    @staticmethod
    async def _grant_refs(db: AsyncSession, *, user_id: int, contact_ids: set[int], source: str) -> None:
        """统一线索池：为用户批量建线索引用（幂等·已引用则跳过）。采集/补爬跑完调用，让发起者「拥有」入池线索。"""
        if not user_id or not contact_ids:
            return
        if settings.GROWTH_PROJECT_DUAL_WRITE_ENABLED:
            for contact_id in sorted(contact_ids):
                await project_lead_compatibility_service.upsert_reference(
                    db,
                    user_id=user_id,
                    lead_contact_id=contact_id,
                    source=source,
                    status='new',
                    update_existing=False,
                )
            return
        await db.execute(
            pg_insert(LeadRef)
            .values([
                {'user_id': user_id, 'lead_contact_id': cid, 'source': source, 'status': 'new'} for cid in contact_ids
            ])
            .on_conflict_do_nothing(constraint='uq_growth_lead_ref_user_lead')
        )
        await db.flush()

    async def _persist_rejected(self, db: AsyncSession, *, job_id: int, reason: str, **kwargs: Any) -> None:
        keyring = kwargs.get('keyring')
        pii_fields = sorted({str(field) for field in kwargs.get('pii_fields') or [] if str(field)})
        pii_values = [value for value in kwargs.get('pii_values') or [] if value]
        metadata: dict[str, Any] = {
            'pii_fields': pii_fields,
            'pii_fingerprint': (
                f'v{keyring.active_hmac_version}:'
                f'{_hmac_fingerprint(keyring, namespace="rejected_record", values=pii_values)}'
                if isinstance(keyring, GrowthPiiKeyring) and pii_values
                else None
            ),
        }
        db.add(
            LeadRejectedRecord(
                job_id=job_id,
                raw_record_id=kwargs.get('raw_record_id'),
                firecrawl_request_id=kwargs.get('firecrawl_request_id'),
                source_type=kwargs.get('source_type'),
                source_url=kwargs.get('source_url'),
                reason=reason,
                email=None,
                phone=None,
                raw_excerpt=None,
                error_message=_safe_token(kwargs.get('error_type'), max_length=128),
                meta_data=metadata,
            )
        )


lead_automation_business_service = LeadAutomationBusinessService()


def model_to_dict(model: Any) -> dict[str, Any]:
    # 注意：meta_data 列的 DB 名是 'metadata'，其 column.key 也是 'metadata'，
    # 但 ORM 属性名是 'meta_data'。直接 getattr(model, column.key) 会取到 SQLAlchemy
    # 类级 .metadata（MetaData 对象）导致序列化炸。改经 mapper 取真实 ORM 属性名。
    mapper = sa.inspect(model).mapper
    data = {}
    for column in model.__table__.columns:
        attr = mapper.get_property_by_column(column).key
        data[column.name] = getattr(model, attr)
    return data


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple | set):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [str(item) for item in value.values()]
    return [str(value)] if value else []


def _final_status(job: LeadCollectionJob, request_count: int) -> str:
    if request_count > 0 and job.firecrawl_failed_count == request_count:
        return 'failed'
    if job.firecrawl_success_count > 0 and job.firecrawl_failed_count > 0:
        return 'partial_succeeded'
    return 'succeeded'


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    return urlparse(url).netloc.removeprefix('www.').lower()


def _safe_token(value: Any, *, max_length: int) -> str | None:
    """只保留内部枚举/版本/异常类型可用的安全 token。"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length:
        return None
    if not all(character.isalnum() or character in '._:-' for character in cleaned):
        return None
    return cleaned


def _safe_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _contains_detectable_pii(value: str) -> bool:
    """判断文本是否包含当前识别器可稳定识别的邮箱或电话号码。"""
    return redact_pii_value(value) != value


def _safe_config_text(value: Any, *, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise errors.RequestError(msg=f'采集配置 {field} 必须是字符串')
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length or _contains_detectable_pii(cleaned):
        raise errors.RequestError(msg=f'采集配置 {field} 无效或包含 PII')
    return cleaned


def _sanitize_collection_url(value: str, *, preserve_query: bool) -> str:
    """清理采集 URL 凭据和片段；任务关键词额外丢弃全部查询参数。"""
    candidate = value.strip()
    if not candidate.startswith(('http://', 'https://')):
        candidate = f'https://{candidate.strip("/")}'
    try:
        parsed = urlsplit(candidate)
        if not parsed.hostname:
            raise ValueError
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError as exc:
        raise errors.RequestError(msg='采集 URL 无效') from exc
    path = parsed.path or '/'
    query = f'?{parsed.query}' if preserve_query and parsed.query else ''
    safe = f'{parsed.scheme}://{parsed.hostname.casefold()}{port}{path}{query}'
    if len(safe) > 2048 or _contains_detectable_pii(safe):
        raise errors.RequestError(msg='采集 URL 无效或包含 PII')
    return safe


def _sanitize_collection_keyword(value: str) -> str:
    """任务关键词禁止 PII；URL 只保留无凭据、无查询、无片段的抓取地址。"""
    keyword = value.strip()
    if not keyword:
        raise errors.RequestError(msg='采集关键词不能为空')
    if not keyword.startswith(('http://', 'https://')) and _contains_detectable_pii(keyword):
        raise errors.RequestError(msg='采集关键词不得包含联系人 PII')
    looks_like_url = keyword.startswith(('http://', 'https://')) or (
        '.' in keyword and not any(character.isspace() for character in keyword)
    )
    if looks_like_url:
        return _sanitize_collection_url(keyword, preserve_query=False)
    return keyword


def _sanitize_firecrawl_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.RequestError(msg='采集配置 firecrawl_options 必须是对象')
    allowed = {'extract_mode', 'prompt_version', 'schema_version', 'search_limit'}
    if unknown := set(value) - allowed:
        raise errors.RequestError(msg=f'采集配置包含未声明字段: firecrawl_options.{min(unknown)}')
    sanitized: dict[str, Any] = {}
    for field in ('extract_mode', 'prompt_version', 'schema_version'):
        if field in value:
            sanitized[field] = _safe_config_text(value[field], field=field, max_length=64)
    if 'search_limit' in value:
        try:
            search_limit = int(value['search_limit'])
        except (TypeError, ValueError) as exc:
            raise errors.RequestError(msg='采集配置 search_limit 必须是整数') from exc
        if not 1 <= search_limit <= 50:
            raise errors.RequestError(msg='采集配置 search_limit 超出范围')
        sanitized['search_limit'] = search_limit
    return sanitized


def _sanitize_crawler_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.RequestError(msg='采集配置 crawler_options 必须是对象')
    allowed = {
        'detail_fields',
        'detail_link_css',
        'next_page_css',
        'search_url_template',
        'start_urls',
    }
    if unknown := set(value) - allowed:
        raise errors.RequestError(msg=f'采集配置包含未声明字段: crawler_options.{min(unknown)}')
    sanitized: dict[str, Any] = {}
    if 'start_urls' in value:
        start_urls = value['start_urls']
        if not isinstance(start_urls, list) or len(start_urls) > 20:
            raise errors.RequestError(msg='采集配置 start_urls 必须是至多 20 项的列表')
        sanitized['start_urls'] = [
            _sanitize_collection_url(str(url), preserve_query=True)
            for url in start_urls
        ]
    if 'search_url_template' in value:
        sanitized['search_url_template'] = _sanitize_collection_url(
            _safe_config_text(
                value['search_url_template'],
                field='search_url_template',
                max_length=2048,
            ),
            preserve_query=True,
        )
    for field in ('detail_link_css', 'next_page_css'):
        if field in value:
            sanitized[field] = _safe_config_text(value[field], field=field, max_length=512)
    if 'detail_fields' in value:
        detail_fields = value['detail_fields']
        allowed_fields = {
            'address',
            'city',
            'company_name',
            'contact_name',
            'industry',
            'region',
            'website',
        }
        if not isinstance(detail_fields, dict) or set(detail_fields) - allowed_fields:
            raise errors.RequestError(msg='采集配置 detail_fields 包含未声明字段')
        sanitized['detail_fields'] = {
            field: _safe_config_text(selector, field=field, max_length=512)
            for field, selector in detail_fields.items()
        }
    return sanitized


def _sanitize_location_config(value: dict[str, Any]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for field in ('city', 'country_hint', 'province', 'region'):
        if field in value:
            sanitized[field] = _safe_config_text(value[field], field=field, max_length=100)
    return sanitized


def _sanitize_contact_field_config(value: dict[str, Any]) -> dict[str, list[str]]:
    sanitized: dict[str, list[str]] = {}
    for field in ('min_contact_fields', 'required_contact_fields'):
        if field not in value:
            continue
        fields = value[field]
        if not isinstance(fields, list) or not fields:
            raise errors.RequestError(msg=f'采集配置 {field} 必须是非空列表')
        normalized = list(dict.fromkeys(str(item).strip().casefold() for item in fields))
        if any(item not in {'email', 'phone'} for item in normalized):
            raise errors.RequestError(msg=f'采集配置 {field} 只允许 email/phone')
        sanitized[field] = normalized
    return sanitized


def _sanitize_raw_policy_config(value: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    if value.get('persist_raw_html'):
        raise errors.RequestError(msg='隐私模式禁止持久化采集原文')
    if 'persist_raw_html' in value:
        sanitized['persist_raw_html'] = False
    if 'max_html_bytes' not in value:
        return sanitized
    try:
        max_html_bytes = int(value['max_html_bytes'])
    except (TypeError, ValueError) as exc:
        raise errors.RequestError(msg='采集配置 max_html_bytes 必须是整数') from exc
    if not 1 <= max_html_bytes <= 5_242_880:
        raise errors.RequestError(msg='采集配置 max_html_bytes 超出范围')
    sanitized['max_html_bytes'] = max_html_bytes
    return sanitized


def _sanitize_collection_config(value: dict[str, Any]) -> dict[str, Any]:
    """按运行时实际契约白名单化采集配置，拒绝隐式透传和 PII 副本。"""
    allowed = {
        'city',
        'country_hint',
        'crawler_options',
        'firecrawl_options',
        'max_html_bytes',
        'min_contact_fields',
        'persist_raw_html',
        'province',
        'region',
        'required_contact_fields',
    }
    if unknown := set(value) - allowed:
        raise errors.RequestError(msg=f'采集配置包含未声明字段: {min(unknown)}')
    sanitized: dict[str, Any] = {
        **_sanitize_location_config(value),
        **_sanitize_contact_field_config(value),
        **_sanitize_raw_policy_config(value),
    }
    if 'firecrawl_options' in value:
        sanitized['firecrawl_options'] = _sanitize_firecrawl_options(value['firecrawl_options'])
    if 'crawler_options' in value:
        sanitized['crawler_options'] = _sanitize_crawler_options(value['crawler_options'])
    return sanitized


def _safe_public_text(value: str | None) -> str | None:
    if not value:
        return None
    redacted = redact_pii_value(value.strip())
    return str(redacted) if redacted else None


_COLLECTION_PII_KEYS = frozenset({
    'address',
    'contact_name',
    'email',
    'emails',
    'name',
    'phone',
    'phones',
    'wechat',
})


def _walk_collection_pii(
    value: Any,
    *,
    path: str,
    matches: list[tuple[str, str]],
    known_pii: bool = False,
) -> None:
    """递归扫描采集载荷，只在内存中收集 PII 字段路径和值。"""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).strip().casefold()
            _walk_collection_pii(
                nested,
                path=f'{path}.{normalized_key}',
                matches=matches,
                known_pii=known_pii or normalized_key in _COLLECTION_PII_KEYS,
            )
        return
    if isinstance(value, list | tuple | set):
        for nested in value:
            _walk_collection_pii(
                nested,
                path=path,
                matches=matches,
                known_pii=known_pii,
            )
        return
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    if known_pii:
        matches.append((path, text))
    elif redact_pii_value(text) != text:
        matches.append((f'{path}.free_text', text))


def _collection_pii(*, item: CrawledItem, cleaned: Any) -> tuple[list[str], list[str]]:
    """独立于格式准入分类 PII，避免无效邮箱/号码逃过隔离字段登记。"""
    matches = [
        (field, value.strip())
        for field, value in (
            ('contact_name', cleaned.contact_name),
            ('email', cleaned.email),
            ('phone', cleaned.phone),
            ('address', cleaned.address),
        )
        if value and value.strip()
    ]
    for path, payload in (
        ('raw_payload', item.raw_payload),
        ('structured_payload', item.structured_payload),
        ('metadata', item.metadata),
    ):
        _walk_collection_pii(payload, path=path, matches=matches)
    for field, text_value in (
        ('title', item.title),
        ('markdown', item.markdown),
        ('raw_text', item.raw_text),
        ('raw_html', item.raw_html),
    ):
        _walk_collection_pii(text_value, path=field, matches=matches)
    fields = sorted({field for field, _value in matches})
    values = list(dict.fromkeys(value for _field, value in matches))
    return fields, values


def _hmac_fingerprint(
    keyring: GrowthPiiKeyring,
    *,
    namespace: str,
    values: list[Any],
) -> str:
    """对任意采集输入生成版本化 HMAC，不持久化无盐摘要。"""
    serialized = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return keyring.hmac_for(namespace, serialized)


def _safe_source_url(
    value: str | None,
    *,
    sensitive_values: list[str],
) -> str | None:
    """移除 URL 凭据、查询和片段；路径命中已知 PII 时只保留站点 origin。"""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return None
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        return None
    path = parsed.path or '/'
    decoded_path = unquote(path).casefold()
    if any(
        sensitive.casefold() in decoded_path
        for sensitive in sensitive_values
        if sensitive.strip()
    ):
        path = '/'
    return f'{parsed.scheme}://{parsed.hostname.casefold()}{port}{path}'[:2048]


def _safe_collection_metadata(
    keyring: GrowthPiiKeyring,
    *,
    item: CrawledItem,
    pii_fields: list[str],
    pii_values: list[str],
) -> dict[str, Any]:
    """采集记录只保存受控枚举、字段名和带密钥指纹。"""
    return {
        'llm_schema_version': _safe_token(
            item.metadata.get('llm_schema_version'),
            max_length=64,
        ),
        'llm_prompt_version': _safe_token(
            item.metadata.get('llm_prompt_version'),
            max_length=64,
        ),
        'attempt_count': _safe_positive_int(
            item.metadata.get('attempt_count'),
            default=1,
        ),
        'extract_mode': _safe_token(item.extract_mode, max_length=32),
        'pii_fields': sorted(set(pii_fields)),
        'pii_fingerprint': (
            f'v{keyring.active_hmac_version}:'
            f'{_hmac_fingerprint(keyring, namespace="collection_pii", values=pii_values)}'
            if pii_values
            else None
        ),
        'source_fingerprint': (
            f'v{keyring.active_hmac_version}:'
            f'{_hmac_fingerprint(keyring, namespace="collection_source", values=[item.source_url])}'
        ),
        'raw_content_discarded': True,
    }


def _safe_request_payload(
    keyring: GrowthPiiKeyring,
    *,
    keyword: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """请求审计不复制查询文本，只保留带密钥指纹和受控配置键。"""
    allowed_config_keys = {
        'country_hint',
        'firecrawl_options',
        'max_html_bytes',
        'min_contact_fields',
        'persist_raw_html',
        'required_contact_fields',
    }
    return {
        'keyword_fingerprint': (
            f'v{keyring.active_hmac_version}:'
            f'{_hmac_fingerprint(keyring, namespace="collection_keyword", values=[keyword])}'
        ),
        'config_keys': sorted(set(config) & allowed_config_keys),
    }


def _contact_field_requirements(config: dict[str, Any]) -> list[str]:
    fields = config.get('required_contact_fields') or config.get('min_contact_fields') or ['email', 'phone']
    return [str(field) for field in _as_list(fields) if str(field) in {'email', 'phone'}] or ['email', 'phone']


def _mask_email(value: str | None) -> str | None:
    if not value or '@' not in value:
        return value
    local, domain = value.split('@', 1)
    return f'{local[:1]}***@{domain}'


def _mask_phone(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return value
    return f'{value[:4]}****{value[-4:]}'
