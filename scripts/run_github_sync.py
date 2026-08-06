#!/usr/bin/env python3
"""已退役的服务器 GitHub 技能同步入口。

官方和受管 GitHub 技能现在必须从可信 ``hasn-hub`` 工作区确定性打包，再经管理员
来源发布 API 写入 CDN 和目录数据库。保留本文件只为给旧运维命令提供明确迁移提示。
"""

import sys


def main() -> int:
    """拒绝旧同步命令并输出新发布入口。"""
    print(
        'GitHub 技能服务器同步已退役，请在可信 hasn-hub 工作区运行 '
        'astrahub publish skills。',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
