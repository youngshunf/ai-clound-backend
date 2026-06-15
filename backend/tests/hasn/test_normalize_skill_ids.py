"""hasn_agents.skills 归一化 _normalize_skill_ids 形态兼容（纯函数，无 DB）。

重点回归：旧 onboarding 误写的包裹形态 {'enabled': [<skill_id>...]}——真实成员藏在 enabled
的值（list）里，而非 key。若不识别，profile 端点会把 ['enabled'] 当成唯一技能下发，
导致该 Agent 真实技能全不 provision（生产 2 个旧「全能助理」分身命中本缺陷）。
"""

from __future__ import annotations

from backend.app.hasn.api.v1.agent.hasn_agent_profile import _normalize_skill_ids


def test_none_and_empty() -> None:
    assert _normalize_skill_ids(None) == []
    assert _normalize_skill_ids([]) == []
    assert _normalize_skill_ids({}) == []


def test_list_of_strings() -> None:
    assert _normalize_skill_ids(['huanxing/x', ' huanxing/y ', '']) == ['huanxing/x', 'huanxing/y']


def test_list_of_dicts() -> None:
    skills = [{'skill_id': 'huanxing/x'}, {'id': 'huanxing/y'}, {'noop': 1}]
    assert _normalize_skill_ids(skills) == ['huanxing/x', 'huanxing/y']


def test_dict_skill_id_to_version_keys_are_ids() -> None:
    # {skill_id: version} 形态：key 即 skill_id（value 是版本号字符串，不是 list）。
    assert sorted(_normalize_skill_ids({'huanxing/x': '1.0', 'huanxing/y': '2.0'})) == [
        'huanxing/x',
        'huanxing/y',
    ]


def test_enabled_wrapper_extracts_inner_list() -> None:
    # 坏形态 {'enabled': [...]}：成员在 value（list）里，必须取出，绝不能返回 ['enabled']。
    wrapped = {
        'enabled': [
            'huanxing/productivity/translator-pro',
            'huanxing/productivity/email-writer',
            'huanxing/utility/weather',
        ]
    }
    assert _normalize_skill_ids(wrapped) == [
        'huanxing/productivity/translator-pro',
        'huanxing/productivity/email-writer',
        'huanxing/utility/weather',
    ]
    # 绝不能退化成把 wrapper key 当技能。
    assert 'enabled' not in _normalize_skill_ids(wrapped)


def test_enabled_wrapper_with_dict_members() -> None:
    # enabled 内部也兼容 list[{skill_id}]（递归走 list 分支）。
    wrapped = {'enabled': [{'skill_id': 'huanxing/x'}, 'huanxing/y']}
    assert _normalize_skill_ids(wrapped) == ['huanxing/x', 'huanxing/y']


def test_enabled_non_list_value_falls_back_to_keys() -> None:
    # 若 enabled 不是 list（异常数据），不当 wrapper 处理，回落到「key 即 skill_id」。
    assert _normalize_skill_ids({'enabled': 'huanxing/x'}) == ['enabled']
