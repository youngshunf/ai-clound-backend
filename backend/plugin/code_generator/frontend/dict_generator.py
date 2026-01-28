"""Dictionary SQL generator for code generator."""

import re
from pathlib import Path

from pydantic.alias_generators import to_pascal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.db import async_db_session
from backend.plugin.code_generator.config_loader import codegen_config
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.crud.crud_column import gen_column_dao
from backend.plugin.code_generator.model import GenBusiness, GenColumn
from backend.plugin.code_generator.parser.sql_parser import TableInfo


# 默认颜色列表，用于循环分配
DEFAULT_COLORS = ['blue', 'green', 'orange', 'red', 'purple', 'cyan', 'pink', 'yellow']


def parse_dict_options_from_comment(comment: str) -> tuple[str, list[dict] | None]:
    """
    从字段注释中解析字典枚举值。
    
    支持格式：
    - "状态 (active:激活/inactive:未激活/expired:过期)"
    - "状态 (active:激活:green/inactive:未激活:gray/expired:过期:red)"
    - "状态（active:激活/inactive:未激活）"  # 中文括号
    
    :param comment: 字段注释
    :return: (简化标签, 枚举选项列表 或 None)
    """
    if not comment:
        return comment or '', None
    
    # 匹配括号内容：支持中英文括号
    match = re.search(r'[\(\uff08]([^\)\uff09]+)[\)\uff09]', comment)
    if not match:
        return comment, None
    
    # 提取简化标签（括号前的部分）
    simple_label = comment[:match.start()].strip()
    enum_str = match.group(1).strip()
    
    # 检查是否包含字典枚举格式（value:label 或 value:label:color）
    if ':' not in enum_str:
        return simple_label or comment, None
    
    # 解析枚举选项
    options = []
    items = enum_str.split('/')
    
    for idx, item in enumerate(items):
        item = item.strip()
        if not item:
            continue
        
        parts = item.split(':')
        if len(parts) < 2:
            continue
        
        value = parts[0].strip()
        label = parts[1].strip()
        color = parts[2].strip() if len(parts) > 2 else DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]
        
        options.append({
            'value': value,
            'label': label,
            'color': color,
        })
    
    if not options:
        return simple_label or comment, None
    
    return simple_label or comment, options


async def generate_dict_sql(
    table_info: TableInfo,
    app: str,
) -> str | None:
    """
    Generate dictionary SQL for fields that match patterns.

    :param table_info: TableInfo object
    :param app: Application name
    :return: SQL string or None if no dict fields found
    """
    from datetime import datetime

    # 查找需要生成字典的字段
    dict_fields = []
    patterns = codegen_config.auto_dict_patterns

    for column in table_info.columns:
        # 跳过主键和时间戳字段
        if column.is_primary_key or column.name.lower() in (
            'created_time',
            'updated_time',
            'created_at',
            'updated_at',
            'deleted_at',
        ):
            continue

        # 检查字段名是否匹配字典模式
        for pattern in patterns:
            if re.search(pattern, column.name.lower()):
                dict_fields.append(column)
                break

    if not dict_fields:
        return None

    # 生成字典 SQL
    sql_lines = [
        '-- =====================================================',
        f'-- {table_info.comment or to_pascal(table_info.name)} 字典数据初始化 SQL',
        f'-- 自动生成于: {datetime.now()}',
        '-- =====================================================',
        '',
    ]

    for column in dict_fields:
        dict_type_code = f'{app}_{column.name}'
        raw_comment = column.comment or column.name
        
        # 从注释中解析字典枚举值
        dict_type_name, parsed_options = parse_dict_options_from_comment(raw_comment)
        
        if parsed_options:
            # 使用注释中定义的枚举值
            options = parsed_options
        elif 'status' in column.name.lower() or 'state' in column.name.lower():
            options = codegen_config.default_status_options
        elif 'type' in column.name.lower():
            options = codegen_config.default_type_options
        else:
            # 使用通用选项
            options = [
                {'label': '选项1', 'value': '1', 'color': 'blue'},
                {'label': '选项2', 'value': '2', 'color': 'green'},
            ]

        sql_lines.extend([
            f'-- {dict_type_name} 字典类型',
            'INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)',
            'VALUES',
            f"('{dict_type_name}', '{dict_type_code}', '{table_info.comment or app}模块-{dict_type_name}', NOW(), NULL)",
            'ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();',
            '',
            f'-- {dict_type_name} 字典数据',
        ])

        # 获取字典类型 ID 并插入字典数据
        if table_info.dialect.value == 'mysql':
            sql_lines.append('SET @dict_type_id = (SELECT id FROM sys_dict_type WHERE code = '
                           f"'{dict_type_code}' ORDER BY id DESC LIMIT 1);")
            type_id_placeholder = '@dict_type_id'
            for idx, option in enumerate(options):
                label = option['label']
                value = option['value']
                color = option.get('color', 'blue')
                sql_lines.append(
                    f"INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time) "
                    f"SELECT '{dict_type_code}', '{label}', '{value}', '{color}', {idx + 1}, 1, {type_id_placeholder}, '', NOW(), NULL "
                    f"WHERE NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = '{dict_type_code}' AND value = '{value}');"
                )
            sql_lines.append('')
        else:
            sql_lines.extend([
                'DO $$',
                'DECLARE',
                '    v_dict_type_id INTEGER;',
                'BEGIN',
                '    SELECT id INTO v_dict_type_id FROM sys_dict_type',
                f"    WHERE code = '{dict_type_code}' ORDER BY id DESC LIMIT 1;",
                '',
            ])
            for idx, option in enumerate(options):
                label = option['label']
                value = option['value']
                color = option.get('color', 'blue')
                sql_lines.extend([
                    f"    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = '{dict_type_code}' AND value = '{value}') THEN",
                    f"        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)",
                    f"        VALUES ('{dict_type_code}', '{label}', '{value}', '{color}', {idx + 1}, 1, v_dict_type_id, '', NOW(), NULL);",
                    '    END IF;',
                ])
            sql_lines.extend(['END $$;', ''])

    sql_lines.extend([
        '-- =====================================================',
        '-- 字典数据生成完成',
        '-- =====================================================',
    ])

    return '\n'.join(sql_lines)


async def generate_dict_sql_from_db(
    business_id: int,
    app: str,
    dialect: str = 'postgresql',
) -> str | None:
    """
    Generate dictionary SQL from gen_business/gen_column tables.

    :param business_id: GenBusiness ID
    :param app: Application name
    :param dialect: Database dialect ('postgresql' or 'mysql')
    :return: SQL string or None if no dict fields found
    """
    from datetime import datetime

    # Query gen_business and gen_column
    async with async_db_session() as db:
        business = await gen_business_dao.get(db, business_id)
        if not business:
            raise ValueError(f'GenBusiness not found: id={business_id}')
        
        columns = await gen_column_dao.get_all_by_business(db, business_id)

    # 查找需要生成字典的字段
    dict_fields = []
    patterns = codegen_config.auto_dict_patterns

    for column in columns:
        # 跳过主键和时间戳字段
        if column.is_pk or column.name.lower() in (
            'created_time',
            'updated_time',
            'created_at',
            'updated_at',
            'deleted_at',
        ):
            continue

        # 检查字段名是否匹配字典模式
        for pattern in patterns:
            if re.search(pattern, column.name.lower()):
                dict_fields.append(column)
                break

    if not dict_fields:
        return None

    table_name = business.table_name
    table_comment = business.table_comment or business.doc_comment or to_pascal(table_name)

    # 生成字典 SQL
    sql_lines = [
        '-- =====================================================',
        f'-- {table_comment} 字典数据初始化 SQL',
        f'-- 自动生成于: {datetime.now()}',
        '-- =====================================================',
        '',
    ]

    for column in dict_fields:
        dict_type_code = f'{app}_{column.name}'
        raw_comment = column.comment or column.name
        
        # 从注释中解析字典枚举值
        dict_type_name, parsed_options = parse_dict_options_from_comment(raw_comment)
        
        if parsed_options:
            # 使用注释中定义的枚举值
            options = parsed_options
        elif 'status' in column.name.lower() or 'state' in column.name.lower():
            options = codegen_config.default_status_options
        elif 'type' in column.name.lower():
            options = codegen_config.default_type_options
        else:
            options = [
                {'label': '选项1', 'value': '1', 'color': 'blue'},
                {'label': '选项2', 'value': '2', 'color': 'green'},
            ]

        sql_lines.extend([
            f'-- {dict_type_name} 字典类型',
            'INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)',
            'VALUES',
            f"('{dict_type_name}', '{dict_type_code}', '{table_comment}模块-{dict_type_name}', NOW(), NULL)",
            'ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();',
            '',
            f'-- {dict_type_name} 字典数据',
        ])

        # 获取字典类型 ID 并插入字典数据
        if dialect == 'mysql':
            sql_lines.append('SET @dict_type_id = (SELECT id FROM sys_dict_type WHERE code = '
                           f"'{dict_type_code}' ORDER BY id DESC LIMIT 1);")
            type_id_placeholder = '@dict_type_id'
            for idx, option in enumerate(options):
                label = option['label']
                value = option['value']
                color = option.get('color', 'blue')
                sql_lines.append(
                    f"INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time) "
                    f"SELECT '{dict_type_code}', '{label}', '{value}', '{color}', {idx + 1}, 1, {type_id_placeholder}, '', NOW(), NULL "
                    f"WHERE NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = '{dict_type_code}' AND value = '{value}');"
                )
            sql_lines.append('')
        else:
            sql_lines.extend([
                'DO $$',
                'DECLARE',
                '    v_dict_type_id INTEGER;',
                'BEGIN',
                '    SELECT id INTO v_dict_type_id FROM sys_dict_type',
                f"    WHERE code = '{dict_type_code}' ORDER BY id DESC LIMIT 1;",
                '',
            ])
            for idx, option in enumerate(options):
                label = option['label']
                value = option['value']
                color = option.get('color', 'blue')
                sql_lines.extend([
                    f"    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = '{dict_type_code}' AND value = '{value}') THEN",
                    f"        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)",
                    f"        VALUES ('{dict_type_code}', '{label}', '{value}', '{color}', {idx + 1}, 1, v_dict_type_id, '', NOW(), NULL);",
                    '    END IF;',
                ])
            sql_lines.extend(['END $$;', ''])

    sql_lines.extend([
        '-- =====================================================',
        '-- 字典数据生成完成',
        '-- =====================================================',
    ])

    return '\n'.join(sql_lines)


async def execute_dict_sql(sql: str, db: AsyncSession) -> None:
    """
    Execute dict SQL directly to database.

    :param sql: SQL string
    :param db: Database session
    """
    # 分割 SQL 语句（处理 DO 块和常规语句）
    statements = []
    current_stmt = []
    in_do_block = False

    for line in sql.split('\n'):
        line_stripped = line.strip()

        # 跳过注释
        if line_stripped.startswith('--'):
            continue

        # 检测 DO 块开始
        if line_stripped.lower().startswith('do $$'):
            in_do_block = True
            current_stmt.append(line)
        # 检测 DO 块结束
        elif in_do_block and 'end $$' in line_stripped.lower():
            current_stmt.append(line)
            statements.append('\n'.join(current_stmt))
            current_stmt = []
            in_do_block = False
        # 常规语句
        elif line_stripped.endswith(';') and not in_do_block:
            current_stmt.append(line)
            statements.append('\n'.join(current_stmt))
            current_stmt = []
        # 语句延续
        elif line_stripped:
            current_stmt.append(line)

    # 添加剩余语句
    if current_stmt:
        statements.append('\n'.join(current_stmt))

    # 执行每个语句
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            await db.execute(text(stmt))

    await db.commit()


async def save_dict_sql_to_file(sql: str, output_path: Path) -> None:
    """
    Save dict SQL to a file.

    :param sql: SQL string
    :param output_path: Output file path
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sql, encoding='utf-8')
