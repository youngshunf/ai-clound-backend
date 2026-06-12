"""hasn_growth 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_growth'`，
使本应用 model 继承它即落到 `hasn_growth.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets）仍留 public，跨 schema 全限定引用。

采集子域（原 lead_automation）整体收编进本应用（设计 07 §5.0）：10 张 `lead_*` 表
`SET SCHEMA hasn_growth` + 去前缀；Python 文件名/类名保留 `lead_*`（churn 控制，表名才是隔离边界）。

设计事实源：docs/AI自动获客任务系统/07-获客营销全链路AI-Native应用设计.md §3/§5。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（= app_id 对应的 schema，ADR-15）
APP_SCHEMA = 'hasn_growth'


class HasnGrowthAppBase(Base):
    """hasn_growth 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_growth。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
