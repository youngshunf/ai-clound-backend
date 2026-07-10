"""创作运营用户端（owner）API 请求模型（设计 00 §6 + 91 §M8）。

owner 面给主人在 WebUI 操作（建项目/设画像/备稿/审核发布/复盘），经 daemon 薄代理调用（铁律）。
写类参数走 Pydantic 校验（fail-fast），与 Agent 工具面（gateway_internal handler 收裸 dict）互补。
身份恒取自 Owner JWT（request.user.id），绝不从 body 读身份。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class CreateProjectParam(SchemaBase):
    name: str = Field(min_length=1, max_length=120, description='项目（号）名称')
    description: str | None = Field(default=None, max_length=2000)
    primary_platform: str | None = Field(default=None, max_length=40, description='主平台 xiaohongshu/douyin/...')
    pipeline_mode: str = Field(default='semi-auto', description='流水线自主度 manual/semi-auto/auto')
    playbook_id: int | None = Field(default=None, description='绑定打法模板')
    assignee_agent_id: str | None = Field(default=None, max_length=128, description='默认承接分身 hasn_id')


class UpdateProjectParam(SchemaBase):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    primary_platform: str | None = Field(default=None, max_length=40)
    pipeline_mode: str | None = Field(default=None)
    playbook_id: int | None = Field(default=None)
    assignee_agent_id: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, description='active/paused/archived')


class ReassignProjectParam(SchemaBase):
    new_assignee: str = Field(description='新负责人主人 hasn_id（须为本企业 approved 成员）', max_length=128)


class SetProfileParam(SchemaBase):
    """设置/更新项目画像（1:1 upsert）。字段子集，service 白名单校验。"""

    fields: dict[str, Any] = Field(description='画像字段（niche/persona/target_audience/pillars 等）')


class AddAccountParam(SchemaBase):
    platform: str = Field(min_length=1, max_length=40, description='平台 xiaohongshu/douyin/...')
    fields: dict[str, Any] | None = Field(default=None, description='账号字段（nickname/home_url/...）')


class UpdateAccountParam(SchemaBase):
    # 人手动改账号资料 + 手填指标（昵称/uid/主账号/粉丝/获赞/作品数…），走 account.update（§6.3）。
    fields: dict[str, Any] = Field(
        description='账号可改字段（nickname/uid/is_primary/home_url/followers/total_likes/total_posts…）'
    )


class LogCompetitorParam(SchemaBase):
    name: str = Field(min_length=1, max_length=120, description='竞品账号名')
    fields: dict[str, Any] | None = Field(default=None, description='竞品字段（platform/home_url/note/...）')


class UpdateCompetitorParam(SchemaBase):
    # 人手动回填竞品调研结果（粉丝/作品数/风格/优势…），走 competitor.update（§6.4）。
    fields: dict[str, Any] = Field(
        description='竞品可改字段（follower_count/works_count/avg_likes/content_style/strengths…）'
    )


class AddMediaParam(SchemaBase):
    # 登记素材（§6.7）：二进制走 hasn://asset/ 引用（禁 base64）；type ∈ image/video/audio/template。
    type: str = Field(min_length=1, max_length=20, description='素材类型 image/video/audio/template')
    asset_uri: str = Field(min_length=1, max_length=500, description='hasn://asset/{id} 私有桶引用（禁 base64）')
    fields: dict[str, Any] | None = Field(
        default=None, description='filename/file_size/width/height/duration/thumbnail_uri/tags/description'
    )


class UpdateMediaParam(SchemaBase):
    # 改素材元信息（标签/描述/文件名/缩略图，§6.7）。
    fields: dict[str, Any] = Field(description='素材可改字段（filename/tags/description/thumbnail_uri）')


class CreateDraftParam(SchemaBase):
    # 建草稿（§6.8）：快速记灵感/半成品，不进正式内容流水线；title 必填。
    title: str = Field(min_length=1, max_length=200, description='草稿标题')
    fields: dict[str, Any] | None = Field(
        default=None, description='content/media[]（hasn://asset）/tags[]/target_platforms[]'
    )


class UpdateDraftParam(SchemaBase):
    # 改草稿（标题/正文/素材/标签/目标平台，§6.8）。
    fields: dict[str, Any] = Field(description='草稿可改字段（title/content/media[]/tags[]/target_platforms[]）')


class SuggestTopicsParam(SchemaBase):
    """主人手动补选题（与分身 suggest 互补；批量入选题池）。"""

    topics: list[dict[str, Any]] = Field(description='选题列表（title/angle/reason/...）')
    batch_date: str | None = Field(default=None, description='批次日期 YYYY-MM-DD')


class AddTopicParam(SchemaBase):
    """主人手动加一条选题（§6.6「手动加选题」；与分身单条加共用 add_topic）。"""

    title: str = Field(min_length=1, max_length=200, description='选题标题')
    reason: str | None = Field(default=None, description='选题理由/背景')
    angle: str | None = Field(default=None, description='一条创意角度（存入 creative_angles）')


class CreateContentParam(SchemaBase):
    project_id: int = Field(description='归属项目')
    title: str = Field(min_length=1, max_length=200)
    content_tracks: str = Field(default='article', description='形态 article/short_video/note/...')
    target_platforms: list[str] | None = Field(default=None)
    topic_id: int | None = Field(default=None)
    viral_pattern_id: int | None = Field(default=None)
    playbook_id: int | None = Field(default=None)
    pipeline_mode: str | None = Field(default=None)


class UpdateContentParam(SchemaBase):
    status: str | None = Field(default=None, description='状态机迁移目标态')
    title: str | None = Field(default=None, max_length=200)
    review_status: str | None = Field(default=None, description='pending/approved/rejected')
    review_note: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] | None = Field(default=None, description='元数据（hashtags 等）')


class SaveStageParam(SchemaBase):
    stage: str = Field(min_length=1, max_length=40, description='research/outline/first_draft/final_draft/cover/...')
    content_text: str | None = Field(default=None)
    asset_refs: list[str] | None = Field(default=None, description='素材引用（hasn://asset/...）')
    source_type: str = Field(default='human', description='ai_generated/human/mixed（owner 备稿默认 human）')


class SubmitPublishParam(SchemaBase):
    content_id: int = Field(description='待发内容')
    account_id: int = Field(description='目标平台账号')
    platform: str | None = Field(default=None, max_length=40)
    method: str = Field(default='manual_assist', description='发布方式（P0 manual_assist）')
    publish_note: str | None = Field(default=None, max_length=2000, description='发布建议（时间/置顶评论/...）')


class MarkPublishedParam(SchemaBase):
    publish_url: str | None = Field(default=None, max_length=1000, description='主人手动发布后回填的链接')


class UpdateMetricsParam(SchemaBase):
    metrics: dict[str, Any] = Field(description='数据指标（views/likes/new_followers/...）')


class LogInsightParam(SchemaBase):
    project_id: int = Field(description='归属项目')
    insight_type: str = Field(min_length=1, max_length=40, description='复盘类型 content/pillar/cadence/...')
    summary: str = Field(min_length=1, max_length=4000, description='结论摘要')
    period: str | None = Field(default=None, description='复盘周期 weekly/monthly/...')
    evidence_json: dict[str, Any] | None = Field(default=None, description='证据（数据切片/对比）')
    proposed_action: dict[str, Any] | None = Field(
        default=None, description='建议动作（pillar_weight_delta/new_viral_pattern/playbook_patch）'
    )
    confidence: float | None = Field(default=None, ge=0, le=1, description='置信度')
