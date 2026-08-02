"""模型注册表的读侧派生（价格档位计算 + 下发视图）。

## 为什么档位是**算出来的**而不是标出来的

`capability` 必须人工标注（靠模型名猜在我们自己的网关上已被证伪），但 `cost_tier` 恰恰相反：
它是从真实价格算出来的事实，且价格会随运营调价漂移——人工标注反而会过期。判据是**结论从哪来**。

## 为什么只给档位、不给倍率

分身和主人需要的判断只有一个：这个模型贵不贵。下发原始倍率等于把内部计费口径泄漏出去，
还会招来「为什么这次扣得比算出来的多」的无谓解释（绝对消耗还要乘分组倍率与各模态自己的
计费公式）。所以对外只有 `economy` / `standard` / `premium` 三档。

## 两条诚实边界

- **拉不到价格就不标档位**——绝不编一个默认值让分身照着花主人的钱；
- **同类里不足两个可比模型时整体不分档**——档位是比较出来的结论，唯一选择既不贵也不便宜，
  标成 `economy` 等于凭空暗示它便宜。

档位**按 capability 分别取基准**：同类内比较才有意义，拿文生图的价去比视频的价没有意义。

设计事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §5.6
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from backend.app.hasn.service.video_model_catalog_service import cost_tier_of

if TYPE_CHECKING:
    from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry


def cost_tier_map(rows: Iterable[HasnModelRegistry]) -> dict[int, str]:
    """算出每一行的价格档位（按 capability 分组、组内以最便宜的为基准）。

    返回 ``{行 id: 档位}``；**没有价格、或同类可比模型不足两个的行不会出现在结果里**
    （调用方据此如实留空，绝不补一个默认档）。人工 `cost_tier_override` 由调用方叠加，
    本函数只负责「算出来的那一份」。
    """
    grouped: dict[str, list[tuple[int, Decimal]]] = defaultdict(list)
    for row in rows:
        if row.relative_cost is None:
            continue  # 拉不到价格就不标档位
        grouped[row.capability].append((row.id, Decimal(row.relative_cost)))

    tiers: dict[int, str] = {}
    for priced in grouped.values():
        # 不足两个可比模型 → 整组不分档（唯一选择既不贵也不便宜）。
        if len(priced) < 2:
            continue
        cheapest = min(cost for _, cost in priced)
        for row_id, cost in priced:
            tiers[row_id] = cost_tier_of(float(cost), float(cheapest))
    return tiers


def effective_cost_tier(row: HasnModelRegistry, computed: dict[int, str]) -> str | None:
    """人工覆盖优先，其次是算出来的；都没有则如实留空。"""
    return row.cost_tier_override or computed.get(row.id)
