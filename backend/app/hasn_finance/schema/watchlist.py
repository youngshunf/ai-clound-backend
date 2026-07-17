from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class WatchlistSchemaBase(SchemaBase):
    """自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    symbol: str = Field(description='标的代码（600519 / 00700 / AAPL）')
    market: str = Field(description='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    display_name: str | None = Field(None, description='名称快照（贵州茅台）。快照非权威——实时名走行情服务')
    note: str | None = Field(None, description='主人自己的备注')
    sort_order: int = Field(description='排序序号（主人手工拖拽次序）')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateWatchlistParam(WatchlistSchemaBase):
    """创建自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）参数"""


class UpdateWatchlistParam(WatchlistSchemaBase):
    """更新自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）参数"""


class DeleteWatchlistParam(SchemaBase):
    """删除自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）参数"""

    pks: list[int] = Field(description='自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1） ID 列表')


class GetWatchlistDetail(WatchlistSchemaBase):
    """自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
