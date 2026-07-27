"""DS-P3 设计系统导入三入口真实样例测试（零 mock）。

覆盖 P3 验收：
- 三入口各 ≥1 真实样例跑通（真 shadcn registry / 真 GitHub 仓 / 真网页扫色），产出含 :root 草稿；
- screenshot/shadcn 失败路径**如实报错**（无 fake fallback）；
- SSRF 闸：私网/元数据地址硬拒；空集不造假。

网络不可达时按连通性错误 skip（非内容错误）；本机经透明 TUN 代理 → 显式开 fake-ip 放行。
"""

from __future__ import annotations

import os

import pytest

from backend.app.hasn_designsystem.service import import_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.common.exception import errors

pytestmark = pytest.mark.asyncio

# 真实样例 URL（稳定公开来源）
_SHADCN_ITEM = 'https://tweakcn.com/r/themes/modern-minimal.json'  # 真 shadcn 格式 registry item（cssVars）
_SHADCN_NO_CSSVARS = 'https://ui.shadcn.com/r/styles/new-york/button.json'  # 组件 item：无 cssVars
_GITHUB_REPO = 'shadcn-ui/ui'  # 真前端仓（globals.css 带自定义属性）
_PAGE_URL = 'https://tailwindcss.com'  # 真网页扫色
_NO_COLOR_URL = 'https://api.github.com/zen'  # 纯文本禅语，无十六进制色 → screenshot 应诚实报错

_NET_HINTS = ('拉取失败', '无法解析', '响应过大')


@pytest.fixture(autouse=True)
def _fakeip_passthrough():
    """本机经透明 TUN 代理（fake-ip 198.18/15）；显式放行以跑真实样例。生产不设此 env。"""
    prev = os.environ.get('DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH')
    os.environ['DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH'] = '1'
    yield
    if prev is None:
        os.environ.pop('DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH', None)
    else:
        os.environ['DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH'] = prev


async def _run_or_skip(source: str, ref: str) -> dict:
    """跑导入；连通性错误（非内容错误）→ skip，让真实内容断言只在联网时生效。"""
    try:
        return await import_design_source(source, ref)
    except errors.RequestError as exc:
        if any(h in str(exc) for h in _NET_HINTS):
            pytest.skip(f'网络不可达，跳过真实样例: {exc}')
        raise


async def _expect_honest_failure(source: str, ref: str, must_contain: str) -> None:
    """断言诚实失败：抛 RequestError 且含特定内容失败信息（非网络错误才算数）。"""
    try:
        await import_design_source(source, ref)
    except errors.RequestError as exc:
        if any(h in str(exc) for h in _NET_HINTS):
            pytest.skip(f'网络不可达，跳过: {exc}')
        assert must_contain in str(exc), f'失败信息不含预期片段「{must_contain}」: {exc}'
        return
    pytest.fail('预期诚实报错，但导入竟成功（疑似 fake fallback）')


# ── 入口一：shadcn ────────────────────────────────────────────────────────────
async def test_import_shadcn_real() -> None:
    """真 shadcn 格式 registry item → cssVars 渲染成 :root + .dark 草稿，保真品牌色。"""
    r = await _run_or_skip('shadcn', _SHADCN_ITEM)
    assert r['source_kind'] == 'imported_shadcn'
    assert r['name']
    css = r['tokens_css']
    assert ':root' in css and '.dark' in css
    # 角色可映射的 shadcn 命名在场（compile_tokens role-hint 据此映射到规范 schema）
    assert '--background' in css and '--foreground' in css and '--primary' in css
    assert r['note']  # 草稿提示在场


async def test_import_shadcn_without_cssvars_honest_failure() -> None:
    """组件 item 无 cssVars → 诚实报错，不造假。"""
    await _expect_honest_failure('shadcn', _SHADCN_NO_CSSVARS, 'cssVars')


# ── 入口二：github ────────────────────────────────────────────────────────────
async def test_import_github_real() -> None:
    """真前端仓 → 命中全局样式入口、扫 CSS 自定义属性收敛成 :root 草稿。"""
    r = await _run_or_skip('github', _GITHUB_REPO)
    assert r['source_kind'] == 'imported_github'
    assert r['name'] == 'ui'
    css = r['tokens_css']
    assert ':root' in css
    decls = [ln.strip() for ln in css.splitlines() if ln.strip().startswith('--')]
    assert len(decls) >= 3
    # 纯自引用别名（值以 var( 开头）已过滤；复合值含 var() 属合法真值，不在此列。
    values = [d.split(':', 1)[1].strip() for d in decls if ':' in d]
    assert all(not v.startswith('var(') for v in values)


async def test_import_github_missing_repo_honest_failure() -> None:
    """真实公开仓没有候选样式入口 → 诚实报错。"""
    await _expect_honest_failure('github', 'octocat/Hello-World', '草稿失败')


# ── 入口三：screenshot / url ──────────────────────────────────────────────────
async def test_import_screenshot_real() -> None:
    """真网页扫色 → 最小草稿（bg/fg/accent），并明示近似偏差告警。"""
    r = await _run_or_skip('url', _PAGE_URL)
    assert r['source_kind'] == 'imported_screenshot'
    css = r['tokens_css']
    assert '--bg' in css and '--fg' in css and '--accent' in css
    assert r['warnings']  # 近似草稿必带偏差告警


async def test_import_screenshot_no_color_honest_failure() -> None:
    """纯文本页面无主色 → 诚实报错，绝不造假兜底。"""
    await _expect_honest_failure('url', _NO_COLOR_URL, '主色')


# ── SSRF 闸 + 空集 + 未知来源（纯逻辑，无外部网络）──────────────────────────────
async def test_ssrf_blocks_private_and_metadata() -> None:
    """私网/元数据地址硬拒（fake-ip 放行不影响真正的内网防护）。"""
    for bad in ('http://10.0.0.1/x.json', 'http://169.254.169.254/latest/meta-data', 'http://192.168.1.1/'):
        with pytest.raises(errors.RequestError):
            import_service._assert_fetchable_url(bad)


async def test_ssrf_blocks_non_http_scheme() -> None:
    with pytest.raises(errors.RequestError):
        import_service._assert_scheme('file:///etc/passwd')
    with pytest.raises(errors.RequestError):
        import_service._assert_scheme('ftp://example.com/x')


async def test_render_root_empty_is_honest_failure() -> None:
    """无任何可用 token → 抛错而非产出空草稿。"""
    with pytest.raises(errors.RequestError):
        import_service._render_root([])


async def test_unknown_source_rejected() -> None:
    with pytest.raises(errors.RequestError):
        await import_design_source('bogus', 'whatever')


async def test_wrap_value_hsl_triplet_and_passthrough() -> None:
    """裸 HSL 三元组包回 hsl(...)；已是函数/hex/oklch 原样保留。"""
    assert import_service._wrap_value('0 0% 100%') == 'hsl(0 0% 100%)'
    assert import_service._wrap_value('oklch(1 0 0)') == 'oklch(1 0 0)'
    assert import_service._wrap_value('#ffffff') == '#ffffff'
    assert import_service._wrap_value('var(--x)') == 'var(--x)'
