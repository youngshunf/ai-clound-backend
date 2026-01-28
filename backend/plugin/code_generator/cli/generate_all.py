"""一键生成所有代码：前端+后端+菜单SQL+字典SQL"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cappa

from backend.common.exception.errors import BaseExceptionError
from backend.database.db import async_db_session
from backend.plugin.code_generator.config_loader import codegen_config
from backend.plugin.code_generator.frontend.dict_generator import generate_dict_sql
from backend.plugin.code_generator.frontend.generator import frontend_generator
from backend.plugin.code_generator.frontend.menu_generator import (
    execute_menu_sql,
    generate_menu_sql,
    save_menu_sql_to_file,
)
from backend.plugin.code_generator.parser.sql_parser import sql_parser
from backend.plugin.code_generator.schema.gen import ImportParam
from backend.plugin.code_generator.service.gen_service import gen_service
from backend.utils.console import console


@cappa.command(name='generate', help='一键生成前后端代码、菜单SQL和字典SQL', default_long=True)
@dataclass
class GenerateAll:
    """一键生成前后端代码、菜单SQL和字典SQL"""

    sql_file: Annotated[
        Path,
        cappa.Arg(help='SQL文件路径'),
    ]
    app: Annotated[
        str,
        cappa.Arg(help='应用名称（例如：admin）'),
    ]
    execute: Annotated[
        bool,
        cappa.Arg(default=False, help='自动执行菜单SQL和字典SQL到数据库'),
    ] = False

    def __post_init__(self):
        """验证参数"""
        if not self.sql_file.exists():
            raise cappa.Exit(f'SQL文件不存在: {self.sql_file}', code=1)

    async def __call__(self) -> None:
        """执行一键代码生成"""
        try:
            # 打印标题
            print('\n' + '=' * 60, flush=True)
            print('  一键代码生成器 - FastAPI Best Architecture', flush=True)
            print('=' * 60 + '\n', flush=True)

            # 解析SQL文件
            print('📄 步骤 1/5: 解析SQL文件...', flush=True)
            sql_content = self.sql_file.read_text(encoding='utf-8')
            all_tables = sql_parser.parse_all(sql_content)
            
            if not all_tables:
                raise cappa.Exit('未找到有效的CREATE TABLE语句', code=1)
            
            print(f'   ✓ 找到 {len(all_tables)} 个表', flush=True)
            for table in all_tables:
                print(f'      - {table.name} ({len(table.columns)} 字段)', flush=True)

            # 检查是否存在 Python 模板文件
            from backend.plugin.code_generator.path_conf import JINJA2_TEMPLATE_DIR
            python_template_dir = JINJA2_TEMPLATE_DIR / 'python'
            has_python_templates = python_template_dir.exists() and any(python_template_dir.glob('*.jinja'))
            
            # 记录生成的文件
            generated_tables = []
            
            # 循环处理每个表
            for idx, table_info in enumerate(all_tables, 1):
                table_name = table_info.name
                print(f'\n{"=" * 60}', flush=True)
                print(f'📁 处理表 {idx}/{len(all_tables)}: {table_name}', flush=True)
                print(f'{"=" * 60}', flush=True)
                
                # 1. 生成前端代码
                print('\n🎨 生成前端代码...', flush=True)
                try:
                    await frontend_generator.generate_from_table_info(
                        table_info=table_info,
                        app=self.app,
                        module=table_name,
                        output_dir=codegen_config.frontend_dir,
                        force=codegen_config.existing_file_behavior == 'overwrite',
                    )
                    print('   ✓ 前端代码生成成功', flush=True)
                except Exception as e:
                    print(f'   ⚠ 前端代码生成失败: {str(e)}', flush=True)

                # 2. 生成后端代码
                print('\n🔧 生成后端代码...', flush=True)
                if not has_python_templates:
                    print('   ⚠ 后端代码模板不存在，跳过', flush=True)
                else:
                    try:
                        from backend.plugin.code_generator.crud.crud_business import gen_business_dao
                        
                        # 检查是否已存在该表的业务记录
                        async with async_db_session() as db:
                            existing_business = await gen_business_dao.get_by_name(db, table_name)
                        
                        if existing_business:
                            # 直接生成代码
                            async with async_db_session.begin() as db:
                                gen_path = await gen_service.generate(db=db, pk=existing_business.id)
                            print(f'   ✓ 后端代码生成成功', flush=True)
                        else:
                            # 导入表信息到数据库
                            import_param = ImportParam(
                                app=self.app,
                                table_schema=codegen_config.default_db_schema,
                                table_name=table_name,
                            )
                            async with async_db_session.begin() as db:
                                await gen_service.import_business_and_model(db=db, obj=import_param)
                            
                            # 获取刚导入的业务记录并生成代码
                            async with async_db_session() as db:
                                business = await gen_business_dao.get_by_name(db, table_name)
                                if business:
                                    async with async_db_session.begin() as db2:
                                        gen_path = await gen_service.generate(db=db2, pk=business.id)
                                    print(f'   ✓ 后端代码生成成功', flush=True)
                                else:
                                    print(f'   ⚠ 导入业务记录失败', flush=True)
                    except Exception as e:
                        print(f'   ⚠ 后端代码生成失败: {str(e)}', flush=True)

                # 3. 生成菜单SQL
                print('\n📋 生成菜单SQL...', flush=True)
                try:
                    menu_sql = await generate_menu_sql(
                        table_info=table_info,
                        app=self.app,
                        module=table_name,
                    )
                    menu_sql_file = codegen_config.menu_sql_dir / f'{table_name}_menu.sql'
                    await save_menu_sql_to_file(menu_sql, menu_sql_file)
                    print(f'   ✓ 菜单SQL已保存: {menu_sql_file}', flush=True)
                    
                    if self.execute or codegen_config.auto_execute_menu_sql:
                        async with async_db_session.begin() as db:
                            await execute_menu_sql(menu_sql, db)
                        print('   ✓ 菜单SQL已执行', flush=True)
                except Exception as e:
                    print(f'   ⚠ 菜单SQL生成失败: {str(e)}', flush=True)

                # 4. 生成字典SQL
                print('\n📚 生成字典SQL...', flush=True)
                try:
                    dict_sql = await generate_dict_sql(
                        table_info=table_info,
                        app=self.app,
                    )
                    
                    if dict_sql:
                        dict_sql_file = codegen_config.dict_sql_dir / f'{table_name}_dict.sql'
                        dict_sql_file.parent.mkdir(parents=True, exist_ok=True)
                        dict_sql_file.write_text(dict_sql, encoding='utf-8')
                        print(f'   ✓ 字典SQL已保存: {dict_sql_file}', flush=True)
                        
                        if self.execute or codegen_config.auto_execute_dict_sql:
                            from backend.plugin.code_generator.frontend.dict_generator import execute_dict_sql
                            async with async_db_session.begin() as db:
                                await execute_dict_sql(dict_sql, db)
                            print('   ✓ 字典SQL已执行', flush=True)
                    else:
                        print('   ⚠ 未找到需要生成字典的字段', flush=True)
                except Exception as e:
                    print(f'   ⚠ 字典SQL生成失败: {str(e)}', flush=True)
                
                generated_tables.append(table_name)

            # 完成
            print('\n' + '=' * 60, flush=True)
            print(f'✨ 代码生成完成！共处理 {len(generated_tables)} 个表', flush=True)
            print('=' * 60 + '\n', flush=True)
            
            print(f'📦 生成的表:', flush=True)
            for tbl in generated_tables:
                print(f'   - {tbl}', flush=True)
            print(f'\n📂 文件位置:', flush=True)
            print(f'   前端: apps/web-antd/src/views/{self.app}/<table_name>/', flush=True)
            print(f'   API:  apps/web-antd/src/api/{self.app}/<table_name>.ts', flush=True)
            print(f'   后端: backend/app/{self.app}/', flush=True)
            print(f'   SQL:  {codegen_config.menu_sql_dir}/', flush=True)
            print(flush=True)

        except KeyboardInterrupt:
            print(f'\n⚠ 用户中断操作', flush=True)
            raise cappa.Exit('用户中断', code=130)
        except Exception as e:
            # 不将错误报告给用户，只记录警告
            error_msg = str(e)
            if 'does not exist' in error_msg or 'UndefinedColumn' in error_msg:
                print(f'\n⚠ 警告: 数据库表结构不匹配，请手动执行SQL文件', flush=True)
            else:
                print(f'\n⚠ 警告: {error_msg}', flush=True)
