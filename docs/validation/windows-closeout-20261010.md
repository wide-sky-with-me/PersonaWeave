# Windows 改动收尾与工程接口回归排查：2026-10-10

范围：`bd6a675` 基础上的 Windows 交付保护改动；本记录补充 [2026-10-08 验证](windows-portable-safety-20261008.md)，不改写其历史结论。当前交付契约见 [Windows 指南](../windows-portable.md)。

## 交付行为

- 公共构建按 Windows 大小写和分隔符归一检查私人配置、数据库及结果；源码修复包使用正斜杠路径键。
- 修复包在同盘暂存，核验 ZIP 成员集合、逐成员摘要后发布 ZIP 与校验和；异常保留上一版产物。
- 差分更新持有应用锁，完整校验后写入中断标记，优先更新便携启动器；混合安装停止启动，同一载荷可重试。清单已发布但标记清理失败时，重新核验替换及删除结果后才解除保护。
- 素材路径查询使用 SQLite `mode=ro`，自检及预览归档不迁移表、不切换日志模式。WAL 协调文件可能由 SQLite 维护，已提交 WAL 数据保持可见。

实现与回归入口见 [Windows 指南](../windows-portable.md#升级修复与自检)，原因见 [交付保护决策](../../.agents/notes/windows/20261008-guard-interrupted-updates-and-readonly-checks.md)。

## 工程接口测试停滞

受限沙箱运行 `tests/test_projects.py::test_http_contract_safe_media_and_stale_save`，设置 `-o faulthandler_timeout=10` 后取栈：主线程停在 Starlette `TestClient.__enter__` / AnyIO `start_task_soon`，循环线程等待 selector，尚未进入工程路由。探针外层设置 30 秒超时和 5 秒强制终止，进程已结束。

最小探针在相同环境中执行：

```python
import socket
from fastapi import FastAPI
from fastapi.testclient import TestClient

left, right = socket.socketpair()
try:
    left.send(b"x")
except OSError as error:
    print(repr(error), flush=True)
finally:
    left.close()
    right.close()
with TestClient(FastAPI()):
    print("entered", flush=True)
```

通过 `uv run --no-sync python -u` 执行，外层设置 12 秒超时和 3 秒强制终止。发送返回 `PermissionError(1, 'Operation not permitted')`，空应用进入同样停滞，进程已结束。允许 socketpair 唤醒的沙箱外运行同一工程接口测试，**1 项通过，0.47 秒**。

本次无需修改工程接口、SQLite 或测试断言；不能用跳过测试或替换生命周期掩盖执行环境限制。执行前提与取舍见 [测试环境决策](../../.agents/notes/platform/20261010-testclient-requires-socketpair-wakeup.md)。

## 提交前检查

Python 使用 `/tmp/personaweave-windows-closeout-cache` 作为临时 uv 缓存，避免默认缓存目录的只读限制；不更新依赖。需要 TestClient 的回归在允许 socketpair 唤醒的沙箱外执行。

- `uv --cache-dir /tmp/personaweave-windows-closeout-cache run --no-sync pytest -q -o faulthandler_timeout=30`：**844 项通过，39.08 秒**。
- `uv --cache-dir /tmp/personaweave-windows-closeout-cache run --no-sync ruff format .`：无格式改动；同环境 `ruff check .` 全部通过。
- `pnpm --dir web build`：TypeScript 和 Vite 生产构建通过。相同源码在本会话前一轮 `pnpm --dir web lint` 与 `pnpm --dir web test` 通过，**42 个测试文件、238 项测试通过**；本轮没有前端源码改动。

- `uv --cache-dir /tmp/personaweave-windows-closeout-cache run --no-sync pytest -q tests/test_documentation_consistency.py tests/test_documentation_governance.py`：本轮说明和决策落盘后 **23 项通过，0.28 秒**。

检查只验证代码行为、契约和构建，不代表画质达标。

## 验证范围

本次不启动真实应用、不调用付费 AI、不改用户安装或个人数据。完整便携包重建、实际模型推理、另一台干净 Windows、突然断电恢复及最低硬件容量未在本轮验收。10/08 修复 ZIP 与校验和属于那次具体载荷，本次说明文档不能作为它的逐字节对应证明。
