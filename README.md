<div align="center">

# tiny-redis

**用纯 Python 标准库从零实现的迷你 Redis —— 能被官方 redis-py 直接驱动**

[![Tests](https://github.com/Chenjie-Prog/tiny-redis/actions/workflows/ci.yml/badge.svg)](https://github.com/Chenjie-Prog/tiny-redis/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](#为什么做这个项目)

[中文教程](docs/) · [English](README.en.md)

</div>

```text
$ redis-cli -p 6380
127.0.0.1:6380> SET greeting "你好，tiny-redis"
OK
127.0.0.1:6380> GET greeting
"\xe4\xbd\xa0\xe5\xa5\xbd\xef\xbc\x8ctiny-redis"
127.0.0.1:6380> INCR hits
(integer) 1
127.0.0.1:6380> LPUSH queue a b c
(integer) 3
```

上面这些命令由 tiny-redis 服务器应答——一个只用 Python 标准库写的
Redis 协议兼容服务器：RESP2 解析器、selectors 单线程事件循环、
三种数据结构、TTL 过期、CRC64 校验的二进制快照，全在一个包里。

## 为什么做这个项目

- **学得会**。Redis 的核心是"协议 + 事件循环 + 键空间"三件事，
  tiny-redis 用约 1000 行带注释的代码把它们讲清楚，配套 5 章中文教程
  （[docs/](docs/)），每一章都引用仓库里的真实代码；
- **跑得动**。不是玩具伪实现：官方 redis-py 可以直接连上来增删改查
  （[兼容性脚本](scripts/compat_check.py) 23 项全过）；
- **零依赖**。运行时一个第三方包都没有，Python 3.9+ 开箱即用。

## 快速开始

```bash
git clone https://github.com/Chenjie-Prog/tiny-redis.git
cd tiny-redis

python -m tinyredis --port 6380          # 启动服务器
```

另开一个终端：

```bash
# 用自带的零依赖客户端
python -c "from tinyredis.client import Redis; \
print(Redis(port=6380).get('greeting'))"

# 或用 redis-cli / redis-py 连上来（redis-py 需要 protocol=2）
redis-cli -p 6380
```

### 在代码里当嵌入式数据库用

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

### 作为命令行程序安装

```bash
pip install git+https://github.com/Chenjie-Prog/tiny-redis.git
tiny-redis-server --port 6380 --dir ./data --snapshot-interval 60
```

## 支持的命令

| 类别 | 命令 |
| :-- | :-- |
| 连接 | `PING` `ECHO` `SELECT` `QUIT` `HELLO`(RESP2) `COMMAND DOCS` `INFO` |
| 字符串 | `SET`(EX/PX/NX/XX/KEPTTTL/GET) `GET` `GETSET` `SETNX` `MGET` `MSET` `INCR` `INCRBY` `DECR` `DECRBY` `APPEND` `STRLEN` `GETRANGE` |
| 键空间 | `DEL` `UNLINK` `EXISTS` `TYPE` `KEYS` `RENAME` `EXPIRE` `PEXPIRE` `TTL` `PTTL` `PERSIST` `DBSIZE` `FLUSHDB` `FLUSHALL` |
| 列表 | `LPUSH` `RPUSH` `LRANGE` `LLEN` `LPOP` `RPOP` |
| 哈希 | `HSET`(多字段) `HGET` `HGETALL` `HDEL` `HEXISTS` `HKEYS` `HVALS` `HLEN` |
| 管理 | `SAVE` `BGSAVE` `SHUTDOWN [SAVE\|NOSAVE]` |

错误信息（`WRONGTYPE`、`ERR no such key`……）逐字对齐 Redis，
TTL/过期/空容器自动删除等语义也与 Redis 一致。

## 与真实 Redis 的差距（刻意保留）

| 方面 | 真实 Redis | tiny-redis |
| :-- | :-- | :-- |
| 语言 | C | 纯 Python（约 1000 行核心代码） |
| 协议 | RESP2/RESP3 | 仅 RESP2（`HELLO 3` 回 `NOPROTO`） |
| 并发 | 单线程命令执行 + I/O 线程 | 单线程包揽一切 |
| 阻塞命令 | BLPOP 等 | 不支持（单线程下会卡死，见[教程第 2 章](docs/02-事件循环.md)） |
| 集群/复制 | 支持 | 路线图 |
| 底层编码 | listpack/skiplist/... | dict / list / dict |
| RDB 格式 | 变长编码 | TRDB（定长编码教学版，含 CRC64） |

## 性能参考

顺序模式（无 pipeline，单连接，Windows / Python 3.14）：

```text
SET : 13010 ops/s
GET : 15330 ops/s
INCR: 16887 ops/s
```

瓶颈在 Python 解释器而非数据结构——教学项目该有的诚实结论。
复现：`python scripts/benchmark.py -n 5000`。

## 中文教程

1. [RESP 协议 —— 线缆上的字节](docs/01-RESP协议.md)
2. [事件循环 —— 为什么单线程反而是优点](docs/02-事件循环.md)
3. [键空间与命令 —— dict 上的一层薄皮](docs/03-键空间与命令.md)
4. [过期与持久化 —— 让数据活得下来](docs/04-过期与持久化.md)
5. [客户端、兼容性与测试](docs/05-客户端与兼容性.md)

## 开发

```bash
python -m unittest discover -s tests      # 133 个测试
python scripts/compat_check.py            # redis-py 兼容性（需 pip install redis）
python scripts/benchmark.py -n 5000       # 基准（需先起服务器）
```

欢迎 Issue 和 PR！参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 路线图

- [ ] 主从复制（传播写命令流）
- [ ] AOF 追加日志 + 两种持久化并存
- [ ] SCAN 渐进遍历
- [ ] Pipeline 支持
- [ ] SET 类型 / ZSET（跳表）

## 许可证

[MIT](LICENSE)
