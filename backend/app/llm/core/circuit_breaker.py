"""熔断器实现"""

import time

from threading import Lock

from backend.app.llm.enums import CircuitState
from backend.core.conf import settings


class CircuitBreaker:
    """熔断器"""

    def __init__(
        self,
        name: str,
        failure_threshold: int | None = None,
        recovery_timeout: int | None = None,
        half_open_max_calls: int = 3,
    ) -> None:
        """
        初始化熔断器

        :param name: 熔断器名称（通常是供应商名称）
        :param failure_threshold: 失败阈值，达到后触发熔断
        :param recovery_timeout: 恢复超时时间（秒）
        :param half_open_max_calls: 半开状态最大调用次数
        """
        self.name = name
        self.failure_threshold = failure_threshold or getattr(settings, 'LLM_CIRCUIT_BREAKER_THRESHOLD', 5)
        self.recovery_timeout = recovery_timeout or getattr(settings, 'LLM_CIRCUIT_BREAKER_TIMEOUT', 30)
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0
        self._half_open_calls = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        """获取当前状态"""
        with self._lock:
            self._check_state_transition()
            return self._state

    @property
    def failure_count(self) -> int:
        """获取失败计数"""
        return self._failure_count

    def _check_state_transition(self) -> None:
        """检查并执行状态转换"""
        if self._state == CircuitState.OPEN and time.time() - self._last_failure_time > self.recovery_timeout:
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
            self._success_count = 0

    def allow_request(self) -> bool:
        """
        检查是否允许请求

        :return: True 允许，False 拒绝
        """
        with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                return False

            # HALF_OPEN 状态
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True

            return False

    def record_success(self) -> None:
        """记录成功调用"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                # 成功时重置失败计数
                self._failure_count = 0

    def record_failure(self) -> None:
        """记录失败调用"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # 半开状态下失败，立即回到打开状态
                self._state = CircuitState.OPEN
                self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN

    def reset(self) -> None:
        """重置熔断器"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = 0
            self._half_open_calls = 0

    def get_status(self) -> dict:
        """获取熔断器状态"""
        with self._lock:
            self._check_state_transition()
            return {
                'name': self.name,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'failure_threshold': self.failure_threshold,
                'recovery_timeout': self.recovery_timeout,
                'last_failure_time': self._last_failure_time,
                'time_until_recovery': max(0, self.recovery_timeout - (time.time() - self._last_failure_time))
                if self._state == CircuitState.OPEN
                else 0,
            }


class CircuitBreakerManager:
    """熔断器管理器"""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = Lock()

    def get_breaker(self, name: str) -> CircuitBreaker:
        """
        获取或创建熔断器

        :param name: 熔断器名称
        :return: 熔断器实例
        """
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name)
            return self._breakers[name]

    def get_all_status(self) -> list[dict]:
        """获取所有熔断器状态"""
        with self._lock:
            return [breaker.get_status() for breaker in self._breakers.values()]

    def reset_all(self) -> None:
        """重置所有熔断器"""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# 创建全局熔断器管理器
circuit_breaker_manager = CircuitBreakerManager()
