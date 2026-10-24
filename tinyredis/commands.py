# -*- coding: utf-8 -*-
"""命令层：命令表 + 参数检查 + 各命令的实现。

每条命令注册为 ``(handler, arity)``。``arity`` 语义与 Redis 一致：
正数表示参数个数必须严格相等（含命令名本身），负数表示"至少 |arity| 个"。

命令函数签名统一为 ``handler(store, args) -> reply``，``args`` **不含**
命令名。返回协议层的 ``simple/error/int/bulk/array`` 元组，由服务器直接
编码发送。预期中的错误（参数错误、类型错误等）以 ``error(...)`` 应答返回，
而不是抛异常——抛异常留给真正的内部 bug。
"""

from . import protocol, storage

__all__ = ["execute", "COMMANDS"]


def _err_args(name):
    return protocol.error("ERR wrong number of arguments for '%s' command" % name)


def _need(store, args, name, arity):
    if len(args) != arity:
        return _err_args(name)
    return None


def _need_min(store, args, name, minimum):
    if len(args) < minimum:
        return _err_args(name)
    return None


# ---- 连接类 ---------------------------------------------------------

def cmd_ping(store, args):
    # PING 可以带一个可选的消息参数。
    if len(args) > 1:
        return _err_args("ping")
    if args:
        return protocol.bulk(args[0])
    return protocol.simple("PONG")


def cmd_echo(store, args):
    if err := _need(store, args, "echo", 1):
        return err
    return protocol.bulk(args[0])


def cmd_select(store, args):
    # tiny-redis 只有 0 号库；SELECT 收下任何 0，其他一律报错。
    if err := _need(store, args, "select", 1):
        return err
    try:
        index = int(args[0])
    except ValueError:
        return protocol.error("ERR value is not an integer or out of range")
    if index != 0:
        return protocol.error("ERR DB index is out of range")
    return protocol.OK


def cmd_quit(_store, _args):
    return protocol.OK  # 真正的关闭动作由连接层处理


def cmd_command(_store, args):
    # redis-cli 启动时会发 COMMAND DOCS 拉文档，返回空数组让它安静。
    if args and args[0].upper() == "DOCS":
        return protocol.array([])
    return protocol.error("ERR unknown command 'COMMAND'")


def cmd_hello(_store, args):
    """HELLO 握手。tiny-redis 只支持 RESP2，客户端请求协议 3 时报 NOPROTO。

    应答是扁平数组形式的 map（RESP2 没有 map 类型），字段与真实 Redis 相同：
    server / version / proto / id / mode / role / modules。
    """
    if args:
        try:
            proto = int(args[0])
        except ValueError:
            return protocol.error("NOPROTO unsupported protocol version")
        if proto != 2:
            return protocol.error("NOPROTO unsupported protocol version")
    # HELLO [AUTH u p] [SETNAME name] 的参数收下但不做任何事。
    return protocol.array([
        protocol.bulk(b"server"), protocol.bulk(b"redis"),
        protocol.bulk(b"version"), protocol.bulk(b"7.0.0"),
        protocol.bulk(b"proto"), protocol.integer(2),
        protocol.bulk(b"id"), protocol.integer(1),
        protocol.bulk(b"mode"), protocol.bulk(b"standalone"),
        protocol.bulk(b"role"), protocol.bulk(b"master"),
        protocol.bulk(b"modules"), protocol.array([]),
    ])


# ---- 字符串 ---------------------------------------------------------

def _parse_set_options(store, args, name):
    """解析 SET 的附加选项，返回 (flags dict, error_reply)。"""
    flags = {"expire_seconds": None, "expire_ms": None, "nx": False,
             "xx": False, "keep_ttl": False, "get_old": False}
    i = 0
    while i < len(args):
        opt = args[i].upper()
        if opt == b"NX":
            flags["nx"] = True
        elif opt == b"XX":
            flags["xx"] = True
        elif opt == b"KEPTTTL":
            flags["keep_ttl"] = True
        elif opt == b"GET":
            flags["get_old"] = True
        elif opt in (b"EX", b"PX"):
            if i + 1 >= len(args):
                return None, protocol.error("ERR syntax error")
            try:
                seconds = int(args[i + 1])
            except ValueError:
                return None, protocol.error("ERR value is not an integer or out of range")
            if seconds <= 0:
                return None, protocol.error("ERR invalid expire time in '%s' command" % name)
            if opt == b"EX":
                flags["expire_seconds"] = seconds
            else:
                flags["expire_ms"] = seconds
            i += 1
        elif opt in (b"EXAT", b"PXAT", b"ABSTTL"):
            return None, protocol.error("ERR syntax error")  # 未实现，明确拒绝
        else:
            return None, protocol.error("ERR syntax error")
        i += 1
    if flags["nx"] and flags["xx"]:
        return None, protocol.error("ERR syntax error")
    return flags, None


def cmd_set(store, args):
    if err := _need_min(store, args, "set", 2):
        return err
    key, value = args[0], args[1]
    flags, err = _parse_set_options(store, args[2:], "set")
    if err:
        return err
    status, old = store.set(key, value, nx=flags["nx"], xx=flags["xx"],
                            expire_seconds=flags["expire_seconds"],
                            expire_ms=flags["expire_ms"],
                            keep_ttl=flags["keep_ttl"],
                            get_old=flags["get_old"])
    if status != "ok":
        return protocol.bulk(old) if flags["get_old"] else protocol.NIL
    return protocol.bulk(old) if flags["get_old"] else protocol.OK


def cmd_get(store, args):
    if err := _need(store, args, "get", 1):
        return err
    return protocol.bulk(store.get(args[0]))


def cmd_getset(store, args):
    if err := _need(store, args, "getset", 2):
        return err
    key, value = args
    _, old = store.set(key, value)
    return protocol.bulk(old)


def cmd_setnx(store, args):
    if err := _need(store, args, "setnx", 2):
        return err
    return protocol.integer(store.setnx(args[0], args[1]))


def cmd_mget(store, args):
    if err := _need_min(store, args, "mget", 1):
        return err
    return protocol.array([protocol.bulk(v) for v in store.mget(*args)])


def cmd_mset(store, args):
    if len(args) == 0 or len(args) % 2 != 0:
        return _err_args("mset")
    store.mset([(args[i], args[i + 1]) for i in range(0, len(args), 2)])
    return protocol.OK


def cmd_incrby(store, args, delta=None):
    if err := _need(store, args, "incrby", 2 if delta is None else 1):
        return err
    if delta is None:
        try:
            delta = int(args[1])
        except ValueError:
            return protocol.error("ERR value is not an integer or out of range")
    try:
        return protocol.integer(store.incr_by(args[0], delta))
    except storage.NotIntegerError as exc:
        return protocol.error("ERR %s" % exc)
    except storage.WrongTypeError as exc:
        return protocol.error(str(exc).split(" %r" % args[0])[0].strip())


def cmd_incr(store, args):
    if err := _need(store, args, "incr", 1):
        return err
    try:
        return protocol.integer(store.incr_by(args[0], 1))
    except storage.NotIntegerError as exc:
        return protocol.error("ERR %s" % exc)
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_decr(store, args):
    if err := _need(store, args, "decr", 1):
        return err
    try:
        return protocol.integer(store.incr_by(args[0], -1))
    except storage.NotIntegerError as exc:
        return protocol.error("ERR %s" % exc)
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_decrby(store, args):
    if err := _need(store, args, "decrby", 2):
        return err
    try:
        delta = int(args[1])
    except ValueError:
        return protocol.error("ERR value is not an integer or out of range")
    try:
        return protocol.integer(store.incr_by(args[0], -delta))
    except storage.NotIntegerError as exc:
        return protocol.error("ERR %s" % exc)
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_append(store, args):
    if err := _need(store, args, "append", 2):
        return err
    return protocol.integer(store.append(args[0], args[1]))


def cmd_strlen(store, args):
    if err := _need(store, args, "strlen", 1):
        return err
    return protocol.integer(store.strlen(args[0]))


def cmd_getrange(store, args):
    if err := _need(store, args, "getrange", 3):
        return err
    try:
        start, end = int(args[1]), int(args[2])
    except ValueError:
        return protocol.error("ERR value is not an integer or out of range")
    return protocol.bulk(store.getrange(args[0], start, end))


# ---- 键空间 ---------------------------------------------------------

def cmd_del(store, args):
    if err := _need_min(store, args, "del", 1):
        return err
    return protocol.integer(store.delete(*args))


def cmd_exists(store, args):
    if err := _need_min(store, args, "exists", 1):
        return err
    return protocol.integer(store.exists(*args))


def cmd_type(store, args):
    if err := _need(store, args, "type", 1):
        return err
    return protocol.simple(store.key_type(args[0]))


def cmd_keys(store, args):
    if err := _need(store, args, "keys", 1):
        return err
    return protocol.array([protocol.bulk(k) for k in store.keys(args[0])])


def cmd_rename(store, args):
    if err := _need(store, args, "rename", 2):
        return err
    if not store.rename(args[0], args[1]):
        return protocol.error("ERR no such key")
    return protocol.OK


def cmd_expire(store, args):
    return _expire(store, args, "expire", 1)


def cmd_pexpire(store, args):
    return _expire(store, args, "pexpire", 1000)


def _expire(store, args, name, factor):
    if err := _need(store, args, name, 2):
        return err
    try:
        seconds = int(args[1])
    except ValueError:
        return protocol.error("ERR value is not an integer or out of range")
    if seconds <= 0:
        # 非 正过期时间相当于 DEL。
        return protocol.integer(store.delete(args[0]))
    import time
    ok = store.expire_at(args[0], time.time() + seconds / factor)
    return protocol.integer(1 if ok else 0)


def cmd_ttl(store, args):
    return _ttl(store, args, "ttl", 1000)


def cmd_pttl(store, args):
    return _ttl(store, args, "pttl", 1)


def _ttl(store, args, name, factor):
    if err := _need(store, args, name, 1):
        return err
    ms = store.ttl_ms(args[0])
    if ms == -2:
        return protocol.integer(-2)
    if ms == -1:
        return protocol.integer(-1)
    # TTL 向上取整毫秒再换算成秒，保证刚设置的 TTL=1s 不会立刻读出 0。
    return protocol.integer((ms + factor - 1) // factor)


def cmd_persist(store, args):
    if err := _need(store, args, "persist", 1):
        return err
    return protocol.integer(store.persist(args[0]))


def cmd_dbsize(store, args):
    return protocol.integer(store.dbsize())


def cmd_flushdb(store, args):
    store.flushdb()
    return protocol.OK


def cmd_flushall(store, args):
    store.flushdb()
    return protocol.OK


# ---- 列表 -----------------------------------------------------------

def cmd_lpush(store, args):
    return _push(store, args, "lpush", left=True)


def cmd_rpush(store, args):
    return _push(store, args, "rpush", left=False)


def _push(store, args, name, left):
    if err := _need_min(store, args, name, 2):
        return err
    try:
        return protocol.integer(store.push(args[0], args[1:], left=left))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_lrange(store, args):
    if err := _need(store, args, "lrange", 3):
        return err
    try:
        start, end = int(args[1]), int(args[2])
    except ValueError:
        return protocol.error("ERR value is not an integer or out of range")
    try:
        return protocol.array([protocol.bulk(v) for v in store.lrange(args[0], start, end)])
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_llen(store, args):
    if err := _need(store, args, "llen", 1):
        return err
    try:
        return protocol.integer(store.llen(args[0]))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_lpop(store, args):
    return _pop(store, args, "lpop", left=True)


def cmd_rpop(store, args):
    return _pop(store, args, "rpop", left=False)


def _pop(store, args, name, left):
    if err := _need(store, args, name, 1):
        return err
    try:
        return protocol.bulk(store.pop(args[0], left=left))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


# ---- 哈希 -----------------------------------------------------------

def cmd_hset(store, args):
    # HSET key field value [field value ...]，支持一次写多个字段值对。
    if len(args) < 3 or len(args) % 2 != 1:
        return _err_args("hset")
    try:
        added = 0
        for i in range(1, len(args), 2):
            added += store.hset(args[0], args[i], args[i + 1])
        return protocol.integer(added)
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hget(store, args):
    if err := _need(store, args, "hget", 2):
        return err
    try:
        return protocol.bulk(store.hget(args[0], args[1]))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hgetall(store, args):
    if err := _need(store, args, "hgetall", 1):
        return err
    try:
        return protocol.array([protocol.bulk(v) for v in store.hgetall(args[0])])
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hdel(store, args):
    if err := _need_min(store, args, "hdel", 2):
        return err
    try:
        return protocol.integer(store.hdel(args[0], *args[1:]))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hexists(store, args):
    if err := _need(store, args, "hexists", 2):
        return err
    try:
        return protocol.integer(store.hexists(args[0], args[1]))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hkeys(store, args):
    if err := _need(store, args, "hkeys", 1):
        return err
    try:
        return protocol.array([protocol.bulk(v) for v in store.hkeys(args[0])])
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hvals(store, args):
    if err := _need(store, args, "hvals", 1):
        return err
    try:
        return protocol.array([protocol.bulk(v) for v in store.hvals(args[0])])
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


def cmd_hlen(store, args):
    if err := _need(store, args, "hlen", 1):
        return err
    try:
        return protocol.integer(store.hlen(args[0]))
    except storage.WrongTypeError:
        return protocol.error("WRONGTYPE Operation against a key holding the wrong kind of value")


# ---- 服务器管理（SAVE/SHUTDOWN 通过 hooks 与服务器交互） --------------

def cmd_save(store, args, hooks):
    if args:
        return _err_args("save")
    if hooks is None or not getattr(hooks, "save_snapshot", None):
        return protocol.error("ERR persistence is not configured")
    hooks.save_snapshot()
    return protocol.OK


def cmd_bgsave(store, args, hooks):
    # tiny-redis 是单线程的，BGSAVE 退化为同步 SAVE（教程第 4 章解释原因）。
    return cmd_save(store, args, hooks)


def cmd_shutdown(store, args, hooks):
    mode = None
    if len(args) > 1:
        return _err_args("shutdown")
    if args:
        mode = args[0].upper()
        if mode not in (b"SAVE", b"NOSAVE"):
            return protocol.error("ERR syntax error")
    if hooks is not None and getattr(hooks, "request_shutdown", None):
        hooks.request_shutdown(save=(mode == b"SAVE"))
    return protocol.NIL  # 通常发不出去：服务器已经退出


def cmd_info(store, args, hooks):
    import time
    start = getattr(hooks, "start_time", None)
    uptime = int(time.time() - start) if start else 0
    lines = [
        "# Server",
        "tiny_redis_version:0.1.0",
        "redis_version:7.0.0",  # 向 redis-cli 自报一个兼容版本号
        "uptime_in_seconds:%d" % uptime,
        "# Keyspace",
        "db0:keys=%d,expires=%d" % (store.dbsize(), len(store._expires)),
    ]
    return protocol.bulk(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _unknown(name):
    return protocol.error(
        "ERR unknown command '%s'" % name.decode("utf-8", "replace"))


class CommandHooks:
    """服务器注入 SAVE/SHUTDOWN 行为的钩子对象（鸭子类型）。"""

    def __init__(self, save_snapshot=None, request_shutdown=None, start_time=None):
        self.save_snapshot = save_snapshot
        self.request_shutdown = request_shutdown
        self.start_time = start_time


# (handler, arity)。arity 语义见模块 docstring。
COMMANDS = {
    "PING": (cmd_ping, -1),
    "ECHO": (cmd_echo, 2),
    "SELECT": (cmd_select, 2),
    "QUIT": (cmd_quit, -1),
    "COMMAND": (cmd_command, -1),
    "HELLO": (cmd_hello, -1),
    "INFO": (cmd_info, -1),
    # 字符串
    "SET": (cmd_set, -3),
    "GET": (cmd_get, 2),
    "GETSET": (cmd_getset, 3),
    "SETNX": (cmd_setnx, 3),
    "MGET": (cmd_mget, -2),
    "MSET": (cmd_mset, -3),
    "INCR": (cmd_incr, 2),
    "INCRBY": (cmd_incrby, 3),
    "DECR": (cmd_decr, 2),
    "DECRBY": (cmd_decrby, 3),
    "APPEND": (cmd_append, 3),
    "STRLEN": (cmd_strlen, 2),
    "GETRANGE": (cmd_getrange, 4),
    # 键空间
    "DEL": (cmd_del, -2),
    "UNLINK": (cmd_del, -2),  # 单线程模型里 UNLINK 就是 DEL
    "EXISTS": (cmd_exists, -2),
    "TYPE": (cmd_type, 2),
    "KEYS": (cmd_keys, 2),
    "RENAME": (cmd_rename, 3),
    "EXPIRE": (cmd_expire, 3),
    "PEXPIRE": (cmd_pexpire, 3),
    "TTL": (cmd_ttl, 2),
    "PTTL": (cmd_pttl, 2),
    "PERSIST": (cmd_persist, 2),
    "DBSIZE": (cmd_dbsize, 1),
    "FLUSHDB": (cmd_flushdb, -1),
    "FLUSHALL": (cmd_flushall, -1),
    # 列表
    "LPUSH": (cmd_lpush, -3),
    "RPUSH": (cmd_rpush, -3),
    "LRANGE": (cmd_lrange, 4),
    "LLEN": (cmd_llen, 2),
    "LPOP": (cmd_lpop, 2),
    "RPOP": (cmd_rpop, 2),
    # 哈希
    "HSET": (cmd_hset, -4),
    "HGET": (cmd_hget, 3),
    "HGETALL": (cmd_hgetall, 2),
    "HDEL": (cmd_hdel, -3),
    "HEXISTS": (cmd_hexists, 3),
    "HKEYS": (cmd_hkeys, 2),
    "HVALS": (cmd_hvals, 2),
    "HLEN": (cmd_hlen, 2),
    # 管理
    "SAVE": (cmd_save, -1),
    "BGSAVE": (cmd_bgsave, -1),
    "SHUTDOWN": (cmd_shutdown, -1),
}


def execute(store, args, hooks=None):
    """执行一条命令（args 含命令名），返回应答元组。永远不抛常规错误。"""
    if not args:
        return protocol.error("ERR empty command")
    # 统一编码成 bytes：str 按 utf-8，int 按十进制（服务器只会传来 bytes，
    # 这里多做一步是为了让存储层面对的类型永远一致）。
    args = [
        arg.encode("utf-8") if isinstance(arg, str)
        else str(arg).encode("ascii") if isinstance(arg, int)
        else bytes(arg)
        for arg in args
    ]
    name = args[0].upper().decode("ascii", "replace")
    entry = COMMANDS.get(name)
    if entry is None:
        return _unknown(args[0])
    handler, arity = entry
    given = len(args) - 1
    if arity > 0 and given != arity - 1:
        return _err_args(name.lower())
    if arity < 0 and given < -arity - 1:
        return _err_args(name.lower())
    try:
        if handler in (cmd_save, cmd_bgsave, cmd_shutdown, cmd_info):
            return handler(store, args[1:], hooks)
        return handler(store, args[1:])
    except storage.WrongTypeError as exc:
        message = str(exc)
        # 存储层为方便测试带的键名后缀去掉，保持与 Redis 一致。
        return protocol.error(message.split(" %r" % args[1])[0].strip() if len(args) > 1 else message)
    except Exception:  # pragma: no cover - 兜底，别让一个 bug 拖垮整个事件循环
        import traceback
        traceback.print_exc()
        return protocol.error("ERR internal error")
