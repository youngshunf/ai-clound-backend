from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, UniversalText


class Strategy(HasnFinanceAppBase):
    """策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）"""

    __tablename__ = 'strategy'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 id（溯源）')
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='策略名')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='策略说明')
    market: Mapped[str] = mapped_column(sa.String(8), default='', comment='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    universe_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='适用标的池')
    params_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='可调参数（均线周期等）')
    code_py: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='策略源码（引擎产出的 code/signal_engine.py）——策略本体。P1 禁止分享：服务端按 finance.strategy 硬拒')
    code_sha256: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='源码指纹（改没改过、回测对不对得上）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (swarm:专家团队生成:blue/manual:手动创建:default/default:内置示例:gray)')
    bound_agent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='协作分身 HASN ID（对齐 doc21 AppCollab）')
    latest_backtest_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='最新回测 id（冗余缓存，列表页显示最新夏普免 N+1）。权威在 backtest_report，不一致时以后者为准。FK 后置补（循环依赖）')
    platform_project_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
