"""技能市场 Agent JWT 权威 Interface（DOC15-95 M1）。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query

from backend.app.marketplace.schema.agent_marketplace import (
    AgentMarketplacePage,
    AgentMarketplacePublishRequest,
)
from backend.app.marketplace.service.agent_publish_service import agent_publish_service
from backend.app.marketplace.service.agent_installation_service import agent_installation_service
from backend.app.marketplace.service.agent_marketplace_service import agent_marketplace_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

SourceType = Literal['huanxing', 'clawhub', 'github', 'user']
Language = Literal['zh-CN', 'en-US', 'zh', 'en']
Sort = Literal['relevance', 'downloads', 'updated']


async def _publish(
    *,
    agent: AgentTokenPayload,
    db: CurrentSessionTransaction,
    resource_kind: Literal['skill', 'template', 'skill_pack'],
    payload: AgentMarketplacePublishRequest,
    idempotency_key: str,
    work_session_id: str | None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_publish_service.publish(
            db,
            identity=agent,
            resource_kind=resource_kind,
            payload=payload,
            idempotency_key=idempotency_key,
            work_session_id=work_session_id,
        )
    )


@router.post('/publish/skills', summary='[Agent] 从 Owner 资产发布技能草稿')
async def publish_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    payload: AgentMarketplacePublishRequest,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    work_session_id: Annotated[
        str | None,
        Header(alias='X-Hasn-Work-Session-Id', max_length=64),
    ] = None,
) -> ResponseSchemaModel[dict]:
    return await _publish(
        agent=agent,
        db=db,
        resource_kind='skill',
        payload=payload,
        idempotency_key=idempotency_key,
        work_session_id=work_session_id,
    )


@router.post('/publish/templates', summary='[Agent] 从 Owner 资产发布分身模板草稿')
async def publish_template(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    payload: AgentMarketplacePublishRequest,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    work_session_id: Annotated[
        str | None,
        Header(alias='X-Hasn-Work-Session-Id', max_length=64),
    ] = None,
) -> ResponseSchemaModel[dict]:
    return await _publish(
        agent=agent,
        db=db,
        resource_kind='template',
        payload=payload,
        idempotency_key=idempotency_key,
        work_session_id=work_session_id,
    )


@router.post('/publish/skill-packs', summary='[Agent] 从 Owner 资产发布技能包草稿')
async def publish_skill_pack(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    payload: AgentMarketplacePublishRequest,
    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)],
    work_session_id: Annotated[
        str | None,
        Header(alias='X-Hasn-Work-Session-Id', max_length=64),
    ] = None,
) -> ResponseSchemaModel[dict]:
    return await _publish(
        agent=agent,
        db=db,
        resource_kind='skill_pack',
        payload=payload,
        idempotency_key=idempotency_key,
        work_session_id=work_session_id,
    )


@router.get('/installed', summary='[Agent] 读取技能与技能包权威期望态')
async def get_installed(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_installation_service.get_installed(
            db,
            identity=agent,
        )
    )


@router.put('/installed/skills/{resource_id:path}', summary='[Agent] 幂等安装技能')
async def install_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    resource_id: str,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_installation_service.install_skill(
            db,
            identity=agent,
            resource_id=resource_id,
        )
    )


@router.delete('/installed/skills/{resource_id:path}', summary='[Agent] 幂等卸载直接技能引用')
async def uninstall_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    resource_id: str,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_installation_service.uninstall_skill(
            db,
            identity=agent,
            resource_id=resource_id,
        )
    )


@router.put('/installed/skill-packs/{resource_id:path}', summary='[Agent] 幂等安装冻结技能包')
async def install_skill_pack(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    resource_id: str,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_installation_service.install_skill_pack(
            db,
            identity=agent,
            package_id=resource_id,
            version=version,
        )
    )


@router.delete('/installed/skill-packs/{resource_id:path}', summary='[Agent] 幂等卸载冻结技能包')
async def uninstall_skill_pack(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    resource_id: str,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_installation_service.uninstall_skill_pack(
            db,
            identity=agent,
            package_id=resource_id,
            version=version,
        )
    )


@router.get('/skills', summary='[Agent] 搜索可及技能')
async def search_skills(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    source_type: SourceType | None = None,
    namespace: Annotated[str | None, Query(max_length=160)] = None,
    language: Language | None = None,
    sort: Sort = 'relevance',
    cursor: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseSchemaModel[AgentMarketplacePage]:
    data = await agent_marketplace_service.search_skills(
        db,
        agent=agent,
        query=query,
        category=category,
        tags=tags,
        source_type=source_type,
        namespace=namespace,
        language=language,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=AgentMarketplacePage.model_validate(data))


@router.get('/skills/{resource_id:path}', summary='[Agent] 读取技能详情')
async def get_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    resource_id: str,
    language: Language | None = None,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_marketplace_service.get_skill(
            db,
            agent=agent,
            resource_id=resource_id,
            language=language,
            version=version,
        )
    )


@router.get('/templates', summary='[Agent] 搜索可及分身模板')
async def search_templates(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    source_type: SourceType | None = None,
    namespace: Annotated[str | None, Query(max_length=160)] = None,
    language: Language | None = None,
    sort: Sort = 'relevance',
    cursor: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseSchemaModel[AgentMarketplacePage]:
    data = await agent_marketplace_service.search_templates(
        db,
        agent=agent,
        kind='template',
        query=query,
        category=category,
        tags=tags,
        source_type=source_type,
        namespace=namespace,
        language=language,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=AgentMarketplacePage.model_validate(data))


@router.get('/templates/{resource_id:path}', summary='[Agent] 读取分身模板详情')
async def get_template(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    resource_id: str,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_marketplace_service.get_template(
            db,
            agent=agent,
            kind='template',
            resource_id=resource_id,
            version=version,
        )
    )


@router.get('/skill-packs', summary='[Agent] 搜索可及技能包')
async def search_skill_packs(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    source_type: SourceType | None = None,
    namespace: Annotated[str | None, Query(max_length=160)] = None,
    language: Language | None = None,
    sort: Sort = 'relevance',
    cursor: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ResponseSchemaModel[AgentMarketplacePage]:
    data = await agent_marketplace_service.search_templates(
        db,
        agent=agent,
        kind='skill_pack',
        query=query,
        category=category,
        tags=tags,
        source_type=source_type,
        namespace=namespace,
        language=language,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=AgentMarketplacePage.model_validate(data))


@router.get('/skill-packs/{resource_id:path}', summary='[Agent] 读取技能包固定版本')
async def get_skill_pack(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    resource_id: str,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> ResponseSchemaModel[dict]:
    return response_base.success(
        data=await agent_marketplace_service.get_template(
            db,
            agent=agent,
            kind='skill_pack',
            resource_id=resource_id,
            version=version,
        )
    )
