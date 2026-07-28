import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_community.model._base import CommunityBase
from backend.common.model import id_key


class HasnDocSpaceSubscriptions(CommunityBase):
    """社区文集订阅关系表"""

    __tablename__ = 'hasn_doc_space_subscriptions'

    id: Mapped[id_key] = mapped_column(init=False)
    subscription_id: Mapped[str] = mapped_column(
        sa.String(40),
        default='',
        comment='订阅关系权威 UUID',
    )
    space_id: Mapped[str] = mapped_column(
        sa.String(40),
        default='',
        comment='文集权威 space_id',
    )
    subscriber_hasn_id: Mapped[str] = mapped_column(
        sa.String(40),
        default='',
        comment='订阅者 hasn_id',
    )
