# -*- coding: utf-8 -*-
"""服务器：selectors 单线程事件循环。

为什么单线程？真实 Redis 也是把命令执行放在单线程里（网络 IO 另算），
好处是**每条命令天然原子**——不需要任何锁，INCR 就是真 INCR，并发测试
可以直接验证。tiny-redis 更进一步：网络 IO 也用 ``selectors`` 放在同一个
循环里跑（非阻塞 socket + 事件驱动），整个服务器就是一个 ``while`` 循环，
这也是 Redis 事件模型的最小可运行版本。

代价是**不能有阻塞命令**：BLPOP 这类"挂着等数据"的命令会卡死所有连接，
所以 tiny-redis 不支持它们（见 README 路线图）。
"""

import selectors
import socket
import threading
import time

from . import commands, persistence, protocol
from .storage import KeySpace

__all__ = ["TinyRedisServer"]

_RECV_SIZE = 65536
_EXPIRY_CYCLE_INTERVAL = 0.1  # 主动过期采样周期（秒）


class Connection:
    __slots__ = ("sock", "addr", "parser", "out", "closing")

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.parser = protocol.RespParser()
        self.out = bytearray()   # 未发送完的应答缓冲
        self.closing = False     # 置位后，发完剩余数据就断开


class TinyRedisServer:
    """单线程事件循环版 Redis 服务器。

    典型用法::

        server = TinyRedisServer(port=6380, snapshot_path="dump.trdb")
        server.serve_forever()          # 阻塞，Ctrl+C 退出

    测试里用::

        server = TinyRedisServer(port=0)   # 0 = 随机端口
        server.start_background()
        server.wait_ready()
        client.Redis(port=server.bound_port)
    """

    def __init__(self, host="127.0.0.1", port=6379, snapshot_path=None,
                 snapshot_interval=0.0, store=None):
        self.host = host
        self._requested_port = port
        self.snapshot_path = snapshot_path
        self.snapshot_interval = snapshot_interval
        self.store = store if store is not None else KeySpace()
        self.hooks = commands.CommandHooks(
            save_snapshot=self.save_snapshot,
            request_shutdown=self.request_shutdown,
            start_time=time.time(),
        )
        self.bound_port = None
        self.start_time = time.time()
        self.commands_processed = 0
        self._listener = None
        self._selector = None
        self._conns = set()
        self._shutdown = False
        self._last_cycle = time.monotonic()
        self._last_snapshot = time.monotonic()
        self._thread = None
        self._ready = threading.Event()
        self._stopped = threading.Event()

    # ---- 生命周期 ----------------------------------------------------

    def bind(self):
        """创建监听 socket。port=0 时绑定随机端口。"""
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((self.host, self._requested_port))
        self._listener.listen(128)
        self._listener.setblocking(False)
        self.bound_port = self._listener.getsockname()[1]
        # bound_port 就绪后再置位 _ready，wait_ready() 永远不会读到半初始化状态。
        self._ready.set()

    def serve_forever(self):
        """阻塞运行事件循环，直到 request_shutdown() 或 KeyboardInterrupt。"""
        if self._listener is None:
            self.bind()
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._listener, selectors.EVENT_READ, None)
        self._ready.set()
        try:
            self._loop()
        finally:
            self._cleanup()
            self._stopped.set()

    def start_background(self):
        """在后台线程启动，供测试和嵌入使用。"""
        self._thread = threading.Thread(
            target=self.serve_forever, name="tiny-redis-server", daemon=True)
        self._thread.start()
        return self._thread

    def wait_ready(self, timeout=5.0):
        """等待端口就绪（bind 完成即就绪）。"""
        if not self._ready.wait(timeout):
            raise RuntimeError("tiny-redis server failed to start")
        return self.bound_port

    def wait_stopped(self, timeout=5.0):
        return self._stopped.wait(timeout)

    def request_shutdown(self, save=False):
        """SHUTDOWN 命令的落点：可选保存快照，然后退出事件循环。"""
        if save and self.snapshot_path:
            self.save_snapshot()
        self._shutdown = True

    def save_snapshot(self):
        if not self.snapshot_path:
            return
        persistence.save_snapshot(self.store, self.snapshot_path)
        self.store.dirty = False
        self._last_snapshot = time.monotonic()
    def load_snapshot(self):
        """启动前把快照读回内存，返回恢复的键数。未配置快照或文件不存在返回 0。"""
        if not self.snapshot_path:
            return 0
        return persistence.load_snapshot(self.store, self.snapshot_path)

    def close(self):
        """外部停止：置位退出标志，等事件循环线程自己清理完再返回。"""
        self._shutdown = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        if not self._stopped.is_set():
            # 线程从未启动或已崩溃：由本线程兜底清理。
            self._cleanup()
            self._stopped.set()

    @property
    def connection_count(self):
        return len(self._conns)

    # ---- 事件循环 ----------------------------------------------------

    def _loop(self):
        while not self._shutdown:
            timeout = min(_EXPIRY_CYCLE_INTERVAL, 0.1)
            events = self._selector.select(timeout=timeout)
            for key, mask in events:
                if key.data is None:
                    self._accept(key.fileobj)
                else:
                    self._handle_event(key, mask)
            self._periodic()

    def _periodic(self):
        """每个循环 tick 的例行任务：主动过期 + 定时快照。"""
        now = time.monotonic()
        if now - self._last_cycle >= _EXPIRY_CYCLE_INTERVAL:
            self._last_cycle = now
            self.store.expired_cycle()
        interval = self.snapshot_interval
        if interval and now - self._last_snapshot >= interval and self.store.dirty:
            self.save_snapshot()

    def _accept(self, listener):
        try:
            sock, addr = listener.accept()
        except OSError:
            return
        sock.setblocking(False)
        conn = Connection(sock, addr)
        self._conns.add(conn)
        self._selector.register(sock, selectors.EVENT_READ, conn)

    def _handle_event(self, key, mask):
        conn = key.data
        if mask & selectors.EVENT_READ:
            if not self._do_read(conn):
                return
        if mask & selectors.EVENT_WRITE:
            self._do_write(conn)
        self._flush(conn)

    def _do_read(self, conn):
        try:
            data = conn.sock.recv(_RECV_SIZE)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            self._close_conn(conn)
            return False
        if not data:  # 对端正常关闭
            self._close_conn(conn)
            return False
        conn.parser.feed(data)
        while True:
            try:
                args = conn.parser.next_command()
            except protocol.RespError as exc:
                # 协议错误：回一条错误后立刻断开（与 Redis 行为一致）。
                conn.out += protocol.encode_reply(protocol.error("ERR %s" % exc))
                conn.closing = True
                return True
            if args is None:
                break
            if not args:  # 空行/空命令，直接忽略
                continue
            self._execute(conn, args)
            if conn.closing:
                break
        return True

    def _execute(self, conn, args):
        name = args[0].upper()
        if name == b"QUIT":
            conn.out += protocol.encode_reply(protocol.OK)
            conn.closing = True
            self.commands_processed += 1
            return
        if name == b"SHUTDOWN":
            # 与 Redis 一致：SHUTDOWN 不回包，直接断开连接再退出循环。
            commands.execute(self.store, args, self.hooks)
            conn.closing = True
            self.commands_processed += 1
            return
        reply = commands.execute(self.store, args, self.hooks)
        conn.out += protocol.encode_reply(reply)
        self.commands_processed += 1

    def _do_write(self, conn):
        pass  # 写就绪只需要继续 flush；保留分支以便未来加流式应答

    def _flush(self, conn):
        if not conn.out:
            return
        try:
            sent = conn.sock.send(bytes(conn.out))
        except (BlockingIOError, InterruptedError):
            sent = 0
        except OSError:
            self._close_conn(conn)
            return
        if sent:
            del conn.out[:sent]
        # 缓冲没发完就订阅 WRITE 事件，发完了只留 READ。
        want = selectors.EVENT_READ | (selectors.EVENT_WRITE if conn.out else 0)
        if key := self._find_key(conn):
            if key.events != want:
                self._selector.modify(key.fileobj, want, conn)
        if not conn.out and conn.closing:
            self._close_conn(conn)

    def _find_key(self, conn):
        for key in self._selector.get_map().values():
            if key.data is conn:
                return key
        return None

    def _close_conn(self, conn):
        if conn in self._conns:
            self._conns.discard(conn)
            try:
                self._selector.unregister(conn.sock)
            except (KeyError, ValueError, OSError):
                pass
            try:
                conn.sock.close()
            except OSError:
                pass

    def _cleanup(self):
        try:
            if self._selector is not None:
                for key in list(self._selector.get_map().values()):
                    if key.data is not None:
                        try:
                            key.fileobj.close()
                        except OSError:
                            pass
                try:
                    self._selector.close()
                except Exception:
                    pass
                self._selector = None
        finally:
            self._conns.clear()
            if self._listener is not None:
                try:
                    self._listener.close()
                except OSError:
                    pass
                self._listener = None
