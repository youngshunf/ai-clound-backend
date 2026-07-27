import hashlib
import re
import string

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.model import HasnAgents
from backend.app.hasn.schema.hasn_agents import (
    AgentRuntimeConfig,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
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
from backend.app.hasn_im.application.provider import get_presence_query
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

_presence_query = get_presence_query()

# 默认/内置分身名的当前格式：`{主人昵称}的{专家名称}`（如「小智的全能助理」），连接词为「的」。
DEFAULT_AGENT_NAME_CONNECTOR = '的'
# 历史遗留格式：`{基名}·{主人昵称}`，分隔符为 U+00B7 间隔号——仅用于识别存量分身以迁移到新格式。
DEFAULT_AGENT_NAME_SEPARATOR = '·'
# 主人尚未设昵称时的手机号掩码兜底（见 get_or_create_phone_user: f'{phone[:3]}****{phone[-4:]}'）。
# onboarding 在登录路径建分身时若主人未设昵称，后缀会被烙进这个掩码 → 需在设昵称/登录时刷新。
PHONE_MASK_NICKNAME_RE = re.compile(r'^\d{3}\*{4}\d{4}$')


def compute_default_agent_display_name(*, owner_nickname: str | None, profession: str | None) -> str:
    """纯逻辑：按当前格式 `{主人昵称}的{专家名称}` 组默认/内置分身名（如「小智的全能助理」）。

    主人昵称有效（非空、非手机号掩码）→ `{昵称}的{专家名称}`；昵称仍是手机号掩码 / 空 → 退化为
    纯专家名占位（「全能助理」），设昵称后由 refresh_seeded_agent_display_names 自愈刷新，
    绝不把手机号掩码烙进名字（issue③）。专家名也缺失（模板未 sync 等异常）时兜底「AI 分身」。
    不做全局唯一化（由调用方在落库前补数字后缀）。抽成纯函数便于确定性单测。
    """
    prof = (profession or '').strip() or 'AI 分身'
    nick = (owner_nickname or '').strip()
    if not nick or PHONE_MASK_NICKNAME_RE.match(nick):
        return prof  # 昵称未就绪 → 纯专家名占位
    return f'{nick}{DEFAULT_AGENT_NAME_CONNECTOR}{prof}'


def _owner_token_is_seeded(token: str, *, previous_nickname: str) -> bool:
    """名字里的「主人标识片段」是否系统烙进的旧值（空占位 / 手机号掩码 / 旧昵称）→ 可安全刷新。"""
    token = token.strip()
    if not token:
        return True  # 纯占位（主人未设昵称时的专家名占位）
    if PHONE_MASK_NICKNAME_RE.match(token):
        return True  # 手机号掩码——绝不可能是用户手取
    prev = previous_nickname.strip()
    # 旧昵称可能带撞名数字尾（小福 / 小福2 同名两用户）→ 去尾后比对。
    return bool(prev) and (token == prev or token.rstrip(string.digits) == prev)


def _is_seeded_default_display(current: str, *, profession: str, previous_nickname: str) -> bool:
    """判 display_name 是否「系统派生的默认分身形态」（可安全刷新，非主人手取名）。

    覆盖三类（以专家名 profession 为锚）：
      - 历史遗留形态 `{基名}·{X}`（旧格式，基名任意，`·` 分隔；新格式用「的」不冲突）；
      - 新占位形态 `{专家名}` / `{专家名}{N}`（主人未设昵称时的纯专家名占位，含唯一化数字尾）；
      - 新旧值形态 `{X}的{专家名}`（可带唯一化数字尾），
    其中 X（主人标识片段）∈ {空, 手机号掩码, previous_nickname}。绝不命中主人手动取的名字。
    """
    # 历史遗留 `·` 形态：基名任意，后缀 ∈ {掩码, 旧昵称} → 供存量分身迁移到新格式。
    sep = current.rfind(DEFAULT_AGENT_NAME_SEPARATOR)
    if sep > 0:
        return _owner_token_is_seeded(
            current[sep + len(DEFAULT_AGENT_NAME_SEPARATOR) :], previous_nickname=previous_nickname
        )
    if not profession:
        return False  # 无专家名锚 → 仅能靠 `·` 形态识别，否则不动（避免误伤手取名）
    # 新占位形态：纯专家名（主人标识片段为空），可带唯一化数字尾（全能助理 / 全能助理2）。
    if current == profession or (current.startswith(profession) and current[len(profession) :].isdigit()):
        return True
    # 新旧值形态：`{X}的{专家名}`，尾部允许唯一化数字。
    marker = f'{DEFAULT_AGENT_NAME_CONNECTOR}{profession}'
    idx = current.rfind(marker)
    if idx > 0:
        tail = current[idx + len(marker) :]
        if tail == '' or tail.isdigit():
            return _owner_token_is_seeded(current[:idx], previous_nickname=previous_nickname)
    return False


def compute_seeded_name_refresh(
    current_display: str | None,
    *,
    profession: str | None = None,
    new_nickname: str | None,
    previous_nickname: str | None,
) -> str | None:
    """纯逻辑：把系统播种的默认/内置分身名刷新为当前格式 `{新昵称}的{专家名称}`。

    仅当 current_display 属「系统派生形态」（见 _is_seeded_default_display）时改写，否则返回 None
    （用户手取名 / 新昵称不可用 / 已是目标名 → 不动）。传入 profession（该分身的专家头衔）时产出
    新格式 `{新昵称}的{专家名称}`，顺带把历史遗留 `{基名}·{旧后缀}` 存量分身迁移到新格式（issue②
    「统一」）；未传 profession（异常/存量无专家名）时退回旧行为——遗留 `·` 形态只换后缀保留基名，
    避免产出裸昵称。不查 DB、不做全局唯一化（由调用方在落库前补数字后缀）。抽成纯函数便于单测。
    """
    nick = (new_nickname or '').strip()
    # 新昵称为空 / 仍是手机号掩码 → 无可改进
    if not nick or PHONE_MASK_NICKNAME_RE.match(nick):
        return None
    current = (current_display or '').strip()
    if not current:
        return None
    prof = (profession or '').strip()
    prev = (previous_nickname or '').strip()
    if not _is_seeded_default_display(current, profession=prof, previous_nickname=prev):
        return None
    if prof:
        candidate = f'{nick}{DEFAULT_AGENT_NAME_CONNECTOR}{prof}'  # 当前格式
    else:
        # 无专家名（异常/存量）：仅遗留 `·` 形态可安全改——保留基名只换后缀，避免裸昵称。
        sep = current.rfind(DEFAULT_AGENT_NAME_SEPARATOR)
        candidate = f'{current[:sep]}{DEFAULT_AGENT_NAME_SEPARATOR}{nick}' if sep > 0 else nick
    return candidate if candidate != current else None


# USER.md 模板首行 `称呼: {{owner_nickname}}`（见 huanxing-hub/templates/USER.md）——主人称呼即
# 分身如何称呼主人。建档时 register_hasn_agent 把 {{owner_nickname}} 渲染成当时 HasnHumans.nickname；
# 主人未设昵称时是手机号掩码 → 烙进 USER.md，且渲染只在建档做一次、serve/runtime 端不再替换，
# 主人之后改昵称这行不会自动刷新 → 分身一直按手机号掩码称呼主人（本次 bug）。半/全角冒号都兜。
_USER_MD_OWNER_LABEL_RE = re.compile(r'^([ \t]*称呼[:：][ \t]*)(.*?)([ \t]*)$', re.MULTILINE)


def compute_user_md_owner_refresh(
    user_md: str | None, *, new_nickname: str | None, previous_nickname: str | None
) -> str | None:
    """纯逻辑：把 USER.md `称呼:` 行里的旧主人称呼（手机号掩码 / previous_nickname）刷成新昵称。

    仅当该行当前值 ∈ {手机号掩码, previous_nickname}（即系统建档渲染的、非主人手改）时替换；空值 /
    已是别的内容（含已是新昵称）→ 不动。返回刷新后的完整 user_md；无可改进返回 None。
    只动 `称呼:` 行，绝不在正文里全局替换（previous_nickname 可能是普通词，全局替换会误伤正文）。
    不查 DB，抽成纯函数便于确定性单测。
    """
    if not user_md:
        return None
    nick = (new_nickname or '').strip()
    # 新昵称为空 / 仍是手机号掩码 → 无可改进（新用户首登在此短路）
    if not nick or PHONE_MASK_NICKNAME_RE.match(nick):
        return None
    prev = (previous_nickname or '').strip()

    changed = False

    def _sub(match: 're.Match[str]') -> str:
        nonlocal changed
        label, value, trailing = match.group(1), match.group(2).strip(), match.group(3)
        is_phone = bool(PHONE_MASK_NICKNAME_RE.match(value))
        is_prev = bool(prev) and value == prev
        if value and value != nick and (is_phone or is_prev):
            changed = True
            return f'{label}{nick}{trailing}'
        return match.group(0)

    updated = _USER_MD_OWNER_LABEL_RE.sub(_sub, user_md)
    return updated if changed else None


# MEMORY.md（分身笔记）模板首行是身份行
# `我是 {{display_name}}，{{owner_nickname}} 在唤星（Astra）的 AI 分身，通过记忆工具读写这份长期记忆。`
# （见 huanxing-hub/templates/MEMORY.md）——同 USER.md `称呼:` 一样在建档时把 {{display_name}}/
# {{owner_nickname}} 渲染成当时值、之后不再替换。主人改昵称 / 分身改名后这行不会自动刷新 → 分身笔记
# 里一直是旧分身名与旧主人昵称（本次 bug：主人档案已刷成新昵称，分身笔记仍是旧的）。
# 只匹配这一「身份行」，绝不碰身份行以外分身自演化追加的记忆正文；半/全角逗号都兜。
_MEMORY_MD_IDENTITY_RE = re.compile(
    r'^([ \t]*我是[ \t]+)(.+?)([，,][ \t]*)(.+?)([ \t]*在唤星（Astra）的[ \t]*AI[ \t]*分身)',
    re.MULTILINE,
)


def compute_memory_md_identity_refresh(
    memory_md: str | None,
    *,
    profession: str | None,
    current_display_name: str | None,
    new_nickname: str | None,
    previous_nickname: str | None,
) -> str | None:
    """纯逻辑：把 MEMORY.md 首行身份行里被烙进的旧分身名 / 旧主人昵称刷成当前值。

    身份行形如 `我是 {display_name}，{owner_nickname} 在唤星（Astra）的 AI 分身，…`，两段各自独立 gate：
      - 分身名段：仅当属「系统派生形态」（_is_seeded_default_display，非主人手取名）时 → 刷成分身当前
        权威 display_name（current_display_name，已由维度①刷新过），保证与分身名列一致（含唯一化尾）；
      - 主人称呼段：仅当值 ∈ {手机号掩码, previous_nickname}（系统建档烙进）时 → 刷成 new_nickname。
    两段都绝不 clobber 用户手改的内容，也绝不动身份行以外自演化追加的记忆正文（只替换首个匹配行）。
    无可改进返回 None。不查 DB，抽成纯函数便于确定性单测。
    """
    if not memory_md:
        return None
    nick = (new_nickname or '').strip()
    prev = (previous_nickname or '').strip()
    prof = (profession or '').strip()
    cur_disp = (current_display_name or '').strip()

    changed = False

    def _sub(match: 're.Match[str]') -> str:
        nonlocal changed
        head, disp, mid, owner, tail = (
            match.group(1),
            match.group(2).strip(),
            match.group(3),
            match.group(4).strip(),
            match.group(5),
        )
        # 分身名段：系统派生形态（含烙进旧昵称/掩码/占位）→ 刷成分身当前权威 display_name。
        if cur_disp and disp != cur_disp and _is_seeded_default_display(disp, profession=prof, previous_nickname=prev):
            disp = cur_disp
            changed = True
        # 主人称呼段：系统烙进的旧值（手机号掩码 / previous_nickname）→ 刷成新昵称。
        if nick and not PHONE_MASK_NICKNAME_RE.match(nick):
            is_phone = bool(PHONE_MASK_NICKNAME_RE.match(owner))
            is_prev = bool(prev) and owner == prev
            if owner and owner != nick and (is_phone or is_prev):
                owner = nick
                changed = True
        return f'{head}{disp}{mid}{owner}{tail}'

    updated = _MEMORY_MD_IDENTITY_RE.sub(_sub, memory_md, count=1)
    return updated if changed else None


class AgentProfileGateway(Protocol):
    async def owns_owner(self, db: AsyncSession, *, owner_id: str, user_id: int) -> bool: ...
    async def get_template(self, db: AsyncSession, *, template_id: str) -> Any | None: ...
    async def create_agent(self, db: AsyncSession, payload: dict[str, Any]) -> tuple[Any, str | None, bool]: ...
    async def is_display_name_taken(self, db: AsyncSession, display_name: str) -> bool: ...
    async def resolve_unique_display_name(
        self, db: AsyncSession, *, desired: str, candidates: list[str] | None = None
    ) -> str: ...
    async def resolve_default_agent_display_name(
        self, db: AsyncSession, *, profession: str | None, owner_nickname: str | None
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
                    sa
                    .select(HasnAgents.id)
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
                sa
                .select(HasnAgents.id)
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

    async def resolve_default_agent_display_name(
        self, db: AsyncSession, *, profession: str | None, owner_nickname: str | None
    ) -> str:
        """默认/内置分身首次命名并全局唯一化：`{主人昵称}的{专家名称}` → +数字后缀。

        昵称有效则 `小智的全能助理`；昵称尚是手机号掩码 / 空 → 退化为纯专家名占位（全能助理），
        绝不烙手机号掩码（issue③），主人设昵称后由 refresh_seeded_agent_display_names 自愈刷新为
        `{昵称}的{专家名称}`（issue②）。仅用于 onboarding / 内置播种默认分身的**首次**命名；
        不对已存在分身重算——否则 is_display_name_taken 会把分身自己那行算成已占而每次登录误改名。
        """
        desired = compute_default_agent_display_name(owner_nickname=owner_nickname, profession=profession)
        if not await self.is_display_name_taken(db, desired):
            return desired
        for suffix in range(2, 1000):
            candidate = f'{desired}{suffix}'
            if not await self.is_display_name_taken(db, candidate):
                return candidate
        import uuid

        return f'{desired}-{uuid.uuid4().hex[:4]}'

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
        # 触发点 2（设计 §6.1）：新建分身后跑 INSERT-only 播种——若有内置任务此前因无对应类型
        # 分身回退绑了主脑则保持现状（INSERT-only 不重绑），尚未播种的条目则补 INSERT 绑到合适分身。
        # best-effort：播种失败绝不阻断分身创建。
        try:
            from backend.app.hasn_task.service.builtin_seeding_service import seed_builtin_tasks

            await seed_builtin_tasks(db, owner_id=payload['owner_id'])
        except Exception as exc:
            from backend.common.log import log

            log.warning('create_agent: seed_builtin_tasks best-effort failed: {!r}', exc)
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
        from backend.app.hasn_sync.adapters.sqlalchemy_appender import (
            SqlAlchemySyncAppender,
        )
        from backend.app.hasn_sync.ports.dto import SyncEnvelope

        source = (
            f'{event_type}:{agent.hasn_id}:'
            f'{int(getattr(agent, "profile_revision", 0) or 0)}'
        )
        await SqlAlchemySyncAppender().append(
            db,
            SyncEnvelope(
                owner_id=owner_id,
                hasn_id=agent.hasn_id,
                event_type=event_type,
                aggregate_type='agent',
                aggregate_id=agent.hasn_id,
                payload={'agent': _agent_snapshot(agent).model_dump(mode='json')},
                producer='agent_profile',
                source_event_id=hashlib.sha256(source.encode('utf-8')).hexdigest(),
            ),
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
                )

                # 插入默认三态权限配置（hasn_agent_scopes：default_mode + capability_modes 授权权威）
                await create_default_agent_scopes(db, agent.hasn_id, request.owner_id)

                # 签发 Agent JWT（scopes 已退役·实施102 S0：JWT 不再携带 scopes，授权只看三态）
                agent_token = await create_agent_access_token(
                    agent_hasn_id=agent.hasn_id,
                    agent_name=agent.display_name or agent.agent_name,
                    owner_hasn_id=request.owner_id,
                    owner_user_id=user_id or 0,
                )

                agent_token_info = AgentTokenInfo(
                    access_token=agent_token.access_token,
                )
            except Exception as e:
                from backend.common.log import log

                log.error(f'为 Agent {agent.hasn_id} 签发 JWT 失败: {e}')
                # JWT 签发失败不影响 Agent 创建

            # 云端形态分身：创建即 provision 到云端 hermes（platform LLM），让新分身首次派发秒回。
            # best-effort：provision 失败不阻断创建——dispatch 阶段会兜底补 provision（自愈）。
            if getattr(agent, 'runtime_location', None) == 'cloud':
                try:
                    from backend.app.hasn.service.hasn_agent_runtime_provision_service import (
                        cloud_profile_id_for,
                        ensure_cloud_profile_provisioned,
                    )

                    cloud_profile_id = await cloud_profile_id_for(
                        db, owner_hasn_id=request.owner_id, agent_name=agent.agent_name
                    )
                    await ensure_cloud_profile_provisioned(
                        db,
                        agent_hasn_id=agent.hasn_id,
                        owner_hasn_id=request.owner_id,
                        profile_id=cloud_profile_id,
                    )
                except Exception as e:
                    from backend.common.log import log

                    log.warning(f'云端分身 {agent.hasn_id} 创建时 provision 失败（dispatch 阶段会兜底）: {e}')

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
        # 记忆三段（doc10 PUT）：owner 在「记忆」tab 编辑保存写云端权威。partial 语义按
        # `in provided`（键传入即写，含空串=清空该段），与 description/avatar 一致。daemon
        # 据返回快照镜像到本地 MemoryStore（单一事实源），profile_revision 自增触发重拉。
        if 'soul_md' in provided:
            agent.soul_md = provided['soul_md']
        if 'user_md' in provided:
            agent.user_md = provided['user_md']
        if 'memory_md' in provided:
            agent.memory_md = provided['memory_md']

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

    async def delete_profile_cloud_first(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        hasn_id: str,
        user_id: int | None = None,
    ) -> str:
        """云端权威硬删 Agent（物理 DELETE hasn_agents 行），返回被删 hasn_id。

        daemon「真删除分身」链路调用：daemon 先停 hermes gateway，再调本端点删云端
        权威记录，成功后才清本地 profile/runtime binding 镜像。与 PATCH
        status=archived 的软归档**本质不同**——本方法物理删除、不可恢复。
        owner 隔离按 (hasn_id, owner_id)；不归属或不存在则 404。
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

        await db.delete(agent)
        await db.flush()
        return hasn_id

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

    async def refresh_seeded_agent_display_names(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        current_nickname: str | None,
        previous_nickname: str | None = None,
    ) -> list[str]:
        """把 owner 名下分身里被烙进的旧主人昵称/手机号掩码刷新为当前真实昵称（三个维度）。

        背景：onboarding 在登录路径建分身时主人尚未设昵称、HasnHumans.nickname 仍是手机号掩码
        （186****2019）或系统默认（用户8368）；主人之后改昵称没有回写分身 → 三处一直显示/称呼旧值
        （本次 bug）：
          1. 分身自己的 display_name：内置/默认分身名 = `{主人昵称}的{专家名}`（旧格式 `{基名}·{主人昵称}`），
             主人标识片段被烙进旧昵称/掩码。
          2. 分身记忆里的 USER.md（主人档案·user_md 列）：模板首行 `称呼: {{owner_nickname}}` 建档即渲染成
             当时昵称，之后不再替换 → 分身读 USER.md 一直按旧昵称称呼主人。
          3. 分身记忆里的 MEMORY.md（分身笔记·memory_md 列）：模板首行身份行
             `我是 {{display_name}}，{{owner_nickname}} 在唤星（Astra）的 AI 分身…` 同样建档即渲染、不再刷新
             → 分身笔记里一直是旧分身名 + 旧主人昵称（主人档案已刷新但分身笔记没刷 = 本次 bug）。

        本方法在两处调用以收口：
          - profile 更新（主人设/改昵称）：previous_nickname=旧昵称、current_nickname=新昵称。
          - onboarding（每次登录幂等自愈）：current_nickname=当前昵称、previous_nickname=None，
            仅修值为手机号掩码的存量坏分身。

        维度① display_name 只动「确属系统派生」的分身（builtin_agent_key 非空，含主脑 assistant）
        且名字属系统派生形态、主人标识片段 ∈ {手机号掩码, previous_nickname}，全局唯一化（排除自身行）。
        维度② USER.md `称呼:` 行、维度③ MEMORY.md 首行身份行都是 owner 维度（所有分身共享语义），故对
        owner 名下全部分身生效，但同样只在被烙进的旧值处替换（维度③分身名段按系统派生形态识别、刷成
        分身当前权威 display_name，主人称呼段刷成新昵称）。三个维度都绝不 clobber 用户手动改的名字/称呼，
        也不动记忆正文。任一维度改动即 bump profile_revision 并下发同步事件（Runtime 据 profile_revision
        变化重拉记忆文件，分身随即按新昵称称呼主人、分身笔记显示新身份）。

        返回被改名后的新 display_name 列表（供日志/测试断言）。
        """
        import sqlalchemy as sa

        nick = (current_nickname or '').strip()
        # 新昵称为空 / 仍是手机号掩码 → 无可改进，避免无谓 churn（新用户首登场景在此短路）。
        if not nick or PHONE_MASK_NICKNAME_RE.match(nick):
            return []
        prev = (previous_nickname or '').strip()

        # user_md（USER.md 称呼）是 owner 维度，所有分身共享语义 → 不再按 builtin_agent_key 过滤，
        # 取 owner 名下全部未删分身；display_name 维度在循环内按 builtin_agent_key 再 gate。
        rows = (
            (
                await db.execute(
                    sa.select(HasnAgents).where(
                        HasnAgents.owner_id == owner_id,
                        HasnAgents.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        renamed: list[str] = []
        for agent in rows:
            touched = False

            # 维度①：分身自己的 display_name 后缀（仅系统播种的内置/默认分身）。
            if agent.builtin_agent_key:
                current = (agent.display_name or '').strip()
                candidate = compute_seeded_name_refresh(
                    current,
                    profession=getattr(agent, 'profession', None),
                    new_nickname=nick,
                    previous_nickname=prev,
                )
                if candidate is not None:
                    new_name = await self._resolve_unique_display_name_excluding(
                        db, desired=candidate, exclude_id=agent.id
                    )
                    if new_name != current:
                        agent.display_name = new_name
                        renamed.append(new_name)
                        touched = True

            # 维度②：USER.md `称呼:` 行（owner 维度，所有分身）——把旧称呼刷成新昵称。
            new_user_md = compute_user_md_owner_refresh(
                getattr(agent, 'user_md', None), new_nickname=nick, previous_nickname=prev
            )
            if new_user_md is not None:
                agent.user_md = new_user_md
                touched = True

            # 维度③：MEMORY.md（分身笔记）首行身份行——把烙进的旧分身名 / 旧主人昵称刷成当前值。
            # 用 agent.display_name（已经过维度①刷新）作分身名段权威值，保证分身笔记与分身名一致。
            new_memory_md = compute_memory_md_identity_refresh(
                getattr(agent, 'memory_md', None),
                profession=getattr(agent, 'profession', None),
                current_display_name=agent.display_name,
                new_nickname=nick,
                previous_nickname=prev,
            )
            if new_memory_md is not None:
                agent.memory_md = new_memory_md
                touched = True

            if touched:
                if hasattr(agent, 'profile_revision'):
                    agent.profile_revision = (agent.profile_revision or 1) + 1
                await db.flush()
                await self.gateway.append_agent_sync_event(
                    db, owner_id=owner_id, agent=agent, event_type='agent.updated'
                )
        return renamed

    async def _resolve_unique_display_name_excluding(self, db: AsyncSession, *, desired: str, exclude_id: int) -> str:
        """全局未占用的 display_name（排除自身行）：desired → desired+数字后缀 → desired-<rand>。"""
        import sqlalchemy as sa

        async def _taken(name: str) -> bool:
            name = (name or '').strip()
            if not name:
                return False
            row = (
                await db.execute(
                    sa
                    .select(HasnAgents.id)
                    .where(
                        HasnAgents.display_name == name,
                        HasnAgents.deleted_at.is_(None),
                        HasnAgents.id != exclude_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row is not None

        desired = (desired or '').strip() or 'AI 分身'
        if not await _taken(desired):
            return desired
        for suffix in range(2, 1000):
            candidate = f'{desired}{suffix}'
            if not await _taken(candidate):
                return candidate
        import uuid

        return f'{desired}-{uuid.uuid4().hex[:4]}'

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
            (
                await db.execute(
                    sa.text(
                        f"""
                    SELECT t.template_id, v.version, v.bundle_slug, v.command_key, v.hermes_yaml,
                           COALESCE(v.content_hash, v.file_hash) AS content_hash
                    FROM hasn_marketplace.marketplace_template t
                    JOIN hasn_marketplace.marketplace_template_version v ON v.template_id = t.template_id
                    WHERE t.template_id = :package_id
                      AND t.template_type = 'skill_pack'
                      {ver_filter}
                    LIMIT 1
                    """
                    ),
                    {'package_id': package_id, 'version': version},
                )
            )
            .mappings()
            .one_or_none()
        )
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
            current_bundles = [
                b for b in current_bundles if not (isinstance(b, dict) and b.get('template_id') == package_id)
            ]
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
        online_map = await _presence_query.get_online_map([snapshot.hasn_id for snapshot in snapshots])
        for snapshot in snapshots:
            snapshot.online_status = 'online' if online_map.get(snapshot.hasn_id) else 'offline'
        # 技能显示层元数据（SKILLNAME）：skills 只是 skill_id slug 清单，命令浮层要显示真友好名+
        # 描述需从 marketplace/个人技能目录反查。跨全部 snapshot 求 skill_id 并集一次批量解析，
        # 再按各 snapshot 自身 skill_id 回填 skill_display。best-effort，查不到留空由 daemon humanize。
        skill_ids_by_snapshot = {snapshot.hasn_id: _normalize_skill_ids(snapshot.skills) for snapshot in snapshots}
        all_skill_ids = {sid for ids in skill_ids_by_snapshot.values() for sid in ids}
        if all_skill_ids:
            skill_display_map = await _resolve_skill_display(db, request.owner_id, list(all_skill_ids))
            if skill_display_map:
                for snapshot in snapshots:
                    scoped = {
                        sid: skill_display_map[sid]
                        for sid in skill_ids_by_snapshot.get(snapshot.hasn_id, [])
                        if sid in skill_display_map
                    }
                    if scoped:
                        snapshot.skill_display = scoped
        server_revision = max(
            (snapshot.profile_revision for snapshot in snapshots), default=request.after_revision or 0
        )
        # 公共技能集合修订号（全局，doc12 §3.4）：随 is_common 成员/版本变化而变；
        # daemon 据其变化触发全量活跃绑定 re-provision，Runtime 再拉最新公共技能。
        _, common_skills_revision = await get_common_skill_snapshot(db)
        # 平台默认配置修订号（全局，PDC）：节点媒体模型 + agent 运行时四槽默认变化即变；
        # daemon 据其变化拉取 /platform-config 全量配置并应用（media 覆盖层 + 活跃绑定 re-provision）。
        from backend.app.hasn.service.platform_default_config_service import platform_default_config_service

        _, platform_config_revision = await platform_default_config_service.get_effective_config(db)
        return AgentSyncResponse(
            owner_id=request.owner_id,
            server_revision=server_revision,
            agents=snapshots,
            common_skills_revision=common_skills_revision,
            platform_config_revision=platform_config_revision,
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


async def _resolve_skill_display(
    db: AsyncSession, owner_id: str, skill_ids: Sequence[str]
) -> dict[str, dict[str, str | None]]:
    """据 skill_id 集合从 marketplace/个人技能目录批量解析显示名+描述。

    `skills` 本身只是 skill_id slug 清单（无友好名/描述），命令浮层要显示真名+描述需从
    目录反查。best-effort：查不到的 skill_id 不进 map（daemon 侧 humanize slug 兜底）。
    返回 `{skill_id: {name, description}}`，供 daemon 覆盖本地技能镜像的显示层字段。
    """
    ids = [sid for sid in dict.fromkeys(skill_ids) if sid]
    if not ids:
        return {}
    import sqlalchemy as sa

    from backend.app.marketplace.model.marketplace_personal_skill import MarketplacePersonalSkill
    from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill

    display: dict[str, dict[str, str | None]] = {}
    # 市场技能：按 skill_id 命中，中文名/描述优先（对齐 marketplace_skill_service 口径）
    mk_result = await db.execute(
        sa.select(
            MarketplaceSkill.skill_id,
            MarketplaceSkill.name,
            MarketplaceSkill.name_zh,
            MarketplaceSkill.name_en,
            MarketplaceSkill.description_zh,
            MarketplaceSkill.description_en,
        ).where(MarketplaceSkill.skill_id.in_(ids))
    )
    for market_row in mk_result.all():
        name = (market_row.name_zh or market_row.name_en or market_row.name or '').strip()
        if not name:
            continue
        desc = market_row.description_zh or market_row.description_en
        display[market_row.skill_id] = {'name': name, 'description': desc.strip() if desc else None}
    # 个人技能：owner 内 scope（hasn_id），按 slug 或 personal_skill_id 命中；不覆盖市场命中项
    ps_result = await db.execute(
        sa.select(
            MarketplacePersonalSkill.personal_skill_id,
            MarketplacePersonalSkill.slug,
            MarketplacePersonalSkill.name,
            MarketplacePersonalSkill.description,
        ).where(
            MarketplacePersonalSkill.hasn_id == owner_id,
            sa.or_(
                MarketplacePersonalSkill.slug.in_(ids),
                MarketplacePersonalSkill.personal_skill_id.in_(ids),
            ),
        )
    )
    id_set = set(ids)
    for personal_row in ps_result.all():
        name = (personal_row.name or '').strip()
        if not name:
            continue
        entry: dict[str, str | None] = {
            'name': name,
            'description': personal_row.description.strip() if personal_row.description else None,
        }
        # skills 里可能用 slug 或 personal_skill_id 引用，命中哪个补哪个键
        for candidate in (personal_row.slug, personal_row.personal_skill_id):
            if candidate and candidate in id_set and candidate not in display:
                display[candidate] = entry
    return display


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
        'memory_md': request.memory_md
        if request.memory_md is not None
        else getattr(template, 'default_memory_md', None),
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
        builtin_agent_key=getattr(agent, 'builtin_agent_key', None),
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

        同事务登记 owner→agent 控制边投影命令，由关系 relay 经 IM role 的
        RelationGateway 幂等落 social+5 关系。

        :param db: 数据库会话
        :param obj: 创建HASN Agent 参数
        :param user_id: 用户 ID
        :return: Agent 信息及 JWT
        """
        await hasn_agents_dao.create(db, obj)
        from backend.app.hasn.service.hasn_relation_command_outbox_service import (
            hasn_relation_command_outbox_service,
        )

        await hasn_relation_command_outbox_service.enqueue_owner_agent_control_edge(
            db,
            owner_hasn_id=obj.owner_id,
            agent_hasn_id=obj.hasn_id,
        )

        # 签发 Agent JWT（scopes 已退役·实施102 S0：JWT 不再携带 scopes，授权只看三态）
        from backend.common.security.agent_jwt import create_agent_access_token

        agent_token = await create_agent_access_token(
            agent_hasn_id=obj.hasn_id,
            agent_name=obj.display_name,
            owner_hasn_id=obj.owner_id,
            owner_user_id=user_id,
        )

        return {
            'hasn_id': obj.hasn_id,
            'owner_id': obj.owner_id,
            'name': obj.display_name,
            'access_token': agent_token.access_token,
            # scopes 已退役（实施102 S0）：恒空占位，兼容旧 daemon 反序列化。
            'scopes': [],
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
