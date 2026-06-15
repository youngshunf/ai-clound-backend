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

from backend.app.hasn_creator.model.account import Account
from backend.app.hasn_creator.model.competitor import Competitor
from backend.app.hasn_creator.model.content import Content
from backend.app.hasn_creator.model.content_stage import ContentStage
from backend.app.hasn_creator.model.profile import Profile
from backend.app.hasn_creator.model.project import Project
from backend.app.hasn_creator.model.publish import Publish
from backend.app.hasn_creator.model.topic import Topic
from backend.app.hasn_creator.model.viral_pattern import ViralPattern
from backend.app.hasn_creator.service.scope_context import (
    CreatorScope,
    apply_scope,
    ownership_fields,
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
        """建项目（运营单元根）+ 1:1 空画像。落双模归属。"""
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
        """保存阶段产出（同 content+stage 已存在则 bump version 新建一版，留迭代痕迹）。"""
        c = await CreatorService._load_content(db, content_id=content_id, user_id=user_id, scope=scope)
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
            asset_refs=asset_refs or [],
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
