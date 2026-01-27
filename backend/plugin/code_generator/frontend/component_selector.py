"""Component selector for smart form and table component selection."""

from backend.plugin.code_generator.frontend.type_mapper import (
    generate_column_options,
    get_form_component,
    get_search_component,
    get_table_cell_renderer,
)
from backend.plugin.code_generator.parser.sql_parser import ColumnInfo


def select_form_component(column: ColumnInfo) -> dict:
    """
    Select appropriate form component with props for a column.

    :param column: ColumnInfo object
    :return: Dictionary with component name and props
    """
    component_info = get_form_component(column)

    # Add options if it's a Select component
    if component_info['component'] == 'Select':
        options = generate_column_options(column)
        if options:
            component_info['props']['options'] = options

    # Add validation rules based on column constraints
    rules = []
    if not column.nullable:
        rules.append({'required': True, 'message': f'{column.comment or column.name} is required'})

    if column.length and component_info['component'] == 'Input':
        rules.append({'max': column.length, 'message': f'Maximum length is {column.length}'})

    if rules:
        component_info['rules'] = rules

    return component_info


def select_table_renderer(column: ColumnInfo) -> dict | None:
    """
    Select appropriate table cell renderer for a column.

    :param column: ColumnInfo object
    :return: Dictionary with renderer name and props, or None for default
    """
    return get_table_cell_renderer(column)


def select_search_component(column: ColumnInfo) -> dict | None:
    """
    Select appropriate search form component for a column.

    :param column: ColumnInfo object
    :return: Dictionary with component name and props, or None if not searchable
    """
    component_info = get_search_component(column)

    if component_info and component_info['component'] == 'Select':
        # Add options if available
        options = generate_column_options(column)
        if options:
            component_info['props']['options'] = options

    return component_info


def should_display_in_table(column: ColumnInfo) -> bool:
    """
    Determine if a column should be displayed in the table by default.

    :param column: ColumnInfo object
    :return: True if should be displayed
    """
    column_name_lower = column.name.lower()

    # Skip large text fields
    if column.type.upper() in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'JSON', 'JSONB'):
        return False

    # Skip password fields
    if 'password' in column_name_lower or 'passwd' in column_name_lower:
        return False

    # Skip binary fields
    if column.type.upper() in ('BLOB', 'BYTEA', 'BINARY', 'VARBINARY'):
        return False

    # Display everything else
    return True


def should_include_in_form(column: ColumnInfo) -> bool:
    """
    Determine if a column should be included in add/edit forms.

    :param column: ColumnInfo object
    :return: True if should be included
    """
    column_name_lower = column.name.lower()

    # Skip auto-increment primary keys
    if column.is_primary_key and column.is_auto_increment:
        return False

    # Skip timestamp fields that are auto-managed
    if column_name_lower in ('created_time', 'updated_time', 'created_at', 'updated_at', 'deleted_at'):
        return False

    # Include everything else
    return True


def should_include_in_search(column: ColumnInfo) -> bool:
    """
    Determine if a column should be included in search filters.

    :param column: ColumnInfo object
    :return: True if should be included
    """
    column_name_lower = column.name.lower()

    # Skip auto-increment primary keys
    if column.is_primary_key and column.is_auto_increment:
        return False

    # Skip large text fields
    if column.type.upper() in ('TEXT', 'MEDIUMTEXT', 'LONGTEXT', 'JSON', 'JSONB'):
        return False

    # Skip binary fields
    if column.type.upper() in ('BLOB', 'BYTEA', 'BINARY', 'VARBINARY'):
        return False

    # Skip password fields
    if 'password' in column_name_lower or 'passwd' in column_name_lower:
        return False

    # Include common search fields
    if any(
        keyword in column_name_lower
        for keyword in ['name', 'title', 'status', 'type', 'category', 'email', 'username', 'code', 'id']
    ):
        return True

    # Include date fields
    if column.type.upper() in ('DATE', 'DATETIME', 'TIMESTAMP', 'TIMESTAMP WITHOUT TIME ZONE', 'TIMESTAMP WITH TIME ZONE'):
        return True

    # Default: don't include
    return False
