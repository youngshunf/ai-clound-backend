"""hasn_growth 工商 API client（enterprise_info_client）纯函数单元测试（零 mock，无 DB）。

覆盖 GROWTH-QCC-4 读穿中台真打企查查（qcc）后发现并修复的两类响应形态解析 bug——
这是「真 qcc 返回中文键 / 单条 flat dict / 列表值法人名」被防御式解析漏掉、导致 ``extract_records``
返回空、读穿入池拿不到任何记录的回归守护：

- ``get_company_registration_info`` → **单个公司 flat dict**（无 list 容器、键全中文：``企业名称``/
  ``法定代表人``/``注册地址``/``所属地区``/``国标行业`` …）；
- ``get_company_by_query`` → 候选列表落在**中文容器键** ``企业信息``，候选里 ``法定代表人名称`` 是
  **列表值**（如 ``["王传福"]``）。

事实源: docs/hasn-node设计文档/MCP统一工具体系/实施/100-企查查平台MCP接入runbook(P7-E).md。
"""

from __future__ import annotations

from backend.app.hasn_growth.service.enterprise_info_client import (
    enterprise_record_to_structured,
    extract_records,
)

# 真 qcc get_company_registration_info 返回形态（单 flat dict，无 list 容器，键全中文）——节选真实键。
_QCC_REGISTRATION = {
    '企业名称': '小米科技有限责任公司',
    '法定代表人': '雷军',
    '统一社会信用代码': '91110108551385082Q',
    '注册地址': '北京市海淀区西二旗中路33号院6号楼6层006号',
    '所属地区': '北京市',
    '国标行业': '软件和信息技术服务业',
    '经营范围': '技术开发；技术服务……',
}

# 真 qcc get_company_by_query 返回形态（候选落中文容器键 企业信息；法人名是列表值）。
_QCC_SEARCH = {
    '匹配结果': '已找到 3 条',
    '检索关键字': '比亚迪',
    '企业信息': [
        {'企业名称': '比亚迪股份有限公司', '法定代表人名称': ['王传福'], '所属地区': '广东省'},
        {'企业名称': '比亚迪汽车有限公司', '法定代表人名称': ['王传福'], '所属地区': '广东省'},
    ],
}


class TestExtractRecordsQccShapes:
    """真 qcc 中文响应形态必须被防御式解析出记录（修复前全返 []）。"""

    def test_registration_single_flat_dict_falls_back_to_single_record(self) -> None:
        # 单条公司对象（无 list 容器）→ 单记录兜底：dict 自身即一条记录
        records = extract_records(_QCC_REGISTRATION)
        assert len(records) == 1
        assert records[0]['企业名称'] == '小米科技有限责任公司'

    def test_search_chinese_container_key(self) -> None:
        # 候选列表落在中文容器键「企业信息」→ 已知容器键探测命中
        records = extract_records(_QCC_SEARCH)
        assert len(records) == 2
        assert records[0]['企业名称'] == '比亚迪股份有限公司'

    def test_unknown_chinese_container_key_generic_scan(self) -> None:
        # 未在 _LIST_KEYS 里的未知中文容器键 → 通用扫描取首个 dict 列表兜底
        payload = {'摘要': '检索完成', '检索命中': [{'企业名称': '某科技公司'}]}
        records = extract_records(payload)
        assert len(records) == 1
        assert records[0]['企业名称'] == '某科技公司'

    def test_top_level_list_passthrough(self) -> None:
        records = extract_records([{'name': 'A'}, 'junk', {'name': 'B'}])
        assert [r['name'] for r in records] == ['A', 'B']

    def test_non_dict_non_list_returns_empty(self) -> None:
        assert extract_records('nope') == []
        assert extract_records(None) == []

    def test_dict_without_any_name_key_returns_empty(self) -> None:
        # 既无容器键、也无任何名称键 → 不误判为单记录
        assert extract_records({'foo': 'bar', 'count': 3}) == []

    def test_english_container_keys_still_work(self) -> None:
        # 向后兼容：天眼查/企查查英文容器键不被中文适配破坏
        assert len(extract_records({'result': [{'name': 'X'}]})) == 1
        assert len(extract_records({'data': {'items': [{'Name': 'Y'}]}})) == 1


class TestEnterpriseRecordToStructuredQcc:
    """qcc 中文键 → cleaner 认得的 structured_payload 字段映射（含列表值法人名）。"""

    def test_registration_chinese_keys_mapped(self) -> None:
        s = enterprise_record_to_structured(_QCC_REGISTRATION)
        assert s['company_name'] == '小米科技有限责任公司'
        assert s['contact_name'] == '雷军'  # 法定代表人 → contact_name
        assert s['region'] == '北京市'  # 所属地区 → region
        assert s['industry'] == '软件和信息技术服务业'  # 国标行业 → industry
        assert s['address'].startswith('北京市海淀区')  # 注册地址 → address

    def test_legal_rep_list_value_takes_first(self) -> None:
        # 法定代表人名称 是列表 ["王传福"] → _first 取首个非空字符串元素
        s = enterprise_record_to_structured(_QCC_SEARCH['企业信息'][0])
        assert s['company_name'] == '比亚迪股份有限公司'
        assert s['contact_name'] == '王传福'

    def test_english_keys_backward_compatible(self) -> None:
        s = enterprise_record_to_structured({'CompanyName': 'Acme', 'OperName': 'John', 'PhoneNumber': '13800138000'})
        assert s['company_name'] == 'Acme'
        assert s['contact_name'] == 'John'
        assert s['phones'] == ['13800138000']
