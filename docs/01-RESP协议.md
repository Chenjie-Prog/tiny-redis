# 第 1 章：RESP 协议 —— 线缆上的字节

任何 Redis 客户端和服务器之间的对话，都是 RESP（REdis Serialization
Protocol）格式的字节流。tiny-redis 实现的是 **RESP2**——Redis 1.x 到 5.x
使用的版本，也是绝大多数客户端库的默认选择。

完整实现见 [`tinyredis/protocol.py`](../tinyredis/protocol.py)。

## 五种类型

RESP2 一共只有五种类型，每种以一个字节开头，以 `\r\n` 结尾：

| 类型 | 首字节 | 例子 |
| :-- | :-- | :-- |
| 简单字符串 | `+` | `+OK\r\n` |
| 错误 | `-` | `-ERR unknown command\r\n` |
| 整数 | `:` | `:42\r\n` |
| 批量字符串 | `$` | `$5\r\nhello\r\n`，`$-1\r\n` 表示 NULL |
| 数组 | `*` | `*2\r\n$3\r\nGET\r\n$3\r\nkey\r\n` |

## 客户端 -> 服务器：只有一种报文

客户端发的永远是"批量字符串组成的数组"。`SET key value` 在线上是：

```
*3\r\n
$3\r\n
SET\r\n
$3\r\n
key\r\n
$5\r\n
value\r\n
```

`*3` 表示数组有 3 个元素，`$3` 表示接下来的 3 字节是一个批量字符串。
**内容里出现 `\r\n` 也没关系**——因为长度已经事先声明，解析器按长度读，
这就是 RESP 是"二进制安全"的原因。

## 服务器 -> 客户端：按值类型选报文

`GET` 返回批量字符串（键不存在返回 `$-1`），`INCR` 返回整数，
`LPUSH` 返回新长度，`KEYS` 返回数组……错误永远是 `-` 开头的行。

## 内联命令

除了 RESP 数组，服务器还接受一行普通文本（telnet 调试友好）：

```
SET "my key" value\r\n
```

tiny-redis 用 `shlex` 切分，所以引号里可以带空格。

## 增量解析：TCP 的粘包与半包

TCP 是字节流：一次 `recv` 可能读到半条命令，也可能读到三条半命令。
所以解析器必须能"吃一口等一口"。`RespParser` 的做法：

1. `feed(data)` 把新到的字节追加进内部缓冲区；
2. `next_command()` 尝试从缓冲区开头解析一条完整命令；
   - 不完整 → 返回 `None`，等下一次读事件；
   - 完整 → 把已消费的字节从缓冲区删掉，返回参数列表；
   - 非法 → 抛 `RespError`，服务器回错误后断开连接。

因为所有状态都在缓冲区里，这套解析器对"一个字节一个字节喂进来"
也能正确工作（测试 `test_partial_feed_byte_by_byte` 验证了这一点）：

```python
parser = RespParser()
wire = encode_command(["SET", "k", "v"])
for i, byte in enumerate(wire):
    parser.feed(bytes([byte]))
    result = parser.next_command()
    if i < len(wire) - 1:
        assert result is None
assert result == [b"SET", b"k", b"v"]
```

## 防御性上限

真实 Redis 有两条硬上限：multibulk 元素数 ≤ 1024*1024，单个 bulk
≤ 512MB。tiny-redis 保留同样的检查（bulk 上限默认 64MB），恶意报文
在长度头阶段就被拒绝，永远不会先分配一块巨大的内存。

## 小结

- RESP2 = 5 种前缀类型，客户端请求固定是 bulk 数组；
- 二进制安全来自"先声明长度，再读内容"；
- 增量解析器 + 缓冲区，是对付 TCP 流的唯一正确姿势。

下一章看这些字节被解析出来之后，进入什么样的事件循环。
