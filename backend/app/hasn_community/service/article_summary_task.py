"""文章摘要 LLM 异步提取（保存后台任务，平台买单）。

用户选定：用云端平台模型（HUANXING_HERMES_PLATFORM_LLM_*，OpenAI 兼容端点）提取摘要，
成本由平台承担。经 FastAPI BackgroundTasks 在响应后触发，开独立会话回写
hasn_articles.summary。best-effort：未配置平台 key / 调用失败 / 文章已有摘要 时安静跳过，
展示层仍由 article_summary.effective_summary 从正文兜底（见 [[article_summary]]）。
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select

from backend.app.hasn_community.model import HasnArticles
from backend.core.conf import settings
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

# 正文太短不值得调用 LLM（直接兜底足够）。
_MIN_CONTENT_LEN = 60
_MAX_CONTENT_CHARS = 6000  # 截断送入 LLM，控成本
_SUMMARY_MAX_LEN = 200


def _platform_model() -> str:
    """平台模型名：OpenAI 兼容端点用裸 alias（去掉 litellm 的 `openai/` 前缀）。"""
    raw = getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_MODEL', '') or 'gpt-5.5'
    return raw.split('/', 1)[1] if raw.startswith('openai/') else raw


async def _call_platform_llm(content: str) -> str | None:
    """调平台 LLM（OpenAI 兼容）取一句话摘要；任何异常返回 None（best-effort）。"""
    base_url = getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_BASE_URL', '') or ''
    api_key = getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_API_KEY', '') or ''
    if not base_url or not api_key:
        logger.info('article summary: 平台 LLM 未配置，跳过 LLM 提取（保留正文兜底）')
        return None
    payload = {
        'model': _platform_model(),
        'messages': [
            {'role': 'system', 'content': '你是中文摘要助手。用一句话（不超过 80 字）概括文章主旨，直接输出摘要正文，不要任何前缀、引号或解释。'},
            {'role': 'user', 'content': content[:_MAX_CONTENT_CHARS]},
        ],
        'max_tokens': 256,
        'temperature': 0.3,
        'stream': False,
    }
    url = base_url.rstrip('/') + '/chat/completions'
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers={'Authorization': f'Bearer {api_key}'})
        resp.raise_for_status()
        data = resp.json()
    choices = data.get('choices') or []
    if not choices:
        return None
    text = (choices[0].get('message') or {}).get('content') or ''
    return ' '.join(text.split()).strip()[:_SUMMARY_MAX_LEN] or None


async def extract_and_store_summary(article_id: str) -> None:
    """后台任务：为空摘要文章用平台 LLM 生成摘要并回写。失败仅日志，不影响发布。"""
    try:
        async with async_db_session() as session:
            article = (
                await session.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))
            ).scalars().first()
            if not article:
                return
            if (article.summary or '').strip():
                return  # 已有摘要（用户手填或并发已填），不覆盖
            content = (article.content or '').strip()
            if len(content) < _MIN_CONTENT_LEN:
                return
            summary = await _call_platform_llm(content)
            if not summary:
                return
            # 再查一次，避免并发期间被手填覆盖
            fresh = (
                await session.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))
            ).scalars().first()
            if fresh and not (fresh.summary or '').strip():
                fresh.summary = summary
                await session.commit()
                logger.info('article summary: 已为 %s 写入 LLM 摘要（%d 字）', article_id, len(summary))
    except Exception as exc:  # noqa: BLE001 — best-effort，任何失败都不应影响发布
        logger.warning('article summary: LLM 提取失败 article_id=%s err=%r（保留正文兜底）', article_id, exc)
