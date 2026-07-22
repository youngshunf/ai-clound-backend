import json

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from backend.app.admin.schema.token import GetTokenDetail
from backend.common.enums import StatusType
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth, DependsSuperUser, jwt_decode, revoke_token
from backend.core.conf import settings
from backend.database.redis import redis_client

router = APIRouter()


@router.get('', summary='获取在线用户', dependencies=[DependsJwtAuth])
async def get_sessions(
    username: Annotated[str | None, Query(description='用户名')] = None,
) -> ResponseSchemaModel[list[GetTokenDetail]]:
    token_keys = await redis_client.get_prefix(f'{settings.TOKEN_REDIS_PREFIX}:*')
    online_clients = await redis_client.smembers(settings.TOKEN_ONLINE_REDIS_PREFIX)
    data: list[GetTokenDetail] = []

    def append_token_detail(extra_info: dict[str, Any]) -> None:
        data.append(
            token_detail.model_copy(
                update={
                    'username': extra_info.get('username', '未知'),
                    'nickname': extra_info.get('nickname', '未知'),
                    'ip': extra_info.get('ip', '未知'),
                    'os': extra_info.get('os', '未知'),
                    'browser': extra_info.get('browser', '未知'),
                    'device': extra_info.get('device', '未知'),
                    'last_login_time': extra_info.get('last_login_time', '未知'),
                },
            ),
        )

    for key in token_keys:
        token = await redis_client.get(key)
        if token is None:
            continue
        try:
            token_payload = jwt_decode(token)
        except errors.TokenError:
            log.warning(f'在线会话包含无效令牌，已清理: key={key}')
            await redis_client.delete(key)
            continue
        user_id = token_payload.id
        session_uuid = token_payload.session_uuid
        token_detail = GetTokenDetail(
            id=user_id,
            session_uuid=session_uuid,
            username='未知',
            nickname='未知',
            ip='未知',
            os='未知',
            browser='未知',
            device='未知',
            status=StatusType.enable if session_uuid in online_clients else StatusType.disable,
            last_login_time='未知',
            expire_time=token_payload.expire_time,
        )
        extra_info_raw = await redis_client.get(f'{settings.TOKEN_EXTRA_INFO_REDIS_PREFIX}:{user_id}:{session_uuid}')
        if extra_info_raw:
            try:
                extra_info = json.loads(extra_info_raw)
            except json.JSONDecodeError:
                log.warning(f'在线会话附加信息格式错误，忽略附加信息: user_id={user_id}, session_uuid={session_uuid}')
                data.append(token_detail)
                continue
            if not isinstance(extra_info, dict):
                log.warning(f'在线会话附加信息不是对象，忽略附加信息: user_id={user_id}, session_uuid={session_uuid}')
                data.append(token_detail)
                continue
            # 排除 swagger 登录生成的 token
            if extra_info.get('swagger') is None:
                if username is not None:
                    if username == extra_info.get('username'):
                        append_token_detail(extra_info)
                else:
                    append_token_detail(extra_info)
        else:
            data.append(token_detail)
    return response_base.success(data=data)


@router.delete(
    '/{pk}',
    summary='强制下线',
    dependencies=[DependsSuperUser],
)
async def delete_session(
    pk: Annotated[int, Path(description='用户 ID')],
    session_uuid: Annotated[str, Query(description='会话 UUID')],
) -> ResponseModel:
    await revoke_token(pk, session_uuid)
    return response_base.success()
