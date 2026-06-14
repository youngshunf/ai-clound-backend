from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, TimeZone
from backend.app.hasn_community.model._base import CommunityBase
from backend.utils.timezone import timezone


class HasnCommunityBlocks(CommunityBase):
    """社区黑名单表（doc-13 §2.3.2）

    简单关联表（一对多），仅需 block/unblock/list，逻辑放在 community_service，
    不走 4-scope codegen 脚手架（避免重蹈 doc-12 §1.1 的 codegen 死代码）。
    """

    __tablename__ = 'hasn_community_blocks'

    id: Mapped[id_key] = mapped_column(init=False)
    blocker_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='拉黑发起者 hasn_id')
    blocked_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被拉黑对象 hasn_id')
    blocked_type: Mapped[str] = mapped_column(sa.String(10), default='human', comment='被拉黑对象类型 (human/agent)')
    reason: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='拉黑原因（可选）')
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='创建时间')
