from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceAgentPublishRequestSchemaBase(SchemaBase):
    """Agent 市场发布幂等请求基础模型"""
    agent_hasn_id: str = Field(description='发起发布的 Agent HASN ID')
    owner_hasn_id: str = Field(description='资源所属主人 HASN ID')
    resource_kind: str = Field(description='资源类型 (skill:技能:blue/template:模板:green/skill_pack:技能包:cyan)')
    idempotency_key: str = Field(description='调用方生成的服务端去重键')
    asset_uri: str = Field(description='经 Owner ACL 验证的 hasn://asset/{id}')
    content_hash: str = Field(description='服务端解包后计算的规范化内容指纹，仅用于冲突检测')
    file_hash: str = Field(description='服务端读取资产字节后计算的 SHA256')
    resource_id: str | None = Field(None, description='首次提交创建或更新的权威资源 ID')
    version: str | None = Field(None, description='首次提交解析出的资源版本')
    state: str = Field(description='请求状态 (processing:处理中:orange/committed:已提交:green/partial:部分成功:yellow/failed:失败:red)')
    result: dict | None = Field(None, description='首次已提交结果，重复请求原样回放')
    work_session_id: str | None = Field(None, description='daemon 可信注入的工作会话 ID')


class CreateMarketplaceAgentPublishRequestParam(MarketplaceAgentPublishRequestSchemaBase):
    """创建Agent 市场发布幂等请求参数"""


class UpdateMarketplaceAgentPublishRequestParam(MarketplaceAgentPublishRequestSchemaBase):
    """更新Agent 市场发布幂等请求参数"""


class DeleteMarketplaceAgentPublishRequestParam(SchemaBase):
    """删除Agent 市场发布幂等请求参数"""

    pks: list[int] = Field(description='Agent 市场发布幂等请求 ID 列表')


class GetMarketplaceAgentPublishRequestDetail(MarketplaceAgentPublishRequestSchemaBase):
    """Agent 市场发布幂等请求详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
