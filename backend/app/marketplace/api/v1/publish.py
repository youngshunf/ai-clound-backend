"""技能市场发布 API。

用户发布沿用私有草稿与审核状态机；官方/GitHub 来源发布仅允许管理员 API Key，
接收本地 Hub 的确定性 ZIP 并直接形成公开、内容寻址的 CDN 制品。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select

from backend.app.admin.model.user import User
from backend.app.hasn_core import hasn_humans_dao
from backend.app.marketplace.model import MarketplaceSkillVersion, MarketplaceTemplateVersion
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.app.marketplace.service.source_release_service import source_release_service
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.app.newapi.apikey.service import api_key_service
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@dataclass
class PublishUser:
    """已认证发布者。"""

    user_id: int
    hasn_id: str
    username: str
    nickname: str
    is_admin: bool


@dataclass
class SourcePublishUser:
    """已认证的官方来源制品发布者。"""

    user_id: int
    username: str
    nickname: str
    is_admin: bool
    auth_type: Literal['bearer', 'api_key'] = 'api_key'


class PublishResult(BaseModel):
    """返回给 CLI 或 Agent 的用户发布结果。"""

    id: str
    namespace: str
    slug: str
    status: str
    visibility: str
    user_id: int
    hasn_id: str
    version: str
    package_url: str
    file_hash: str
    file_size: int


class SourcePublishResult(BaseModel):
    """来源技能制品发布结果。"""

    skill_id: str
    namespace: str
    slug: str
    source_type: str
    version: str
    package_url: str
    file_hash: str
    content_hash: str
    file_size: int
    uploaded: bool


class SourceHubPublishResult(BaseModel):
    """官方 Hub 非技能资源制品发布结果。"""

    resource_id: str
    resource_type: Literal['skill_pack', 'agent_template', 'workflow']
    slug: str
    source_type: str
    version: str
    package_url: str
    file_hash: str
    content_hash: str
    file_size: int
    uploaded: bool


class SourceReconcileRequest(BaseModel):
    """完整来源发布后的下架对账请求。"""

    resource_type: Literal['skill', 'skill_pack', 'agent_template', 'workflow'] = 'skill'
    source_type: Literal['huanxing', 'github'] = 'huanxing'
    active_resource_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices('active_resource_ids', 'active_skill_ids'),
    )


class SourceReconcileResult(BaseModel):
    """来源下架对账结果。"""

    resource_type: str
    source_type: str
    active_count: int
    unpublished_resource_ids: list[str]
    unpublished_skill_ids: list[str]


async def _verify_api_key_user(
    db: CurrentSession,
    x_api_key: str | None,
) -> User:
    """校验发布 API Key 并返回对应用户。"""
    if not x_api_key:
        raise errors.AuthorizationError(msg='缺少 API Key')

    api_key_record = await api_key_service.verify_api_key(db, x_api_key)
    result = await db.execute(select(User).where(User.id == api_key_record.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise errors.AuthorizationError(msg='用户不存在')
    return user


async def verify_publish_api_key(
    db: CurrentSession,
    x_api_key: Annotated[str | None, Header(alias='X-API-Key')] = None,
) -> PublishUser:
    """校验普通发布 API Key 并解析 HASN 身份。"""
    user = await _verify_api_key_user(db, x_api_key)

    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=user.id)
    if not hasn_human:
        raise errors.AuthorizationError(msg='用户未注册 HASN 身份')

    return PublishUser(
        user_id=user.id,
        hasn_id=hasn_human.hasn_id,
        username=user.username,
        nickname=user.nickname,
        is_admin=bool(user.is_superuser or user.is_staff),
    )


def require_source_publish_admin(
    publish_user: SourcePublishUser,
) -> SourcePublishUser:
    """拒绝非管理员的来源制品发布。"""
    if not publish_user.is_admin:
        raise errors.AuthorizationError(msg='官方来源制品发布仅允许管理员')
    return publish_user


def source_publish_user_from_authenticated_user(user: object) -> SourcePublishUser:
    """把 JWT 中间件解析出的后台用户映射为来源发布身份。"""
    user_id = getattr(user, 'id', None)
    if not isinstance(user_id, int):
        raise errors.AuthorizationError(msg='登录身份无效')
    return require_source_publish_admin(
        SourcePublishUser(
            user_id=user_id,
            username=str(getattr(user, 'username', '') or ''),
            nickname=str(getattr(user, 'nickname', '') or ''),
            is_admin=bool(
                getattr(user, 'is_superuser', False)
                or getattr(user, 'is_staff', False)
            ),
            auth_type='bearer',
        )
    )


async def verify_source_publish_api_key(
    db: CurrentSession,
    x_api_key: Annotated[str | None, Header(alias='X-API-Key')] = None,
) -> SourcePublishUser:
    """来源发布只依赖管理员 API Key，不要求后台账号注册 HASN 身份。"""
    user = await _verify_api_key_user(db, x_api_key)
    return require_source_publish_admin(
        SourcePublishUser(
            user_id=user.id,
            username=user.username,
            nickname=user.nickname,
            is_admin=bool(user.is_superuser or user.is_staff),
        )
    )


async def verify_source_publish_admin(
    request: Request,
    db: CurrentSession,
    x_api_key: Annotated[str | None, Header(alias='X-API-Key')] = None,
) -> SourcePublishUser:
    """优先接受管理员登录 JWT，并保留 API Key 供 CI 无人值守发布。"""
    authorization = request.headers.get('Authorization', '')
    if authorization.lower().startswith('bearer '):
        return source_publish_user_from_authenticated_user(request.user)
    return await verify_source_publish_api_key(db, x_api_key)


async def _bump_common_skills(db: CurrentSession) -> None:
    """发布后刷新公共技能修订；推送失败不回滚权威发布事务。"""
    try:
        from backend.app.hasn.service.sync_invalidate_service import bump

        await bump('common_skills', db)
    except Exception as exc:
        log.warning(f'来源技能发布后的公共技能失效通知失败，将由周期对账追平: {exc}')


async def _latest_skill_version(db: CurrentSession, skill_id: str) -> MarketplaceSkillVersion:
    result = await db.execute(
        select(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill_id,
            MarketplaceSkillVersion.is_latest,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise errors.NotFoundError(msg='技能版本不存在')
    return version


async def _latest_template_version(db: CurrentSession, template_id: str) -> MarketplaceTemplateVersion:
    result = await db.execute(
        select(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.template_id == template_id,
            MarketplaceTemplateVersion.is_latest,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise errors.NotFoundError(msg='模板版本不存在')
    return version


@router.get('/source/session', summary='检查 AstraHub 当前管理员发布身份')
async def get_source_publish_session(
    publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
) -> ResponseSchemaModel:
    return response_base.success(
        data={
            'user_id': publish_user.user_id,
            'username': publish_user.username,
            'nickname': publish_user.nickname,
            'is_admin': publish_user.is_admin,
            'auth_type': publish_user.auth_type,
        }
    )


@router.post('/upload-icon', summary='预上传图标')
async def upload_icon_only(
    db: CurrentSession,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='图标文件')],
    item_type: Annotated[str, Form(description='skill/template')] = 'skill',
    item_id: Annotated[str, Form(description='完整资源 ID，例如 user/{hasn_id}/{slug}')] = '',
) -> ResponseSchemaModel:
    if item_type not in {'skill', 'template'}:
        raise errors.RequestError(msg='图标类型仅支持 skill/template')
    if not item_id.startswith(f'user/{publish_user.hasn_id}/'):
        raise errors.AuthorizationError(msg='只能上传自己命名空间下的图标')

    content = await file.read()
    icon_url = await marketplace_storage_service.upload_icon_dedup(
        db=db,
        item_type=item_type,
        item_id=item_id,
        content=content,
        filename=file.filename or 'icon.svg',
    )
    return response_base.success(data={'icon_url': icon_url})


@router.post('/skill', summary='发布技能包')
async def publish_skill(
    db: CurrentSessionTransaction,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='技能包 ZIP 文件')],
    slug: Annotated[str | None, Form(description='公开 slug')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    content = await file.read()
    skill = await marketplace_skill_service.upload_user_skill(
        db=db,
        user_id=publish_user.user_id,
        hasn_id=publish_user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    version = await _latest_skill_version(db, skill.skill_id)
    return response_base.success(
        data=PublishResult(
            id=skill.skill_id,
            namespace=skill.namespace or '',
            slug=skill.slug or '',
            status=skill.status,
            visibility=skill.visibility,
            user_id=skill.user_id or publish_user.user_id,
            hasn_id=skill.hasn_id or publish_user.hasn_id,
            version=version.version,
            package_url=version.package_url or '',
            file_hash=version.file_hash or '',
            file_size=version.file_size or 0,
        ),
    )


@router.post('/source/skill', summary='发布官方或 GitHub 来源技能制品')
async def publish_source_skill(
    db: CurrentSessionTransaction,
    _publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
    file: Annotated[UploadFile, File(description='技能包 ZIP 文件')],
    source_type: Annotated[Literal['huanxing', 'github'], Form(description='来源类型')],
    namespace: Annotated[str, Form(description='huanxing/<category> 或 github/<owner>')],
    slug: Annotated[str, Form(description='目录 slug')],
    source_repo_path: Annotated[str, Form(description='源仓库内相对路径')],
    source_repo_url: Annotated[str | None, Form(description='源仓库 URL')] = None,
    git_commit_hash: Annotated[str | None, Form(description='源仓库 commit')] = None,
    is_common: Annotated[bool, Form(description='是否为默认公共技能')] = False,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
    content_hash: Annotated[str | None, Form(description='本地源内容指纹')] = None,
    file_hash: Annotated[str | None, Form(description='本地 ZIP SHA256')] = None,
) -> ResponseSchemaModel[SourcePublishResult]:
    content = await file.read()
    result = await source_release_service.publish_skill(
        db=db,
        source_type=source_type,
        namespace=namespace,
        slug=slug,
        content=content,
        source_repo_url=source_repo_url,
        source_repo_path=source_repo_path,
        git_commit_hash=git_commit_hash,
        is_common=is_common,
        changelog=changelog,
        expected_content_hash=content_hash,
        expected_file_hash=file_hash,
    )
    await _bump_common_skills(db)
    return response_base.success(data=SourcePublishResult(**result.__dict__))


@router.post('/source/skill-pack', summary='发布官方 Hub 技能包制品')
async def publish_source_skill_pack(
    db: CurrentSessionTransaction,
    _publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
    file: Annotated[UploadFile, File(description='技能包 ZIP 文件')],
    slug: Annotated[str, Form(description='目录 slug')],
    source_repo_path: Annotated[str, Form(description='Hub 仓库内相对路径')],
    git_commit_hash: Annotated[str | None, Form(description='Hub 仓库 commit')] = None,
    is_common: Annotated[bool, Form(description='是否为默认公共技能包')] = False,
    content_hash: Annotated[str | None, Form(description='本地源内容指纹')] = None,
    file_hash: Annotated[str | None, Form(description='本地 ZIP SHA256')] = None,
) -> ResponseSchemaModel[SourceHubPublishResult]:
    result = await source_release_service.publish_skill_pack(
        db=db,
        slug=slug,
        content=await file.read(),
        source_repo_path=source_repo_path,
        git_commit_hash=git_commit_hash,
        is_common=is_common,
        expected_content_hash=content_hash,
        expected_file_hash=file_hash,
    )
    await _bump_common_skills(db)
    return response_base.success(data=SourceHubPublishResult(**result.__dict__))


@router.post('/source/agent-template', summary='发布官方 Hub 分身模板制品')
async def publish_source_agent_template(
    db: CurrentSessionTransaction,
    _publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
    file: Annotated[UploadFile, File(description='分身模板 ZIP 文件')],
    slug: Annotated[str, Form(description='目录 slug')],
    source_repo_path: Annotated[str, Form(description='Hub 仓库内相对路径')],
    git_commit_hash: Annotated[str | None, Form(description='Hub 仓库 commit')] = None,
    content_hash: Annotated[str | None, Form(description='本地源内容指纹')] = None,
    file_hash: Annotated[str | None, Form(description='本地 ZIP SHA256')] = None,
) -> ResponseSchemaModel[SourceHubPublishResult]:
    result = await source_release_service.publish_agent_template(
        db=db,
        slug=slug,
        content=await file.read(),
        source_repo_path=source_repo_path,
        git_commit_hash=git_commit_hash,
        expected_content_hash=content_hash,
        expected_file_hash=file_hash,
    )
    return response_base.success(data=SourceHubPublishResult(**result.__dict__))


@router.post('/source/workflow', summary='发布官方 Hub 场景工作流制品')
async def publish_source_workflow(
    db: CurrentSessionTransaction,
    _publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
    file: Annotated[UploadFile, File(description='场景工作流 ZIP 文件')],
    slug: Annotated[str, Form(description='目录 slug')],
    source_repo_path: Annotated[str, Form(description='Hub 仓库内相对路径')],
    git_commit_hash: Annotated[str | None, Form(description='Hub 仓库 commit')] = None,
    content_hash: Annotated[str | None, Form(description='本地源内容指纹')] = None,
    file_hash: Annotated[str | None, Form(description='本地 ZIP SHA256')] = None,
) -> ResponseSchemaModel[SourceHubPublishResult]:
    result = await source_release_service.publish_workflow(
        db=db,
        slug=slug,
        content=await file.read(),
        source_repo_path=source_repo_path,
        git_commit_hash=git_commit_hash,
        expected_content_hash=content_hash,
        expected_file_hash=file_hash,
    )
    return response_base.success(data=SourceHubPublishResult(**result.__dict__))


@router.post('/source/reconcile', summary='对账官方或 GitHub 来源完整发布清单')
async def reconcile_source_skills(
    db: CurrentSessionTransaction,
    _publish_user: Annotated[SourcePublishUser, Depends(verify_source_publish_admin)],
    request: SourceReconcileRequest,
) -> ResponseSchemaModel[SourceReconcileResult]:
    unpublished = await source_release_service.reconcile_resource(
        db=db,
        resource_type=request.resource_type,
        source_type=request.source_type,
        active_resource_ids=request.active_resource_ids,
    )
    if request.resource_type in {'skill', 'skill_pack'}:
        await _bump_common_skills(db)
    return response_base.success(
        data=SourceReconcileResult(
            resource_type=request.resource_type,
            source_type=request.source_type,
            active_count=len(set(request.active_resource_ids)),
            unpublished_resource_ids=unpublished,
            unpublished_skill_ids=(
                unpublished if request.resource_type == 'skill' else []
            ),
        )
    )


@router.post('/template', summary='发布模板包')
async def publish_template(
    db: CurrentSessionTransaction,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='模板包 ZIP 文件')],
    slug: Annotated[str | None, Form(description='公开 slug')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    content = await file.read()
    template = await marketplace_template_service.upload_user_template(
        db=db,
        user_id=publish_user.user_id,
        hasn_id=publish_user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    version = await _latest_template_version(db, template.template_id)
    return response_base.success(
        data=PublishResult(
            id=template.template_id,
            namespace=template.namespace or '',
            slug=template.slug or '',
            status=template.status,
            visibility=template.visibility,
            user_id=template.user_id or publish_user.user_id,
            hasn_id=template.hasn_id or publish_user.hasn_id,
            version=version.version,
            package_url=version.package_url or '',
            file_hash=version.file_hash or '',
            file_size=version.file_size or 0,
        ),
    )
