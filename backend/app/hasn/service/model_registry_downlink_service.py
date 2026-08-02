"""模型注册表下发（daemon 拉取面）。

## 下发什么、不下发什么

下发 `{name, capability, inputs, quality, cost_tier, scenario, vendor}`——分身要的判断只有
「这个模型吃不吃得下我手上的素材」「贵不贵」「质量如何」「适合什么场景」。

**不下发 `relative_cost`**：原始计费倍率是内部参数，下发出去等于把计费口径泄漏给分身和主人，
还会招来「为什么这次扣得比算出来的多」的无谓解释（绝对消耗还要乘分组倍率与各模态自己的
计费公式）。它只在 Admin 页可见，供运维核对档位算得对不对。

## 谁能进候选

三条硬闸，任一不过即不下发：

1. `capability != 'unclassified'`——未标注的能力猜不出来，猜错会让分身把文生图请求发给 TTS 模型；
2. `upstream_status == 'active'`——网关上已消失的模型发出去只会 503；
3. `agent_visible`——运营显式放开才对分身可见。

## 为什么不并进 `/platform/config`

PDC 的 revision 由 `config_json` 算，而注册表内容不在其中——并进去会「内容变了 revision 没变」，
daemon 缓存永远刷不新。故注册表自带 `registry_revision`，daemon 按它判断是否重拉。

设计事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §5
"""

from __future__ import annotations

import hashlib
import json

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry
from backend.app.hasn.service.model_registry_catalog_service import cost_tier_map, effective_cost_tier

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def compute_registry_revision(grouped: dict[str, list[dict[str, Any]]]) -> str:
    """下发内容指纹：**对 daemon 真正收到的那份 payload 取哈希**。

    这样「revision 变了」与「下发内容变了」是同一件事，不多也不少：

    - 不能只看 `max(updated_time)`——同步器每轮都要刷 `last_synced_time`（记录「这轮还在网关上」），
      那是一次真实 UPDATE，`updated_time` 的 `onupdate` 随之顶新。指纹若含它，**每天 04:40
      定时同步都会让全网 daemon 被 WSPUSH 打醒重拉整张注册表**，哪怕一个字段都没变；
    - 也不能只看行数 + 模型名集合——改标注（能力、`inputs`、档位覆盖、排序）时名字和行数都没动，
      daemon 就永远刷不新。

    对 payload 本身取指纹两头都覆盖：任何影响分身选型的改动必然改指纹，任何不影响的写入必然不改。
    """
    seed = json.dumps(grouped, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]


def _to_downlink(row: HasnModelRegistry, tiers: dict[int, str]) -> dict[str, Any]:
    """一行注册表 → 一条下发记录（**不含** `relative_cost` / `cost_extra` / `enable_groups`）。"""
    entry: dict[str, Any] = {
        'name': row.model_name,
        'capability': row.capability,
        # 输入要求表：省略即 unsupported；daemon 据此在提交前过滤候选、前置拒绝喂不进去的输入。
        'inputs': dict(row.inputs or {}),
        'sort_order': row.sort_order,
    }
    # 可选语义一律「有才带」——留空即未标注，绝不编一个默认值让分身照着做决定。
    if row.quality:
        entry['quality'] = row.quality
    if row.scenario:
        entry['scenario'] = row.scenario
    if row.dialect:
        entry['dialect'] = row.dialect
    if row.vendor_name:
        entry['vendor'] = row.vendor_name
    tier = effective_cost_tier(row, tiers)
    if tier:
        entry['cost_tier'] = tier
    return entry


class ModelRegistryDownlinkService:
    """按能力类别分组下发注册表（daemon 拉取）。"""

    async def list_downlink(self, db: AsyncSession) -> dict[str, Any]:
        """返回 ``{models: {capability: [...]}, registry_revision, total}``。

        注册表整个是空的（还没同步过）→ 返回空分组 + 稳定 revision，daemon 据此保持既有行为，
        **不报错也不伪造模型**（零 fake：宁可让分身看到「当前无可用模型」）。
        """
        rows = list(
            (
                await db.execute(
                    sa.select(HasnModelRegistry).order_by(
                        HasnModelRegistry.capability.asc(),
                        HasnModelRegistry.sort_order.asc(),
                        HasnModelRegistry.model_name.asc(),
                    )
                )
            ).scalars()
        )
        visible = [
            row
            for row in rows
            if row.capability != 'unclassified' and row.upstream_status == 'active' and row.agent_visible
        ]
        # 档位在**参与下发的这批**里按能力分组比价——回答的正是分身该问的那个问题：
        # 在我能用的这几个里，这个算贵的还是便宜的。
        tiers = cost_tier_map(visible)

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in visible:
            grouped[row.capability].append(_to_downlink(row, tiers))
        models = dict(grouped)
        return {
            'models': models,
            'registry_revision': compute_registry_revision(models),
            'total': len(visible),
        }


model_registry_downlink_service = ModelRegistryDownlinkService()
