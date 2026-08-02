"""翻译公共件：语言检测、Markdown 结构保留、术语表、内容翻译器。

由 `backend/app/marketplace/service/translation_service.py` 抽出（语言检测 + 长文分段），
并补齐用户内容翻译需要的结构遮罩与术语表注入。两个消费方：

- `backend/app/marketplace/service/translation_service.py` —— 技能元数据双语化（历史口径）
- `backend/app/hasn/service/content_translation_service.py` —— 用户内容按需翻译（轨道 B）

事实源：docs/hasn-node设计文档/国际化与多语言/00-国际化与多语言总体设计.md §4.4
"""

from backend.common.translation.glossary import glossary_prompt_block, glossary_terms, load_glossary
from backend.common.translation.language import (
    BinaryLanguage,
    contains_chinese,
    detect_binary_language,
    detect_language,
    is_same_language,
    language_name,
    normalize_language,
)
from backend.common.translation.markdown import (
    MarkdownStructureError,
    mask_protected,
    restore_protected,
    split_long_text,
)
from backend.common.translation.translator import (
    DEFAULT_CHUNK_CHARS,
    ContentTranslator,
    TranslationError,
    TranslationOutcome,
    source_hash,
)

__all__ = [
    'DEFAULT_CHUNK_CHARS',
    'BinaryLanguage',
    'ContentTranslator',
    'MarkdownStructureError',
    'TranslationError',
    'TranslationOutcome',
    'contains_chinese',
    'detect_binary_language',
    'detect_language',
    'glossary_prompt_block',
    'glossary_terms',
    'is_same_language',
    'language_name',
    'load_glossary',
    'mask_protected',
    'normalize_language',
    'restore_protected',
    'source_hash',
    'split_long_text',
]
