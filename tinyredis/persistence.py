# -*- coding: utf-8 -*-
"""持久化：TRDB 快照格式 + CRC64 校验。

格式（文档版定义，见 docs/04-过期与持久化.md）::

    +--------+---------+---------+--------+-------+-----------+--------+---------+
    | "TRDB" | "0001"  | 记录 ... | 0xFF   | CRC64 (8 字节大端)                 |
    +--------+---------+---------+--------+-------+-----------+--------+---------+

一条记录::

    [0xFC + 8字节过期毫秒时间戳]   <- 可选，仅带 TTL 的键有
    1 字节类型（0x00 字符串 / 0x01 列表 / 0x02 哈希）
    键 bstr
    值：字符串=1 个 bstr；列表=uint32 数量 + N 个 bstr；
        哈希=uint32 数量 + N 对 (bstr 字段, bstr 值)

bstr = 4 字节大端长度 + 原始字节。与真实 RDB 的变长编码相比，
4 字节定长编码浪费一些空间，但代码一看就懂——这是本项目的取舍。

CRC64 采用 CRC-64/XZ 变体（ECMA-182 多项式的反射形式，与 Redis 相同）：
标准校验值 ``crc64(b"123456789") == 0x995DC9BBDF1939FA``。
"""

import os
import struct
import time
from pathlib import Path

__all__ = ["crc64", "save_snapshot", "load_snapshot", "SnapshotError"]

MAGIC = b"TRDB"
VERSION = b"0001"
EOF_OPCODE = 0xFF
EXPIRE_OPCODE = 0xFC
TYPE_STRING, TYPE_LIST, TYPE_HASH = 0x00, 0x01, 0x02


class SnapshotError(Exception):
    """快照文件损坏时抛出（CRC 校验失败、魔数不对、编码越界等）。"""


# ---- CRC64（CRC-64/XZ，即 Redis 使用的反射版 ECMA-182） --------------

def _build_table():
    poly = 0xC96C5795D7870F42  # ECMA-182 多项式 0x42F0E1EBA9EA3693 的位反转形式
    table = []
    for index in range(256):
        crc = index
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        table.append(crc)
    return table


_CRC_TABLE = _build_table()


def crc64(data):
    crc = 0xFFFFFFFFFFFFFFFF
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFFFFFFFFFF


# ---- 写入 -----------------------------------------------------------

def _bstr(out, payload):
    out += struct.pack(">I", len(payload))
    out += payload
    return out


def save_snapshot(store, path):
    """把整个键空间写成快照文件（原子写：先写临时文件再 os.replace）。"""
    out = bytearray(MAGIC + VERSION)
    for key, entry, expiry in store.iter_entries():
        if expiry is not None:
            out.append(EXPIRE_OPCODE)
            out += struct.pack(">Q", int(expiry * 1000))
        if entry.rtype == "string":
            out.append(TYPE_STRING)
            out = _bstr(out, key)
            out = _bstr(out, entry.value)
        elif entry.rtype == "list":
            out.append(TYPE_LIST)
            out = _bstr(out, key)
            out += struct.pack(">I", len(entry.value))
            for item in entry.value:
                out = _bstr(out, item)
        elif entry.rtype == "hash":
            out.append(TYPE_HASH)
            out = _bstr(out, key)
            out += struct.pack(">I", len(entry.value))
            for field, value in entry.value.items():
                out = _bstr(out, field)
                out = _bstr(out, value)
        else:  # pragma: no cover
            raise SnapshotError("unknown value type: %s" % entry.rtype)
    out.append(EOF_OPCODE)
    out += struct.pack(">Q", crc64(bytes(out)))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(bytes(out))
    os.replace(tmp, path)


# ---- 读取 -----------------------------------------------------------

class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        if self.pos + n > len(self.data):
            raise SnapshotError("unexpected end of snapshot")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def byte(self):
        return self.take(1)[0]

    def bstr(self):
        (length,) = struct.unpack(">I", self.take(4))
        if length > len(self.data):
            raise SnapshotError("invalid string length in snapshot")
        return self.take(length)

    def u32(self):
        return struct.unpack(">I", self.take(4))[0]

    def u64(self):
        return struct.unpack(">Q", self.take(8))[0]


def load_snapshot(store, path):
    """读快照文件并恢复进 ``store``。返回恢复的键数，过期键直接跳过。

    ``store`` 若为 None 则新建 KeySpace。文件不存在返回 0。
    """
    path = Path(path)
    if not path.exists():
        return 0
    if store is None:
        from .storage import KeySpace
        store = KeySpace()

    data = path.read_bytes()
    if len(data) < len(MAGIC) + len(VERSION) + 9:
        raise SnapshotError("snapshot file is truncated")
    if not data.startswith(MAGIC + VERSION):
        raise SnapshotError("bad magic: not a TRDB snapshot")

    # 文件末尾 9 字节 = EOF 操作码(1) + CRC64(8)。
    (stored_crc,) = struct.unpack(">Q", data[-8:])
    if crc64(data[:-8]) != stored_crc:
        raise SnapshotError("CRC64 mismatch: snapshot is corrupted")

    # 记录区 = 去掉 8 字节文件头和末尾 8 字节 CRC；EOF 操作码留在记录区内。
    reader = _Reader(data[len(MAGIC) + len(VERSION):-8])
    restored = 0
    while True:
        opcode = reader.byte()
        if opcode == EOF_OPCODE:
            break
        expiry = None
        if opcode == EXPIRE_OPCODE:
            expiry = reader.u64() / 1000.0
            opcode = reader.byte()
        if opcode not in (TYPE_STRING, TYPE_LIST, TYPE_HASH):
            raise SnapshotError("unknown record type: %d" % opcode)
        key = reader.bstr()
        if expiry is not None and expiry <= time.time():
            # 过期键也要读完值字节，否则会破坏后续记录的对齐。
            if opcode == TYPE_STRING:
                reader.bstr()
            elif opcode == TYPE_LIST:
                for _ in range(reader.u32()):
                    reader.bstr()
            else:
                for _ in range(reader.u32()):
                    reader.bstr()
                    reader.bstr()
            continue
        if opcode == TYPE_STRING:
            store._data[key] = _make(store, "string", reader.bstr())
        elif opcode == TYPE_LIST:
            count = reader.u32()
            value = [reader.bstr() for _ in range(count)]
            store._data[key] = _make(store, "list", value)
        else:
            count = reader.u32()
            value = {}
            for _ in range(count):
                field = reader.bstr()
                value[field] = reader.bstr()
            store._data[key] = _make(store, "hash", value)
        if expiry is not None:
            store._expires[key] = expiry
        restored += 1
    store.dirty = False
    return restored


def _make(store, rtype, value):
    # 延迟导入避免与 storage 的循环依赖。
    from .storage import StringValue, ListValue, HashValue
    return {"string": StringValue, "list": ListValue, "hash": HashValue}[rtype](value)
