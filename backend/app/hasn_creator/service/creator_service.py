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
from backend.app.hasn_creator.model.platform import Platform
from backend.app.hasn_creator.model.playbook import Playbook
from backend.app.hasn_creator.model.profile import Profile
from backend.app.hasn_creator.model.project import Project
from backend.app.hasn_creator.model.publish import Publish
from backend.app.hasn_creator.model.topic import Topic
from backend.app.hasn_creator.model.viral_pattern import ViralPattern
from backend.app.hasn_creator.model.work import Work
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

    # ============================ platform 目录（S1）============================

    @staticmethod
    async def list_platforms(db: AsyncSession) -> list[dict[str, Any]]:
        """列平台目录（选择制，含主页根 URL/主页模板/指标口径），按 sort 升序。

        平台目录是全局内置 seed（无归属裁剪）；前端 PlatformSelect、Agent 选平台/取主页模板均读此。
        """
        rows = (await db.execute(sa.select(Platform).order_by(Platform.sort.asc(), Platform.id.asc()))).scalars().all()
        return [_to_dict(p) for p in rows]

    @staticmethod
    async def _platform_index(db: AsyncSession) -> dict[str, Platform]:
        """平台 key → Platform 行的索引（供校验/主页必填判定复用）。"""
        rows = (await db.execute(sa.select(Platform))).scalars().all()
        return {p.key: p for p in rows}

    @staticmethod
    async def validate_platform(db: AsyncSession, *, platform: str | None) -> None:
        """校验 platform ∈ 目录（§4.3，零 fake）。

        目录未配置（seed 未落）时 fail-open 放行，避免破坏未 seed 的环境；目录非空则严格校验。
        """
        if not platform:
            return
        index = await CreatorService._platform_index(db)
        if index and platform not in index:
            raise errors.RequestError(msg=f'平台「{platform}」不在平台目录中，请从目录中选择')

    @staticmethod
    async def platform_requires_home_url(db: AsyncSession, *, platform: str | None) -> bool:
        """该平台是否要求 home_url 必填（has_public_home=true 的平台必填；公众号/视频号等豁免）。"""
        if not platform:
            return False
        index = await CreatorService._platform_index(db)
        row = index.get(platform)
        if row is None:
            # 目录未配置或平台未知：不强制（校验在 validate_platform 处兜；此处只管必填口径）
            return False
        return bool(row.has_public_home)

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
    _CHILD_MODELS = (
        Profile,
        Account,
        Competitor,
        Topic,
        Content,
        ContentStage,
        ContentInsight,
        Publish,
        Draft,
        Media,
        Work,
    )

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
            await db.execute(sa.update(model).where(model.project_id == project_id).values(assignee=new_assignee))
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
        """加平台账号（S4 §6.3）：platform 校验 ∈ 目录；home_url 按平台条件必填（有公开主页的平台必填）。"""
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        # platform 必选自目录（§4.3 选择制，零 fake）。
        await CreatorService.validate_platform(db, platform=platform)
        # home_url 条件必填：有公开网页主页的平台（小红书/抖音/B站…）必填，公众号/视频号等豁免（§8）。
        home_url = (fields.get('home_url') or '').strip() or None
        if home_url is None and await CreatorService.platform_requires_home_url(db, platform=platform):
            raise errors.RequestError(msg='该平台有公开主页，请填写主页 URL（用于分身抓取粉丝/作品数据）')
        acc = Account(
            project_id=project_id,
            platform=platform,
            platform_uid=fields.get('platform_uid'),
            nickname=fields.get('nickname'),
            avatar_url=fields.get('avatar_url'),
            home_url=home_url,
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
    async def _load_account(db: AsyncSession, *, account_id: int, user_id: int, scope: CreatorScope | None) -> Account:
        stmt = apply_scope(sa.select(Account).where(Account.id == account_id), Account, user_id=user_id, scope=scope)
        acc = (await db.execute(stmt)).scalars().first()
        if acc is None:
            raise errors.NotFoundError(msg='平台账号不存在或无权访问')
        return acc

    @staticmethod
    async def update_account(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        account_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """更新账号资料（人/分身改昵称/uid/主账号/主页/简介 + 手填指标，§6.3 ①人操作回填）。"""
        acc = await CreatorService._load_account(db, account_id=account_id, user_id=user_id, scope=scope)
        allowed = {
            'nickname',
            'platform_uid',
            'avatar_url',
            'home_url',
            'bio',
            'is_primary',
            'notes',
            # 人手填指标（知道就填；分身抓取走 update_account_metrics）。
            'followers',
            'following',
            'total_likes',
            'total_favorites',
            'total_comments',
            'total_posts',
        }
        metric_keys = {'followers', 'following', 'total_likes', 'total_favorites', 'total_comments', 'total_posts'}
        touched_metric = False
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == 'is_primary':
                setattr(acc, k, bool(v))
            elif k in metric_keys:
                setattr(acc, k, int(v))
                touched_metric = True
            else:
                setattr(acc, k, v)
        # 人手填指标也刷新「更新于 T」，与分身抓取口径一致（数据新鲜度诚实标注）。
        if touched_metric:
            acc.metrics_updated_at = datetime.datetime.now(datetime.UTC)
        await db.flush()
        return _to_dict(acc)

    @staticmethod
    async def update_account_metrics(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        account_id: int,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """回填账号指标（分身抓取后调；§6.3 ②分身派发 web-reach → 解析粉丝/获赞/作品数）。

        已知列（followers/following/total_likes/total_favorites/total_comments/total_posts）直接落列；
        其余平台特有指标并入 `metrics_json`（保留原始口径，避免丢失）。刷新 `metrics_updated_at`。
        """
        acc = await CreatorService._load_account(db, account_id=account_id, user_id=user_id, scope=scope)
        known = {'followers', 'following', 'total_likes', 'total_favorites', 'total_comments', 'total_posts'}
        extra = dict(acc.metrics_json or {})
        for k, v in metrics.items():
            if v is None:
                continue
            if k in known:
                setattr(acc, k, int(v))
            else:
                extra[k] = v
        acc.metrics_json = extra
        acc.metrics_updated_at = datetime.datetime.now(datetime.UTC)
        await db.flush()
        return _to_dict(acc)

    # ============================ work（作品明细）============================

    # 逐条 upsert 允许透传的作品字段（归并键 external_id/url 除外，另行处理）。
    _WORK_FIELDS = ('title', 'cover_uri', 'published_at', 'views', 'likes', 'comments', 'shares', 'favorites')
    _WORK_INT_FIELDS = {'views', 'likes', 'comments', 'shares', 'favorites'}

    @staticmethod
    def _apply_work_fields(work: Work, item: dict[str, Any]) -> None:
        """把一条作品数据落到 Work 行（归并更新与新建共用）。"""
        for key in CreatorService._WORK_FIELDS:
            if key not in item or item[key] is None:
                continue
            value = int(item[key]) if key in CreatorService._WORK_INT_FIELDS else item[key]
            setattr(work, key, value)

    @staticmethod
    async def upsert_works(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        source_type: str,
        owner_ref_id: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """逐条 upsert 作品（§6.3/§6.4 分身抓取回填）。source_type=own→account_id，competitor→competitor_id。

        归并键（§7）：同一 (project_id, source_type, owner_ref_id) 下，按 external_id 优先、否则 url 匹配既有行→更新指标；
        无匹配→插入。归并让「发布记录回填」与「账号抓取」两路指标对得上，避免同一作品两条。
        collected_at 刷新为当前时刻（数据新鲜度）。
        """
        if source_type not in ('own', 'competitor'):
            raise errors.RequestError(msg='source_type 只能是 own 或 competitor')
        # 校验归属主体存在且属本 owner（own→account / competitor→competitor），拿到 project + 平台冗余。
        if source_type == 'own':
            parent = await CreatorService._load_account(db, account_id=owner_ref_id, user_id=user_id, scope=scope)
        else:
            stmt = apply_scope(
                sa.select(Competitor).where(Competitor.id == owner_ref_id), Competitor, user_id=user_id, scope=scope
            )
            parent = (await db.execute(stmt)).scalars().first()
            if parent is None:
                raise errors.NotFoundError(msg='竞品不存在或无权访问')
        project_id = parent.project_id
        platform = parent.platform or ''
        own = {
            'owner_scope': parent.owner_scope,
            'user_id': parent.user_id,
            'enterprise_id': parent.enterprise_id,
            'assignee': parent.assignee,
        }
        # 该主体下现有作品（用于归并匹配）。
        base = sa.select(Work).where(Work.project_id == project_id, Work.source_type == source_type)
        base = (
            base.where(Work.account_id == owner_ref_id)
            if source_type == 'own'
            else base.where(Work.competitor_id == owner_ref_id)
        )
        existing = (await db.execute(base)).scalars().all()
        by_ext = {w.external_id: w for w in existing if w.external_id}
        by_url = {w.url: w for w in existing if w.url}

        now = datetime.datetime.now(datetime.UTC)
        upserted = 0
        for item in items or []:
            ext = (item.get('external_id') or '').strip() or None
            url = (item.get('url') or '').strip() or None
            match = (by_ext.get(ext) if ext else None) or (by_url.get(url) if url else None)
            if match is None:
                match = Work(
                    project_id=project_id,
                    source_type=source_type,
                    account_id=owner_ref_id if source_type == 'own' else None,
                    competitor_id=owner_ref_id if source_type == 'competitor' else None,
                    platform=item.get('platform') or platform,
                    external_id=ext,
                    url=url,
                    **own,
                )
                db.add(match)
                if ext:
                    by_ext[ext] = match
                if url:
                    by_url[url] = match
            else:
                if ext and not match.external_id:
                    match.external_id = ext
                if url and not match.url:
                    match.url = url
            CreatorService._apply_work_fields(match, item)
            match.collected_at = now
            upserted += 1
        # 竞品作品数随抓取结果刷新（§6.4 works_count 由调研回填）。
        if source_type == 'competitor':
            total = (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(Work)
                    .where(Work.source_type == 'competitor', Work.competitor_id == owner_ref_id)
                )
            ).scalar() or 0
            parent.works_count = int(total)
        await db.flush()
        return {'upserted': upserted, 'source_type': source_type, 'owner_ref_id': owner_ref_id}

    @staticmethod
    async def list_works(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        source_type: str,
        owner_ref_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列某账号/竞品的作品（按发布时间倒序；点开账号卡下钻作品用）。"""
        if source_type not in ('own', 'competitor'):
            raise errors.RequestError(msg='source_type 只能是 own 或 competitor')
        # 归属校验（复用父主体加载，确保 owner 隔离）。
        if source_type == 'own':
            await CreatorService._load_account(db, account_id=owner_ref_id, user_id=user_id, scope=scope)
            stmt = sa.select(Work).where(Work.source_type == 'own', Work.account_id == owner_ref_id)
        else:
            comp = apply_scope(
                sa.select(Competitor).where(Competitor.id == owner_ref_id), Competitor, user_id=user_id, scope=scope
            )
            if (await db.execute(comp)).scalars().first() is None:
                raise errors.NotFoundError(msg='竞品不存在或无权访问')
            stmt = sa.select(Work).where(Work.source_type == 'competitor', Work.competitor_id == owner_ref_id)
        stmt = stmt.order_by(Work.published_at.desc().nullslast(), Work.id.desc()).limit(min(limit, 300))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(w) for w in rows]

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
        """记竞品（§6.4 工具层强制录真）：platform+url 必填；researched=true 时 follower_count+works_count 必填。

        兼容「先挂 URL 待分身调研」：researched=false（默认）时只强制 platform+url+name，指标待补；
        researched=true（分身调研完带真数据）时强制 follower_count+works_count（零 fake 的录真）。
        """
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        platform = (fields.get('platform') or '').strip() or None
        url = (fields.get('url') or '').strip() or None
        researched = bool(fields.get('researched', False))
        if not platform:
            raise errors.RequestError(msg='竞品必须选平台（从平台目录中选）')
        await CreatorService.validate_platform(db, platform=platform)
        if not url:
            raise errors.RequestError(msg='竞品必须填主页 URL（先挂 URL 待分身调研也需要）')
        if researched:
            if fields.get('follower_count') is None:
                raise errors.RequestError(msg='已调研的竞品必须填粉丝数（follower_count）')
            if fields.get('works_count') is None:
                raise errors.RequestError(msg='已调研的竞品必须填作品数（works_count）')
        comp = Competitor(
            project_id=project_id,
            name=name,
            platform=platform,
            url=url,
            follower_count=int(fields.get('follower_count') or 0),
            works_count=int(fields.get('works_count') or 0),
            avg_likes=int(fields.get('avg_likes') or 0),
            content_style=fields.get('content_style'),
            strengths=fields.get('strengths') or [],
            notes=fields.get('notes'),
            tags=fields.get('tags') or [],
            last_analyzed=datetime.datetime.now(datetime.UTC) if researched else None,
            **_child_ownership(proj),
        )
        db.add(comp)
        await db.flush()
        return _to_dict(comp)

    @staticmethod
    async def update_competitor(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        competitor_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """回填竞品调研结果（§6.4 分身调研后带完整数据：粉丝/作品数/风格/优势）。"""
        stmt = apply_scope(
            sa.select(Competitor).where(Competitor.id == competitor_id), Competitor, user_id=user_id, scope=scope
        )
        comp = (await db.execute(stmt)).scalars().first()
        if comp is None:
            raise errors.NotFoundError(msg='竞品不存在或无权访问')
        if fields.get('platform') is not None:
            await CreatorService.validate_platform(db, platform=fields.get('platform'))
        int_keys = {'follower_count', 'works_count', 'avg_likes'}
        allowed = {'name', 'platform', 'url', 'content_style', 'strengths', 'notes', 'tags'} | int_keys
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            setattr(comp, k, int(v) if k in int_keys else v)
        # 有调研数据回填即刷新调研时间（§6.4「上次调研 T」）。
        comp.last_analyzed = datetime.datetime.now(datetime.UTC)
        await db.flush()
        return _to_dict(comp)

    @staticmethod
    async def list_competitors(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int
    ) -> list[dict[str, Any]]:
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        rows = (await db.execute(sa.select(Competitor).where(Competitor.project_id == project_id))).scalars().all()
        return [_to_dict(c) for c in rows]

    # ============================ media / draft（素材库 / 草稿箱·S6）============================

    # 素材类型白名单（§6.7；与 Media.type 字典一致）。
    _MEDIA_TYPES = ('image', 'video', 'audio', 'template')

    @staticmethod
    async def _load_media(db: AsyncSession, *, media_id: int, user_id: int, scope: CreatorScope | None) -> Media:
        stmt = apply_scope(sa.select(Media).where(Media.id == media_id), Media, user_id=user_id, scope=scope)
        m = (await db.execute(stmt)).scalars().first()
        if m is None:
            raise errors.NotFoundError(msg='素材不存在或无权访问')
        return m

    @staticmethod
    async def add_media(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        media_type: str,
        asset_uri: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """登记素材（§6.7）：二进制走 `hasn://asset/` 引用（禁 base64，铁律）；type ∈ 白名单。

        分身抓取/生成的配图、封面、reel 成片等落私有桶后，把 `asset_uri` 登记进素材库；
        原始字节由 daemon/工具侧上桶，服务层只存引用。
        """
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        if media_type not in CreatorService._MEDIA_TYPES:
            raise errors.RequestError(msg=f'素材类型只能是 {"/".join(CreatorService._MEDIA_TYPES)}')
        uri = (asset_uri or '').strip()
        if not uri.startswith('hasn://asset/'):
            # 铁律：入参禁 base64/字节块，二进制必须是私有桶引用。
            raise errors.RequestError(msg='asset_uri 必须是 hasn://asset/ 引用（禁 base64 字节块）')
        media = Media(
            project_id=project_id,
            type=media_type,
            asset_uri=uri,
            filename=fields.get('filename'),
            file_size=fields.get('file_size'),
            width=fields.get('width'),
            height=fields.get('height'),
            duration=fields.get('duration'),
            thumbnail_uri=fields.get('thumbnail_uri'),
            tags=fields.get('tags') or {},
            description=fields.get('description'),
            **_child_ownership(proj),
        )
        db.add(media)
        await db.flush()
        return _to_dict(media)

    @staticmethod
    async def list_media(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        media_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列项目素材（§6.7；可按 type 过滤，按创建时间倒序）。"""
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        stmt = sa.select(Media).where(Media.project_id == project_id)
        if media_type:
            if media_type not in CreatorService._MEDIA_TYPES:
                raise errors.RequestError(msg=f'素材类型只能是 {"/".join(CreatorService._MEDIA_TYPES)}')
            stmt = stmt.where(Media.type == media_type)
        stmt = stmt.order_by(Media.created_time.desc(), Media.id.desc()).limit(min(limit, 300))
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(m) for m in rows]

    @staticmethod
    async def update_media(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        media_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """改素材元信息（§6.7；标签/描述/文件名，人手动整理素材库用）。"""
        media = await CreatorService._load_media(db, media_id=media_id, user_id=user_id, scope=scope)
        allowed = {'filename', 'tags', 'description', 'thumbnail_uri'}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            setattr(media, k, v)
        await db.flush()
        return _to_dict(media)

    @staticmethod
    async def delete_media(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, media_id: int
    ) -> dict[str, Any]:
        """删素材（§6.7；仅删库内引用行，私有桶资产另行回收）。"""
        media = await CreatorService._load_media(db, media_id=media_id, user_id=user_id, scope=scope)
        await db.delete(media)
        await db.flush()
        return {'deleted': True, 'id': media_id}

    @staticmethod
    async def _load_draft(db: AsyncSession, *, draft_id: int, user_id: int, scope: CreatorScope | None) -> Draft:
        stmt = apply_scope(sa.select(Draft).where(Draft.id == draft_id), Draft, user_id=user_id, scope=scope)
        d = (await db.execute(stmt)).scalars().first()
        if d is None:
            raise errors.NotFoundError(msg='草稿不存在或无权访问')
        return d

    @staticmethod
    async def create_draft(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        title: str,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """建草稿（§6.8）：快速记灵感/半成品，不进正式内容流水线；title 必填。

        content=正文；media=引用素材 asset 列表（`hasn://asset/`）；target_platforms=目标平台 key 列表。
        草稿养熟后经 `promote_draft` 转正为正式 Content。
        """
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        fields = fields or {}
        title = (title or '').strip()
        if not title:
            raise errors.RequestError(msg='草稿标题必填')
        draft = Draft(
            project_id=project_id,
            title=title,
            content=fields.get('content'),
            media=fields.get('media') or [],
            tags=fields.get('tags') or [],
            target_platforms=fields.get('target_platforms') or [],
            **_child_ownership(proj),
        )
        db.add(draft)
        await db.flush()
        return _to_dict(draft)

    @staticmethod
    async def update_draft(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        draft_id: int,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """改草稿（§6.8；标题/正文/素材/标签/目标平台）。"""
        draft = await CreatorService._load_draft(db, draft_id=draft_id, user_id=user_id, scope=scope)
        allowed = {'title', 'content', 'media', 'tags', 'target_platforms'}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            setattr(draft, k, v)
        await db.flush()
        return _to_dict(draft)

    @staticmethod
    async def list_drafts(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, project_id: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        """列项目草稿（§6.8；按创建时间倒序）。"""
        await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        stmt = (
            sa
            .select(Draft)
            .where(Draft.project_id == project_id)
            .order_by(Draft.created_time.desc(), Draft.id.desc())
            .limit(min(limit, 300))
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [_to_dict(d) for d in rows]

    @staticmethod
    async def delete_draft(
        db: AsyncSession, *, user_id: int, scope: CreatorScope | None, draft_id: int
    ) -> dict[str, Any]:
        """删草稿（§6.8）。"""
        draft = await CreatorService._load_draft(db, draft_id=draft_id, user_id=user_id, scope=scope)
        await db.delete(draft)
        await db.flush()
        return {'deleted': True, 'id': draft_id}

    @staticmethod
    async def promote_draft(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        draft_id: int,
        created_by_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """草稿转正为正式内容（§6.8）：draft → Content（进创作流水线），并删掉原草稿行。

        沿用草稿的 title/target_platforms 建 Content（图文轨默认 article）；转正后返回新内容，
        草稿使命完成即删除，避免草稿箱与内容流水线两处并存同一条。
        """
        draft = await CreatorService._load_draft(db, draft_id=draft_id, user_id=user_id, scope=scope)
        content = await CreatorService.create_content(
            db,
            user_id=user_id,
            scope=scope,
            project_id=draft.project_id,
            title=draft.title,
            content_tracks='article',
            target_platforms=list(draft.target_platforms or []),
            created_by_agent_id=created_by_agent_id,
        )
        # 草稿转正后使命完成，删原草稿行（避免草稿箱与内容流水线两处并存同一条）。
        await db.delete(draft)
        await db.flush()
        return content

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
    async def add_topic(
        db: AsyncSession,
        *,
        user_id: int,
        scope: CreatorScope | None,
        project_id: int,
        title: str,
        reason: str | None = None,
        angle: str | None = None,
    ) -> dict[str, Any]:
        """人或分身单条加选题（§6.6「手动加选题」）——① owner 通道与 ② 分身通道共用此方法。

        与 suggest_topics（批量、分身生成）区别：单条、可来自主人手动录入；angle 作为一条创意角度
        存入 creative_angles。零 fake：分数缺省 0，不编造热度/潜力（Topic 表无创作者审计列，与 suggest_topics 一致）。
        """
        proj = await CreatorService._load_project(db, project_id=project_id, user_id=user_id, scope=scope)
        row = Topic(
            project_id=project_id,
            title=str(title or '').strip()[:200] or '未命名选题',
            reason=reason,
            creative_angles=[angle] if angle else [],
            potential_score=0,
            heat_index=0,
            status=0,
            **_child_ownership(proj),
        )
        db.add(row)
        await db.flush()
        return _to_dict(row)

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
                else Decimal(0),
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
            confidence=Decimal(str(confidence)) if confidence is not None else Decimal(0),
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
