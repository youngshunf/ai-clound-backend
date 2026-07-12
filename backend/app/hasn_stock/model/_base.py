"""hasn_stock 应用独立 PG schema 基类（ADR-15 应用命名空间隔离）。

素材站目录表落独立 schema `hasn_stock`，与身份表（public.hasn_humans/hasn_agents）、
资产表（public.hasn_assets）等共享表隔离，跨 schema 全限定引用。与 external_mcp/hasn_diag 同构。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名
APP_SCHEMA = 'hasn_stock'


class HasnStockAppBase(Base):
    """hasn_stock 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_stock。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
