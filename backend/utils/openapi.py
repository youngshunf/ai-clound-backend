from fastapi import FastAPI
from fastapi.routing import APIRoute


def simplify_operation_ids(app: FastAPI) -> None:
    """
    简化操作 ID，以便生成的客户端具有更简单的 API 函数名称

    :param app: FastAPI 应用实例
    :return:
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.operation_id = route.name


def ensure_unique_route_names(app: FastAPI) -> None:
    """
    检查路由名称是否唯一

    路由名称（route.name，默认取处理函数名）在 FastAPI 中是全局命名空间，
    与挂载 prefix 无关。两个不同应用即使 URL path 不同，只要处理函数同名
    （或同名 ``name=``）就会冲突，导致 OpenAPI operation_id 撞车、客户端生成歧义。

    一次性收集**所有**重复名称并连同各自的 method+path 一起报出，避免一次只
    暴露一个、改完又冒下一个的反复重启。

    :param app: FastAPI 应用实例
    :return:
    """
    seen: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            location = f'{",".join(sorted(route.methods or []))} {route.path}'
            if route.name in seen:
                duplicates.setdefault(route.name, [seen[route.name]]).append(location)
            else:
                seen[route.name] = location

    if duplicates:
        detail = '\n'.join(
            f'  - {name}: ' + ' / '.join(locations) for name, locations in sorted(duplicates.items())
        )
        raise ValueError(
            f'发现 {len(duplicates)} 个重复路由名称（route.name 全局唯一，需显式 name= 区分）:\n{detail}'
        )
