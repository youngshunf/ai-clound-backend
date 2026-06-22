"""quant 引擎 provider 包（主云端 → quant-engine-service 内网 REST 的唯一耦合点）。"""

from backend.app.hasn_quant.provider.engine_client import quant_engine_provider

__all__ = ['quant_engine_provider']
