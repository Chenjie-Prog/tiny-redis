# -*- coding: utf-8 -*-
"""用官方 redis-py 客户端验证 tiny-redis 的协议兼容性。

本脚本是开发/演示工具，redis-py 只是开发期依赖，不进入运行时依赖树::

    pip install redis          # 开发机执行一次
    python scripts/compat_check.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis.server import TinyRedisServer


def run_checks(r):
    """对已连接的 redis-py 客户端执行一组检查，返回 [(名称, 是否通过, 实际值)]。"""
    import redis as redis_py

    def wrongtype(client):
        try:
            client.rpush("str:key", "x")
            client.get("str:key")
        except redis_py.ResponseError as exc:
            return exc
        return None

    def unknown(client):
        try:
            client.execute_command("NOSUCHCMD", "x")
        except redis_py.ResponseError as exc:
            return exc
        return None

    checks = []
    def check(name, got, want):
        checks.append((name, got == want, got))

    check("PING", r.ping(), True)
    check("SET", r.set("str:key", "hello"), True)
    check("GET", r.get("str:key"), b"hello")
    check("INCR x2", [r.incr("cnt"), r.incr("cnt")], [1, 2])
    check("EXISTS", r.exists("str:key", "nope"), 1)
    check("EXPIRE", r.expire("str:key", 100), True)
    check("TTL in range", 0 < r.ttl("str:key") <= 100, True)
    check("MSET/MGET", (r.mset({"a": "1", "b": "2"}),
                        r.mget("a", "b", "zz"))[1], [b"1", b"2", None])
    check("RPUSH", r.rpush("list:key", "a", "b", "c"), 3)
    check("LRANGE", r.lrange("list:key", 0, -1), [b"a", b"b", b"c"])
    check("LPOP", r.lpop("list:key"), b"a")
    check("LLEN", r.llen("list:key"), 2)
    check("HSET", r.hset("hash:key", mapping={"f1": "v1", "f2": "v2"}), 2)
    check("HGET", r.hget("hash:key", "f1"), b"v1")
    check("HGETALL", r.hgetall("hash:key"), {b"f1": b"v1", b"f2": b"v2"})
    check("TYPE", r.type("list:key"), b"list")
    check("STRLEN", r.strlen("str:key"), 5)
    check("KEYS", sorted(r.keys()), sorted([b"str:key", b"cnt", b"a", b"b",
                                            b"list:key", b"hash:key"]))
    check("DBSIZE", r.dbsize(), 6)
    check("WRONGTYPE error", type(wrongtype(r)), redis_py.ResponseError)
    check("unknown command error", type(unknown(r)), redis_py.ResponseError)
    check("FLUSHALL", r.flushall(), True)
    check("empty after FLUSHALL", r.dbsize(), 0)
    return checks


def main():
    server = TinyRedisServer(port=0)
    server.start_background()
    port = server.wait_ready()
    time.sleep(0.1)
    try:
        import redis as redis_py
        # tiny-redis 目前只支持 RESP2；redis-py 8.x 默认尝试 RESP3，
        # 需要显式指定 protocol=2。
        with redis_py.Redis(host="127.0.0.1", port=port, socket_timeout=5,
                            protocol=2) as client:
            checks = run_checks(client)
    finally:
        server.close()
        server.wait_stopped(5)

    failed = [c for c in checks if not c[1]]
    for name, ok, got in checks:
        if not ok:
            print("FAIL %s -> %r" % (name, got))
    print("redis-py %s 兼容性检查：%d/%d 项通过"
          % (redis_py.__version__, len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
