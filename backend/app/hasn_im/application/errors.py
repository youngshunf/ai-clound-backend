"""hasn_im.application.errors · 通信域应用层错误（值语义，无框架依赖）

port 边界抛出的结构化错误：协议级硬拒（对方屏蔽/自发/身份未声明等）不是投递态，
用异常表达；调用方（协议层/工具面）据 code 映射统一信封。
"""

from __future__ import annotations


class ImError(Exception):
    """通信域应用层错误基类。"""


class ImConversationNotFound(ImError):
    """conversation_id 不存在（发送前必须先 ensure）。"""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        super().__init__(f'会话不存在：{conversation_id}')


class ImSenderNotParticipant(ImError):
    """发送方不是该会话参与者。"""

    def __init__(self, conversation_id: str, sender_hasn_id: str):
        self.conversation_id = conversation_id
        self.sender_hasn_id = sender_hasn_id
        super().__init__(f'发送方 {sender_hasn_id} 不属于会话 {conversation_id}')


class ImSendRejected(ImError):
    """协议级硬拒（对方屏蔽、不能给自己发、身份未声明、commerce 门等）。

    非投递态——携带原 route 层 code + message，供协议层映射统一信封错误码。
    """

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f'[{code}] {message}')
