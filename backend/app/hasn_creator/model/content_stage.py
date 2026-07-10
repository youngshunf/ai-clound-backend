import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import UniversalText, id_key


class ContentStage(HasnCreatorAppBase):
    """阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播"""

    __tablename__ = 'content_stage'

    id: Mapped[id_key] = mapped_column(init=False)
    content_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    stage: Mapped[str] = mapped_column(sa.String(30), default='', comment='阶段 (research:调研:blue/outline:大纲:cyan/first_draft:初稿:orange/final_draft:终稿:purple/cover:封面:green/storyboard:分镜:teal/voiceover:口播:violet/final_video:成片:red)')
    content_text: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    asset_refs: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='文件产出（封面/配图 hasn://asset/ 引用，落私有桶）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (draft:草稿:gray/approved:已采用:green/archived:已归档:gray)')
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    source_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='来源 (ai_generated:AI生成:violet/human_edited:人工编辑:blue/imported:导入:gray)')
