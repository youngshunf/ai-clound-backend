"""Dictionary SQL generator for code generator."""

import re
from pathlib import Path

from pydantic.alias_generators import to_pascal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.plugin.code_generator.config_loader import codegen_config
from backend.plugin.code_generator.parser.sql_parser import TableInfo


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
        dict_type_name = column.comment or column.name

        # 确定使用哪个默认选项
        if 'status' in column.name.lower() or 'state' in column.name.lower():
            options = codegen_config.default_status_options
        elif 'type' in column.name.lower():
            options = codegen_config.default_type_options
        else:
            # 使用通用选项
            options = [
                {'label': '选项1', 'value': 1, 'color': 'blue'},
                {'label': '选项2', 'value': 2, 'color': 'green'},
            ]

        sql_lines.extend([
            f'-- {dict_type_name} 字典类型',
            'INSERT INTO sys_dict_type (name, code, status, remark, created_time, updated_time)',
            'VALUES',
            f"('{dict_type_name}', '{dict_type_code}', 1, '{table_info.comment or app}模块-{dict_type_name}', NOW(), NULL)",
            'ON CONFLICT (code) DO NOTHING;',
            '',
            f'-- {dict_type_name} 字典数据',
        ])

        # 获取刚插入的字典类型 ID
        if table_info.dialect.value == 'mysql':
            sql_lines.append('SET @dict_type_id = (SELECT id FROM sys_dict_type WHERE code = '
                           f"'{dict_type_code}' ORDER BY id DESC LIMIT 1);")
            type_id_placeholder = '@dict_type_id'
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
            type_id_placeholder = 'v_dict_type_id'

        # 插入字典数据选项
        for idx, option in enumerate(options):
            label = option['label']
            value = option['value']
            color = option.get('color', 'blue')

            if table_info.dialect.value == 'mysql':
                sql_lines.append(
                    'INSERT INTO sys_dict_data (label, value, sort, status, color_type, '
                    'type_id, remark, created_time, updated_time)'
                )
                sql_lines.append('VALUES')
                sql_lines.append(
                    f"('{label}', '{value}', {idx + 1}, 1, '{color}', "
                    f"{type_id_placeholder}, '', NOW(), NULL);"
                )
            else:
                sql_lines.append(
                    '    INSERT INTO sys_dict_data (label, value, sort, status, color_type, '
                    'type_id, remark, created_time, updated_time)'
                )
                sql_lines.append('    VALUES')
                sql_lines.append(
                    f"    ('{label}', '{value}', {idx + 1}, 1, '{color}', "
                    f"{type_id_placeholder}, '', NOW(), NULL);"
                )

        if table_info.dialect.value == 'postgresql':
            sql_lines.extend(['END $$;', ''])
        else:
            sql_lines.append('')

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
