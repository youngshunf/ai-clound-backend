"""hasn_growth LLM 结构化提取（llm_extractor）纯函数单元测试（零 mock，无网络）。

只覆盖确定性的纯函数：messages 构造、LLM 响应解析与字段归一化。``LeadLLMExtractor.extract``
的网络部分依赖真实 new-api 网关，由集成联调验证（不在此 mock HTTP）。

事实源: docs/AI自动获客任务系统/08-采集引擎v3选型决策与众包线索池架构.md。
"""

from __future__ import annotations

from backend.app.hasn_growth.service.llm_extractor import (
    MAX_MARKDOWN_CHARS,
    build_extract_messages,
    parse_extract_response,
)


class TestBuildExtractMessages:
    def test_includes_system_and_user_with_markdown(self) -> None:
        messages = build_extract_messages('广州XX光电 联系电话 13800138000')
        assert len(messages) == 2
        assert messages[0]['role'] == 'system'
        assert messages[1]['role'] == 'user'
        assert '13800138000' in messages[1]['content']
        # 系统/用户提示词必须显式约束「不得推断编造」，防 LLM 幻觉出页面没有的联系方式
        joined = messages[0]['content'] + messages[1]['content']
        assert '不要推断' in joined or '严禁推断' in joined

    def test_truncates_overlong_markdown(self) -> None:
        # 用模板里不出现的字符 Z 标记正文，精确断言正文被截断到上限（不耦合模板固有字母）
        huge = 'Z' * (MAX_MARKDOWN_CHARS + 5000)
        user = build_extract_messages(huge)[1]['content']
        assert user.count('Z') == MAX_MARKDOWN_CHARS

    def test_empty_markdown_still_builds(self) -> None:
        messages = build_extract_messages('')
        assert len(messages) == 2


class TestParseExtractResponse:
    def test_parses_plain_json(self) -> None:
        content = '{"company_name":"广州XX光电","emails":["sales@gzled.com"],"phones":["13800138000"]}'
        payload = parse_extract_response(content)
        assert payload is not None
        assert payload['company_name'] == '广州XX光电'
        assert payload['emails'] == ['sales@gzled.com']
        assert payload['phones'] == ['13800138000']

    def test_strips_code_fence(self) -> None:
        content = '```json\n{"company_name":"测试","phones":["020-87654321"]}\n```'
        payload = parse_extract_response(content)
        assert payload is not None
        assert payload['company_name'] == '测试'
        assert payload['phones'] == ['020-87654321']

    def test_extracts_json_embedded_in_text(self) -> None:
        content = '好的，提取结果如下：{"company_name":"嵌入测试","emails":[]}  以上。'
        payload = parse_extract_response(content)
        assert payload is not None
        assert payload['company_name'] == '嵌入测试'
        assert payload['emails'] == []

    def test_single_email_string_becomes_list(self) -> None:
        payload = parse_extract_response('{"email":"hr@company.cn","phone":"13900139000"}')
        assert payload is not None
        assert payload['emails'] == ['hr@company.cn']
        assert payload['phones'] == ['13900139000']

    def test_missing_fields_default_empty(self) -> None:
        payload = parse_extract_response('{"company_name":"只有公司名"}')
        assert payload is not None
        assert payload['emails'] == []
        assert payload['phones'] == []
        assert payload['website'] is None

    def test_invalid_returns_none(self) -> None:
        assert parse_extract_response('这不是 JSON，纯文本回复') is None
        assert parse_extract_response('') is None
        assert parse_extract_response(None) is None

    def test_non_object_json_returns_none(self) -> None:
        # 顶层是数组而非对象 → 无法作为 structured_payload
        assert parse_extract_response('[1, 2, 3]') is None

    def test_drops_blank_list_items(self) -> None:
        payload = parse_extract_response('{"emails":["a@b.com", "", "  ", "c@d.com"]}')
        assert payload is not None
        assert payload['emails'] == ['a@b.com', 'c@d.com']
