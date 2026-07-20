from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

_ASSET_URI_TEMPLATE = 'hasn://asset/' + '{id}'


class HasnProjectSchemaBase(SchemaBase):
    """平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）基础模型"""

    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，逻辑引用 public.hasn_humans，绝不跨 owner）')
    name: str = Field(description='项目名')
    goal: str | None = Field(None, description='一句话目标（分身建项目时采集，供聚合视图与派发上下文注入，可空）')
    cover_asset_uri: str | None = Field(
        None,
        description=(
            f'封面图资产引用（{_ASSET_URI_TEMPLATE}，来源=上传/素材下载/AI 生成；'
            '序列化边界换 CDN 签名 URL，不存直链；可空回落品牌渐变+首字）'
        ),
    )
    status: str = Field(description='状态 (active:进行中:blue/archived:已归档:gray)')
    bound_agent_id: str | None = Field(
        None,
        description='默认协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；对齐 doc21 AppCollab，列名铁律 doc38 §8）',
    )
    enterprise_id: str | UUID | None = Field(
        None,
        description='企业归属（双模化，个人 NULL / 企业非空，对齐 GE，可空）',
    )


class CreateHasnProjectParam(HasnProjectSchemaBase):
    """创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数"""

    client_request_id: str | None = Field(
        None,
        max_length=128,
        description='创建请求幂等键（主人范围唯一；可空表示普通非幂等创建）',
    )


class UpdateHasnProjectParam(HasnProjectSchemaBase):
    """更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数"""


class DeleteHasnProjectParam(SchemaBase):
    """删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数"""

    pks: list[int] = Field(description='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID 列表')


class GetHasnProjectDetail(HasnProjectSchemaBase):
    """平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    client_request_id: str | None = None
    created_time: datetime
    updated_time: datetime | None = None
