"""会议副驾应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
模块目录与 PG schema 统一用 `hasn_` 前缀（app_id 为 `copilot`，URL 为 /api/v1/copilot/*）。
本基类在 fba `Base` 之上注入 `schema='hasn_copilot'`，使 model 继承它即落到 `hasn_copilot.*` schema，
无需逐表手写 `__table_args__`（codegen 生成的 model 默认落 public，必须改继承本基类）。
共享表（身份 public.hasn_humans/hasn_agents）仍留 public，跨 schema 全限定引用。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=copilot）
APP_SCHEMA = 'hasn_copilot'


class CopilotBase(Base):
    """会议副驾应用模型基类：继承 fba Base（created_time/updated_time）+ schema=hasn_copilot。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
