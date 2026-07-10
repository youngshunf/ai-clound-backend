"""费用与账单中心 API - 面向前端用户（JWT 认证）· 实施/92 MK-7

路径前缀: /api/v1/user_tier/app/center

- `GET  /center`        账单中心聚合（订阅+积分快照 + 权益总账 + 提醒条），一发命中
- `POST /center/trial`  对某 feature_key 发放一次试用（走内核统一 grant_trial）

订单/积分流水沿用各自既有端点（/pay/app/orders、/user_tier/app/subscription/transactions）。
daemon `domains/billing` 把 center 接 local_first 镜像；trial 为写操作，直通云端不落镜像。
"""

from fastapi import APIRouter, Request

from backend.app.billing.schema.access import AccessDecision
from backend.app.billing.schema.center import BillingCenterResponse, GrantTrialParam
from backend.app.billing.service import access_service
from backend.app.billing.service.billing_center_service import billing_center_service
from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='费用与账单中心聚合',
    description='一次读齐订阅+积分快照、权益总账、到期/宽限提醒条',
    dependencies=[DependsJwtAuth],
)
async def get_billing_center(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[BillingCenterResponse]:
    app_code = getattr(request.state, 'app_code', 'huanxing')
    data = await billing_center_service.get_center(db, user_id=request.user.id, app_code=app_code)
    return response_base.success(data=data)


@router.post(
    '/trial',
    summary='开通试用',
    description='对某 feature_key 发放一次试用（内核统一 grant_trial，返回判定 AccessDecision）',
    dependencies=[DependsJwtAuth],
)
async def open_trial(
    request: Request,
    db: CurrentSessionTransaction,
    obj: GrantTrialParam,
) -> ResponseSchemaModel[AccessDecision]:
    owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.RequestError(msg='当前账号无 HASN 身份，无法开通试用')
    decision = await access_service.grant_trial(
        db, feature_key=obj.feature_key, subject_type='owner', subject_id=owner_hasn_id
    )
    return response_base.success(data=decision)
