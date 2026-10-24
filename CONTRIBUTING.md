# 贡献指南

感谢关注 tiny-redis！这个项目有两个不变的原则：

1. **运行时零依赖**——标准库能解决的，不引入第三方包；
2. **代码要能当教程读**——宁可用十行清楚的代码，不用五行聪明的代码。

## 开发流程

```bash
git clone https://github.com/USER/tiny-redis.git
cd tiny-redis
python -m unittest discover -s tests     # 133 个测试应全部通过
```

1. Fork 并新建分支（`git checkout -b my-feature`）。
2. 修改代码，为新行为补测试用例（`tests/` 下）。
3. 运行测试套件，确保全部通过；若改动涉及协议，跑
   `pip install redis && python scripts/compat_check.py` 确认官方客户端兼容。
4. 提交 PR，描述里写清动机和改动范围。

## 代码风格

- Python 3.9+ 兼容（不要用 3.10+ 才有的语法）。
- 测试用 `unittest`，不引入 pytest。
- 错误信息保持与真实 Redis 逐字一致，改动前先查 Redis 文档/源码。
- 教程（docs/）与代码同步更新：改了行为就改对应章节。

## 报告问题

提交 Issue 请附上：最小复现（redis-cli 会话、协议字节或测试用例）、
Python 版本、期望行为与实际行为。协议类问题最好附上原始字节。
