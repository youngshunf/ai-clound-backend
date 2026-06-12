from __future__ import annotations

import hashlib

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.app.hasn_growth.service.cleaner_service import CleanedLead


@dataclass(slots=True)
class DedupeResult:
    contact: dict[str, Any]
    created: bool
    match_dimension: str


@dataclass(slots=True)
class InMemoryLeadStore:
    contacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


def dedupe_key(value: str | None, *, lead_scope: str, user_id: int | None) -> str | None:
    if not value:
        return None
    scope_user = user_id or 0
    raw = f'{value}|{lead_scope}|{scope_user}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def upsert_lead(
    store: InMemoryLeadStore,
    cleaned: CleanedLead,
    *,
    lead_scope: str,
    user_id: int | None,
    keyword: str | None,
) -> DedupeResult:
    keys = {
        'email': dedupe_key(cleaned.email_normalized, lead_scope=lead_scope, user_id=user_id),
        'phone': dedupe_key(cleaned.phone_normalized, lead_scope=lead_scope, user_id=user_id),
        'domain': dedupe_key(cleaned.domain, lead_scope=lead_scope, user_id=user_id),
    }
    for dimension in ('email', 'phone', 'domain'):
        key = keys[dimension]
        if key is None:
            continue
        existing = _find_by_key(store.contacts, f'dedupe_key_{dimension}', key)
        if existing is not None:
            existing['last_seen_at'] = datetime.now(UTC)
            _append_source(store, existing, cleaned, dimension)
            return DedupeResult(contact=existing, created=False, match_dimension=dimension)

    contact = {
        'id': len(store.contacts) + 1,
        'lead_no': f'LEAD{len(store.contacts) + 1:08d}',
        'lead_scope': lead_scope,
        'user_id': user_id,
        'company_name': cleaned.company_name,
        'contact_name': cleaned.contact_name,
        'email': cleaned.email,
        'email_normalized': cleaned.email_normalized,
        'phone': cleaned.phone,
        'phone_normalized': cleaned.phone_normalized,
        'website': cleaned.website,
        'domain': cleaned.domain,
        'source_type': cleaned.source_type,
        'source_url': cleaned.source_url,
        'keyword': keyword,
        'status': 'new',
        'confidence_score': cleaned.system_score,
        'dedupe_key_email': keys['email'],
        'dedupe_key_phone': keys['phone'],
        'dedupe_key_domain': keys['domain'],
        'normalization_version': cleaned.normalization_version,
        'first_seen_at': datetime.now(UTC),
        'last_seen_at': datetime.now(UTC),
        'metadata': cleaned.metadata,
    }
    store.contacts.append(contact)
    _append_source(store, contact, cleaned, 'new')
    return DedupeResult(contact=contact, created=True, match_dimension='new')


def _find_by_key(contacts: list[dict[str, Any]], field: str, key: str) -> dict[str, Any] | None:
    return next((contact for contact in contacts if contact.get(field) == key), None)


def _append_source(store: InMemoryLeadStore, contact: dict[str, Any], cleaned: CleanedLead, match_dimension: str) -> None:
    store.sources.append(
        {
            'lead_contact_id': contact['id'],
            'source_type': cleaned.source_type,
            'source_url': cleaned.source_url,
            'match_dimension': match_dimension,
            'seen_at': datetime.now(UTC),
            'metadata': cleaned.metadata,
        }
    )
