# -*- coding: utf-8 -*-
"""``python -m tinyredis``：命令行入口。

用法::

    python -m tinyredis --port 6380 --dir ./data --dbfilename dump.trdb \
        --snapshot-interval 60
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .server import TinyRedisServer


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tiny-redis",
        description="纯 Python 标准库实现的迷你 Redis 服务器（v%s）" % __version__)
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=6379, help="监听端口（默认 6379）")
    parser.add_argument("--dir", default=".", help="快照文件目录（默认当前目录）")
    parser.add_argument("--dbfilename", default="dump.trdb", help="快照文件名（默认 dump.trdb）")
    parser.add_argument("--snapshot-interval", type=float, default=0.0,
                        help="自动快照间隔秒数，0 表示关闭（默认 0）")
    parser.add_argument("--version", action="version", version="tiny-redis %s" % __version__)
    args = parser.parse_args(argv)

    snapshot_path = Path(args.dir) / args.dbfilename
    server = TinyRedisServer(
        host=args.host, port=args.port,
        snapshot_path=snapshot_path, snapshot_interval=args.snapshot_interval)
    server.bind()

    restored = server.load_snapshot()
    if restored:
        print("[tiny-redis] 已从 %s 恢复 %d 个键" % (snapshot_path, restored))
    print("[tiny-redis] v%s 正在监听 %s:%d （Ctrl+C 退出）"
          % (__version__, args.host, server.bound_port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server.snapshot_path:
            server.save_snapshot()
            print("[tiny-redis] 快照已保存到 %s" % server.snapshot_path)
        print("[tiny-redis] 已退出，共处理 %d 条命令" % server.commands_processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
