"""finance_provider —— 唯一耦合点：httpx 调 finance-data-service（设计 §1.1 支柱2 / §5）。"""

from backend.app.hasn_finance.provider.client import finance_provider

__all__ = ['finance_provider']
