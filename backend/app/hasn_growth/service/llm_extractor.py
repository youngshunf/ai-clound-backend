"""线索结构化提取（方案 A）：firecrawl 只抓 markdown，结构化字段由后端调 new-api LLM 提取。

背景：自托管 firecrawl 2.10.3 的 LLM extract 模型名硬编码（gpt-4o-mini/gpt-4o），接不上
唤星 new-api 网关（只认 agnes-2.0-flash）。因此结构化提取下沉到后端，模型/prompt/成本全可控，
契合「firecrawl 不做业务判定」的设计边界。提取结果写入 ``CrawledItem.structured_payload``，
由 ``cleaner_service`` 二次校验（邮箱/电话归一化、准入判定），LLM 失败则退回 markdown 正则兜底。

事实源: docs/AI自动获客任务系统/08-采集引擎v3选型决策与众包线索池架构.md。
"""

from __future__ import annotations

import json

from typing import Any

import httpx

# schema/prompt 版本写进 raw_record，便于回溯（设计 05 §8 LLM 抽取风控）。
GROWTH_EXTRACT_SCHEMA_VERSION = 'lead_v1'
GROWTH_EXTRACT_PROMPT_VERSION = 'lead_extract_llm_v1'

# 单页正文上限，避免超长网页烧 token；超出截断（联系方式通常在页首/页尾，12k 足够覆盖）。
MAX_MARKDOWN_CHARS = 12000

_SYSTEM_PROMPT = (
    '你是企业线索信息提取助手。只提取网页正文中明确出现的公司与联系信息，'
    '严禁推断、编造或补全页面未出现的邮箱、电话或任何字段。必须返回单个 JSON 对象。'
)

_USER_TEMPLATE = """从以下网页正文中提取线索字段，只返回一个 JSON 对象（不要任何解释或 markdown）。

字段定义：
- company_name: 公司/机构名称
- contact_name: 联系人姓名
- emails: 邮箱数组（页面明确出现的，去掉明显的占位/示例邮箱）
- phones: 电话数组（手机/座机/400-800，保留原始格式）
- website: 官网地址
- address: 地址
- industry: 所属行业
- description: 一句话业务简介

规则：页面没有的字段留空字符串或空数组；绝对不要推断或编造缺失的邮箱和电话。

网页正文：
\"\"\"
{markdown}
\"\"\""""


def build_extract_messages(markdown: str) -> list[dict[str, str]]:
    """构造 chat/completions 的 messages（纯函数，便于单测）。"""
    body = (markdown or '').strip()[:MAX_MARKDOWN_CHARS]
    return [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': _USER_TEMPLATE.format(markdown=body)},
    ]


def parse_extract_response(content: str | None) -> dict[str, Any] | None:
    """把 LLM 返回的文本解析为归一化 structured_payload；无法解析返回 None（退回正则兜底）。

    容错处理：去掉 ```json``` code fence、截取首个 ``{`` 到末个 ``}``。
    """
    if not content:
        return None
    text = content.strip()
    if text.startswith('```'):
        # 去掉 ```json ... ``` 或 ``` ... ``` 围栏
        fenced = text.split('```')
        text = fenced[1] if len(fenced) >= 3 else text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
        text = text.strip()
    data = _loads_lenient(text)
    if not isinstance(data, dict):
        return None
    return _normalize_payload(data)


def _loads_lenient(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None


def _normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        'company_name': _as_text(data.get('company_name') or data.get('company')),
        'contact_name': _as_text(data.get('contact_name')),
        'emails': _as_text_list(data.get('emails') or data.get('email')),
        'phones': _as_text_list(data.get('phones') or data.get('phone')),
        'website': _as_text(data.get('website')),
        'address': _as_text(data.get('address')),
        'industry': _as_text(data.get('industry')),
        'description': _as_text(data.get('description')),
    }


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


class LeadLLMExtractor:
    """调用 OpenAI 兼容网关（new-api）做线索结构化提取。"""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def extract(self, markdown: str | None, *, source_url: str | None = None) -> dict[str, Any] | None:
        """从 markdown 提取结构化线索字段；任何失败返回 None（不抛，退回正则兜底）。

        返回 ``{structured_payload, llm_confidence, schema_version, prompt_version}`` 或 None。
        """
        if not markdown or not markdown.strip():
            return None
        payload = {
            'model': self.model,
            'messages': build_extract_messages(markdown),
            'temperature': 0,
            'max_tokens': 800,
            'response_format': {'type': 'json_object'},
            'stream': False,
        }
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            resp = await client.post(f'{self.base_url}/chat/completions', json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        content = (body.get('choices') or [{}])[0].get('message', {}).get('content')
        structured = parse_extract_response(content)
        if not structured:
            return None
        return {
            'structured_payload': structured,
            'llm_confidence': None,
            'schema_version': GROWTH_EXTRACT_SCHEMA_VERSION,
            'prompt_version': GROWTH_EXTRACT_PROMPT_VERSION,
            'source_url': source_url,
        }


def build_default_extractor() -> LeadLLMExtractor | None:
    """按 settings 构造默认提取器；未配置 base_url/api_key 则返回 None（跳过 LLM 提取）。"""
    from backend.core.conf import settings

    base_url = (settings.GROWTH_LLM_BASE_URL or '').strip()
    api_key = (settings.GROWTH_LLM_API_KEY or '').strip()
    if not base_url or not api_key:
        return None
    model = (settings.GROWTH_LLM_MODEL or 'agnes-2.0-flash').strip()
    return LeadLLMExtractor(base_url=base_url, api_key=api_key, model=model)
