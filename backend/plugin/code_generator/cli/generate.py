"""Simplified CLI for complete code generation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cappa

from sqlalchemy import text

from backend.common.exception.errors import BaseExceptionError
from backend.database.db import async_db_session
from backend.plugin.code_generator.config_loader import codegen_config
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.crud.crud_gen import gen_dao
from backend.plugin.code_generator.frontend.dict_generator import (
    execute_dict_sql,
    generate_dict_sql_from_db,
    save_dict_sql_to_file,
)
from backend.plugin.code_generator.frontend.generator import frontend_generator
from backend.plugin.code_generator.frontend.menu_generator import (
    execute_menu_sql,
    generate_menu_sql_from_db,
    save_menu_sql_to_file,
)
from backend.plugin.code_generator.parser.sql_parser import sql_parser
from backend.plugin.code_generator.schema.business import UpdateGenBusinessParam
from backend.plugin.code_generator.schema.gen import ImportParam
from backend.plugin.code_generator.service.gen_service import gen_service
from backend.utils.console import console


@cappa.command(help='一键生成完整的前后端代码、菜单SQL和字典SQL', default_long=True)
@dataclass
class Generate:
    """一键生成完整的前后端代码、菜单SQL和字典SQL（使用配置文件）"""

    sql_file: Annotated[
        Path,
        cappa.Arg(help='SQL文件路径'),
    ]
    app: Annotated[
        str,
        cappa.Arg(help='应用/模块名称'),
    ]
    module: Annotated[
        str | None,
        cappa.Arg(help='子模块名称（可选，默认从表名推导）'),
    ] = None
    execute: Annotated[
        bool,
        cappa.Arg(default=False, help='自动执行生成的SQL（菜单和字典）'),
    ] = False
    schema: Annotated[
        str,
        cappa.Arg(default='public', help='数据库schema（默认public）'),
    ] = 'public'

    def __post_init__(self):
        """验证参数"""
        if not self.sql_file.exists():
            raise cappa.Exit(f'SQL文件不存在: {self.sql_file}', code=1)

    async def __call__(self) -> None:
        """执行完整代码生成"""
        try:
            console.print('\n[bold cyan]═══════════════════════════════════════════════[/]')
            console.print('[bold cyan]  一键代码生成器 - FastAPI Best Architecture[/]')
            console.print('[bold cyan]═══════════════════════════════════════════════[/]\n')

            # 解析 SQL
            console.print('[bold white]📄 解析SQL文件...[/]')
            sql_content = self.sql_file.read_text(encoding='utf-8')
            table_info = sql_parser.parse(sql_content)
            console.print(f'   ✓ 表名: [cyan]{table_info.name}[/]')
            console.print(f'   ✓ 注释: [cyan]{table_info.comment or "无"}[/]')
            console.print(f'   ✓ 字段数: [cyan]{len(table_info.columns)}[/]')
            console.print(f'   ✓ 数据库: [cyan]{table_info.dialect.value}[/]\n')

            # 使用配置文件的设置
            if not self.module:
                self.module = table_info.name  # keep underscores for consistent directory naming

            # 步骤0: 检查表是否存在于数据库，不存在则自动执行建表SQL
            console.print('\n[bold white]🔍 步骤 0/4: 检查并创建数据库表...[/]')
            async with async_db_session() as db:
                db_table_info = await gen_dao.get_table(db, self.schema, table_info.name)

            if not db_table_info:
                console.print(f'   ⚠ 表 [yellow]{table_info.name}[/] 不存在，准备自动执行SQL建表...')
                async with async_db_session.begin() as db:
                    # 分割多条语句执行
                    for stmt in sql_content.split(';'):
                        if stmt.strip():
                            await db.execute(text(stmt))
                console.print('   [green]✓ 建表SQL执行成功[/]')
            else:
                console.print(f'   ✓ 表 [cyan]{table_info.name}[/] 已存在，无需重复建表')

            # 导入表元数据到 gen_business / gen_column
            console.print('\n[bold white]📥 步骤 0.5/4: 导入表元数据...[/]')
            business_id = None
            try:
                async with async_db_session() as db:
                    existing_business = await gen_business_dao.get_by_name(db, table_info.name)

                if existing_business:
                    business_id = existing_business.id
                    if existing_business.app_name != self.app:
                        update_obj = UpdateGenBusinessParam.model_validate(
                            existing_business,
                            from_attributes=True,
                        ).model_copy(update={'app_name': self.app})
                        async with async_db_session.begin() as db:
                            await gen_business_dao.update(db, business_id, update_obj)
                        console.print(f'   ✓ 表元数据已存在 (id={business_id})，app 已更新为 [cyan]{self.app}[/]')
                    else:
                        console.print(f'   ✓ 表元数据已存在 (id={business_id})')
                else:
                    import_param = ImportParam(
                        app=self.app,
                        table_schema=self.schema,
                        table_name=table_info.name,
                    )
                    async with async_db_session.begin() as db:
                        await gen_service.import_business_and_model(db=db, obj=import_param)

                    async with async_db_session() as db:
                        business = await gen_business_dao.get_by_name(db, table_info.name)
                        business_id = business.id if business else None

                    if business_id:
                        console.print(f'   [green]✓ 表元数据导入成功 (id={business_id})[/]')
                    else:
                        raise BaseExceptionError(msg='表元数据导入失败')
            except Exception as e:
                raise BaseExceptionError(msg=f'导入表元数据失败: {e}') from e

            if business_id is None:
                raise BaseExceptionError(msg='表元数据缺少有效 ID')

            # 步骤1: 生成前端代码
            if codegen_config.generate_frontend:
                console.print('\n[bold white]🎨 步骤 1/4: 生成前端代码...[/]')
                await self._generate_frontend(business_id, table_info.name)

            # 步骤2: 生成后端代码
            if codegen_config.generate_backend:
                console.print('\n[bold white]🔧 步骤 2/4: 生成后端代码...[/]')
                from backend.plugin.code_generator.path_conf import JINJA2_TEMPLATE_DIR
                python_template_dir = JINJA2_TEMPLATE_DIR / 'python'
                has_python_templates = python_template_dir.exists() and any(python_template_dir.glob('*.jinja'))

                if not has_python_templates:
                    console.print('   [yellow]⚠ 后端代码模板不存在，跳过[/]')
                else:
                    try:
                        async with async_db_session.begin() as db:
                            await gen_service.generate(db=db, pk=business_id)
                        console.print('   [green]✓ 后端代码生成成功[/]')
                    except Exception as e:
                        console.print(f'   [red]✗ 后端代码生成失败: {e}[/]')
                        raise

            # 步骤3: 生成菜单SQL
            if codegen_config.generate_menu_sql:
                console.print('\n[bold white]📋 步骤 3/4: 生成菜单SQL...[/]')
                await self._generate_menu_sql(business_id, table_info.name)

            # 步骤4: 生成字典SQL
            if codegen_config.generate_dict_sql:
                console.print('\n[bold white]📚 步骤 4/4: 生成字典SQL...[/]')
                await self._generate_dict_sql(business_id, table_info.name)

            console.print('\n[bold green]✨ 代码生成完成！[/]\n')
            console.print('[bold cyan]═══════════════════════════════════════════════[/]')
            console.print('[bold yellow]📝 提示：[/]')
            console.print(f'   • 配置文件: [cyan]{codegen_config.CONFIG_FILE}[/]')
            console.print(f'   • 前端目录: [cyan]{codegen_config.frontend_dir}[/]')
            console.print(f'   • 菜单SQL: [cyan]{codegen_config.menu_sql_dir}[/]')
            console.print(f'   • 字典SQL: [cyan]{codegen_config.dict_sql_dir}[/]')
            console.print('[bold cyan]═══════════════════════════════════════════════[/]\n')

        except Exception as e:
            raise cappa.Exit(e.msg if isinstance(e, BaseExceptionError) else str(e), code=1)

    async def _generate_frontend(self, business_id: int, table_name: str) -> None:
        """生成前端代码"""
        try:
            force = codegen_config.existing_file_behavior == 'overwrite'
            await frontend_generator.generate_from_db(
                business_id=business_id,
                app=self.app,
                module=table_name,
                output_dir=codegen_config.frontend_dir,
                force=force,
            )
            console.print('   [green]✓ 前端代码生成成功[/]')
        except Exception as e:
            console.print(f'   [red]✗ 前端代码生成失败: {e}[/]')
            raise

    async def _generate_menu_sql(self, business_id: int, table_name: str) -> None:
        """生成菜单SQL"""
        try:
            menu_sql = await generate_menu_sql_from_db(
                business_id=business_id,
                app=self.app,
                module=table_name,
            )

            # 保存到文件
            output_file = codegen_config.menu_sql_dir / f'{table_name}_menu.sql'
            await save_menu_sql_to_file(menu_sql, output_file)
            console.print(f'   ✓ 菜单SQL已保存: [cyan]{output_file}[/]')

            # 自动执行SQL
            auto_execute = self.execute or codegen_config.auto_execute_menu_sql
            if auto_execute:
                async with async_db_session.begin() as db:
                    await execute_menu_sql(menu_sql, db)
                console.print('   [green]✓ 菜单SQL已执行[/]')
        except Exception as e:
            console.print(f'   [red]✗ 菜单SQL生成失败: {e}[/]')
            raise

    async def _generate_dict_sql(self, business_id: int, table_name: str) -> None:
        """生成字典SQL"""
        try:
            dict_sql = await generate_dict_sql_from_db(
                business_id=business_id,
                app=self.app,
            )

            if not dict_sql:
                console.print('   [yellow]⚠ 未找到需要生成字典的字段[/]')
                return

            # 保存到文件
            output_file = codegen_config.dict_sql_dir / f'{table_name}_dict.sql'
            await save_dict_sql_to_file(dict_sql, output_file)
            console.print(f'   ✓ 字典SQL已保存: [cyan]{output_file}[/]')

            # 自动执行SQL
            auto_execute = self.execute or codegen_config.auto_execute_dict_sql
            if auto_execute:
                async with async_db_session.begin() as db:
                    await execute_dict_sql(dict_sql, db)
                console.print('   [green]✓ 字典SQL已执行[/]')
        except Exception as e:
            console.print(f'   [yellow]⚠ 字典SQL生成失败: {e}[/]')
