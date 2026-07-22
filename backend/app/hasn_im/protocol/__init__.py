"""hasn_im.protocol · HASN 节点协议层（无 DB 纯模块：帧编解码 / 校验 / 分派登记）。

doc92 R1-09「协议层纯化」的落点：把 ws_node.py 里与业务/传输/ORM 纠缠的协议编解码
提为无副作用纯模块，可独立单测。后续 R1-09 小步（typed handler registry、
frame size/超时/backpressure 配置化、Socket.IO 兼容 adapter）在此包内推进。
"""
