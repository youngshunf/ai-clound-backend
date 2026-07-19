from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class TradeReviewSchemaBase(SchemaBase):
    """交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    shadow_account_id: int = Field(description='所属影子账户 id（权威关系；复合 FK 保证与本行同 owner）')
    title: str = Field(description='复盘标题')
    body_md: str = Field(description='复盘正文（markdown）')
    findings_json: dict = Field(description='结构化诊断（可跨期对比）')
    shadow_backtest_id: int | None = Field(None, description='影子回测 id（「你要是一直按自己的策略做，会怎样」；可空；复合 FK 保证同 owner）')
    pdf_asset_uri: str | None = Field(None, description='复盘 PDF 资产引用（hasn://asset/{id}）。主人确认派生同步后，引擎产出的 PDF 才经 daemon AssetGateway 落私有桶；确认前只保留本地路径且不得进 sync payload。序列化边界经 resolve_assets 换签名 URL')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateTradeReviewParam(TradeReviewSchemaBase):
    """创建交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）参数"""


class UpdateTradeReviewParam(TradeReviewSchemaBase):
    """更新交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）参数"""


class DeleteTradeReviewParam(SchemaBase):
    """删除交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）参数"""

    pks: list[int] = Field(description='交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6） ID 列表')


class GetTradeReviewDetail(TradeReviewSchemaBase):
    """交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
