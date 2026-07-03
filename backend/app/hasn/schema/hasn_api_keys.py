from datetime import datetime

from pydantic import BaseModel, Field


class CreateApiKeyReq(BaseModel):
    name: str = Field(description='API Key 名称 (如: 办公室 Mac)')
    scopes: dict | None = Field(default=None, description='授权 scopes JSON')
    bound_node_id: str | None = Field(default=None, description='绑定 Node ID（可为空）')
    expires_at: datetime | None = Field(default=None, description='过期时间')


class ApiKeyOut(BaseModel):
    key_id: str = Field(description='Owner API Key 唯一标识')
    key_name: str | None = Field(None, description='API Key 名称')
    owner_id: str = Field(description='Owner 的 hasn_id')
    status: str = Field(description='状态')
    scopes: dict | None = Field(default=None, description='授权 scopes JSON')
    bound_node_id: str | None = Field(default=None, description='绑定 Node ID（可为空）')
    expires_at: datetime | None = Field(default=None, description='过期时间')
    created_time: datetime | None = Field(None, description='创建时间')
    last_seen_at: datetime | None = Field(None, description='最后使用时间')


class CreateApiKeyRes(ApiKeyOut):
    owner_api_key: str = Field(description='明文 Owner API Key (仅返回一次)')
