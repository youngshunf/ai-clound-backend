"""Menu SQL generator for frontend CRUD pages."""

from pathlib import Path

from pydantic.alias_generators import to_pascal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.plugin.code_generator.parser.sql_parser import TableInfo
from backend.plugin.code_generator.utils.gen_template import gen_template


async def generate_menu_sql(
    table_info: TableInfo,
    app: str,
    module: str | None = None,
    parent_menu_id: int | None = None,
) -> str:
    """
    Generate menu SQL using existing templates.

    :param table_info: TableInfo object
    :param app: Application name
    :param module: Module name (defaults to table name with dashes)
    :param parent_menu_id: Optional parent menu ID
    :return: SQL string
    """
    # Use module name or derive from table name
    if not module:
        module = table_info.name.replace('_', '-')

    # Prepare template variables
    from datetime import datetime
    class_name = to_pascal(table_info.name)
    vars_dict = {
        'app_name': app,
        'table_name': table_info.name,
        'doc_comment': table_info.comment or class_name,
        'schema_name': class_name,
        'permission': table_info.name.replace('_', ':'),
        'now': datetime.now,  # 提供 now() 函数用于模板
    }

    # If parent_menu_id is provided, modify the template to use it
    if parent_menu_id:
        vars_dict['parent_menu_id'] = parent_menu_id

    # Determine which template to use based on dialect
    if table_info.dialect.value == 'mysql':
        template_path = 'sql/mysql/init.jinja'
    else:
        template_path = 'sql/postgresql/init.jinja'

    # Render template
    template = gen_template.get_template(template_path)
    sql = await template.render_async(**vars_dict)

    return sql


async def execute_menu_sql(sql: str, db: AsyncSession) -> None:
    """
    Execute menu SQL directly to database.

    :param sql: SQL string
    :param db: Database session
    """
    # Split SQL into statements (handle DO blocks and regular statements)
    statements = []
    current_stmt = []
    in_do_block = False

    for line in sql.split('\n'):
        line_stripped = line.strip()

        # Detect DO block start
        if line_stripped.lower().startswith('do $$'):
            in_do_block = True
            current_stmt.append(line)
        # Detect DO block end
        elif in_do_block and line_stripped.startswith('end $$'):
            current_stmt.append(line)
            statements.append('\n'.join(current_stmt))
            current_stmt = []
            in_do_block = False
        # Regular statement
        elif line_stripped.endswith(';') and not in_do_block:
            current_stmt.append(line)
            statements.append('\n'.join(current_stmt))
            current_stmt = []
        # Continuation of current statement
        elif line_stripped:
            current_stmt.append(line)

    # Add any remaining statement
    if current_stmt:
        statements.append('\n'.join(current_stmt))

    # Execute each statement
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            await db.execute(text(stmt))

    await db.commit()


async def find_parent_menu(app: str, db: AsyncSession) -> int | None:
    """
    Find existing parent menu for app.

    :param app: Application name
    :param db: Database session
    :return: Parent menu ID or None
    """
    # Query sys_menu for parent by path pattern
    sql = """
    SELECT id FROM sys_menu
    WHERE path LIKE :path_pattern
    AND type = 0
    ORDER BY id DESC
    LIMIT 1
    """
    result = await db.execute(text(sql), {'path_pattern': f'/{app}%'})
    row = result.fetchone()
    return row[0] if row else None


async def save_menu_sql_to_file(sql: str, output_path: Path) -> None:
    """
    Save menu SQL to a file.

    :param sql: SQL string
    :param output_path: Output file path
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sql, encoding='utf-8')
