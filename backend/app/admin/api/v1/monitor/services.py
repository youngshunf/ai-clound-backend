"""内部服务健康监控端点（消费 service_registry 目录 + service_health 聚合）。

管理端「内部服务健康」页一次拿到全部内部独立服务（finance/quant/ragflow/new-api）的死活，
免去逐个 curl。零业务副作用：只做轻量探活。
"""

from dataclasses import asdict

from fastapi import APIRouter

from backend.app.admin.schema.monitor import ServiceHealthInfo
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.service_health import check_all_services_health

router = APIRouter()


@router.get('', summary='内部服务健康', dependencies=[DependsJwtAuth])
async def get_services_health() -> ResponseSchemaModel[list[ServiceHealthInfo]]:
    reports = await check_all_services_health()
    data = [ServiceHealthInfo(**asdict(r)) for r in reports]
    return response_base.success(data=data)
