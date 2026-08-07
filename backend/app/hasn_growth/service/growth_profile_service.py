"""获客项目画像版本、Knowledge 绑定与服务端 readiness 事实计算。"""

from __future__ import annotations

import hashlib
import json
import operator

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import identity
from backend.app.hasn_growth.model.growth_profile_suggestion import GrowthProfileSuggestion
from backend.app.hasn_growth.model.growth_profile_version import GrowthProfileVersion
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_knowledge.model.document import Document
from backend.app.hasn_knowledge.model.document_version import DocumentVersion
from backend.app.hasn_knowledge.model.kb import Kb
from backend.common.exception import errors

_KB_URI_PREFIX = 'hasn://knowledge/kbs/'
_PROFILE_DECISIONS = frozenset({'accept', 'reject'})


def _parse_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise errors.NotFoundError(msg='获客项目不存在') from exc


def _parse_kb_id(kb_ref: str | None) -> int | None:
    if not kb_ref or not kb_ref.startswith(_KB_URI_PREFIX):
        return None
    raw_id = kb_ref.removeprefix(_KB_URI_PREFIX)
    try:
        kb_id = int(raw_id)
    except ValueError:
        return None
    return kb_id if kb_id > 0 else None


def _canonical_hash(document_versions: list[dict[str, int]]) -> str:
    canonical = json.dumps(
        document_versions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_items(value: Any) -> bool:
    return isinstance(value, list) and any((_has_text(item) if isinstance(item, str) else bool(item)) for item in value)


def _product_complete(profile: dict[str, Any]) -> bool:
    return _has_text(profile.get('offering')) and _has_items(profile.get('value_propositions'))


def _icp_complete(profile: dict[str, Any]) -> bool:
    return all(_has_items(profile.get(field)) for field in ('industries', 'buyer_roles', 'pain_points', 'exclusions'))


def _suggestion_to_dict(row: GrowthProfileSuggestion) -> dict[str, Any]:
    return {
        'id': row.id,
        'growth_project_id': str(row.growth_project_id),
        'expected_version': row.expected_version,
        'product_profile': row.product_profile,
        'icp_profile': row.icp_profile,
        'knowledge_document_versions': row.knowledge_document_versions,
        'source_hash': row.source_hash,
        'proposed_by_kind': row.proposed_by_kind,
        'proposed_by_id': row.proposed_by_id,
        'status': row.status,
        'created_time': row.created_time.isoformat(),
        'updated_time': row.updated_time.isoformat() if row.updated_time is not None else None,
    }


class GrowthProfileService:
    """维护已确认画像与待确认建议，所有权威门禁都在服务端执行。"""

    @staticmethod
    async def _owned_growth(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        for_update: bool,
    ) -> GrowthProject:
        statement = sa.select(GrowthProject).where(
            GrowthProject.id == _parse_uuid(growth_project_id),
            GrowthProject.owner_hasn_id == owner_hasn_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await db.execute(statement)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='获客项目不存在')
        return row

    @staticmethod
    async def _owned_project_kb(
        db: AsyncSession,
        *,
        growth: GrowthProject,
        owner_hasn_id: str,
        kb_id: int,
    ) -> Kb:
        """跨 Owner、跨项目和软删除统一收敛为 404，避免泄露资源存在性。"""
        kb = (
            await db.execute(
                sa.select(Kb).where(
                    Kb.id == kb_id,
                    Kb.owner_id == owner_hasn_id,
                    Kb.platform_project_id == growth.platform_project_id,
                    Kb.status == 'active',
                    Kb.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            raise errors.NotFoundError(msg='同项目知识库不存在')
        return kb

    @staticmethod
    async def _document_versions(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        kb_id: int,
        document_ids: list[int],
    ) -> list[dict[str, int]]:
        normalized_ids = sorted(set(document_ids))
        if not normalized_ids:
            raise errors.RequestError(msg='knowledge_document_ids 不能为空')
        documents = (
            (
                await db.execute(
                    sa.select(Document).where(
                        Document.id.in_(normalized_ids),
                        Document.kb_id == kb_id,
                        Document.owner_id == owner_hasn_id,
                        Document.deleted_time.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(documents) != len(normalized_ids):
            raise errors.NotFoundError(msg='画像来源文档不存在')
        versions = sorted(
            (
                {'document_id': document.id, 'version': document.current_version}
                for document in documents
                if document.current_version > 0
            ),
            key=operator.itemgetter('document_id'),
        )
        if len(versions) != len(normalized_ids):
            raise errors.ConflictError(
                msg='画像来源文档尚无可追溯版本',
                data={'error_code': 'KNOWLEDGE_DOCUMENT_VERSION_MISSING'},
            )
        existing_version_count = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(DocumentVersion)
            .where(
                sa.tuple_(
                    DocumentVersion.document_id,
                    DocumentVersion.version_no,
                ).in_([(item['document_id'], item['version']) for item in versions])
            )
        )
        if existing_version_count != len(versions):
            raise errors.ConflictError(
                msg='画像来源文档版本历史不完整',
                data={'error_code': 'KNOWLEDGE_DOCUMENT_VERSION_MISSING'},
            )
        return versions

    async def bind_knowledge(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
        kb_id: int,
        expected_profile_version: int,
    ) -> dict[str, Any]:
        """幂等绑定或显式改绑同项目知识库，不移动或删除任何 Knowledge 原件。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if growth.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，不能改绑知识库',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if growth.profile_version != expected_profile_version:
            raise errors.ConflictError(
                msg='画像版本已变化，请刷新后重试',
                data={
                    'error_code': 'PROFILE_VERSION_CONFLICT',
                    'current_version': growth.profile_version,
                },
            )
        await self._owned_project_kb(
            db,
            growth=growth,
            owner_hasn_id=owner_hasn_id,
            kb_id=kb_id,
        )
        next_ref = f'{_KB_URI_PREFIX}{kb_id}'
        changed = growth.kb_ref != next_ref
        growth.kb_ref = next_ref
        if changed:
            growth.readiness_snapshot = {
                'ready': False,
                'profile_sync_status': 'stale',
                'blocking_reasons': ['profile_stale'],
            }
        await db.flush()
        return {
            'growth_project_id': str(growth.id),
            'kb_ref': growth.kb_ref,
            'changed': changed,
            'profile_sync_status': ('stale' if changed and growth.profile_source_hash is not None else 'missing'),
        }

    async def reconcile_knowledge_binding(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """修复“库已创建但 Growth 未回填引用”，候选严格限制为同 Owner、同项目幂等库。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        current_kb_id = _parse_kb_id(growth.kb_ref)
        if current_kb_id is not None:
            await self._owned_project_kb(
                db,
                growth=growth,
                owner_hasn_id=owner_hasn_id,
                kb_id=current_kb_id,
            )
            return {
                'growth_project_id': str(growth.id),
                'kb_ref': growth.kb_ref,
                'repaired': False,
            }
        kb = (
            await db.execute(
                sa.select(Kb).where(
                    Kb.owner_id == owner_hasn_id,
                    Kb.platform_project_id == growth.platform_project_id,
                    Kb.client_request_id == f'growth:{growth.id}:knowledge',
                    Kb.status == 'active',
                    Kb.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            return {
                'growth_project_id': str(growth.id),
                'kb_ref': None,
                'repaired': False,
            }
        growth.kb_ref = f'{_KB_URI_PREFIX}{kb.id}'
        growth.readiness_snapshot = {
            'ready': False,
            'profile_sync_status': ('stale' if growth.profile_source_hash is not None else 'missing'),
            'blocking_reasons': (['profile_stale'] if growth.profile_source_hash is not None else []),
        }
        await db.flush()
        return {
            'growth_project_id': str(growth.id),
            'kb_ref': growth.kb_ref,
            'repaired': True,
        }

    async def submit_suggestion(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        growth_project_id: str | UUID,
        expected_version: int,
        product_profile: dict[str, Any],
        icp_profile: dict[str, Any],
        knowledge_document_ids: list[int],
        trace_id: str | UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """分身仅提交待确认建议；当前画像和 readiness 不在本方法中改写。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        if growth.status == 'archived':
            raise errors.ConflictError(
                msg='获客项目已归档，不能提交画像建议',
                data={'error_code': 'GROWTH_PROJECT_ARCHIVED'},
            )
        if growth.profile_version != expected_version:
            raise errors.ConflictError(
                msg='画像版本已变化，请基于当前版本重新生成建议',
                data={
                    'error_code': 'PROFILE_VERSION_CONFLICT',
                    'current_version': growth.profile_version,
                },
            )
        agent = await identity.agent_owned_by(
            db, hasn_id=agent_hasn_id, owner_hasn_id=owner_hasn_id, require_active=True
        )
        if agent is None:
            raise errors.NotFoundError(msg='负责分身不存在或不可用')
        kb_id = _parse_kb_id(growth.kb_ref)
        if kb_id is None:
            raise errors.ConflictError(
                msg='获客项目尚未绑定知识库',
                data={'error_code': 'KNOWLEDGE_MISSING'},
            )
        await self._owned_project_kb(
            db,
            growth=growth,
            owner_hasn_id=owner_hasn_id,
            kb_id=kb_id,
        )
        versions = await self._document_versions(
            db,
            owner_hasn_id=owner_hasn_id,
            kb_id=kb_id,
            document_ids=knowledge_document_ids,
        )
        source_hash = _canonical_hash(versions)
        try:
            canonical_trace_id = UUID(str(trace_id))
        except (TypeError, ValueError) as exc:
            raise errors.RequestError(msg='trace_id 必须是有效 UUID') from exc
        normalized_key = idempotency_key.strip()
        if not normalized_key:
            raise errors.RequestError(msg='idempotency_key 不能为空')
        if len(normalized_key) > 200:
            raise errors.RequestError(msg='idempotency_key 最长 200 个字符')
        existing = (
            await db.execute(
                sa.select(GrowthProfileSuggestion).where(
                    GrowthProfileSuggestion.growth_project_id == growth.id,
                    sa.or_(
                        GrowthProfileSuggestion.trace_id == canonical_trace_id,
                        GrowthProfileSuggestion.idempotency_key == normalized_key,
                    ),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            same_intent = (
                existing.trace_id == canonical_trace_id
                and existing.idempotency_key == normalized_key
                and existing.expected_version == expected_version
                and existing.product_profile == product_profile
                and existing.icp_profile == icp_profile
                and existing.knowledge_document_versions == versions
            )
            if not same_intent:
                raise errors.ConflictError(
                    msg='trace_id 或 idempotency_key 已用于其他画像建议',
                    data={'error_code': 'PROFILE_SUGGESTION_IDEMPOTENCY_CONFLICT'},
                )
            return _suggestion_to_dict(existing)
        suggestion = GrowthProfileSuggestion(
            growth_project_id=growth.id,
            expected_version=expected_version,
            product_profile=product_profile,
            icp_profile=icp_profile,
            knowledge_document_versions=versions,
            source_hash=source_hash,
            proposed_by_kind='agent',
            proposed_by_id=agent_hasn_id,
            trace_id=canonical_trace_id,
            idempotency_key=normalized_key,
            status='pending',
        )
        db.add(suggestion)
        await db.flush()
        return _suggestion_to_dict(suggestion)

    async def review_suggestion(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        owner_user_id: int,
        growth_project_id: str | UUID,
        suggestion_id: int,
        decision: str,
    ) -> dict[str, Any]:
        """主人接受或拒绝建议；接受才生成不可变画像版本。"""
        if decision not in _PROFILE_DECISIONS:
            raise errors.RequestError(msg='decision 只允许 accept 或 reject')
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        suggestion = (
            await db.execute(
                sa
                .select(GrowthProfileSuggestion)
                .where(
                    GrowthProfileSuggestion.id == suggestion_id,
                    GrowthProfileSuggestion.growth_project_id == growth.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if suggestion is None:
            raise errors.NotFoundError(msg='画像建议不存在')
        if suggestion.status == 'rejected' and decision == 'reject':
            return _suggestion_to_dict(suggestion)
        if suggestion.status == 'accepted' and decision == 'accept':
            return await self.project_summary(
                db,
                owner_hasn_id=owner_hasn_id,
                growth_project_id=growth.id,
            )
        if suggestion.status != 'pending':
            raise errors.ConflictError(
                msg='画像建议已处理',
                data={'error_code': 'PROFILE_SUGGESTION_ALREADY_REVIEWED'},
            )
        now = datetime.now(UTC)
        if decision == 'reject':
            suggestion.status = 'rejected'
            suggestion.reviewed_by_owner_id = str(owner_user_id)
            suggestion.reviewed_time = now
            await db.flush()
            return _suggestion_to_dict(suggestion)
        if suggestion.expected_version != growth.profile_version:
            suggestion.status = 'stale'
            suggestion.reviewed_by_owner_id = str(owner_user_id)
            suggestion.reviewed_time = now
            await db.flush()
            raise errors.ConflictError(
                msg='画像建议基于旧版本，请重新生成',
                data={
                    'error_code': 'PROFILE_SUGGESTION_STALE',
                    'current_version': growth.profile_version,
                },
            )
        next_version = growth.profile_version + 1
        version = GrowthProfileVersion(
            growth_project_id=growth.id,
            version=next_version,
            product_profile=suggestion.product_profile,
            icp_profile=suggestion.icp_profile,
            knowledge_document_versions=suggestion.knowledge_document_versions,
            source_hash=suggestion.source_hash,
            confirmed_by_kind='owner',
            confirmed_by_id=str(owner_user_id),
        )
        db.add(version)
        growth.product_profile = suggestion.product_profile
        growth.icp_profile = suggestion.icp_profile
        growth.profile_version = next_version
        growth.profile_source_hash = suggestion.source_hash
        growth.profile_updated_time = now
        suggestion.status = 'accepted'
        suggestion.reviewed_by_owner_id = str(owner_user_id)
        suggestion.reviewed_time = now
        await db.flush()
        readiness = await self.compute_readiness(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth.id,
        )
        growth.readiness_snapshot = readiness
        await db.flush()
        return {
            'growth_project_id': str(growth.id),
            'profile_version': growth.profile_version,
            'profile_source_hash': growth.profile_source_hash,
            'readiness': readiness,
        }

    async def compute_readiness(  # ruff: ignore[complex-structure]
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        """每次从 Growth、Knowledge 文档版本和 Agent 状态重算，不信任缓存快照。"""
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        blocking: list[str] = []
        kb_id = _parse_kb_id(growth.kb_ref)
        kb: Kb | None = None
        if kb_id is None:
            blocking.append('knowledge_missing')
        else:
            kb = (
                await db.execute(
                    sa.select(Kb).where(
                        Kb.id == kb_id,
                        Kb.owner_id == owner_hasn_id,
                        Kb.status == 'active',
                        Kb.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if kb is None:
                blocking.append('knowledge_missing')
            elif kb.platform_project_id != growth.platform_project_id:
                blocking.append('project_mismatch')
        product_complete = _product_complete(growth.product_profile)
        if not product_complete:
            blocking.append('product_incomplete')
        icp_complete = _icp_complete(growth.icp_profile)
        if not icp_complete:
            blocking.append('icp_incomplete')
        agent = None
        if growth.owner_agent_id:
            agent = await identity.agent_owned_by(
                db, hasn_id=growth.owner_agent_id, owner_hasn_id=owner_hasn_id, require_active=True
            )
        if agent is None:
            blocking.append('agent_missing')

        profile_version = (
            await db.execute(
                sa.select(GrowthProfileVersion).where(
                    GrowthProfileVersion.growth_project_id == growth.id,
                    GrowthProfileVersion.version == growth.profile_version,
                )
            )
        ).scalar_one_or_none()
        profile_sync_status = 'missing'
        source_versions: list[dict[str, int]] = []
        if profile_version is not None and kb is not None:
            source_versions = profile_version.knowledge_document_versions
            document_ids = [item['document_id'] for item in source_versions]
            try:
                current_versions = await self._document_versions(
                    db,
                    owner_hasn_id=owner_hasn_id,
                    kb_id=kb.id,
                    document_ids=document_ids,
                )
            except (errors.NotFoundError, errors.ConflictError):
                current_versions = []
            current_hash = _canonical_hash(current_versions) if current_versions else ''
            if current_hash == profile_version.source_hash and current_hash == growth.profile_source_hash:
                profile_sync_status = 'synced'
            else:
                profile_sync_status = 'stale'
                blocking.append('profile_stale')
        elif growth.profile_source_hash is not None or product_complete or icp_complete:
            profile_sync_status = 'stale'
            blocking.append('profile_stale')

        return {
            'ready': not blocking,
            'blocking_reasons': list(dict.fromkeys(blocking)),
            'profile_sync_status': profile_sync_status,
            'profile_version': growth.profile_version,
            'profile_source_hash': growth.profile_source_hash,
            'knowledge_document_versions': source_versions,
            'checks': {
                'knowledge_bound': kb is not None and kb.platform_project_id == growth.platform_project_id,
                'product_complete': product_complete,
                'icp_complete': icp_complete,
                'profile_current': profile_sync_status == 'synced',
                'agent_available': agent is not None,
            },
            'knowledge': (
                {
                    'id': kb.id,
                    'name': kb.name,
                    'document_count': kb.document_count,
                    'uri': f'{_KB_URI_PREFIX}{kb.id}',
                    'platform_project_id': str(kb.platform_project_id),
                }
                if kb is not None
                else None
            ),
            'owner_agent': (
                {
                    'hasn_id': agent.hasn_id,
                    'display_name': agent.display_name,
                    'profession': agent.profession,
                    # agent 非 None 时已由 identity.agent_owned_by(require_active=True) 保证
                    # status == 'active'（AgentRef 不携带该字段，此处按查询语义直接给字面量）。
                    'status': 'active',
                }
                if agent is not None
                else None
            ),
        }

    async def list_suggestions(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> list[dict[str, Any]]:
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        rows = (
            (
                await db.execute(
                    sa
                    .select(GrowthProfileSuggestion)
                    .where(GrowthProfileSuggestion.growth_project_id == growth.id)
                    .order_by(
                        GrowthProfileSuggestion.created_time.desc(),
                        GrowthProfileSuggestion.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_suggestion_to_dict(row) for row in rows]

    async def project_summary(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> dict[str, Any]:
        growth = await self._owned_growth(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth_project_id,
            for_update=False,
        )
        readiness = await self.compute_readiness(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth.id,
        )
        suggestions = await self.list_suggestions(
            db,
            owner_hasn_id=owner_hasn_id,
            growth_project_id=growth.id,
        )
        versions = (
            (
                await db.execute(
                    sa
                    .select(GrowthProfileVersion)
                    .where(GrowthProfileVersion.growth_project_id == growth.id)
                    .order_by(GrowthProfileVersion.version.desc())
                )
            )
            .scalars()
            .all()
        )
        return {
            'growth_project_id': str(growth.id),
            'profile_version': growth.profile_version,
            'product_profile': growth.product_profile,
            'icp_profile': growth.icp_profile,
            'profile_updated_time': (
                growth.profile_updated_time.isoformat() if growth.profile_updated_time is not None else None
            ),
            'readiness': readiness,
            'pending_suggestions': [suggestion for suggestion in suggestions if suggestion['status'] == 'pending'],
            'profile_versions': [
                {
                    'id': version.id,
                    'version': version.version,
                    'source_hash': version.source_hash,
                    'confirmed_by_kind': version.confirmed_by_kind,
                    'confirmed_by_id': version.confirmed_by_id,
                    'knowledge_document_versions': version.knowledge_document_versions,
                    'created_time': version.created_time.isoformat(),
                }
                for version in versions
            ],
        }


growth_profile_service = GrowthProfileService()
