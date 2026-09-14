# Windows GPU 差分交付实测（2026-09-11）

在原始 CPU 交付 ZIP 的 Windows 本地解压副本中保存测试工程、图片和测试 `.env`，执行 GPU 升级后核对字节，再运行 [`smoke_windows_portable.py --gpu`](../scripts/smoke_windows_portable.py) 的完整流程。未读取或复制开发者真实 AI 凭据。

- CPU 基准 manifest SHA-256：`d322f93676a3c55e313b2601274e9e2c48b1b2528143cf96db739c7f6ddc4aa9`。
- GPU 目标 manifest SHA-256：`a808f151e6b58cf79dae3cf1755b32a8d61a49aace644487310c71eed946a722`。
- 升级和重复执行合计 44.8 秒（本机观测）。全部升级载荷均检查摘要，旧文件只检查本次替换/删除范围，避免逐个复核两万多个不会改写的依赖文件；未改写模型。
- `.env`、工程数据库和已保存图片在升级前后 SHA-256 相同；升级载荷自动清理，第二次运行安全返回。重启后的工程列表仍保留原 CPU 工程。
- 升级后真实换脸检测 5 人、替换指定 1 人，2.99 秒；输出 SHA-256 与完整 GPU 包测试一致：`52b3c3f3678f19a49fa71b8f145d4eea9c0f070238371b44e533430f2e9ecb1c`。
- 进程中 12 个 NVIDIA 运行库均从升级后的便携目录加载；不依赖系统 CUDA Toolkit。保存/重启恢复/原字节下载/删除测试通过。
- 在 GPU 包进程中设置 `CUDA_VISIBLE_DEVICES=-1` 后，后端选择 `CPUExecutionProvider`。这验证无可见 CUDA 设备的选择分支；实际无 NVIDIA 驱动的电脑未现场测试，其错误分支由单元测试覆盖。
- Ruff 通过；便携版、升级器、GPU 选择、包边界、文档一致性共 60 项回归通过。六个模型的 CUDA kernel profiling 见 [GPU 模型实测](windows-portable-validation.md)。

| 交付文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `PersonaWeave-Windows-GPU-Upgrade.zip` | 1917270699 | `923667f03df0b2cc5c4db32f9992213e50a91cd995fb48484c2c62a80f7b6699` |
| `PersonaWeave-Windows-GPU-x64.zip` | 2983550632 | `6944b033a9f1fe9cd8473c1d399eb69f230a8e14ec93ee6334914b770f87a2d5` |

完整包全部 23879 个文件通过清单/摘要校验；升级包无重复 ZIP 条目、无重复模型载荷，CRC 与升级脚本源码匹配检查通过。原始 JSON、日志及输出位于本地 `build/windows/upgrade-validation.json`、`upgraded-gpu-smoke.json`、`gpu-archive-verification.json`。临时 Windows 副本在测试完成后清理。复现需使用一次性副本，方法见 [便携交付说明](windows-portable.md)，升级保护回归见 [`test_windows_gpu_upgrade.py`](../tests/test_windows_gpu_upgrade.py)。
