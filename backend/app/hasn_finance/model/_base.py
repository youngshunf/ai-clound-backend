"""hasn_finance 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
本基类在 fba `Base`（created_time/updated_time DateTimeMixin）之上注入 `schema='hasn_finance'`，
使本应用 model 继承它即落到 `hasn_finance.*` schema，无需逐表手写 `__table_args__`。

命名：schema 内**不重复 app 前缀**（`hasn_finance.strategy` 而非 `hasn_finance.finance_strategy`）——
对齐 `hasn_creator.content` / `hasn_creator.profile`；`hasn_quant.quant_strategy` 那种带前缀的是旧形状，
正随本次换根退役（05 §3.3）。

共享表仍留 public，跨 schema 全限定引用：身份 public.hasn_humans/hasn_agents、产物
public.hasn_artifacts（六类产物的登记指针）、资产 public.hasn_assets。平台项目
hasn_project.hasn_project 由两张容器表（strategy / shadow_account）以可空 platform_project_id
联邦挂靠（doc38 层2，05 §4）。

注意（codegen 修正）：fba codegen 默认生成继承裸 `Base` 的 model（落 public），且不保留 SQL 里的
DEFAULT 值。本应用 model 一律手工改为继承本基类，并把默认值对齐各表 SQL
（backend/sql/hasn_finance/*.sql）——尤其 `revision` 默认 1、`status` 默认 'active'。

设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1。
"""

from datetime import datetime

from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from backend.common.model import Base, TimeZone
from backend.utils.timezone import timezone

# 本应用的 PG schema 名（hasn_ 前缀；app_id=finance，URL /api/v1/finance/*）
APP_SCHEMA = 'hasn_finance'


class HasnFinanceAppBase(Base):
    """hasn_finance 应用模型基类：created_time/updated_time（继承自 fba Base）+ schema=hasn_finance。"""

    __abstract__ = True

    # 覆盖 fba DateTimeMixin 的 updated_time：本应用各表 updated_time 声明为 NOT NULL
    # （下行增量同步游标按 updated_time 递增取增量，禁止 NULL——见各表 SQL COMMENT）。
    # 而 DateTimeMixin 仅挂 onupdate（只在 UPDATE 触发），INSERT 时 ORM 会显式写 NULL，
    # 覆盖 DB 的 DEFAULT now() → 撞 NOT NULL。这里补 default_factory 使其在 INSERT 也落值
    # （与 created_time 对齐：create 时 created_time == updated_time），UPDATE 仍走 onupdate 刷新。
    updated_time: Mapped[datetime] = mapped_column(
        TimeZone,
        init=False,
        default_factory=timezone.now,
        onupdate=timezone.now,
        sort_order=999,
        comment='更新时间',
    )

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
