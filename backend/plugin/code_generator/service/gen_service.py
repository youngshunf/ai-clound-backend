import io
import os
import zipfile

from collections.abc import Sequence

import anyio

from anyio import open_file
from pydantic.alias_generators import to_pascal
from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.exception import errors
from backend.core.conf import settings
from backend.core.path_conf import BASE_PATH
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.crud.crud_column import gen_column_dao
from backend.plugin.code_generator.crud.crud_gen import gen_dao
from backend.plugin.code_generator.model import GenBusiness
from backend.plugin.code_generator.schema.business import CreateGenBusinessParam
from backend.plugin.code_generator.schema.column import CreateGenColumnParam
from backend.plugin.code_generator.schema.gen import ImportParam
from backend.plugin.code_generator.service.column_service import gen_column_service
from backend.plugin.code_generator.utils.format_code import format_python_code
from backend.plugin.code_generator.utils.gen_template import SCOPE_LABELS, SCOPE_ROUTER_VAR, gen_template
from backend.plugin.code_generator.utils.type_conversion import sql_type_to_pydantic
from backend.utils.locks import acquire_distributed_reload_lock


class GenService:
    """代码生成服务类"""

    @staticmethod
    async def get_tables(*, db: AsyncSession, table_schema: str) -> Sequence[RowMapping]:
        """
        获取指定 schema 下的所有表名

        :param db: 数据库会话
        :param table_schema: 数据库 schema 名称
        :return:
        """
        return await gen_dao.get_all_tables(db, table_schema)

    @staticmethod
    async def import_business_and_model(*, db: AsyncSession, obj: ImportParam) -> None:
        """
        导入业务和模型列数据

        :param db: 数据库会话
        :param obj: 导入参数对象
        :return:
        """
        if settings.ENVIRONMENT != 'dev':
            raise errors.ForbiddenError(msg='禁止在非开发环境下导入代码生成业务')

        table_info = await gen_dao.get_table(db, obj.table_schema, obj.table_name)
        if not table_info:
            raise errors.NotFoundError(msg='数据库表不存在')

        business_info = await gen_business_dao.get_by_name(db, obj.table_name)
        if business_info:
            raise errors.ConflictError(msg='已存在相同数据库表业务')

        table_name = table_info['table_name']
        doc_comment = (
            table_info['table_comment'][:-1]
            if table_info['table_comment'][-1] == '表'
            else table_info['table_comment'] or table_name.split('_')[-1]
        )
        new_business = GenBusiness(
            **CreateGenBusinessParam(
                app_name=obj.app,
                table_name=table_name,
                doc_comment=doc_comment,
                table_comment=table_info['table_comment'],
                class_name=to_pascal(table_name),
                schema_name=to_pascal(table_name),
                filename=table_name,
                tag=doc_comment,
                api_scope=getattr(obj, 'api_scope', 'admin'),
            ).model_dump(),
        )
        db.add(new_business)
        await db.flush()

        column_info = await gen_dao.get_all_columns(db, obj.table_schema, table_name)
        for column in column_info:
            column_type = column['column_type'].split('(')[0].upper()
            pd_type = sql_type_to_pydantic(column_type)
            await gen_column_dao.create(
                db,
                CreateGenColumnParam(
                    name=column['column_name'],
                    comment=column['column_comment'],
                    type=column_type,
                    sort=column['sort'],
                    length=column['column_type'].split('(')[1][:-1]
                    if pd_type == 'str' and '(' in column['column_type']
                    else 0,
                    is_pk=column['is_pk'],
                    is_nullable=column['is_nullable'],
                    gen_business_id=new_business.id,
                ),
                pd_type=pd_type,
            )

    @staticmethod
    async def _render_tpl_code(*, db: AsyncSession, business: GenBusiness) -> dict[str, str]:
        """
        渲染模板代码

        :param db: 数据库会话
        :param business: 业务对象
        :return:
        """
        gen_models = await gen_column_service.get_columns(db=db, business_id=business.id)
        if not gen_models:
            raise errors.NotFoundError(msg='代码生成模型表为空')

        gen_vars = gen_template.get_vars(business, gen_models)
        template_mapping = gen_template.get_template_path_mapping(business)

        rendered_codes = {}
        for template_path, output_path in template_mapping.items():
            # 跳过 router.py，由单独的方法处理
            if output_path.endswith('/router.py'):
                continue
            code = await gen_template.get_template(template_path).render_async(**gen_vars)
            if output_path.endswith('.py'):
                code = await format_python_code(code)
            rendered_codes[output_path] = code

        return rendered_codes

    @staticmethod
    async def _update_app_router(*, business: GenBusiness, app_path: str) -> str:  # ruff:ignore[complex-structure]
        """
        更新应用路由文件（追加而非覆盖），支持多 scope 路由

        :param business: 业务对象
        :param app_path: 应用代码路径
        :return: 生成的路由内容
        """
        router_file = anyio.Path(app_path) / business.app_name / 'api' / 'router.py'
        scopes = gen_template._parse_scopes(business)

        # 检查文件是否存在
        if await router_file.exists():
            # 读取现有内容
            async with await open_file(router_file, 'r', encoding='utf-8') as f:
                existing_content = await f.read()

            modified = False

            for scope in scopes:
                # 检查该 scope 的 import 是否已存在
                import_line = (
                    f'from backend.app.{business.app_name}.api.{business.api_version}'
                    f'.{scope}.{business.filename} import router as {scope}_{business.table_name}_router'
                )
                if import_line in existing_content:
                    continue

                # 需要追加新的 import 和 include_router
                modified = True
                lines = existing_content.split('\n')

                # 找到该 scope 对应的 section
                scope_router_var = SCOPE_ROUTER_VAR[scope]
                scope_label = SCOPE_LABELS[scope]

                # 查找该 scope 的 section 是否存在
                section_exists = False
                section_end_idx = len(lines)

                for i, line in enumerate(lines):
                    # 查找该 scope 的路由变量定义
                    if f'{scope_router_var} = APIRouter(' in line:
                        section_exists = True
                    # 查找该 scope 的最后一个 include_router
                    if section_exists and f'{scope_router_var}.include_router(' in line:
                        section_end_idx = i + 1

                if section_exists:
                    # 在该 scope section 中追加 import 和 include_router
                    # 先找到正确的 import 位置（在该 scope 的 注释之后的 import 区域）
                    import_insert_idx = 0
                    for i, line in enumerate(lines):
                        if f'from backend.app.{business.app_name}.api.{business.api_version}.{scope}.' in line:
                            import_insert_idx = i + 1

                    if import_insert_idx == 0:
                        # 没找到现有的 scope import，找到 import 区域末尾
                        for i, line in enumerate(lines):
                            if line.startswith('from backend.app.'):
                                import_insert_idx = i + 1

                    lines.insert(import_insert_idx, import_line)

                    # 追加 include_router（位置要在 section_end_idx 之后，因为插入了一行 import）
                    prefix = f"/{business.table_name.replace('_', '/')}s"
                    include_line_str = (
                        f'{scope_router_var}.include_router('
                        f"{scope}_{business.table_name}_router, prefix='{prefix}')"
                    )
                    if business.tag:
                        include_line_str = (
                            f'{scope_router_var}.include_router('
                            f"{scope}_{business.table_name}_router, prefix='{prefix}', "
                            f"tags=['{business.tag}-{business.doc_comment}'])"
                        )
                    lines.insert(section_end_idx + 1, include_line_str)  # +1 because we already inserted import above
                else:
                    # 该 scope 的 section 不存在，在文件末尾追加新 section
                    lines.append('')
                    lines.append(f'# --- {scope_label} ---')
                    lines.append(import_line)
                    lines.append('')

                    if scope == 'admin':
                        var_def = f"v1 = APIRouter(prefix=f'{{settings.FASTAPI_API_V1_PATH}}/{business.app_name}'"
                    elif scope == 'app':
                        var_def = f"app = APIRouter(prefix=f'{{settings.FASTAPI_API_V1_PATH}}/{business.app_name}/app'"
                    elif scope == 'open':
                        var_def = (
                            "open_api = APIRouter(prefix=f'"
                            f"{{settings.FASTAPI_API_V1_PATH}}/{business.app_name}/open'"
                        )
                    elif scope == 'agent':
                        var_def = (
                            "agent = APIRouter(prefix=f'"
                            f"{{settings.FASTAPI_API_V1_PATH}}/{business.app_name}/agent'"
                        )
                    else:
                        continue

                    if business.tag:
                        var_def += f", tags=['{business.tag}']"
                    var_def += ')'
                    lines.append(var_def)
                    lines.append('')

                    prefix = f"/{business.table_name.replace('_', '/')}s"
                    include_line_str = (
                        f'{scope_router_var}.include_router('
                        f"{scope}_{business.table_name}_router, prefix='{prefix}')"
                    )
                    if business.tag:
                        include_line_str = (
                            f'{scope_router_var}.include_router('
                            f"{scope}_{business.table_name}_router, prefix='{prefix}', "
                            f"tags=['{business.tag}-{business.doc_comment}'])"
                        )
                    lines.append(include_line_str)

                existing_content = '\n'.join(lines)

            if not modified:
                return existing_content

            content = existing_content
        else:
            # 文件不存在，使用模板生成新的路由文件
            gen_vars = gen_template.get_vars(business, [])
            content = await gen_template.get_template('python/router.jinja').render_async(**gen_vars)

        # 格式化代码
        content = await format_python_code(content)

        # 写入文件
        await router_file.parent.mkdir(parents=True, exist_ok=True)
        async with await open_file(router_file, 'w', encoding='utf-8') as f:
            await f.write(content)

        return content

    @staticmethod
    async def _inject_app_router(  # ruff:ignore[complex-structure]
        *, app_name: str, scopes: list[str] | None = None, write: bool = True
    ) -> str | None:
        """
        注入应用路由到全局router.py，支持多 scope 路由

        :param app_name: 应用名称
        :param scopes: API scope 列表
        :param write: 是否写入文件
        :return:
        """
        app_root_router = BASE_PATH / 'app' / 'router.py'

        async with await open_file(app_root_router, 'r', encoding='utf-8') as f:
            content = await f.read()

        if scopes is None:
            scopes = ['admin']

        modified = False

        for scope in scopes:
            SCOPE_ROUTER_VAR[scope]

            # 构建 import 和 include 行
            if scope == 'admin':
                import_var = f'{app_name}_v1'
                import_line = f'from backend.app.{app_name}.api.router import v1 as {import_var}'
                include_line = f'router.include_router({import_var})'
            elif scope == 'app':
                import_var = f'{app_name}_app'
                import_line = f'from backend.app.{app_name}.api.router import app as {import_var}'
                include_line = f'router.include_router({import_var})'
            elif scope == 'open':
                import_var = f'{app_name}_open'
                import_line = f'from backend.app.{app_name}.api.router import open_api as {import_var}'
                include_line = f'router.include_router({import_var})'
            elif scope == 'agent':
                import_var = f'{app_name}_agent'
                import_line = f'from backend.app.{app_name}.api.router import agent as {import_var}'
                include_line = f'router.include_router({import_var})'
            else:
                continue

            has_import = import_line in content
            has_include = include_line in content

            if has_import and has_include:
                continue

            modified = True
            if not has_import:
                # 尝试合并到同一行的 import（检查是否已有该 app 的 import）
                existing_import_prefix = f'from backend.app.{app_name}.api.router import '
                if existing_import_prefix in content:
                    # 尝试追加到现有 import 行
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if line.startswith(existing_import_prefix):
                            # 追加新的变量到该 import 行
                            if scope == 'admin':
                                addition = f'v1 as {import_var}'
                            elif scope == 'app':
                                addition = f'app as {import_var}'
                            elif scope == 'open':
                                addition = f'open_api as {import_var}'
                            elif scope == 'agent':
                                addition = f'agent as {import_var}'
                            else:
                                continue
                            lines[i] = line.rstrip() + f', {addition}'
                            break
                    content = '\n'.join(lines)
                else:
                    content = f'{import_line}\n{content}'

            if not has_include:
                content = f'{content}\n{include_line}'

        if not modified:
            return None

        content = await format_python_code(content)

        if write:
            async with await open_file(app_root_router, 'w', encoding='utf-8') as f:
                await f.write(content)

        return content

    async def preview(self, *, db: AsyncSession, pk: int) -> dict[str, bytes]:
        """
        预览生成的代码

        :param db: 数据库会话
        :param pk: 业务 ID
        :return:
        """
        business = await gen_business_dao.get(db, pk)
        if not business:
            raise errors.NotFoundError(msg='业务不存在')

        codes = {}
        backend_path = 'fastapi_best_architecture/backend/app/'

        init_files = gen_template.get_init_files(business)
        for filepath, content in init_files.items():
            codes[f'{backend_path}{filepath}'] = content.encode('utf-8')

        rendered_codes = await self._render_tpl_code(db=db, business=business)
        for filepath, code in rendered_codes.items():
            codes[f'{backend_path}{filepath}'] = code.encode('utf-8')

        scopes = gen_template._parse_scopes(business)
        app_router_content = await self._inject_app_router(
            app_name=business.app_name, scopes=scopes, write=False
        )
        if app_router_content:
            codes[f'{backend_path}router.py'] = app_router_content.encode('utf-8')

        return codes

    @staticmethod
    async def get_generate_path(*, db: AsyncSession, pk: int) -> list[str]:
        """
        获取代码生成路径

        :param db: 数据库会话
        :param pk: 业务 ID
        :return:
        """
        business = await gen_business_dao.get(db, pk)
        if not business:
            raise errors.NotFoundError(msg='业务不存在')

        gen_path = business.gen_path or '<project_root>/backend/app'
        paths: list[str] = []

        init_files = gen_template.get_init_files(business)
        paths.extend(os.path.join(gen_path, *filepath.split('/')) for filepath in init_files.keys())

        template_mapping = gen_template.get_template_path_mapping(business)
        paths.extend(os.path.join(gen_path, *filepath.split('/')) for filepath in template_mapping.values())

        return paths

    async def _write_init_file(self, filepath: str, new_content: str, gen_path: str) -> None:
        """
        写入 __init__.py 文件（追加模式）

        :param filepath: 相对文件路径
        :param new_content: 要写入的新内容
        :param gen_path: 生成路径
        """
        full_path = anyio.Path(gen_path) / filepath

        # 确保目录存在
        await full_path.parent.mkdir(parents=True, exist_ok=True)

        # 如果文件存在且有新内容，追加而不是覆盖
        if await full_path.exists() and new_content.strip():
            async with await open_file(full_path, 'r', encoding='utf-8') as f:
                existing_content = await f.read()

            # 检查新内容是否已存在
            if new_content.strip() not in existing_content:
                # 追加新内容
                combined_content = existing_content.rstrip() + '\n' + new_content
                async with await open_file(full_path, 'w', encoding='utf-8') as f:
                    await f.write(combined_content)
        else:
            # 文件不存在或内容为空，直接写入
            async with await open_file(full_path, 'w', encoding='utf-8') as f:
                await f.write(new_content)

    async def generate(self, *, db: AsyncSession, pk: int) -> str:
        """
        生成代码文件

        :param db: 数据库会话
        :param pk: 业务 ID
        :return:
        """
        if settings.ENVIRONMENT != 'dev':
            raise errors.ForbiddenError(msg='禁止在非开发环境下生成代码')

        business = await gen_business_dao.get(db, pk)
        if not business:
            raise errors.NotFoundError(msg='业务不存在')

        gen_path = business.gen_path or str(BASE_PATH / 'app')
        scopes = gen_template._parse_scopes(business)

        async with acquire_distributed_reload_lock():
            # 先处理 __init__.py 文件（追加模式）
            init_files = gen_template.get_init_files(business)
            for filepath, content in init_files.items():
                await self._write_init_file(filepath, content, gen_path)

            # 处理其他生成的代码文件（存在则跳过）
            rendered_codes = await self._render_tpl_code(db=db, business=business)
            for filepath, content in rendered_codes.items():
                full_path = anyio.Path(gen_path) / filepath

                # 如果文件已存在，跳过
                if await full_path.exists():
                    continue

                # 确保目录存在
                await full_path.parent.mkdir(parents=True, exist_ok=True)

                # 写入文件
                async with await open_file(full_path, 'w', encoding='utf-8') as f:
                    await f.write(content)

            # 更新应用级路由（追加模式）
            await self._update_app_router(business=business, app_path=gen_path)
            # 注入全局路由（支持多 scope）
            await self._inject_app_router(app_name=business.app_name, scopes=scopes)

        return gen_path

    async def download(self, *, db: AsyncSession, pk: int) -> io.BytesIO:
        """
        下载生成的代码

        :param db: 数据库会话
        :param pk: 业务 ID
        :return:
        """
        business = await gen_business_dao.get(db, pk)
        if not business:
            raise errors.NotFoundError(msg='业务不存在')

        all_files = {}
        init_files = gen_template.get_init_files(business)
        all_files.update(init_files)
        rendered_codes = await self._render_tpl_code(db=db, business=business)
        all_files.update(rendered_codes)

        scopes = gen_template._parse_scopes(business)
        app_router_content = await self._inject_app_router(
            app_name=business.app_name, scopes=scopes, write=False
        )
        if app_router_content:
            all_files['router.py'] = app_router_content

        bio = io.BytesIO()
        with zipfile.ZipFile(bio, 'w') as zf:
            for filepath, content in all_files.items():
                zf.writestr(filepath, content)

        bio.seek(0)
        return bio


gen_service: GenService = GenService()
