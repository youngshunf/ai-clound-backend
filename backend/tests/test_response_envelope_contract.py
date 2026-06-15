"""响应信封契约守卫（项目硬规则）。

**规则**：fba 云端"正常业务接口"必须用统一返回格式——
`response_model=ResponseModel`（含子类 `ResponseSchemaModel`）+ `return response_base.success(...)`。
daemon 经 transport `.send()` → `decode_ok_envelope` 解析 `{code,msg,data}` 信封；
handler 若裸返回 Pydantic 模型会让 daemon 解析失败（2026-06-02 权限 tab `get_scope_catalog`
"error decoding response body" 即此，修复 commit 54da4c4）。

**裸返回仅限"统一信封根本满足不了"的接口**：OpenAI/Anthropic 兼容代理（外部 SDK 按原生形状解析）、
文件/YAML/下载/导出、重定向、第三方 webhook（须回 provider 指定文本）等。这些 + 当前历史欠债端点
冻在下方 `KNOWN_NON_ENVELOPE` 基线里。

本守卫只挡**新增漂移**：任何新路由若不返回 ResponseModel 且不在基线 → 失败。届时：
- 正常业务接口 → 改成 `response_model=ResponseModel` + `response_base.success(...)`；
- 确属真例外 → 显式加进基线 GENUINE 段并写明"信封哪满足不了"。
迁移欠债端点到信封后，记得从 DEBT 段删除对应行（`test_no_stale_baseline` 会提醒）。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.common.response.response_schema import ResponseModel
from backend.plugin.core import build_final_router

# ---- 真例外（统一信封满足不了，永久保留）----
_GENUINE = {
    # OpenAI/Anthropic 兼容代理：外部 SDK / hermes runtime 按原生形状解析，套信封即违反协议
    # 注：自建 /api/v1/llm/proxy/* 网关已随 NEWAPI-P6 删除（OpenAI 兼容面下沉 new-api 自身），
    # 故此处仅保留仍存活的 hermes agent chat 兼容代理。
    'POST /api/v1/hermes/app/agents/{agent_id}/chat/completions',
    # 文件 / YAML / 下载 / 导出（返回二进制或文本文件，非 JSON 业务体）
    'GET /api/v1/client/version/latest-linux.yml',
    'GET /api/v1/client/version/latest-mac.yml',
    'GET /api/v1/client/version/latest.yml',
    'GET /api/v1/code-generation/generations/{pk}',
    'GET /api/v1/marketplace/open/skills/{resource_id:path}/download',
    'GET /api/v1/marketplace/open/templates/{resource_id:path}/download',
    # 个人技能库私有包带鉴权下载（SKILLSYNC-C2 难点5）：Agent JWT 校验 owner 归属后
    # StreamingResponse 直回 zip 字节（不 302 跳私有桶，因 hermes urllib 取私有技能会 401）
    'GET /api/v1/marketplace/agent/personal-skills/{personal_skill_id:path}/download',
    # 知识库原件下载（D10）：私有桶流式二进制透传（StreamingResponse），信封满足不了
    'GET /api/v1/knowledge/app/documents/{doc_id}/download',
    # 网页发布公开查看面 /s/*（模块 18，独立分享域名）：浏览器直接加载的公开 HTML 外壳 /
    # 二进制制品 / 解锁 JSON——制品内容绝不在 API 主域以信封套壳；/content 恒带 CSP sandbox（见 hosting.py）
    'GET /s/{slug}',
    'POST /s/{slug}/unlock',
    'GET /s/{slug}/content',
    'GET /s/{slug}/assets/{name:path}',
    # Runtime 派发代理面 SSE（云端形态）：text/event-stream 逐事件透传，非 JSON 信封
    'POST /api/v1/hasn/agent/runtime/runs',
    # 重定向 / 外部 OAuth 回调
    'GET /api/v1/oauth2/github/callback',
    'GET /api/v1/oauth2/google/callback',
    # 第三方支付 webhook（须回 provider 指定文本，如 "success"）
    'POST /api/v1/pay/open/contract-notify/{channel_id}',
    'POST /api/v1/pay/open/notify/{channel_id}',
    'POST /api/v1/pay/open/refund-notify/{channel_id}',
    # Swagger 文档授权（fba 内置，给 docs UI 用）
    'POST /api/v1/auth/login/swagger',
    # 插件原始响应（plugin 自定义返回）
    'GET /api/v1/sys/plugins/{plugin}',
}

# ---- 已知欠债（正常业务接口，当前裸返回但自洽工作；按规则应逐步迁到信封，迁完删行）----
_DEBT = {
    # hasn 登录 / 同步 / 消息：daemon 侧用 .send_json() 配对，迁移须云端+daemon 两仓同步改
    'POST /api/v1/hasn/auth/phone/send_code',
    'POST /api/v1/hasn/auth/phone/verify',
    'POST /api/v1/hasn/auth/token/refresh',
    'POST /api/v1/hasn/inbox/pull',
    'POST /api/v1/hasn/memory/sync/pull',
    'POST /api/v1/hasn/messages/send',
    'POST /api/v1/hasn/onboarding/ensure',
    'POST /api/v1/hasn/runtime/report',
    'POST /api/v1/hasn/sync/pull',
    'POST /api/v1/hasn/sync/push',
    # hasn_task 模块（从 app/hasn 拆出）任务定义同步：daemon 侧 .send_json() 配对，同上 hasn/sync 系列
    'POST /api/v1/hasn-task/app/sync/pull',
    'POST /api/v1/hasn-task/app/sync/push',
    # hasn 企业 / ragflow / workspace：业务 JSON 但 handler 无 response_model 注解
    'GET /api/v1/hasn/enterprise/invite-codes',
    'GET /api/v1/hasn/enterprise/memberships',
    'GET /api/v1/hasn/enterprises',
    'GET /api/v1/huanxing/analytics',
    # marketplace open 浏览：daemon 侧用 .send_json() 配对
    'GET /api/v1/marketplace/open/categories',
    'GET /api/v1/marketplace/open/categories/{category_slug}/skills',
    'GET /api/v1/marketplace/open/categories/{category_slug}/templates',
    'GET /api/v1/marketplace/open/skills',
    'GET /api/v1/marketplace/open/skills/search',
    'GET /api/v1/marketplace/open/skills/{resource_id:path}',
    'GET /api/v1/marketplace/open/templates',
    'GET /api/v1/marketplace/open/templates/search',
    'GET /api/v1/marketplace/open/templates/{resource_id:path}',
    'GET /api/v1/marketplace/open/trending/skills',
    'GET /api/v1/marketplace/open/trending/templates',
    # marketplace admin 同步工具：返回 dict
    'DELETE /api/v1/marketplace/admin/sync/cache',
    'GET /api/v1/marketplace/admin/sync/cache/stats',
    'GET /api/v1/marketplace/admin/sync/status',
    'POST /api/v1/marketplace/admin/sync/clawhub',
    'POST /api/v1/marketplace/admin/sync/github',
    'POST /api/v1/marketplace/admin/sync/github/templates',
    'POST /api/v1/marketplace/admin/sync/retranslate',
    # openclaw gateway
    'GET /api/v1/openclaw/gateway/configs',
    'PATCH /api/v1/openclaw/gateway/config',
    'POST /api/v1/openclaw/gateway/token',
    'POST /api/v1/openclaw/gateway/verify-token',
}

KNOWN_NON_ENVELOPE = _GENUINE | _DEBT


def _current_non_envelope() -> set[str]:
    """内省装配后的全部路由，返回不走信封的 `METHOD path` 集合。"""
    router = build_final_router()
    found: set[str] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        model = route.response_model
        if isinstance(model, type) and issubclass(model, ResponseModel):
            continue
        for method in route.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            found.add(f'{method} {route.path}')
    return found


def test_no_new_non_envelope_routes() -> None:
    """新增的业务接口必须走统一信封；不在基线的裸返回路由 → 失败。"""
    new = _current_non_envelope() - KNOWN_NON_ENVELOPE
    assert not new, (
        '发现新的非信封路由，违反"业务接口必须统一返回格式"硬规则。\n'
        '正常业务接口请用 response_model=ResponseModel + return response_base.success(data=...)；\n'
        '确属真例外（OpenAI 兼容代理/文件/重定向/webhook）才加入 KNOWN_NON_ENVELOPE 并注明理由：\n'
        + '\n'.join(f'  - {k}' for k in sorted(new))
    )


def test_no_stale_baseline() -> None:
    """基线里已不存在或已迁到信封的条目应删除，保持名单诚实（欠债迁移后提醒清理）。"""
    stale = KNOWN_NON_ENVELOPE - _current_non_envelope()
    assert not stale, (
        '以下基线条目已不再是非信封路由（可能已迁到信封或路由变更），请从 '
        'KNOWN_NON_ENVELOPE 删除：\n' + '\n'.join(f'  - {k}' for k in sorted(stale))
    )
