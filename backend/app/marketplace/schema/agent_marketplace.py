"""技能市场 Agent 权威 Interface 的 typed schema（DOC15-95 M1）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentMarketplacePage(BaseModel):
    """过滤后同一集合的游标分页结果。"""

    items: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int = 0


class AgentMarketplaceResource(BaseModel):
    """市场资源详情；稳定字段由领域服务补齐，类型差异留在 payload。"""

    payload: dict[str, Any]


class AgentMarketplacePublishRequest(BaseModel):
    """Agent 发布只接收资产引用和发布意图，身份与哈希均由服务端生成。"""

    asset_uri: str = Field(min_length=1, max_length=255)
    changelog: str | None = Field(default=None, max_length=5000)
    visibility: Literal['private', 'public'] = 'private'
    submit_review: bool = False
