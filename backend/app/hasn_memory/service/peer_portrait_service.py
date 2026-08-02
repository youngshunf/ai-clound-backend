"""Peer 画像云端存储服务（doc19 §5.3 / §8.5）。

`hasn_memory.peer_portrait` 按 (owner_id, peer_hasn_id) 唯一——owner 视角唯一，跨该 owner 名下
**全部分身**对同一对方的观察合并成一份画像。

**doc19 §10 退役（2026-07-31）**：云端 LLM 合成整条下线——`synthesize_peer_portrait`、
`sweep_peer_portraits`、方案B 脏判定与 `peer_portrait_sweep` celery beat 全部删除。画像现在由
**主脑分身在它自己的设备上**从合并后的事实重算（§5.3 第三段），整轮结果经云端合并闸
（`merge_gate_service`）提交；本表退为「合并态存储 + MEMPUSH 下发源」，**写者换人，表不删**。

云端在本路径上不做任何语义处理（§8.5：不合并、不检索、不提取、不合成画像、不算 embedding）——
`upsert_merged_portrait` 只落库 + 发下行，正文一个字都不改。

下行：`memory.peer_portrait.upserted`（namespace='portraits'）→ daemon
`MemorySyncPullApplier::apply_peer_portrait` 落本地镜像 → `PromptData::load` 注入 runtime。
"""

from __future__ import annotations

from time import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_memory.model.peer_portrait import PeerPortrait
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_MAX_PORTRAIT_CHARS = 900  # 画像正文软上限（注入 identity_peer 用，控注入预算）


def _now_ms() -> int:
    return int(time() * 1000)


def _peer_kind(peer_hasn_id: str) -> str:
    """按 HASN ID 前缀判定对方类型：a_ 为分身，其余（h_）为人类（doc17 §5.6）。"""
    return 'agent' if (peer_hasn_id or '').startswith('a_') else 'human'


def _estimate_tokens(text: str) -> int:
    # 粗略估算：中文按字符、英文按 ~4 字符/token，取保守上界（同 owner_memory）。
    return max(1, len(text) // 3)


class PeerPortraitService:
    """Peer 画像云端存储（owner 隔离强制）；写入口只有合并闸。"""

    async def _existing_portrait(self, db: AsyncSession, *, owner_id: str, peer_hasn_id: str) -> PeerPortrait | None:
        return (
            await db.execute(
                sa.select(PeerPortrait).where(
                    PeerPortrait.owner_id == owner_id,
                    PeerPortrait.peer_hasn_id == peer_hasn_id,
                )
            )
        ).scalar_one_or_none()

    async def get_portrait(self, db: AsyncSession, *, owner_id: str, peer_hasn_id: str) -> dict[str, Any] | None:
        """读取一份画像（owner 隔离）。无则 None。"""
        row = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)
        return _serialize(row) if row is not None else None

    async def upsert_merged_portrait(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        peer_hasn_id: str,
        portrait_text: str,
        peer_kind: str | None = None,
        revised_by: str,
    ) -> dict[str, Any]:
        """落一份**主脑已重算好**的画像（version++）并发下行（doc19 §5.3）。

        云端一个字都不改正文，只做：超长截断（注入预算硬约束，本就是存储层规格）、
        `peer_kind` 缺省按 hasn_id 前缀补齐、version 单调递增、时间戳与 token 估算。

        `source_fact_count` 在合并态下由主脑掌握而云端拿不到——**保留旧值、绝不瞎填**：
        新建行留 0（如实表示「未知」），更新行不动。零 fake 同款要求。
        """
        owner_id = (owner_id or '').strip()
        peer_hasn_id = (peer_hasn_id or '').strip()
        text = (portrait_text or '').strip()
        if not owner_id or not peer_hasn_id:
            raise ValueError('owner_id 与 peer_hasn_id 必填')
        if not text:
            raise ValueError('portrait_text 不能为空（空画像不写库，零 fake）')
        if len(text) > _MAX_PORTRAIT_CHARS:
            text = text[:_MAX_PORTRAIT_CHARS].rstrip()

        existing = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)
        kind = peer_kind if peer_kind in ('human', 'agent') else _peer_kind(peer_hasn_id)
        now = _now_ms()
        new_version = (int(existing.version) if existing else 0) + 1
        token_count = _estimate_tokens(text)

        await db.execute(
            pg_insert(PeerPortrait)
            .values(
                owner_id=owner_id,
                peer_hasn_id=peer_hasn_id,
                peer_kind=kind,
                portrait_text=text,
                language='zh',
                version=new_version,
                revised_by=revised_by[:40],
                source_fact_count=int(existing.source_fact_count or 0) if existing else 0,
                last_synthesized_at=now,
                last_interaction_at=existing.last_interaction_at if existing else None,
                token_count=token_count,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=['owner_id', 'peer_hasn_id'],
                set_={
                    'peer_kind': kind,
                    'portrait_text': text,
                    'version': new_version,
                    'revised_by': revised_by[:40],
                    'last_synthesized_at': now,
                    'token_count': token_count,
                    'updated_at': now,
                },
            )
        )
        await db.flush()
        row = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)
        if row is None:
            # 同事务内落库后立刻读不到 = 不变量破坏，这才是 error 级。
            log.error(f'peer 画像落库后读不到该行：owner={owner_id} peer={peer_hasn_id}')
            raise RuntimeError(f'peer_portrait_vanished_after_upsert:{peer_hasn_id}')
        portrait = _serialize(row)
        await self.emit_portrait_downlink(db, owner_id=owner_id, portrait=portrait)
        return portrait

    async def delete_merged_portrait(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        peer_hasn_id: str,
    ) -> bool:
        """删除失去事实依据的合并态画像，并发出跨节点删除事件。"""
        owner_id = (owner_id or '').strip()
        peer_hasn_id = (peer_hasn_id or '').strip()
        if not owner_id or not peer_hasn_id:
            raise ValueError('owner_id 与 peer_hasn_id 必填')
        deleted = (
            await db.execute(
                sa.delete(PeerPortrait)
                .where(
                    PeerPortrait.owner_id == owner_id,
                    PeerPortrait.peer_hasn_id == peer_hasn_id,
                )
                .returning(PeerPortrait.peer_hasn_id)
            )
        ).scalar_one_or_none()
        await db.flush()
        # 即使云端已无该行也必须发删除事件：离线节点可能仍持有旧画像，clear 的语义是
        # 把所有镜像收敛到“不存在”，而不是仅报告本次 DELETE 是否命中。
        await self.emit_portrait_deleted_downlink(
            db,
            owner_id=owner_id,
            peer_hasn_id=peer_hasn_id,
        )
        return deleted is not None

    async def emit_portrait_deleted_downlink(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        peer_hasn_id: str,
    ) -> None:
        """发 `memory.peer_portrait.deleted`，让所有设备物理删除本地镜像。"""
        from backend.app.hasn.service.hasn_sync_service import hasn_sync_service

        await hasn_sync_service.gateway.emit_memory_event(
            db,
            owner_id=owner_id,
            event_type='memory.peer_portrait.deleted',
            namespace='portraits',
            aggregate_id=peer_hasn_id,
            payload={'peer_hasn_id': peer_hasn_id, 'deleted_at': _now_ms()},
        )

    async def emit_portrait_downlink(self, db: AsyncSession, *, owner_id: str, portrait: dict[str, Any]) -> None:
        """发 `memory.peer_portrait.upserted` 下行事件（doc17 P3 · G4，doc19 §5.3 回灌）。

        经 hasn_sync_service.emit_memory_event（namespace='portraits'）写 hasn_sync_events →
        daemon `pull_memory_events` 增量拉取 → `parse_peer_portrait_payload` 校验落本地镜像。
        payload 字段名严格对齐 daemon 解析器：正文键是 **portrait**（非 portrait_text），
        created_at/updated_at 必填（epoch ms）。
        """
        from backend.app.hasn.service.hasn_sync_service import hasn_sync_service

        peer_hasn_id = portrait['peer_hasn_id']
        body = {
            'peer_hasn_id': peer_hasn_id,
            'peer_kind': portrait.get('peer_kind') or 'human',
            'portrait': portrait.get('portrait_text') or '',
            'language': portrait.get('language') or 'zh',
            'version': int(portrait.get('version') or 1),
            'revised_by': portrait.get('revised_by') or 'system',
            'last_interaction_at': portrait.get('last_interaction_at'),
            'created_at': int(portrait['created_at']),
            'updated_at': int(portrait['updated_at']),
        }
        await hasn_sync_service.gateway.emit_memory_event(
            db,
            owner_id=owner_id,
            event_type='memory.peer_portrait.upserted',
            namespace='portraits',
            aggregate_id=peer_hasn_id,
            payload=body,
        )


def _serialize(row: PeerPortrait) -> dict[str, Any]:
    return {
        'owner_id': row.owner_id,
        'peer_hasn_id': row.peer_hasn_id,
        'peer_kind': row.peer_kind,
        'portrait_text': row.portrait_text,
        'language': row.language,
        'version': int(row.version or 1),
        'revised_by': row.revised_by,
        'source_fact_count': int(row.source_fact_count or 0),
        'last_synthesized_at': row.last_synthesized_at,
        'last_interaction_at': row.last_interaction_at,
        'token_count': int(row.token_count or 0),
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


peer_portrait_service = PeerPortraitService()
