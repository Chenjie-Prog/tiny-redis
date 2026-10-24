# -*- coding: utf-8 -*-
"""RESP2 协议层：解析客户端发来的命令，序列化服务器返回的应答。

RESP2（REdis Serialization Protocol）是 Redis 1.x~5.x 的线缆协议，
只有五种类型，全部以 CRLF（``\\r\\n``）结尾：

==========  ========  ============================
类型        首字节    例子
==========  ========  ============================
简单字符串  ``+``     ``+OK\\r\\n``
错误        ``-``     ``-ERR unknown command\\r\\n``
整数        ``:``     ``:42\\r\\n``
批量字符串  ``$``     ``$5\\r\\nhello\\r\\n``（``$-1`` 表示 NULL）
数组        ``*``     ``*2\\r\\n$3\\r\\nGET\\r\\n...``（``*-1`` 表示 NULL）
==========  ========  ============================

客户端 -> 服务器只发一种报文：由批量字符串组成的数组。此外 Redis 也
接受"内联命令"（一行普通文本，按空白切分），方便用 telnet 手工调试。
"""

import shlex

__all__ = ["RespError", "RespParser", "encode_command", "encode_reply",
           "simple", "error", "integer", "bulk", "array", "OK"]

CRLF = b"\r\n"
NIL = ("bulk", None)

# 单个批量字符串的上限，默认 64MB（真实 Redis 是 512MB）。
MAX_BULK_LEN = 64 * 1024 * 1024
# 内联命令一行的上限，超过直接按协议错误断开。
MAX_INLINE_LEN = 64 * 1024


class RespError(Exception):
    """协议层错误：解析失败或应答中的错误类型。连接层负责区分这两者。"""


class RespParser:
    """增量式 RESP 解析器。

    用法：把 socket 读到的字节 ``feed()`` 进来，反复调用
    ``next_command()`` 取出完整的命令数组；返回 ``None`` 表示
    数据还不够，继续等下一次读事件。这样天然支持 TCP 粘包/半包。
    """

    def __init__(self, max_bulk_len=MAX_BULK_LEN):
        self._buf = bytearray()
        self._max_bulk = max_bulk_len

    def feed(self, data):
        self._buf += data

    def next_command(self):
        """解析下一条命令；不够则返回 None，协议非法则抛 RespError。"""
        buf = self._buf
        if not buf:
            return None
        if buf[0:1] == b"*":
            return self._parse_array()
        return self._parse_inline()

    # ---- 数组模式：*N\r\n 之后跟 N 个 $len\r\n<payload>\r\n ----

    def _parse_array(self):
        buf = self._buf
        eol = buf.find(b"\n")
        if eol < 0:
            return None
        header = bytes(buf[1:eol]).strip()
        try:
            count = int(header)
        except ValueError:
            raise RespError("Protocol error: invalid multibulk length") from None
        if count > 1024 * 1024:
            raise RespError("Protocol error: invalid multibulk length") from None
        if count <= 0:
            del buf[:eol + 1]
            return []

        pos = eol + 1
        args = []
        for _ in range(count):
            if pos >= len(buf) or buf[pos:pos + 1] != b"$":
                if pos < len(buf) and pos + 1 < len(buf):
                    raise RespError("Protocol error: expected '$'")
                return None
            eol = buf.find(b"\n", pos)
            if eol < 0:
                return None
            try:
                length = int(bytes(buf[pos + 1:eol]).strip())
            except ValueError:
                raise RespError("Protocol error: invalid bulk length") from None
            if length < 0 or length > self._max_bulk:
                raise RespError("Protocol error: invalid bulk length") from None
            start, end = eol + 1, eol + 1 + length + 2
            if len(buf) < end:
                return None
            if buf[end - 2:end] != CRLF:
                raise RespError("Protocol error: expected CRLF after bulk") from None
            args.append(bytes(buf[start:end - 2]))
            pos = end
        del buf[:pos]
        return args

    # ---- 内联模式：一行文本按空白切分，支持引号 ----

    def _parse_inline(self):
        buf = self._buf
        eol = buf.find(b"\n")
        if eol < 0:
            if len(buf) > MAX_INLINE_LEN:
                raise RespError("Protocol error: too big inline request") from None
            return None
        line = bytes(buf[:eol]).rstrip(b"\r")
        del buf[:eol + 1]
        if not line:
            return []
        try:
            # shlex 处理 GET "my key" 这类带引号的内联命令；解析失败就退回普通切分。
            words = shlex.split(line.decode("utf-8", "replace"))
        except ValueError:
            words = line.split()
        return [w.encode("utf-8") for w in words]


# ---- 服务器 -> 客户端方向：应答编码 ---------------------------------

def simple(text):
    """简单字符串应答。"""
    return ("simple", text)


def error(text):
    """错误应答。"""
    return ("error", text)


def integer(n):
    """整数应答。"""
    return ("int", n)


def bulk(data):
    """批量字符串应答；None 编码为 NULL（$-1）。"""
    return ("bulk", data)


def array(items):
    """数组应答；None 编码为 NULL（*-1）。"""
    return ("array", items)


OK = simple("OK")


def encode_reply(reply):
    """把命令层的返回值（递归元组）编码成 RESP 字节流。

    命令统一返回 ``("simple"|"error"|"int"|"bulk"|"array", payload)``
    元组，本函数负责变成线缆格式。
    """
    kind, payload = reply
    if kind == "simple":
        return b"+" + payload.encode("utf-8") + CRLF
    if kind == "error":
        return b"-" + payload.encode("utf-8") + CRLF
    if kind == "int":
        return b":" + str(int(payload)).encode("ascii") + CRLF
    if kind == "bulk":
        if payload is None:
            return b"$-1" + CRLF
        if not isinstance(payload, (bytes, bytearray)):
            payload = str(payload).encode("utf-8")
        return b"$%d" % len(payload) + CRLF + bytes(payload) + CRLF
    if kind == "array":
        if payload is None:
            return b"*-1" + CRLF
        out = [b"*%d" % len(payload) + CRLF]
        out.extend(encode_reply(item) for item in payload)
        return b"".join(out)
    raise RespError("unknown reply kind: %r" % (kind,))


def encode_command(args):
    """把参数列表编码成客户端请求报文（RESP 数组）。"""
    out = [b"*%d" % len(args) + CRLF]
    for arg in args:
        if isinstance(arg, str):
            arg = arg.encode("utf-8")
        elif isinstance(arg, int):
            arg = str(arg).encode("ascii")
        elif not isinstance(arg, (bytes, bytearray)):
            arg = str(arg).encode("utf-8")
        out.append(b"$%d" % len(arg) + CRLF + bytes(arg) + CRLF)
    return b"".join(out)
