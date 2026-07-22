"""技能市场同步 API

检查已安装的技能/模板是否有新版本
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


class InstalledItem(BaseModel):
    """已安装的项"""

    id: str = Field(description='技能或模板ID')
    version: str = Field(description='当前版本号')
    type: str = Field(description='类型: skill 或 template')


class SyncRequest(BaseModel):
    """同步请求"""

    installed: list[InstalledItem] = Field(description='已安装的项列表')


class UpdateItem(BaseModel):
    """有更新的项"""

    id: str
    type: str
    current_version: str
    latest_version: str
    changelog: str | None
    download_url: str | None
    file_hash: str | None


class SyncResponse(BaseModel):
    """同步响应"""

    updates: list[UpdateItem]


@router.post(
    '',
    summary='同步检查更新',
    description='检查已安装的技能和模板是否有新版本',
)
async def sync_installed(
    db: CurrentSession,
    request: SyncRequest,
) -> ResponseSchemaModel[SyncResponse]:
    updates = []

    for item in request.installed:
        if item.type == 'skill':
            # 获取技能最新版本
            skill_version = await marketplace_skill_version_dao.get_latest_by_skill(db, item.id)
            # 简单版本比较（可以改用 semver 库）
            if (
                skill_version
                and skill_version.version != item.version
                and _is_newer_version(skill_version.version, item.version)
            ):
                updates.append(
                    UpdateItem(
                        id=item.id,
                        type='skill',
                        current_version=item.version,
                        latest_version=skill_version.version,
                        changelog=skill_version.changelog,
                        download_url=skill_version.package_url,
                        file_hash=skill_version.file_hash,
                    )
                )
        elif item.type == 'template':
            # 获取模板最新版本
            template_version = await marketplace_template_version_dao.get_latest_by_template(db, item.id)
            if (
                template_version
                and template_version.version != item.version
                and _is_newer_version(template_version.version, item.version)
            ):
                updates.append(
                    UpdateItem(
                        id=item.id,
                        type='template',
                        current_version=item.version,
                        latest_version=template_version.version,
                        changelog=template_version.changelog,
                        download_url=template_version.package_url,
                        file_hash=template_version.file_hash,
                    )
                )

    return response_base.success(data=SyncResponse(updates=updates))


def _is_newer_version(latest: str, current: str) -> bool:
    """
    简单的语义化版本比较

    :param latest: 最新版本
    :param current: 当前版本
    :return: 如果 latest > current 返回 True
    """
    try:

        def parse_version(v: str) -> tuple[int, ...]:
            # 移除可能的前缀 v
            v = v.lstrip('v')
            # 处理 -local.x 之类的后缀
            v = v.split('-')[0]
            return tuple(int(x) for x in v.split('.'))

        latest_parts = parse_version(latest)
        current_parts = parse_version(current)

    except (ValueError, AttributeError):
        # 如果解析失败，认为不是更新
        return False
    else:
        return latest_parts > current_parts
