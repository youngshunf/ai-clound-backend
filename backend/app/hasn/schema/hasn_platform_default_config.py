"""平台默认配置 schema（云端权威，单行下发）。

强类型嵌套：
  - node.media：节点级媒体模型默认（image/tts/stt failover 顺序，首个优先）
  - agent_runtime.models：平台默认 agent 运行时四槽模型（复用 ``AgentRuntimeModels``）

服务出参经 service 合并/序列化，daemon 据 revision 比对拉取。
"""

from datetime import datetime

from pydantic import Field

from backend.app.hasn.schema.hasn_agents import AgentRuntimeModels
from backend.common.schema import SchemaBase


class PlatformMediaDefaults(SchemaBase):
    """节点级媒体模型默认（failover 列表，首个优先；空=daemon 退回本地 file/struct 默认）。"""

    image_models: list[str] = Field(default_factory=list, description='图像生成模型 failover 顺序')
    tts_models: list[str] = Field(default_factory=list, description='语音合成模型 failover 顺序')
    stt_models: list[str] = Field(default_factory=list, description='语音识别模型 failover 顺序')


class PlatformNodeDefaults(SchemaBase):
    """节点级平台默认。"""

    media: PlatformMediaDefaults = Field(default_factory=PlatformMediaDefaults, description='媒体模型默认')


class PlatformAgentRuntimeDefaults(SchemaBase):
    """平台默认 agent 运行时配置（分身未显式设对应槽时回落）。"""

    models: AgentRuntimeModels = Field(default_factory=AgentRuntimeModels, description='4 槽模型平台默认')


class PlatformDefaultConfig(SchemaBase):
    """平台默认配置（覆盖式整体）。"""

    node: PlatformNodeDefaults = Field(default_factory=PlatformNodeDefaults, description='节点级默认')
    agent_runtime: PlatformAgentRuntimeDefaults = Field(
        default_factory=PlatformAgentRuntimeDefaults, description='agent 运行时默认'
    )


class PlatformDefaultConfigResponse(SchemaBase):
    """平台默认配置读取/更新出参。"""

    config: PlatformDefaultConfig = Field(description='当前平台默认配置')
    revision: str = Field(description='配置内容指纹（sha256[:16]）')
    updated_by: str | None = Field(None, description='最后修改管理员')
    updated_time: datetime | None = Field(None, description='最后更新时间')


class UpdatePlatformDefaultConfigRequest(PlatformDefaultConfig):
    """更新平台默认配置入参（覆盖式：Admin 取当前值整体提交）。"""
