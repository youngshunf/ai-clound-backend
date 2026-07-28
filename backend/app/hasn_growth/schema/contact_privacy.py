"""获客联系人私有资料请求模型。"""

from typing import Literal

from pydantic import Field

from backend.common.schema import SchemaBase


class RevealContactChannelParam(SchemaBase):
    """Owner 单渠道明文查看请求。"""

    purpose: Literal[
        'manual_assist_send',
        'contact_verification',
        'customer_support',
        'data_correction',
    ] = Field(description='受控的明文查看原因码，禁止写入自由文本或联系方式')
