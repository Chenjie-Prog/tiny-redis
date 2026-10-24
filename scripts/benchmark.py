# -*- coding: utf-8 -*-
"""简易性能基准：顺序 SET/GET 的每秒操作数。

用法::

    python scripts/benchmark.py [--port 6379] [-n 10000]

注意：这是同步顺序基准（每条命令等应答），衡量的是单连接往返延迟；
真实 Redis 在 pipeline 下的吞吐会高一个数量级，tiny-redis 同理。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis.client import Redis


def main():
    parser = argparse.ArgumentParser(description="tiny-redis 基准测试")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("-n", type=int, default=10000, help="每种操作的次数")
    args = parser.parse_args()

    with Redis(host=args.host, port=args.port, timeout=30) as r:
        r.execute_command("FLUSHALL")

        start = time.perf_counter()
        for i in range(args.n):
            r.set("bench:%d" % i, "x" * 32)
        set_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        for i in range(args.n):
            r.get("bench:%d" % i)
        get_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        for i in range(args.n):
            r.incr("counter")
        incr_elapsed = time.perf_counter() - start

    print("tiny-redis 基准（顺序模式，单连接，%d 次/操作）" % args.n)
    print("  SET : %8.0f ops/s" % (args.n / set_elapsed))
    print("  GET : %8.0f ops/s" % (args.n / get_elapsed))
    print("  INCR: %8.0f ops/s" % (args.n / incr_elapsed))


if __name__ == "__main__":
    main()
