"""获客落地页表单回流 + 退订 公开 API（设计 07 §8.4，open scope，无鉴权）。

POST /forms/{publish_ref}/submit：落地页表单提交 → 反滥用 → 建 inbound_form 客户。
反滥用：蜜罐字段 + IP（source_meta）记录，可疑判 spam 不进漏斗。
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request

from backend.app.hasn_growth.schema.funnel import FormSubmitParam
from backend.app.hasn_growth.service.form_service import growth_form_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _client_ip_hash(request: Request) -> str | None:
    # 归因/反滥用：只存 IP 哈希，不存明文 IP。
    fwd = request.headers.get('x-forwarded-for')
    ip = (fwd.split(',')[0].strip() if fwd else None) or (request.client.host if request.client else None)
    if not ip:
        return None
    return hashlib.sha256(ip.encode('utf-8')).hexdigest()


@router.post('/forms/{publish_ref}/submit', summary='[Open] 落地页表单回流')
async def submit_form(request: Request, db: CurrentSessionTransaction, publish_ref: str, obj: FormSubmitParam) -> ResponseModel:
    source_meta = {
        'ip_hash': _client_ip_hash(request),
        'referer': request.headers.get('referer'),
        'user_agent': request.headers.get('user-agent'),
        **obj.extra,
    }
    data = await growth_form_service.submit_form(
        db, publish_ref=publish_ref, data=obj.model_dump(), source_meta=source_meta
    )
    return response_base.success(data=data)
