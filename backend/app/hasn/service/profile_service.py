"""合并 profile 服务 — 在事务中读取 / 更新 sys_user + hasn_humans。

WebUI 通过 hasn-node daemon 的 `/api/v1/owner/me/profile` 代理调到本服务。
读取时取两表的并集（避免 daemon 两次往返）；更新时一次事务内同时写两张表
（nickname/avatar/bio 三个共享字段双写），避免出现单表更新成功导致的不一致。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.crud.crud_user import user_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.schema.profile import GetMergedProfile, UpdateMergedProfileParam
from backend.common.exception import errors


class HasnProfileService:
    """聚合 sys_user + hasn_humans 的合并 profile 服务。"""

    @staticmethod
    async def get_merged(*, db: AsyncSession, user_id: int) -> GetMergedProfile:
        """合并读取当前用户的 sys_user + hasn_humans 资料。

        :param db: AsyncSession
        :param user_id: sys_user.id（来自 session）
        :return: GetMergedProfile
        """
        user = await user_dao.get(db, user_id)
        if not user:
            raise errors.NotFoundError(msg='用户不存在')
        human = await hasn_humans_dao.get_by_user_id(db, user_id)
        if not human:
            # 注册成功后 onboarding 流会写入 hasn_humans 行，缺失意味着流程没跑完
            # — 给出有名错误而不是返回空字段，便于前端排查。
            raise errors.NotFoundError(msg='HASN 人类身份未初始化')

        return GetMergedProfile.model_validate({
            'user_id': user.id,
            'hasn_id': human.hasn_id,
            'star_id': human.star_id,
            # 公开身份：以 hasn_humans 为准；缺失时回退 sys_user.nickname
            'nickname': human.nickname or user.nickname,
            'avatar': human.avatar or user.avatar,
            'bio': human.bio if human.bio is not None else user.bio,
            # sys_user 业务字段
            'gender': user.gender,
            'birthday': user.birthday,
            'province': user.province,
            'city': user.city,
            'district': user.district,
            'phone': user.phone,
            'email': user.email,
            # hasn_humans 扩展
            'timezone': human.timezone,
            'created_at': human.created_time,
            'updated_at': human.updated_time,
        })

    @staticmethod
    async def update_merged(*, db: AsyncSession, user_id: int, obj: UpdateMergedProfileParam) -> GetMergedProfile:
        """事务内一次更新 sys_user + hasn_humans 两张表。

        nickname / avatar / bio 双写到两表；gender / birthday / province /
        city / district 只写 sys_user；timezone 只写 hasn_humans。phone /
        email 永远不在此处更新（需走 captcha 流）。

        :param db: AsyncSession (transaction)
        :param user_id: sys_user.id
        :param obj: UpdateMergedProfileParam
        :return: 最新合并 profile
        """
        # exclude_unset 让前端只发送变化的字段；user_dao.update_profile 会过滤
        # 掉 None 值，因此显式 `null` 想清空字段需要前端发空字符串而非 null。
        provided = obj.model_dump(exclude_unset=True)

        # 1. sys_user 列（nickname/avatar/bio/gender/birthday/province/city/district）
        sys_keys = (
            'nickname',
            'avatar',
            'bio',
            'gender',
            'birthday',
            'province',
            'city',
            'district',
        )
        sys_data = {k: provided[k] for k in sys_keys if k in provided}
        if sys_data:
            await user_dao.update_profile(db, user_id, sys_data)

        # 2. hasn_humans 列（nickname/avatar/bio/timezone）
        human = await hasn_humans_dao.get_by_user_id(db, user_id)
        if not human:
            raise errors.NotFoundError(msg='HASN 人类身份未初始化')
        previous_nickname = human.nickname  # 改名前捕获，供分身昵称回写比对
        human_keys = ('nickname', 'avatar', 'bio', 'timezone')
        human_data = {k: provided[k] for k in human_keys if k in provided}
        if human_data:
            # CRUDPlus update_model 接受 dict — 与 user_dao.update_profile 同款。
            await hasn_humans_dao.update_model(db, human.id, human_data)

        # 2.5 昵称变更 → 把内置/默认分身名里被烙进的旧昵称/手机号掩码后缀刷成新昵称
        #     （主人「填完昵称后更新分身昵称」）。best-effort：失败不阻断资料保存。
        new_nickname = provided.get('nickname')
        if 'nickname' in provided and new_nickname and new_nickname != previous_nickname:
            from backend.app.hasn.service.hasn_agents_service import agent_profile_service

            try:
                await agent_profile_service.refresh_seeded_agent_display_names(
                    db,
                    owner_id=human.hasn_id,
                    current_nickname=new_nickname,
                    previous_nickname=previous_nickname,
                )
            except Exception as exc:
                from backend.common.log import log

                log.warning('refresh seeded agent names failed for %s: %s', human.hasn_id, exc)

        # 3. 回读合并 profile（同一事务读自己的写，看到刚提交的值）
        return await HasnProfileService.get_merged(db=db, user_id=user_id)


hasn_profile_service = HasnProfileService()
