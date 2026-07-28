from __future__ import annotations

import csv
import hashlib
import io

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.hasn_growth.service.audit_service import log_event
from backend.app.hasn_growth.service.pii import mask_contact_fields


@dataclass(slots=True)
class ExportResult:
    batch: dict[str, Any]
    items: list[dict[str, Any]]
    audit_log: dict[str, Any]
    csv_text: str


def build_csv_export(
    contacts: list[dict[str, Any]],
    *,
    batch_no: str,
    user_id: int,
    filter_payload: dict[str, Any],
    now: datetime,
) -> ExportResult:
    safe_contacts = [mask_contact_fields(contact, reveal=False) for contact in contacts]
    output = io.StringIO()
    fieldnames = [
        'lead_no',
        'company_name',
        'contact_name',
        'email',
        'phone',
        'website',
        'domain',
        'source_type',
        'keyword',
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for contact in safe_contacts:
        writer.writerow({field: contact.get(field) for field in fieldnames})
    csv_text = output.getvalue()
    file_sha256 = hashlib.sha256(csv_text.encode('utf-8')).hexdigest()
    batch = {
        'batch_no': batch_no,
        'user_id': user_id,
        'filter_payload': filter_payload,
        'format': 'csv',
        'total_count': len(safe_contacts),
        'file_sha256': file_sha256,
        'status': 'succeeded',
        'started_at': now,
        'finished_at': now,
    }
    items = [
        {
            'batch_no': batch_no,
            'lead_contact_id': contact.get('id'),
            'lead_no': contact.get('lead_no'),
            'snapshot': dict(contact),
        }
        for contact in safe_contacts
    ]
    audit_log = log_event(
        event_type='export',
        actor_user_id=user_id,
        actor_role='app',
        target_table='lead_export_batch',
        target_count=len(safe_contacts),
        target_ref=batch_no,
        payload={
            'batch_no': batch_no,
            'filter_payload': filter_payload,
            'file_sha256': file_sha256,
            'total_count': len(safe_contacts),
        },
    )
    return ExportResult(batch=batch, items=items, audit_log=audit_log, csv_text=csv_text)
