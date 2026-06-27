"""hasn_reel 应用独立 PG schema 基类（ADR-15：AI-Native 应用命名空间与目录约定）。

每个 AI-Native 应用使用独立 PG schema。本基类在 fba `Base`（bigint `id_key` + DateTimeMixin:
created_time/updated_time）之上注入 `schema='hasn_reel'`，使本应用 model 继承它即落到 `hasn_reel.*`
schema，无需逐表手写 `__table_args__`。共享表（身份 public.hasn_humans/hasn_agents、资产
public.hasn_assets、工作会话 public.hasn_sessions/hasn_session_artifacts 等）仍留 public，跨 schema 全限定引用。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/29-短视频reel项目化创作设计.md §2。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（= app_id 对应的 schema，ADR-15）
APP_SCHEMA = 'hasn_reel'


class HasnReelAppBase(Base):
    """hasn_reel 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_reel。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
