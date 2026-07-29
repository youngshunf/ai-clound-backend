from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAssetsSchemaBase(SchemaBase):
    """HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）基础模型"""
    asset_id: str = Field(description='资产 ID (hasn://asset/{asset_id})')
    owner_hasn_id: str = Field(description='所属主人 hasn_id')
    access: str = Field(description='访问类型 (public:公开:green/private:私有:orange)')
    storage_id: int = Field(description='存储空间 ID (关联 s3_storage.id)')
    object_key: str = Field(description='对象 key (相对 opendal root，不含 provider URL)')
    kind: str = Field(description='资产类型 (image:图片:blue/voice:语音:purple/file:文件:gray)')
    mime: str = Field(description='MIME 类型')
    size_bytes: int = Field(description='字节大小')
    content_sha256: str | None = Field(None, description='资产内容 sha256；本地原件快照据此幂等上传')
    width: int | None = Field(None, description='图片宽 (px)')
    height: int | None = Field(None, description='图片高 (px)')
    duration_ms: int | None = Field(None, description='语音时长 (毫秒)')
    transcript: str | None = Field(None, description='语音转写文本 (STT 结果)')
    thumbnail_asset_id: str | None = Field(None, description='缩略图资产 ID')
    extract_status: str = Field(description='抽取状态 (pending:待处理:orange/done:完成:green/unsupported:不支持:gray/stt_unavailable:STT不可用:red)')


class CreateHasnAssetsParam(HasnAssetsSchemaBase):
    """创建HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）参数"""


class UpdateHasnAssetsParam(HasnAssetsSchemaBase):
    """更新HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）参数"""


class DeleteHasnAssetsParam(SchemaBase):
    """删除HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）参数"""

    pks: list[int] = Field(description='HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据） ID 列表')


class GetHasnAssetsDetail(HasnAssetsSchemaBase):
    """HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
