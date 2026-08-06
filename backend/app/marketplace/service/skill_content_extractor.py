"""Shared SKILL.md content extraction for marketplace syncs.

Both syncs land a skill's files on disk before writing the DB row — github_sync
from a hasn-hub checkout, clawhub_sync from an extracted ClawHub zip — so the
on-disk skill directory is the single source for the detail-page content fields:

- the Markdown **body** (everything after the YAML frontmatter), and
- the recursive **file listing** (``{path, size}`` — names + byte sizes only,
  never contents),

plus **bilingual resolution** of the body (original on its source-language side,
translation on the other). Extracted here so github_sync and clawhub_sync share
one implementation instead of drifting copies.
"""

from __future__ import annotations

import hashlib
import operator
import os
import re

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from backend.app.marketplace.service.translation_service import translation_service

if TYPE_CHECKING:
    from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill

_FRONTMATTER_RE = re.compile(r'\A---\s*\n.*?\n---\s*(?:\n|\Z)', re.DOTALL)

# 中文正文的 CJK 字符占比阈值：达到即判为中文源。中英混排的技能文档（中文正文 +
# 大量英文 API 名/代码/URL）langdetect 会在 en/zh 间误判（实测 25%~29% CJK 的中文
# SKILL.md 被判成 en）；CJK 占比是稳定确定的信号。
_CJK_SOURCE_RATIO = 0.10


def detect_body_source_lang(
    body: str,
    source_language: str | None = None,
) -> Literal['en', 'zh']:
    """Decide the body's source language ('zh' or 'en') for bilingual storage.

    CJK ratio is the **primary, deterministic** signal — for a zh/en marketplace the
    real question is "does this contain substantial Chinese?". ``langdetect`` flips
    between en/zh on mixed technical docs and cannot be trusted as the first signal;
    it is only consulted when there is little/no CJK. ``source_language`` (name-derived)
    and a non-ASCII heuristic are last-resort fallbacks.
    """
    if not body:
        if source_language == 'zh':
            return 'zh'
        return 'en'
    cjk = sum(1 for ch in body if '一' <= ch <= '鿿')
    if cjk / len(body) >= _CJK_SOURCE_RATIO:
        return 'zh'
    detected = translation_service.detect_language(body)
    if detected == 'zh':
        return 'zh'
    if detected == 'en':
        return 'en'
    if source_language == 'zh':
        return 'zh'
    if source_language == 'en':
        return 'en'
    return 'zh' if any(ord(ch) > 127 for ch in body) else 'en'


def extract_skill_body(markdown: str) -> str:
    """Return the Markdown body after the YAML frontmatter block.

    When there is no frontmatter the whole document is treated as body.
    """
    match = _FRONTMATTER_RE.match(markdown)
    body = markdown[match.end():] if match else markdown
    return body.strip()


def list_skill_files(skill_dir: Path) -> list[dict[str, Any]]:
    """List files under the skill directory (recursive) as ``{path, size}``.

    Only file names (relative POSIX paths) and byte sizes are captured — never
    file contents. Mirrors the package-zip filters: hidden dirs/files,
    ``__pycache__`` and ``.pyc`` are skipped. Sorted by path for stable output.
    """
    files: list[dict[str, Any]] = []
    for root, dirs, names in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for name in names:
            if name.startswith('.') or name.endswith('.pyc'):
                continue
            file_path = Path(root) / name
            try:
                size = file_path.stat().st_size
            except OSError:
                size = None
            files.append({'path': file_path.relative_to(skill_dir).as_posix(), 'size': size})
    files.sort(key=operator.itemgetter('path'))
    return files


def _normalize_text_for_hash(text: str) -> str:
    """规范化文本用于稳定指纹：统一换行 + 去每行行尾空白 + 去首尾空白。

    避免 CRLF/LF 差异、行尾多余空格造成的 content_hash 抖动（否则每次同步都误判
    内容变化、桌面端反复重拉）。
    """
    unified = text.replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(line.rstrip() for line in unified.split('\n')).strip()


def compute_skill_content_hash(skill_dir: Path, skill_md_text: str) -> str:
    """技能源内容指纹（doc14 §A1）：sha256(规范化SKILL.md 全文 + 排序后附带文件指纹)[:16]。

    - SKILL.md 全文（含 frontmatter + 正文）按 `_normalize_text_for_hash` 规范化后入哈希，
      逐字覆盖技能说明的任何改动。
    - 附带文件（脚本/参考/icon，**不含 SKILL.md 自身**，其内容已经由上面的全文覆盖）按相对
      路径排序，每个文件入 `path:sha256(原始字节)`——附带文件改动（含二进制 icon）也会变指纹。
    - 与 list_skill_files 同一套过滤：跳过隐藏目录/文件、__pycache__、.pyc。
    - 截断到 16 hex（与 common_skills_revision 短码同量级），稳定可比较、零随机/时间。
    """
    h = hashlib.sha256()
    h.update(_normalize_text_for_hash(skill_md_text).encode('utf-8'))
    h.update(b'\n--FILES--\n')
    digests: list[str] = []
    for root, dirs, names in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for name in names:
            if name.startswith('.') or name.endswith('.pyc'):
                continue
            file_path = Path(root) / name
            rel = file_path.relative_to(skill_dir).as_posix()
            if rel == 'SKILL.md':
                continue  # 全文已单独入哈希，避免重复计入
            try:
                file_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            except OSError:
                file_digest = 'unreadable'
            digests.append(f'{rel}:{file_digest}')
    for line in sorted(digests):
        h.update(line.encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()[:16]


async def resolve_bilingual_body(
    existing_skill: MarketplaceSkill | None,
    source_language: str | None,
    body: str,
) -> tuple[str | None, str | None]:
    """Resolve ``(body_en, body_zh)``: original on its source side, translation on the other.

    Change-gated: when the existing row already stores the identical source-side
    body AND a non-empty target-side translation, the cached translation is reused
    instead of calling the LLM again — so steady-state syncs don't re-translate
    unchanged readmes. An empty body clears both sides (honest: no readme).

    Zero fake: a failed translation leaves the target side ``None`` (the serializer
    falls back to the original-language body) rather than duplicating the source.
    """
    if not body.strip():
        return None, None

    # 正文语言**按正文本身判定**（CJK 占比为主信号），不沿用 name 推出的
    # source_language——技能常见英文名/中文正文（或反之），按名字判会把正文落到错误侧。
    src = detect_body_source_lang(body, source_language)
    tgt: Literal['en', 'zh'] = 'zh' if src == 'en' else 'en'

    existing_src = getattr(existing_skill, f'body_{src}', None) if existing_skill else None
    existing_tgt = getattr(existing_skill, f'body_{tgt}', None) if existing_skill else None
    if existing_skill and existing_src == body and existing_tgt:
        tgt_body = existing_tgt
    else:
        tgt_body = await translation_service.translate_markdown(body, src, tgt)

    # Zero-fake echo guard: very long docs can exceed the model's budget and come
    # back byte-identical to the source — that is NOT a translation. Drop it so the
    # target side stays null and the serializer falls back to the source-language
    # body, rather than storing the source text mislabeled as the other language.
    if tgt_body is not None and tgt_body.strip() == body.strip():
        tgt_body = None

    sides = {src: body, tgt: tgt_body}
    return sides.get('en'), sides.get('zh')


def raw_bilingual_body(
    source_language: str | None,
    body: str,
) -> tuple[str | None, str | None]:
    """Resolve ``(body_en, body_zh)`` WITHOUT translating: original text on its
    detected source side, ``None`` on the other.

    Used by bulk seed syncs that intentionally skip body translation (cost/time):
    the readme is stored as-is on its own language side, and the serializer falls
    back to that side for the other language. Zero fake — we never duplicate the
    source text mislabeled as a translation. An empty body clears both sides.

    No I/O / no LLM, so this is a plain (sync) function unlike
    :func:`resolve_bilingual_body`.
    """
    if not body.strip():
        return None, None
    src = detect_body_source_lang(body, source_language)
    tgt = 'zh' if src == 'en' else 'en'
    sides = {src: body, tgt: None}
    return sides.get('en'), sides.get('zh')
