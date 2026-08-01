"""事实删除凭证服务（doc19 §4.5 purge 级联 + 墓碑防复活）。

S3 只落存储与判据；purge 的级联删除（递归清 `merged_from` 含被删 fact 的派生事实、登记画像
重算待办）与 webui 发起路径属 S7，本服务先提供两个不可绕开的原语：

- `record_purge`：登记删除凭证（幂等），**不接受任何被删事实的内容字段**；
- `is_purged` / `filter_purged`：同步 apply 侧的永久拒绝判据——离线节点重新上线推来的晚到
  事件按凭证拒绝（一次性 warn），不让已删内容复活。
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_memory.crud.crud_fact_tombstone import fact_tombstone_dao
from backend.app.hasn_memory.model import FactTombstone
from backend.app.hasn_memory.schema.fact_tombstone import CreateFactTombstoneParam


class FactTombstoneService:
    """事实删除凭证（墓碑）云端权威读写。"""

    @staticmethod
    async def record_purge(
        db: AsyncSession,
        *,
        fact_id: str,
        owner_id: str,
        purged_by: str,
        cascade_from: str | None = None,
        reason: str | None = None,
    ) -> FactTombstone:
        """登记一条删除凭证（幂等：同 fact_id 重复登记返回首条，不改写首次删除事实）。"""
        if not fact_id or not owner_id or not purged_by:
            raise ValueError('fact_id / owner_id / purged_by 均必填（purge 必须可追责到发起人）')
        return await fact_tombstone_dao.record(
            db,
            CreateFactTombstoneParam(
                fact_id=fact_id,
                owner_id=owner_id,
                purged_by=purged_by,
                cascade_from=cascade_from,
                reason=reason,
            ),
        )

    @staticmethod
    async def is_purged(db: AsyncSession, fact_id: str) -> bool:
        """该事实是否已被永久删除（晚到上行事件的拒绝判据）。"""
        return await fact_tombstone_dao.is_purged(db, fact_id)

    @staticmethod
    async def filter_purged(db: AsyncSession, fact_ids: Sequence[str]) -> set[str]:
        """批量筛出已被永久删除的 fact_id（同步 apply 一次过滤整批事件）。"""
        return await fact_tombstone_dao.filter_purged(db, fact_ids)

    @staticmethod
    async def list_by_owner(db: AsyncSession, owner_id: str, *, limit: int = 100) -> Sequence[FactTombstone]:
        """列某主人的删除凭证（最近在前）。"""
        return await fact_tombstone_dao.list_by_owner(db, owner_id, limit=limit)


fact_tombstone_service: FactTombstoneService = FactTombstoneService()
