# -*- coding: utf-8 -*-
"""端到端测试：真实 socket 上跑完整服务器 + 客户端。"""

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinyredis.client import Redis, ResponseError
from tinyredis.server import TinyRedisServer


@contextmanager
def running_server(**kwargs):
    server = TinyRedisServer(port=0, **kwargs)
    server.load_snapshot()  # 与 CLI 行为一致：启动前恢复快照
    server.start_background()
    port = server.wait_ready()
    try:
        yield server, port
    finally:
        server.close()
        server.wait_stopped(5)


def raw_send(port, wire, expect_reply=True):
    """用原始 socket 发一段字节，读回应答。"""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.sendall(wire)
        if not expect_reply:
            return b""
        time.sleep(0.05)
        return sock.recv(65536)


class BasicSessionTest(unittest.TestCase):
    def test_ping_set_get_roundtrip(self):
        with running_server() as (_, port), Redis(port=port) as r:
            self.assertEqual(r.ping(), "PONG")
            self.assertEqual(r.set("greeting", "你好"), "OK")
            self.assertEqual(r.get("greeting"), "你好".encode("utf-8"))
            self.assertEqual(r.incr("hits"), 1)
            self.assertEqual(r.incr("hits"), 2)
            self.assertEqual(r.get("hits"), b"2")

    def test_error_reply_raises(self):
        with running_server() as (_, port), Redis(port=port) as r:
            r.rpush("l", "x")
            with self.assertRaises(ResponseError) as ctx:
                r.get("l")
            self.assertIn("WRONGTYPE", str(ctx.exception))

    def test_unknown_command(self):
        with running_server() as (_, port), Redis(port=port) as r:
            with self.assertRaises(ResponseError) as ctx:
                r.execute_command("NOSUCHCMD")
            self.assertIn("unknown command", str(ctx.exception))

    def test_big_value_roundtrip(self):
        blob = bytes(range(256)) * 4096  # 1MB
        with running_server() as (_, port), Redis(port=port) as r:
            r.set("blob", blob)
            self.assertEqual(r.get("blob"), blob)
            self.assertEqual(r.execute_command("STRLEN", "blob"), len(blob))

    def test_ttl_end_to_end(self):
        with running_server() as (_, port), Redis(port=port) as r:
            r.set("temp", "v", px=120)
            self.assertEqual(r.get("temp"), b"v")
            self.assertGreater(r.execute_command("PTTL", "temp"), 0)
            time.sleep(0.2)
            self.assertIsNone(r.get("temp"))
            self.assertEqual(r.execute_command("TTL", "temp"), -2)

    def test_quit_closes_connection(self):
        with running_server() as (_, port):
            sock = socket.create_connection(("127.0.0.1", port), timeout=3)
            sock.sendall(b"QUIT\r\n")
            self.assertEqual(sock.recv(64), b"+OK\r\n")
            self.assertEqual(sock.recv(64), b"")  # 服务器主动关闭
            sock.close()


class WireProtocolTest(unittest.TestCase):
    def test_inline_command(self):
        with running_server() as (_, port):
            self.assertEqual(raw_send(port, b"PING\r\n"), b"+PONG\r\n")

    def test_inline_quoted(self):
        with running_server() as (_, port):
            self.assertEqual(raw_send(port, b'SET "k 1" v\r\n'), b"+OK\r\n")
            self.assertEqual(
                raw_send(port, b"*2\r\n$3\r\nGET\r\n$3\r\nk 1\r\n"), b"$1\r\nv\r\n")

    def test_protocol_error_disconnects(self):
        with running_server() as (_, port):
            reply = raw_send(port, b"*abc\r\n")
            self.assertTrue(reply.startswith(b"-ERR Protocol error"))

    def test_empty_lines_ignored(self):
        with running_server() as (_, port):
            self.assertEqual(raw_send(port, b"\r\n\r\nPING\r\n"), b"+PONG\r\n")


class ConcurrencyTest(unittest.TestCase):
    def test_parallel_incr_is_atomic(self):
        # 单线程事件循环保证每条命令原子执行：4 个客户端并发 INCR，结果必须精确。
        workers, rounds = 4, 200
        with running_server() as (_, port), Redis(port=port) as r:
            r.set("counter", 0)
            errors = []

            def worker():
                try:
                    with Redis(port=port, timeout=10) as client:
                        for _ in range(rounds):
                            client.incr("counter")
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(errors, [])
            self.assertEqual(r.get("counter"), str(workers * rounds).encode())

    def test_interleaved_clients_independent(self):
        with running_server() as (_, port):
            a, b = Redis(port=port), Redis(port=port)
            try:
                a.set("who", "alice")
                b.set("who", "bob")
                self.assertEqual(a.get("who"), b"bob")
                a.rpush("q", 1)
                b.rpush("q", 2)
                a.rpush("q", 3)
                self.assertEqual(b.lrange("q", 0, -1), [b"1", b"2", b"3"])
            finally:
                a.close()
                b.close()

    def test_many_connections(self):
        with running_server() as (_, port):
            clients = [Redis(port=port, timeout=10) for _ in range(30)]
            try:
                for i, c in enumerate(clients):
                    c.set("k%d" % i, i)
                for i, c in enumerate(clients):
                    self.assertEqual(c.get("k%d" % i), str(i).encode())
            finally:
                for c in clients:
                    c.close()


class PersistenceEndToEndTest(unittest.TestCase):
    def test_save_then_reload_into_new_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.trdb"
            with running_server(snapshot_path=path) as (server, port), Redis(port=port) as r:
                r.set("key", "value")
                r.rpush("mylist", "a", "b")
                r.hset("myhash", "field", "val")
                r.execute_command("SAVE")
                self.assertTrue(path.exists())

            with running_server(snapshot_path=path) as (_, port2), Redis(port=port2) as r:
                self.assertEqual(r.get("key"), b"value")
                self.assertEqual(r.lrange("mylist", 0, -1), [b"a", b"b"])
                self.assertEqual(r.hget("myhash", "field"), b"val")

    def test_shutdown_save_writes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.trdb"
            server = TinyRedisServer(port=0, snapshot_path=path)
            server.start_background()
            port = server.wait_ready()
            with Redis(port=port) as r:
                r.set("survives", "yes")
                # SHUTDOWN 不返回应答，客户端会读到连接关闭。
                with self.assertRaises(Exception):
                    r.execute_command("SHUTDOWN", "SAVE")
            self.assertTrue(server.wait_stopped(5))
            self.assertTrue(path.exists())

            restored = TinyRedisServer(port=0, snapshot_path=path)
            self.assertEqual(restored.load_snapshot(), 1)

    def test_snapshot_interval_auto_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.trdb"
            with running_server(snapshot_path=path, snapshot_interval=0.2) as (_, port), \
                    Redis(port=port) as r:
                r.set("auto", "saved")
                deadline = time.time() + 3
                while not path.exists() and time.time() < deadline:
                    time.sleep(0.05)
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
