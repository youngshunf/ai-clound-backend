
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_designsystem.model._base import DesignSystemBase
from backend.common.model import id_key

# 注：codegen 元数据按裸表名缓存，`revision` 与 hasn_deck.revision 撞名导致首版生成复用了
# deck 的列形状。本文件已按 backend/sql/hasn_designsystem/revision.sql（权威 DDL）手工校正。


class Revision(DesignSystemBase):
    """设计系统版本（每次 save/协作出一版，可回滚）"""

    __tablename__ = 'revision'

    id: Mapped[id_key] = mapped_column(init=False)
    design_system_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 design_system.id')
    rev_no: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='版本号（design_system 内单调递增，从 1 起）')
    author_kind: Mapped[str] = mapped_column(
        sa.String(8), default='human', comment='作者类型 (human:人:blue/agent:分身:violet)'
    )
    author_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='作者 HASN ID（人或分身）')
    bundle_asset_id: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment=f'完整 bundle zip 资产引用（hasn://asset/{id}，可空）'
    )
    tokens_css: Mapped[str | None] = mapped_column(sa.TEXT(), default=None, comment='真源 tokens.css（四层 token 契约）')
    design_tokens_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='派生 design-tokens.json（含分层/血缘/评分）'
    )
    tailwind_css: Mapped[str | None] = mapped_column(
        sa.TEXT(), default=None, comment='派生 tailwind-v4.css（@theme 映射）'
    )
    design_md: Mapped[str | None] = mapped_column(
        sa.TEXT(), default=None, comment='设计说明 DESIGN.md（创意部分，分身产出）'
    )
    components_html: Mapped[str | None] = mapped_column(sa.TEXT(), default=None, comment='组件样例 components.html')
    components_manifest_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='组件清单 components.manifest.json'
    )
    token_contract_report_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='token 契约评分 + 血缘报告'
    )
    note: Mapped[str | None] = mapped_column(
        sa.String(512), default=None, comment='版本备注（如"主色调暖一点"，可空）'
    )
