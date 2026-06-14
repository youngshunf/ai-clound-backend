from datetime import datetime
from pydantic import BaseModel, Field


class CreatePayMerchantParam(BaseModel):
    name: str = Field(description='商户名称')
    type: str = Field(description='类型 weixin/alipay')
    config: dict = Field(default_factory=dict, description='配置 JSON')
    status: int = Field(default=1, description='状态')
    remark: str | None = None


class UpdatePayMerchantParam(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    status: int | None = None
    remark: str | None = None


class GetPayMerchantDetail(BaseModel):
    model_config = {'from_attributes': True}

    id: int
    name: str
    type: str
    config: dict
    status: int
    remark: str | None
    created_time: datetime | None
    updated_time: datetime | None


class GetPayMerchantSimple(BaseModel):
    """下拉选择用"""
    model_config = {'from_attributes': True}

    id: int
    name: str
    type: str
    status: int
