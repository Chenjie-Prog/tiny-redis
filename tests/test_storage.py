import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis.storage import (KeySpace, NotIntegerError, WrongTypeError,
                               TYPE_HASH, TYPE_LIST, TYPE_NONE, TYPE_STRING)


class StringTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_set_get_roundtrip(self):
        status, _ = self.ks.set(b"k", b"v")
        self.assertEqual(status, "ok")
        self.assertEqual(self.ks.get(b"k"), b"v")

    def test_get_missing(self):
        self.assertIsNone(self.ks.get(b"nope"))

    def test_overwrite(self):
        self.ks.set(b"k", b"1")
        self.ks.set(b"k", b"2")
        self.assertEqual(self.ks.get(b"k"), b"2")

    def test_get_wrong_type_raises(self):
        self.ks.push(b"list", [b"x"])
        with self.assertRaises(WrongTypeError):
            self.ks.get(b"list")

    def test_binary_value(self):
        blob = bytes(range(256))
        self.ks.set(b"k", blob)
        self.assertEqual(self.ks.get(b"k"), blob)

    def test_set_nx_xx(self):
        self.assertEqual(self.ks.set(b"k", b"1", nx=True)[0], "ok")
        self.assertEqual(self.ks.set(b"k", b"2", nx=True)[0], "nx")
        self.assertEqual(self.ks.get(b"k"), b"1")
        self.assertEqual(self.ks.set(b"k", b"3", xx=True)[0], "ok")
        self.assertEqual(self.ks.set(b"new", b"4", xx=True)[0], "xx")
        self.assertIsNone(self.ks.get(b"new"))

    def test_set_with_expiry(self):
        self.ks.set(b"k", b"v", expire_ms=50)
        self.assertEqual(self.ks.get(b"k"), b"v")
        time.sleep(0.08)
        self.assertIsNone(self.ks.get(b"k"))

    def test_keptttl_keeps_previous_expiry(self):
        self.ks.set(b"k", b"v1", expire_ms=200)
        self.ks.set(b"k", b"v2", keep_ttl=True)
        self.assertLess(self.ks.ttl_ms(b"k"), 200)
        self.assertGreater(self.ks.ttl_ms(b"k"), 0)

    def test_set_clears_ttl_unless_kept(self):
        self.ks.set(b"k", b"v1", expire_ms=200)
        self.ks.set(b"k", b"v2")
        self.assertEqual(self.ks.ttl_ms(b"k"), -1)

    def test_incr_missing_starts_at_zero(self):
        self.assertEqual(self.ks.incr_by(b"n", 5), 5)

    def test_incr_not_integer(self):
        self.ks.set(b"k", b"abc")
        with self.assertRaises(NotIntegerError):
            self.ks.incr_by(b"k", 1)

    def test_incr_wrong_type(self):
        self.ks.push(b"l", [b"1"])
        with self.assertRaises(WrongTypeError):
            self.ks.incr_by(b"l", 1)

    def test_append(self):
        self.assertEqual(self.ks.append(b"k", b"ab"), 2)
        self.assertEqual(self.ks.append(b"k", b"cd"), 4)
        self.assertEqual(self.ks.get(b"k"), b"abcd")

    def test_strlen(self):
        self.assertEqual(self.ks.strlen(b"missing"), 0)
        self.ks.set(b"k", b"hello")
        self.assertEqual(self.ks.strlen(b"k"), 5)

    def test_getrange(self):
        self.ks.set(b"k", b"hello world")
        self.assertEqual(self.ks.getrange(b"k", 0, 4), b"hello")
        self.assertEqual(self.ks.getrange(b"k", -5, -1), b"world")
        self.assertEqual(self.ks.getrange(b"k", 20, 30), b"")

    def test_mget_skips_wrong_type(self):
        self.ks.set(b"a", b"1")
        self.ks.push(b"l", [b"x"])
        self.assertEqual(self.ks.mget(b"a", b"l", b"z"), [b"1", None, None])


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_ttl_semantics(self):
        self.assertEqual(self.ks.ttl_ms(b"missing"), -2)
        self.ks.set(b"k", b"v")
        self.assertEqual(self.ks.ttl_ms(b"k"), -1)
        self.ks.expire_at(b"k", time.time() + 5)
        self.assertLessEqual(self.ks.ttl_ms(b"k"), 5000)
        self.assertGreater(self.ks.ttl_ms(b"k"), 4900)

    def test_persist(self):
        self.ks.set(b"k", b"v")
        self.assertEqual(self.ks.persist(b"k"), 0)  # 无过期时间可移除
        self.ks.expire_at(b"k", time.time() + 5)
        self.assertEqual(self.ks.persist(b"k"), 1)
        self.assertEqual(self.ks.ttl_ms(b"k"), -1)

    def test_expire_missing_key(self):
        self.assertEqual(self.ks.expire_at(b"missing", time.time() + 5), False)

    def test_rename_moves_ttl(self):
        self.ks.set(b"a", b"v", expire_ms=300)
        self.assertTrue(self.ks.rename(b"a", b"b"))
        self.assertEqual(self.ks.ttl_ms(b"a"), -2)
        self.assertGreater(self.ks.ttl_ms(b"b"), 0)
        self.assertEqual(self.ks.get(b"b"), b"v")

    def test_rename_overwrite_clears_dst_ttl(self):
        self.ks.set(b"dst", b"old", expire_ms=300)
        self.ks.set(b"src", b"new")
        self.ks.rename(b"src", b"dst")
        self.assertEqual(self.ks.ttl_ms(b"dst"), -1)

    def test_lazy_expiry_on_exists(self):
        self.ks.set(b"k", b"v", expire_ms=30)
        time.sleep(0.06)
        self.assertEqual(self.ks.exists(b"k"), 0)

    def test_expired_cycle_removes(self):
        self.ks.set(b"dead", b"v", expire_ms=20)
        self.ks.set(b"alive", b"v", expire_seconds=100)
        time.sleep(0.05)
        removed = self.ks.expired_cycle()
        self.assertEqual(removed, 1)
        self.assertEqual(self.ks.exists(b"dead"), 0)
        self.assertEqual(self.ks.exists(b"alive"), 1)


class KeyspaceTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_type(self):
        self.assertEqual(self.ks.key_type(b"missing"), TYPE_NONE)
        self.ks.set(b"s", b"v")
        self.ks.push(b"l", [b"x"])
        self.ks.hset(b"h", b"f", b"v")
        self.assertEqual(self.ks.key_type(b"s"), TYPE_STRING)
        self.assertEqual(self.ks.key_type(b"l"), TYPE_LIST)
        self.assertEqual(self.ks.key_type(b"h"), TYPE_HASH)

    def test_delete_and_exists(self):
        self.ks.set(b"a", b"1")
        self.ks.set(b"b", b"2")
        self.assertEqual(self.ks.exists(b"a", b"b", b"c"), 2)
        self.assertEqual(self.ks.delete(b"a", b"c"), 1)
        self.assertEqual(self.ks.exists(b"a"), 0)

    def test_exists_repeated_key_counts_each_time(self):
        self.ks.set(b"a", b"1")
        self.assertEqual(self.ks.exists(b"a", b"a"), 2)

    def test_keys_pattern(self):
        for name in (b"user:1", b"user:2", b"post:1"):
            self.ks.set(name, b"v")
        self.assertEqual(sorted(self.ks.keys(b"user:*")), [b"user:1", b"user:2"])
        self.assertEqual(len(self.ks.keys()), 3)

    def test_dbsize_and_flushdb(self):
        self.ks.set(b"a", b"1")
        self.ks.set(b"b", b"2")
        self.assertEqual(self.ks.dbsize(), 2)
        self.ks.flushdb()
        self.assertEqual(self.ks.dbsize(), 0)

    def test_dirty_flag(self):
        self.assertFalse(self.ks.dirty)
        self.ks.set(b"a", b"1")
        self.assertTrue(self.ks.dirty)


class ListTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_rpush_lrange(self):
        self.ks.push(b"l", [b"a", b"b", b"c"])
        self.assertEqual(self.ks.lrange(b"l", 0, -1), [b"a", b"b", b"c"])
        self.assertEqual(self.ks.lrange(b"l", 1, 2), [b"b", b"c"])
        self.assertEqual(self.ks.lrange(b"missing", 0, -1), [])

    def test_lpush_prepends(self):
        self.ks.push(b"l", [b"a"], left=True)
        self.ks.push(b"l", [b"b"], left=True)
        self.assertEqual(self.ks.lrange(b"l", 0, -1), [b"b", b"a"])

    def test_negative_indices(self):
        self.ks.push(b"l", [b"a", b"b", b"c"])
        self.assertEqual(self.ks.lrange(b"l", -2, -1), [b"b", b"c"])

    def test_len_and_pop(self):
        self.ks.push(b"l", [b"a", b"b"])
        self.assertEqual(self.ks.llen(b"l"), 2)
        self.assertEqual(self.ks.pop(b"l", left=True), b"a")
        self.assertEqual(self.ks.pop(b"l", left=False), b"b")
        self.assertEqual(self.ks.llen(b"l"), 0)
        self.assertEqual(self.ks.key_type(b"l"), TYPE_NONE)  # 空列表自动删除
        self.assertIsNone(self.ks.pop(b"l"))

    def test_push_wrong_type(self):
        self.ks.set(b"k", b"v")
        with self.assertRaises(WrongTypeError):
            self.ks.push(b"k", [b"x"])


class HashTest(unittest.TestCase):
    def setUp(self):
        self.ks = KeySpace()

    def test_hset_hget(self):
        self.assertEqual(self.ks.hset(b"h", b"f1", b"v1"), 1)
        self.assertEqual(self.ks.hset(b"h", b"f1", b"v1x"), 0)  # 覆盖不算新字段
        self.assertEqual(self.ks.hget(b"h", b"f1"), b"v1x")
        self.assertIsNone(self.ks.hget(b"h", b"missing"))
        self.assertIsNone(self.ks.hget(b"missing", b"f"))

    def test_hgetall_flat(self):
        self.ks.hset(b"h", b"a", b"1")
        self.ks.hset(b"h", b"b", b"2")
        self.assertEqual(sorted(self.ks.hgetall(b"h")),
                         sorted([b"a", b"1", b"b", b"2"]))

    def test_hdel(self):
        self.ks.hset(b"h", b"a", b"1")
        self.ks.hset(b"h", b"b", b"2")
        self.assertEqual(self.ks.hdel(b"h", b"a", b"zz"), 1)
        self.assertEqual(self.ks.hdel(b"h", b"b"), 1)
        self.assertEqual(self.ks.key_type(b"h"), TYPE_NONE)  # 空哈希自动删除

    def test_hexists_hkeys_hvals_hlen(self):
        self.ks.hset(b"h", b"a", b"1")
        self.ks.hset(b"h", b"b", b"2")
        self.assertEqual(self.ks.hexists(b"h", b"a"), 1)
        self.assertEqual(self.ks.hexists(b"h", b"z"), 0)
        self.assertEqual(sorted(self.ks.hkeys(b"h")), [b"a", b"b"])
        self.assertEqual(sorted(self.ks.hvals(b"h")), [b"1", b"2"])
        self.assertEqual(self.ks.hlen(b"h"), 2)

    def test_hset_wrong_type(self):
        self.ks.set(b"k", b"v")
        with self.assertRaises(WrongTypeError):
            self.ks.hset(b"k", b"f", b"v")


if __name__ == "__main__":
    unittest.main()
