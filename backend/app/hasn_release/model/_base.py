"""hasn_release 应用独立 PG schema 基类（ADR-15：AI-Native 应用命名空间与目录约定）。

在 fba `Base`（bigint `id_key` + DateTimeMixin：created_time/updated_time）之上注入
`schema='hasn_release'`，本应用 model 继承它即落到 `hasn_release.*` schema，无需逐表手写
`__table_args__`。建表 DDL 权威在 `backend/sql/hasn_release/001_create_release_tables.sql`
（含 CHECK/唯一索引/FK CASCADE），本处 ORM 仅负责查询/写入映射，schema 名必须一致。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（= app_id）
APP_SCHEMA = 'hasn_release'


class HasnReleaseAppBase(Base):
    """hasn_release 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_release。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
