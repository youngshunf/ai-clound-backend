from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


NORMALIZATION_VERSION = 'v1'
EMAIL_RE = re.compile(r'(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])')


@dataclass(slots=True)
class CleanedLead:
    accepted: bool
    rejected_reason: str | None
    company_name: str | None = None
    contact_name: str | None = None
    email: str | None = None
    email_normalized: str | None = None
    phone: str | None = None
    phone_normalized: str | None = None
    website: str | None = None
    domain: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    address: str | None = None
    industry: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    keyword: str | None = None
    llm_confidence: float | None = None
    extract_mode: str | None = None
    normalization_version: str = NORMALIZATION_VERSION
    system_score: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    email = unicodedata.normalize('NFKC', value).strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return None
    local, domain = email.rsplit('@', 1)
    if domain == 'example.com' or local.startswith('test'):
        return None
    if domain in {'gmail.com', 'googlemail.com'}:
        local = local.split('+', 1)[0].replace('.', '')
        domain = 'gmail.com'
    elif domain in {'outlook.com', 'hotmail.com', 'live.com'}:
        local = local.split('+', 1)[0]
    if not local:
        return None
    return f'{local}@{domain}'


def normalize_phone(value: str | None, *, country_hint: str = 'CN') -> str | None:
    if not value:
        return None
    raw = unicodedata.normalize('NFKC', value).strip()
    digits = re.sub(r'\D+', '', raw)
    if raw.startswith('+'):
        return f'+{digits}' if 8 <= len(digits) <= 15 else None
    country = (country_hint or 'CN').upper()
    if country == 'CN' and re.fullmatch(r'1\d{10}', digits):
        return f'+86{digits}'
    if country == 'US' and len(digits) == 10:
        return f'+1{digits}'
    if country in {'GB', 'UK'} and digits.startswith('0') and 10 <= len(digits) <= 11:
        return f'+44{digits[1:]}'
    if 8 <= len(digits) <= 15 and digits.startswith(('86', '1', '44')):
        return f'+{digits}'
    return None


def clean_raw_record(
    raw_record: dict[str, Any],
    *,
    min_contact_fields: list[str] | None = None,
    country_hint: str = 'CN',
) -> CleanedLead:
    min_fields = min_contact_fields or ['email', 'phone']
    structured = raw_record.get('structured_payload') or {}
    markdown = raw_record.get('markdown') or ''
    raw_text = raw_record.get('raw_text') or ''
    text = f'{markdown}\n{raw_text}'

    email_candidates = _as_list(structured.get('emails') or structured.get('email'))
    if not email_candidates:
        email_candidates = EMAIL_RE.findall(text)
    selected_email, email_normalized = _select_email(email_candidates)

    phone_candidates = _as_list(structured.get('phones') or structured.get('phone'))
    if not phone_candidates:
        phone_candidates = _extract_phone_candidates(text)
    selected_phone, phone_normalized = _select_phone(phone_candidates, country_hint=country_hint)

    website = _first_text(structured.get('website') or raw_record.get('website'))
    domain = _domain_from(website or raw_record.get('source_url'))
    metadata = dict(raw_record.get('metadata') or {})
    metadata.update(
        {
            'email_candidates': email_candidates,
            'phone_candidates': phone_candidates,
            'phone_invalid_candidates': [
                candidate for candidate in phone_candidates if normalize_phone(candidate, country_hint=country_hint) is None
            ],
            'structured_payload_present': bool(structured),
        }
    )

    rejected_reason = _admission_rejection(
        min_fields=min_fields,
        email_normalized=email_normalized,
        phone_normalized=phone_normalized,
    )
    accepted = rejected_reason is None
    cleaned = CleanedLead(
        accepted=accepted,
        rejected_reason=rejected_reason,
        company_name=_first_text(structured.get('company_name') or structured.get('company')),
        contact_name=_first_text(structured.get('contact_name')),
        email=selected_email,
        email_normalized=email_normalized,
        phone=selected_phone,
        phone_normalized=phone_normalized,
        website=website,
        domain=domain,
        country=_first_text(structured.get('country')),
        region=_first_text(structured.get('region')),
        city=_first_text(structured.get('city')),
        address=_first_text(structured.get('address')),
        industry=_first_text(structured.get('industry')),
        source_type=raw_record.get('source_type'),
        source_url=raw_record.get('source_url'),
        keyword=raw_record.get('keyword'),
        llm_confidence=raw_record.get('llm_confidence'),
        extract_mode=raw_record.get('extract_mode'),
        metadata=metadata,
    )
    from backend.app.hasn_growth.service.scoring_service import score_cleaned_lead

    cleaned.system_score = score_cleaned_lead(cleaned)
    return cleaned


def _select_email(candidates: list[str]) -> tuple[str | None, str | None]:
    for candidate in candidates:
        normalized = normalize_email(candidate)
        if normalized:
            return candidate.strip(), normalized
    return None, None


def _select_phone(candidates: list[str], *, country_hint: str) -> tuple[str | None, str | None]:
    for candidate in candidates:
        normalized = normalize_phone(candidate, country_hint=country_hint)
        if normalized:
            return candidate.strip(), normalized
    return None, None


def _extract_phone_candidates(text: str) -> list[str]:
    pattern = re.compile(r'(?:(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,5}\d{3,4})')
    return [match.group(0).strip() for match in pattern.finditer(text or '')]


def _admission_rejection(*, min_fields: list[str], email_normalized: str | None, phone_normalized: str | None) -> str | None:
    allowed = set(min_fields or ['email', 'phone'])
    if allowed == {'email', 'phone'}:
        if email_normalized or phone_normalized:
            return None
        return 'missing_both'
    if allowed == {'email'}:
        if email_normalized:
            return None
        return 'missing_email'
    if allowed == {'phone'}:
        if phone_normalized:
            return None
        return 'missing_phone'
    if 'email' in allowed and email_normalized:
        return None
    if 'phone' in allowed and phone_normalized:
        return None
    return 'missing_contact'


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if item]
    return [str(value)]


def _first_text(value: Any) -> str | None:
    values = _as_list(value)
    return values[0].strip() if values and values[0].strip() else None


def _domain_from(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    return parsed.netloc.lower().removeprefix('www.') or None
