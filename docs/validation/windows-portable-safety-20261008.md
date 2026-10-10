# Windows 便携交付保护验证：2026-10-08

范围：`bd6a675` 基础上的本轮源码改动。用户安装目录与个人数据保持原样，无付费 AI、应用启动或模型推理；不代表完整便携包或另一台干净 Windows 的验收。当前操作契约见 [Windows 指南](../windows-portable.md)。

## 自动回归

- `uv run ruff format .`：格式干净；`uv run ruff check .`：全部通过。
- `uv run pytest -q tests/test_windows_portable.py tests/test_windows_gpu_upgrade.py tests/test_windows_fix.py tests/test_gpu_helper.py tests/test_portrait_library.py tests/test_projects.py tests/test_documentation_consistency.py tests/test_documentation_governance.py`：185 通过。
- `uv run pytest -q`：844 通过，41.01 秒。测试通过不等于图片质量或新机器兼容性通过。

## 原生 Windows 针对性探针

通过 WSL 调用 Windows PowerShell，使用已有 10/04 CPU 包的嵌入式 Python 3.13.15（`-I -B -X utf8`）执行标准库临时样本与当前源码；仅解释器和源码访问共享路径，所有待更新文件、SQLite 库和合成图片创建在 Windows 本地 `TemporaryDirectory`，退出后清理。没有把共享路径部署当作受支持的应用启动。

实测 `sys.platform=win32`，全部断言通过、进程退出码 0：

- 原生路径修复包构建成功，清单与 ZIP 成员均无反斜杠。
- 构建写入异常保留上一版 ZIP 和校验和字节。
- `.ENV`、`OUTPUTS/private.png`、`DATA/private.png`、`api/private.DB`、`LOGS/private.txt` 均被公共构建预检拦截。
- 以旧清单为起点，在启动器更新后注入下一个源码文件替换失败：中断标记保留、启动检查停止；重跑同一载荷后内容与清单一致、标记移除。
- DELETE 模式素材库删去两张兼容表模拟旧库，自检及正式预览归档成功，数据库字节、日志模式与表集合保持不变。

Linux 回归额外覆盖发布校验和失败、源文件在打包中变更、标记清理中断后重试/篡改检查、其他事务冲突、WAL 协调文件边界和未 checkpoint 的已提交素材可见性。SQLite 的只读 WAL 查询可能维护辅助文件，详见 [官方说明](https://sqlite.org/wal.html#read_only_databases)；不使用 `immutable=1` 忽略真实库的并发变化。

## 实际交付修复包

`dist/updates-20261008/PersonaWeave-Windows-Source-Fix.zip`，4,306,193 字节；SHA-256：`6c9506debc6ad33a4458456a9327285757adb5358dd68de72efca299d04696a3`。生成命令：`uv run python scripts/build_windows_fix.py --cpu dist/release-20261004/PW-CPU --gpu dist/release-20261004/PW-GPU --output dist/updates-20261008/PersonaWeave-Windows-Source-Fix.zip`。此包只适配这两份具体清单，其他版本须另建对应差分；未重建或覆盖完整包。

独立核对 ZIP 校验和、CPU/GPU 原始清单摘要、目标清单摘要及所有差异载荷，字节均匹配当前源码；每版只替换 `api/portable.py`、`api/portraits/library.py`、README 和 Windows 指南。根目录附新版升级引擎、自检与操作说明，不含模型或个人数据库的文件内容。

追加原生验证使用 Windows 本地临时目录，复制真实 CPU/GPU 旧包的 API 源码、清单和指南，并放入合成配置、数据库及结果；不复制真实私人文件。直接导入旧 `api.portable`（确认无 `UPDATE_MARKER`）后执行 ZIP 内的引擎。启动器替换后注入 README 替换失败，再以独立 Windows Python 子进程调用新便携入口：退出码 1、提示升级未完成、未写运行地址。重跑后目标清单逐字节一致、标记清除，合成用户状态保留。CPU/GPU 两版均通过；没有启动模型服务。

最后更新文档及包说明后的检查：Ruff 通过，`test_windows_fix.py` 加文档一致性/治理共 46 项通过。独立只读复核未发现新增确认缺陷；其旧启动器真实进程验证缺口由上述追加原生探针覆盖。仍未验证突然断电、实际模型推理或另一台干净 Windows。
