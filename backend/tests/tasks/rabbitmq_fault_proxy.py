from __future__ import annotations

import contextlib
import socket
import struct
import threading

from collections.abc import Callable
from enum import StrEnum
from types import TracebackType

from typing_extensions import Self

_AMQP_PROTOCOL_HEADER_SIZE = 8
_AMQP_FRAME_HEADER_SIZE = 7
_AMQP_FRAME_END = 0xCE
_AMQP_METHOD_FRAME = 1
_BASIC_CLASS_ID = 60
_BASIC_PUBLISH_METHOD_ID = 40
_BASIC_ACK_METHOD_ID = 80


class RabbitMQFaultMode(StrEnum):
    """真实 AMQP 连接的故障注入位置。"""

    BEFORE_PUBLISH = 'before_publish'
    BEFORE_CONFIRM = 'before_confirm'


def _receive_exact(
    connection: socket.socket,
    size: int,
    stop_event: threading.Event,
) -> bytes:
    """从真实 TCP 连接读取固定字节数，并定期响应代理停止信号。"""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = connection.recv(remaining)
        except TimeoutError:
            if stop_event.is_set():
                raise EOFError('AMQP 转发已停止') from None
            continue
        if not chunk:
            raise EOFError('AMQP 连接已关闭')
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _receive_frame(
    connection: socket.socket,
    stop_event: threading.Event,
) -> tuple[bytes, int, bytes]:
    """读取并校验一个 AMQP 0-9-1 frame。"""
    header = _receive_exact(connection, _AMQP_FRAME_HEADER_SIZE, stop_event)
    frame_type = header[0]
    payload_size = struct.unpack('>I', header[3:7])[0]
    payload_and_end = _receive_exact(connection, payload_size + 1, stop_event)
    if payload_and_end[-1] != _AMQP_FRAME_END:
        raise RuntimeError('AMQP frame 结束标记不合法')
    return header + payload_and_end, frame_type, payload_and_end[:-1]


def _method_identity(frame_type: int, payload: bytes) -> tuple[int, int] | None:
    """返回 method frame 的 class/method 标识。"""
    if frame_type != _AMQP_METHOD_FRAME or len(payload) < 4:
        return None
    return struct.unpack('>HH', payload[:4])


class RabbitMQFaultProxy:
    """透明转发真实 AMQP 流量，并在指定协议边界关闭单个测试连接。"""

    def __init__(
        self,
        *,
        upstream_host: str,
        upstream_port: int,
        mode: RabbitMQFaultMode,
    ) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.mode = mode
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(('127.0.0.1', 0))
        self._listener.listen(1)
        self._listener.settimeout(10)
        self._client: socket.socket | None = None
        self._upstream: socket.socket | None = None
        self._close_lock = threading.Lock()
        self._stop = threading.Event()
        self._publish_seen = threading.Event()
        self._fault_seen = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._serve,
            name=f'rabbitmq-fault-{mode.value}',
            daemon=True,
        )

    @property
    def port(self) -> int:
        """返回本地监听端口。"""
        return int(self._listener.getsockname()[1])

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
        self._thread.join(timeout=10)
        if self._thread.is_alive() and exc_type is None:
            raise TimeoutError('RabbitMQ 故障代理未能停止')
        if self._error is not None and exc_type is None:
            raise RuntimeError('RabbitMQ 故障代理异常') from self._error

    def wait_for_fault(self, timeout: float = 10) -> None:
        """等待指定故障点被真实 AMQP 流量触发。"""
        if not self._fault_seen.wait(timeout):
            raise TimeoutError(f'RabbitMQ 故障点未触发：{self.mode.value}')

    def close(self) -> None:
        """关闭监听和已建立的双向连接。"""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._listener.close()
        self._close_connections(reset_client=False)

    def _close_connections(self, *, reset_client: bool) -> None:
        with self._close_lock:
            if reset_client and self._client is not None:
                with contextlib.suppress(OSError):
                    self._client.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack('ii', 1, 0),
                    )
            for connection in (self._client, self._upstream):
                if connection is None:
                    continue
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
                with contextlib.suppress(OSError):
                    connection.close()

    def _trigger_fault(self) -> None:
        self._fault_seen.set()
        self._stop.set()
        self._close_connections(reset_client=True)

    def _serve(self) -> None:
        try:
            client, _address = self._listener.accept()
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port),
                timeout=10,
            )
            # 短轮询只用于让转发线程在 shutdown 在某些平台未立即唤醒 recv 时，
            # 仍能在停止信号后确定性退出；无停止信号时 timeout 会继续读取。
            client.settimeout(0.25)
            upstream.settimeout(0.25)
            self._client = client
            self._upstream = upstream
            client_to_upstream = threading.Thread(
                target=self._run_forwarder,
                args=(self._forward_client_frames,),
                name='rabbitmq-fault-client-to-upstream',
                daemon=True,
            )
            upstream_to_client = threading.Thread(
                target=self._run_forwarder,
                args=(self._forward_server_frames,),
                name='rabbitmq-fault-upstream-to-client',
                daemon=True,
            )
            client_to_upstream.start()
            upstream_to_client.start()
            while not self._stop.is_set() and client_to_upstream.is_alive() and upstream_to_client.is_alive():
                client_to_upstream.join(timeout=0.1)
                upstream_to_client.join(timeout=0.1)

            self._stop.set()
            self._close_connections(reset_client=False)
            client_to_upstream.join(timeout=2)
            upstream_to_client.join(timeout=2)
            if client_to_upstream.is_alive() or upstream_to_client.is_alive():
                self._error = TimeoutError('RabbitMQ 故障代理转发线程未能停止')
        except (EOFError, OSError, TimeoutError) as exc:
            if not self._stop.is_set():
                self._error = exc
        except BaseException as exc:
            self._error = exc
        finally:
            self.close()

    def _run_forwarder(self, forwarder: Callable[[], None]) -> None:
        """运行单向转发，并让另一方向及时退出。"""
        try:
            forwarder()
        except BaseException as exc:
            if not self._stop.is_set():
                self._error = exc
        finally:
            self._stop.set()
            self._close_connections(reset_client=False)

    def _forward_client_frames(self) -> None:
        client = self._required_connection(self._client)
        upstream = self._required_connection(self._upstream)
        try:
            protocol_header = _receive_exact(client, _AMQP_PROTOCOL_HEADER_SIZE, self._stop)
            upstream.sendall(protocol_header)
            while not self._stop.is_set():
                frame, frame_type, payload = _receive_frame(client, self._stop)
                method = _method_identity(frame_type, payload)
                if method == (_BASIC_CLASS_ID, _BASIC_PUBLISH_METHOD_ID):
                    self._publish_seen.set()
                    if self.mode == RabbitMQFaultMode.BEFORE_PUBLISH:
                        self._trigger_fault()
                        return
                upstream.sendall(frame)
        except (EOFError, OSError, TimeoutError):
            if not self._stop.is_set():
                raise

    def _forward_server_frames(self) -> None:
        upstream = self._required_connection(self._upstream)
        client = self._required_connection(self._client)
        try:
            while not self._stop.is_set():
                frame, frame_type, payload = _receive_frame(upstream, self._stop)
                method = _method_identity(frame_type, payload)
                if (
                    self.mode == RabbitMQFaultMode.BEFORE_CONFIRM
                    and self._publish_seen.is_set()
                    and method == (_BASIC_CLASS_ID, _BASIC_ACK_METHOD_ID)
                ):
                    self._trigger_fault()
                    return
                client.sendall(frame)
        except (EOFError, OSError, TimeoutError):
            if not self._stop.is_set():
                raise

    @staticmethod
    def _required_connection(
        connection: socket.socket | None,
    ) -> socket.socket:
        if connection is None:
            raise RuntimeError('RabbitMQ 故障代理连接尚未建立')
        return connection
