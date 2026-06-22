"""创作运营领域服务（设计 00 §3/§4/§6）：定位→创作→审核→发布→复盘全链路业务编排。

在 codegen CRUD 之上做归属隔离 + 状态机推进 + 双模归属落地。一切查询经 `apply_scope` 裁剪
（个人按 user_id；企业按 enterprise_id + 角色 assignee）。子对象（profile/account/content/...）
归属继承父 project，便于角色裁剪查询免 join。

工具映射（施工 91 §4.2）：本服务方法被云端 MCP handler（creator_tool_handlers）进程内直调，
也被 owner app 端点复用。进化回写（insight.log）在 insight_service；本服务不碰对外发送。
"""

from __future__ import annotations

import datetime

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import sqlalchemy as sa

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn_creator.model.account import Account
from backend.app.hasn_creator.model.competitor import Competitor
from backend.app.hasn_creator.model.content import Content
from backend.app.hasn_creator.model.content_insight import ContentInsight
from backend.app.hasn_creator.model.content_stage import ContentStage
from backend.app.hasn_creator.model.draft import Draft
from backend.app.hasn_creator.model.media import Media
from backend.app.hasn_creator.model.playbook import Playbook
from backend.app.hasn_creator.model.profile import Profile
from backend.app.hasn_creator.model.project import Project
from backend.app.hasn_creator.model.publish import Publish
from backend.app.hasn_creator.model.topic import Topic
from backend.app.hasn_creator.model.viral_pattern import ViralPattern
from backend.app.hasn_creator.service.scope_context import (
    CreatorScope,
    apply_scope,
    can_manage_assignment,
    ownership_fields,
    validate_enterprise_member_hasn_id,
)
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 内容状态机合法迁移（设计 §3.2）。键=当前态，值=允许的下一态集合。
_CONTENT_TRANSITIONS: dict[str, set[str]] = {
    'idea': {'researching', 'drafting', 'archived'},
    'researching': {'drafting', 'archived'},
    'drafting': {'reviewing', 'archived'},
    'reviewing': {'ready', 'drafting', 'archived'},  # 打回回 drafting
    'ready': {'published', 'drafting', 'archived'},
    'published': {'analyzing', 'completed', 'archived'},
    'analyzing': {'completed', 'archived'},
    'completed': {'archived'},
    'archived': set(),
}

# 发布状态机（设计 §3.3）。
_PUBLISH_TRANSITIONS: dict[str, set[str]] = {
    'draft': {'pending_review'},
    'pending_review': {'approved', 'draft', 'failed'},
    'approved': {'publishing', 'published', 'failed'},
    'publishing': {'published', 'failed'},
    'published': set(),
    'failed': {'pending_review'},
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _to_dict(obj: Any) -> dict[str, Any]:
    """SQLAlchemy 行 → JSON-safe dict（全列；Decimal→float，datetime→isoformat）。"""
    return {c.name: _jsonable(getattr(obj, c.name)) for c in obj.__table__.columns}


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _child_ownership(project: Project) -> dict[str, Any]:
    """子对象继承父 project 的归属列（owner_scope/user_id/enterprise_id/assignee）。"""
    return {
        'owner_scope': project.owner_scope,
        'user_id': project.user_id,
        'enterprise_id': project.enterprise_id,
        'assignee': project.assignee,
    }


def _normalize_asset_refs(asset_refs: list | None) -> list[Any]:
    """校验阶段产出的素材引用 shape（doc19 §5.5：在历史云端引用之上加本地引用，**向后兼容不改既有形态**）。

    历史上 content_stage.asset_refs 是云端引用（封面/配图落私有桶），存为**裸字符串** `hasn://asset/...`
    （owner API schema `asset_refs: list[str]`）或 `{kind:'cloud', asset_uri}` 对象。reel 成片是重资产、
    本地优先不自动上云（doc19 N20），故扩出**本地引用**——成片字节留本机、只记路径 + 设备 node_id，webui
    据 `kind=='local'`（+ node_id）判「本机可直开 / 他机需该设备在线或显式上云」。

    **不重写既有形态**（避免破坏现有调用方/webui 的 round-trip）：
      - 裸字符串 `'hasn://asset/...'` → 原样保留（云端引用，必须非空；webui 视无 ``kind=='local'`` 即云端）。
      - 任意已有 dict（含 `{kind:'cloud', asset_uri}` / `{asset_uri}` / 其它历史形态）→ **非 local** 一律原样
        透传（不强校验、不改 shape，保护未知历史数据）。
      - **本地引用** `{kind:'local', path, node_id, uploaded?}` → **严格校验**（path/node_id 必填非空 str；
        uploaded 可选 bool 缺省 False）并归一（补 uploaded 默认）；这是本次唯一强约束的新 shape。

    仅对 **kind=='local'** 的非法 shape fail-fast 抛 RequestError（不写脏本地引用）；非 list / 含非 str 非
    dict 元素也拒。``None`` / 空数组 → 返回 ``[]``。
    """
    if not asset_refs:
        return []
    if not isinstance(asset_refs, list):
        raise errors.RequestError(msg='asset_refs 必须是数组')

    normalized: list[Any] = []
    for idx, ref in enumerate(asset_refs):
        # 历史形态：裸字符串 = 云端 hasn://asset/ 引用（owner API schema list[str]）。原样保留。
        if isinstance(ref, str):
            if not ref.strip():
                raise errors.RequestError(msg=f'asset_refs[{idx}] 云端引用不能是空字符串')
            normalized.append(ref)
            continue
        if not isinstance(ref, dict):
            raise errors.RequestError(msg=f'asset_refs[{idx}] 必须是字符串或对象（{{kind, ...}}）')

        if ref.get('kind') == 'local':
            # 本地引用：唯一强校验的新 shape（重资产成片本地优先，doc19 N20）。
            path = ref.get('path')
            node_id = ref.get('node_id')
            if not isinstance(path, str) or not path.strip():
                raise errors.RequestError(msg=f'asset_refs[{idx}] 本地引用缺少 path（非空字符串）')
            if not isinstance(node_id, str) or not node_id.strip():
                raise errors.RequestError(msg=f'asset_refs[{idx}] 本地引用缺少 node_id（非空字符串，标识所在设备）')
            uploaded = ref.get('uploaded', False)
            if not isinstance(uploaded, bool):
                raise errors.RequestError(msg=f'asset_refs[{idx}] uploaded 必须是布尔值')
            normalized.append({'kind': 'local', 'path': path, 'node_id': node_id, 'uploaded': uploaded})
        else:
            # 非 local 的既有 dict（cloud/历史形态）原样透传，保护现有 round-trip 与未知历史数据。
            normalized.append(ref)

    return normalized


class CreatorService:
    """创作运营领域服务（归属隔离 + 状态机）。"""

    # ============================ project / profile ============================

    @staticmethod
    async def _load_project(db: AsyncSession, *, project_id: int, user_id: int, scope: CreatorScope | None) -> Project:
        stmt = apply_scope(sa.select(Project).where(Project.id == project_id), Project, user_id=user_id, scope=scope)
        proj = (await db.execute(stmt)).scalars().first()
        if proj is None:
            raise errors.NotFoundError(msg='项目不存在或无权访问')
        return proj

    @staticmethod
    async def _validate_assignee_agent(db: AsyncSession, *, scope: CreatorScope | None, agent_id: str) -> None:
        """校验 assignee_agent_id 是本 owner 名下分身（同 deck/copilot）；不是则 404 不泄露他人分身是否存在。

        负责运营的分身只能绑自己名下（owner_hasn_id == 当前行动主人）。owner_hasn_id 缺失（未解析出主人）
        时一律拒——不许在无法核实归属的情况下落任何分身绑定（零信任边界）。
        """
        owner_hasn_id = scope.owner_hasn_id if scope else None
        if not owner_hasn_id:
            raise errors.NotFoundError(msg='指定的负责分身不存在或不属于你')
        row = (
            await db.execute(
                sa.select(HasnAgents.id).where(
                    HasnAgents.hasn_id == agent_id,
                    HasnAgents.owner_id == owner_hasn_id,
                )
            )
        ).first()
        if row is None:
            raise errors.NotFoundError(msg='指定的负责分身不存在或不属于你')

    @staticmethod
    async def create_project(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        name: str,
        description: str | None = None,
        primary_platform: str | None = None,
        pipeline_mode: str = 'semi-auto',
        playbook_id: int | None = None,
        assignee_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """建项目（运营单元根）+ 1:1 空画像。落双模归属。绑定分身须归本 owner（建时即校验）。"""
        if assignee_agent_id:
            await CreatorService._validate_assignee_agent(db, scope=scope, agent_id=assignee_agent_id)
        own = ownership_fields(scope, user_id=user_id)
        proj = Project(
            project_no=_gen_no('PROJ'),
            name=name,
            description=description,
            primary_platform=primary_platform,
            pipeline_mode=pipeline_mode,
            playbook_id=playbook_id,
            assignee_agent_id=assignee_agent_id,
            status='active',
            **own,
        )
        db.add(proj)
        await db.flush()
        # 1:1 画像占位（profile.set 后续填充），归属继承 project。
        prof = Profile(project_id=proj.id, **_child_ownership(proj))
        db.add(prof)
        await db.flush()
        return _to_dict(proj)

    @staticmethod
    async def list_projects(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        stmt = apply_scope(sa.select(Project), Project, user_id=user_id, scope=scope)
        if status:
            stmt = stmt.where(Project.status == status)
        stmt = stmt.order_by(Project.created_time.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]

    @staticmethod
    async def get_project(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> dict[str, Any]:
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        prof = (await db.execute(sa.select(Profile).where(Profile.project_id == project_id))).scalars().first()
        accounts = (await db.execute(sa.select(Account).where(Account.project_id == project_id))).scalars().all()
        content_count = (
            await db.execute(sa.select(sa.func.count()).select_from(Content).where(Content.project_id == project_id))
        ).scalar() or 0
        pending = (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(Content)
                .where(Content.project_id == project_id, Content.status == 'reviewing')
            )
        ).scalar() or 0
        data = _to_dict(proj)
        data['profile'] = _to_dict(prof) if prof else None
        data['accounts'] = [_to_dict(a) for a in accounts]
        data['content_count'] = int(content_count)
        data['pending_review_count'] = int(pending)
        return data

    @staticmethod
    async def update_project(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        # 改绑负责分身（assignee_agent_id）须先校验新分身归本 owner（改绑不能随便改成别人的分身）。
        new_agent = fields.get('assignee_agent_id')
        if new_agent:
            await CreatorService._validate_assignee_agent(db, scope=scope, agent_id=new_agent)
        allowed = {
            'name',
            'description',
            'primary_platform',
            'pipeline_mode',
            'playbook_id',
            'assignee_agent_id',
            'status',
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(proj, k, v)
        await db.flush()
        return _to_dict(proj)

    # 项目下所有子对象表（双模归属冗余 assignee）—— reassign 时整体级联迁负责人。
    _CHILD_MODELS = (Profile, Account, Competitor, Topic, Content, ContentStage, ContentInsight, Publish, Draft, Media)

    @staticmethod
    async def reassign_project(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        new_assignee: str,
    ) -> dict[str, Any]:
        """企业主编把项目转给另一名成员（§6.7 双模归属落地）。

        - 仅企业主编（owner/admin）可操作；个人/运营调用一律拒（`can_manage_assignment`）。
        - new_assignee 必须是本企业 approved 成员的主人 hasn_id（不许转给企业外的人）。
        - 整体级联：project + 全部子对象的 assignee 一并改写，使新负责人在「我的」视图看得到全套。
        """
        if not can_manage_assignment(scope):
            raise errors.ForbiddenError(msg='只有企业主编可以分配/转移项目负责人')
        assert scope is not None and scope.enterprise_id is not None  # can_manage_assignment 已保证
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        if not await validate_enterprise_member_hasn_id(
            db, enterprise_id=scope.enterprise_id, owner_hasn_id=new_assignee
        ):
            raise errors.RequestError(msg='目标负责人不是本企业成员')
        proj.assignee = new_assignee
        for model in CreatorService._CHILD_MODELS:
            await db.execute(
                sa.update(model).where(model.project_id == project_id).values(assignee=new_assignee)
            )
        await db.flush()
        return _to_dict(proj)

    @staticmethod
    async def get_profile(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> dict[str, Any]:
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        prof = (await db.execute(sa.select(Profile).where(Profile.project_id == project_id))).scalars().first()
        if prof is None:
            raise errors.NotFoundError(msg='画像不存在')
        return _to_dict(prof)

    @staticmethod
    async def set_profile(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """设置/更新画像（upsert，1:1）。"""
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        prof = (await db.execute(sa.select(Profile).where(Profile.project_id == project_id))).scalars().first()
        if prof is None:
            prof = Profile(project_id=project_id, **_child_ownership(proj))
            db.add(prof)
        allowed = {
            'niche',
            'sub_niche',
            'persona',
            'target_audience',
            'tone',
            'keywords',
            'content_pillars',
            'posting_frequency',
            'best_posting_time',
            'style_references',
            'taboo_topics',
            'bio',
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(prof, k, v)
        await db.flush()
        return _to_dict(prof)

    @staticmethod
    async def analyze_profile(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> dict[str, Any]:
        """辅助定位：返回竞品 + 当前画像 + 草案骨架（真实数据，供分身据此提炼后调 profile.set）。"""
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        prof = (await db.execute(sa.select(Profile).where(Profile.project_id == project_id))).scalars().first()
        comps = (await db.execute(sa.select(Competitor).where(Competitor.project_id == project_id))).scalars().all()
        return {
            'project': _to_dict(proj),
            'current_profile': _to_dict(prof) if prof else None,
            'competitors': [_to_dict(c) for c in comps],
            'draft_skeleton': {
                'niche': prof.niche if prof else None,
                'persona': '（建议：基于赛道与竞品提炼差异化人设）',
                'target_audience': '（建议：明确核心受众画像）',
                'content_pillars': [],
                'tone': None,
                'posting_frequency': None,
            },
            'note': '草案骨架仅为占位，请分身基于竞品与赛道真实提炼后调用 profile.set 落定（零 fake）',
        }

    # ============================ account / competitor ============================

    @staticmethod
    async def add_account(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        platform: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        acc = Account(
            project_id=project_id,
            platform=platform,
            platform_uid=fields.get('platform_uid'),
            nickname=fields.get('nickname'),
            home_url=fields.get('home_url'),
            bio=fields.get('bio'),
            is_primary=bool(fields.get('is_primary', False)),
            notes=fields.get('notes'),
            **_child_ownership(proj),
        )
        db.add(acc)
        await db.flush()
        return _to_dict(acc)

    @staticmethod
    async def list_accounts(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> list[dict[str, Any]]:
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        rows = (await db.execute(sa.select(Account).where(Account.project_id == project_id))).scalars().all()
        return [_to_dict(a) for a in rows]

    @staticmethod
    async def log_competitor(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        name: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        comp = Competitor(
            project_id=project_id,
            name=name,
            platform=fields.get('platform'),
            url=fields.get('url'),
            follower_count=int(fields.get('follower_count') or 0),
            avg_likes=int(fields.get('avg_likes') or 0),
            content_style=fields.get('content_style'),
            strengths=fields.get('strengths') or [],
            notes=fields.get('notes'),
            tags=fields.get('tags') or [],
            **_child_ownership(proj),
        )
        db.add(comp)
        await db.flush()
        return _to_dict(comp)

    @staticmethod
    async def list_competitors(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> list[dict[str, Any]]:
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        rows = (await db.execute(sa.select(Competitor).where(Competitor.project_id == project_id))).scalars().all()
        return [_to_dict(c) for c in rows]

    # ============================ topic ============================

    @staticmethod
    async def suggest_topics(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        topics: list[dict[str, Any]],
        batch_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """写入分身生成的选题到选题池（分身提供 title/reason/angles，service 落库，零 fake）。"""
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        out = []
        for t in topics:
            row = Topic(
                project_id=project_id,
                title=str(t.get('title') or '').strip()[:200] or '未命名选题',
                potential_score=float(t.get('potential_score') or 0),
                heat_index=float(t.get('heat_index') or 0),
                reason=t.get('reason'),
                keywords=t.get('keywords') or [],
                creative_angles=t.get('creative_angles') or [],
                status=0,
                batch_date=batch_date,
                **_child_ownership(proj),
            )
            db.add(row)
            out.append(row)
        await db.flush()
        return [_to_dict(r) for r in out]

    @staticmethod
    async def list_topics(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        status: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        stmt = sa.select(Topic).where(Topic.project_id == project_id)
        if status is not None:
            stmt = stmt.where(Topic.status == status)
        stmt = stmt.order_by(Topic.potential_score.desc(), Topic.created_time.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]

    # ============================ content / stage ============================

    @staticmethod
    async def create_content(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        title: str,
        content_tracks: str = 'article',
        target_platforms: list | None = None,
        topic_id: int | None = None,
        viral_pattern_id: int | None = None,
        playbook_id: int | None = None,
        pipeline_mode: str | None = None,
        created_by_agent_id: str | None = None,
    ) -> dict[str, Any]:
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        content = Content(
            content_no=_gen_no('CONT'),
            project_id=project_id,
            created_by_agent_id=created_by_agent_id,
            title=title,
            status='idea',
            content_tracks=content_tracks or 'article',
            pipeline_mode=pipeline_mode,
            target_platforms=target_platforms or [],
            topic_id=topic_id,
            viral_pattern_id=viral_pattern_id,
            playbook_id=playbook_id,
            metadata_json={},
            **_child_ownership(proj),
        )
        db.add(content)
        await db.flush()
        # 采纳选题 → 标记 topic 已采纳 + 关联
        if topic_id:
            topic = (await db.execute(sa.select(Topic).where(Topic.id == topic_id))).scalars().first()
            if topic and topic.project_id == project_id:
                topic.status = 1
                topic.content_id = content.id
                await db.flush()
        return _to_dict(content)

    @staticmethod
    async def _load_content(db: AsyncSession, *, content_id: int, user_id: int, scope: CreatorScope | None) -> Content:
        stmt = apply_scope(sa.select(Content).where(Content.id == content_id), Content, user_id=user_id, scope=scope)
        c = (await db.execute(stmt)).scalars().first()
        if c is None:
            raise errors.NotFoundError(msg='内容不存在或无权访问')
        return c

    @staticmethod
    async def list_content(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int | None = None,
        status: str | None = None,
        review_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        stmt = apply_scope(sa.select(Content), Content, user_id=user_id, scope=scope)
        if project_id is not None:
            stmt = stmt.where(Content.project_id == project_id)
        if status:
            stmt = stmt.where(Content.status == status)
        if review_status:
            stmt = stmt.where(Content.review_status == review_status)
        stmt = stmt.order_by(Content.created_time.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]

    @staticmethod
    async def get_content(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, content_id: int
    ) -> dict[str, Any]:
        c = await CreatorService._load_content(db, content_id=content_id, user_id=user_id, scope=scope)
        stages = (
            (
                await db.execute(
                    sa
                    .select(ContentStage)
                    .where(ContentStage.content_id == content_id)
                    .order_by(ContentStage.created_time.asc())
                )
            )
            .scalars()
            .all()
        )
        pubs = (await db.execute(sa.select(Publish).where(Publish.content_id == content_id))).scalars().all()
        data = _to_dict(c)
        data['stages'] = [_to_dict(s) for s in stages]
        data['publishes'] = [_to_dict(p) for p in pubs]
        return data

    @staticmethod
    async def update_content(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        content_id: int,
        status: str | None = None,
        title: str | None = None,
        review_status: str | None = None,
        review_note: str | None = None,
        reviewer_user_id: int | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        c = await CreatorService._load_content(db, content_id=content_id, user_id=user_id, scope=scope)
        if status is not None and status != c.status:
            allowed = _CONTENT_TRANSITIONS.get(c.status, set())
            if status not in allowed:
                raise errors.RequestError(msg=f'内容状态不可从 {c.status} 迁移到 {status}')
            c.status = status
        if title is not None:
            c.title = title
        if review_status is not None:
            c.review_status = review_status
            c.reviewed_at = datetime.datetime.now(datetime.timezone.utc)
            if reviewer_user_id is not None:
                c.reviewer_user_id = reviewer_user_id
        if review_note is not None:
            c.review_note = review_note
        if metadata is not None:
            merged = dict(c.metadata_json or {})
            merged.update(metadata)
            c.metadata_json = merged
        await db.flush()
        return _to_dict(c)

    @staticmethod
    async def save_stage(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        content_id: int,
        stage: str,
        content_text: str | None = None,
        asset_refs: list | None = None,
        source_type: str = 'ai_generated',
    ) -> dict[str, Any]:
        """保存阶段产出（同 content+stage 已存在则 bump version 新建一版，留迭代痕迹）。

        asset_refs 支持云端引用（``{kind:'cloud', asset_uri}``）+ 本地引用（``{kind:'local', path,
        node_id, uploaded}``，doc19 §5.5：reel 成片重资产本地优先不自动上云）。shape 经 _normalize_asset_refs
        校验，非法 shape fail-fast 抛 RequestError（不写脏数据）。
        """
        c = await CreatorService._load_content(db, content_id=content_id, user_id=user_id, scope=scope)
        normalized_refs = _normalize_asset_refs(asset_refs)
        prev = (
            await db.execute(
                sa.select(sa.func.coalesce(sa.func.max(ContentStage.version), 0)).where(
                    ContentStage.content_id == content_id, ContentStage.stage == stage
                )
            )
        ).scalar() or 0
        row = ContentStage(
            content_id=content_id,
            project_id=c.project_id,
            stage=stage,
            content_text=content_text,
            asset_refs=normalized_refs,
            status='draft',
            version=int(prev) + 1,
            source_type=source_type,
            owner_scope=c.owner_scope,
            user_id=c.user_id,
            enterprise_id=c.enterprise_id,
            assignee=c.assignee,
        )
        db.add(row)
        await db.flush()
        return _to_dict(row)

    # ============================ publish ============================

    @staticmethod
    async def submit_publish(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        content_id: int,
        account_id: int,
        platform: str | None = None,
        method: str = 'manual_assist',
        publish_note: str | None = None,
    ) -> dict[str, Any]:
        """请求发布：落 pending_review 等主人审，绝不绕过审核（C3 铁律）。返回真实状态。"""
        c = await CreatorService._load_content(db, content_id=content_id, user_id=user_id, scope=scope)
        acc = (await db.execute(sa.select(Account).where(Account.id == account_id))).scalars().first()
        if acc is None or acc.project_id != c.project_id:
            raise errors.RequestError(msg='平台账号不存在或不属于该内容所在项目')
        pub = Publish(
            content_id=content_id,
            account_id=account_id,
            project_id=c.project_id,
            platform=platform or acc.platform,
            method=method,
            status='pending_review',
            publish_note=publish_note,
            owner_scope=c.owner_scope,
            user_id=c.user_id,
            enterprise_id=c.enterprise_id,
            assignee=c.assignee,
        )
        db.add(pub)
        # 内容转待审（若还在 drafting/ready）
        if c.status in ('drafting', 'researching', 'idea'):
            c.status = 'reviewing'
        await db.flush()
        return {'publish_id': pub.id, 'status': pub.status, 'content_id': content_id, 'account_id': account_id}

    @staticmethod
    async def _load_publish(db: AsyncSession, *, publish_id: int, user_id: int, scope: CreatorScope | None) -> Publish:
        stmt = apply_scope(sa.select(Publish).where(Publish.id == publish_id), Publish, user_id=user_id, scope=scope)
        p = (await db.execute(stmt)).scalars().first()
        if p is None:
            raise errors.NotFoundError(msg='发布记录不存在或无权访问')
        return p

    @staticmethod
    async def list_publish(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int | None = None,
        content_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = apply_scope(sa.select(Publish), Publish, user_id=user_id, scope=scope)
        if project_id is not None:
            stmt = stmt.where(Publish.project_id == project_id)
        if content_id is not None:
            stmt = stmt.where(Publish.content_id == content_id)
        if status:
            stmt = stmt.where(Publish.status == status)
        stmt = stmt.order_by(Publish.created_time.desc()).limit(min(limit, 300))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]

    @staticmethod
    async def approve_publish(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, publish_id: int, approval_user_id: int
    ) -> dict[str, Any]:
        p = await CreatorService._load_publish(db, publish_id=publish_id, user_id=user_id, scope=scope)
        if 'approved' not in _PUBLISH_TRANSITIONS.get(p.status, set()):
            raise errors.RequestError(msg=f'发布状态 {p.status} 不可批准')
        p.status = 'approved'
        p.approval_user_id = approval_user_id
        p.approved_at = datetime.datetime.now(datetime.timezone.utc)
        await db.flush()
        return _to_dict(p)

    @staticmethod
    async def mark_published(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        publish_id: int,
        publish_url: str | None = None,
    ) -> dict[str, Any]:
        """人工辅助：主人发布后回填链接 → published；内容转 published/analyzing。"""
        p = await CreatorService._load_publish(db, publish_id=publish_id, user_id=user_id, scope=scope)
        if 'published' not in _PUBLISH_TRANSITIONS.get(p.status, set()):
            raise errors.RequestError(msg=f'发布状态 {p.status} 不可标记已发布')
        p.status = 'published'
        p.publish_url = publish_url
        p.published_at = datetime.datetime.now(datetime.timezone.utc)
        # 内容推进到 published（再到 analyzing 由数据采集触发）
        content = (await db.execute(sa.select(Content).where(Content.id == p.content_id))).scalars().first()
        if content and content.status in ('ready', 'reviewing'):
            content.status = 'published'
        await db.flush()
        return _to_dict(p)

    @staticmethod
    async def update_metrics(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        publish_id: int,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """回填发布数据指标（分身周期采集 / 主人手填）。"""
        p = await CreatorService._load_publish(db, publish_id=publish_id, user_id=user_id, scope=scope)
        for k in ('views', 'likes', 'comments', 'shares', 'favorites', 'new_followers'):
            if k in metrics and metrics[k] is not None:
                setattr(p, k, int(metrics[k]))
        extra = {
            k: v
            for k, v in metrics.items()
            if k not in ('views', 'likes', 'comments', 'shares', 'favorites', 'new_followers')
        }
        if extra:
            merged = dict(p.metrics_json or {})
            merged.update(extra)
            p.metrics_json = merged
        p.metrics_updated_at = datetime.datetime.now(datetime.timezone.utc)
        # 已发布内容进入数据跟踪态
        content = (await db.execute(sa.select(Content).where(Content.id == p.content_id))).scalars().first()
        if content and content.status == 'published':
            content.status = 'analyzing'
        await db.flush()
        return _to_dict(p)

    # 文案阶段优先级（成品包正文取最高优先且最新版本的阶段）。
    _BODY_STAGE_PRIORITY = ('final_draft', 'first_draft', 'outline')

    @staticmethod
    async def assemble_publish_package(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, publish_id: int
    ) -> dict[str, Any]:
        """组装 manual_assist 成品包（设计 §10 P0 主路径）：文案 + 封面/配图 + 话题标签 + 发布建议。

        供主人「复制成品包」手动发到平台后回填 url/数据（零外部依赖、零封号风险）。
        正文取最高优先阶段（final_draft>first_draft>outline）的最新版本；配图汇总各阶段 asset_refs；
        话题标签取 content.metadata_json.hashtags。``ready`` 标识正文是否齐备（无正文如实 False，不造假）。
        """
        p = await CreatorService._load_publish(db, publish_id=publish_id, user_id=user_id, scope=scope)
        content = (await db.execute(sa.select(Content).where(Content.id == p.content_id))).scalars().first()
        stages = (
            (
                await db.execute(
                    sa
                    .select(ContentStage)
                    .where(ContentStage.content_id == p.content_id)
                    .order_by(ContentStage.version.desc(), ContentStage.created_time.desc())
                )
            )
            .scalars()
            .all()
        )
        # 正文：按优先级取首个命中阶段的最新版本（stages 已按 version desc 排序）。
        body_stage = None
        for stage_name in CreatorService._BODY_STAGE_PRIORITY:
            body_stage = next((s for s in stages if s.stage == stage_name), None)
            if body_stage is not None:
                break
        cover_stage = next((s for s in stages if s.stage == 'cover'), None)
        cover_refs = list(cover_stage.asset_refs or []) if cover_stage else []
        # 配图：封面 refs 领先（成品包封面在前），再接其余各阶段 asset_refs（保序去重）。
        assets: list[Any] = list(cover_refs)
        seen: set[str] = {repr(ref) for ref in cover_refs}
        for s in stages:
            for ref in s.asset_refs or []:
                key = repr(ref)
                if key not in seen:
                    seen.add(key)
                    assets.append(ref)
        acc = (await db.execute(sa.select(Account).where(Account.id == p.account_id))).scalars().first()
        meta = content.metadata_json or {} if content else {}
        return {
            'publish_id': p.id,
            'content_id': p.content_id,
            'status': p.status,
            'method': p.method,
            'platform': p.platform,
            'title': content.title if content else None,
            'content_tracks': content.content_tracks if content else None,
            'body_text': body_stage.content_text if body_stage else None,
            'body_stage': body_stage.stage if body_stage else None,
            'cover': cover_refs[0] if cover_refs else None,
            'assets': assets,
            'hashtags': list(meta.get('hashtags') or []),
            'publish_note': p.publish_note,
            'publish_url': p.publish_url,
            'account': {
                'id': acc.id,
                'platform': acc.platform,
                'nickname': acc.nickname,
                'home_url': acc.home_url,
            }
            if acc
            else None,
            'ready': bool(body_stage and body_stage.content_text),
        }

    # ============================ insight（进化沉淀；回写在 M5/insight_service）============================

    @staticmethod
    async def log_insight(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        insight_type: str,
        summary: str,
        period: str | None = None,
        evidence_json: dict[str, Any] | None = None,
        proposed_action: dict[str, Any] | None = None,
        confidence: float | None = None,
        created_by_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """沉淀一条内容洞察并据 proposed_action **原子回写**进化（设计 §7，进化引擎灵魂）。

        三处回写（同事务，要么全成要么随事务回滚）：
        1. ``pillar_weight_delta`` → 累加进 `profile.pillar_weights`（下次按权重选支柱）；
        2. ``new_viral_pattern`` → 入 `viral_pattern` 库（source=ai_extracted；下次 pattern.search 命中）；
        3. ``playbook_patch`` → patch 本项目自有 playbook（内置/他人 playbook 不动，如实跳过）。

        `action_taken` 如实记录**实际**做了什么（零 fake：跳过的写明 skipped 原因），供留痕审计。
        """
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        action = proposed_action or {}
        action_taken: dict[str, Any] = {}

        # ① 画像支柱权重：累加 delta（下界 0），记 pillar_weights_updated_at。
        weight_delta = action.get('pillar_weight_delta') or {}
        if isinstance(weight_delta, dict) and weight_delta:
            prof = (await db.execute(sa.select(Profile).where(Profile.project_id == project_id))).scalars().first()
            if prof is not None:
                weights = dict(prof.pillar_weights or {})
                applied: dict[str, float] = {}
                for pillar, delta in weight_delta.items():
                    try:
                        new_w = round(float(weights.get(pillar, 0)) + float(delta), 4)
                    except (TypeError, ValueError):
                        continue
                    weights[pillar] = max(new_w, 0.0)
                    applied[pillar] = weights[pillar]
                if applied:
                    prof.pillar_weights = weights
                    prof.pillar_weights_updated_at = datetime.datetime.now(datetime.timezone.utc)
                    action_taken['pillar_weights'] = applied
            else:
                action_taken['pillar_weights_skipped'] = '画像不存在'

        # ② 爆款模式库：提炼新钩子/结构入库（归属继承 project；viral_pattern 无 assignee 列）。
        new_pattern = action.get('new_viral_pattern') or {}
        if isinstance(new_pattern, dict) and new_pattern.get('name') and new_pattern.get('pattern_type'):
            vp = ViralPattern(
                project_id=project_id,
                user_id=proj.user_id,
                enterprise_id=proj.enterprise_id,
                owner_scope=proj.owner_scope,
                name=str(new_pattern['name'])[:200],
                pattern_type=str(new_pattern['pattern_type'])[:24],
                template=new_pattern.get('template'),
                description=new_pattern.get('description'),
                example=new_pattern.get('example'),
                tags=new_pattern.get('tags') or [],
                source='ai_extracted',
                is_builtin=False,
                success_rate=Decimal(str(new_pattern['success_rate']))
                if new_pattern.get('success_rate') is not None
                else Decimal('0'),
            )
            db.add(vp)
            await db.flush()
            action_taken['viral_pattern_id'] = vp.id

        # ③ 账号打法：patch 本项目自有 playbook（内置/他人不动，如实跳过）。
        playbook_patch = action.get('playbook_patch') or {}
        if isinstance(playbook_patch, dict) and playbook_patch and proj.playbook_id:
            pb = (await db.execute(sa.select(Playbook).where(Playbook.id == proj.playbook_id))).scalars().first()
            if pb is None:
                action_taken['playbook_skipped'] = 'playbook 不存在'
            elif pb.is_builtin or (pb.user_id is not None and pb.user_id != proj.user_id):
                action_taken['playbook_skipped'] = '内置或他人 playbook，不可改'
            else:
                allowed = {'goal', 'content_strategy', 'cadence', 'tone_guide', 'red_lines'}
                patched = [k for k, v in playbook_patch.items() if k in allowed and v is not None]
                for k in patched:
                    setattr(pb, k, playbook_patch[k])
                action_taken['playbook_patched'] = patched

        # 留痕：evidence 存 proposed_action 原文，action_taken 存实际动作。
        evidence = dict(evidence_json or {})
        if action:
            evidence['proposed_action'] = action
        row = ContentInsight(
            project_id=project_id,
            created_by_agent_id=created_by_agent_id,
            period=period,
            insight_type=insight_type or 'lesson',
            summary=summary or '',
            evidence_json=evidence,
            action_taken=action_taken,
            confidence=Decimal(str(confidence)) if confidence is not None else Decimal('0'),
            **_child_ownership(proj),
        )
        db.add(row)
        await db.flush()
        return _to_dict(row)

    # ============================ viral_pattern / report ============================

    @staticmethod
    async def search_patterns(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int | None = None,
        pattern_type: str | None = None,
        query: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """搜爆款模式库：本项目 + 自己的 + 全局内置（project_id NULL 且 is_builtin）。"""
        own_filter = sa.or_(
            ViralPattern.user_id == user_id,
            sa.and_(ViralPattern.project_id.is_(None), ViralPattern.is_builtin.is_(True)),
        )
        if scope is not None and scope.is_enterprise:
            own_filter = sa.or_(own_filter, ViralPattern.enterprise_id == scope.enterprise_id)
        stmt = sa.select(ViralPattern).where(own_filter)
        if project_id is not None:
            stmt = stmt.where(sa.or_(ViralPattern.project_id == project_id, ViralPattern.project_id.is_(None)))
        if pattern_type:
            stmt = stmt.where(ViralPattern.pattern_type == pattern_type)
        if query:
            like = f'%{query}%'
            stmt = stmt.where(sa.or_(ViralPattern.name.ilike(like), ViralPattern.template.ilike(like)))
        stmt = stmt.order_by(ViralPattern.success_rate.desc(), ViralPattern.usage_count.desc()).limit(min(limit, 100))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]

    @staticmethod
    async def report_overview(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int | None = None
    ) -> dict[str, Any]:
        """项目/全部数据总览：内容状态分布 + 发布数据汇总（复盘 + 简报数据源）。"""
        c_stmt = apply_scope(sa.select(Content.status, sa.func.count()), Content, user_id=user_id, scope=scope)
        if project_id is not None:
            c_stmt = c_stmt.where(Content.project_id == project_id)
        c_stmt = c_stmt.group_by(Content.status)
        status_rows = (await db.execute(c_stmt)).all()
        status_dist = {row[0]: int(row[1]) for row in status_rows}

        p_stmt = apply_scope(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(Publish.views), 0),
                sa.func.coalesce(sa.func.sum(Publish.likes), 0),
                sa.func.coalesce(sa.func.sum(Publish.comments), 0),
                sa.func.coalesce(sa.func.sum(Publish.shares), 0),
                sa.func.coalesce(sa.func.sum(Publish.favorites), 0),
                sa.func.coalesce(sa.func.sum(Publish.new_followers), 0),
            ).where(Publish.status == 'published'),
            Publish,
            user_id=user_id,
            scope=scope,
        )
        if project_id is not None:
            p_stmt = p_stmt.where(Publish.project_id == project_id)
        prow = (await db.execute(p_stmt)).first()
        return {
            'project_id': project_id,
            'content_status_distribution': status_dist,
            'content_total': sum(status_dist.values()),
            'published_count': int(prow[0]) if prow else 0,
            'metrics': {
                'views': int(prow[1]) if prow else 0,
                'likes': int(prow[2]) if prow else 0,
                'comments': int(prow[3]) if prow else 0,
                'shares': int(prow[4]) if prow else 0,
                'favorites': int(prow[5]) if prow else 0,
                'new_followers': int(prow[6]) if prow else 0,
            },
        }


creator_service = CreatorService()
