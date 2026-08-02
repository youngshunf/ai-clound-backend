"""术语表载入与 prompt 注入（公共件）。

`glossary.json` 与轨道 A 的 `hasn-node/webui/scripts/i18n/glossary.json` **是同一份内容**
（逐字节相同）：界面里叫 Agent，内容翻译里也必须叫 Agent，否则同一个词在按钮上和帖子里
两个译法，用户会以为是两个东西。两边一致性由 `backend/tests/test_translation_glossary.py`
守。改术语表必须两边一起改。

只有 `terms` 段参与云端翻译（注入 prompt）；`forbidden`/`audits`/`overrides` 是轨道 A
的静态文案管线工具用的，云端不消费，但**照抄保留**以便两边保持逐字节一致。
"""

from __future__ import annotations

import json

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

GLOSSARY_PATH: Final[Path] = Path(__file__).parent / 'glossary.json'


@lru_cache(maxsize=1)
def load_glossary() -> dict[str, Any]:
    """载入术语表（进程内缓存一次）。"""
    return json.loads(GLOSSARY_PATH.read_text(encoding='utf-8'))


def glossary_terms(target_lang: str) -> dict[str, str]:
    """取目标语言的「中文原文 → 指定译法」映射；该语言没登记译法的词条自动略过。"""
    terms = load_glossary().get('terms') or {}
    pairs: dict[str, str] = {}
    for source, translations in terms.items():
        if not isinstance(translations, dict):
            continue
        translated = translations.get(target_lang)
        if isinstance(translated, str) and translated.strip():
            pairs[source] = translated.strip()
    return pairs


def glossary_prompt_block(target_lang: str, *, source_text: str | None = None) -> str:
    """生成注入 system prompt 的术语约束片段；无可用词条时返回 ``''``。

    传 ``source_text`` 时只注入**正文里真出现过**的词条：整张表塞进去会白烧 prompt
    token，而且给了模型把不相干词条硬套进译文的机会。
    """
    pairs = glossary_terms(target_lang)
    if source_text is not None:
        pairs = {src: dst for src, dst in pairs.items() if src in source_text}
    if not pairs:
        return ''
    lines = '\n'.join(f'- {source} -> {translated}' for source, translated in pairs.items())
    return (
        'Terminology (MANDATORY, use these exact renderings; they are product names '
        f'and must not be paraphrased):\n{lines}\n'
    )
