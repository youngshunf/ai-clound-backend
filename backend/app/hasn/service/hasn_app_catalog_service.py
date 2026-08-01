import copy

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
from backend.app.hasn.model import HasnAppCatalog
from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    UpdateHasnAppCatalogParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data

# 由专用签名发布端点独占写入的配置子树，路径以 (顶层键, 子键...) 表示。
# 通用「编辑配置」面是整块覆盖式写 config_json，与发布端点共用同一 `hasn:app:catalog:edit`
# 权限且互不感知：运营改一个无关字段保存，就会静默删掉全网在用的签名目录/引擎清单，
# 接口仍返回 200，daemon 重拉后模型集体失效且页面上没有任何报错指向那次保存。
_PROTECTED_CONFIG_SUBTREES: tuple[tuple[str, ...], ...] = (
    ('models', 'signed_catalog'),
    ('engine',),
)


def _read_subtree(config_json: dict | None, path: tuple[str, ...]) -> Any | None:
    node: Any = config_json
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _write_subtree(config_json: dict, path: tuple[str, ...], value: Any) -> None:
    node = config_json
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def _preserve_protected_config_subtrees(current: dict | None, incoming: dict) -> dict:
    """剥离入参对受保护子树的改动，并从库中现值回填。

    改动即 400（防误改），缺失即回填（防误删）——两者都要：整块覆盖式的保存请求
    通常根本不带这些键，只做「改动即拒」拦不住删除。
    """
    merged = copy.deepcopy(incoming)
    for path in _PROTECTED_CONFIG_SUBTREES:
        existing_value = _read_subtree(current, path)
        incoming_value = _read_subtree(merged, path)
        if existing_value is None:
            continue
        if incoming_value is not None and incoming_value != existing_value:
            readable = '.'.join(path)
            raise errors.RequestError(
                msg=f'配置项 {readable} 由签名发布端点独占写入，请改用对应的发布接口',
            )
        _write_subtree(merged, path, copy.deepcopy(existing_value))
    return merged


class HasnAppCatalogService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAppCatalog:
        """
        获取AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :return:
        """
        hasn_app_catalog = await hasn_app_catalog_dao.get(db, pk)
        if not hasn_app_catalog:
            raise errors.NotFoundError(msg='AI-Native 应用目录（云端权威）不存在')
        return hasn_app_catalog

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取AI-Native 应用目录（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        hasn_app_catalog_select = await hasn_app_catalog_dao.get_select()
        return await paging_data(db, hasn_app_catalog_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAppCatalog]:
        """
        获取所有AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :return:
        """
        hasn_app_catalog_list = await hasn_app_catalog_dao.get_all(db)
        return hasn_app_catalog_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAppCatalogParam) -> None:
        """
        创建AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param obj: 创建AI-Native 应用目录（云端权威）参数
        :return:
        """
        await hasn_app_catalog_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAppCatalogParam) -> int:
        """
        更新AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :param obj: 更新AI-Native 应用目录（云端权威）参数
        :return:
        """
        count = await hasn_app_catalog_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def update_config(*, db: AsyncSession, pk: int, config_json: dict) -> int:
        """
        仅更新应用专属平台级配置 JSON（管理端「编辑配置」），不回填整行

        签名子树（``models.signed_catalog``、``engine``）由专用发布端点独占写入，
        本通用配置面只做剥离与回填，禁止改写。

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :param config_json: 应用专属平台级配置 JSON
        :return:
        """
        # 存在性校验：不存在直接 404，避免静默 0 影响（与全字段 update 行为对齐）
        catalog = await HasnAppCatalogService.get(db=db, pk=pk)
        merged = _preserve_protected_config_subtrees(catalog.config_json, config_json)
        count = await hasn_app_catalog_dao.update_config(db, pk, merged)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAppCatalogParam) -> int:
        """
        删除AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param obj: AI-Native 应用目录（云端权威） ID 列表
        :return:
        """
        count = await hasn_app_catalog_dao.delete(db, obj.pks)
        return count


hasn_app_catalog_service: HasnAppCatalogService = HasnAppCatalogService()
