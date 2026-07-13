"""素材站下载 SSRF 防线纯函数测试（A-P2-2·§4.6 步骤 1）。

无 DB、无网络：只验下载服务的 SSRF 判定与文件名/类型纯逻辑。IP 判定用**字面 IP host**
（getaddrinfo 对数字 host 直接解析、不发 DNS），保证确定性 + 离线。

覆盖：
1. _host_in_whitelist —— 精确 + 子域后缀。
2. _reject_private_ip —— 环回/内网/链路本地/保留 拒；公网数字 IP 放行。
3. _ssrf_check —— 非 https 拒、无 host 拒、非白名单拒、白名单命中但解析到内网 IP 拒、白名单公网放行。
4. _kind_and_cap —— image/video 判类型 + 上限；其它 Content-Type 拒。
5. _filename —— 取末段；缺后缀按媒体类型给默认名。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_stock.service.download_service import (
    _MAX_IMAGE_BYTES,
    _MAX_VIDEO_BYTES,
    StockDownloadError,
    _host_in_whitelist,
    _kind_and_cap,
    _reject_private_ip,
    _ssrf_check,
)

# --------------------------------------------------------------------------- #
# _host_in_whitelist：精确 + 子域后缀
# --------------------------------------------------------------------------- #


def test_host_in_whitelist_exact_and_subdomain() -> None:
    wl = {'pexels.com', 'cdn.pixabay.com'}
    assert _host_in_whitelist('pexels.com', wl) is True
    assert _host_in_whitelist('videos.pexels.com', wl) is True  # 子域后缀
    assert _host_in_whitelist('cdn.pixabay.com', wl) is True
    assert _host_in_whitelist('PEXELS.COM', wl) is True  # 大小写不敏感
    # 不能被「后缀相似」骗过：evilpexels.com 不是 pexels.com 的子域。
    assert _host_in_whitelist('evilpexels.com', wl) is False
    assert _host_in_whitelist('pixabay.com', wl) is False  # 白名单是 cdn.pixabay.com，父域不自动命中
    assert _host_in_whitelist('other.example', wl) is False


# --------------------------------------------------------------------------- #
# _reject_private_ip：字面 IP host（离线确定性）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    'host',
    [
        '127.0.0.1',  # 环回
        '10.0.0.1',  # 私有 A
        '192.168.1.1',  # 私有 C
        '169.254.169.254',  # 链路本地（云元数据端点·经典 SSRF 目标）
        '0.0.0.0',  # unspecified
    ],
)
def test_reject_private_ip_blocks_internal(host: str) -> None:
    with pytest.raises(StockDownloadError):
        _reject_private_ip(host)


def test_reject_private_ip_allows_public_numeric() -> None:
    # 公网数字 IP：不发 DNS、不属内网 → 放行（不抛）。
    _reject_private_ip('8.8.8.8')
    _reject_private_ip('1.1.1.1')


# --------------------------------------------------------------------------- #
# 方案A：dev 放行透明代理 fake-ip 段（198.18.0.0/15），prod 严格拒、真内网永远拒
# 固化安全不变量——「本机放行仅限 fake-ip 占位段，生产/真内网零放宽」。
# --------------------------------------------------------------------------- #


def test_reject_private_ip_dev_allows_fake_ip_but_not_real_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 环境放行透明代理 fake-ip 段（198.18.0.0/15），但真内网段仍一律拒（安全不放宽）。"""
    from backend.app.hasn_stock.service import download_service

    monkeypatch.setattr(download_service.settings, 'ENVIRONMENT', 'dev')
    # fake-ip 段（RFC2544 benchmarking 保留，透明代理占位）→ dev 放行（不抛）
    _reject_private_ip('198.18.0.212')
    _reject_private_ip('198.19.255.1')  # /15 段内上界
    # 真内网段无论 dev/prod 一律拒——尤其云元数据端点必须永远拒
    for internal in ('10.0.0.1', '192.168.1.1', '127.0.0.1', '169.254.169.254'):
        with pytest.raises(StockDownloadError):
            _reject_private_ip(internal)


def test_reject_private_ip_prod_rejects_fake_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产 prod 环境：fake-ip 段也不放行——生产不受开发放行影响（安全零降级）。"""
    from backend.app.hasn_stock.service import download_service

    monkeypatch.setattr(download_service.settings, 'ENVIRONMENT', 'prod')
    with pytest.raises(StockDownloadError):
        _reject_private_ip('198.18.0.212')


# --------------------------------------------------------------------------- #
# _ssrf_check：https + host 白名单 + 非内网 IP 三重
# --------------------------------------------------------------------------- #


def test_ssrf_check_rejects_non_https() -> None:
    with pytest.raises(StockDownloadError):
        _ssrf_check('http://8.8.8.8/x.jpg', {'8.8.8.8'})


def test_ssrf_check_rejects_missing_host() -> None:
    with pytest.raises(StockDownloadError):
        _ssrf_check('https:///x.jpg', {'8.8.8.8'})


def test_ssrf_check_rejects_non_whitelisted_host() -> None:
    with pytest.raises(StockDownloadError):
        _ssrf_check('https://evil.example/x.jpg', {'8.8.8.8'})


def test_ssrf_check_rejects_whitelisted_but_private_ip() -> None:
    # host 在白名单内，但解析到内网 IP → 仍拒（SSRF 防线之二兜底 DNS rebinding）。
    with pytest.raises(StockDownloadError):
        _ssrf_check('https://127.0.0.1/x.jpg', {'127.0.0.1'})


def test_ssrf_check_accepts_whitelisted_public() -> None:
    # https + host 白名单命中 + 公网 IP → 放行（不抛）。
    _ssrf_check('https://8.8.8.8/photo.jpg', {'8.8.8.8'})


# --------------------------------------------------------------------------- #
# _kind_and_cap：Content-Type → (媒体类型, 上限)
# --------------------------------------------------------------------------- #


def test_kind_and_cap_image_and_video() -> None:
    assert _kind_and_cap('image/jpeg') == ('image', _MAX_IMAGE_BYTES)
    assert _kind_and_cap('image/png; charset=binary') == ('image', _MAX_IMAGE_BYTES)  # 带参数
    assert _kind_and_cap('video/mp4') == ('video', _MAX_VIDEO_BYTES)


@pytest.mark.parametrize('ct', ['text/html', 'application/json', '', 'application/octet-stream'])
def test_kind_and_cap_rejects_non_media(ct: str) -> None:
    with pytest.raises(StockDownloadError):
        _kind_and_cap(ct)


# --------------------------------------------------------------------------- #
# _filename：取末段 / 缺后缀给默认
# --------------------------------------------------------------------------- #


def test_filename_from_url_tail() -> None:
    from backend.app.hasn_stock.service.download_service import StockDownloadService

    svc = StockDownloadService()
    assert svc._filename('https://cdn.example.com/a/b/cat.jpg', 'image') == 'cat.jpg'
    # 缺后缀 → 按媒体类型给默认名。
    assert svc._filename('https://cdn.example.com/download', 'image') == 'stock.jpg'
    assert svc._filename('https://cdn.example.com/download', 'video') == 'stock.mp4'
    assert svc._filename('https://cdn.example.com/', 'image') == 'stock.jpg'
