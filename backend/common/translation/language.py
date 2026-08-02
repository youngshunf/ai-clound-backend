"""语言检测与语言码归一（公共件）。

两套 API，别混用：

- :func:`detect_binary_language` —— 只回 ``en``/``zh``/``unknown``。这是 marketplace
  技能元数据双语化的历史口径（英中双语市场），抽公共件时**原样搬过来**，行为不得改动。
- :func:`detect_language` —— 回归一化后的多语言码（``zh``/``zh-TW``/``en``/``ja``/``ko``
  /``es``/``fr``/``de`` ...）。轨道 B 的用户内容翻译用这个：帖子可能是任何语言，
  二元判定会把日文帖子判成 ``en`` 然后「翻译成英文」时原地打转。
"""

from __future__ import annotations

import re

from typing import Final, Literal

from langdetect import LangDetectException, detect

# 汉字区段（含扩展 A）。langdetect 对短文本会直接抛异常，靠这个兜底。
_HAN_RE: Final = re.compile(r'[㐀-䶿一-鿿]')
# 日文假名：有假名一定是日文，不会是中文（汉字则两者共用，不能作判据）。
_KANA_RE: Final = re.compile(r'[぀-ゟ゠-ヿ]')
# 谚文：韩文判据。
_HANGUL_RE: Final = re.compile(r'[가-힯ᄀ-ᇿ]')
# 繁体中文特征字（简繁分野用，命中即倾向 zh-TW）。
_TRADITIONAL_RE: Final = re.compile(r'[繁體臺灣為與這樣個門開關閉髮舊爾東馬車讀寫國學過發們時間]')

BinaryLanguage = Literal['en', 'zh', 'unknown']

# langdetect 的输出码 → 我们的归一码。未列出的原样小写透传。
_LANGDETECT_ALIAS: Final[dict[str, str]] = {
    'zh-cn': 'zh',
    'zh-tw': 'zh-TW',
}

# 入参语言码归一：大小写/下划线/区域后缀差异统一成一个写法。
_CODE_ALIAS: Final[dict[str, str]] = {
    'zh': 'zh',
    'zh-cn': 'zh',
    'zh-hans': 'zh',
    'zh-sg': 'zh',
    'cmn': 'zh',
    'chinese': 'zh',
    '中文': 'zh',
    'zh-tw': 'zh-TW',
    'zh-hk': 'zh-TW',
    'zh-hant': 'zh-TW',
    'en': 'en',
    'en-us': 'en',
    'en-gb': 'en',
    'english': 'en',
    'ja': 'ja',
    'ja-jp': 'ja',
    'jp': 'ja',
    'ko': 'ko',
    'ko-kr': 'ko',
    'es': 'es',
    'fr': 'fr',
    'de': 'de',
}

# 各语言码的自然语言名，进 prompt 用（模型对 "Japanese" 的理解好过 "ja"）。
_LANGUAGE_NAMES: Final[dict[str, str]] = {
    'zh': 'Simplified Chinese',
    'zh-TW': 'Traditional Chinese',
    'en': 'English',
    'ja': 'Japanese',
    'ko': 'Korean',
    'es': 'Spanish',
    'fr': 'French',
    'de': 'German',
}


def normalize_language(code: str | None) -> str:
    """把外部传入的语言码归一成本系统写法；无法识别时原样小写返回。

    ``zh-CN``/``zh_Hans``/``Chinese`` 都归一到 ``zh``；``zh-TW``/``zh-HK`` 归一到 ``zh-TW``。
    """
    raw = (code or '').strip().replace('_', '-')
    if not raw:
        return ''
    return _CODE_ALIAS.get(raw.lower(), raw.lower())


def language_name(code: str) -> str:
    """语言码 → 进 prompt 的自然语言名；未知码回落语言码本身。"""
    normalized = normalize_language(code)
    return _LANGUAGE_NAMES.get(normalized, normalized or code)


def contains_chinese(text: str | None) -> bool:
    """文本是否含汉字。"""
    return bool(text and _HAN_RE.search(text))


def detect_binary_language(text: str) -> BinaryLanguage:
    """英中二元语言检测（marketplace 技能元数据历史口径，行为不得改动）。

    检测不出时按汉字兜底判 ``zh``，否则默认 ``en``；空串回 ``unknown``。
    """
    if not text or not text.strip():
        return 'unknown'

    try:
        lang = detect(text)
    except LangDetectException:
        return 'zh' if contains_chinese(text) else 'en'

    if lang == 'en':
        return 'en'
    if lang in ('zh-cn', 'zh-tw', 'zh'):
        return 'zh'
    if contains_chinese(text):
        return 'zh'
    return 'en'


def detect_language(text: str) -> str:
    """多语言检测，返回归一化语言码；判不出返回 ``''``（**不猜**）。

    判不出时返回空串而不是随便给个 ``en``：调用方据此决定「不翻」或「让用户指定」，
    比拿一个瞎猜的源语言去调 LLM 更诚实（零 fake：宁可说不知道，不造一个假答案）。

    脚本判据优先于 langdetect：假名/谚文出现即可定案，且对短文本远比统计模型可靠
    （langdetect 对一两句话经常直接抛异常）。
    """
    if not text or not text.strip():
        return ''

    # 假名/谚文是硬判据：出现即定案，不必再问统计模型。
    if _KANA_RE.search(text):
        return 'ja'
    if _HANGUL_RE.search(text):
        return 'ko'

    try:
        raw = detect(text)
    except LangDetectException:
        # 统计模型放弃时，只有汉字这一条兜底判据；其余一律认「不知道」。
        return _han_variant(text) if contains_chinese(text) else ''

    normalized = _LANGDETECT_ALIAS.get(raw.lower(), raw.lower())
    if normalized in ('zh', 'zh-TW'):
        # langdetect 的简繁判定不稳，用特征字自己再判一次。
        return _han_variant(text)
    return normalized


def _han_variant(text: str) -> str:
    """含汉字文本的简繁分野。命中繁体特征字判 ``zh-TW``，否则 ``zh``。"""
    return 'zh-TW' if _TRADITIONAL_RE.search(text) else 'zh'


def is_same_language(a: str | None, b: str | None) -> bool:
    """两个语言码归一后是否同一语言（用于「源=目标则跳过翻译」判定）。"""
    left = normalize_language(a)
    right = normalize_language(b)
    return bool(left) and left == right
