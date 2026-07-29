from collections.abc import Sequence
from datetime import datetime
from typing import Any, Generic, TypeVar, overload

from pydantic import BaseModel
from sqlalchemy import Column, Row, Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

Model = TypeVar("Model", bound=DeclarativeBase)

class JoinConfig:
    model: Any
    join_on: Any
    join_type: str
    fill_result: bool
    def __init__(
        self,
        *,
        model: Any,
        join_on: Any,
        join_type: str = ...,
        fill_result: bool = ...,
    ) -> None: ...

class CRUDPlus(Generic[Model]):
    model: type[Model]
    model_column_names: list[str]
    primary_key: Column[Any] | list[Column[Any]]

    def __init__(self, model: type[Model]) -> None: ...

    async def create_model(
        self,
        session: AsyncSession,
        obj: BaseModel,
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> Model: ...

    async def create_models(
        self,
        session: AsyncSession,
        objs: Sequence[BaseModel],
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> list[Model]: ...

    async def bulk_create_models(
        self,
        session: AsyncSession,
        objs: Sequence[dict[str, Any]],
        render_nulls: bool = ...,
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> Sequence[Model]: ...

    async def count(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: Any = ...,
        **kwargs: Any,
    ) -> int: ...

    async def exists(
        self,
        session: AsyncSession,
        *whereclause: Any,
        join_conditions: Any = ...,
        **kwargs: Any,
    ) -> bool: ...

    @overload
    async def select_model(
        self,
        session: AsyncSession,
        pk: Any,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Model | None: ...
    @overload
    async def select_model(
        self,
        session: AsyncSession,
        pk: Any,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Row[Any] | Model | None: ...

    @overload
    async def select_model_by_column(
        self,
        session: AsyncSession,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: None = ...,
        **kwargs: Any,
    ) -> Model | None: ...
    @overload
    async def select_model_by_column(
        self,
        session: AsyncSession,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any,
        **kwargs: Any,
    ) -> Row[Any] | Model | None: ...

    async def select(
        self,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any = ...,
        **kwargs: Any,
    ) -> Select[Any]: ...

    async def select_order(
        self,
        sort_columns: str | list[str],
        sort_orders: str | list[str] | None = ...,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any = ...,
        **kwargs: Any,
    ) -> Select[Any]: ...

    @overload
    async def select_models(
        self,
        session: AsyncSession,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: None = ...,
        limit: int | None = ...,
        offset: int | None = ...,
        **kwargs: Any,
    ) -> Sequence[Model]: ...
    @overload
    async def select_models(
        self,
        session: AsyncSession,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any,
        limit: int | None = ...,
        offset: int | None = ...,
        **kwargs: Any,
    ) -> Sequence[Row[Any] | Model]: ...

    @overload
    async def select_models_order(
        self,
        session: AsyncSession,
        sort_columns: str | list[str],
        sort_orders: str | list[str] | None = ...,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: None = ...,
        limit: int | None = ...,
        offset: int | None = ...,
        **kwargs: Any,
    ) -> Sequence[Model]: ...
    @overload
    async def select_models_order(
        self,
        session: AsyncSession,
        sort_columns: str | list[str],
        sort_orders: str | list[str] | None = ...,
        *whereclause: Any,
        load_options: Any = ...,
        load_strategies: Any = ...,
        join_conditions: Any,
        limit: int | None = ...,
        offset: int | None = ...,
        **kwargs: Any,
    ) -> Sequence[Row[Any] | Model]: ...

    async def update_model(
        self,
        session: AsyncSession,
        pk: Any,
        obj: BaseModel | dict[str, Any],
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> int: ...

    async def update_model_by_column(
        self,
        session: AsyncSession,
        obj: BaseModel | dict[str, Any],
        allow_multiple: bool = ...,
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> int: ...

    async def bulk_update_models(
        self,
        session: AsyncSession,
        objs: Sequence[BaseModel | dict[str, Any]],
        pk_mode: bool = ...,
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> int: ...

    async def delete_model(
        self,
        session: AsyncSession,
        pk: Any,
        flush: bool = ...,
        commit: bool = ...,
    ) -> int: ...

    async def delete_model_by_column(
        self,
        session: AsyncSession,
        allow_multiple: bool = ...,
        logical_deletion: bool = ...,
        deleted_flag_column: str = ...,
        deleted_at_column: str = ...,
        deleted_at_factory: datetime = ...,
        flush: bool = ...,
        commit: bool = ...,
        **kwargs: Any,
    ) -> int: ...

__version__: str
