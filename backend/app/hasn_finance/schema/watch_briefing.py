from datetime import datetime, date
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class WatchBriefingSchemaBase(SchemaBase):
    """盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    briefing_date: date = Field(description='简报日期')
    title: str = Field(description='简报标题')
    body_md: str = Field(description='简报正文（markdown）')
    covered_symbols_json: dict = Field(description='覆盖了哪些标的（按标的反查简报）')
    trigger: str = Field(description='触发 (scheduled:定时:blue/manual:手动:default)')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateWatchBriefingParam(WatchBriefingSchemaBase):
    """创建盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）参数"""


class UpdateWatchBriefingParam(WatchBriefingSchemaBase):
    """更新盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）参数"""


class DeleteWatchBriefingParam(SchemaBase):
    """删除盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）参数"""

    pks: list[int] = Field(description='盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7） ID 列表')


class GetWatchBriefingDetail(WatchBriefingSchemaBase):
    """盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
