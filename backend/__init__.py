import sqlalchemy as sa

from backend.utils.dynamic_import import get_all_models

# import all models for auto create db tables
for cls in get_all_models():
    if isinstance(cls, sa.Table):
        table_name = cls.name
        if table_name not in globals():
            globals()[table_name] = cls
    else:
        class_name = getattr(cls, '__name__', None)
        if not isinstance(class_name, str):
            continue
        if class_name not in globals():
            globals()[class_name] = cls


__version__ = '1.13.0'
