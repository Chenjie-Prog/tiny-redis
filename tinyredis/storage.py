# -*- coding: utf-8 -*-
"""键空间：三种数据结构 + TTL 过期策略。

设计要点（与真实 Redis 一致）：

* 所有键存进一个 ``dict``，值按类型分三个小类（字符串/列表/哈希）。
* 过期时间是**绝对时间戳**（Unix 秒，浮点毫秒精度），不存"剩余秒数"，
  因为持久化恢复后剩余时间应该从文件里的绝对时刻继续算。
* 过期删除采用两层策略：
  1. **惰性删除**——任何访问先检查是否过期，过期就当它不存在并删除；
  2. **主动过期**——事件循环每隔一小段时间从过期表里随机采样，
     把已过期的键删掉（见 ``expired_cycle``），避免大量冷键占着内存。
"""

import fnmatch
import random
import time

__all__ = ["StringValue", "ListValue", "HashValue", "KeySpace",
           "WrongTypeError", "NotIntegerError"]

TYPE_STRING = "string"
TYPE_LIST = "list"
TYPE_HASH = "hash"
TYPE_NONE = "none"


class WrongTypeError(Exception):
    """对持有错误类型值的键执行操作时抛出（对应 Redis 的 WRONGTYPE）。"""


class NotIntegerError(Exception):
    """字符串不是合法整数（对应 Redis 的 'value is not an integer'）。"""


class StringValue:
    rtype = TYPE_STRING

    def __init__(self, value):
        self.value = value


class ListValue:
    rtype = TYPE_LIST

    def __init__(self, value):
        self.value = value


class HashValue:
    rtype = TYPE_HASH

    def __init__(self, value):
        self.value = value


def _require_type(entry, expected, key):
    if entry.rtype != expected:
        raise WrongTypeError(
            "WRONGTYPE Operation against a key holding the wrong kind of value %r" % key)


def _parse_int(text):
    try:
        value = int(text)
    except (ValueError, TypeError):
        raise NotIntegerError("value is not an integer or out of range") from None
    if not (-(2 ** 63) <= value < 2 ** 63):
        raise NotIntegerError("value is not an integer or out of range")
    return value


def _clamp_range(start, end, length):
    """把 Redis 风格的负数下标夹进 [0, length]，闭区间，不合法返回 None。"""
    if start < 0:
        start = max(length + start, 0)
    if end < 0:
        end = length + end
    if start > end or start >= length:
        return None
    return start, min(end, length - 1)


class KeySpace:
    """单库键空间：数据字典 + 过期字典 + 三种结构的读写操作。"""

    def __init__(self):
        self._data = {}
        self._expires = {}  # key -> 过期时刻（Unix 秒，浮点）
        self.dirty = False  # 有写入时置位，供自动快照判断

    # ---- 过期 -------------------------------------------------------

    def _purge(self, key):
        """惰性过期：键已过期则删除。返回键当前是否存在。"""
        deadline = self._expires.get(key)
        if deadline is not None and time.time() >= deadline:
            self._expires.pop(key, None)
            self._data.pop(key, None)
            self.dirty = True
        return key in self._data

    def expire_at(self, key, when):
        """设置绝对过期时刻（Unix 秒）。键不存在返回 False。"""
        if not self._purge(key):
            return False
        self._expires[key] = float(when)
        self.dirty = True
        return True

    def persist(self, key):
        """移除过期时间。返回是否真的移除了一个。"""
        if not self._purge(key) or key not in self._expires:
            return False
        del self._expires[key]
        self.dirty = True
        return True

    def ttl_ms(self, key):
        """剩余毫秒。-2 键不存在，-1 无过期时间。"""
        if not self._purge(key):
            return -2
        deadline = self._expires.get(key)
        if deadline is None:
            return -1
        return max(int((deadline - time.time()) * 1000), 0)

    def expired_cycle(self, samples=20, max_rounds=4):
        """主动过期：随机采样过期表，删除已过期键。返回删除数量。

        真实 Redis 会一直循环直到过期比例低于 25%，这里简化为最多
        ``max_rounds`` 轮，避免极端情况下长时间占用事件循环。
        """
        removed = 0
        for _ in range(max_rounds):
            keys = list(self._expires)
            if not keys:
                break
            picked = random.sample(keys, min(samples, len(keys)))
            now = time.time()
            round_removed = 0
            for key in picked:
                if now >= self._expires[key]:
                    self._expires.pop(key, None)
                    self._data.pop(key, None)
                    round_removed += 1
            removed += round_removed
            if round_removed < len(picked) // 4:  # 过期比例够低，可以歇了
                break
        if removed:
            self.dirty = True
        return removed

    # ---- 通用 -------------------------------------------------------

    def exists(self, *keys):
        return sum(1 for key in keys if self._purge(key))

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if self._purge(key):
                del self._data[key]
                self._expires.pop(key, None)
                removed += 1
        if removed:
            self.dirty = True
        return removed

    def rename(self, src, dst):
        """RENAME：src 不存在返回 False；dst 已存在则被覆盖（连同 TTL）。"""
        if not self._purge(src):
            return False
        entry = self._data.pop(src)
        expiry = self._expires.pop(src, None)
        self._purge(dst)
        self._expires.pop(dst, None)
        self._data[dst] = entry
        if expiry is not None:  # TTL 跟着键走
            self._expires[dst] = expiry
        self.dirty = True
        return True

    def key_type(self, key):
        if not self._purge(key):
            return TYPE_NONE
        return self._data[key].rtype

    def keys(self, pattern=b"*"):
        if isinstance(pattern, str):
            pattern = pattern.encode("utf-8")
        # Redis 的 [^x] 表示取反，fnmatch 用 [!x]，做个翻译。
        glob = pattern.decode("utf-8", "replace").replace("[^", "[!")

        def as_text(key):
            return key.decode("utf-8", "replace") if isinstance(key, bytes) else key

        return [key for key in list(self._data) if self._purge(key)
                and fnmatch.fnmatchcase(as_text(key), glob)]

    def dbsize(self):
        return sum(1 for key in list(self._data) if self._purge(key))

    def flushdb(self):
        count = len(self._data)
        self._data.clear()
        self._expires.clear()
        self.dirty = True
        return count

    def iter_entries(self):
        """供持久化遍历：惰性清理后返回 [(key, entry, expiry|None), ...]。"""
        result = []
        for key in list(self._data):
            if self._purge(key):
                result.append((key, self._data[key], self._expires.get(key)))
        return result

    # ---- 字符串 -----------------------------------------------------

    def get(self, key):
        if not self._purge(key):
            return None
        entry = self._data[key]
        if entry.rtype != TYPE_STRING:
            raise WrongTypeError(
                "WRONGTYPE Operation against a key holding the wrong kind of value")
        return entry.value

    def set(self, key, value, expire_seconds=None, expire_ms=None,
            nx=False, xx=False, keep_ttl=False, get_old=False):
        """SET 的完整语义，返回应答需要的旧值信息。

        返回 ``(status, old)``：status 为 "ok"/"nx"/"xx"（后两者表示
        因 NX/XX 条件不满足而没有写入）；old 是 GET 选项要的旧值。
        """
        old = self.get(key) if self._purge(key) else None
        exists = old is not None
        if (nx and exists) or (xx and not exists):
            return ("nx" if nx else "xx"), old
        entry = StringValue(value)
        self._data[key] = entry
        if keep_ttl:
            if not exists:
                self._expires.pop(key, None)
        else:
            self._expires.pop(key, None)
            if expire_seconds is not None:
                self._expires[key] = time.time() + expire_seconds
            elif expire_ms is not None:
                self._expires[key] = time.time() + expire_ms / 1000.0
        self.dirty = True
        return "ok", old

    def setnx(self, key, value):
        if self._purge(key):
            return 0
        self._data[key] = StringValue(value)
        self.dirty = True
        return 1

    def incr_by(self, key, delta):
        if not self._purge(key):
            current = 0
        else:
            entry = self._data[key]
            if entry.rtype != TYPE_STRING:
                raise WrongTypeError(
                    "WRONGTYPE Operation against a key holding the wrong kind of value")
            current = _parse_int(entry.value)
        total = current + delta
        if not (-(2 ** 63) <= total < 2 ** 63):
            raise NotIntegerError("increment or decrement would overflow")
        self._data[key] = StringValue(str(total).encode("ascii"))
        self.dirty = True
        return total

    def append(self, key, value):
        if not self._purge(key):
            current = b""
        else:
            entry = self._data[key]
            if entry.rtype != TYPE_STRING:
                raise WrongTypeError(
                    "WRONGTYPE Operation against a key holding the wrong kind of value")
            current = entry.value
        self._data[key] = StringValue(current + value)
        self.dirty = True
        return len(current + value)

    def strlen(self, key):
        value = self.get(key)
        return len(value) if value is not None else 0

    def getrange(self, key, start, end):
        value = self.get(key)
        if value is None:
            return b""
        clamped = _clamp_range(start, end, len(value))
        if clamped is None:
            return b""
        begin, finish = clamped
        return value[begin:finish + 1]

    def mget(self, *keys):
        out = []
        for key in keys:
            try:
                out.append(self.get(key))
            except WrongTypeError:
                out.append(None)  # MGET 遇到错误类型返回 nil 而不是报错
        return out

    def mset(self, pairs):
        for key, value in pairs:
            self._data[key] = StringValue(value)
        self.dirty = True

    # ---- 列表 -------------------------------------------------------

    def _list(self, key, create=False):
        if not self._purge(key):
            if not create:
                return None
            entry = ListValue([])
            self._data[key] = entry
            return entry
        entry = self._data[key]
        _require_type(entry, TYPE_LIST, key)
        return entry

    def push(self, key, values, left=False):
        entry = self._list(key, create=True)
        for value in values:
            if left:
                entry.value.insert(0, value)
            else:
                entry.value.append(value)
        self.dirty = True
        return len(entry.value)

    def lrange(self, key, start, end):
        entry = self._list(key)
        if entry is None:
            return []
        clamped = _clamp_range(start, end, len(entry.value))
        if clamped is None:
            return []
        begin, finish = clamped
        return entry.value[begin:finish + 1]

    def llen(self, key):
        entry = self._list(key)
        return len(entry.value) if entry is not None else 0

    def pop(self, key, left=True):
        entry = self._list(key)
        if entry is None or not entry.value:
            return None
        value = entry.value.pop(0) if left else entry.value.pop()
        if not entry.value:
            self.delete(key)
        self.dirty = True
        return value

    # ---- 哈希 -------------------------------------------------------

    def _hash(self, key, create=False):
        if not self._purge(key):
            if not create:
                return None
            entry = HashValue({})
            self._data[key] = entry
            return entry
        entry = self._data[key]
        _require_type(entry, TYPE_HASH, key)
        return entry

    def hset(self, key, field, value):
        entry = self._hash(key, create=True)
        is_new = field not in entry.value
        entry.value[field] = value
        self.dirty = True
        return 1 if is_new else 0

    def hget(self, key, field):
        entry = self._hash(key)
        return entry.value.get(field) if entry is not None else None

    def hgetall(self, key):
        entry = self._hash(key)
        if entry is None:
            return []
        flat = []
        for field, value in entry.value.items():
            flat.extend((field, value))
        return flat

    def hdel(self, key, *fields):
        entry = self._hash(key)
        if entry is None:
            return 0
        removed = 0
        for field in fields:
            if entry.value.pop(field, None) is not None:
                removed += 1
        if removed:
            self.dirty = True
        if not entry.value:
            self.delete(key)
        return removed

    def hexists(self, key, field):
        entry = self._hash(key)
        return 1 if entry is not None and field in entry.value else 0

    def hkeys(self, key):
        entry = self._hash(key)
        return list(entry.value.keys()) if entry is not None else []

    def hvals(self, key):
        entry = self._hash(key)
        return list(entry.value.values()) if entry is not None else []

    def hlen(self, key):
        entry = self._hash(key)
        return len(entry.value) if entry is not None else 0
