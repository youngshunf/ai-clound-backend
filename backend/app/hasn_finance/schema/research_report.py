from datetime import datetime, date
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ResearchReportSchemaBase(SchemaBase):
    """投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建（本模块罕见）')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源：这份报告是哪台机器跑的）')
    symbol: str = Field(description='标的代码（查询键①）')
    market: str = Field(description='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    display_name: str | None = Field(None, description='名称快照（非权威，实时名走行情服务）')
    title: str = Field(description='报告标题')
    verdict: str = Field(description='结论 (bullish:看多:red/bearish:看空:green/neutral:中性:default)')
    conviction: int | None = Field(None, description='信心 1–5。允许为空 = 分身没给，不许默认 3 假装有')
    summary: str | None = Field(None, description='一句话结论（列表页展示，免读全文）')
    body_md: str = Field(description='报告正文（markdown）')
    findings_json: dict = Field(description='结构化要点（估值/风险/催化剂），列表页筛选用')
    data_as_of: date = Field(description='数据截止时点（诚实性红线的数据层强制：不记它主人就无法判断报告是否新鲜；UI 必须常驻展示，不许折叠进详情）')
    swarm_preset: str | None = Field(None, description='用的哪套专家团队预设')
    swarm_run_ref: str | None = Field(None, description='本地 run_id（仅溯源，同 local_ref 规约：不进 URI、不据它打开）')
    engine_version: str | None = Field(None, description='引擎版本（可复现性）')
    bound_agent_id: str | None = Field(None, description='协作分身 HASN ID（详情页「找它改」，对齐 doc21 AppCollab）')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateResearchReportParam(ResearchReportSchemaBase):
    """创建投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）参数"""


class UpdateResearchReportParam(ResearchReportSchemaBase):
    """更新投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）参数"""


class DeleteResearchReportParam(SchemaBase):
    """删除投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）参数"""

    pks: list[int] = Field(description='投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2） ID 列表')


class GetResearchReportDetail(ResearchReportSchemaBase):
    """投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
