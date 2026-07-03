"""Hermes 用户端 Agent 模板列表（M1 §5.4）。

GET /api/v1/hermes/app/templates
- JOIN marketplace_template (template_type='agent') + marketplace_template_version (is_latest=true)
- 返回过滤敏感字段：只暴露 {template_id, name, description, emoji, icon_url, version}
  package_url + file_hash 是 backend 内部用（apply_template 时推给 runtime），不返给浏览器
- marketplace 没有 publish 模板时返 []（不报错）
"""
from __future__ import annotations

import sqlalchemy as sa

from fastapi import APIRouter

from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('', summary='获取可选 Agent 模板列表', dependencies=[DependsJwtAuth])
async def list_agent_templates(db: CurrentSession) -> ResponseModel:
    stmt = (
        sa.select(
            MarketplaceTemplate.template_id,
            MarketplaceTemplate.name,
            MarketplaceTemplate.description,
            MarketplaceTemplate.emoji,
            MarketplaceTemplate.icon_url,
            MarketplaceTemplateVersion.version,
        )
        .join(
            MarketplaceTemplateVersion,
            MarketplaceTemplateVersion.template_id == MarketplaceTemplate.template_id,
        )
        .where(
            MarketplaceTemplate.template_type == 'agent',
            MarketplaceTemplateVersion.is_latest.is_(True),
        )
        .order_by(MarketplaceTemplate.id.asc())
    )
    rows = (await db.execute(stmt)).mappings().all()
    items = [
        {
            'template_id': row['template_id'],
            'name': row['name'],
            'description': row['description'],
            'emoji': row['emoji'],
            'icon_url': row['icon_url'],
            'version': row['version'],
        }
        for row in rows
    ]
    return response_base.success(data=items)
