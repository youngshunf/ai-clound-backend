"""工作台「未处理项」聚合的结构化契约（设计 doc 05）。

后端把主人名下各 AI-Native 应用的未处理项聚合成一份**权威、不漏**的清单交给主脑，
主脑据此分诊派发（§4 四态：直接做 / 提问 / 提醒）。字段刻意贴合 `workbench_briefing_document`
的 FocusItem，主脑几乎零转换即可抬进简报。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 与 FocusItem.category 同枚举（04 §4.2），主脑抬进简报时无需换算。
PendingCategory = Literal['task', 'social', 'app', 'plan', 'risk']
PendingUrgency = Literal['high', 'medium', 'low']


class PendingItem(BaseModel):
    """单条未处理项（owner 私有，来自某个应用的现成只读查询）。"""

    model_config = ConfigDict(extra='forbid')

    app_id: str = Field(description='来源应用 id（如 task/plan）')
    category: PendingCategory = Field(description='归类，对齐 FocusItem.category')
    urgency: PendingUrgency = Field(description='后端按到期/失败等给的初值，主脑可覆盖')
    title: str = Field(description='一句话标题')
    summary: str | None = Field(default=None, description='补充说明（可空）')
    ref: str = Field(description='去重键（对齐 FocusItem.source.ref，如 task:<uuid> / todo:<id>）')
    deep_link: str = Field(description='canonical 客户端路由 /apps/<id>...（后端产，不含 /workbench 旧前缀）')
    occurred_at: int | None = Field(default=None, description='发生/到期时间（ms epoch，可空）')


class AppPendingGroup(BaseModel):
    """某应用的未处理项分组（含总数与前 N 条明细）。"""

    model_config = ConfigDict(extra='forbid')

    app_id: str
    count: int = Field(description='该应用未处理项总数（可 > items 长度，items 是前 N 条）')
    items: list[PendingItem] = Field(default_factory=list)


class PendingScanResult(BaseModel):
    """一次全应用未处理项扫描结果（供 hasn.workbench.pending.scan 返回）。"""

    model_config = ConfigDict(extra='forbid')

    total: int = Field(description='所有应用未处理项 count 之和')
    by_app: dict[str, AppPendingGroup] = Field(
        default_factory=dict, description='按应用分组；无未处理项的应用不出现在此'
    )
    degraded: list[str] = Field(
        default_factory=list,
        description='本次读取失败/不可达的应用 id（如实标注，绝不为其造项；零 fake）',
    )
