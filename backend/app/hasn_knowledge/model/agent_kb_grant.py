from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_knowledge.model._base import KnowledgeBase
from backend.common.model import id_key


class AgentKbGrant(KnowledgeBase):
    """分身知识库白名单（维度②，云端权威）"""

    __tablename__ = 'agent_kb_grant'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='分身 HASN ID')
    mode: Mapped[str] = mapped_column(sa.String(16), default='', comment='授权模式 (inherit:继承全部:green/restricted:限定范围:orange/denied:禁止访问:red)')
    kb_ids: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='restricted 白名单 kb_id 数组')
