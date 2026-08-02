"""PDC 写入的模型名校验（本次事故的根因闭环）。

## 修的是什么

2026-08-02 线上视频全线不可用，根因之一是 PDC 里配的模型名**在网关上根本不存在**：
配的是 `agnes-2.0-video`，真名是 `agnes-video-v2.0`；中途改成 `agnes-video-2.0` 仍然是错的。
错法很隐蔽——请求打到网关才 503，而 503 看起来像「渠道没开通」，排查方向直接被带偏。

保存时校验一次，这个错法就从源头消失：**再也不可能存进一个网关上不存在的名字**。

## 判据

模型名必须在注册表里且 `upstream_status='active'`。两条都要：
- 不在注册表 → 网关上没有这个模型（同步器每天从 `/api/pricing` 拉全量）；
- 标了 `missing` → 曾经有、现在网关上没了，存进去照样 503。

拒绝时给**最接近的候选**（编辑距离），把「`agnes-2.0-video` → 你是不是想填 `agnes-video-v2.0`」
直接摆在运营面前，而不是让他自己去猜哪个字母写错了。

## 为什么不校验 `capability` 对不对

槽位与能力的对应（`tts_models` 里应当只有 TTS 模型）当然也该对，但那是**语义**问题，
运营标注 capability 时可能还没标完；而模型名写错是**事实**问题，当场就能判死。
把两件事混在一起会让「标注还没做完」阻塞掉正常的配置保存。

设计事实源：docs/hasn-node设计文档/运行时配置下发/02-模型注册表与语义标注下发设计.md §5.2
"""

from __future__ import annotations

import difflib

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model.hasn_model_registry import HasnModelRegistry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfig

# 建议候选的最低相似度（difflib 比值）。太低会给出毫不相干的建议，反而误导。
_SUGGESTION_CUTOFF = 0.5
# 每个错名最多给几个建议。
_SUGGESTION_LIMIT = 3


def collect_configured_models(config: PlatformDefaultConfig) -> list[tuple[str, str]]:
    """把 PDC 里所有**模型名**位置摊平成 `[(配置路径, 模型名)]`。

    覆盖 `node.media.*_models` 五处、`agent_runtime.models` 四槽、`model_fallback_pool`。
    视频那一处元素可能是字符串或对象，两种写法都取出 `name`。
    """
    found: list[tuple[str, str]] = []
    media = config.node.media
    for field in ('image_models', 'image_edit_models', 'tts_models', 'stt_models'):
        for index, name in enumerate(getattr(media, field, []) or []):
            if isinstance(name, str) and name.strip():
                found.append((f'node.media.{field}[{index}]', name.strip()))
    for index, spec in enumerate(media.video_models or []):
        name = spec if isinstance(spec, str) else getattr(spec, 'name', '')
        if isinstance(name, str) and name.strip():
            found.append((f'node.media.video_models[{index}]', name.strip()))

    runtime = config.agent_runtime
    for slot in ('main', 'fast', 'vision', 'delegation'):
        name = getattr(runtime.models, slot, None)
        if isinstance(name, str) and name.strip():
            found.append((f'agent_runtime.models.{slot}', name.strip()))
    for index, name in enumerate(runtime.model_fallback_pool or []):
        if isinstance(name, str) and name.strip():
            found.append((f'agent_runtime.model_fallback_pool[{index}]', name.strip()))
    return found


def _suggest(name: str, known: Iterable[str]) -> list[str]:
    """给最接近的候选（编辑距离）。没有足够接近的就不给——瞎猜的建议比不给更误导。"""
    return difflib.get_close_matches(name, list(known), n=_SUGGESTION_LIMIT, cutoff=_SUGGESTION_CUTOFF)


def build_rejections(
    configured: list[tuple[str, str]],
    active: set[str],
    missing: set[str],
) -> list[dict[str, Any]]:
    """纯函数判定：返回每条不合法配置的 `{path, model, reason, suggestions}`。

    `active`/`missing` 分开是有意的——两者的运营动作完全不同：不在注册表要改名字，
    标 `missing` 要么等渠道回来、要么换一个，错误文案必须说清是哪种。
    """
    rejections: list[dict[str, Any]] = []
    for path, name in configured:
        if name in active:
            continue
        if name in missing:
            rejections.append({
                'path': path,
                'model': name,
                'reason': '该模型在网关上已消失（upstream_status=missing）',
                'suggestions': _suggest(name, active),
            })
            continue
        rejections.append({
            'path': path,
            'model': name,
            'reason': '该模型不在模型注册表里（网关上没有这个名字）',
            'suggestions': _suggest(name, active),
        })
    return rejections


def format_rejections(rejections: list[dict[str, Any]]) -> str:
    """把判定结果排成一句运营看得懂、能直接照做的错误消息。"""
    lines = []
    for item in rejections:
        line = f'{item["path"]} = {item["model"]!r}：{item["reason"]}'
        if item['suggestions']:
            line += f'。你是不是想填：{"、".join(item["suggestions"])}'
        lines.append(line)
    return '模型名校验未通过（这些名字保存后打到网关只会 503）：' + '；'.join(lines)


class PdcModelValidationService:
    """PDC 保存前的模型名校验。"""

    @staticmethod
    async def load_registry_names(db: AsyncSession) -> tuple[set[str], set[str]]:
        """取注册表里的 `(active 模型名, missing 模型名)`。"""
        rows = (
            await db.execute(sa.select(HasnModelRegistry.model_name, HasnModelRegistry.upstream_status))
        ).all()
        active = {name for name, status in rows if status == 'active'}
        missing = {name for name, status in rows if status != 'active'}
        return active, missing

    async def validate(self, db: AsyncSession, config: PlatformDefaultConfig) -> list[dict[str, Any]]:
        """校验一份待保存的 PDC，返回不合法项（空列表 = 通过）。

        **注册表整个是空的时候放行**：那说明还没同步过（刚建表 / 新环境），此时拦住保存等于
        让运营在「同步」和「配置」之间死锁。同步一跑起来校验自然生效——宁可晚一天生效，
        也不要让一个还没准备好的守卫把正常运维卡死。
        """
        active, missing = await self.load_registry_names(db)
        if not active and not missing:
            return []
        return build_rejections(collect_configured_models(config), active, missing)


pdc_model_validation_service = PdcModelValidationService()
