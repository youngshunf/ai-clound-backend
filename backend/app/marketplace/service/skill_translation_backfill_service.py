"""Backfill bilingual translation, bilingual tags, and emoji for existing skills.

The sync services translate inline, but rows synced while the LLM gateway was
unavailable (or before emoji/bilingual support existed) end up with one language
side missing, fallback-garbage tags, and no emoji. This service re-runs the
batched translator over existing ``marketplace_skill`` rows and persists the
canonical bilingual fields.
"""
import json

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.service.translation_service import translation_service
from backend.common.log import log
from backend.utils.timezone import timezone


async def backfill_skill_translations(
    db: AsyncSession,
    *,
    only_missing: bool = False,
    skill_ids: list[str] | None = None,
    limit: int | None = None,
    batch_size: int = 10,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Re-translate marketplace skills and persist bilingual fields + emoji.

    Args:
        db: Database session.
        only_missing: Only rows missing a translated side / emoji.
        skill_ids: Restrict to these ``skill_id`` values.
        limit: Cap number of rows processed.
        batch_size: Skills per LLM request (default 10).
        concurrency: Parallel batch requests against the gateway.

    Returns:
        Summary dict: total / updated / failed / errors.
    """
    stmt = select(MarketplaceSkill)
    if skill_ids:
        stmt = stmt.where(MarketplaceSkill.skill_id.in_(skill_ids))
    if only_missing:
        stmt = stmt.where(
            or_(
                MarketplaceSkill.name_en.is_(None),
                MarketplaceSkill.name_zh.is_(None),
                MarketplaceSkill.description_en.is_(None),
                MarketplaceSkill.description_zh.is_(None),
                MarketplaceSkill.emoji.is_(None),
            )
        )
    stmt = stmt.order_by(MarketplaceSkill.id)
    if limit:
        stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return {'success': True, 'total': 0, 'updated': 0, 'failed': 0, 'message': 'no rows to translate'}

    # The original text lives in whichever description side matches source_language;
    # the stored tags are often fallback garbage, so we let the LLM regenerate them.
    items = [
        {
            'name': row.name,
            'description': row.description_zh or row.description_en or '',
            'tag_hints': None,
            'source_lang': row.source_language,
        }
        for row in rows
    ]

    log.info(
        f"Backfilling translations for {len(rows)} skills "
        f"(batch_size={batch_size}, concurrency={concurrency})"
    )
    translations = await translation_service.batch_translate_skill_metadata(
        items, batch_size=batch_size, concurrency=concurrency
    )

    now = timezone.now()
    updated = 0
    failed = 0
    errors: list[str] = []
    for row, translated in zip(rows, translations):
        try:
            tags_en = translation_service.normalize_tag_list(translated.get('tags_en'))
            tags_zh = translation_service.normalize_tag_list(translated.get('tags_zh'))
            tags = tags_en or tags_zh
            update_fields = {
                'name_en': translated.get('name_en'),
                'name_zh': translated.get('name_zh'),
                'description_en': translated.get('description_en'),
                'description_zh': translated.get('description_zh'),
                'source_language': translated.get('source_language') or row.source_language,
                'tags': json.dumps(tags, ensure_ascii=False),
                'tags_en': json.dumps(tags_en or tags, ensure_ascii=False),
                'tags_zh': json.dumps(tags_zh or tags, ensure_ascii=False),
                'emoji': translated.get('emoji'),
                'translated_at': now,
            }
            await marketplace_skill_dao.update_model(db, row.id, update_fields)
            updated += 1
        except Exception as exc:  # noqa: PERF203
            failed += 1
            errors.append(f"{row.skill_id}: {exc}")
            log.error(f"Failed to persist translation for {row.skill_id}: {exc}")

    await db.commit()
    return {
        'success': failed == 0,
        'total': len(rows),
        'updated': updated,
        'failed': failed,
        'errors': errors[:20],
    }
