"""GeoLite2 离线 IP 归属地解析（零 Mock）。

约束（事实源：docs/hasn-node设计文档/多设备登录与跨设备消息路由/00-设计总览.md §4）：
- 只用 MaxMind GeoLite2-City.mmdb 离线库，**不发任何外部网络请求**。
- mmdb 缺失 / geoip2 未安装 / IP 为私网回环 / 库内查不到 → 返回 None，
  调用方按「未知归属地」如实展示，**绝不伪造城市**。
- mmdb 路径：环境变量 `GEOLITE2_CITY_MMDB_PATH`，缺省 `backend/data/GeoLite2-City.mmdb`。
  MaxMind 免费库需注册账号下载，属**部署前提**；缺失只 warn 一次，不影响主流程。
- Reader 懒加载、进程内复用（geoip2 Reader 读操作线程安全）。
"""

from __future__ import annotations

import ipaddress
import os
import threading

from backend.common.log import log

_DEFAULT_MMDB = 'backend/data/GeoLite2-City.mmdb'

_reader = None
_open_attempted = False
_lock = threading.Lock()


def _mmdb_path() -> str:
    return os.getenv('GEOLITE2_CITY_MMDB_PATH') or _DEFAULT_MMDB


def _get_reader():
    """懒加载 GeoLite2 Reader；打开失败仅 warn 一次并返回 None。"""
    global _reader, _open_attempted
    if _open_attempted:
        return _reader
    with _lock:
        if _open_attempted:
            return _reader
        _open_attempted = True
        path = _mmdb_path()
        if not os.path.exists(path):
            log.warning(f'[geoip] GeoLite2 mmdb 不存在: {path}，IP 归属地将显示「未知」')
            return None
        try:
            import geoip2.database

            _reader = geoip2.database.Reader(path)
            log.info(f'[geoip] GeoLite2 已加载: {path}')
        except Exception as exc:  # noqa: BLE001 - 任何加载异常都降级为「未知」
            log.warning(f'[geoip] GeoLite2 加载失败: {exc}，IP 归属地将显示「未知」')
            _reader = None
        return _reader


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


def _localized(obj) -> str | None:
    """优先中文名，回退默认（英文）名。"""
    if obj is None:
        return None
    names = getattr(obj, 'names', None) or {}
    return names.get('zh-CN') or names.get('zh') or getattr(obj, 'name', None)


def lookup_location(ip: str | None) -> str | None:
    """解析 IP → 归属地字符串（如「上海, 上海市, 中国」）；无法解析返回 None。"""
    if not ip or not _is_public_ip(ip):
        return None
    reader = _get_reader()
    if reader is None:
        return None
    try:
        resp = reader.city(ip)
    except Exception:  # noqa: BLE001 - AddressNotFoundError / 数据缺失等一律「未知」
        return None

    city = _localized(resp.city)
    subdivision = _localized(resp.subdivisions.most_specific) if resp.subdivisions else None
    country = _localized(resp.country)

    parts: list[str] = []
    for part in (city, subdivision, country):
        if part and part not in parts:
            parts.append(part)
    return ', '.join(parts) if parts else None
