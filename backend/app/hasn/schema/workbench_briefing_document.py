"""BriefingDocument 统一产物 schema（设计 doc 04 §4）。

主脑经 MCP 工具 `hasn.workbench.briefing.publish` 提交结构化文档，**工具入口强校验**本 schema
（校验失败让模型重试，绝不正则解析自由文本——零 fake）。工作台只认这个 schema 渲染，不内嵌
业务判断。owner_id/agent_id 由凭证回填（身份由认证决定），不信任文档自带值。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.common.schema import SchemaBase

# 关注项来源域（→ 图标/色调）/ 紧急度（→ 排序与徽章）/ 简报状态。
FocusCategory = Literal['task', 'social', 'app', 'plan', 'risk']
Urgency = Literal['high', 'medium', 'low']
BriefingState = Literal['generating', 'ready', 'failed']
ActionKind = Literal['open_app', 'run_task', 'open_route', 'dismiss']
PlanHorizon = Literal['today', 'week']


class BriefingAction(BaseModel):
    """四类操作之一（§4.3）。kind 决定语义，其余字段按 kind 选填；额外字段保留以便前向兼容。"""

    model_config = ConfigDict(extra='allow')

    kind: ActionKind = Field(description='操作类型')
    label: str = Field(min_length=1, max_length=64, description='按钮文案')
    # open_app
    app_id: str | None = Field(default=None, description='open_app：目标 AI-Native 应用 id')
    deep_link: str | None = Field(default=None, description='open_app：应用内深链 /workbench/apps/{app_id}/...')
    # run_task
    agent_id: str | None = Field(default=None, description='run_task：执行分身（默认主脑）')
    prompt: str | None = Field(default=None, description='run_task：派发提示词')
    skill_ids: list[str] | None = Field(default=None, description='run_task：限定技能')
    confirm: bool | None = Field(default=None, description='run_task：是否弹确认后再派发')
    # open_route
    route: str | None = Field(default=None, description='open_route：客户端内部路由')


class BriefingSource(BaseModel):
    """关注项溯源出处（可点开核验，抗 fake）。"""

    model_config = ConfigDict(extra='allow')

    app_id: str | None = Field(default=None, description='来源应用 id（knowledge/messages/tasks...）')
    ref: str | None = Field(default=None, description='来源引用（doc:1234 / task:T-12...，用于反馈去重）')
    deep_link: str | None = Field(default=None, description='可点开的深链')


class FocusItem(BaseModel):
    """关注项（§4.2）。"""

    model_config = ConfigDict(extra='allow')

    item_id: str = Field(min_length=1, max_length=64, description='关注项 id（反馈去重键）')
    category: FocusCategory = Field(description='来源域 → 图标/色调')
    urgency: Urgency = Field(description='紧急度 → 排序/徽章')
    title: str = Field(min_length=1, max_length=200, description='标题')
    summary: str = Field(default='', max_length=2000, description='摘要')
    source: BriefingSource | None = Field(default=None, description='溯源出处')
    evidence: list[str] = Field(default_factory=list, description='佐证（便于主人核验）')
    actions: list[BriefingAction] = Field(default_factory=list, description='可执行操作')


class PlanItem(BaseModel):
    """计划项（§4.4）。"""

    model_config = ConfigDict(extra='allow')

    plan_id: str = Field(min_length=1, max_length=64, description='计划 id')
    title: str = Field(min_length=1, max_length=200, description='标题')
    horizon: PlanHorizon = Field(description='时间范围 today/week')
    steps: list[str] = Field(default_factory=list, description='步骤')
    actions: list[BriefingAction] = Field(default_factory=list, description='可执行操作')


class BriefingDocument(BaseModel):
    """每日关注简报统一产物（§4.1）。owner_id/agent_id 由凭证回填，period 缺省取当日。"""

    model_config = ConfigDict(extra='allow')

    briefing_id: str | None = Field(default=None, description='ULID（缺省由云端生成）')
    owner_id: str | None = Field(default=None, description='主人 id（由凭证回填，不信任入参）')
    agent_id: str | None = Field(default=None, description='产出主脑 id（由凭证回填）')
    generated_at: int | None = Field(default=None, description='产出时间 epoch ms（缺省云端补）')
    period: str | None = Field(default=None, description='覆盖周期 YYYY-MM-DD（缺省取当日）')
    state: BriefingState = Field(default='ready', description='状态')
    summary: str = Field(min_length=1, max_length=2000, description='一句话总览（Hero 副标题，必填）')
    focus_items: list[FocusItem] = Field(default_factory=list, description='关注项')
    plans: list[PlanItem] = Field(default_factory=list, description='计划项')


class BriefingLatestResponse(SchemaBase):
    """读最新简报响应（app）：空态如实返回 has_briefing=False，不造卡。"""

    has_briefing: bool = Field(description='是否有简报（无则前端引导生成）')
    state: str | None = Field(default=None, description='状态 generating/ready/failed')
    period: str | None = Field(default=None, description='周期 YYYY-MM-DD')
    generated_at: int | None = Field(default=None, description='产出时间 epoch ms')
    document: dict | None = Field(default=None, description='完整 BriefingDocument（无则 null）')


class BriefingDismissParam(SchemaBase):
    """关注项 dismiss/已处理 入参（app）。"""

    period: str = Field(description='所属简报周期 YYYY-MM-DD')
    action: str = Field(default='dismiss', description='dismiss/done')
    source_ref: str | None = Field(default=None, description='关注项溯源 source.ref（去重用）')
    note: str | None = Field(default=None, description='备注（可空）')
