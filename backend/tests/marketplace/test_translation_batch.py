"""Unit tests for the batched skill-metadata translation coercion (no LLM calls)."""
from backend.app.marketplace.service.translation_service import TranslationService


def test_clean_emoji_strips_label_and_caps_length():
    svc = TranslationService()
    assert svc._clean_emoji('🛃') == '🛃'
    assert svc._clean_emoji('  🗑️  ') == '🗑️'
    # Models sometimes append a label; keep only the leading token.
    assert svc._clean_emoji('🛃 Customs') == '🛃'
    assert svc._clean_emoji('') is None
    assert svc._clean_emoji(None) is None


def test_coerce_metadata_dict_includes_emoji_and_bilingual_fields():
    svc = TranslationService()
    data = {
        'source_language': 'zh',
        'target_language': 'en',
        'name_en': 'Customs Data Analysis Expert',
        'name_zh': '海关数据分析专家',
        'description_en': 'Customs query service.',
        'description_zh': '提供海关数据查询服务。',
        'tags_en': ['Customs Data', 'Trade Analysis'],
        'tags_zh': ['海关数据', '贸易分析'],
        'emoji': '🛃',
    }
    out = svc._coerce_metadata_dict(
        data=data, name='海关数据分析专家', description='提供海关数据查询服务。',
        tag_hints=[], source_lang='zh',
    )
    assert out['source_language'] == 'zh'
    assert out['target_language'] == 'en'
    assert out['name_en'] == 'Customs Data Analysis Expert'
    assert out['name_zh'] == '海关数据分析专家'
    assert out['emoji'] == '🛃'
    assert out['tags_en'] == ['Customs Data', 'Trade Analysis']
    assert out['tags_zh'] == ['海关数据', '贸易分析']


def test_coerce_batch_response_maps_by_index_and_falls_back_for_missing():
    svc = TranslationService()
    inputs = [
        {'name': 'A', 'description': 'desc a', 'tag_hints': [], 'source_lang': 'en'},
        {'name': 'B', 'description': 'desc b', 'tag_hints': [], 'source_lang': 'en'},
    ]
    # LLM returned items out of order, and omitted index 1 entirely.
    raw = (
        '{"items": [{"index": 0, "source_language": "en", "target_language": "zh", '
        '"name_en": "A", "name_zh": "甲", "description_en": "desc a", "description_zh": "描述 a", '
        '"tags_en": ["alpha", "tool"], "tags_zh": ["甲", "工具"], "emoji": "🅰️"}]}'
    )
    results = svc._coerce_batch_response(raw=raw, inputs=inputs)
    assert len(results) == 2
    # index 0 mapped from the LLM payload
    assert results[0]['name_zh'] == '甲'
    assert results[0]['emoji'] == '🅰️'
    # index 1 missing -> fallback keeps original side, leaves the other untranslated
    assert results[1]['name_en'] == 'B'
    assert results[1]['name_zh'] is None
    assert results[1]['emoji'] is None


def test_retryable_status_codes_documented_in_post_handler():
    # Guard against accidentally removing transient-error handling. Transport (retry on
    # transient gateway errors) now lives in the unified backend.common.llm client.
    from backend.common.llm.client import _TRANSIENT_STATUS

    for code in (429, 503, 500, 502, 504):
        assert code in _TRANSIENT_STATUS
