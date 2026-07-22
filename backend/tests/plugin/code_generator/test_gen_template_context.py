"""代码生成模板上下文回归测试。"""

import pytest

from backend.plugin.code_generator.model import GenBusiness, GenColumn
from backend.plugin.code_generator.utils.gen_template import GenTemplate


def test_template_context_preserves_heterogeneous_generation_values() -> None:
    """模板上下文必须保留列元数据、作用域和时间函数等异构值。"""
    business = GenBusiness(
        app_name='hasn_demo',
        table_name='demo_record',
        doc_comment='演示记录',
        table_comment='用于验证生成上下文',
        class_name='DemoRecord',
        schema_name='DemoRecordSchema',
        filename='demo_record',
        datetime_mixin=True,
        api_version='v1',
        tag='演示',
        api_scope='app,agent',
    )
    column = GenColumn(
        name='metadata',
        comment='元数据',
        type='JSON',
        pd_type='dict[str, object]',
        default=None,
        sort=1,
        length=0,
        is_pk=False,
        is_nullable=True,
        gen_business_id=1,
    )

    template_vars = GenTemplate.get_vars(business, [column])

    assert template_vars['app_name'] == 'hasn_demo'
    assert template_vars['api_scopes'] == ['app', 'agent']
    assert callable(template_vars['now'])
    assert template_vars['models'] == [
        {
            'name': 'meta_data',
            'db_column': 'metadata',
            'comment': '元数据',
            'type': 'JSON',
            'pd_type': 'dict[str, object]',
            'default': None,
            'sort': 1,
            'length': 0,
            'is_pk': False,
            'is_nullable': True,
        }
    ]


@pytest.mark.asyncio
async def test_agent_api_template_uses_empty_create_response() -> None:
    """创建服务不返回资源时，生成的 Agent API 必须保留成功空数据信封。"""
    code = await (
        GenTemplate()
        .get_template('python/api_agent.jinja')
        .render_async(
            app_name='hasn_demo',
            doc_comment='演示记录',
            filename='demo_record',
            schema_name='DemoRecord',
            table_name='demo_record',
        )
    )

    compile(code, '<api_agent>', 'exec')
    assert 'await demo_record_service.create(db=db, obj=obj)' in code
    assert 'return response_base.success()' in code
    assert 'response_base.success(data=result)' not in code
