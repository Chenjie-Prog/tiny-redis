import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis import commands, protocol
from tinyredis.storage import KeySpace


def run(*args, **kwargs):
    """直接执行一条命令，返回应答元组。"""
    store = kwargs.pop("store", None)
    hooks = kwargs.pop("hooks", None)
    return commands.execute(store if store is not None else KeySpace(), args, hooks)


class ConnectionCommandTest(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(run("PING"), protocol.simple("PONG"))
        self.assertEqual(run("PING", "hi"), protocol.bulk(b"hi"))

    def test_echo(self):
        self.assertEqual(run("ECHO", "hello"), protocol.bulk(b"hello"))

    def test_select_zero_ok_other_error(self):
        self.assertEqual(run("SELECT", 0), protocol.OK)
        self.assertEqual(run("SELECT", 1)[0], "error")

    def test_unknown_command(self):
        reply = run("NOSUCH", "arg")
        self.assertEqual(reply[0], "error")
        self.assertIn("unknown command", reply[1])

    def test_empty_command(self):
        self.assertEqual(run()[0], "error")


class ArityTest(unittest.TestCase):
    def test_exact_arity(self):
        self.assertEqual(run("GET")[0], "error")
        self.assertIn("wrong number of arguments", run("GET")[1])

    def test_minimum_arity(self):
        self.assertEqual(run("SET", "k")[0], "error")
        self.assertEqual(run("MSET", "k")[0], "error")


class SetGetTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_set_get(self):
        self.assertEqual(run("SET", "k", "v", store=self.ks), protocol.OK)
        self.assertEqual(run("GET", "k", store=self.ks), protocol.bulk(b"v"))

    def test_set_nx_xx_replies(self):
        self.assertEqual(run("SET", "k", "v", "NX", store=self.ks), protocol.OK)
        self.assertEqual(run("SET", "k", "v", "NX", store=self.ks), protocol.NIL)
        self.assertEqual(run("SET", "k", "v", "XX", store=self.ks), protocol.OK)
        self.assertEqual(run("SET", "fresh", "v", "XX", store=self.ks), protocol.NIL)
        self.assertIsNone(self.ks.get(b"fresh"))

    def test_set_ex_then_get_after_expiry(self):
        import time
        self.assertEqual(run("SET", "k", "v", "PX", 50, store=self.ks), protocol.OK)
        self.assertEqual(run("GET", "k", store=self.ks), protocol.bulk(b"v"))
        time.sleep(0.08)
        self.assertEqual(run("GET", "k", store=self.ks), protocol.NIL)

    def test_set_get_option_returns_old(self):
        run("SET", "k", "old", store=self.ks)
        self.assertEqual(run("SET", "k", "new", "GET", store=self.ks),
                         protocol.bulk(b"old"))
        self.assertEqual(run("SET", "fresh", "x", "GET", store=self.ks),
                         protocol.NIL)

    def test_set_syntax_error(self):
        self.assertEqual(run("SET", "k", "v", "BADOPT", store=self.ks)[0], "error")
        self.assertEqual(run("SET", "k", "v", "EX", "abc", store=self.ks)[0], "error")
        self.assertEqual(run("SET", "k", "v", "EX", 0, store=self.ks)[0], "error")
        self.assertEqual(run("SET", "k", "v", "NX", "XX", store=self.ks)[0], "error")

    def test_getset(self):
        run("SET", "k", "v1", store=self.ks)
        self.assertEqual(run("GETSET", "k", "v2", store=self.ks), protocol.bulk(b"v1"))
        self.assertEqual(run("GET", "k", store=self.ks), protocol.bulk(b"v2"))

    def test_setnx(self):
        self.assertEqual(run("SETNX", "k", "v", store=self.ks), protocol.integer(1))
        self.assertEqual(run("SETNX", "k", "v2", store=self.ks), protocol.integer(0))
        self.assertEqual(run("GET", "k", store=self.ks), protocol.bulk(b"v"))

    def test_mget_mset(self):
        run("MSET", "a", "1", "b", "2", store=self.ks)
        self.assertEqual(run("MGET", "a", "b", "c", store=self.ks),
                         protocol.array([protocol.bulk(b"1"), protocol.bulk(b"2"),
                                         protocol.NIL]))
        self.assertEqual(run("MSET", "odd_only", store=self.ks)[0], "error")

    def test_counter_commands(self):
        self.assertEqual(run("INCR", "n", store=self.ks), protocol.integer(1))
        self.assertEqual(run("INCRBY", "n", 9, store=self.ks), protocol.integer(10))
        self.assertEqual(run("DECR", "n", store=self.ks), protocol.integer(9))
        self.assertEqual(run("DECRBY", "n", 19, store=self.ks), protocol.integer(-10))
        run("SET", "s", "abc", store=self.ks)
        self.assertEqual(run("INCR", "s", store=self.ks)[1],
                         "ERR value is not an integer or out of range")

    def test_append_strlen(self):
        run("APPEND", "k", "ab", store=self.ks)
        run("APPEND", "k", "cd", store=self.ks)
        self.assertEqual(run("STRLEN", "k", store=self.ks), protocol.integer(4))
        self.assertEqual(run("GET", "k", store=self.ks), protocol.bulk(b"abcd"))

    def test_getrange(self):
        run("SET", "k", "hello world", store=self.ks)
        self.assertEqual(run("GETRANGE", "k", 0, 4, store=self.ks),
                         protocol.bulk(b"hello"))
        self.assertEqual(run("GETRANGE", "k", -5, -1, store=self.ks),
                         protocol.bulk(b"world"))


class KeyspaceCommandTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_del_exists(self):
        run("SET", "a", "1", store=self.ks)
        self.assertEqual(run("DEL", "a", "b", store=self.ks), protocol.integer(1))
        self.assertEqual(run("EXISTS", "a", store=self.ks), protocol.integer(0))

    def test_type(self):
        run("SET", "s", "1", store=self.ks)
        run("RPUSH", "l", "x", store=self.ks)
        run("HSET", "h", "f", "v", store=self.ks)
        self.assertEqual(run("TYPE", "s", store=self.ks), protocol.simple("string"))
        self.assertEqual(run("TYPE", "l", store=self.ks), protocol.simple("list"))
        self.assertEqual(run("TYPE", "h", store=self.ks), protocol.simple("hash"))
        self.assertEqual(run("TYPE", "z", store=self.ks), protocol.simple("none"))

    def test_keys_pattern(self):
        run("SET", "user:1", "a", store=self.ks)
        run("SET", "user:2", "b", store=self.ks)
        run("SET", "post:1", "c", store=self.ks)
        self.assertEqual(len(run("KEYS", "user:*", store=self.ks)[1]), 2)

    def test_rename(self):
        run("SET", "a", "1", store=self.ks)
        self.assertEqual(run("RENAME", "a", "b", store=self.ks), protocol.OK)
        self.assertEqual(run("GET", "b", store=self.ks), protocol.bulk(b"1"))
        self.assertEqual(run("RENAME", "ghost", "x", store=self.ks)[1], "ERR no such key")

    def test_expire_ttl_persist(self):
        run("SET", "k", "v", store=self.ks)
        self.assertEqual(run("TTL", "k", store=self.ks), protocol.integer(-1))
        self.assertEqual(run("EXPIRE", "k", 100, store=self.ks), protocol.integer(1))
        self.assertEqual(run("TTL", "k", store=self.ks), protocol.integer(100))
        self.assertEqual(run("PERSIST", "k", store=self.ks), protocol.integer(1))
        self.assertEqual(run("TTL", "k", store=self.ks), protocol.integer(-1))
        self.assertEqual(run("EXPIRE", "ghost", 5, store=self.ks), protocol.integer(0))
        self.assertEqual(run("TTL", "ghost", store=self.ks), protocol.integer(-2))

    def test_dbsize_flushdb_flushall(self):
        run("SET", "a", "1", store=self.ks)
        self.assertEqual(run("DBSIZE", store=self.ks), protocol.integer(1))
        self.assertEqual(run("FLUSHDB", store=self.ks), protocol.simple("OK"))
        self.assertEqual(run("DBSIZE", store=self.ks), protocol.integer(0))

    def test_ttl_rounds_up(self):
        import time
        run("SET", "k", "v", store=self.ks)
        self.ks.expire_at(b"k", time.time() + 0.5)
        self.assertEqual(run("TTL", "k", store=self.ks), protocol.integer(1))


class ListCommandTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_push_pop_lrange(self):
        run("RPUSH", "l", "a", "b", "c", store=self.ks)
        self.assertEqual(run("LRANGE", "l", 0, -1, store=self.ks),
                         protocol.array([protocol.bulk(b"a"), protocol.bulk(b"b"),
                                         protocol.bulk(b"c")]))
        self.assertEqual(run("LPOP", "l", store=self.ks), protocol.bulk(b"a"))
        self.assertEqual(run("RPOP", "l", store=self.ks), protocol.bulk(b"c"))
        self.assertEqual(run("LLEN", "l", store=self.ks), protocol.integer(1))

    def test_lpush(self):
        run("LPUSH", "l", "a", store=self.ks)
        run("LPUSH", "l", "b", store=self.ks)
        self.assertEqual(run("LRANGE", "l", 0, -1, store=self.ks),
                         protocol.array([protocol.bulk(b"b"), protocol.bulk(b"a")]))

    def test_wrongtype(self):
        run("SET", "k", "v", store=self.ks)
        reply = run("LPUSH", "k", "x", store=self.ks)
        self.assertEqual(reply[0], "error")
        self.assertTrue(reply[1].startswith("WRONGTYPE"))


class HashCommandTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_hash_commands(self):
        self.assertEqual(run("HSET", "h", "f1", "v1", store=self.ks),
                         protocol.integer(1))
        self.assertEqual(run("HGET", "h", "f1", store=self.ks), protocol.bulk(b"v1"))
        run("HSET", "h", "f2", "v2", store=self.ks)
        self.assertEqual(run("HGETALL", "h", store=self.ks)[1],
                         [protocol.bulk(b"f1"), protocol.bulk(b"v1"),
                          protocol.bulk(b"f2"), protocol.bulk(b"v2")])
        self.assertEqual(run("HLEN", "h", store=self.ks), protocol.integer(2))
        self.assertEqual(run("HEXISTS", "h", "f1", store=self.ks), protocol.integer(1))
        self.assertEqual(run("HDEL", "h", "f1", "f2", store=self.ks),
                         protocol.integer(2))

    def test_hkeys_hvals(self):
        run("HSET", "h", "a", "1", store=self.ks)
        run("HSET", "h", "b", "2", store=self.ks)
        self.assertEqual(len(run("HKEYS", "h", store=self.ks)[1]), 2)
        self.assertEqual(len(run("HVALS", "h", store=self.ks)[1]), 2)


class ServerCommandTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_save_without_hooks_errors(self):
        self.assertEqual(run("SAVE", store=self.ks)[0], "error")

    def test_info(self):
        run("SET", "a", "1", store=self.ks)
        hooks = commands.CommandHooks(start_time=0)
        reply = run("INFO", store=self.ks, hooks=hooks)
        self.assertEqual(reply[0], "bulk")
        self.assertIn(b"tiny_redis_version", reply[1])

    def test_wrongtype_from_storage_get(self):
        run("RPUSH", "l", "x", store=self.ks)
        reply = run("GET", "l", store=self.ks)
        self.assertEqual(reply[0], "error")
        self.assertTrue(reply[1].startswith("WRONGTYPE"))


if __name__ == "__main__":
    unittest.main()
