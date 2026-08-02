"""视频模型目录（下发给 daemon，最终暴露给分身用于自主选型）。

## 为什么要合并三个来源，而不是在 PDC 里再配一遍模型清单

**new-api 是模型的唯一事实源**（福仔 2026-06-15 已就 LLM 模型拍过同样的板，见
``app/newapi/api/v1/llm_models.py``）：哪些模型真的有渠道、当前是否启用、相对成本多少，
只有 new-api 知道。PDC 里再抄一份「可用模型清单」必然与它漂移——2026-08-02 线上那次
`agnes-2.0-video` 503 就是抄错了名字（真名 `agnes-video-v2.0`），抄的人无从发现。

所以三个来源各司其职，谁也不重复谁：

- **new-api 注册表**（`list_available_models`）→ 这个模型现在还在不在、启没启用。
- **new-api 定价表**（`get_pricing`）→ 相对成本 `model_ratio`。
- **PDC 语义表**（`node.media.video_models`）→ new-api **表达不了**的东西：输入模态
  （文生/图生）、入参方言（阿里档位 vs OpenAI 像素）、质量档与适用场景。

合并规则是**交集**：语义表列出的模型，只有在 new-api 里真实可用才会下发。语义表没列的
视频模型不会凭空出现（能力语义猜不出来，猜错就是静默失败），但会 warn 提示运营去补。

## 为什么分身需要价格，以及为什么只给档位

分身代表主人利益。同一件事用 `wan2.6-i2v-flash` 还是 `happyhorse-1.1-i2v` 差 5 倍，草稿和
终稿显然不该用同一个。不把成本告诉分身，它只能瞎选或永远用第一个——那笔钱是主人的。

但**只给档位、不给原始倍率**：倍率是内部计费参数，下发出去等于把计费口径泄漏给分身和主人，
而他们要的判断只有「这个贵不贵」。档位在本次实际下发的模型之间比较得出。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.hasn_platform_default_config import VideoModelSpec
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.common.log import log


# 价格档位阈值：相对**同类最便宜的那个**的倍数。分身只需要判断贵不贵，
# 不需要（也不该）知道我们的计费口径。
_ECONOMY_MAX_MULTIPLE = 1.5
_STANDARD_MAX_MULTIPLE = 4.0


def cost_tier_of(ratio: float, cheapest: float) -> str:
    """把计费倍率折成价格档位（`economy` / `standard` / `premium`）。

    以同类里最便宜的模型为基准取倍数分档——绝对倍率是内部计费参数，下发出去等于泄漏计费
    口径，而分身要的判断只有「这个比那个贵多少」。实测视频三档正好落成：
    `wan2.6-i2v-flash` 0.5（基准 → economy）、`agnes-video-v2.0` 1.5（3 倍 → standard）、
    `happyhorse-1.1-i2v` 2.5（5 倍 → premium）。
    """
    if cheapest <= 0:
        return 'standard'
    multiple = ratio / cheapest
    if multiple <= _ECONOMY_MAX_MULTIPLE:
        return 'economy'
    if multiple <= _STANDARD_MAX_MULTIPLE:
        return 'standard'
    return 'premium'


def merge_catalog(
    declared: list[str | VideoModelSpec],
    available_names: set[str],
    pricing: dict[str, float],
) -> list[dict[str, Any]]:
    """把语义表与 new-api 事实合并成目录（纯函数，便于直接断言合并规则）。

    顺序沿用语义表，即运营指定的 failover 优先级。语义表里配了但 new-api 上没有的模型会被
    跳过并 warn——那多半是模型名写错或渠道被下掉，正是线上踩过的坑。

    价格以 `cost_tier` 档位下发，**不下发原始倍率**（那是内部计费参数）。档位在**本次实际
    下发的模型之间**比较得出，因此它回答的正是分身该问的那个问题：在我能用的这几个里，
    这个算贵的还是便宜的。
    """
    survivors: list[dict[str, Any]] = []
    for spec in declared:
        normalized = _normalize(spec)
        name = normalized['name']
        if not name:
            continue
        if name not in available_names:
            log.warning(f'[video-catalog] PDC 声明的视频模型 {name!r} 在 new-api 上不可用，已跳过')
            continue
        survivors.append(normalized)

    # 基准取「本次下发的模型里最便宜的那个」。**不足两个有价格的模型时整体不分档**：
    # 档位是比较出来的结论，唯一选择既不贵也不便宜，标成 economy 等于凭空给了个便宜的暗示。
    ratios = [pricing[m['name']] for m in survivors if m['name'] in pricing]
    cheapest = min(ratios) if len(ratios) >= 2 else None

    catalog: list[dict[str, Any]] = []
    for entry in survivors:
        ratio = pricing.get(entry['name'])
        if ratio is not None and cheapest is not None:
            entry['cost_tier'] = cost_tier_of(ratio, cheapest)
        catalog.append(entry)
    return catalog


def _normalize(spec: str | VideoModelSpec) -> dict[str, Any]:
    """把 PDC 的两种写法（模型名字符串 / 对象）归一成同一形状。"""
    if isinstance(spec, str):
        return {
            'name': spec.strip(),
            'modality': 'any',
            'dialect': 'openai',
            'quality': None,
            'notes': None,
        }
    return {
        'name': spec.name.strip(),
        'modality': spec.modality,
        'dialect': spec.dialect,
        'quality': spec.quality,
        'notes': spec.notes,
    }


class VideoModelCatalogService:
    """把 new-api 的可用性/定价与 PDC 的语义元数据合并成一份可下发的视频模型目录。"""

    async def list_catalog(self, db: AsyncSession) -> list[dict[str, Any]]:
        """返回合并后的视频模型目录。

        new-api 不可达时**不静默放行**：如实降级为空目录并 warn——宁可让分身看到「当前无可用
        视频模型」，也不能报一份不知真假的清单让它照着花钱（零 fake）。
        """
        config, _revision = await platform_default_config_service.get_effective_config(db)
        declared = config.node.media.video_models
        if not declared:
            return []

        available_names, pricing = await self._fetch_newapi_facts()
        if available_names is None:
            log.warning('[video-catalog] new-api 不可达，视频模型目录降级为空')
            return []
        return merge_catalog(declared, available_names, pricing)

    async def _fetch_newapi_facts(self) -> tuple[set[str] | None, dict[str, float]]:
        """从 new-api 定价表一次拿到「可用模型名集合」与「定价倍率表」。

        **为什么用 `/api/pricing` 而不是模型注册表 `/api/models/`**：2026-08-02 实测生产网关
        `llm.dcfuture.cn` 的注册表是**空的**（`total=0`）——运营只配渠道、从不登记模型，
        所以 `list_available_models()` 在生产恒返回空列表（既有端点
        `/api/v1/llm/models/available` 因此也一直返回 0 个模型）。定价表则由渠道聚合而来，
        实测 64 条，是当前唯一真实反映「网关上有哪些模型」的来源。

        拉不到 → 返回 ``(None, {})`` 让调用方整体降级为空目录，绝不报一份不知真假的清单
        让分身照着花钱（零 fake）。
        """
        try:
            rows = await newapi_admin_client.get_pricing()
        except NewApiError as error:
            log.warning(f'[video-catalog] 拉取 new-api 定价表失败: {error}')
            return None, {}

        available_names: set[str] = set()
        pricing: dict[str, float] = {}
        for row in rows:
            name = str(row.get('model_name') or '').strip()
            if not name:
                continue
            available_names.add(name)
            ratio = row.get('model_ratio')
            if isinstance(ratio, (int, float)):
                pricing[name] = float(ratio)
        return available_names, pricing


video_model_catalog_service = VideoModelCatalogService()
