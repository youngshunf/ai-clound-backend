from collections.abc import Sequence
from typing import Any, Generic, Literal, TypeVar, overload

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

Model = TypeVar('Model')


class JoinConfig:
    def __init__(
        self,
        *,
        model: Any,
        join_on: Any,
        join_type: Literal['inner', 'left', 'full'] = ...,
        fill_result: bool = ...,
    ) -> None: ...


class CRUDPlus(Generic[Model]):
    model: type[Model]

    def __init__(self, model: type[Model]) -> None: ...

    @overload
    async def select_model(
        self,
        session: AsyncSession,
        pk: Any,
        *whereclause: Any,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Model | None: ...
    @overload
    async def select_model(
        self,
        session: AsyncSession,
        pk: Any,
        *whereclause: Any,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Any: ...

    @overload
    async def select_model_by_column(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Model | None: ...
    @overload
    async def select_model_by_column(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Any: ...

    async def select(self, *whereclause: Any, **kwargs: Any) -> Select[Any]: ...
    async def select_order(self, sort_columns: Any, sort_orders: Any = ..., *whereclause: Any, **kwargs: Any) -> Select[Any]: ...

    @overload
    async def select_models(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Sequence[Model]: ...
    @overload
    async def select_models(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Any: ...

    @overload
    async def select_models_order(
        self,
        session: AsyncSession,
        sort_columns: Any,
        sort_orders: Any = ...,
        *whereclause: Any,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Sequence[Model]: ...
    @overload
    async def select_models_order(
        self,
        session: AsyncSession,
        sort_columns: Any,
        sort_orders: Any = ...,
        *whereclause: Any,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Any: ...

    async def create_model(self, session: AsyncSession, obj: Any, **kwargs: Any) -> Model: ...
    async def create_models(self, session: AsyncSession, objs: list[Any], **kwargs: Any) -> list[Model]: ...
    async def bulk_create_models(self, session: AsyncSession, objs: list[dict[str, Any]], **kwargs: Any) -> Sequence[Model]: ...
    async def update_model(self, session: AsyncSession, pk: Any, obj: Any, **kwargs: Any) -> int: ...
    async def update_model_by_column(self, session: AsyncSession, obj: Any, **kwargs: Any) -> int: ...
    async def delete_model(self, session: AsyncSession, pk: Any, **kwargs: Any) -> int: ...
    async def delete_model_by_column(self, session: AsyncSession, **kwargs: Any) -> int: ...
    async def count(self, session: AsyncSession, *whereclause: Any, **kwargs: Any) -> int: ...
    async def exists(self, session: AsyncSession, *whereclause: Any, **kwargs: Any) -> bool: ...
