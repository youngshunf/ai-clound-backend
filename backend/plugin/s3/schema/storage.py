from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class S3StorageSchemaBase(SchemaBase):
    """S3 存储基础模型"""

    name: str = Field(description='存储名称')
    endpoint: str = Field(description='终端节点')
    access_key: str = Field(description='访问密钥')
    secret_key: str = Field(description='密钥')
    bucket: str = Field(description='存储桶')
    prefix: str | None = Field(None, description='前缀')
    region: str | None = Field(None, description='区域')
    cdn_domain: str | None = Field(None, description='CDN域名，如 https://cdn.example.com')
    access: str = Field('private', description='访问类型：public 公开(CDN 直读不签名) / private 私有(签名访问)')
    sign_strategy: str = Field(
        's3_presign',
        description=(
            '签名策略(私有时生效)：s3_presign S3预签名 / cdn_timestamp CDN时间戳防盗链 / '
            'qiniu_private 七牛私有下载凭证(e+token,私有桶+回源鉴权) / nginx_secure_link Nginx防盗链'
        ),
    )
    remark: str | None = Field(None, description='备注')


class CreateS3StorageParam(S3StorageSchemaBase):
    """创建 S3 存储参数"""


class UpdateS3StorageParam(S3StorageSchemaBase):
    """更新 S3 存储参数"""


class DeleteS3StorageParam(SchemaBase):
    """删除 S3 存储参数"""

    pks: list[int] = Field(description='S3 存储 ID 列表')


class GetS3StorageDetail(S3StorageSchemaBase):
    """S3 存储详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description='S3 存储 ID')
    created_time: datetime = Field(description='创建时间')
    updated_time: datetime | None = Field(None, description='更新时间')
