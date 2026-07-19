from datetime import datetime, date
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ShadowAccountSchemaBase(SchemaBase):
    """影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    broker: str | None = Field(None, description='券商')
    account_alias: str | None = Field(None, description='主人给的别名（「我的打新账户」）。★隐私红线：绝不存真实账号')
    stmt_period_start: date | None = Field(None, description='对账单覆盖区间起')
    stmt_period_end: date | None = Field(None, description='对账单覆盖区间止')
    profile_json: dict = Field(description='交易画像（持仓周期/交易频率/胜率/盈亏比/偏好标的）。★高度敏感：仅主人确认同步清单后才上推')
    behaviors_json: dict = Field(description='行为诊断（处置效应/过度交易/追涨/锚定）。★高度敏感：同上')
    source_file_name: str | None = Field(None, description='脱敏显示名；basename 后仍须清除账号/用户名，无法可靠脱敏就置 NULL——不是原始文件名备份')
    source_hash: str | None = Field(None, description='已上传分享快照的 sha256，必须与 source_asset_uri 对应；P1 恒为 NULL')
    source_asset_uri: str | None = Field(None, description='未来显式分享原件后才有 hasn://asset/{id}；P1 恒为 NULL')
    source_synced_at: datetime | None = Field(None, description='原件分享快照上传时刻；P1 恒为 NULL')
    version: int = Field(description='版本号：这季度 vs 上季度')
    superseded_by: int | None = Field(None, description='被哪个新版本取代（自引用；复合 FK 保证版本链不跨主人）')
    platform_project_id: str | UUID | None = Field(None, description='挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateShadowAccountParam(ShadowAccountSchemaBase):
    """创建影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）参数"""


class UpdateShadowAccountParam(ShadowAccountSchemaBase):
    """更新影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）参数"""


class DeleteShadowAccountParam(SchemaBase):
    """删除影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）参数"""

    pks: list[int] = Field(description='影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5） ID 列表')


class GetShadowAccountDetail(ShadowAccountSchemaBase):
    """影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
