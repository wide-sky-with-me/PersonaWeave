# TestClient 停滞须先核对 socketpair 唤醒权限
状态：有效
日期：2026-10-10

## 背景

工程接口回归停在 `tests/test_projects.py::test_http_contract_safe_media_and_stale_save`。线程栈显示主线程等待 AnyIO portal 启动，事件循环线程等待 selector；尚未执行工程路由。相同沙箱中的空 FastAPI TestClient 也停滞，标准库 socketpair 的发送返回 `PermissionError(1, "Operation not permitted")`。同一接口测试在沙箱外 0.47 秒通过。

## 决策

将这次停滞归为测试执行环境限制。HTTP 回归须在允许 asyncio socketpair 跨线程唤醒的环境运行；受限环境用有时限的最小应用和线程栈先定位，再运行同一测试作对照。工程路由、TestClient 和接口断言保持不变，不能把在沙箱中完成的纯函数测试当作 HTTP 回归通过。

## 被放弃的方案

- 修改工程路由或 SQLite 锁：复现发生在空应用的 TestClient 进入阶段，没有证据指向业务实现。
- 跳过接口测试、改用无生命周期客户端或 monkeypatch 事件循环：会改变验证范围或掩盖执行环境限制。
- 无限等待或提高超时：被禁止的跨线程唤醒不会因此恢复，须用有界探针并清理进程。

## 影响与验证

工程回归保持 [test_projects.py](../../../tests/test_projects.py) 的原始断言；执行前提见 [工程架构](../../../docs/engineering-architecture.md)，本次复现和完整检查结果见 [收尾记录](../../../docs/validation/windows-closeout-20261010.md)。此结论仅解释本次环境；其他停滞仍须先取线程栈，不能统一归因于沙箱。
