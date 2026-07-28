"""获客落地页表单回流 + 退订 公开 API（设计 07 §8.4，open scope，无鉴权）。

POST /forms/{publish_ref}/submit：落地页表单提交 → 反滥用 → 建 inbound_form 客户。
反滥用：蜜罐字段 + IP HMAC，可疑判 spam 不进漏斗；PII 只写主体私有密文表。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.hasn_growth.schema.funnel import FormSubmitParam
from backend.app.hasn_growth.service.form_service import growth_form_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    """只接受反向代理覆盖的真实 IP，业务层立即转为带密钥 HMAC。"""
    real_ip = request.headers.get('x-real-ip')
    return (real_ip.strip() if real_ip else None) or (request.client.host if request.client else None)


@router.post('/forms/{publish_ref}/submit', summary='[Open] 落地页表单回流')
async def submit_form(
    request: Request,
    db: CurrentSessionTransaction,
    publish_ref: str,
    obj: FormSubmitParam,
) -> ResponseModel:
    data = await growth_form_service.submit_form(
        db,
        publish_ref=publish_ref,
        data=obj.model_dump(),
        client_ip=_client_ip(request),
        referrer=request.headers.get('referer'),
    )
    return response_base.success(data=data)
