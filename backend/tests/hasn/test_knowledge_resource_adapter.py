"""知识库资源适配器的纯逻辑测试。"""

from backend.app.hasn_knowledge.service.resource_adapter import _asset_ids_from_content


def test_asset_ids_from_content_deduplicates_and_ignores_malformed_uri() -> None:
    content = (
        '![首图](hasn://asset/as_first-1)\n'
        '![次图](<hasn://asset/as_second_2>)\n'
        '重复 ![](hasn://asset/as_first-1)\n'
        '畸形 ![](hasn://asset/)\n'
    )

    assert _asset_ids_from_content(content) == {'as_first-1', 'as_second_2'}
    assert _asset_ids_from_content(None) == set()


def test_asset_ids_from_content_only_accepts_rendered_images() -> None:
    content = (
        '正文提到 hasn://asset/as_plain_text，但它不是图片。\n'
        '[普通链接](hasn://asset/as_link) 也不能获得图片投影权限。\n'
        '`![行内代码](hasn://asset/as_inline_code)`\n'
        '```markdown\n'
        '![围栏代码](hasn://asset/as_fenced_code)\n'
        '```\n'
        '    ![缩进代码](hasn://asset/as_indented_code)\n'
        '<pre><img src="hasn://asset/as_html_code"></pre>\n'
        '![前缀陷阱](hasn://asset/as_prefix.extra)\n'
        '<a href="hasn://asset/as_href">链接</a>\n'
        '<!-- ![注释图](hasn://asset/as_comment) -->\n'
        '<img alt="原始 HTML 不会被页面渲染" src="hasn://asset/as_raw_html">\n'
        '\n'
        '![真实 Markdown 图](hasn://asset/as_real_markdown)\n'
        '> ![引用块中的真实图](hasn://asset/as_real_blockquote)\n'
    )

    assert _asset_ids_from_content(content) == {'as_real_markdown', 'as_real_blockquote'}
