"""Translation Service for Marketplace Skills."""
import asyncio
import json
import re
from typing import Any, Literal

import httpx
from langdetect import detect, LangDetectException

from backend.common.log import log
from backend.core.conf import settings

LANGUAGES = {'en', 'zh'}
VERSION_TAG_RE = re.compile(r'^v?\d+(?:\.\d+){1,4}(?:[-+][0-9A-Za-z.-]+)?$')


class TranslationService:
    """Translation service for marketplace content"""

    def __init__(self):
        self._translation_cache = {}  # Simple in-memory cache

    def detect_language(self, text: str) -> Literal['en', 'zh', 'unknown']:
        """
        Detect language of text

        Args:
            text: Text to detect

        Returns:
            'en', 'zh', or 'unknown'
        """
        if not text or not text.strip():
            return 'unknown'

        try:
            # Use langdetect library
            lang = detect(text)

            # Map to our supported languages
            if lang in ['en']:
                return 'en'
            elif lang in ['zh-cn', 'zh-tw', 'zh']:
                return 'zh'
            else:
                # Fallback: check for Chinese characters
                if re.search(r'[一-鿿]', text):
                    return 'zh'
                return 'en'  # Default to English

        except LangDetectException:
            # Fallback: check for Chinese characters
            if re.search(r'[一-鿿]', text):
                return 'zh'
            return 'en'

    async def translate(
        self,
        text: str,
        source_lang: Literal['en', 'zh'] | None = None,
        target_lang: Literal['en', 'zh'] = 'zh'
    ) -> str:
        """
        Translate text using LLM

        Args:
            text: Text to translate
            source_lang: Source language (auto-detect if None)
            target_lang: Target language

        Returns:
            Translated text
        """
        if not text or not text.strip():
            return text

        # Auto-detect source language if not provided
        if source_lang is None:
            source_lang = self.detect_language(text)
            if source_lang == 'unknown':
                log.warning(f"Could not detect language for text: {text[:50]}...")
                return text

        # Skip translation if source and target are the same
        if source_lang == target_lang:
            return text

        # Check cache
        cache_key = f"{source_lang}:{target_lang}:{hash(text)}"
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]

        # Translate using LLM
        try:
            translated = await self._translate_with_llm(text, source_lang, target_lang)

            # Cache the result
            self._translation_cache[cache_key] = translated

            return translated

        except Exception as e:
            log.error(f"Translation failed: {e}")
            return text  # Return original text on error

    async def _translate_with_llm(
        self,
        text: str,
        source_lang: Literal['en', 'zh'],
        target_lang: Literal['en', 'zh']
    ) -> str:
        """
        Translate text using LLM API directly

        Args:
            text: Text to translate
            source_lang: Source language
            target_lang: Target language

        Returns:
            Translated text
        """
        # Prepare prompt
        lang_names = {'en': 'English', 'zh': 'Chinese'}
        source_name = lang_names[source_lang]
        target_name = lang_names[target_lang]

        prompt = f"""Translate the following {source_name} text to {target_name}.
Keep the translation natural and concise. Only return the translated text, no explanations.

Text to translate:
{text}

Translation:"""

        return await self._complete_chat(
            [
                {"role": "system", "content": "You are a professional translator."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
        )

    async def _complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        *,
        response_format: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        """Call the configured chat completion API and return message content."""
        # Get LLM configuration
        api_base = getattr(settings, 'LLM_API_BASE_URL', 'http://127.0.0.1:3180')
        if not api_base.endswith('/v1'):
            api_base = f"{api_base}/v1"
        api_key = getattr(settings, 'LLM_API_KEY', 'sk-system-translation')
        primary_model = getattr(settings, 'TRANSLATION_MODEL', 'gpt-4o-mini')
        fallback_model = getattr(settings, 'TRANSLATION_FALLBACK_MODEL', 'gpt-5.5')
        models = [primary_model]
        if fallback_model and fallback_model != primary_model:
            models.append(fallback_model)

        base_payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            # 用流式：部分 new-api 网关渠道在 stream=false 时只回空 chunk
            # (completion_tokens=0)，stream=true 才出内容；解析层已支持 SSE
            # (_parse_sse_chat_response)。stream=true 对合规网关也通用、无副作用。
            "stream": True,
        }

        # Reasoning models (e.g. qwen3.7-max) emit long reasoning_content and need a
        # generous read timeout, especially for multi-item batch requests.
        request_timeout = timeout if timeout is not None else float(getattr(settings, 'TRANSLATION_TIMEOUT', 120.0))
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            for model_index, model in enumerate(models):
                payload = {**base_payload, "model": model}
                if response_format:
                    payload["response_format"] = response_format
                attempts = 3
                for attempt in range(attempts):
                    translated = await self._post_chat_completion(client, api_base, api_key, payload, model, attempt)
                    if translated:
                        return translated
                    # Back off before retrying transient gateway errors (503/429/etc.).
                    if attempt < attempts - 1:
                        await asyncio.sleep(2.0 * (attempt + 1))

                if model_index > 0 and response_format:
                    relaxed_payload = {**base_payload, "model": model}
                    translated = await self._post_chat_completion(
                        client,
                        api_base,
                        api_key,
                        relaxed_payload,
                        model,
                        attempts,
                    )
                    if translated:
                        return translated

            raise Exception("Invalid LLM response: empty content")

    async def _post_chat_completion(
        self,
        client: httpx.AsyncClient,
        api_base: str,
        api_key: str,
        payload: dict[str, Any],
        model: str,
        attempt: int,
    ) -> str | None:
        response = await client.post(
            f"{api_base}/chat/completions",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )

        if response.status_code != 200:
            log.error(f"LLM API error: {response.status_code} - {response.text}")
            # Transient gateway errors: let the caller retry with backoff.
            if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                return None
            raise Exception(f"LLM API error: {response.status_code}")

        content_type = response.headers.get('content-type', '')
        result = (
            self._parse_sse_chat_response(response.text)
            if 'text/event-stream' in content_type
            else response.json()
        )

        translated = self._extract_chat_content(result)
        if translated:
            return translated

        log.warning(f"LLM API returned empty content on model {model} attempt {attempt + 1}: {result}")
        return None

    @staticmethod
    def _extract_chat_content(result: dict[str, Any]) -> str | None:
        choices = result.get('choices')
        if not isinstance(choices, list) or not choices:
            return None
        chunks: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get('message')
            if isinstance(message, dict) and message.get('content'):
                chunks.append(str(message['content']))
            delta = choice.get('delta')
            if isinstance(delta, dict) and delta.get('content'):
                chunks.append(str(delta['content']))
        content = ''.join(chunks).strip()
        return content or None

    @classmethod
    def _parse_sse_chat_response(cls, text: str) -> dict[str, Any]:
        choices: list[dict[str, Any]] = []
        usage: dict[str, Any] | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload = line.removeprefix('data:').strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get('usage'):
                usage = chunk['usage']
            for choice in chunk.get('choices') or []:
                if isinstance(choice, dict):
                    choices.append(choice)
        return {'choices': choices, 'usage': usage}

    async def translate_skill_metadata(
        self,
        name: str | None = None,
        description: str | None = None,
        source_lang: Literal['en', 'zh'] | None = None,
        tag_hints: list[str] | str | None = None,
        categories: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Normalize skill metadata to bilingual name, description, and tags.

        The LLM receives the original text and decides the source language. This
        avoids brittle local language detection for short names and mixed metadata.

        Args:
            name: Skill name
            description: Skill description
            source_lang: Optional source language hint
            tag_hints: Optional raw tags from package metadata or upstream API

        Returns:
            Dict with keys: name_en, name_zh, description_en, description_zh,
            source_language, target_language, tags_en, tags_zh.
        """
        hints = self.normalize_tag_list(tag_hints)
        cat_sig = tuple(sorted(str(c.get('slug') or '') for c in (categories or [])))
        cache_key = f"skill-metadata:{hash((name or '', description or '', tuple(hints), source_lang or '', cat_sig))}"
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]

        try:
            raw = await self._complete_chat(
                self._metadata_messages(
                    name=name, description=description, tag_hints=hints,
                    source_lang=source_lang, categories=categories,
                ),
                max_tokens=1400,
                response_format={'type': 'json_object'},
            )
            normalized = self._coerce_metadata_response(
                raw=raw,
                name=name,
                description=description,
                tag_hints=hints,
                source_lang=source_lang,
                categories=categories,
            )
        except Exception as exc:
            log.error(f"Skill metadata translation failed: {exc}")
            normalized = self._fallback_skill_metadata(
                name=name,
                description=description,
                tag_hints=hints,
                source_lang=source_lang,
            )

        self._translation_cache[cache_key] = normalized
        return normalized

    def _metadata_messages(
        self,
        *,
        name: str | None,
        description: str | None,
        tag_hints: list[str],
        source_lang: Literal['en', 'zh'] | None,
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        payload = {
            'name': name or '',
            'description': description or '',
            'tag_hints': tag_hints,
            'source_language_hint': source_lang,
        }
        category_block = self._category_prompt_block(categories)
        keys = (
            'source_language, target_language, name_en, name_zh, '
            'description_en, description_zh, tags_en, tags_zh, emoji'
        )
        if category_block:
            keys += ', category'
        return [
            {
                'role': 'system',
                'content': (
                    'You normalize AI skill marketplace metadata. '
                    'Return strict JSON only, with no markdown or explanations.'
                ),
            },
            {
                'role': 'user',
                'content': (
                    'Normalize this skill metadata for a bilingual Chinese/English marketplace.\n'
                    'Rules:\n'
                    '- Decide source_language from the original name and description. Use only "zh" or "en".\n'
                    '- Set target_language to the opposite language.\n'
                    '- Put the original wording in the field matching its language when possible.\n'
                    '- Translate naturally and concisely for the other language.\n'
                    '- Generate 2 to 5 concise capability/domain tags in both English and Chinese.\n'
                    '- Ignore version numbers, "latest", license names, author names, and repository names as tags.\n'
                    '- tag_hints are hints only; do not copy bad hints blindly.\n'
                    '- Pick exactly ONE representative emoji for the skill.\n'
                    + category_block
                    + f'Return exactly these keys: {keys}.\n\n'
                    + f'Original metadata JSON:\n{json.dumps(payload, ensure_ascii=False)}'
                ),
            },
        ]

    def _coerce_metadata_response(
        self,
        *,
        raw: str,
        name: str | None,
        description: str | None,
        tag_hints: list[str],
        source_lang: Literal['en', 'zh'] | None,
        categories: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        data = self._parse_json_object(raw)
        return self._coerce_metadata_dict(
            data=data,
            name=name,
            description=description,
            tag_hints=tag_hints,
            source_lang=source_lang,
            categories=categories,
        )

    def _coerce_metadata_dict(
        self,
        *,
        data: dict[str, Any],
        name: str | None,
        description: str | None,
        tag_hints: list[str],
        source_lang: Literal['en', 'zh'] | None,
        categories: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Coerce one already-parsed LLM metadata object into the canonical shape."""
        fallback = self._fallback_skill_metadata(
            name=name,
            description=description,
            tag_hints=tag_hints,
            source_lang=source_lang,
        )

        source_language = self._normalize_language(data.get('source_language')) or fallback['source_language']
        target_language = self._normalize_language(data.get('target_language'))
        if target_language not in LANGUAGES or target_language == source_language:
            target_language = 'zh' if source_language == 'en' else 'en'

        tags_en = self.normalize_tag_list(data.get('tags_en'))
        tags_zh = self.normalize_tag_list(data.get('tags_zh'))
        if len(tags_en) < 2:
            tags_en = fallback['tags_en']
        if len(tags_zh) < 2:
            tags_zh = fallback['tags_zh']

        return {
            'source_language': source_language,
            'target_language': target_language,
            'name_en': self._clean_text(data.get('name_en')) or fallback['name_en'],
            'name_zh': self._clean_text(data.get('name_zh')) or fallback['name_zh'],
            'description_en': self._clean_text(data.get('description_en')) or fallback['description_en'],
            'description_zh': self._clean_text(data.get('description_zh')) or fallback['description_zh'],
            'tags_en': tags_en[:5],
            'tags_zh': tags_zh[:5],
            'emoji': self._clean_emoji(data.get('emoji')) or fallback['emoji'],
            'category': self._coerce_category(data.get('category'), categories),
        }

    def _fallback_skill_metadata(
        self,
        *,
        name: str | None,
        description: str | None,
        tag_hints: list[str],
        source_lang: Literal['en', 'zh'] | None,
    ) -> dict[str, Any]:
        source_language = source_lang if source_lang in LANGUAGES else self._detect_language_by_script(name, description)
        target_language = 'zh' if source_language == 'en' else 'en'
        fallback_tags = self._fallback_tags(name=name, description=description, tag_hints=tag_hints)
        zh_tags = [tag for tag in fallback_tags if self._contains_chinese(tag)]
        en_tags = [tag for tag in fallback_tags if not self._contains_chinese(tag)]

        if source_language == 'zh' and not zh_tags:
            zh_tags = fallback_tags[:5]
        if source_language == 'en' and not en_tags:
            en_tags = fallback_tags[:5]

        return {
            'source_language': source_language,
            'target_language': target_language,
            'name_en': name if source_language == 'en' else None,
            'name_zh': name if source_language == 'zh' else None,
            'description_en': description if source_language == 'en' else None,
            'description_zh': description if source_language == 'zh' else None,
            'tags_en': en_tags[:5],
            'tags_zh': zh_tags[:5],
            'emoji': None,
            'category': None,
        }

    @staticmethod
    def _category_prompt_block(categories: list[dict[str, Any]] | None) -> str:
        """Prompt fragment listing allowed categories; '' when none provided."""
        if not categories:
            return ''
        lines: list[str] = []
        for cat in categories:
            slug = str(cat.get('slug') or '').strip()
            if not slug:
                continue
            label = str(cat.get('name') or cat.get('name_zh') or cat.get('name_en') or '').strip()
            lines.append(f'  - {slug}: {label}' if label else f'  - {slug}')
        if not lines:
            return ''
        return (
            '- Classify the skill into exactly ONE category and return its slug in a "category" field. '
            'Choose the single best fit from this list and use the slug EXACTLY as written; '
            'if nothing fits well use "other" when present.\n'
            'Available categories (slug: label):\n' + '\n'.join(lines) + '\n'
        )

    @staticmethod
    def _coerce_category(value: Any, categories: list[dict[str, Any]] | None) -> str | None:
        """Validate the LLM-picked category slug against the allowed set."""
        if not categories:
            return None
        slugs = {str(cat.get('slug') or '').strip() for cat in categories if cat.get('slug')}
        slugs.discard('')
        candidate = str(value or '').strip()
        if candidate in slugs:
            return candidate
        lower_map = {slug.lower(): slug for slug in slugs}
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
        return 'other' if 'other' in slugs else None

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        content = raw.strip()
        if content.startswith('```'):
            content = re.sub(r'\A```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```\Z', '', content)
        start = content.find('{')
        end = content.rfind('}')
        if start == -1 or end == -1 or end < start:
            raise ValueError('LLM response is not a JSON object')
        data = json.loads(content[start:end + 1])
        if not isinstance(data, dict):
            raise TypeError('LLM response JSON must be an object')
        return data

    @staticmethod
    def _normalize_language(value: Any) -> Literal['en', 'zh'] | None:
        normalized = str(value or '').strip().lower()
        if normalized in {'zh', 'zh-cn', 'zh-tw', 'chinese', '中文'}:
            return 'zh'
        if normalized in {'en', 'english', '英文'}:
            return 'en'
        return None

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _clean_emoji(value: Any) -> str | None:
        """Normalize an LLM-provided emoji to a short single glyph (column is varchar(20))."""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # Models occasionally append a label ("🛃 Customs"); keep only the leading token.
        first = text.split()[0] if ' ' in text else text
        return first[:20] or None

    @classmethod
    def normalize_tag_list(cls, tags: Any) -> list[str]:
        if tags is None:
            values: list[Any] = []
        elif isinstance(tags, str):
            try:
                parsed = json.loads(tags)
                values = parsed if isinstance(parsed, list) else [tags]
            except json.JSONDecodeError:
                values = re.split(r'[,，\n]', tags)
        elif isinstance(tags, dict):
            values = list(tags.keys())
        elif isinstance(tags, list | tuple | set):
            values = list(tags)
        else:
            values = [tags]

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = str(value).strip().strip('"\'')
            key = tag.lower()
            if not tag or key in seen or cls._is_bad_tag(tag):
                continue
            seen.add(key)
            normalized.append(tag)
            if len(normalized) >= 5:
                break
        return normalized

    @classmethod
    def _fallback_tags(cls, *, name: str | None, description: str | None, tag_hints: list[str]) -> list[str]:
        tags = cls.normalize_tag_list(tag_hints)
        if len(tags) >= 2:
            return tags

        text = f'{name or ""} {description or ""}'
        if cls._contains_chinese(text):
            candidates = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
        else:
            candidates = re.findall(r'[A-Za-z][A-Za-z0-9-]{2,24}', text.lower())

        for candidate in candidates:
            if candidate.lower() in {'the', 'and', 'with', 'for', 'use', 'when', 'skill', 'helper'}:
                continue
            tags.extend(cls.normalize_tag_list([candidate]))
            if len(tags) >= 5:
                break

        if not tags:
            tags = ['skill']
        if len(tags) == 1:
            tags.append('assistant' if not cls._contains_chinese(tags[0]) else '助手')
        return tags[:5]

    @staticmethod
    def _is_bad_tag(tag: str) -> bool:
        normalized = tag.strip().lower()
        return (
            normalized in {'latest', 'version', 'versions', 'license', 'author'}
            or VERSION_TAG_RE.match(normalized) is not None
        )

    @staticmethod
    def _contains_chinese(text: str | None) -> bool:
        return bool(text and re.search(r'[\u4e00-\u9fff]', text))

    @classmethod
    def _detect_language_by_script(cls, name: str | None, description: str | None) -> Literal['en', 'zh']:
        return 'zh' if cls._contains_chinese(f'{name or ""} {description or ""}') else 'en'

    @classmethod
    def tags_json(cls, tags: Any) -> str:
        return json.dumps(cls.normalize_tag_list(tags), ensure_ascii=False)

    async def batch_translate_skill_metadata(
        self,
        items: list[dict[str, Any]],
        *,
        batch_size: int | None = None,
        concurrency: int = 1,
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Normalize a list of skill metadata in batched LLM calls.

        Each input item is a dict with optional keys: ``name``, ``description``,
        ``tag_hints``, ``source_lang``. Language detection, bilingual translation,
        bilingual tags, and a representative emoji are all produced in ONE request
        per batch (default 10 items).

        ``concurrency`` controls how many batch requests run in parallel (useful for
        backfilling many rows; keep at 1 for inline sync to avoid hammering the gateway).

        Returns a list aligned to the input order. Each entry has keys:
        source_language, target_language, name_en, name_zh, description_en,
        description_zh, tags_en, tags_zh, emoji.
        """
        if not items:
            return []

        size = batch_size or int(getattr(settings, 'TRANSLATION_BATCH_SIZE', 10) or 10)
        size = max(1, size)
        chunks = [items[start:start + size] for start in range(0, len(items), size)]

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_chunk(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._translate_one_batch(chunk, categories=categories)

        chunk_results = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks))
        flattened: list[dict[str, Any]] = []
        for chunk_result in chunk_results:
            flattened.extend(chunk_result)
        return flattened

    async def _translate_one_batch(
        self,
        chunk: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_inputs = [
            {
                'name': self._clean_text(item.get('name')),
                'description': self._clean_text(item.get('description')),
                'tag_hints': self.normalize_tag_list(item.get('tag_hints')),
                'source_lang': self._normalize_language(item.get('source_lang')),
            }
            for item in chunk
        ]
        try:
            raw = await self._complete_chat(
                self._batch_metadata_messages(normalized_inputs, categories=categories),
                max_tokens=min(12000, 800 * len(chunk) + 800),
                response_format={'type': 'json_object'},
                timeout=min(300.0, 45.0 + 20.0 * len(chunk)),
            )
            return self._coerce_batch_response(raw=raw, inputs=normalized_inputs, categories=categories)
        except Exception as exc:
            log.error(f"Batch skill metadata translation failed ({len(chunk)} items): {exc}")
            return [
                self._fallback_skill_metadata(
                    name=item['name'],
                    description=item['description'],
                    tag_hints=item['tag_hints'],
                    source_lang=item['source_lang'],
                )
                for item in normalized_inputs
            ]

    def _batch_metadata_messages(
        self,
        items: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        payload = [
            {
                'index': i,
                'name': item.get('name') or '',
                'description': item.get('description') or '',
                'tag_hints': item.get('tag_hints') or [],
                'source_language_hint': item.get('source_lang'),
            }
            for i, item in enumerate(items)
        ]
        category_block = self._category_prompt_block(categories)
        element_keys = (
            'index, source_language, target_language, name_en, name_zh, '
            'description_en, description_zh, tags_en, tags_zh, emoji'
        )
        if category_block:
            element_keys += ', category'
        return [
            {
                'role': 'system',
                'content': (
                    'You normalize AI skill marketplace metadata. '
                    'Return strict JSON only, with no markdown or explanations.'
                ),
            },
            {
                'role': 'user',
                'content': (
                    'Normalize each skill below for a bilingual Chinese/English marketplace.\n'
                    'Rules per item:\n'
                    '- Decide source_language from the original name and description. Use only "zh" or "en".\n'
                    '- Set target_language to the opposite language.\n'
                    '- Put the original wording in the field matching its language; translate naturally '
                    'and concisely for the other language.\n'
                    '- Generate 2 to 5 concise capability/domain tags in BOTH English and Chinese.\n'
                    '- Ignore version numbers, "latest", license names, author names, and repository names as tags.\n'
                    '- tag_hints are hints only; do not copy bad hints blindly.\n'
                    '- Pick exactly ONE representative emoji for the skill.\n'
                    + category_block
                    + 'Return strict JSON shaped exactly as {"items": [ ... ]}. '
                    + f'Each element must include: {element_keys}. '
                    + 'Keep the same index as the input item.\n\n'
                    + f'Input items JSON:\n{json.dumps(payload, ensure_ascii=False)}'
                ),
            },
        ]

    def _coerce_batch_response(
        self,
        *,
        raw: str,
        inputs: list[dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        parsed = self._parse_json_object(raw)
        raw_items = parsed.get('items')
        by_index: dict[int, dict[str, Any]] = {}
        if isinstance(raw_items, list):
            for pos, entry in enumerate(raw_items):
                if not isinstance(entry, dict):
                    continue
                idx = entry.get('index')
                key = idx if isinstance(idx, int) and 0 <= idx < len(inputs) else pos
                by_index.setdefault(key, entry)

        results: list[dict[str, Any]] = []
        for i, item in enumerate(inputs):
            data = by_index.get(i)
            if data is None:
                log.warning(f"Batch translation missing item index {i}; using fallback")
                results.append(
                    self._fallback_skill_metadata(
                        name=item['name'],
                        description=item['description'],
                        tag_hints=item['tag_hints'],
                        source_lang=item['source_lang'],
                    )
                )
                continue
            results.append(
                self._coerce_metadata_dict(
                    data=data,
                    name=item['name'],
                    description=item['description'],
                    tag_hints=item['tag_hints'],
                    source_lang=item['source_lang'],
                    categories=categories,
                )
            )
        return results

    async def batch_translate(
        self,
        texts: list[str],
        source_lang: Literal['en', 'zh'],
        target_lang: Literal['en', 'zh']
    ) -> list[str]:
        """
        Batch translate multiple texts (optimized for efficiency)

        Args:
            texts: List of texts to translate
            source_lang: Source language
            target_lang: Target language

        Returns:
            List of translated texts
        """
        if not texts:
            return []

        # For now, translate one by one
        # TODO: Optimize by batching multiple texts in one LLM call
        results = []
        for text in texts:
            translated = await self.translate(text, source_lang, target_lang)
            results.append(translated)

        return results


# Singleton instance
translation_service = TranslationService()
