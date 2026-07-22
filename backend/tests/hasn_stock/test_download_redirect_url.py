"""素材下载重定向 URL 解析回归测试。"""

from backend.app.hasn_stock.service.download_service import _resolve_redirect_url


def test_redirect_url_resolves_relative_location() -> None:
    """相对 Location 必须基于当前资源 URL 解析为下一跳绝对地址。"""
    assert (
        _resolve_redirect_url('https://cdn.example.com/images/source.jpg', '../media/final.jpg?size=large')
        == 'https://cdn.example.com/media/final.jpg?size=large'
    )
