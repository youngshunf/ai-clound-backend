import re

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.model import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.schema.hasn_agents import (
    AgentRuntimeConfig,
    AgentSnapshot,
    AgentSyncRequest,
    AgentSyncResponse,
    CloudCreateAgentRequest,
    CloudCreateAgentResponse,
    CreateHasnAgentsParam,
    DeleteHasnAgentsParam,
    UpdateAgentBindingRequest,
    UpdateAgentProfileRequest,
    UpdateAgentProfileResponse,
    UpdateHasnAgentsParam,
)
from backend.app.marketplace.service.common_skills_service import get_common_skill_snapshot
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


class AgentProfileGateway(Protocol):
    async def owns_owner(self, db: AsyncSession, *, owner_id: str, user_id: int) -> bool: ...
    async def get_template(self, db: AsyncSession, *, template_id: str) -> Any | None: ...
    async def create_agent(self, db: AsyncSession, payload: dict[str, Any]) -> tuple[Any, str | None, bool]: ...
    async def is_display_name_taken(self, db: AsyncSession, display_name: str) -> bool: ...
    async def resolve_unique_display_name(
        self, db: AsyncSession, *, desired: str, candidates: list[str] | None = None
    ) -> str: ...
    async def list_owner_agents(
        self, db: AsyncSession, *, owner_id: str, after_revision: int | None = None
    ) -> list[Any]: ...
    async def append_agent_sync_event(
        self, db: AsyncSession, *, owner_id: str, agent: Any, event_type: str
    ) -> None: ...


class SqlAlchemyAgentProfileGateway:
    async def owns_owner(self, db: AsyncSession, *, owner_id: str, user_id: int) -> bool:
        import sqlalchemy as sa

        from backend.app.hasn.model import HasnHumans

        result = await db.execute(
            sa
            .select(HasnHumans.id)
            .where(
                HasnHumans.hasn_id == owner_id,
                HasnHumans.user_id == user_id,
                HasnHumans.status == 'active',
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_template(self, db: AsyncSession, *, template_id: str) -> Any | None:
        """读取 Agent 创建模板（权威源 = 活表 marketplace_template）。

        marketplace_template 由 github_app_sync_service 从 huanxing-hub 同步，含
        SOUL/AGENTS/USER 内容（P1 新增列）与 skill_dependencies。这里返回一个归一化
        适配对象，使 _merge_agent_create_payload 与具体模板表解耦（不再读空表
        hasn_agent_templates）。
        """
        from types import SimpleNamespace

        import sqlalchemy as sa

        from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
        from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion

        tpl = (
            await db.execute(
                sa
                .select(MarketplaceTemplate)
                .where(
                    MarketplaceTemplate.template_id == template_id,
                    MarketplaceTemplate.status == 'published',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if tpl is None:
            return None

        version = (
            await db.execute(
                sa
                .select(MarketplaceTemplateVersion.version)
                .where(
                    MarketplaceTemplateVersion.template_id == template_id,
                    MarketplaceTemplateVersion.is_latest.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        skill_ids = [s.strip() for s in (tpl.skill_dependencies or '').split(',') if s.strip()]

        return SimpleNamespace(
            template_id=tpl.template_id,
            template_version=version,
            agent_name=tpl.slug,
            avatar=tpl.icon_url,
            default_description=tpl.description,
            default_skills=skill_ids,
            default_soul_md=tpl.soul_md,
            default_agents_md=tpl.agents_md,
            default_user_md=tpl.user_md,
            default_memory_md=getattr(tpl, 'memory_md', None),
            default_runtime_type='hermes',
        )

    async def _ensure_unique_agent_name(self, db: AsyncSession, *, owner_id: str, base_slug: str) -> str:
        """同 owner 下保证 agent_name 唯一：base 被占用则依次试 base-2 / base-3 …。

        令「同模板重复创建」产出独立 Agent——register_hasn_agent 按 (owner_id, agent_name)
        查重幂等，slug 不唯一会把第二次创建当成更新覆盖上一个 Agent（star_id/profile 目录复用）。
        agent_name 列上限 30，故 root 截断到 22 留出后缀空间。
        """
        import sqlalchemy as sa

        async def _taken(name: str) -> bool:
            row = (
                await db.execute(
                    sa.select(HasnAgents.id)
                    .where(HasnAgents.owner_id == owner_id, HasnAgents.agent_name == name)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row is not None

        if not await _taken(base_slug):
            return base_slug
        root = base_slug[:22].rstrip('-_') or 'agent'
        for suffix in range(2, 1000):
            candidate = f'{root}-{suffix}'
            if not await _taken(candidate):
                return candidate
        import uuid

        return f'{root}-{uuid.uuid4().hex[:6]}'

    @staticmethod
    async def is_display_name_taken(db: AsyncSession, display_name: str) -> bool:
        """display_name 全局唯一（应用层校验）：任一未删除分身占用即视为已占。

        注：不加 DB 唯一约束——存量数据已有重复 display_name（历史默认填领域名），
        硬约束会迁移失败；故收口在创建路径 + 查重端点的应用层。
        """
        import sqlalchemy as sa

        name = (display_name or '').strip()
        if not name:
            return False
        row = (
            await db.execute(
                sa.select(HasnAgents.id)
                .where(HasnAgents.display_name == name, HasnAgents.deleted_at.is_(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        return row is not None

    async def resolve_unique_display_name(
        self, db: AsyncSession, *, desired: str, candidates: list[str] | None = None
    ) -> str:
        """挑一个全局未占用的人名：desired → 候选池按序 → desired+数字后缀。"""
        desired = (desired or '').strip() or 'AI 分身'
        if not await self.is_display_name_taken(db, desired):
            return desired
        for raw in candidates or []:
            cand = (raw or '').strip()
            if cand and cand != desired and not await self.is_display_name_taken(db, cand):
                return cand
        root = desired[:56]
        for suffix in range(2, 1000):
            cand = f'{root}{suffix}'
            if not await self.is_display_name_taken(db, cand):
                return cand
        import uuid

        return f'{root}-{uuid.uuid4().hex[:4]}'

    async def create_agent(self, db: AsyncSession, payload: dict[str, Any]) -> tuple[Any, str | None, bool]:
        from backend.app.hasn.service.hasn_auth import register_hasn_agent

        # 同模板可重复创建：先把 agent_name 在该 owner 下唯一化（assistant→assistant-2…），
        # 避免撞 register_hasn_agent 的 (owner_id, agent_name) 幂等分支而覆盖上一个 Agent。
        agent_name = await self._ensure_unique_agent_name(
            db, owner_id=payload['owner_id'], base_slug=payload['agent_name']
        )

        # display_name 全局唯一：webui 创建前已查重，此处兜底并发竞态——撞名则按候选池/后缀
        # 挑首个空闲名落库（返回快照即真实存名，daemon/webui 据此回写并按需提示）。
        display_name = await self.resolve_unique_display_name(
            db,
            desired=payload['display_name'],
            candidates=payload.get('display_name_candidates'),
        )

        result = await register_hasn_agent(
            db=db,
            owner_hasn_id=payload['owner_id'],
            agent_name=agent_name,
            display_name=display_name,
            profession=payload.get('profession'),
            agent_type=payload.get('agent_type') or 'desktop',
            node_id=payload.get('node_id'),
            role=payload.get('role') or 'specialist',
            description=payload.get('description'),
            capabilities=payload.get('capabilities'),
            created_via='client',
            avatar=payload.get('avatar'),
            template_id=payload.get('template_id'),
            template_version=payload.get('template_version'),
            skills=payload.get('skills'),
            soul_md=payload.get('soul_md'),
            agents_md=payload.get('agents_md'),
            user_md=payload.get('user_md'),
            memory_md=payload.get('memory_md'),
        )
        agent = result['agent']
        # 只用非 None 值覆盖，避免幂等命中已有 Agent 时把已存的 profile 字段清空。
        # 注意：soul/agents/user/memory_md 不在此列——它们由 register_hasn_agent 建档即渲染占位符
        # 后写入（权威完整），此处再用 payload 的模板原文覆盖会把 {{}} 灌回去，破坏权威 profile。
        for attr in ('template_id', 'template_version', 'skills'):
            value = payload.get(attr)
            if value is not None and hasattr(agent, attr):
                setattr(agent, attr, value)
        if hasattr(agent, 'profile_source'):
            agent.profile_source = 'cloud'
        if not getattr(agent, 'profile_revision', None):
            agent.profile_revision = 1
        # 运行位置（双形态 Runtime，设计 08/02）：仅新建分身按请求落库（默认 local）；幂等命中
        # 已有分身时不改位置——切换位置是 detach + 重新 bind 的显式动作，不在创建路径覆盖。
        if not bool(result.get('already_exists')) and hasattr(agent, 'runtime_location'):
            location = payload.get('runtime_location') or 'local'
            agent.runtime_location = location if location in ('local', 'cloud') else 'local'
        await db.flush()
        return agent, result.get('agent_key'), bool(result.get('already_exists'))

    async def list_owner_agents(
        self, db: AsyncSession, *, owner_id: str, after_revision: int | None = None
    ) -> list[Any]:
        import sqlalchemy as sa

        stmt = sa.select(HasnAgents).where(HasnAgents.owner_id == owner_id)
        if after_revision is not None and hasattr(HasnAgents, 'profile_revision'):
            stmt = stmt.where(HasnAgents.profile_revision > after_revision)
        stmt = stmt.order_by(HasnAgents.id.asc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def append_agent_sync_event(self, db: AsyncSession, *, owner_id: str, agent: Any, event_type: str) -> None:
        from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

        await SqlAlchemySyncGateway()._append_sync_event(
            db,
            owner_id=owner_id,
            hasn_id=agent.hasn_id,
            event_type=event_type,
            aggregate_type='agent',
            aggregate_id=agent.hasn_id,
            payload={'agent': _agent_snapshot(agent).model_dump(mode='json')},
        )


class HasnAgentProfileService:
    def __init__(self, gateway: AgentProfileGateway | None = None) -> None:
        self.gateway = gateway or SqlAlchemyAgentProfileGateway()

    async def create_cloud_first(
        self, db: AsyncSession, request: CloudCreateAgentRequest, *, user_id: int | None = None
    ) -> CloudCreateAgentResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        template = await self.gateway.get_template(db, template_id=request.template_id) if request.template_id else None
        payload = _merge_agent_create_payload(request, template)
        agent, agent_key, already_exists = await self.gateway.create_agent(db, payload)
        await self.gateway.append_agent_sync_event(
            db,
            owner_id=request.owner_id,
            agent=agent,
            event_type='agent.updated' if already_exists else 'agent.created',
        )

        # 为新创建的 Agent 插入默认权限配置并签发 JWT
        agent_token_info = None
        if not already_exists:
            try:
                from backend.app.hasn.schema.hasn_agents import AgentTokenInfo
                from backend.common.security.agent_jwt import (
                    create_agent_access_token,
                    create_default_agent_scopes,
                    get_agent_scopes_cached,
                )

                # 插入默认权限配置
                await create_default_agent_scopes(db, agent.hasn_id, request.owner_id)

                # 获取权限配置
                scopes_config = await get_agent_scopes_cached(agent.hasn_id, db)
                scopes = scopes_config.get('scopes', [])

                # 签发 Agent JWT
                agent_token = await create_agent_access_token(
                    agent_hasn_id=agent.hasn_id,
                    agent_name=agent.display_name or agent.agent_name,
                    owner_hasn_id=request.owner_id,
                    owner_user_id=user_id or 0,
                    scopes=scopes,
                )

                agent_token_info = AgentTokenInfo(
                    access_token=agent_token.access_token,
                    scopes=agent_token.scopes,
                )
            except Exception as e:
                from backend.common.log import log

                log.error(f'为 Agent {agent.hasn_id} 签发 JWT 失败: {e}')
                # JWT 签发失败不影响 Agent 创建

        return CloudCreateAgentResponse(
            agent=_agent_snapshot(agent),
            agent_key=agent_key,
            agent_token=agent_token_info,
            already_exists=already_exists,
        )

    async def update_profile_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        request: UpdateAgentProfileRequest,
        user_id: int | None = None,
    ) -> UpdateAgentProfileResponse:
        """云端权威更新 Agent profile。

        daemon 调用：云端先落库 → 返回最新快照 → daemon 据此回写本地镜像。
        所有字段都是 partial：只有显式传入的字段才会被写。
        """
        import sqlalchemy as sa

        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)

        agent = (
            await db.execute(
                sa.select(HasnAgents).where(
                    HasnAgents.hasn_id == hasn_id,
                    HasnAgents.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')

        provided = request.model_dump(exclude_unset=True)
        if not provided:
            return UpdateAgentProfileResponse(agent=_agent_snapshot(agent))

        if 'status' in provided and provided['status'] is not None:
            if provided['status'] not in _ALLOWED_STATUS_VALUES:
                raise errors.RequestError(msg=f'ERR_HASN_AGENT_STATUS_INVALID:{provided["status"]}')

        if 'star_id' in provided and provided['star_id'] is not None:
            new_star_id = provided['star_id']
            if new_star_id != agent.star_id:
                conflict = (
                    await db.execute(
                        sa.select(HasnAgents.id).where(
                            HasnAgents.star_id == new_star_id,
                            HasnAgents.id != agent.id,
                        )
                    )
                ).scalar_one_or_none()
                if conflict is not None:
                    raise errors.RequestError(msg='ERR_HASN_AGENT_STAR_ID_TAKEN')
                agent.star_id = new_star_id

        if 'display_name' in provided and provided['display_name'] is not None:
            new_display_name = provided['display_name']
            if new_display_name != agent.display_name:
                # 改名也保 display_name 全局唯一：被他人占用则拒（webui 据错误码再查重/给建议）。
                dn_conflict = (
                    await db.execute(
                        sa.select(HasnAgents.id).where(
                            HasnAgents.display_name == new_display_name,
                            HasnAgents.deleted_at.is_(None),
                            HasnAgents.id != agent.id,
                        )
                    )
                ).scalar_one_or_none()
                if dn_conflict is not None:
                    raise errors.RequestError(msg='ERR_HASN_AGENT_DISPLAY_NAME_TAKEN')
                agent.display_name = new_display_name
        if 'description' in provided:
            agent.description = provided['description']
        if 'avatar' in provided:
            agent.avatar = provided['avatar']
        if 'role' in provided and provided['role'] is not None:
            agent.role = provided['role']
        if 'profession' in provided and provided['profession'] is not None:
            agent.profession = provided['profession']
        if 'tags' in provided and provided['tags'] is not None:
            agent.tags = list(provided['tags'])
        if 'capability_set_id' in provided:
            agent.capability_set_id = provided['capability_set_id']
        if 'persona_ref' in provided:
            agent.persona_ref = provided['persona_ref']
        if 'status' in provided and provided['status'] is not None:
            agent.status = provided['status']

        if hasattr(agent, 'profile_revision'):
            agent.profile_revision = (agent.profile_revision or 1) + 1
        await db.flush()

        await self.gateway.append_agent_sync_event(
            db,
            owner_id=owner_id,
            agent=agent,
            event_type='agent.updated',
        )

        return UpdateAgentProfileResponse(agent=_agent_snapshot(agent))

    async def get_runtime_config(
        self, db: AsyncSession, *, owner_id: str, hasn_id: str, user_id: int | None = None
    ) -> AgentRuntimeConfig:
        """读取 Agent 的 hermes runtime 原生配置（未设项为 None）。

        owner 归属校验：当前用户须拥有该 owner + Agent 须属于该 owner。存量行
        runtime_config_json 为 NULL → 返回全默认（全 None）。
        """
        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)
        raw = getattr(agent, 'runtime_config_json', None) or {}
        return AgentRuntimeConfig.model_validate(raw)

    async def update_runtime_config(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        config: AgentRuntimeConfig,
        user_id: int | None = None,
    ) -> AgentRuntimeConfig:
        """覆盖式更新 Agent 的 hermes runtime 原生配置（云端权威）。

        落库后 bump profile_revision + append `agent.updated` 同步事件（经单一
        chokepoint `_append_sync_event`，advisory lock 串行化 gapless revision），
        daemon 据返回值回写本地镜像并下发 runtime。
        """
        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)
        agent.runtime_config_json = config.model_dump(mode='json')
        if hasattr(agent, 'profile_revision'):
            agent.profile_revision = (agent.profile_revision or 1) + 1
        await db.flush()
        await self.gateway.append_agent_sync_event(
            db,
            owner_id=owner_id,
            agent=agent,
            event_type='agent.updated',
        )
        return AgentRuntimeConfig.model_validate(agent.runtime_config_json or {})

    async def _get_owned_agent(self, db: AsyncSession, *, owner_id: str, hasn_id: str) -> Any:
        """按 (hasn_id, owner_id) 取 Agent；不存在或不归属则 404。"""
        import sqlalchemy as sa

        agent = (
            await db.execute(
                sa.select(HasnAgents).where(
                    HasnAgents.hasn_id == hasn_id,
                    HasnAgents.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')
        return agent

    async def attach_skill_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        skill_id: str,
        user_id: int | None = None,
    ) -> UpdateAgentProfileResponse:
        """为 Agent 装配技能（云端权威）。

        校验：owner 归属 + Agent 存在 + 技能 published/public（命名空间化 ID）。
        写入：把 skill_id 并入 hasn_agents.skills（归一 list[str] 保序去重）→ bump
        profile_revision → append `agent.updated` 同步事件。技能包的实际下载与物化由
        daemon 触发 re-provision、runtime 据权威清单下载完成——云端只持有权威 skill_id 清单。
        幂等：已在清单中则不改动、不 bump、不发事件，直接回快照。
        """
        from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
        from backend.app.marketplace.service.resource_id import parse_resource_id

        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)

        namespace, slug = parse_resource_id(skill_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug_public(db, namespace, slug)
        if skill is None:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_NOT_FOUND')
        # 用规范化资源 ID 入库，避免前后导斜杠等差异造成清单内重复项。
        canonical_id = f'{namespace}/{slug}'

        current = _normalize_skill_ids(agent.skills)
        if canonical_id not in current:
            agent.skills = [*current, canonical_id]
            agent.profile_revision = (agent.profile_revision or 1) + 1
            await db.flush()
            await self.gateway.append_agent_sync_event(
                db,
                owner_id=owner_id,
                agent=agent,
                event_type='agent.updated',
            )

        return UpdateAgentProfileResponse(agent=_agent_snapshot(agent))

    async def detach_skill_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        skill_id: str,
        user_id: int | None = None,
    ) -> UpdateAgentProfileResponse:
        """卸载 Agent 技能（云端权威）。从 skills 清单移除 → bump revision → 同步事件。

        不校验技能是否仍在市场（已下架技能也允许卸载）。幂等：不在清单中则不改动。
        """
        from backend.app.marketplace.service.resource_id import parse_resource_id

        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)

        namespace, slug = parse_resource_id(skill_id)
        canonical_id = f'{namespace}/{slug}'

        current = _normalize_skill_ids(agent.skills)
        if canonical_id in current:
            agent.skills = [sid for sid in current if sid != canonical_id]
            agent.profile_revision = (agent.profile_revision or 1) + 1
            await db.flush()
            await self.gateway.append_agent_sync_event(
                db,
                owner_id=owner_id,
                agent=agent,
                event_type='agent.updated',
            )

        return UpdateAgentProfileResponse(agent=_agent_snapshot(agent))

    async def attach_personal_skill_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        personal_skill_id: str,
        user_id: int,
    ) -> UpdateAgentProfileResponse:
        """把个人技能（个人技能库 SSOT）装配到 Agent（云端权威，SKILLSYNC-C2）。

        与 attach_skill_cloud_first 的区别：校验对象是 marketplace_personal_skill（owner 私有库），
        **不要求**技能是已发布 public 市场技能——这正是"运行时自学/本地上传的私有技能"装配到分身、
        进而跨设备物化的路径。写入：把 personal_skill_id 并入 hasn_agents.skills（保序去重）→ bump
        profile_revision → append 同步事件（触发跨设备 provisioning 物化）。幂等：已在清单则不改动。
        """
        import sqlalchemy as sa

        from backend.app.marketplace.model.marketplace_personal_skill import MarketplacePersonalSkill

        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)

        skill = (
            await db.execute(
                sa.select(MarketplacePersonalSkill).where(
                    (MarketplacePersonalSkill.user_id == user_id)
                    & (MarketplacePersonalSkill.personal_skill_id == personal_skill_id)
                )
            )
        ).scalar_one_or_none()
        if skill is None:
            raise errors.NotFoundError(msg='ERR_PERSONAL_SKILL_NOT_FOUND')

        current = _normalize_skill_ids(agent.skills)
        if skill.personal_skill_id not in current:
            agent.skills = [*current, skill.personal_skill_id]
            agent.profile_revision = (agent.profile_revision or 1) + 1
            await db.flush()
            await self.gateway.append_agent_sync_event(
                db,
                owner_id=owner_id,
                agent=agent,
                event_type='agent.updated',
            )

        return UpdateAgentProfileResponse(agent=_agent_snapshot(agent))

    async def attach_bundle_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        package_id: str,
        version: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """为 Agent 安装技能包 skill_pack（云端权威，实施/91 B2.5）。

        展开成员 skills[] **在云端做**（云端是 skills[] 权威）：解析 skill_pack 版本 → 成员技能
        批量并入 hasn_agents.skills（保序去重）→ 记录已安装包引用进 hasn_agents.skill_bundles
        （按 template_id 去重）→ bump profile_revision → append 同步事件。返回 bundle 快照供 daemon
        回填本地 cache + provision 物化。幂等：成员与包都已在清单中则不改动不 bump。
        """
        import sqlalchemy as sa

        from backend.app.marketplace.service import skill_pack_service

        await self._assert_owner_access(db, owner_id=owner_id, user_id=user_id)
        agent = await self._get_owned_agent(db, owner_id=owner_id, hasn_id=hasn_id)

        # 解析 skill_pack 版本（指定 version 取该版，否则取 is_latest）。
        ver_filter = 'AND v.version = :version' if version else 'AND v.is_latest = true'
        row = (
            await db.execute(
                sa.text(
                    f"""
                    SELECT t.template_id, v.version, v.bundle_slug, v.command_key, v.hermes_yaml,
                           COALESCE(v.content_hash, v.file_hash) AS content_hash
                    FROM public.marketplace_template t
                    JOIN public.marketplace_template_version v ON v.template_id = t.template_id
                    WHERE t.template_id = :package_id
                      AND t.template_type = 'skill_pack'
                      {ver_filter}
                    LIMIT 1
                    """
                ),
                {'package_id': package_id, 'version': version},
            )
        ).mappings().one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_PACK_NOT_FOUND')

        members = skill_pack_service.member_skill_ids(row['hermes_yaml'])
        resolved_version = row['version']

        current_skills = _normalize_skill_ids(agent.skills)
        merged_skills = [*current_skills]
        for member in members:
            if member not in merged_skills:
                merged_skills.append(member)

        current_bundles = list(agent.skill_bundles or [])
        bundle_ids = {b.get('template_id') for b in current_bundles if isinstance(b, dict)}
        bundle_changed = package_id not in bundle_ids
        if bundle_changed:
            current_bundles = [b for b in current_bundles if not (isinstance(b, dict) and b.get('template_id') == package_id)]
            current_bundles.append({'template_id': package_id, 'version': resolved_version})

        if merged_skills != current_skills or bundle_changed:
            agent.skills = merged_skills
            agent.skill_bundles = current_bundles
            agent.profile_revision = (agent.profile_revision or 1) + 1
            await db.flush()
            await self.gateway.append_agent_sync_event(
                db,
                owner_id=owner_id,
                agent=agent,
                event_type='agent.updated',
            )

        return {
            'agent': _agent_snapshot(agent).model_dump(),
            'bundle': {
                'template_id': row['template_id'],
                'version': resolved_version,
                'bundle_slug': row['bundle_slug'],
                'command_key': row['command_key'],
                'hermes_yaml': row['hermes_yaml'],
                'content_hash': row['content_hash'],
                'skill_ids': members,
            },
            'profile_revision': int(agent.profile_revision or 1),
        }

    async def sync_agents(
        self, db: AsyncSession, request: AgentSyncRequest, *, user_id: int | None = None
    ) -> AgentSyncResponse:
        await self._assert_owner_access(db, owner_id=request.owner_id, user_id=user_id)
        agents = await self.gateway.list_owner_agents(
            db,
            owner_id=request.owner_id,
            after_revision=request.after_revision,
        )
        if not request.include_disabled:
            agents = [agent for agent in agents if getattr(agent, 'status', 'active') == 'active']
        snapshots = [_agent_snapshot(agent) for agent in agents]
        # 回填实时在线状态：daemon 换设备登录时据此判断「其他设备是否仍在线持有该
        # agent」——仅当在线持有才跳过自动绑定，离线/未绑定则接管到当前设备。必须用
        # Redis presence（断线即清），不能用持久列 online_status（断线不清零会误判）。
        from backend.app.hasn.service.ws_router import ws_router

        online_map = await ws_router.get_online_map([snapshot.hasn_id for snapshot in snapshots])
        for snapshot in snapshots:
            snapshot.online_status = 'online' if online_map.get(snapshot.hasn_id) else 'offline'
        server_revision = max(
            (snapshot.profile_revision for snapshot in snapshots), default=request.after_revision or 0
        )
        # 公共技能集合修订号（全局，doc12 §3.4）：随 is_common 成员/版本变化而变；
        # daemon 据其变化触发全量活跃绑定 re-provision，Runtime 再拉最新公共技能。
        _, common_skills_revision = await get_common_skill_snapshot(db)
        return AgentSyncResponse(
            owner_id=request.owner_id,
            server_revision=server_revision,
            agents=snapshots,
            common_skills_revision=common_skills_revision,
        )

    async def update_binding(
        self,
        db: AsyncSession,
        hasn_id: str,
        request: UpdateAgentBindingRequest,
        *,
        user_id: int | None = None,
    ) -> AgentSnapshot:
        import sqlalchemy as sa

        from backend.utils.timezone import timezone as tz

        result = await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == hasn_id).limit(1))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise errors.NotFoundError(msg=f'agent {hasn_id} not found')
        if user_id is not None and not await self.gateway.owns_owner(db, owner_id=agent.owner_id, user_id=user_id):
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')
        if request.binding_status not in _ALLOWED_BINDING_STATUS_VALUES:
            raise errors.RequestError(msg=f'ERR_HASN_AGENT_BINDING_STATUS_INVALID:{request.binding_status}')

        now_unix = int(tz.now().timestamp())
        await db.execute(
            sa
            .update(HasnAgents)
            .where(HasnAgents.hasn_id == hasn_id)
            .values(
                binding_node_id=request.binding_node_id,
                binding_status=request.binding_status,
                binding_updated_at=now_unix,
            )
        )
        await db.refresh(agent)
        return _agent_snapshot(agent)

    async def update_heartbeat(
        self,
        db: AsyncSession,
        hasn_id: str,
        request: 'AgentHeartbeatRequest',
        *,
        user_id: int | None = None,
    ) -> 'AgentHeartbeatResponse':
        """更新 agent 心跳状态。"""
        from datetime import datetime

        import sqlalchemy as sa

        from backend.app.hasn.schema.hasn_agents import AgentHeartbeatResponse

        result = await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == hasn_id).limit(1))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise errors.NotFoundError(msg=f'agent {hasn_id} not found')
        if user_id is not None and not await self.gateway.owns_owner(db, owner_id=agent.owner_id, user_id=user_id):
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')

        # 更新在线状态和心跳时间
        await db.execute(
            sa
            .update(HasnAgents)
            .where(HasnAgents.hasn_id == hasn_id)
            .values(
                binding_node_id=request.node_id,
                online_status=request.online_status,
                last_heartbeat_at=datetime.fromtimestamp(request.last_heartbeat_at),
            )
        )
        await db.commit()
        return AgentHeartbeatResponse(success=True)

    async def _assert_owner_access(self, db: AsyncSession, *, owner_id: str, user_id: int | None) -> None:
        if user_id is None:
            return
        if not await self.gateway.owns_owner(db, owner_id=owner_id, user_id=user_id):
            raise errors.AuthorizationError(msg='ERR_HASN_OWNER_ACCESS_DENIED')


# 公共技能不再在建 Agent 时持久化进 hasn_agents.skills（doc12 §3.3 改为读取时叠加）：
# 由 `GET /api/v1/hasn/agent/profile` 出参把 is_common 集合并入技能清单，成员/版本变化
# 对全量 Agent 自动生效、零回填。建 Agent 只存 Agent 自装技能。


def _normalize_skill_ids(skills: Any) -> list[str]:
    """把 hasn_agents.skills（JSONB）归一化为 skill_id 字符串清单（保序去重）。

    兼容三种历史形态：list[str] / list[{skill_id|id}] / {skill_id: version}。
    与 `app/hasn/api/v1/agent/hasn_agent_profile.py` 的同名 helper 逻辑一致——
    service 层不反向依赖 api 层，故此处独立实现，二者均为纯归一化无状态函数。
    """
    out: list[str] = []
    if isinstance(skills, list):
        for item in skills:
            sid: str | None = None
            if isinstance(item, str) and item.strip():
                sid = item.strip()
            elif isinstance(item, dict):
                raw = item.get('skill_id') or item.get('id')
                sid = str(raw) if raw else None
            if sid and sid not in out:
                out.append(sid)
    elif isinstance(skills, dict):
        for key in skills:
            if key and str(key) not in out:
                out.append(str(key))
    return out


def _merge_agent_create_payload(request: CloudCreateAgentRequest, template: Any | None) -> dict[str, Any]:
    template_skills = getattr(template, 'default_skills', None)
    skills = request.skills if request.skills is not None else template_skills
    return {
        'owner_id': request.owner_id,
        'template_id': request.template_id,
        'template_version': getattr(template, 'template_version', None),
        'agent_name': _resolve_agent_slug(request, template),
        'display_name': request.display_name,
        'display_name_candidates': request.display_name_candidates,
        # 领域专家头衔：优先用请求显式传入（webui 据所选模板 name），回退 hasn_agent_templates.name。
        'profession': request.profession or getattr(template, 'name', None),
        'description': request.description
        or getattr(template, 'default_description', None)
        or getattr(template, 'description', None),
        'avatar': request.avatar or getattr(template, 'avatar', None),
        # 只存 Agent 自装/模板技能；公共技能改为 profile 出参叠加（doc12 §3.3）。
        'skills': skills,
        'soul_md': request.soul_md if request.soul_md is not None else getattr(template, 'default_soul_md', None),
        'agents_md': (
            request.agents_md if request.agents_md is not None else getattr(template, 'default_agents_md', None)
        ),
        'user_md': request.user_md if request.user_md is not None else getattr(template, 'default_user_md', None),
        # MEMORY.md 由模板种子（§ 记录格式最小起点），Agent 运行后用记忆工具自演化回写；
        # provision 首次缺省时落盘、已有非空不覆盖（见 hermes-runtime provisioning）。
        'memory_md': request.memory_md if request.memory_md is not None else getattr(template, 'default_memory_md', None),
        'runtime_type': request.runtime_type or getattr(template, 'default_runtime_type', None) or 'hermes',
        # 运行位置（双形态 Runtime，设计 08/02）：local（默认，本地非沙箱）/ cloud（云端 Docker 沙箱）。
        'runtime_location': (getattr(request, 'runtime_location', None) or 'local'),
        'node_id': request.node_id,
        'agent_type': request.agent_type,
        'role': request.role,
        'capabilities': request.capabilities,
    }


_SLUG_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')

# 云端 hasn_agents.status 的允许值集合：业务态 + 生命周期态合并落同一列。
_ALLOWED_STATUS_VALUES: frozenset[str] = frozenset({'active', 'disabled', 'revoked', 'archived', 'deleted'})
_ALLOWED_BINDING_STATUS_VALUES: frozenset[str] = frozenset({'unbound', 'binding', 'bound', 'failed'})


def _resolve_agent_slug(request: CloudCreateAgentRequest, template: Any | None) -> str:
    if request.agent_name and _SLUG_RE.match(request.agent_name):
        return request.agent_name
    for candidate in (
        getattr(template, 'agent_name', None),
        getattr(template, 'template_id', None),
        request.template_id,
    ):
        if isinstance(candidate, str) and _SLUG_RE.match(candidate):
            return candidate
    slug = re.sub(r'[^a-z0-9_-]+', '-', request.display_name.lower()).strip('-_')[:64]
    if slug and _SLUG_RE.match(slug):
        return slug
    return 'agent'


def _agent_snapshot(agent: Any) -> AgentSnapshot:
    raw_tags = getattr(agent, 'tags', None)
    tags = [str(item) for item in raw_tags if item is not None] if isinstance(raw_tags, list) else []
    return AgentSnapshot(
        hasn_id=agent.hasn_id,
        star_id=getattr(agent, 'star_id', ''),
        owner_id=agent.owner_id,
        agent_name=agent.agent_name,
        display_name=agent.display_name,
        description=getattr(agent, 'description', None),
        avatar=getattr(agent, 'avatar', None),
        type=getattr(agent, 'type', 'desktop') or 'desktop',
        runtime_location=getattr(agent, 'runtime_location', 'local') or 'local',
        role=getattr(agent, 'role', 'specialist') or 'specialist',
        profession=getattr(agent, 'profession', None),
        node_id=getattr(agent, 'node_id', None),
        capabilities=getattr(agent, 'capabilities', None),
        capability_set_id=getattr(agent, 'capability_set_id', None),
        persona_ref=getattr(agent, 'persona_ref', None),
        tags=tags,
        template_id=getattr(agent, 'template_id', None),
        template_version=getattr(agent, 'template_version', None),
        skills=getattr(agent, 'skills', None),
        soul_md=getattr(agent, 'soul_md', None),
        agents_md=getattr(agent, 'agents_md', None),
        user_md=getattr(agent, 'user_md', None),
        memory_md=getattr(agent, 'memory_md', None),
        profile_revision=int(getattr(agent, 'profile_revision', 1) or 1),
        status=getattr(agent, 'status', 'active') or 'active',
        social_enabled=bool(getattr(agent, 'social_enabled', False)),
        binding_node_id=getattr(agent, 'binding_node_id', None),
        binding_status=getattr(agent, 'binding_status', 'unbound') or 'unbound',
        binding_updated_at=getattr(agent, 'binding_updated_at', None),
        updated_time=getattr(agent, 'updated_time', None),
    )


agent_profile_service = HasnAgentProfileService()


class HasnAgentsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAgents:
        """
        获取HASN Agent

        :param db: 数据库会话
        :param pk: HASN Agent  ID
        :return:
        """
        hasn_agents = await hasn_agents_dao.get(db, pk)
        if not hasn_agents:
            raise errors.NotFoundError(msg='HASN Agent 不存在')
        return hasn_agents

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Agent 列表

        :param db: 数据库会话
        :return:
        """
        hasn_agents_select = await hasn_agents_dao.get_select()
        return await paging_data(db, hasn_agents_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAgents]:
        """
        获取所有HASN Agent

        :param db: 数据库会话
        :return:
        """
        hasn_agentss = await hasn_agents_dao.get_all(db)
        return hasn_agentss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAgentsParam, user_id: int) -> dict[str, Any]:
        """
        创建HASN Agent

        附带写入 hasn_contacts（owner→agent 的 service 关系，trust_level=5/connected），
        与 hasn_auth.register_hasn_agent 行为对齐：所有 agent 创建路径
        （app create_my_hasn_agents / admin create_hasn_agents）一律自动落 contacts。
        ON CONFLICT (owner_id, peer_id, relation_type) DO NOTHING 幂等。

        :param db: 数据库会话
        :param obj: 创建HASN Agent 参数
        :param user_id: 用户 ID
        :return: Agent 信息及 JWT
        """
        await hasn_agents_dao.create(db, obj)
        await db.execute(
            pg_insert(HasnContacts)
            .values(
                owner_id=obj.owner_id,
                peer_id=obj.hasn_id,
                peer_owner_id=obj.owner_id,
                peer_type='agent',
                relation_type='service',
                trust_level=5,
                status='connected',
                channel_source='system',
                subscription=False,
                interaction_count=0,
                custom_permissions={},
                nickname=obj.name,
                connected_at=timezone.now(),
            )
            .on_conflict_do_nothing(
                index_elements=['owner_id', 'peer_id', 'relation_type'],
            )
        )

        # 签发 Agent JWT
        from backend.common.security.agent_jwt import create_agent_access_token, get_agent_scopes_cached

        scopes_config = await get_agent_scopes_cached(obj.hasn_id, db)
        agent_token = await create_agent_access_token(
            agent_hasn_id=obj.hasn_id,
            agent_name=obj.name,
            owner_hasn_id=obj.owner_id,
            owner_user_id=user_id,
            scopes=scopes_config['scopes'],
        )

        return {
            'hasn_id': obj.hasn_id,
            'owner_id': obj.owner_id,
            'name': obj.name,
            'access_token': agent_token.access_token,
            'scopes': agent_token.scopes,
            'expire_time': agent_token.access_token_expire_time.isoformat(),
        }

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAgentsParam) -> int:
        """
        更新HASN Agent

        :param db: 数据库会话
        :param pk: HASN Agent  ID
        :param obj: 更新HASN Agent 参数
        :return:
        """
        count = await hasn_agents_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAgentsParam) -> int:
        """
        删除HASN Agent

        :param db: 数据库会话
        :param obj: HASN Agent  ID 列表
        :return:
        """
        count = await hasn_agents_dao.delete(db, obj.pks)
        return count


hasn_agents_service: HasnAgentsService = HasnAgentsService()
