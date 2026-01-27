"""Type mapping utilities for frontend code generation."""

from backend.plugin.code_generator.parser.sql_parser import ColumnInfo


def sql_to_typescript(sql_type: str) -> str:
    """
    Map SQL type to TypeScript type.

    :param sql_type: SQL type string (e.g., "VARCHAR", "INTEGER")
    :return: TypeScript type string
    """
    sql_type_upper = sql_type.upper()

    # String types
    if sql_type_upper in ('VARCHAR', 'CHAR', 'TEXT', 'TINYTEXT', 'MEDIUMTEXT', 'LONGTEXT', 'CHARACTER VARYING', 'CHARACTER'):
        return 'string'

    # Integer types
    if sql_type_upper in ('INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT', 'SERIAL', 'BIGSERIAL', 'SMALLSERIAL'):
        return 'number'

    # Decimal/Float types
    if sql_type_upper in ('DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'REAL', 'DOUBLE PRECISION'):
        return 'number'

    # Boolean types
    if sql_type_upper in ('BOOLEAN', 'BOOL', 'BIT'):
        return 'boolean'

    # Date/Time types
    if sql_type_upper in ('DATE', 'DATETIME', 'TIMESTAMP', 'TIME', 'YEAR', 'TIMESTAMP WITHOUT TIME ZONE', 'TIMESTAMP WITH TIME ZONE'):
        return 'string'  # ISO format string

    # JSON types
    if sql_type_upper in ('JSON', 'JSONB'):
        return 'Record<string, any>'

    # Binary types
    if sql_type_upper in ('BLOB', 'BYTEA', 'BINARY', 'VARBINARY'):
        return 'string'  # Base64 encoded

    # Default to string
    return 'string'


def get_form_component(column: ColumnInfo) -> dict:
    """
    Determine appropriate form component for a column.

    :param column: ColumnInfo object
    :return: Dictionary with component name and props
    """
    column_name_lower = column.name.lower()
    column_type_upper = column.type.upper()

    # Boolean fields -> Switch
    if column_type_upper in ('BOOLEAN', 'BOOL', 'BIT'):
        return {'component': 'Switch', 'props': {}}

    # Date/DateTime fields -> DatePicker
    if column_type_upper in ('DATE', 'DATETIME', 'TIMESTAMP', 'TIMESTAMP WITHOUT TIME ZONE', 'TIMESTAMP WITH TIME ZONE'):
        if column_type_upper == 'DATE':
            return {'component': 'DatePicker', 'props': {'format': 'YYYY-MM-DD', 'valueFormat': 'YYYY-MM-DD'}}
        else:
            return {'component': 'DatePicker', 'props': {'showTime': True, 'format': 'YYYY-MM-DD HH:mm:ss', 'valueFormat': 'YYYY-MM-DD HH:mm:ss'}}

    # Time fields -> TimePicker
    if column_type_upper == 'TIME':
        return {'component': 'TimePicker', 'props': {'format': 'HH:mm:ss', 'valueFormat': 'HH:mm:ss'}}

    # Number fields -> InputNumber
    if column_type_upper in ('INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT', 'DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'REAL', 'SERIAL', 'BIGSERIAL', 'SMALLSERIAL', 'DOUBLE PRECISION'):
        return {'component': 'InputNumber', 'props': {'style': 'width: 100%'}}

    # Password fields -> InputPassword
    if 'password' in column_name_lower or 'passwd' in column_name_lower:
        return {'component': 'InputPassword', 'props': {}}

    # Status/Type/Category fields -> Select
    if any(keyword in column_name_lower for keyword in ['status', 'type', 'category', 'state', 'level']):
        return {'component': 'Select', 'props': {'options': []}}

    # Long text fields -> Textarea
    if column_type_upper in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT') or (column.length and column.length > 255):
        return {'component': 'Textarea', 'props': {'rows': 4}}

    # JSON fields -> Textarea (for JSON editing)
    if column_type_upper in ('JSON', 'JSONB'):
        return {'component': 'Textarea', 'props': {'rows': 6, 'placeholder': 'Enter JSON'}}

    # Default -> Input
    return {'component': 'Input', 'props': {}}


def get_table_cell_renderer(column: ColumnInfo) -> dict | None:
    """
    Determine table cell renderer for a column.

    :param column: ColumnInfo object
    :return: Dictionary with renderer name and props, or None for default rendering
    """
    column_name_lower = column.name.lower()
    column_type_upper = column.type.upper()

    # Boolean fields -> CellSwitch (read-only switch display)
    if column_type_upper in ('BOOLEAN', 'BOOL', 'BIT'):
        return {'name': 'CellSwitch', 'props': {}}

    # Status fields -> CellTag (colored tag)
    if 'status' in column_name_lower or 'state' in column_name_lower:
        return {'name': 'CellTag', 'props': {}}

    # Image fields -> CellImage
    if any(keyword in column_name_lower for keyword in ['image', 'img', 'avatar', 'photo', 'picture']):
        return {'name': 'CellImage', 'props': {}}

    # Link/URL fields -> CellLink
    if any(keyword in column_name_lower for keyword in ['url', 'link', 'href']):
        return {'name': 'CellLink', 'props': {}}

    # Default rendering
    return None


def get_search_component(column: ColumnInfo) -> dict | None:
    """
    Determine search form component for a column.

    :param column: ColumnInfo object
    :return: Dictionary with component name and props, or None if not searchable
    """
    column_name_lower = column.name.lower()
    column_type_upper = column.type.upper()

    # Skip non-searchable fields
    if column.is_primary_key or column.is_auto_increment:
        return None

    # Boolean fields -> Select with Yes/No options
    if column_type_upper in ('BOOLEAN', 'BOOL', 'BIT'):
        return {
            'component': 'Select',
            'props': {
                'options': [
                    {'label': 'Yes', 'value': True},
                    {'label': 'No', 'value': False},
                ]
            },
        }

    # Date fields -> DatePicker with range
    if column_type_upper in ('DATE', 'DATETIME', 'TIMESTAMP', 'TIMESTAMP WITHOUT TIME ZONE', 'TIMESTAMP WITH TIME ZONE'):
        return {'component': 'RangePicker', 'props': {'format': 'YYYY-MM-DD'}}

    # Status/Type fields -> Select
    if any(keyword in column_name_lower for keyword in ['status', 'type', 'category', 'state', 'level']):
        return {'component': 'Select', 'props': {'options': []}}

    # String fields -> Input
    if column_type_upper in ('VARCHAR', 'CHAR', 'TEXT', 'CHARACTER VARYING'):
        return {'component': 'Input', 'props': {'placeholder': f'Search by {column.comment or column.name}'}}

    # Number fields -> InputNumber
    if column_type_upper in ('INT', 'INTEGER', 'TINYINT', 'SMALLINT', 'MEDIUMINT', 'BIGINT', 'SERIAL', 'BIGSERIAL', 'SMALLSERIAL'):
        return {'component': 'InputNumber', 'props': {'style': 'width: 100%'}}

    # Default: no search component
    return None


def generate_column_options(column: ColumnInfo) -> list[dict] | None:
    """
    Generate options for Select components based on column metadata.

    :param column: ColumnInfo object
    :return: List of option dictionaries or None
    """
    column_name_lower = column.name.lower()

    # Status field options
    if 'status' in column_name_lower:
        return [
            {'label': 'Active', 'value': 1},
            {'label': 'Inactive', 'value': 0},
        ]

    # Type field options (generic)
    if 'type' in column_name_lower:
        return [
            {'label': 'Type 1', 'value': 1},
            {'label': 'Type 2', 'value': 2},
        ]

    # Level field options
    if 'level' in column_name_lower:
        return [
            {'label': 'Low', 'value': 1},
            {'label': 'Medium', 'value': 2},
            {'label': 'High', 'value': 3},
        ]

    # Category field options
    if 'category' in column_name_lower:
        return [
            {'label': 'Category A', 'value': 'A'},
            {'label': 'Category B', 'value': 'B'},
        ]

    return None
