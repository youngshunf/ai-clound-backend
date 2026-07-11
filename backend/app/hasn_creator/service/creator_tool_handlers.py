"""创作运营 AI-Native 工具 handler（云端 gateway_internal）。

设计原则（与 community/knowledge/growth 一致）：创作运营是**纯云端业务应用**（定位/创作/审核/发布/
复盘/进化，无任何本地文件/电脑操作），其 `hasn.creator.*` 工具一律走**云端 MCP**——由
`ai_native_runtime_gateway` 在 `transport=gateway_internal` 时进程内直调本文件 handler，handler 再直调
hasn_creator service。**不经 hasn-node 本地 hasn-mcp 注册、不经 daemon Agent 工具代理**（那是 task/deck
等有本地理由的应用的模式）。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 身份恒取自 Agent JWT claims（`owner_user_id`/`agent_hasn_id`/`owner_hasn_id`），绝不从入参读身份；
- 企业化裁剪经 `CreatorScope`（主编见全部 / 运营见 assignee=自己），写类授权由 service 强制；
- 返回**裸 data**（gateway 负责信封/审计），与 community/knowledge/growth handler 一致。

蓝本：`app/hasn_growth/service/growth_tool_handlers.py`（同一套 service 调用，去 HTTP 壳）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_creator.service.creator_service import creator_service
from backend.app.hasn_creator.service.scope_context import CreatorScope, resolve_creator_scope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


async def _scope(db: AsyncSession, agent: AgentTokenPayload, view: str = 'team') -> CreatorScope:
    """分身代主人解析创作上下文：身份恒取自 JWT，assignee 键为主人 hasn_id。"""
    return await resolve_creator_scope(db, user_id=agent.owner_user_id, owner_hasn_id=agent.owner_hasn_id, view=view)


def _int(payload: dict[str, Any], key: str) -> int:
    return int(payload[key])


# ---------------- 平台目录（S1）----------------


async def handle_platform_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """列平台目录（选择制，含主页根 URL/主页模板/指标口径）；分身选平台/取主页模板时调。"""
    items = await creator_service.list_platforms(db)
    return {'items': items}


# ---------------- 项目 ----------------


async def handle_project_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    items = await creator_service.list_projects(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        status=input_payload.get('status'),
        limit=int(input_payload.get('limit', 50)),
    )
    return {'items': items, 'scope': scope.to_meta()}


async def handle_project_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.get_project(
        db, user_id=agent.owner_user_id, scope=scope, project_id=_int(input_payload, 'project_id')
    )


async def handle_project_create(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.create_project(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        name=str(input_payload['name']),
        description=input_payload.get('description'),
        primary_platform=input_payload.get('primary_platform'),
        pipeline_mode=input_payload.get('pipeline_mode') or 'semi-auto',
        playbook_id=input_payload.get('playbook_id'),
    )


# ---------------- 画像 ----------------


async def handle_profile_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.get_profile(
        db, user_id=agent.owner_user_id, scope=scope, project_id=_int(input_payload, 'project_id')
    )


async def handle_profile_analyze(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.analyze_profile(
        db, user_id=agent.owner_user_id, scope=scope, project_id=_int(input_payload, 'project_id')
    )


async def handle_profile_set(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.set_profile(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        fields=dict(input_payload.get('fields') or {}),
    )


# ---------------- 账号 / 竞品 ----------------


async def handle_account_add(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.add_account(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        platform=str(input_payload['platform']),
        fields=input_payload.get('fields'),
    )


async def handle_account_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.list_accounts(
        db, user_id=agent.owner_user_id, scope=scope, project_id=_int(input_payload, 'project_id')
    )
    return {'items': items}


async def handle_account_update(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_account(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        account_id=_int(input_payload, 'account_id'),
        fields=dict(input_payload.get('fields') or {}),
    )


async def handle_account_update_metrics(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_account_metrics(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        account_id=_int(input_payload, 'account_id'),
        metrics=dict(input_payload.get('metrics') or {}),
    )


async def handle_account_works_upsert(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.upsert_works(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        source_type='own',
        owner_ref_id=_int(input_payload, 'account_id'),
        items=list(input_payload.get('works') or []),
    )


async def handle_account_works_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.list_works(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        source_type='own',
        owner_ref_id=_int(input_payload, 'account_id'),
        limit=int(input_payload.get('limit', 100)),
    )
    return {'items': items}


async def handle_competitor_log(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.log_competitor(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        name=str(input_payload['name']),
        fields=input_payload.get('fields'),
    )


async def handle_competitor_update(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_competitor(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        competitor_id=_int(input_payload, 'competitor_id'),
        fields=dict(input_payload.get('fields') or {}),
    )


async def handle_competitor_works_upsert(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.upsert_works(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        source_type='competitor',
        owner_ref_id=_int(input_payload, 'competitor_id'),
        items=list(input_payload.get('works') or []),
    )


# ---------------- 素材库 / 草稿箱（S6）----------------


async def handle_media_add(db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.add_media(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        media_type=str(input_payload['type']),
        asset_uri=str(input_payload['asset_uri']),
        fields=input_payload.get('fields'),
    )


async def handle_media_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.list_media(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        media_type=input_payload.get('type'),
        limit=int(input_payload.get('limit', 100)),
    )
    return {'items': items}


async def handle_media_update(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_media(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        media_id=_int(input_payload, 'media_id'),
        fields=dict(input_payload.get('fields') or {}),
    )


async def handle_media_delete(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.delete_media(
        db, user_id=agent.owner_user_id, scope=scope, media_id=_int(input_payload, 'media_id')
    )


async def handle_draft_create(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.create_draft(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        title=str(input_payload['title']),
        fields=input_payload.get('fields'),
    )


async def handle_draft_update(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_draft(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        draft_id=_int(input_payload, 'draft_id'),
        fields=dict(input_payload.get('fields') or {}),
    )


async def handle_draft_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.list_drafts(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        limit=int(input_payload.get('limit', 100)),
    )
    return {'items': items}


async def handle_draft_delete(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.delete_draft(
        db, user_id=agent.owner_user_id, scope=scope, draft_id=_int(input_payload, 'draft_id')
    )


async def handle_draft_promote(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.promote_draft(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        draft_id=_int(input_payload, 'draft_id'),
        created_by_agent_id=agent.agent_hasn_id,
    )


# ---------------- 选题 ----------------


async def handle_topic_suggest(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.suggest_topics(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        topics=list(input_payload.get('topics') or []),
        batch_date=input_payload.get('batch_date'),
    )
    return {'items': items}


async def handle_topic_add(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """分身单条加选题（§6.6）——与主人手动加选题共用 add_topic，单条落库。"""
    scope = await _scope(db, agent)
    item = await creator_service.add_topic(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        title=str(input_payload.get('title') or ''),
        reason=input_payload.get('reason'),
        angle=input_payload.get('angle'),
    )
    return item


# ---------------- 内容 / 阶段产出 ----------------


async def handle_content_create(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.create_content(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        title=str(input_payload['title']),
        content_tracks=input_payload.get('content_tracks') or 'article',
        target_platforms=input_payload.get('target_platforms'),
        topic_id=input_payload.get('topic_id'),
        viral_pattern_id=input_payload.get('viral_pattern_id'),
        playbook_id=input_payload.get('playbook_id'),
        pipeline_mode=input_payload.get('pipeline_mode'),
        created_by_agent_id=agent.agent_hasn_id,
    )


async def handle_content_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    items = await creator_service.list_content(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=input_payload.get('project_id'),
        status=input_payload.get('status'),
        review_status=input_payload.get('review_status'),
        limit=int(input_payload.get('limit', 50)),
    )
    return {'items': items, 'scope': scope.to_meta()}


async def handle_content_get(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.get_content(
        db, user_id=agent.owner_user_id, scope=scope, content_id=_int(input_payload, 'content_id')
    )


async def handle_content_update(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_content(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        content_id=_int(input_payload, 'content_id'),
        status=input_payload.get('status'),
        title=input_payload.get('title'),
        review_status=input_payload.get('review_status'),
        review_note=input_payload.get('review_note'),
        metadata=input_payload.get('metadata'),
    )


async def handle_content_stage_save(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.save_stage(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        content_id=_int(input_payload, 'content_id'),
        stage=str(input_payload['stage']),
        content_text=input_payload.get('content_text'),
        asset_refs=input_payload.get('asset_refs'),
        source_type=input_payload.get('source_type') or 'ai_generated',
    )


# ---------------- 发布 ----------------


async def handle_publish_submit(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.submit_publish(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        content_id=_int(input_payload, 'content_id'),
        account_id=_int(input_payload, 'account_id'),
        platform=input_payload.get('platform'),
        method=input_payload.get('method') or 'manual_assist',
        publish_note=input_payload.get('publish_note'),
    )


async def handle_publish_list(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.list_publish(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=input_payload.get('project_id'),
        content_id=input_payload.get('content_id'),
        status=input_payload.get('status'),
        limit=int(input_payload.get('limit', 100)),
    )
    return {'items': items}


async def handle_publish_update_metrics(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.update_metrics(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        publish_id=_int(input_payload, 'publish_id'),
        metrics=dict(input_payload.get('metrics') or {}),
    )


# ---------------- 进化 / 爆款 / 复盘 ----------------


async def handle_insight_log(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    return await creator_service.log_insight(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=_int(input_payload, 'project_id'),
        insight_type=str(input_payload['insight_type']),
        summary=str(input_payload['summary']),
        period=input_payload.get('period'),
        evidence_json=input_payload.get('evidence_json'),
        proposed_action=input_payload.get('proposed_action'),
        confidence=input_payload.get('confidence'),
        created_by_agent_id=agent.agent_hasn_id,
    )


async def handle_pattern_search(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent)
    items = await creator_service.search_patterns(
        db,
        user_id=agent.owner_user_id,
        scope=scope,
        project_id=input_payload.get('project_id'),
        pattern_type=input_payload.get('pattern_type'),
        query=input_payload.get('query'),
        limit=int(input_payload.get('limit', 30)),
    )
    return {'items': items}


async def handle_report_overview(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    scope = await _scope(db, agent, view=str(input_payload.get('view', 'team')))
    return await creator_service.report_overview(
        db, user_id=agent.owner_user_id, scope=scope, project_id=input_payload.get('project_id')
    )
