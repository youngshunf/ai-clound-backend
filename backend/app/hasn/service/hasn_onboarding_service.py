"""S2 HASN onboarding service.

Scope guard:
- Implements only phone auth adaptation and onboarding ensure.
- Does not implement message hub, runtime scheduling, sandbox creation, or channel bridge.
- Persists/updates only server-authoritative identity, node, binding, default-agent,
  and pending-intent association data; runtime-private endpoint/workspace/PID/CLI/OAuth
  details are intentionally filtered out.
"""

from __future__ import annotations

import random
import string

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

import sqlalchemy as sa

from backend.app.admin.crud.crud_user import user_dao
from backend.app.admin.model import User
from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.schema.hasn_onboarding import (
    AgentSummary,
    AgentTokenInfo,
    HumanSummary,
    OnboardingEnsureRequest,
    OnboardingEnsureResponse,
    OwnerBindingSummary,
    PhoneSendCodeRequest,
    PhoneSendCodeResponse,
    PhoneVerifyRequest,
    PhoneVerifyResponse,
)
from backend.app.hasn.service import hasn_auth as hasn_auth_service
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.app.marketplace.crud.crud_marketplace_template import marketplace_template_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.common.exception import errors
from backend.common.log import log
from backend.common.security.jwt import create_access_token, create_refresh_token
from backend.common.sms import sms_service
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SMS_CODE_PREFIX = 'sms_code'
SMS_CODE_EXPIRE = 1800
SMS_RATE_PREFIX = 'sms_rate'
SMS_RATE_EXPIRE = 60

# 默认 Agent 采用 huanxing-hub 的 `assistant`（星诺 💎 首席特助）权威模板：
# onboarding 创建时读 marketplace_template 把 SOUL/AGENTS/USER + 技能物化进
# hasn_agents，与「WebUI 手动创建 assistant」完全等价。模板缺失（云端尚未
# sync）时回退到下方兜底常量，绝不让 onboarding 因模板缺失而失败。
DEFAULT_AGENT_TEMPLATE_ID = 'huanxing/agent/assistant'
# agent_name 是 slug 槽位（→ star_id `<owner>#assistant`），daemon 镜像依赖，保持不变。
DEFAULT_AGENT_NAME = 'assistant'
# 模板缺失时的兜底 display_name / description（正常路径用模板的 name/description）。
DEFAULT_AGENT_DISPLAY_NAME = '星诺'
DEFAULT_AGENT_DESCRIPTION = 'HASN onboarding 默认 Agent，用于承接首次登录后的基础会话与 pending intent。'
DEFAULT_AGENT_TEMPLATE: dict[str, Any] = {
    'template_id': 'hasn_default_agent_v1',
    'protocol': 'hasn/0.2',
    'role': 'owner_default_agent',
    'capabilities': [
        'owner_visible_inbox',
        'pending_intent_resume',
        'runtime_optional',
    ],
    'runtime_required': False,
}

PRIVATE_NODE_INFO_KEYS = {
    'workspace',
    'workspace_path',
    'endpoint',
    'local_endpoint',
    'pid',
    'process_id',
    'cli_args',
    'oauth_path',
    'session_cache',
}


class RedisLike(Protocol):
    async def exists(self, key: str) -> bool: ...
    async def ttl(self, key: str) -> int: ...
    async def setex(self, key: str, seconds: int, value: str) -> Any: ...
    async def get(self, key: str) -> Any: ...
    async def delete(self, key: str) -> Any: ...


class SmsLike(Protocol):
    async def send_code(self, phone: str, code: str) -> bool: ...


class PlatformUserGateway(Protocol):
    async def get_or_create_phone_user(self, db: AsyncSession, phone: str) -> tuple[Any, bool]: ...


class LlmCredentialIssuer(Protocol):
    async def issue(self, db: AsyncSession, user: Any) -> tuple[str | None, str | None, str | None]: ...


class AgentTokenIssuer(Protocol):
    async def issue(
        self,
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        agent_name: str,
        owner_hasn_id: str,
        owner_user_id: int,
    ) -> Any: ...


class OnboardingGateway(Protocol):
    async def get_user(self, db: AsyncSession, user_id: int) -> Any | None: ...
    async def ensure_human(self, db: AsyncSession, user: Any) -> tuple[Any, bool]: ...
    async def ensure_node(
        self, db: AsyncSession, user_id: int, owner_id: str, request: OnboardingEnsureRequest
    ) -> Any: ...
    async def ensure_owner_binding(self, db: AsyncSession, node_id: str, owner_id: str) -> Any: ...
    async def ensure_default_agent(self, db: AsyncSession, owner_id: str, node_id: str | None) -> tuple[Any, bool]: ...
    async def consume_pending_intent(
        self, db: AsyncSession, pending_intent_id: str, owner_id: str, agent_hasn_id: str
    ) -> bool: ...
    async def get_sync_feed_head(self, db: AsyncSession, owner_id: str) -> int: ...


class SqlAlchemyPlatformUserGateway:
    """Platform-user adapter for HASN phone verification."""

    async def get_or_create_phone_user(self, db: AsyncSession, phone: str) -> tuple[User, bool]:
        user = await user_dao.select_model_by_column(db, phone=phone)
        if user:
            return user, False

        username = phone
        nickname = f'{phone[:3]}****{phone[-4:]}'
        if await user_dao.get_by_username(db, username):
            username = f'{phone}_{_generate_code(4)}'

        user = User(
            username=username,
            nickname=nickname,
            phone=phone,
            password=None,
            salt=None,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

        return user, True


class SqlAlchemyLlmCredentialIssuer:
    async def issue(self, db: AsyncSession, user: Any) -> tuple[str | None, str | None, str | None]:
        from backend.app.newapi.service import (
            llm_newapi_user_mapping_service,
        )

        mapping = await llm_newapi_user_mapping_service.ensure_newapi_user(
            db,
            user.id,
            username=user.phone or user.username,
            nickname=user.nickname or '',
        )
        # 只下发 per-owner 的 token + base_url；**主模型一律留空（None）**。
        # 历史上这里回 `settings.LLM_DEFAULT_MODEL`（恒为 'gpt-5.5'），会被 daemon 镜像进
        # `owner_llm_credentials.llm_model`，并在主模型解析链
        # `per_agent.or(owner_model).or(platform_default_main).or(configured_model)` 里
        # 以 `owner_model` 身份**恒先命中**——直接把平台默认配置下发（PDC）的
        # `agent_runtime.models.main` 永久架空（运营在 Admin 改了也下不去）。
        # 全局默认模型的唯一权威是 PDC 单行（platform_default_config）+ 节点 config
        # `configured_model` 兜底；此处不再注入「全局默认伪装成 per-owner 偏好」。
        # daemon 收到 None 即把本地镜像列覆盖为 NULL（session.rs 覆盖式 upsert），
        # 存量 owner 下次登录自愈，无需迁移。未来若要真正的 per-user 选模型，再写真实值。
        return f'sk-{mapping.newapi_token_key}', settings.LLM_API_BASE_URL, None


class SqlAlchemyAgentTokenIssuer:
    async def issue(
        self,
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        agent_name: str,
        owner_hasn_id: str,
        owner_user_id: int,
    ) -> Any:
        from backend.common.security.agent_jwt import create_agent_access_token, get_agent_scopes_cached

        scopes_config = await get_agent_scopes_cached(agent_hasn_id, db)
        return await create_agent_access_token(
            agent_hasn_id=agent_hasn_id,
            agent_name=agent_name,
            owner_hasn_id=owner_hasn_id,
            owner_user_id=owner_user_id,
            scopes=scopes_config['scopes'],
        )


class SqlAlchemyOnboardingGateway:
    """Production persistence adapter for S2 onboarding business operations."""

    async def get_user(self, db: AsyncSession, user_id: int) -> User | None:
        result = await db.execute(sa.select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def ensure_human(self, db: AsyncSession, user: User) -> tuple[Any, bool]:
        result = await hasn_auth_service.register_hasn_identity(
            db=db,
            user_id=user.id,
            name=user.nickname or user.username or '唤星用户',
            avatar=user.avatar,
            bio=user.bio,
        )
        return result['human'], not result.get('already_exists', False)

    async def ensure_node(self, db: AsyncSession, user_id: int, owner_id: str, request: OnboardingEnsureRequest) -> Any:
        node_info = _safe_node_info(request)
        node = await hasn_auth_service.register_node(
            db=db,
            node_id=request.node.node_id,
            user_id=user_id,
            owner_hasn_id=owner_id,
            node_type=_coerce_node_type(request.node.platform),
            node_name=request.node.device_name,
            node_info=node_info,
        )
        return node

    async def ensure_owner_binding(self, db: AsyncSession, node_id: str, owner_id: str) -> Any:
        return await hasn_node_bindings_service.add_owner_binding(
            db=db,
            node_id=node_id,
            owner_id=owner_id,
            auth_profile='bearer_token',
            scopes={'bind_owner': True, 'register_agent': True, 'onboarding': True},
            expires_at=timezone.now() + timedelta(days=7),
        )

    async def ensure_default_agent(self, db: AsyncSession, owner_id: str, node_id: str | None) -> tuple[Any, bool]:
        # 采用 hub `assistant` 模板（云端权威源 marketplace_template，由 github_app_sync 同步），
        # 把 SOUL/AGENTS/USER + 技能 + 专家头衔(profession) + 头像 物化进 hasn_agents——与
        # 「WebUI 手动建 assistant」等价。
        #
        # 字段映射（2026-06-15 修正，原先把 tpl.name 错当 display_name、漏了 profession/avatar、
        # skills 形态错误）：
        #   - display_name ← name_pool 首位（如「星诺」）；全局唯一化见下
        #   - profession   ← tpl.name（专家头衔，如「全能助理」）
        #   - avatar       ← tpl.icon_url
        #   - skills       ← list[str]（_normalize_skill_ids 兼容形态；原 {'enabled': [...]} 会被
        #                     profile 下发端点误读成 ['enabled']，把模板技能整组丢掉）
        from backend.app.hasn.model import HasnAgents, HasnHumans
        from backend.app.hasn.service.hasn_agents_service import agent_profile_service

        tpl = await marketplace_template_dao.get_by_id(db, DEFAULT_AGENT_TEMPLATE_ID)
        base_name = DEFAULT_AGENT_DISPLAY_NAME
        description: str | None = DEFAULT_AGENT_DESCRIPTION
        profession: str | None = None
        avatar: str | None = None
        template_id: str | None = None
        template_version: str | None = None
        soul_md: str | None = None
        agents_md: str | None = None
        user_md: str | None = None
        memory_md: str | None = None
        skills: list[str] | None = None
        if tpl is not None:
            template_id = DEFAULT_AGENT_TEMPLATE_ID
            # name_pool 首位是昵称基名（星诺）；tpl.name（全能助理）是专家头衔 → profession。
            name_pool = [s.strip() for s in (tpl.name_pool or '').split(',') if s.strip()]
            base_name = name_pool[0] if name_pool else (tpl.name or DEFAULT_AGENT_DISPLAY_NAME)
            profession = tpl.name or None
            avatar = tpl.icon_url or None
            description = tpl.description or DEFAULT_AGENT_DESCRIPTION
            soul_md = tpl.soul_md
            agents_md = tpl.agents_md
            user_md = tpl.user_md
            memory_md = getattr(tpl, 'memory_md', None)
            skills = [s.strip() for s in (tpl.skill_dependencies or '').split(',') if s.strip()] or None
            version = await marketplace_template_version_dao.get_latest_by_template(db, DEFAULT_AGENT_TEMPLATE_ID)
            template_version = getattr(version, 'version', None)
        else:
            # IM-first / 零 fake：模板尚未 sync 时不阻断 onboarding，退回纯身份创建。
            log.warning(
                'default agent template %s not found in marketplace_template; '
                'creating default agent without persona (run github_app_sync)',
                DEFAULT_AGENT_TEMPLATE_ID,
            )

        # onboarding 在登录路径幂等执行：对「已存在」的默认分身不重算昵称、不 clobber 用户可能
        # 已自定义的 skills/persona——只一次性回填「当前为空」的 profession/avatar；存量已坏分身
        # （display_name 历史被填成专家头衔）由独立一次性脚本修复，不混进登录路径。
        existing = (
            await db.execute(
                sa.select(HasnAgents).where(
                    HasnAgents.owner_id == owner_id,
                    HasnAgents.agent_name == DEFAULT_AGENT_NAME,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            display_name = existing.display_name
            profession = profession if not getattr(existing, 'profession', None) else None
            avatar = avatar if not getattr(existing, 'avatar', None) else None
            template_id = template_id if not getattr(existing, 'template_id', None) else None
            template_version = template_version if template_id else None
            description = None
            skills = None
            soul_md = agents_md = user_md = memory_md = None
        else:
            # 新建：昵称基名（星诺）全局撞名时用「主人昵称」派生（星诺·福仔），不再每个用户都同名。
            owner_nickname = (
                await db.execute(sa.select(HasnHumans.nickname).where(HasnHumans.hasn_id == owner_id))
            ).scalar_one_or_none()
            display_name = await agent_profile_service.gateway.resolve_default_agent_display_name(
                db, base=base_name, owner_nickname=owner_nickname, owner_id=owner_id
            )

        result = await hasn_auth_service.register_hasn_agent(
            db=db,
            owner_hasn_id=owner_id,
            agent_name=DEFAULT_AGENT_NAME,
            display_name=display_name,
            profession=profession,
            avatar=avatar,
            agent_type='cloud',
            node_id=node_id,
            role='primary',
            # 主脑 = assistant 内置模板：标记 builtin_agent_key 让 reconcile_builtin_agents
            # 据此判存在跳过（不重复创建主脑），同时让 target_agent_type='assistant' 的内置任务命中。
            builtin_agent_key='assistant',
            description=description,
            capabilities=[DEFAULT_AGENT_TEMPLATE],
            created_via='onboarding',
            template_id=template_id,
            template_version=template_version,
            skills=skills,
            soul_md=soul_md,
            agents_md=agents_md,
            user_md=user_md,
            memory_md=memory_md,
        )
        return result['agent'], not result.get('already_exists', False)

    async def consume_pending_intent(
        self, db: AsyncSession, pending_intent_id: str, owner_id: str, agent_hasn_id: str
    ) -> bool:
        """Associate a pending intent with onboarding result.

        This is specific S2 business logic, not a generic CRUD surface. The table
        is a S1 codegen input; S5 will own full channel/pending-intent management.
        """
        result = await db.execute(
            sa.text(
                """
                UPDATE public.hasn_pending_intents
                SET owner_id = :owner_id,
                    agent_hasn_id = :agent_hasn_id,
                    status = 'consumed',
                    consumed_at = now(),
                    updated_time = now()
                WHERE intent_id = :intent_id
                  AND status = 'pending'
                  AND expires_at > now()
                RETURNING intent_id
                """
            ),
            {
                'owner_id': owner_id,
                'agent_hasn_id': agent_hasn_id,
                'intent_id': pending_intent_id,
            },
        )
        return result.first() is not None

    async def get_sync_feed_head(self, db: AsyncSession, owner_id: str) -> int:
        """该 owner 权威 sync feed（hasn_sync_events）当前 head revision；空 feed 为 0。"""
        result = await db.execute(
            sa.text(
                'SELECT COALESCE(MAX(revision), 0) FROM public.hasn_sync_events WHERE owner_id = :owner_id'
            ),
            {'owner_id': owner_id},
        )
        return int(result.scalar_one())


@dataclass(slots=True)
class HasnPhoneAuthService:
    redis: RedisLike = field(default=redis_client)
    sms: SmsLike = field(default=sms_service)
    users: PlatformUserGateway = field(default_factory=SqlAlchemyPlatformUserGateway)
    token_expire_seconds: int = settings.TOKEN_EXPIRE_SECONDS
    code_generator: Any | None = None
    token_creator: Any = create_access_token
    llm_credentials: LlmCredentialIssuer = field(default_factory=SqlAlchemyLlmCredentialIssuer)
    agent_tokens: AgentTokenIssuer = field(default_factory=SqlAlchemyAgentTokenIssuer)

    async def send_code(self, request: PhoneSendCodeRequest) -> PhoneSendCodeResponse:
        phone = request.phone
        rate_key = f'{SMS_RATE_PREFIX}:{phone}'
        if await self.redis.exists(rate_key):
            ttl = await self.redis.ttl(rate_key)
            return PhoneSendCodeResponse(ok=False, retry_after_sec=max(int(ttl or 0), 0))

        generator = self.code_generator or _generate_code
        code = generator()
        await self.redis.setex(f'{SMS_CODE_PREFIX}:{phone}', SMS_CODE_EXPIRE, code)

        if settings.ENVIRONMENT == 'dev':
            print(f'[HASN] phone verification code [{phone}]: {code}')

        sent = await self.sms.send_code(phone, code)
        if not sent and settings.ENVIRONMENT != 'dev':
            raise errors.RequestError(msg='验证码发送失败，请稍后重试')

        await self.redis.setex(rate_key, SMS_RATE_EXPIRE, '1')
        return PhoneSendCodeResponse(ok=True, retry_after_sec=0)

    async def verify(self, db: AsyncSession, request: PhoneVerifyRequest) -> PhoneVerifyResponse:
        phone = request.phone
        stored_code = await self.redis.get(f'{SMS_CODE_PREFIX}:{phone}')
        stored_code = _decode_redis_value(stored_code)
        if not stored_code:
            raise errors.RequestError(msg='验证码已过期，请重新获取')
        if stored_code != request.code:
            raise errors.RequestError(msg='验证码错误')

        await self.redis.delete(f'{SMS_CODE_PREFIX}:{phone}')
        user, _ = await self.users.get_or_create_phone_user(db, phone)
        user.last_login_time = timezone.now()
        await db.flush()

        access_token = await self.token_creator(
            user.id,
            multi_login=user.is_multi_login,
            username=user.username,
            nickname=user.nickname,
            phone=user.phone,
            pending_intent_id=request.pending_intent_id,
            hasn_onboarding=True,
        )

        # PR7: ensure newapi user + token so the daemon receives per-owner LLM credentials.
        try:
            llm_token, llm_base_url, llm_model = await self.llm_credentials.issue(db, user)
        except Exception as exc:
            raise errors.ServerError(msg=f'LLM 服务初始化失败: {exc}') from exc

        refresh_token_data = await create_refresh_token(
            access_token.session_uuid,
            user.id,
            multi_login=user.is_multi_login,
        )
        agent_tokens = await _issue_phone_verify_agent_tokens(
            db,
            user=user,
            agent_tokens=self.agent_tokens,
        )

        return PhoneVerifyResponse(
            access_token=access_token.access_token,
            expires_in_sec=self.token_expire_seconds,
            refresh_token=refresh_token_data.refresh_token,
            refresh_token_expire_sec=settings.HASN_REFRESH_TOKEN_EXPIRE_SECONDS,
            llm_token=llm_token,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            agent_tokens=agent_tokens,
        )


@dataclass(slots=True)
class HasnOnboardingService:
    gateway: OnboardingGateway = field(default_factory=SqlAlchemyOnboardingGateway)
    agent_tokens: AgentTokenIssuer = field(default_factory=SqlAlchemyAgentTokenIssuer)

    async def ensure(
        self, db: AsyncSession, user_id: int, request: OnboardingEnsureRequest
    ) -> OnboardingEnsureResponse:
        user = await self.gateway.get_user(db, user_id)
        if user is None:
            raise errors.NotFoundError(msg='用户不存在')

        human, _ = await self.gateway.ensure_human(db, user)
        node = await self.gateway.ensure_node(db, user_id, human.hasn_id, request)
        binding = await self.gateway.ensure_owner_binding(db, node.node_id, human.hasn_id)
        agent, _ = await self.gateway.ensure_default_agent(db, human.hasn_id, node.node_id)

        # 内置定时任务体系（§5.1）：建主脑后建齐内置 agent，再 INSERT-only 播种内置任务。
        # best-effort：失败不阻断 onboarding（IM-first），存量用户下次登录幂等补建/补播种。
        try:
            from backend.app.hasn_task.service.builtin_seeding_service import (
                reconcile_builtin_agents,
                seed_builtin_tasks,
            )

            await reconcile_builtin_agents(db, owner_id=human.hasn_id, node_id=node.node_id)
            await seed_builtin_tasks(db, owner_id=human.hasn_id)
        except Exception as exc:
            log.warning('builtin agent/task seeding failed during onboarding for %s: %s', human.hasn_id, exc)

        # DS-P6 官方内置设计系统（全局 owner='system'，reconcile：新增/换代替换/退役）：每次登录跑一次
        # 轻量对账，内容未变即幂等短路；官方库换代时自动落新版+退役旧占位（如历史遗留『官方暖沙』）。
        # best-effort：失败不阻断登录。设计事实源：docs/.../实施/12-设计系统生成应用实施清单.md（P6）。
        try:
            from backend.app.hasn_designsystem.service.builtin_seeding_service import (
                seed_builtin_design_systems,
            )

            await seed_builtin_design_systems(db)
        except Exception as exc:
            log.warning('builtin design system seeding failed during onboarding for %s: %s', human.hasn_id, exc)

        # 自愈：内置/默认分身名首登时若主人未设昵称会被烙进手机号掩码（186****2019），
        # 存量用户即便已设真实昵称分身仍显示手机号 → 每次登录幂等把这类后缀刷成真实昵称。
        # 新用户此刻昵称仍是手机号掩码，方法内部短路不动（无 churn）。best-effort 不阻断登录。
        try:
            from backend.app.hasn.service.hasn_agents_service import agent_profile_service

            await agent_profile_service.refresh_seeded_agent_display_names(
                db, owner_id=human.hasn_id, current_nickname=getattr(human, 'nickname', None)
            )
        except Exception as exc:
            log.warning('refresh seeded agent names failed during onboarding for %s: %s', human.hasn_id, exc)

        if request.pending_intent_id:
            await self.gateway.consume_pending_intent(
                db,
                pending_intent_id=request.pending_intent_id,
                owner_id=human.hasn_id,
                agent_hasn_id=agent.hasn_id,
            )

        agent_token = await self.agent_tokens.issue(
            db,
            agent_hasn_id=agent.hasn_id,
            agent_name=getattr(agent, 'name', None) or DEFAULT_AGENT_DISPLAY_NAME,
            owner_hasn_id=human.hasn_id,
            owner_user_id=user_id,
        )

        # B2②（hasn-node 实施/90 §2）：bootstrap 游标返回该 owner 权威 feed 的真实 head，
        # 不再硬编码 0。修前每次登录都回 `...:0`，旧版 daemon 无条件镜像 → 本地已推进的游标
        # 被重置、feed 从头重放（且登录只拉一页，超一页的积压永远追不上）。daemon 侧已配套
        # 改成 bootstrap-only（本地已有可解析游标一律不覆盖），此值仅全新设备首登生效——
        # 从「现在」起步，历史恢复走镜像/read-through 权威路径而非 feed 重放。
        sync_feed_head = await self.gateway.get_sync_feed_head(db, owner_id=human.hasn_id)

        return OnboardingEnsureResponse(
            human=HumanSummary(
                human_id=human.hasn_id,
                owner_id=human.hasn_id,
                display_name=getattr(human, 'name', None),
            ),
            owner_binding=OwnerBindingSummary(
                owner_id=human.hasn_id,
                node_id=node.node_id,
                status=_binding_status(getattr(binding, 'status', 'active')),
                revision=int(getattr(binding, 'sync_revision', 1) or 1),
            ),
            default_agent=AgentSummary(
                agent_id=agent.hasn_id,
                owner_id=human.hasn_id,
                hasn_id=agent.hasn_id,
                # PR1.5: 透传 hasn_agents.star_id 给 daemon，避免 daemon 用
                # hasn_id 顶替 star_id 写本地导致绑定时报 empty。
                star_id=getattr(agent, 'star_id', '') or '',
                display_name=getattr(agent, 'name', None),
                access_token=agent_token.access_token,
                scopes=agent_token.scopes,
                expire_time=agent_token.access_token_expire_time.isoformat(),
            ),
            # hasn_tenant_sandboxes 已退役（沙箱功能从未建设，恒 None）；响应字段保留兼容 daemon。
            sandbox=None,
            sync_cursor=f'owner:{human.hasn_id}:{sync_feed_head}',
        )


def _generate_code(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))


def _decode_redis_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _coerce_node_type(platform: str) -> str:
    normalized = (platform or '').lower()
    if normalized in {'ios', 'android'}:
        return 'mobile'
    if normalized in {'web', 'browser'}:
        return 'web'
    if normalized in {'sdk', 'server'}:
        return 'sdk'
    return 'desktop'


def _safe_node_info(request: OnboardingEnsureRequest) -> dict[str, Any]:
    raw = {
        'device_fingerprint': request.node.node_id,
        'device_platform': request.node.platform,
        'client_version': request.node.client_version,
        'protocol': request.client.protocol,
        'supported_extensions': request.client.supported_extensions or [],
    }
    return {key: value for key, value in raw.items() if key not in PRIVATE_NODE_INFO_KEYS and value is not None}


def _binding_status(status: str) -> str:
    if status == 'revoked':
        return 'revoked'
    if status == 'expired':
        return 'expiring'
    return 'active'


def _sandbox_status(state: str) -> str:
    if state == 'error':
        return 'failed'
    if state in {'creating', 'active', 'sleeping', 'deleted', 'failed'}:
        return state
    return 'sleeping'


async def _issue_phone_verify_agent_tokens(
    db: AsyncSession,
    *,
    user: User,
    agent_tokens: AgentTokenIssuer,
) -> list[AgentTokenInfo]:
    try:
        human = await hasn_humans_dao.get_by_user_id(db, user.id)
        if not human or not human.hasn_id:
            return []

        agents = await hasn_agents_dao.get_active_agents_by_owner(db, human.hasn_id)
    except Exception as exc:
        log.error(f'批量签发 Agent JWT 失败: {exc}')
        return []

    issued_agent_tokens: list[AgentTokenInfo] = []
    for agent in agents:
        try:
            token = await agent_tokens.issue(
                db,
                agent_hasn_id=agent.hasn_id,
                agent_name=getattr(agent, 'display_name', None) or getattr(agent, 'agent_name', None),
                owner_hasn_id=human.hasn_id,
                owner_user_id=user.id,
            )
            issued_agent_tokens.append(
                AgentTokenInfo(
                    agent_hasn_id=agent.hasn_id,
                    agent_name=getattr(agent, 'display_name', None) or getattr(agent, 'agent_name', None),
                    access_token=token.access_token,
                    scopes=token.scopes,
                    expire_time=getattr(token, 'access_token_expire_time', None).isoformat()
                    if getattr(token, 'access_token_expire_time', None)
                    else None,
                    expires_at_unix=getattr(token, 'expires_at_unix', None),
                )
            )
        except Exception as exc:
            log.error(f'为 Agent {agent.hasn_id} 签发 JWT 失败: {exc}')
            continue

    return issued_agent_tokens


hasn_phone_auth_service = HasnPhoneAuthService()
hasn_onboarding_service = HasnOnboardingService()
