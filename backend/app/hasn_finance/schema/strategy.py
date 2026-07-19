from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StrategySchemaBase(SchemaBase):
    """策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    name: str = Field(description='策略名')
    description: str | None = Field(None, description='策略说明')
    market: str = Field(description='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    universe_json: dict = Field(description='适用标的池')
    params_json: dict = Field(description='可调参数（均线周期等）')
    code_py: str | None = Field(None, description='策略源码（引擎产出的 code/signal_engine.py）——策略本体。P1 禁止分享：服务端按 finance.strategy 硬拒')
    code_sha256: str | None = Field(None, description='源码指纹（改没改过、回测对不对得上）')
    source: str = Field(description='来源 (swarm:专家团队生成:blue/manual:手动创建:default/default:内置示例:gray)')
    bound_agent_id: str | None = Field(None, description='协作分身 HASN ID（对齐 doc21 AppCollab）')
    latest_backtest_id: int | None = Field(None, description='最新回测 id（冗余缓存，列表页显示最新夏普免 N+1）。权威在 backtest_report，不一致时以后者为准。FK 后置补（循环依赖）')
    platform_project_id: str | UUID | None = Field(None, description='挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateStrategyParam(StrategySchemaBase):
    """创建策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）参数"""


class UpdateStrategyParam(StrategySchemaBase):
    """更新策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）参数"""


class DeleteStrategyParam(SchemaBase):
    """删除策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）参数"""

    pks: list[int] = Field(description='策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3） ID 列表')


class GetStrategyDetail(StrategySchemaBase):
    """策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
