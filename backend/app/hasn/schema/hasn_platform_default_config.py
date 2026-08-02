"""平台默认配置 schema（云端权威，单行下发）。

强类型嵌套：
  - node.media：节点级媒体模型默认（文生图/图像编辑/tts/stt failover 顺序，首个优先）
  - agent_runtime.models：平台默认 agent 运行时四槽模型（复用 ``AgentRuntimeModels``）

服务出参经 service 合并/序列化，daemon 据 revision 比对拉取。
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from backend.app.hasn.schema.hasn_agents import AgentRuntimeModels
from backend.common.schema import SchemaBase


class VideoModelSpec(SchemaBase):
    """视频模型声明（对象写法）。

    视频渠道之间的入参差异远大于图像/语音，必须显式声明两件事，daemon 据此构造请求体：

    - ``modality``：该模型接哪种输入。多数上游把文生视频与图生视频做成不同模型，把 t2v
      请求发给 i2v 模型必然失败**且仍会预扣配额**，声明后 daemon 直接跳过不提交。
    - ``dialect``：入参方言。阿里通义万相系（``wan*`` / ``happyhorse*``）图生视频只认
      ``480P/720P/1080P`` 档位，发 ``1280x720`` 会被上游拒绝；OpenAI 兼容渠道反过来要
      ``宽x高``。方言靠模型名猜不可靠（``happyhorse-1.1-i2v`` 看不出是阿里），故要显式写。
    """

    name: str = Field(description='new-api 上的模型名')
    modality: Literal['any', 'text_to_video', 'image_to_video'] = Field(
        default='any', description='能承接的输入形态：任意 / 仅文生视频 / 仅图生视频'
    )
    dialect: Literal['openai', 'ali'] = Field(
        default='openai', description='入参方言：OpenAI 兼容 / 阿里通义万相系'
    )


class PlatformMediaDefaults(SchemaBase):
    """节点级媒体模型默认（failover 列表，首个优先；空=daemon 退回本地 file/struct 默认）。"""

    image_models: list[str] = Field(default_factory=list, description='图像生成模型 failover 顺序')
    image_edit_models: list[str] = Field(
        default_factory=list,
        description='图像编辑模型 failover 顺序（扩图、生成式填充、风格迁移；/images/edits）',
    )
    tts_models: list[str] = Field(default_factory=list, description='语音合成模型 failover 顺序')
    stt_models: list[str] = Field(default_factory=list, description='语音识别模型 failover 顺序')
    video_models: list[str | VideoModelSpec] = Field(
        default_factory=list,
        description=(
            '视频生成模型 failover 顺序（task 式异步）。默认空——视频渠道需运营先在 new-api 开通后再下发。'
            '每项可写模型名字符串（等价 modality=any + dialect=openai），也可写 VideoModelSpec 对象'
            '显式声明模态与方言；视频渠道入参差异大，建议一律用对象写法'
        ),
    )


class PlatformNodeDefaults(SchemaBase):
    """节点级平台默认。"""

    media: PlatformMediaDefaults = Field(default_factory=PlatformMediaDefaults, description='媒体模型默认')


class PlatformAgentRuntimeDefaults(SchemaBase):
    """平台默认 agent 运行时配置（分身未显式设对应槽时回落）。"""

    models: AgentRuntimeModels = Field(default_factory=AgentRuntimeModels, description='4 槽模型平台默认')
    model_fallback_pool: list[str] = Field(
        default_factory=list,
        description=(
            '主模型 failover 全局兜底池（有序模型名，同一 new-api 网关只换模型名）。'
            'daemon 据此为每个分身的已解析主模型生成兜底链（剔除主模型自身、去重、保序），随 LLM '
            '凭据下发，runtime 物化为 fallback_providers。空=无兜底（单模型，行为不回归）。'
            '主人只配主模型，平台维护此池'
        ),
    )


class PlatformSecurityDefaults(SchemaBase):
    """节点级安全默认（三层漏斗裁判开关等，doc07）。"""

    sensitive_scanner_enabled: bool = Field(
        default=True,
        description=(
            'L1 敏感信息扫描器（hasn-core SensitiveScanner）总开关，缺省开。关闭时 daemon 出站闸 '
            'guard_outbound_disclosure 直接跳过 scan_sensitive（L2 LLM 裁判照常）——仅关正则层，'
            '不影响硬权限 L0 与云端 LLM 裁判 L2'
        ),
    )


class PlatformDefaultConfig(SchemaBase):
    """平台默认配置（覆盖式整体）。

    ``node``/``agent_runtime`` 是真·平台默认（Admin「平台默认配置」页编辑、覆盖式 PUT 写回 PDC 表）；
    ``app_configs`` 是**只读下发聚合**——各 AI-Native 应用自治的 ``hasn_app_catalog.config_json``
    （如 film 的 5 类模型 + 引擎包 manifest 内联），权威在 catalog、管理端编辑 catalog，这里只搭
    platform-config 下发通道（compute_revision 涵盖→daemon 重拉→从 ``app_configs.<app_id>`` 读）。
    **PUT 写回不得反向写 catalog**（service.update_config 落 PDC 表时丢弃 app_configs）。
    """

    node: PlatformNodeDefaults = Field(default_factory=PlatformNodeDefaults, description='节点级默认')
    agent_runtime: PlatformAgentRuntimeDefaults = Field(
        default_factory=PlatformAgentRuntimeDefaults, description='agent 运行时默认'
    )
    security: PlatformSecurityDefaults = Field(
        default_factory=PlatformSecurityDefaults, description='节点级安全默认（裁判漏斗开关等）'
    )
    app_configs: dict = Field(
        default_factory=dict,
        description='各 AI-Native 应用平台级配置聚合（app_id→config_json），源自 hasn_app_catalog.config_json，只读下发',
    )


class PlatformDefaultConfigResponse(SchemaBase):
    """平台默认配置读取/更新出参。"""

    config: PlatformDefaultConfig = Field(description='当前平台默认配置')
    revision: str = Field(description='配置内容指纹（sha256[:16]）')
    updated_by: str | None = Field(None, description='最后修改管理员')
    updated_time: datetime | None = Field(None, description='最后更新时间')


class UpdatePlatformDefaultConfigRequest(PlatformDefaultConfig):
    """更新平台默认配置入参（覆盖式：Admin 取当前值整体提交）。"""
