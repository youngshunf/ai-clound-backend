"""资产上传/解析 API 的请求/响应 DTO（09 Stage1e）。

非 codegen 模型 schema，而是端点 DTO（与 IssueAgentMcpKeyParam 等同类）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UploadedAsset(BaseModel):
    """上传成功后返回的资产元数据。"""

    asset_id: str = Field(description='资产 ID (hasn://asset/{asset_id})')
    kind: str = Field(description='资产类型 image/voice/file')
    mime: str = Field(description='MIME 类型')
    size: int = Field(description='字节大小')
    width: int | None = Field(default=None, description='图片宽 px')
    height: int | None = Field(default=None, description='图片高 px')
    duration_ms: int | None = Field(default=None, description='语音时长毫秒')


class UploadedSourceSnapshot(UploadedAsset):
    """本地原件快照上传结果。"""

    asset_uri: str = Field(pattern=r'^hasn://asset/[^/]+$', description='跨端稳定资产引用')
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$', description='服务端校验后的内容 sha256')


class ResolveAssetsParam(BaseModel):
    """批量解析入参：asset_ids + 会话上下文。"""

    asset_ids: list[str] = Field(min_length=1, max_length=50, description='待解析的资产 ID 列表')
    conversation_id: str | None = Field(default=None, description='会话上下文（私有资产按会话授权）')
    resource_ref: str | None = Field(
        default=None,
        description='资源上下文（如 `deck:{cloud_id}`），据资源 ACL 额外授权其引用的资产（被分享者打开共享 deck 的图片）',
    )
    expires_in: int = Field(default=3600, ge=60, le=604800, description='签名有效期（秒，私有资产用）')


class ResolvedAssetItem(BaseModel):
    """单个解析结果。无权/不存在的资产不出现在结果列表中。"""

    asset_id: str
    display_url: str = Field(description='可展示 URL（public 直读 / private 临时签名）')
    expires_at: str | None = Field(default=None, description='过期时间 ISO8601（public 为 null=不过期）')
