# -*- coding: utf-8 -*-
"""纯标准库的 Redis 客户端。

与服务器共用 protocol.py 的序列化函数，解析方向则独立实现——
应答是五种 RESP 类型任意的组合，用一个小递归下降解析器处理。
同步阻塞模型，一条连接一个实例（与 redis-py 默认行为一致）。
"""

import socket

from .protocol import CRLF, encode_command

__all__ = ["Redis", "ResponseError", "ClientError"]


class ResponseError(Exception):
    """服务器返回的错误应答（``-ERR ...``）。"""


class ClientError(Exception):
    """连接断开、超时等客户端侧错误。"""


class Redis:
    """同步 Redis 客户端，例如::

        from tinyredis.client import Redis
        with Redis(port=6379) as r:
            r.set("greeting", "hello")
            print(r.get("greeting"))   # b'hello'
    """

    def __init__(self, host="127.0.0.1", port=6379, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self._buf = bytearray()

    # ---- 连接管理 ----------------------------------------------------

    def _connect(self):
        if self._sock is None:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
            self._buf = bytearray()
        return self._sock

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 核心 --------------------------------------------------------

    def execute_command(self, *args):
        """发送一条命令并等待应答。参数自动编码为 RESP 数组。"""
        sock = self._connect()
        sock.sendall(encode_command(args))
        return self._read_reply()

    def _read_reply(self):
        while True:
            reply, consumed = _parse_reply(self._buf)
            if consumed:
                del self._buf[:consumed]
                return reply
            try:
                chunk = self._connect().recv(65536)
            except socket.timeout as exc:
                raise ClientError("read timed out") from exc
            if not chunk:
                raise ClientError("connection closed by server")
            self._buf += chunk

    # ---- 常用命令的便捷封装 -------------------------------------------

    def ping(self, message=None):
        args = ("PING",) if message is None else ("PING", message)
        return self.execute_command(*args)

    def echo(self, message):
        return self.execute_command("ECHO", message)

    def get(self, key):
        return self.execute_command("GET", key)

    def set(self, key, value, ex=None, px=None, nx=False, xx=False,
            keepttl=False, get=False):
        args = ["SET", key, value]
        if ex is not None:
            args += ["EX", ex]
        if px is not None:
            args += ["PX", px]
        if nx:
            args.append("NX")
        if xx:
            args.append("XX")
        if keepttl:
            args.append("KEPTTTL")
        if get:
            args.append("GET")
        return self.execute_command(*args)

    def delete(self, *keys):
        return self.execute_command("DEL", *keys)

    def exists(self, *keys):
        return self.execute_command("EXISTS", *keys)

    def incr(self, key):
        return self.execute_command("INCR", key)

    def decr(self, key):
        return self.execute_command("DECR", key)

    def expire(self, key, seconds):
        return self.execute_command("EXPIRE", key, seconds)

    def ttl(self, key):
        return self.execute_command("TTL", key)

    def keys(self, pattern=b"*"):
        return self.execute_command("KEYS", pattern)

    def lpush(self, key, *values):
        return self.execute_command("LPUSH", key, *values)

    def rpush(self, key, *values):
        return self.execute_command("RPUSH", key, *values)

    def lrange(self, key, start, end):
        return self.execute_command("LRANGE", key, start, end)

    def lpop(self, key):
        return self.execute_command("LPOP", key)

    def rpop(self, key):
        return self.execute_command("RPOP", key)

    def hset(self, key, field, value):
        return self.execute_command("HSET", key, field, value)

    def hget(self, key, field):
        return self.execute_command("HGET", key, field)

    def hgetall(self, key):
        return self.execute_command("HGETALL", key)


# ---- 应答解析（客户端方向）-------------------------------------------

def _find_crlf(buf, start=0):
    return buf.find(CRLF, start)


def _parse_reply(buf):
    """从 buf 开头解析一条应答。返回 (reply, consumed)；不完整返回 (None, 0)。"""
    if not buf:
        return None, 0
    kind, rest = buf[0:1], buf[1:]
    eol = _find_crlf(buf, 1)
    if eol < 0:
        return None, 0
    line = bytes(rest[:eol - 1])

    if kind == b"+":
        return line.decode("utf-8"), eol + 2
    if kind == b"-":
        raise ResponseError(line.decode("utf-8"))
    if kind == b":":
        return int(line), eol + 2
    if kind == b"$":
        length = int(line)
        if length == -1:
            return None, eol + 2
        end = eol + 2 + length
        if len(buf) < end + 2:
            return None, 0
        if bytes(buf[end:end + 2]) != CRLF:
            raise ClientError("protocol error: missing CRLF after bulk")
        return bytes(buf[eol + 2:end]), end + 2
    if kind == b"*":
        count = int(line)
        if count == -1:
            return None, eol + 2
        pos = eol + 2
        items = []
        for _ in range(count):
            item, used = _parse_reply(bytes(buf[pos:]))
            if used == 0:
                return None, 0
            items.append(item)
            pos += used
        return items, pos
    raise ClientError("protocol error: unknown reply type %r" % kind)
