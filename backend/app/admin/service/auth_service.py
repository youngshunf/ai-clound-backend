from fastapi import Request, Response
from fastapi.security import HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask, BackgroundTasks

from backend.app.admin.crud.crud_menu import menu_dao
from backend.app.admin.crud.crud_user import user_dao
from backend.app.admin.model import User
from backend.app.admin.schema.token import AgentTokenInfo, GetLoginToken, GetNewToken
from backend.app.admin.schema.user import AuthLoginParam
from backend.app.admin.service.login_log_service import login_log_service
from backend.app.admin.service.user_password_history_service import password_security_service
from backend.app.admin.utils.password_security import password_verify
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.common.context import ctx
from backend.common.enums import LoginLogStatusType
from backend.common.exception import errors
from backend.common.i18n import t
from backend.common.log import log
from backend.common.response.response_code import CustomErrorCode
from backend.common.security.jwt import (
    create_access_token,
    create_new_token,
    create_refresh_token,
    get_token,
    jwt_decode,
)
from backend.core.conf import settings
from backend.database.db import uuid4_str
from backend.database.redis import redis_client
from backend.utils.dynamic_config import load_login_config
from backend.utils.timezone import timezone


class AuthService:
    """认证服务类"""

    @staticmethod
    async def user_verify(db: AsyncSession, username: str, password: str) -> tuple[User, int | None]:
        """
        验证用户名/手机号和密码

        :param db: 数据库会话
        :param username: 用户名或手机号
        :param password: 密码
        :return:
        """
        # 先尝试通过用户名查找
        user = await user_dao.get_by_username(db, username)
        # 如果找不到，尝试通过手机号查找
        if not user:
            user = await user_dao.get_by_phone(db, username)
        if not user:
            raise errors.NotFoundError(msg='用户名或密码有误')

        await password_security_service.check_status(user.id, user.status)

        if user.password is None or not password_verify(password, user.password):
            await password_security_service.handle_login_failure(db, user.id)
            raise errors.AuthorizationError(msg='用户名或密码有误')

        days_remaining = await password_security_service.check_password_expiry_status(
            db, user.last_password_changed_time
        )

        await password_security_service.handle_login_success(user.id)

        return user, days_remaining

    async def swagger_login(self, *, db: AsyncSession, obj: HTTPBasicCredentials) -> tuple[str, User]:
        """
        Swagger 文档登录

        :param db: 数据库会话
        :param obj: 登录凭证
        :return:
        """
        user, _ = await self.user_verify(db, obj.username, obj.password)
        await user_dao.update_login_time(db, obj.username)
        access_token_data = await create_access_token(
            user.id,
            multi_login=user.is_multi_login,
            # extra info
            swagger=True,
        )
        return access_token_data.access_token, user

    async def login(
        self,
        *,
        db: AsyncSession,
        response: Response,
        obj: AuthLoginParam,
        background_tasks: BackgroundTasks,
    ) -> GetLoginToken:
        """
        用户登录

        :param db: 数据库会话
        :param response: 响应对象
        :param obj: 登录参数
        :param background_tasks: 后台任务
        :return:
        """
        user = None
        try:
            await load_login_config(db)
            if settings.LOGIN_CAPTCHA_ENABLED:
                if not obj.uuid or not obj.captcha:
                    raise errors.RequestError(msg=t('error.captcha.invalid'))
                captcha_code = await redis_client.get(f'{settings.LOGIN_CAPTCHA_REDIS_PREFIX}:{obj.uuid}')
                if not captcha_code:
                    raise errors.RequestError(msg=t('error.captcha.expired'))
                if captcha_code.lower() != obj.captcha.lower():
                    raise errors.CustomError(error=CustomErrorCode.CAPTCHA_ERROR)
                await redis_client.delete(f'{settings.LOGIN_CAPTCHA_REDIS_PREFIX}:{obj.uuid}')

            user, days_remaining = await self.user_verify(db, obj.username, obj.password)
            await user_dao.update_login_time(db, obj.username)
            await db.refresh(user)
            access_token_data = await create_access_token(
                user.id,
                multi_login=user.is_multi_login,
                # extra info
                username=user.username,
                nickname=user.nickname,
                last_login_time=timezone.to_str(user.last_login_time) if user.last_login_time else '未知',
                ip=ctx.ip,
                os=ctx.os,
                browser=ctx.browser,
                device=ctx.device,
            )
            refresh_token_data = await create_refresh_token(
                access_token_data.session_uuid,
                user.id,
                multi_login=user.is_multi_login,
            )
            response.set_cookie(
                key=settings.COOKIE_REFRESH_TOKEN_KEY,
                value=refresh_token_data.refresh_token,
                max_age=settings.COOKIE_REFRESH_TOKEN_EXPIRE_SECONDS,
                expires=timezone.to_utc(refresh_token_data.refresh_token_expire_time),
                httponly=True,
            )
        except errors.NotFoundError as e:
            log.error('登陆错误: 用户名不存在')
            raise errors.NotFoundError(msg=e.msg or '用户名或密码有误')
        except (errors.RequestError, errors.CustomError) as e:
            if not user:
                log.error('登陆错误: 用户密码有误')
            task = BackgroundTask(
                login_log_service.create,
                user_uuid=user.uuid if user else uuid4_str(),
                username=obj.username,
                login_time=timezone.now(),
                status=LoginLogStatusType.fail.value,
                msg=e.msg or '请求失败',
            )
            raise errors.RequestError(code=e.code, msg=e.msg or '请求失败', background=task)
        except Exception as e:
            log.error(f'登陆错误: {e}')
            raise
        else:
            background_tasks.add_task(
                login_log_service.create,
                user_uuid=user.uuid,
                username=obj.username,
                login_time=timezone.now(),
                status=LoginLogStatusType.success.value,
                msg=t('success.login.success'),
            )
            # 获取 LLM Token（通过 new-api 创建/获取）
            try:
                newapi_username = user.phone or user.username
                mapping = await llm_newapi_user_mapping_service.ensure_newapi_user(
                    db, user.id,
                    username=newapi_username,
                    nickname=user.nickname or '',
                )
                llm_token = f'sk-{mapping.newapi_token_key}'
            except Exception as e:
                log.error(f'new-api 用户创建失败: {e}')
                raise errors.ServerError(msg='LLM 服务初始化失败，请稍后重试')
            # HASN Node Key 已与登录解耦，由客户端在网络初始化时调用专用接口获取
            hasn_node_key = None

            # 批量签发 Agent JWT
            agent_tokens_list = []
            try:
                from backend.app.hasn_core import hasn_agents_dao, hasn_humans_dao
                from backend.common.security.agent_jwt import create_agent_access_token

                # 查询用户的 hasn_id
                human = await hasn_humans_dao.get_by_user_id(db, user.id)
                if human and human.hasn_id:
                    # 查询该用户的所有活跃 Agent
                    agents = await hasn_agents_dao.get_active_agents_by_owner(db, human.hasn_id)

                    # 为每个 Agent 签发 JWT（scopes 已退役·实施102 S0：JWT 不携带 scopes，授权只看三态）
                    for agent in agents:
                        try:
                            agent_token = await create_agent_access_token(
                                agent_hasn_id=agent.hasn_id,
                                agent_name=agent.display_name or agent.agent_name,
                                owner_hasn_id=human.hasn_id,
                                owner_user_id=user.id,
                            )

                            agent_tokens_list.append(AgentTokenInfo(
                                agent_hasn_id=agent.hasn_id,
                                agent_name=agent.display_name or agent.agent_name,
                                access_token=agent_token.access_token,
                            ))
                        except Exception as e:
                            log.error(f'为 Agent {agent.hasn_id} 签发 JWT 失败: {e}')
                            # 单个 Agent 签发失败不影响整体登录流程
                            continue
            except Exception as e:
                log.error(f'批量签发 Agent JWT 失败: {e}')
                # Agent JWT 签发失败不影响用户登录

            data = GetLoginToken(
                access_token=access_token_data.access_token,
                access_token_expire_time=access_token_data.access_token_expire_time,
                session_uuid=access_token_data.session_uuid,
                refresh_token=refresh_token_data.refresh_token,
                refresh_token_expire_time=refresh_token_data.refresh_token_expire_time,
                password_expire_days_remaining=days_remaining,
                llm_token=llm_token,
                llm_base_url=settings.LLM_API_BASE_URL,
                hasn_node_key=hasn_node_key,
                agent_tokens=agent_tokens_list,
                user=user,  # type: ignore
            )
            return data

    @staticmethod
    async def get_codes(*, db: AsyncSession, request: Request) -> list[str]:
        """
        获取用户权限码

        :param db: 数据库会话
        :param request: FastAPI 请求对象
        :return:
        """
        codes: set[str] = set()
        if request.user.is_superuser:
            menus = await menu_dao.get_all(db, None, None)
            for menu in menus:
                if menu.perms:
                    codes.add(*menu.perms.split(','))
        else:
            roles = request.user.roles
            if roles:
                for role in roles:
                    for menu in role.menus:
                        if menu.perms:
                            codes.add(*menu.perms.split(','))

        return list(codes)

    @staticmethod
    async def refresh_token(
        *,
        db: AsyncSession,
        request: Request,
        response: Response,
        body_refresh_token: str | None = None,
    ) -> GetNewToken:
        """
        刷新令牌

        优先从 body 读取 refresh_token（官网等前端客户端），
        回退从 Cookie 读取（管理后台等使用 httpOnly Cookie 的客户端）。

        :param db: 数据库会话
        :param request: FastAPI 请求对象
        :param response: FastAPI 响应对象
        :param body_refresh_token: 请求体中的 refresh_token（可选）
        :return:
        """
        # 优先 body，回退 Cookie
        refresh_token = body_refresh_token or request.cookies.get(settings.COOKIE_REFRESH_TOKEN_KEY)
        if not refresh_token:
            raise errors.RequestError(msg='Refresh Token 已过期，请重新登录')
        token_payload = jwt_decode(refresh_token)

        user = await user_dao.get(db, token_payload.id)
        if not user:
            raise errors.TokenError(msg='Refresh Token 已过期，请重新登录')
        if not user.status:
            raise errors.AuthorizationError(msg='用户已被锁定, 请联系统管理员')
        if not user.is_multi_login and await redis_client.get_prefix(f'{settings.TOKEN_REDIS_PREFIX}:{user.id}:*'):
            raise errors.ForbiddenError(msg='此用户已在异地登录，请重新登录并及时修改密码')
        new_token = await create_new_token(
            refresh_token,
            token_payload.session_uuid,
            user.id,
            multi_login=user.is_multi_login,
            # extra info
            username=user.username,
            nickname=user.nickname,
            last_login_time=timezone.to_str(user.last_login_time) if user.last_login_time else '未知',
            ip=ctx.ip,
            os=ctx.os,
            browser=ctx.browser,
            device_type=ctx.device,
        )
        # 将新的 refresh_token 写回 Cookie（兼容管理后台）
        response.set_cookie(
            key=settings.COOKIE_REFRESH_TOKEN_KEY,
            value=new_token.new_refresh_token,
            max_age=settings.COOKIE_REFRESH_TOKEN_EXPIRE_SECONDS,
            expires=timezone.to_utc(new_token.new_refresh_token_expire_time),
            httponly=True,
        )
        data = GetNewToken(
            access_token=new_token.new_access_token,
            access_token_expire_time=new_token.new_access_token_expire_time,
            session_uuid=new_token.session_uuid,
            new_refresh_token=new_token.new_refresh_token,
            new_refresh_token_expire_time=new_token.new_refresh_token_expire_time,
        )
        return data

    @staticmethod
    async def logout(*, request: Request, response: Response) -> None:
        """
        用户登出

        :param request: FastAPI 请求对象
        :param response: FastAPI 响应对象
        :return:
        """
        try:
            token = get_token(request)
            token_payload = jwt_decode(token)
            user_id = token_payload.id
            session_uuid = token_payload.session_uuid
            refresh_token = request.cookies.get(settings.COOKIE_REFRESH_TOKEN_KEY)
        except errors.TokenError:
            return
        finally:
            response.delete_cookie(settings.COOKIE_REFRESH_TOKEN_KEY)

        await redis_client.delete(f'{settings.TOKEN_REDIS_PREFIX}:{user_id}:{session_uuid}')
        await redis_client.delete(f'{settings.TOKEN_EXTRA_INFO_REDIS_PREFIX}:{user_id}:{session_uuid}')
        if refresh_token:
            await redis_client.delete(f'{settings.TOKEN_REFRESH_REDIS_PREFIX}:{user_id}:{session_uuid}')


auth_service: AuthService = AuthService()
