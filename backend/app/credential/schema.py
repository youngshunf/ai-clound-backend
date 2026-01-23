"""
凭证同步 Schema

@author Ysf
@date 2025-12-28
"""

from datetime import datetime

from pydantic import BaseModel, Field


class CredentialSyncRequest(BaseModel):
    """凭证同步请求"""
    platform: str = Field(..., description="平台名称")
    account_id: str = Field(..., description="账号ID")
    account_name: str = Field(default="", description="账号名称")
    encrypted_data: str = Field(..., description="Base64编码的加密数据")
    sync_key_hash: str = Field(..., description="同步密钥哈希")
    client_id: str | None = Field(default=None, description="客户端设备ID")
    version: int = Field(default=1, description="数据版本号")


class CredentialSyncResponse(BaseModel):
    """凭证同步响应"""
    success: bool
    platform: str
    account_id: str
    version: int
    message: str = ""


class CredentialInfo(BaseModel):
    """凭证信息"""
    platform: str
    account_id: str
    account_name: str
    encrypted_data: str  # Base64编码
    version: int
    created_time: datetime
    updated_time: datetime | None = None


class CredentialListResponse(BaseModel):
    """凭证列表响应"""
    credentials: list[CredentialInfo]
    total: int


class CredentialDeleteResponse(BaseModel):
    """凭证删除响应"""
    success: bool
    platform: str
    account_id: str
    message: str = ""
