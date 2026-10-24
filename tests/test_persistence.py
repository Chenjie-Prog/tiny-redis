import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis import persistence
from tinyredis.persistence import SnapshotError, crc64, load_snapshot, save_snapshot
from tinyredis.storage import KeySpace


class Crc64Test(unittest.TestCase):
    def test_standard_check_value(self):
        # ECMA-182 的标准校验值。
        self.assertEqual(crc64(b"123456789"), 0x995DC9BBDF1939FA)

    def test_empty_and_stable(self):
        self.assertEqual(crc64(b""), 0)
        self.assertEqual(crc64(b"abc"), crc64(b"abc"))
        self.assertNotEqual(crc64(b"abc"), crc64(b"abd"))


class SaveLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dump.trdb"

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store(self):
        save_snapshot(KeySpace(), self.path)
        self.assertEqual(load_snapshot(KeySpace(), self.path), 0)

    def test_roundtrip_all_types(self):
        store = KeySpace()
        store.set(b"str", b"value with spaces")
        store.set(b"bin", bytes(range(256)))
        store.push(b"list", [b"a", b"b", b"c"])
        store.hset(b"hash", b"field", b"value")
        save_snapshot(store, self.path)

        loaded = KeySpace()
        self.assertEqual(load_snapshot(loaded, self.path), 4)
        self.assertEqual(loaded.get(b"str"), b"value with spaces")
        self.assertEqual(loaded.get(b"bin"), bytes(range(256)))
        self.assertEqual(loaded.lrange(b"list", 0, -1), [b"a", b"b", b"c"])
        self.assertEqual(loaded.hget(b"hash", b"field"), b"value")
        self.assertEqual(loaded.ttl_ms(b"str"), -1)

    def test_roundtrip_ttl_survives_as_remaining_time(self):
        store = KeySpace()
        store.set(b"k", b"v", expire_ms=60_000)
        save_snapshot(store, self.path)
        loaded = KeySpace()
        load_snapshot(loaded, self.path)
        ttl = loaded.ttl_ms(b"k")
        self.assertGreater(ttl, 55_000)
        self.assertLessEqual(ttl, 60_000)

    def test_load_skips_expired_keys(self):
        store = KeySpace()
        store.set(b"fresh", b"v", expire_seconds=100)
        store.set(b"dead", b"v", expire_ms=10)
        save_snapshot(store, self.path)
        time.sleep(0.05)
        loaded = KeySpace()
        self.assertEqual(load_snapshot(loaded, self.path), 1)
        self.assertIsNone(loaded.get(b"dead"))
        self.assertEqual(loaded.get(b"fresh"), b"v")

    def test_atomic_write_leaves_no_tmp(self):
        store = KeySpace()
        store.set(b"k", b"v")
        save_snapshot(store, self.path)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_suffix(".trdb.tmp").exists())

    def test_missing_file_returns_zero(self):
        self.assertEqual(load_snapshot(KeySpace(), self.path), 0)


class CorruptedFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dump.trdb"
        store = KeySpace()
        store.set(b"k", b"v")
        save_snapshot(store, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_flipped_byte_detected_by_crc(self):
        data = bytearray(self.path.read_bytes())
        data[12] ^= 0xFF  # 翻转记录区的一个字节
        self.path.write_bytes(bytes(data))
        with self.assertRaises(SnapshotError):
            load_snapshot(KeySpace(), self.path)

    def test_flipped_crc_detected(self):
        data = bytearray(self.path.read_bytes())
        data[-1] ^= 0x01
        self.path.write_bytes(bytes(data))
        with self.assertRaises(SnapshotError):
            load_snapshot(KeySpace(), self.path)

    def test_bad_magic(self):
        data = bytearray(self.path.read_bytes())
        data[0:4] = b"XXXX"
        self.path.write_bytes(bytes(data))
        with self.assertRaises(SnapshotError):
            load_snapshot(KeySpace(), self.path)

    def test_truncated_file(self):
        data = self.path.read_bytes()
        self.path.write_bytes(data[: len(data) // 2])
        with self.assertRaises(SnapshotError):
            load_snapshot(KeySpace(), self.path)


if __name__ == "__main__":
    unittest.main()
