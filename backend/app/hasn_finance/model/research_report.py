from datetime import datetime, date
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, UniversalText


class ResearchReport(HasnFinanceAppBase):
    """投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）"""

    __tablename__ = 'research_report'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产出分身 HASN ID。为空 = 主人手工建（本模块罕见）')
    local_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 id（溯源：这份报告是哪台机器跑的）')
    symbol: Mapped[str] = mapped_column(sa.String(16), default='', comment='标的代码（查询键①）')
    market: Mapped[str] = mapped_column(sa.String(8), default='', comment='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    display_name: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='名称快照（非权威，实时名走行情服务）')
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='报告标题')
    verdict: Mapped[str] = mapped_column(sa.String(16), default='', comment='结论 (bullish:看多:red/bearish:看空:green/neutral:中性:default)')
    conviction: Mapped[int | None] = mapped_column(sa.SMALLINT(), default=None, comment='信心 1–5。允许为空 = 分身没给，不许默认 3 假装有')
    summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='一句话结论（列表页展示，免读全文）')
    body_md: Mapped[str] = mapped_column(UniversalText, default='', comment='报告正文（markdown）')
    findings_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='结构化要点（估值/风险/催化剂），列表页筛选用')
    data_as_of: Mapped[date] = mapped_column(sa.DATE(), default_factory=date.today, comment='数据截止时点（诚实性红线的数据层强制：不记它主人就无法判断报告是否新鲜；UI 必须常驻展示，不许折叠进详情）')
    swarm_preset: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='用的哪套专家团队预设')
    swarm_run_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地 run_id（仅溯源，同 local_ref 规约：不进 URI、不据它打开）')
    engine_version: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='引擎版本（可复现性）')
    bound_agent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='协作分身 HASN ID（详情页「找它改」，对齐 doc21 AppCollab）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
