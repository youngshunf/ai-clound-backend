"""默认/内置分身头像生成器——DiceBear bottts 机器人风格（seed → SVG）。

背景：默认分身历史上一律取模板 `icon_url`，导致「每个人的默认分身头像都一样」。
早期改用自写的几何头像生成器（circle/square/triangle 随机摆放），但观感杂乱、不规则、
不好看。现统一改用 DiceBear 官方 **bottts（机器人）** 风格——一眼即「AI 分身」、契合产品
定位、免费商用（style license: *Free for personal and commercial use*）。

DiceBear 官方是 JS 库、且默认走在线 CDN 会依赖第三方，不符合我们「资产落桶」惯例，故用其
**离线 Python 移植**（`dicebear-core` + `dicebear-styles`，纯本地、不出网、无 CDN）在云端
Python 侧生成：

  seed（`{owner_id}:{agent_name}`）→ DiceBear bottts → 确定性 SVG。

- **确定性**：同 seed 恒定产出同一 SVG（DiceBear seed 驱动内部 PRNG，无 `random`/时间）；
  不同 seed 几乎必然不同 → 每个分身一张、无限不重复。
- **落桶**：生成的 SVG 上传公共桶 `avatars/generated/agent-{hash}.svg`，`avatar` 存**桶 URL**
  （不是 webui 相对路径），多端一致、别人可见（见「hasn:// 客户端无关」原则）。
- **best-effort**：无公共桶配置 / 上传失败一律返回 None，调用方回退模板 icon 或预置池，
  绝不阻断分身注册（零 fake、错误如实记 warn，属可恢复的镜像回填失败）。

> 迁移策略「只影响新建」：本模块只在**新建分身且 avatar 为空**时被调用回填，存量分身
> （avatar 已 = 模板 icon 或旧几何头像）不会被触发覆盖。
"""

from __future__ import annotations

import hashlib
import importlib.resources as resources

from functools import lru_cache
from typing import TYPE_CHECKING

from dicebear import Avatar, Style

from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 默认/内置分身头像统一用 bottts（机器人）风格——一眼即「AI 分身」，契合产品定位、免费商用。
_AVATAR_STYLE_NAME = 'bottts'


@lru_cache(maxsize=1)
def _bottts_style() -> Style:
    """懒加载并缓存 DiceBear bottts 风格定义（进程内只解析一次 JSON，后续复用）。"""
    raw = (
        resources.files('dicebear_styles')
        .joinpath(f'{_AVATAR_STYLE_NAME}.json')
        .read_text('utf-8')
    )
    return Style.from_json(raw)


def generate_bottts_avatar_svg(seed: str) -> str:
    """由 seed 确定性生成一张 DiceBear bottts 头像 SVG 字符串（纯函数、无 I/O、不出网）。

    同 seed 必得同一 SVG；不同 seed 几乎必然不同（每个分身一张、无限不重复）。
    """
    return Avatar(_bottts_style(), {'seed': seed}).to_string()


async def resolve_generated_avatar_url(db: AsyncSession, seed: str) -> str | None:
    """为默认/内置分身生成确定性 bottts 头像并落公共桶，返回可跨端渲染的桶 URL。

    - **幂等**：object path 由 seed 哈希决定，重复注册覆盖同一对象、不产生垃圾。
    - **best-effort**：无公共桶配置 / 上传失败一律返回 None，调用方回退模板 icon 或预置池，
      绝不阻断分身注册（可恢复的镜像回填失败，记 warn）。
    """
    try:
        from backend.plugin.s3.crud.storage import s3_storage_dao
        from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes

        storages = await s3_storage_dao.get_all(db)
        s3_storage = pick_public_storage(storages)
        if not s3_storage:
            # 无公共桶配置（如本地 dev 未接 S3）→ 回退，不报错
            return None

        svg = generate_bottts_avatar_svg(seed)
        digest = hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]
        object_path = f'avatars/generated/agent-{digest}.svg'
        await write_bytes(s3_storage, object_path, svg.encode('utf-8'), 'image/svg+xml')
        return build_object_url(s3_storage, object_path)
    except Exception as exc:
        log.warning('生成/上传默认分身头像失败 seed=%s: %s', seed, exc)
        return None
