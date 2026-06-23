"""new-api 管理 HTTP API 客户端（唯一出站通道，替代 DB 直连第二引擎）。

设计要点（详见 docs/AI网关/实施/2026-06-15-...迁移方案.md §13 实测修正）：

- **唯一出站通道**：所有对 new-api 管理面的访问都经此类，封装 `{success,message,data}`
  信封、超时、降级。
- **鉴权（admin 通道）**：`Authorization=<root access_token>` + `New-Api-User=<root_id>`
  （`NEWAPI_ADMIN_USER_ID`，默认 1）。new-api `authHelper` 要求 `New-Api-User` 必须等于
  access_token 持有者 id（无跨用户冒充），故管理类操作以 root 身份 + 显式目标定位，
  **不用 `/self` 冒充他人**。
- **代用户建 relay token**：new-api 无 admin 建 token 接口；建 token 必须以用户身份
  （`POST /api/token/`，UserAuth=调用方）。流程：admin 设密码 → 用户 login（独立 cookie
  jar）→ `GET /api/user/token` 铸用户 access_token → 用该 token（cookieless）AddToken →
  admin `admin_token` 取明文 key。用户 access_token 由 service 加密存映射表复用。
- **trust_env=False**（强制）：本机/生产可能配 HTTP_PROXY/ALL_PROXY，httpx 默认会把
  localhost:3180 也走代理 → 503/ERR。new-api 是内网服务，绝不经外部代理。
- **不自动重试非幂等 POST**（与项目网关铁律一致）；连接级错误如实抛出。
- **降级**：调用方按需 try/except 本类抛出的 NewApiError → None/0/空，绝不 500、绝不造假。

单位约定：本客户端是 new-api 边界适配器，方法层面**只说 new-api 原生 `quota`（整数微单位）**。
积分↔quota 的 ×/÷ QUOTA_PER_DOLLAR 由 `app/newapi` 的单位工具（service 层）统一封装，
业务层只认「积分 = $」。
"""

from __future__ import annotations

import secrets
import string

from typing import Any

import httpx

from backend.common.log import log
from backend.common.service_http import get_service_client
from backend.common.service_registry import service_endpoint
from backend.core.conf import settings

# 模型注册表分页拉取上限（list_available_models）——安全护栏，非静默截断。
MODEL_LIST_PAGE_SIZE = 100
MODEL_LIST_MAX_PAGES = 50


class NewApiError(Exception):
    """new-api 管理 API 调用失败（非 2xx 或 success=false 或连接异常）。

    调用方决定是否降级（读类常降级为 None/0/空），写类一般直接抛出。
    """

    def __init__(self, message: str, *, status_code: int | None = None, endpoint: str | None = None) -> None:
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


def _gen_password(length: int = 18) -> str:
    """生成 new-api 用户密码（8~20 字符约束内）：字母+数字，避免特殊字符歧义。"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


class NewApiAdminClient:
    """new-api 管理 API 客户端（httpx, trust_env=False）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        access_token: str | None = None,
        admin_user_id: int | None = None,
        timeout: float | None = None,
    ) -> None:
        # 连接三元组经统一服务目录解析（解析链：env NEWAPI_ADMIN_* → settings → services.toml
        # [service.newapi] → dev 本机回落）。token 走 derive_token=False 分支，**绝不派生**——
        # 仍是外部 new-api 系统的真实 admin 密钥。NEWAPI_ADMIN_BASE_URL 自带 /api 默认，故
        # ep.base_url 恒含 /api（dev 端口回落 :3180 仅在 settings 默认被显式清空时才会触发）。
        ep = service_endpoint('newapi')
        self.base_url = (base_url or ep.base_url or settings.NEWAPI_ADMIN_BASE_URL).rstrip('/')
        self.access_token = access_token if access_token is not None else (ep.token or settings.NEWAPI_ADMIN_ACCESS_TOKEN)
        self.admin_user_id = admin_user_id if admin_user_id is not None else settings.NEWAPI_ADMIN_USER_ID
        self.timeout = timeout if timeout is not None else (ep.timeout or settings.NEWAPI_HTTP_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------ #
    # 底层请求 / 信封解包
    # ------------------------------------------------------------------ #
    def _admin_headers(self) -> dict[str, str]:
        return {
            'Authorization': self.access_token,
            'New-Api-User': str(self.admin_user_id),
        }

    def _new_client(self) -> httpx.AsyncClient:
        """独立短命 client（带 base_url + 独立 cookie jar）——**仅供 login 等需要会话隔离的流程**。

        trust_env=False：绝不经外部代理访问内网 new-api（见类 docstring）。普通 admin 调用走
        进程级连接池（见 :meth:`_request` 默认路径），不用本方法。
        """
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, trust_env=False)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> Any:
        """发请求并解包信封，返回 `data` 字段；失败抛 NewApiError（如实记录）。

        两条路径：
        - **caller 传入 client**（login cookie jar 隔离流）：用它 + relative path（client 自带
          base_url），**由 caller 负责关闭**（其 ``async with`` 收尾），本方法不关。
        - **默认**（无 caller client）：走进程级连接池单例 ``get_service_client('newapi')``——
          **绝不 aclose**（lifespan 统一关），且池单例无 base_url，须用**绝对 URL**
          （``self.base_url + path``）。超时 per-request 传入。trust_env=False 已是池的全局默认。
        """
        if client is not None:
            c, request_url = client, path  # caller 持有的隔离 client（自带 base_url），caller 负责关闭
        else:
            c, request_url = get_service_client('newapi'), f'{self.base_url}{path}'  # 池单例无 base_url → 绝对 URL
        try:
            resp = await c.request(method, request_url, headers=headers, params=params, json=json, timeout=self.timeout)
        except httpx.HTTPError as e:  # 连接 / 超时等
            log.warning(f'[new-api] {method} {path} 传输失败: {e!r}')
            raise NewApiError(f'new-api 不可达: {e}', endpoint=path) from e

        if resp.status_code >= 400:
            raise NewApiError(
                f'new-api {method} {path} HTTP {resp.status_code}: {resp.text[:200]}',
                status_code=resp.status_code,
                endpoint=path,
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise NewApiError(f'new-api {method} {path} 返回非 JSON: {resp.text[:200]}', endpoint=path) from e

        if not body.get('success', False):
            raise NewApiError(
                f'new-api {method} {path} success=false: {body.get("message")!r}',
                status_code=resp.status_code,
                endpoint=path,
            )
        return body.get('data')

    # ------------------------------------------------------------------ #
    # 状态 / 启动校验
    # ------------------------------------------------------------------ #
    async def get_status(self) -> dict:
        """GET /status（无鉴权）。返回 data，含 quota_per_unit。"""
        return await self._request('GET', '/status')

    async def get_quota_per_unit(self) -> int | None:
        data = await self.get_status()
        val = (data or {}).get('quota_per_unit')
        return int(val) if val is not None else None

    # ------------------------------------------------------------------ #
    # 用户管理（admin 通道）
    # ------------------------------------------------------------------ #
    async def search_user_by_username(self, username: str) -> dict | None:
        """按 keyword 模糊搜，精确匹配 username 返回该用户对象；无则 None。"""
        data = await self._request(
            'GET', '/user/search', headers=self._admin_headers(), params={'keyword': username, 'page_size': 50}
        )
        items = (data or {}).get('items') or []
        for it in items:
            if it.get('username') == username:
                return it
        return None

    async def create_user(self, *, username: str, password: str, display_name: str) -> None:
        """CreateUser（admin）。不返回 id（须随后 search 取 id）。"""
        await self._request(
            'POST',
            '/user/',
            headers=self._admin_headers(),
            json={'username': username, 'password': password, 'display_name': display_name or username},
        )

    async def ensure_user(self, *, username: str, display_name: str) -> int:
        """幂等确保 new-api 用户存在，返回 newapi_user_id。

        撞 username（已存在）→ 复用其 id（替代旧 DB ON CONFLICT 自愈）。
        """
        existing = await self.search_user_by_username(username)
        if existing:
            return int(existing['id'])
        # 不存在 → 创建（密码用后由 service bootstrap access_token 时重设，这里随机占位）
        try:
            await self.create_user(username=username, password=_gen_password(), display_name=display_name)
        except NewApiError:
            # search/create 之间可能被并发请求创建；也可能存在历史孤儿用户但 search 索引短暂未命中。
            recovered = await self.search_user_by_username(username)
            if recovered:
                log.warning(f'[new-api] CreateUser 冲突后复用已有用户 username={username} id={recovered.get("id")}')
                return int(recovered['id'])
            raise
        created = await self.search_user_by_username(username)
        if not created:
            raise NewApiError(f'CreateUser 后 search 不到用户 {username}', endpoint='/user/')
        return int(created['id'])

    async def ensure_user_group(self, *, newapi_user_id: int, group: str) -> bool:
        """确保 new-api 用户分组为 `group`（relay 渠道按用户分组匹配可用渠道）。

        new-api admin CreateUser（POST /user/）不接受 group 字段 → 新建用户分组为空字符串
        → relay 报「No available channel for model X under group  ()」（空组匹配不到任何渠道）。
        本方法读用户对象，分组已正确则 no-op（返回 False）；否则取**整个用户对象**仅改 group
        回 PUT（UpdateUser 强制保留 quota、空密码视为保留，安全），返回是否发生更新。

        `group` 为空字符串时不强制（沿用 new-api 行为），直接返回 False。
        """
        if not group:
            return False
        user = await self.get_user(newapi_user_id)
        if not user:
            raise NewApiError(f'设分组前取用户失败 id={newapi_user_id}', endpoint=f'/user/{newapi_user_id}')
        if (user.get('group') or '') == group:
            return False
        payload = dict(user)
        payload['group'] = group
        await self._request('PUT', '/user/', headers=self._admin_headers(), json=payload)
        log.info(f'[new-api] 用户 {newapi_user_id} 分组由 {user.get("group")!r} 修正为 {group!r}')
        return True

    async def get_user(self, newapi_user_id: int) -> dict | None:
        """GET /user/:id（admin）。返回含 quota/used_quota/request_count 的用户对象。"""
        data = await self._request('GET', f'/user/{newapi_user_id}', headers=self._admin_headers())
        return data

    async def get_user_quota(self, newapi_user_id: int) -> dict | None:
        """返回 {id, quota, used_quota, request_count}（与旧 DAO 同形），无则 None。"""
        data = await self.get_user(newapi_user_id)
        if not data:
            return None
        return {
            'id': data.get('id'),
            'quota': int(data.get('quota') or 0),
            'used_quota': int(data.get('used_quota') or 0),
            'request_count': int(data.get('request_count') or 0),
        }

    async def _quota_equals(self, newapi_user_id: int, expected: int) -> bool:
        """回读校验：new-api 当前 quota 是否等于 expected（设 quota 自校验用）。"""
        info = await self.get_user_quota(newapi_user_id)
        return bool(info and int(info.get('quota') or 0) == int(expected))

    async def set_user_quota(self, *, newapi_user_id: int, quota: int) -> None:
        """覆盖式设置 users.quota（admin），按序试两条机制 + 回读校验，命中即止。

        ⚠️ new-api 不同构建对「设 quota」支持的端点**相反，且都对失败谎报 success=True**：
          - 生产构建（实测 117.72.92.229）：`UpdateUser`(PUT /user/) 认 quota；
            `ManageUser`(POST /user/manage action=add_quota) 静默 no-op（这正是新用户 quota=0 的根因）。
          - 另一些构建（如本地契约测试机）：恰好相反——add_quota 有效、PUT 忽略 quota。
        故不能信任任一端点的 success，必须**回读校验**：先 UpdateUser，再回退 ManageUser，
        两条都未生效才抛（quota 是计费关键，禁静默漂移）。
        """
        quota = int(quota)

        # 机制 1：UpdateUser —— 取整对象 → 改 quota → PUT /user/（生产有效，空密码视为保留）
        user = await self.get_user(newapi_user_id)
        if not user:
            raise NewApiError(f'设 quota 前取用户失败 id={newapi_user_id}', endpoint=f'/user/{newapi_user_id}')
        payload = dict(user)
        payload['quota'] = quota
        await self._request('PUT', '/user/', headers=self._admin_headers(), json=payload)
        if await self._quota_equals(newapi_user_id, quota):
            log.info(f'[new-api] 用户 {newapi_user_id} quota 设为 {quota}（UpdateUser）')
            return

        # 机制 2：ManageUser add_quota override（部分构建有效）
        await self._request(
            'POST',
            '/user/manage',
            headers=self._admin_headers(),
            json={'id': newapi_user_id, 'action': 'add_quota', 'mode': 'override', 'value': quota},
        )
        if await self._quota_equals(newapi_user_id, quota):
            log.info(f'[new-api] 用户 {newapi_user_id} quota 设为 {quota}（ManageUser add_quota）')
            return

        raise NewApiError(
            f'设 quota 失败：UpdateUser 与 ManageUser 回读均未生效 id={newapi_user_id} target={quota}',
            endpoint='/user/',
        )

    async def set_user_password(self, *, newapi_user_id: int, username: str, password: str) -> None:
        """重设用户密码（admin，UpdateUser）。用于 bootstrap access_token / 孤儿恢复。"""
        await self._request(
            'PUT',
            '/user/',
            headers=self._admin_headers(),
            json={'id': newapi_user_id, 'username': username, 'password': password},
        )

    async def delete_user(self, newapi_user_id: int) -> bool:
        """DELETE /user/:id（admin）。返回是否成功（失败降级 False，不抛）。"""
        try:
            await self._request('DELETE', f'/user/{newapi_user_id}', headers=self._admin_headers())
            return True
        except NewApiError as e:
            log.warning(f'[new-api] delete_user id={newapi_user_id} 失败: {e}')
            return False

    async def get_batch_users_quota(self, newapi_user_ids: list[int]) -> dict[int, dict]:
        """批量取 quota（无批量接口 → 逐个 GET /user/:id）。返回 {id: {quota,used_quota,request_count}}。

        管理端只对当前页 N 条调用；单个失败时跳过该用户（如实降级），不整体 500。
        """
        out: dict[int, dict] = {}
        for uid in newapi_user_ids:
            try:
                info = await self.get_user_quota(uid)
            except NewApiError as e:
                log.warning(f'[new-api] get_batch_users_quota uid={uid} 跳过: {e}')
                continue
            if info:
                out[int(uid)] = {
                    'quota': info['quota'],
                    'used_quota': info['used_quota'],
                    'request_count': info['request_count'],
                }
        return out

    # ------------------------------------------------------------------ #
    # 代用户铸 access_token + 建 relay token
    # ------------------------------------------------------------------ #
    async def bootstrap_user_access_token(self, *, newapi_user_id: int, username: str) -> str:
        """为用户铸 new-api access_token（管理面身份令牌，配 New-Api-User 用）。

        admin 设临时密码 → 用户 login（独立 cookie jar）→ GET /user/token。
        返回明文 access_token（service 负责加密落库复用）。
        """
        password = _gen_password()
        await self.set_user_password(newapi_user_id=newapi_user_id, username=username, password=password)
        # 独立 client 持 login 会话（cookie jar 不污染 admin 调用）
        async with self._new_client() as login_c:
            await self._request(
                'POST', '/user/login', json={'username': username, 'password': password}, client=login_c
            )
            token = await self._request(
                'GET', '/user/token', headers={'New-Api-User': str(newapi_user_id)}, client=login_c
            )
        if not token or not isinstance(token, str):
            raise NewApiError(f'铸 access_token 失败 user={username}', endpoint='/user/token')
        return token

    async def add_token(
        self,
        *,
        user_access_token: str,
        newapi_user_id: int,
        name: str,
        unlimited_quota: bool = True,
        remain_quota: int = 0,
        expired_time: int = -1,
    ) -> None:
        """以用户身份建 relay token（AddToken）。不返回 id/key（须随后 admin 查取）。"""
        user_headers = {'Authorization': user_access_token, 'New-Api-User': str(newapi_user_id)}
        await self._request(
            'POST',
            '/token/',
            headers=user_headers,
            json={
                'name': name,
                'unlimited_quota': unlimited_quota,
                'remain_quota': int(remain_quota),
                'expired_time': int(expired_time),
                'model_limits_enabled': False,
            },
        )

    async def find_token(self, *, username: str, name: str) -> dict | None:
        """admin 按 username 搜该用户 token，按 name 精确匹配，多个取最大 id（最新）。"""
        data = await self._request(
            'GET', '/admin_token/search', headers=self._admin_headers(), params={'username': username, 'page_size': 100}
        )
        items = (data or {}).get('items') or []
        matched = [it for it in items if it.get('name') == name]
        if not matched:
            return None
        return max(matched, key=lambda it: it.get('id') or 0)

    async def get_token(self, token_id: int) -> dict | None:
        """admin 取单个 token（key 脱敏）。"""
        return await self._request('GET', f'/admin_token/{token_id}', headers=self._admin_headers())

    async def get_token_remain_quota(self, token_id: int) -> int | None:
        data = await self.get_token(token_id)
        if not data:
            return None
        return int(data.get('remain_quota') or 0)

    async def get_token_key(self, token_id: int) -> str:
        """admin 取 token 明文 key（POST /admin_token/:id/key）。"""
        data = await self._request('POST', f'/admin_token/{token_id}/key', headers=self._admin_headers())
        key = (data or {}).get('key')
        if not key:
            raise NewApiError(f'取 token key 失败 id={token_id}', endpoint=f'/admin_token/{token_id}/key')
        return key

    async def disable_token(self, token_id: int) -> bool:
        """软禁用 token（status=2，PUT /admin_token/?status_only=1）。返回是否成功。"""
        try:
            await self._request(
                'PUT',
                '/admin_token/',
                headers=self._admin_headers(),
                params={'status_only': '1'},
                json={'id': token_id, 'status': 2},
            )
            return True
        except NewApiError as e:
            log.warning(f'[new-api] disable_token id={token_id} 失败: {e}')
            return False

    async def provision_user_relay_token(
        self,
        *,
        newapi_user_id: int,
        username: str,
        user_access_token: str,
        name: str,
        unlimited_quota: bool = True,
    ) -> tuple[int, str]:
        """建 relay token 并取回 (token_id, 明文 key) 的高层封装。

        admin find by name（历史残留/重试幂等复用）→ AddToken（用户身份）
        → admin find by name → admin get plaintext key。
        """
        existing = await self.find_token(username=username, name=name)
        if existing:
            token_id = int(existing['id'])
            key = await self.get_token_key(token_id)
            return token_id, key

        try:
            await self.add_token(
                user_access_token=user_access_token,
                newapi_user_id=newapi_user_id,
                name=name,
                unlimited_quota=unlimited_quota,
            )
        except NewApiError:
            recovered = await self.find_token(username=username, name=name)
            if recovered:
                token_id = int(recovered['id'])
                log.warning(f'[new-api] AddToken 失败后复用已有 token username={username} name={name} id={token_id}')
                key = await self.get_token_key(token_id)
                return token_id, key
            raise
        token = await self.find_token(username=username, name=name)
        if not token:
            raise NewApiError(f'AddToken 后查不到 token name={name} user={username}', endpoint='/admin_token/search')
        token_id = int(token['id'])
        key = await self.get_token_key(token_id)
        return token_id, key

    # ------------------------------------------------------------------ #
    # 日志 / 用量（admin 通道，以 username/user_id 定位）
    # ------------------------------------------------------------------ #
    async def get_logs(
        self,
        *,
        username: str | None = None,
        token_name: str | None = None,
        model_name: str | None = None,
        log_type: int = 2,
        start_timestamp: int,
        end_timestamp: int,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict], int]:
        """GET /log/（admin）。返回 (items, total)。type=2 为消费日志。"""
        params: dict[str, Any] = {
            'type': log_type,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp,
            'p': page,
            'page_size': page_size,
        }
        if username:
            params['username'] = username
        if token_name:
            params['token_name'] = token_name
        if model_name:
            params['model_name'] = model_name
        data = await self._request('GET', '/log/', headers=self._admin_headers(), params=params)
        items = (data or {}).get('items') or []
        total = int((data or {}).get('total') or 0)
        return items, total

    async def get_log_stat(
        self,
        *,
        username: str | None = None,
        token_name: str | None = None,
        model_name: str | None = None,
        log_type: int = 2,
        start_timestamp: int,
        end_timestamp: int,
    ) -> dict:
        """GET /log/stat（admin）。返回 {quota, rpm, tpm}（quota=窗口 SUM；rpm/tpm 为 60s 快照）。"""
        params: dict[str, Any] = {
            'type': log_type,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp,
        }
        if username:
            params['username'] = username
        if token_name:
            params['token_name'] = token_name
        if model_name:
            params['model_name'] = model_name
        data = await self._request('GET', '/log/stat', headers=self._admin_headers(), params=params)
        return data or {}

    async def list_available_models(self) -> list[dict]:
        """GET /api/models/（admin，AdminAuth）。分页拉 new-api 模型注册表，返回 status==1 的项。

        用作「可用模型目录」的权威源（解耦后 new-api 为模型权威；策展元数据已退化为默认/启发式）。
        每项含 `{id, model_name, description, icon, vendor_id, status, ...}`。
        分页到 MODEL_LIST_MAX_PAGES 上限即止（非静默：到顶告警）。
        """
        out: list[dict] = []
        for page in range(1, MODEL_LIST_MAX_PAGES + 1):
            data = await self._request(
                'GET',
                '/models/',
                headers=self._admin_headers(),
                params={'p': page, 'page_size': MODEL_LIST_PAGE_SIZE},
            )
            items = (data or {}).get('items') or []
            if not items:
                break
            out.extend(items)
            if len(items) < MODEL_LIST_PAGE_SIZE:
                break
            if page == MODEL_LIST_MAX_PAGES:
                log.warning(f'[new-api] list_available_models 到达分页上限 {MODEL_LIST_MAX_PAGES} 页，结果可能不完整')
        return [m for m in out if int(m.get('status') or 0) == 1]

    async def get_quota_data(
        self,
        *,
        username: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        """GET /data/?username=（admin）。QuotaData 行（按 model+hour 桶聚合）。

        每行：{model_name, created_at(unix 秒, hour 对齐), quota, token_used, count, ...}。
        token_used = prompt+completion 合计。
        """
        data = await self._request(
            'GET',
            '/data/',
            headers=self._admin_headers(),
            params={'username': username, 'start_timestamp': start_timestamp, 'end_timestamp': end_timestamp},
        )
        return data or []


# 模块单例（admin 通道默认配置；service 直接 import 使用）
newapi_admin_client = NewApiAdminClient()
