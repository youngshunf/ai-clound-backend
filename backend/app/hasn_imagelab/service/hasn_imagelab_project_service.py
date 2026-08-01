from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_imagelab.crud.crud_hasn_imagelab_project import hasn_imagelab_project_dao


class HasnImagelabProjectService:
    """历史图坊本地引用兼容登记服务。

    当前流程直接使用平台项目 UUID；本服务只为旧客户端把历史 daemon 本地引用
    （owner_id + local_ref）幂等映射到稳定兼容 ID（server_id）。
    """

    @staticmethod
    async def register_project(*, db: AsyncSession, owner_id: str, local_ref: str, name: str) -> str:
        """按 (owner_id, local_ref) 幂等登记，返回历史兼容 server_id。

        - 已登记（同 owner 同 local_ref）→ 复用既有 id（幂等）；名字有更新则同步刷新。
        - 未登记 → 新建一行，DB 默认 gen_random_uuid() 生成兼容 id。
        owner 行级隔离：查询恒带 owner_id，绝不跨 owner 命中他人登记。
        """
        existing = await hasn_imagelab_project_dao.get_by_owner_and_local_ref(
            db, owner_id=owner_id, local_ref=local_ref
        )
        if existing is not None:
            # 幂等复用；名字变化则顺手更新（不影响权威 id）。
            if name and existing.name != name:
                existing.name = name
                await db.flush()
            return str(existing.id)

        created = await hasn_imagelab_project_dao.create_registration(
            db, owner_id=owner_id, local_ref=local_ref, name=name
        )
        return str(created.id)


hasn_imagelab_project_service: HasnImagelabProjectService = HasnImagelabProjectService()
