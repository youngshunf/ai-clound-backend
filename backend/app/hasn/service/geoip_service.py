"""ip2region 离线 IP 归属地解析（零 Mock）。

约束（事实源：docs/hasn-node设计文档/多设备登录与跨设备消息路由/00-设计总览.md §4；
2026-07-14 福仔拍板从 GeoLite2 切换 ip2region——免注册、xdb 数据文件直接进仓库、
部署零额外步骤、国内 IP 归属地粒度更好「省+市+ISP」）：
- 只用 lionsoul2014/ip2region 官方 xdb 离线库（py-ip2region binding），**不发任何外部网络请求**。
- xdb 缺失 / 载入失败 / IP 为私网回环 / 库内查不到 → 返回 None，
  调用方按「未知归属地」如实展示，**绝不伪造城市**。
- xdb 路径：环境变量 `IP2REGION_XDB_PATH`，缺省 `backend/data/ip2region_v4.xdb`（已进仓库）。
- 整库 buffer 载入内存（~11MB）懒加载、进程内复用，buffer 模式并发查询安全。
"""

from __future__ import annotations

import ipaddress
import os
import threading

from pathlib import Path

from backend.common.log import log

# 基于本文件位置推导（backend/app/hasn/service → backend/data），不依赖进程 cwd
_DEFAULT_XDB = str(Path(__file__).resolve().parents[3] / 'data' / 'ip2region_v4.xdb')

_searcher = None
_open_attempted = False
_lock = threading.Lock()


def _xdb_path() -> str:
    return os.getenv('IP2REGION_XDB_PATH') or _DEFAULT_XDB


def _get_searcher():
    """懒加载 ip2region Searcher（整库入内存）；打开失败仅 warn 一次并返回 None。"""
    global _searcher, _open_attempted
    if _open_attempted:
        return _searcher
    with _lock:
        if _open_attempted:
            return _searcher
        _open_attempted = True
        path = _xdb_path()
        if not os.path.exists(path):
            log.warning(f'[geoip] ip2region xdb 不存在: {path}，IP 归属地将显示「未知」')
            return None
        try:
            from ip2region import searcher, util

            buf = util.load_content_from_file(path)
            _searcher = searcher.new_with_buffer(util.IPv4, buf)
            log.info(f'[geoip] ip2region xdb 已加载: {path}')
        except Exception as exc:
            log.warning(f'[geoip] ip2region xdb 加载失败: {exc}，IP 归属地将显示「未知」')
            _searcher = None
        return _searcher


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


def _format_region(raw: str) -> str | None:
    """把 xdb 原始结果格式化为展示字符串。

    v4 xdb 原始格式五段「国家|省|市|ISP|国家码」，缺失段为 '0'：
    - 中国境内 → 「云南省昆明市 电信」（省+市相邻去重，ISP 有值则空格缀后）
    - 海外 → 「United States California」（国家+区域）
    - 全段缺失 / Reserved → None
    """
    parts = [p for p in raw.split('|')]
    if len(parts) < 4:
        return None
    country, province, city, isp = parts[0], parts[1], parts[2], parts[3]

    def _has(v: str) -> bool:
        return bool(v) and v not in ('0', 'Reserved')

    segs: list[str] = []
    if country == '中国':
        # 国内省市粒度足够定位，不重复展示「中国」；直辖市（省=市）相邻去重
        for part in (province, city):
            if _has(part) and (not segs or segs[-1] != part):
                segs.append(part)
    else:
        for part in (country, province, city):
            if _has(part) and part not in segs:
                segs.append(part)
    if not segs:
        return None
    location = ''.join(segs) if country == '中国' else ', '.join(segs)
    if _has(isp) and country == '中国':
        location = f'{location} {isp}'
    return location


def lookup_location(ip: str | None) -> str | None:
    """解析 IP → 归属地字符串（如「云南省昆明市 电信」）；无法解析返回 None。"""
    if not ip or not _is_public_ip(ip):
        return None
    s = _get_searcher()
    if s is None:
        return None
    try:
        raw = s.search(ip)
    except Exception:
        return None
    if not raw:
        return None
    return _format_region(raw)
