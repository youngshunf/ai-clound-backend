"""默认/内置分身头像生成器——DiceBear 风格的确定性几何头像（seed → SVG）。

背景：默认分身历史上一律取模板 `icon_url`，导致「每个人的默认分身头像都一样」。
DiceBear 官方是 JS 库、且走在线 CDN 会依赖第三方、不符合我们「资产落桶」惯例，故在
云端 Python 侧实现一个**无外部依赖、不出网**的确定性几何头像生成器：

  seed（`{owner_id}:{agent_name}`）→ sha256 字节流 → 确定性 PRNG → 调色板 + 几何形 → SVG。

- **确定性**：同 seed 恒定产出同一 SVG（PRNG 完全由哈希字节驱动，无 `random`/时间）；
  不同 seed 几乎必然不同 → 每个分身一张、无限不重复。
- **落桶**：生成的 SVG 上传公共桶 `avatars/generated/agent-{hash}.svg`，`avatar` 存**桶 URL**
  （不是 webui 相对路径），多端一致、别人可见（见「hasn:// 客户端无关」原则）。
- **best-effort**：无公共桶配置 / 上传失败一律返回 None，调用方回退模板 icon 或预置池，
  绝不阻断分身注册（零 fake、错误如实记 warn，属可恢复的镜像回填失败）。
"""

from __future__ import annotations

import hashlib

from typing import TYPE_CHECKING

from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 画布尺寸（viewBox）。SVG 可无损缩放，前端按需渲染。
_CANVAS = 80


class _SeededRng:
    """基于 sha256(seed) 字节流的确定性 PRNG——同 seed 恒定产出同一序列，无外部依赖、不出网。

    反复哈希扩展字节池，够画多层几何形消费；超出池长度后循环取用（对头像随机性已足够）。
    """

    def __init__(self, seed: str) -> None:
        pool = b''
        h = hashlib.sha256(seed.encode('utf-8')).digest()
        while len(pool) < 64:
            pool += h
            h = hashlib.sha256(h).digest()
        self._pool = pool
        self._i = 0

    def _byte(self) -> int:
        b = self._pool[self._i % len(self._pool)]
        self._i += 1
        return b

    def randint(self, lo: int, hi: int) -> int:
        """闭区间 [lo, hi] 内的确定性整数。"""
        span = hi - lo + 1
        return lo + (self._byte() % span)

    def choice(self, seq: list[str]) -> str:
        return seq[self._byte() % len(seq)]


def _hsl(h: int, s: int, lightness: int) -> str:
    return f'hsl({h}, {s}%, {lightness}%)'


def _render_shape(rng: _SeededRng, kind: str, color: str, size: int) -> str:
    """在画布内摆一个几何形（圆/方/三角），位置与大小由 seed 决定，恒定在画布范围内。"""
    if kind == 'circle':
        r = rng.randint(size // 5, size // 2)
        cx = rng.randint(r, size - r) if size - r > r else size // 2
        cy = rng.randint(r, size - r) if size - r > r else size // 2
        return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>'
    if kind == 'square':
        w = rng.randint(size // 4, size // 2)
        x = rng.randint(0, size - w)
        y = rng.randint(0, size - w)
        rot = rng.randint(0, 45)
        cx, cy = x + w // 2, y + w // 2
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{w}" rx="{max(1, w // 8)}" '
            f'fill="{color}" transform="rotate({rot} {cx} {cy})"/>'
        )
    # triangle
    s = rng.randint(size // 3, size)
    x = rng.randint(0, max(1, size - s))
    y = rng.randint(0, max(1, size - s))
    pts = f'{x},{y + s} {x + s // 2},{y} {x + s},{y + s}'
    return f'<polygon points="{pts}" fill="{color}"/>'


def generate_geometric_avatar_svg(seed: str, *, size: int = _CANVAS) -> str:
    """由 seed 确定性生成一张几何头像 SVG 字符串（纯函数、无 I/O、无外部依赖）。

    组成：柔和浅色背景 + 2~3 个分层几何形（同源强调色，和谐不刺眼）。
    同 seed 必得同一 SVG；不同 seed 几乎必然不同。
    """
    rng = _SeededRng(seed)
    hue = rng.randint(0, 359)
    # 柔和浅色背景 + 三档同源强调色（本色/相邻/互补色相），观感协调
    bg = _hsl(hue, rng.randint(45, 65), rng.randint(88, 94))
    accents = [
        _hsl(hue, rng.randint(60, 72), rng.randint(48, 56)),
        _hsl((hue + 35) % 360, rng.randint(55, 68), rng.randint(52, 60)),
        _hsl((hue + 330) % 360, rng.randint(50, 62), rng.randint(44, 54)),
    ]
    parts = [f'<rect width="{size}" height="{size}" fill="{bg}"/>']
    shapes = ['circle', 'square', 'triangle']
    count = rng.randint(2, 3)
    for idx in range(count):
        kind = rng.choice(shapes)
        color = accents[idx % len(accents)]
        parts.append(_render_shape(rng, kind, color, size))
    body = ''.join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}">{body}</svg>'
    )


async def resolve_generated_avatar_url(db: AsyncSession, seed: str) -> str | None:
    """为默认/内置分身生成确定性几何头像并落公共桶，返回可跨端渲染的桶 URL。

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

        svg = generate_geometric_avatar_svg(seed)
        digest = hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]
        object_path = f'avatars/generated/agent-{digest}.svg'
        await write_bytes(s3_storage, object_path, svg.encode('utf-8'), 'image/svg+xml')
        return build_object_url(s3_storage, object_path)
    except Exception as exc:
        log.warning('生成/上传默认分身头像失败 seed=%s: %s', seed, exc)
        return None
