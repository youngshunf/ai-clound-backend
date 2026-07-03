"""D10 移动端 App - GET /api/v1/app/feature-flags/{hasn_id}.

依赖规范: docs/架构设计/移动端/08-构建打包与发布详细设计.md §9.1。

M1 决策: 纯国内发行不走 Play Console 灰度 track; 灰度由后端
`feature_flags` 注册表 + `feature_flag_assignments` 白名单实现。客户端
启动时(或推送唤醒后)调用本端点, 拉取该 hasn_id 生效的 flag 列表。

返回结构:
    {code: 200, msg: "...", data: {flags: [{key, enabled, payload}]}}

解析规则 (service 层 `resolve_flags_for_hasn_id`):
- 遍历 `feature_flags` 全表
- 若 assignment 存在 → enabled = assignment.enabled (显式覆盖)
- 否则 → enabled = default_enabled (全局缺省)
- payload 直接回 flag.payload (可为 null)

鉴权: 本端点供客户端首屏(甚至未登录态)使用, 当前无鉴权; payload 由运营
显式入库, 不包含 PII。若后续需要可叠加 DependsJwtAuth + hasn_id 归属校验。

测试解耦: service 函数 `resolve_flags_for_hasn_id` 可被 monkeypatch 替换,
避免引入 aiosqlite — 与 B4/B8 测试模式一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Path
from pydantic import Field
from sqlalchemy import select

from backend.app.hasn_client.model.feature_flag import FeatureFlag, FeatureFlagAssignment
from backend.common.response.response_schema import (
    ResponseSchemaModel,
    response_base,
)
from backend.common.schema import SchemaBase
from backend.database.db import CurrentSession

router = APIRouter()


@dataclass(frozen=True)
class _ResolvedFlag:
    key: str
    enabled: bool
    payload: dict | None


class FeatureFlagEntry(SchemaBase):
    """单个 flag 的客户端可见字段."""

    key: str = Field(description='flag 唯一 key')
    enabled: bool = Field(description='对当前 hasn_id 是否启用')
    payload: dict | None = Field(default=None, description='可选配置 JSON')


class FeatureFlagsResponse(SchemaBase):
    """GET /feature-flags/{hasn_id} 响应载荷."""

    flags: list[FeatureFlagEntry] = Field(
        default_factory=list,
        description='对当前 hasn_id 生效的 flag 列表',
    )


async def resolve_flags_for_hasn_id(db: Any, hasn_id: str) -> list[_ResolvedFlag]:
    """service: 对 hasn_id 解析生效的 flag 列表.

    - 读 feature_flags 全表 (M1 规模小; 未来可加 Redis 缓存)
    - 读该 hasn_id 的 assignments 并构建 flag_id→enabled 映射
    - assignment 存在 → 覆盖 default_enabled
    """
    flags_result = await db.execute(select(FeatureFlag))
    all_flags = flags_result.scalars().all()

    assigns_result = await db.execute(
        select(FeatureFlagAssignment).where(
            FeatureFlagAssignment.hasn_id == hasn_id
        )
    )
    assignments = {a.flag_id: a.enabled for a in assigns_result.scalars().all()}

    resolved: list[_ResolvedFlag] = []
    for flag in all_flags:
        effective = assignments.get(flag.id, flag.default_enabled)
        resolved.append(
            _ResolvedFlag(
                key=flag.key,
                enabled=bool(effective),
                payload=flag.payload,
            )
        )
    return resolved


@router.get(
    '/{hasn_id}',
    summary='拉取 hasn_id 生效的 feature flag 列表 (移动端 D10 灰度)',
)
async def get_feature_flags(
    db: CurrentSession,
    hasn_id: Annotated[
        str,
        Path(min_length=1, max_length=40, description='客户端登录态 hasn_id'),
    ],
) -> ResponseSchemaModel[FeatureFlagsResponse]:
    """GET /api/v1/app/feature-flags/{hasn_id} — 返回 flag 列表.

    200: {data: {flags: [{key, enabled, payload}]}}
    """
    resolved = await resolve_flags_for_hasn_id(db, hasn_id)
    return response_base.success(
        data=FeatureFlagsResponse(
            flags=[
                FeatureFlagEntry(
                    key=item.key, enabled=item.enabled, payload=item.payload,
                )
                for item in resolved
            ]
        )
    )
