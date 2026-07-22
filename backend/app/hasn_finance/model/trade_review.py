from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, UniversalText


class TradeReview(HasnFinanceAppBase):
    """交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）"""

    __tablename__ = 'trade_review'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 id（溯源）')
    shadow_account_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属影子账户 id（权威关系；复合 FK 保证与本行同 owner）')
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='复盘标题')
    body_md: Mapped[str] = mapped_column(UniversalText, default='', comment='复盘正文（markdown）')
    findings_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='结构化诊断（可跨期对比）')
    shadow_backtest_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='影子回测 id（「你要是一直按自己的策略做，会怎样」；可空；复合 FK 保证同 owner）')
    pdf_asset_uri: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='复盘 PDF 资产引用（hasn://asset/{id}）。主人确认派生同步后，引擎产出的 PDF 才经 daemon AssetGateway 落私有桶；确认前只保留本地路径且不得进 sync payload。序列化边界经 resolve_assets 换签名 URL')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
