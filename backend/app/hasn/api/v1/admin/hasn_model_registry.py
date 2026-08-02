"""模型注册表 - 管理端（运营在此标注能力语义、并从网关同步模型清单）。

三个端点，刻意不多不少：

- ``GET /``      列表（按 capability / 网关状态 / 关键字过滤，分页），带只读 `suggested_capability`。
- ``PATCH /{pk}`` 只改人工标注列（new-api 权威列由同步器覆盖，人工改了下轮就被冲掉）。
- ``POST /sync``  立即从 new-api 同步一轮，返回真实报告。

**没有创建/删除**：行只能来自 new-api 同步——放开手工新增等于放开「手输一个网关上不存在的
模型名」，那正是 2026-08-02 线上视频全线 503 的根因；而删行会连人工标注一起丢，同步语义是
「消失只标 missing、绝不删」。
"""

import logging

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn.schema.hasn_model_registry import (
    GetHasnModelRegistryDetail,
    ModelRegistryRow,
    ModelRegistrySyncReportSchema,
    PatchModelAnnotationParam,
)
from backend.app.hasn.service.hasn_model_registry_service import hasn_model_registry_service
from backend.app.hasn.service.model_registry_catalog_service import cost_tier_map
from backend.app.hasn.service.model_registry_sync_service import model_registry_sync_service, suggest_capability
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

log = logging.getLogger(__name__)

router = APIRouter()


def _to_row(item: Any, tiers: dict[int, str] | None = None) -> ModelRegistryRow:
    """详情 + 两个只读派生列。

    - `suggested_capability`：能力建议值，**现算不入库**（随启发式规则演进，存下来只会过期）；
    - `cost_tier`：生效价格档位，人工覆盖优先、否则按同能力内比价算出（`tiers` 由调用方按
      **全表**算好传入——分页页内比价会得出错的档位）。

    入参既可能是 ORM 行（PATCH 出参），也可能是分页 `model_dump` 后的 dict（列表出参），
    `model_validate` 两者都吃。
    """
    detail = GetHasnModelRegistryDetail.model_validate(item)
    endpoint_types = (detail.cost_extra or {}).get('supported_endpoint_types')
    return ModelRegistryRow(
        **detail.model_dump(),
        suggested_capability=suggest_capability(detail.model_name, endpoint_types),
        cost_tier=detail.cost_tier_override or (tiers or {}).get(detail.id),
    )


async def _bump_registry_revision(db) -> None:
    """注册表内容变更 → 主动 push `hasn.sync.invalidate(model_registry)` 给在线节点。

    best-effort：推送失败绝不影响已写入的标注/同步结果——离线节点靠重连握手快照对账追平。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_MODEL_REGISTRY
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump(KIND_MODEL_REGISTRY, db)
    except Exception as exc:  # noqa: BLE001 - 推送失败不致命
        log.warning(f'[HASN] model_registry invalidate 推送失败 (非致命): {exc}')


@router.get(
    '',
    summary='分页列出模型注册表（可按能力类别 / 网关状态 / 关键字过滤）',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='hasn_admin_list_model_registry',
)
async def list_model_registry(
    db: CurrentSession,
    capability: Annotated[str | None, Query(description='按能力类别过滤，unclassified 即待标注')] = None,
    upstream_status: Annotated[str | None, Query(description='按网关状态过滤 active/missing')] = None,
    keyword: Annotated[str | None, Query(description='按模型名模糊过滤')] = None,
) -> ResponseSchemaModel[PageData[ModelRegistryRow]]:
    page_data = await hasn_model_registry_service.get_list(
        db, capability=capability, upstream_status=upstream_status, keyword=keyword
    )
    # 档位按**全表**同能力比价算（分页页内比价会得出错的档位：翻页就变档）。全表 60+ 行，一次查询即可。
    tiers = cost_tier_map(await hasn_model_registry_service.get_all(db=db))
    page_data['items'] = [_to_row(item, tiers) for item in page_data['items']]
    return response_base.success(data=page_data)


@router.patch(
    '/{pk}',
    summary='更新单个模型的人工标注（能力/输入要求/方言/质量/场景/可见性/排序/价格档覆盖）',
    dependencies=[
        Depends(RequestPermission('hasn:model:registry:edit')),
        DependsRBAC,
    ],
    name='hasn_admin_patch_model_registry_annotation',
)
async def patch_model_registry_annotation(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='模型注册表条目 ID')],
    obj: PatchModelAnnotationParam,
) -> ResponseSchemaModel[ModelRegistryRow]:
    row = await hasn_model_registry_service.patch_annotation(db=db, pk=pk, obj=obj)
    tiers = cost_tier_map(await hasn_model_registry_service.get_all(db=db))
    await _bump_registry_revision(db)
    return response_base.success(data=_to_row(row, tiers))


@router.post(
    '/sync',
    summary='立即从 new-api 同步模型清单（upsert，绝不删行）',
    dependencies=[
        Depends(RequestPermission('hasn:model:registry:edit')),
        DependsRBAC,
    ],
    name='hasn_admin_sync_model_registry',
)
async def sync_model_registry(db: CurrentSessionTransaction) -> ResponseSchemaModel[ModelRegistrySyncReportSchema]:
    """同步失败**不吞**：new-api 不可达时抛 `NewApiError`，运营看到真实错误，
    而不是一份「同步成功、0 个模型」的假报告（零 fake）。"""
    report = await model_registry_sync_service.sync(db)
    await _bump_registry_revision(db)
    return response_base.success(data=ModelRegistrySyncReportSchema(**report.as_dict()))
