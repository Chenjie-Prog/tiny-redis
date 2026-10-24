<div align="center">

# tiny-redis

**A mini Redis built from scratch on the pure Python standard library — drivable by the official redis-py client**

[![Tests](https://github.com/USER/tiny-redis/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/tiny-redis/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](#why-this-project)

[中文文档 & 教程](README.md) · [Tutorial (Chinese)](docs/)

</div>

A Redis-protocol-compatible server written with **zero third-party
dependencies**: a RESP2 parser, a single-threaded `selectors` event loop,
string/list/hash data structures, TTL expiry, and binary snapshots with
CRC64 integrity checking — in one small package with a 5-chapter tutorial.

## Why this project

- **Learnable** — Redis is "protocol + event loop + keyspace". tiny-redis
  explains all three in ~1,000 lines of commented code plus a 5-chapter
  tutorial (in Chinese, [docs/](docs/)) that quotes the real code.
- **It actually works** — the official redis-py client connects and runs
  real workloads ([compat script](scripts/compat_check.py), 23/23 checks).
- **Zero dependencies** — nothing to `pip install` at runtime; Python 3.9+.

## Quick start

```bash
git clone https://github.com/USER/tiny-redis.git
cd tiny-redis
python -m tinyredis --port 6380        # start the server
```

In another terminal:

```bash
# bundled zero-dependency client
python -c "from tinyredis.client import Redis; \
print(Redis(port=6380).get('greeting'))"

# or connect redis-cli / redis-py (redis-py needs protocol=2)
redis-cli -p 6380
```

### Embedded in your code

```python
from tinyredis.server import TinyRedisServer
from tinyredis.client import Redis

with TinyRedisServer(port=0, snapshot_path="data/dump.trdb") as server:
    server.start_background()
    port = server.wait_ready()

    r = Redis(port=port)
    r.set("config:name", "demo", ex=3600)
    r.hset("user:1", mapping={"name": "chen", "level": "9"})
    print(r.hgetall("user:1"))
```

### Install as a CLI

```bash
pip install git+https://github.com/USER/tiny-redis.git
tiny-redis-server --port 6380 --dir ./data --snapshot-interval 60
```

## Supported commands

| Category | Commands |
| :-- | :-- |
| Connection | `PING` `ECHO` `SELECT` `QUIT` `HELLO`(RESP2) `COMMAND DOCS` `INFO` |
| Strings | `SET`(EX/PX/NX/XX/KEPTTTL/GET) `GET` `GETSET` `SETNX` `MGET` `MSET` `INCR` `INCRBY` `DECR` `DECRBY` `APPEND` `STRLEN` `GETRANGE` |
| Keyspace | `DEL` `UNLINK` `EXISTS` `TYPE` `KEYS` `RENAME` `EXPIRE` `PEXPIRE` `TTL` `PTTL` `PERSIST` `DBSIZE` `FLUSHDB` `FLUSHALL` |
| Lists | `LPUSH` `RPUSH` `LRANGE` `LLEN` `LPOP` `RPOP` |
| Hashes | `HSET`(multi-field) `HGET` `HGETALL` `HDEL` `HEXISTS` `HKEYS` `HVALS` `HLEN` |
| Admin | `SAVE` `BGSAVE` `SHUTDOWN [SAVE\|NOSAVE]` |

Error messages (`WRONGTYPE`, `ERR no such key`, …) match Redis verbatim;
TTL/expiry/empty-container semantics behave like the real thing.

## Differences from real Redis (kept on purpose)

| Aspect | Real Redis | tiny-redis |
| :-- | :-- | :-- |
| Language | C | Pure Python (~1,000 lines core) |
| Protocol | RESP2/RESP3 | RESP2 only (`HELLO 3` → `NOPROTO`) |
| Concurrency | single-threaded commands + I/O threads | single thread does everything |
| Blocking commands | BLPOP etc. | not supported (see [tutorial ch.2](docs/02-事件循环.md)) |
| Replication / cluster | yes | roadmap |
| Low-level encodings | listpack/skiplist/… | dict / list / dict |
| RDB format | varint encoding | TRDB (fixed-length, CRC64-checked) |

## Benchmarks

Sequential mode (no pipeline, single connection, Windows / Python 3.14):

```text
SET : 13010 ops/s
GET : 15330 ops/s
INCR: 16887 ops/s
```

The bottleneck is the Python interpreter, not the data structures —
the honest conclusion for a teaching project.
Reproduce: `python scripts/benchmark.py -n 5000`.

## Development

```bash
python -m unittest discover -s tests      # 133 tests
python scripts/compat_check.py            # redis-py compatibility (needs pip install redis)
python scripts/benchmark.py -n 5000       # benchmark (server must be running)
```

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

- [ ] master–slave replication (write-command stream)
- [ ] AOF append-only log alongside snapshots
- [ ] SCAN cursor iteration
- [ ] pipelining
- [ ] SET / ZSET (skiplist) types

## License

[MIT](LICENSE)
