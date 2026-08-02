"""视频模型目录合并规则测试。

合并规则是这次线上故障的直接对策：PDC 抄了一份模型清单、抄错了名字（`agnes-2.0-video`，
真名 `agnes-video-v2.0`），抄的人无从发现，分身撞 503。改成与 new-api 取交集后，这类错配
会被跳过并 warn，而不是让分身照着一个不存在的模型花时间。
"""

from backend.app.hasn.schema.hasn_platform_default_config import VideoModelSpec
from backend.app.hasn.service.video_model_catalog_service import merge_catalog


def _spec(name: str, modality: str = 'any', dialect: str = 'openai', **kwargs) -> VideoModelSpec:
    return VideoModelSpec(name=name, modality=modality, dialect=dialect, **kwargs)


def test_只下发在newapi上真实可用的模型() -> None:
    declared = [
        _spec('happyhorse-1.1-i2v', modality='image_to_video', dialect='ali'),
        _spec('agnes-2.0-video'),  # 线上那个写错的名字：new-api 上不存在
    ]
    catalog = merge_catalog(declared, {'happyhorse-1.1-i2v', 'agnes-video-v2.0'}, {})
    assert [m['name'] for m in catalog] == ['happyhorse-1.1-i2v'], '不存在的模型必须被跳过而非下发'


def test_附带相对成本供分身取舍() -> None:
    declared = [
        _spec('wan2.6-i2v-flash', modality='image_to_video', dialect='ali', quality='draft'),
        _spec('happyhorse-1.1-i2v', modality='image_to_video', dialect='ali', quality='high'),
    ]
    available = {'wan2.6-i2v-flash', 'happyhorse-1.1-i2v'}
    catalog = merge_catalog(declared, available, {'wan2.6-i2v-flash': 0.5, 'happyhorse-1.1-i2v': 2.5})
    by_name = {m['name']: m for m in catalog}
    # 5 倍差价是分身选型的关键依据（草稿用便宜的、终稿用贵的）。
    assert by_name['wan2.6-i2v-flash']['relative_cost'] == 0.5
    assert by_name['happyhorse-1.1-i2v']['relative_cost'] == 2.5
    assert by_name['wan2.6-i2v-flash']['quality'] == 'draft'
    assert by_name['happyhorse-1.1-i2v']['modality'] == 'image_to_video'


def test_缺定价时仍下发目录只是不带成本() -> None:
    # 定价拉不到不该让整个目录不可用——少一个选型依据，但模型仍能用。
    catalog = merge_catalog([_spec('agnes-video-v2.0')], {'agnes-video-v2.0'}, {})
    assert len(catalog) == 1
    assert 'relative_cost' not in catalog[0], '没有价格就不写这个字段，绝不编一个默认值'


def test_字符串简写归一为无声明形态() -> None:
    catalog = merge_catalog(['some-model'], {'some-model'}, {})
    assert catalog[0]['modality'] == 'any'
    assert catalog[0]['dialect'] == 'openai'
    assert catalog[0]['quality'] is None


def test_顺序沿用语义表即failover优先级() -> None:
    declared = [_spec('b-model'), _spec('a-model')]
    catalog = merge_catalog(declared, {'a-model', 'b-model'}, {})
    assert [m['name'] for m in catalog] == ['b-model', 'a-model']
