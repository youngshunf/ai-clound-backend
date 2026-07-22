import asyncio

import pytest

from backend.common.i18n import I18n
from backend.common.queue import batch_dequeue
from backend.common.schema import CustomEmailStr, ser_string
from backend.core.conf import settings


def test_schema_helpers_preserve_string_contract() -> None:
    """邮箱与字符串序列化辅助函数始终返回其声明类型。"""
    assert CustomEmailStr._validate('owner@example.com') == 'owner@example.com'
    assert ser_string(0) == '0'
    assert ser_string(None) is None

    with pytest.raises(ValueError, match='邮箱不能为空'):
        CustomEmailStr._validate('')


def test_i18n_returns_fallback_for_non_text_translation() -> None:
    """翻译节点不是文本时必须回退为字符串，而不是泄漏字典。"""
    translator = object.__new__(I18n)
    translator.locales = {
        settings.I18N_DEFAULT_LANGUAGE: {
            'greeting': '你好，{name}',
            'group': {'child': '子节点'},
        },
    }

    assert translator.t('greeting', name='唤星') == '你好，唤星'
    assert translator.t('group', default='默认文案') == '默认文案'
    assert translator.t('missing') == 'missing'


@pytest.mark.asyncio
async def test_batch_dequeue_returns_typed_queue_items() -> None:
    """批量出队保持队列元素类型与顺序。"""
    queue: asyncio.Queue[int] = asyncio.Queue()
    await queue.put(1)
    await queue.put(2)

    assert await batch_dequeue(queue, max_items=2, timeout=0.1) == [1, 2]
