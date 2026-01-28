"""一键生成所有代码：前端+后端+菜单SQL+字典SQL

流程：
1. 从数据库读取表信息（需要先执行 SQL 文件创建表）
2. 导入表元数据到 gen_business/gen_column 表
3. 基于 gen_* 表生成前端、后端、菜单SQL、字典SQL
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cappa

from backend.database.db import async_db_session
from backend.plugin.code_generator.config_loader import codegen_config
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.crud.crud_gen import gen_dao
from backend.plugin.code_generator.frontend.dict_generator import generate_dict_sql_from_db
from backend.plugin.code_generator.frontend.generator import frontend_generator
from backend.plugin.code_generator.frontend.menu_generator import (
    execute_menu_sql,
    generate_menu_sql_from_db,
    save_menu_sql_to_file,
)
from backend.plugin.code_generator.schema.gen import ImportParam
from backend.plugin.code_generator.service.gen_service import gen_service


@cappa.command(name='generate', help='一键生成前后端代码、菜单SQL和字典SQL', default_long=True)
@dataclass
class GenerateAll:
    """
    一键生成前后端代码、菜单SQL和字典SQL
    
    前提：SQL文件中的表已在数据库中创建
    流程：导入表元数据 -> 生成前端 -> 生成后端 -> 生成菜单SQL -> 生成字典SQL
    """

    table: Annotated[
        str,
        cappa.Arg(help='表名（支持逗号分隔多个表，如：user,role,menu）'),
    ]
    app: Annotated[
        str,
        cappa.Arg(help='应用名称（例如：admin）'),
    ]
    execute: Annotated[
        bool,
        cappa.Arg(default=False, help='自动执行菜单SQL和字典SQL到数据库'),
    ] = False
    schema: Annotated[
        str,
        cappa.Arg(default='public', help='数据库schema（默认public）'),
    ] = 'public'

    async def __call__(self) -> None:
        """执行一键代码生成"""
        try:
            # 打印标题
            print('\n' + '=' * 60, flush=True)
            print('  一键代码生成器 - FastAPI Best Architecture', flush=True)
            print('=' * 60 + '\n', flush=True)

            # 解析表名列表
            table_names = [t.strip() for t in self.table.split(',') if t.strip()]
            if not table_names:
                raise cappa.Exit('请指定表名', code=1)
            
            print(f'📄 准备处理 {len(table_names)} 个表:', flush=True)
            for t in table_names:
                print(f'   - {t}', flush=True)

            # 检查是否存在 Python 模板文件
            from backend.plugin.code_generator.path_conf import JINJA2_TEMPLATE_DIR
            python_template_dir = JINJA2_TEMPLATE_DIR / 'python'
            has_python_templates = python_template_dir.exists() and any(python_template_dir.glob('*.jinja'))
            
            # 记录生成的文件
            generated_tables = []
            
            # 循环处理每个表
            for idx, table_name in enumerate(table_names, 1):
                print(f'\n{"=" * 60}', flush=True)
                print(f'📁 处理表 {idx}/{len(table_names)}: {table_name}', flush=True)
                print(f'{"=" * 60}', flush=True)
                
                # 步骤1: 检查表是否存在于数据库
                print('\n🔍 检查数据库表...', flush=True)
                async with async_db_session() as db:
                    table_info = await gen_dao.get_table(db, self.schema, table_name)
                
                if not table_info:
                    print(f'   ⚠ 表 {table_name} 不存在于数据库，跳过', flush=True)
                    continue
                print(f'   ✓ 表存在: {table_info["table_comment"] or table_name}', flush=True)
                
                # 步骤2: 导入表元数据到 gen_business/gen_column
                print('\n📥 导入表元数据...', flush=True)
                try:
                    async with async_db_session() as db:
                        existing_business = await gen_business_dao.get_by_name(db, table_name)
                    
                    if existing_business:
                        business_id = existing_business.id
                        # 检查 app_name 是否需要更新
                        if existing_business.app_name != self.app:
                            async with async_db_session.begin() as db:
                                await gen_business_dao.update(db, business_id, {'app_name': self.app})
                            print(f'   ✓ 表元数据已存在 (id={business_id})，app 已更新为 {self.app}', flush=True)
                        else:
                            print(f'   ✓ 表元数据已存在 (id={business_id})', flush=True)
                    else:
                        import_param = ImportParam(
                            app=self.app,
                            table_schema=self.schema,
                            table_name=table_name,
                        )
                        async with async_db_session.begin() as db:
                            await gen_service.import_business_and_model(db=db, obj=import_param)
                        
                        async with async_db_session() as db:
                            business = await gen_business_dao.get_by_name(db, table_name)
                            business_id = business.id if business else None
                        
                        if business_id:
                            print(f'   ✓ 表元数据导入成功 (id={business_id})', flush=True)
                        else:
                            print(f'   ⚠ 表元数据导入失败', flush=True)
                            continue
                except Exception as e:
                    print(f'   ⚠ 导入失败: {str(e)}', flush=True)
                    continue
                
                # 步骤3: 生成前端代码
                print('\n🎨 生成前端代码...', flush=True)
                try:
                    await frontend_generator.generate_from_db(
                        business_id=business_id,
                        app=self.app,
                        module=table_name,
                        output_dir=codegen_config.frontend_dir,
                        force=codegen_config.existing_file_behavior == 'overwrite',
                    )
                    print('   ✓ 前端代码生成成功', flush=True)
                except Exception as e:
                    print(f'   ⚠ 前端代码生成失败: {str(e)}', flush=True)

                # 步骤4: 生成后端代码
                print('\n🔧 生成后端代码...', flush=True)
                if not has_python_templates:
                    print('   ⚠ 后端代码模板不存在，跳过', flush=True)
                else:
                    try:
                        async with async_db_session.begin() as db:
                            gen_path = await gen_service.generate(db=db, pk=business_id)
                        print(f'   ✓ 后端代码生成成功', flush=True)
                    except Exception as e:
                        print(f'   ⚠ 后端代码生成失败: {str(e)}', flush=True)

                # 步骤5: 生成菜单SQL
                print('\n📋 生成菜单SQL...', flush=True)
                try:
                    menu_sql = await generate_menu_sql_from_db(
                        business_id=business_id,
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

                # 步骤6: 生成字典SQL
                print('\n📚 生成字典SQL...', flush=True)
                try:
                    dict_sql = await generate_dict_sql_from_db(
                        business_id=business_id,
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
            
            if generated_tables:
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
        except cappa.Exit:
            raise
        except Exception as e:
            print(f'\n⚠ 错误: {str(e)}', flush=True)
            raise cappa.Exit(str(e), code=1)
