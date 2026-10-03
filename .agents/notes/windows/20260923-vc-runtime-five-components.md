# 便携包必须带齐五个 VC++ DLL，文件清单校验通过不代表依赖完整

状态：有效
日期：2026-09-23

## 背景

原始 CPU/GPU 便携包遗漏了 `msvcp140_1.dll`。包内 `manifest.json` 校验全部通过，因为这个文件从未进入清单；检查 ONNX Runtime 的 PE 导入表（如 `objdump -p runtime/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd`）才确认它是直接依赖。在开发机上清空开发环境后运行成功，是因为该 DLL 从 System32 加载，所以“本机成功”不能证明未安装 VC 运行库的机器可用。

## 决策

- 构建清单固定为五个组件（`scripts/build_windows_portable.py::MSVC_FILES`），构建前先检查齐全；同一版本、Microsoft 数字签名为 Valid，不混用不同版本的零散 DLL。
- 依赖核查用 `scripts/verify_windows_dependencies.py`：在清除开发路径、仅保留 System32 的子进程里运行，并枚举已加载 DLL 来源。
- 旧包用 `Repair-Runtime.cmd` 修复，且用 Windows 自带 PowerShell 执行：用程序自身 Python 更新其已加载的 DLL 会被文件锁挡住。

## 被放弃的方案

- **只靠清单摘要核验完整性**：清单漏项时摘要永远通过。
- **让用户自行安装 VC++ 可再分发包**：目标是不要求最终用户安装任何开发运行库。
- **把所有 HTTP 422 都归因于缺库**：422 是接口校验/归档拒绝，缺库通常表现为启动或导入失败。

## 影响与验证

流程见 [windows-portable.md](../../../docs/windows-portable.md)；回归 `tests/test_windows_portable.py`、`tests/test_windows_fix.py`。环境隔离测试不等于另一台全新 Windows 的验收。
