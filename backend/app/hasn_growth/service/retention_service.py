from __future__ import annotations

from datetime import datetime
from typing import Any


def archive_expired_contacts(contacts: list[dict[str, Any]], *, now: datetime) -> int:
    archived = 0
    for contact in contacts:
        archived_at = contact.get('archived_at')
        if archived_at is None or archived_at > now:
            continue
        if contact.get('status') in {'contacted', 'exported'}:
            continue
        contact['status'] = 'archived'
        contact['email'] = None
        contact['phone'] = None
        contact['email_normalized'] = None
        contact['phone_normalized'] = None
        archived += 1
    return archived
