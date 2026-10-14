# Windows x64 便携版

CPU 与 NVIDIA GPU 两版均内置 Python、依赖、六个生产基线模型和前端，无需安装 Python、Node、Docker、PyTorch 或 CUDA Toolkit。GPU 版带 CUDA/cuDNN 运行库，只要求兼容的 NVIDIA 驱动；驱动不可用或运行库缺失时选 CPU 版。AMD/Intel 显卡不由此 CUDA 包加速。

包内仅含稳定 InSwapper 基线，不含研究/修复模型；界面显示实际执行后端。实测范围见 [windows-portable-validation.md](windows-portable-validation.md)，文件摘要见 ZIP 旁的 `.sha256`。构建成功不等于原生 Windows 或 GPU 验收通过。

## 使用

**前提。** 解压到 Windows 本地磁盘的可写短路径（如 `D:\PW`），不要在 ZIP 内、网络共享或 WSL 共享目录运行。

1. 双击 `Start.cmd`；模型加载后自动打开浏览器，启动窗口显示本机地址。
2. 载入随包样例或上传参考/目标图。工程、历史、素材和结果保存在包内 `data/`、`outputs/`，启动前检查两目录可写。
3. 使用 AI 精修前运行 `Configure-AI.cmd`，填写视觉模型、图像模型、API 地址和 Key 后重启。模型名须带 LiteLLM provider 前缀；OpenAI 兼容服务填 `openai/实际模型名`，发送前由 LiteLLM 去掉前缀。AI 联网并由服务商计费，启动与自检不调用模型。
4. 在启动窗口按 `Ctrl+C` 停止：HTTP 连接最多等待 10 秒，再执行应用关闭。再次启动会打开已有实例，不另建后台服务。

**数据与许可。** 公共包不含开发者凭据或工作记录。模型遵守 [原许可证](../models/README.md)；依赖许可证在 `runtime/Lib/site-packages/`，Python 许可证在 `runtime/`。

升级前停程序并备份整个旧目录，保留 `data/`、`outputs/`、`.env`，不以新包的 `data/` 覆盖它。迁机时先停止程序，再完整复制 `runtime/`、`api/`、`core/`、`utils/`、`config/`、`models/`、`web/dist/`、`manifest.json`、`data/`、`outputs/`；`.env` 含 AI 凭据，只交给被授权的人。

## 图片与路径

- **容量：** JPEG/PNG/WebP，最大 20 MiB、4000 万像素；内存不足会停止当前图片并提示。
- **相机 JPEG：** 带 MPF 信息且被 Pillow 识别为 MPO 时只读主图，不拆辅助帧。工程保留原始字节，回看使用 `image/jpeg`。实现 `core/image_security.py::image_mime_type`；回归 `tests/test_image_security.py`、`tests/test_projects.py`。
- **中文路径：** Python 打开文件，OpenCV 在内存编解码，不将路径直接传给 `cv2.imread/imwrite`。实现 [image_io.py](../utils/image_io.py)；回归 `tests/test_image_io.py`。
- **多图：** 可一次选择多张，程序逐张读取/处理。“等待内存”时释放其他程序占用即可继续；超过两分钟停止并说明，保留已完成图片。规则见 [内存调度](memory-scheduling.md)。

## 构建

**前提。** `scripts/build_windows_portable.py` 从 `uv.lock` 导出固定依赖，Windows ONNX Runtime 版本固定在脚本中。CPU/GPU 使用独立输出目录，不混装；仅 headless OpenCV 提供 `cv2`。构建者须提供来源已核实的 Microsoft VC 运行库。

```sh
uv run python scripts/build_windows_portable.py --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_portable.py --edition gpu --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_gpu_upgrade.py
```

**产物与完整性。** CPU 为 `dist/PersonaWeave-Windows-x64.zip`，GPU 为 `dist/PersonaWeave-Windows-GPU-x64.zip`，均附 SHA-256。`manifest.json`（文件清单）记录全部文件及模型摘要；模型仅按 `scripts/download_models.py::MODEL_SHA256` 验证后复制，Python 来源与官方摘要固定在构建脚本中。

构建会重建前端并整体替换 `web/dist`。VC 运行库五项须齐全且版本一致：`msvcp140.dll`、`msvcp140_1.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`、`concrt140.dll`。离线分词缓存 `cl100k_base` 仅接受规范化后匹配既定 SHA-256 的内容，避免离线启动尝试下载。

**发布与回滚。** 在同盘、同一文件系统的临时目录组装，ZIP 临时写入并逐项核对摘要；全部成功才一起发布。失败恢复原目录、ZIP 和摘要，不承诺抵御突然断电；发行目录和发布目标不接受符号链接。

**构建选项。** `--skip-deps` 仅复用依赖输入指纹、requirements、完整 runtime 文件集合及逐文件摘要都匹配的成功构建；无指纹清单须不带该参数重建。`--no-archive` 只更新目录，原 ZIP/摘要不变。公共包私有路径检查忽略 Windows 大小写及分隔符差异；含 `.env`、个人数据库或结果的目录须保留并另选输出目录，不收集凭据。个人完整包可额外带素材库和 AI 配置，清单标记 `private_configuration_included`，仅用于授权本机，不公开分发。

**原生验证。** 在 Windows 核对内置 Python 导入、CPU 模型加载、换脸 API、网页、工程恢复及结果下载：

- `scripts/smoke_windows_portable.py` 只在一次性副本做真实换脸、单实例保护、重启恢复、逐字节下载和删除验证，不对在用目录执行。
- GPU 另跑 `scripts/verify_windows_gpu.py` 的六模型真实 CUDA kernel profile（算子执行记录），及 `smoke_windows_portable.py --gpu`：强制 CUDA，核对 NVIDIA 运行库来自便携目录。健康检查不足以证明 GPU 验证成功。
- `scripts/verify_windows_dependencies.py` 在清除 Python/Conda/CUDA 路径的子进程运行，枚举 DLL 来源为包内、Windows 系统或外部。同机隔离测试不代表另一台全新 Windows 验收。

## GPU 选择

**选择依据。** 系统 `nvcuda.dll` 初始化成功且设备数有效，才证明驱动可用。`utils/gpu_helper.py` 用 ONNX Runtime `preload_dlls(directory="")` 及进程内 DLL 搜索目录加载随包库，并检查 CUDA Provider 的 DLL 依赖链；wheel 列出的 Provider（执行后端）不证明实际执行。依据：[预加载与 CUDA/cuDNN 兼容说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#preload-dlls)。

**结果。** 健康接口返回各模型实际加载后端和队列阶段；界面显示 CPU 模式、CUDA 已加载或部分模型 CPU，不以 `gpu_available` 单独判定。

## 升级、修复与自检

**CPU 升级到 GPU。** 关闭工作台，将 `PersonaWeave-Windows-GPU-Upgrade.zip` 解压到 CPU 目录、与 `Start.cmd` 同层，运行 `Upgrade-GPU.cmd`，之后仍用 `Start.cmd`。差分包须按用户拿到的 CPU manifest 构建；升级器核对该清单、现有程序和全部升级文件摘要，运行中停止替换。只移除 CPU 包文件，保留 `.env`、工程、历史、素材、结果；中断后重跑可继续。版本不匹配或程序被改动时停止覆盖，使用完整 GPU 包。实测：[GPU 升级记录](windows-gpu-upgrade-validation.md)。

**源码修复。** `scripts/build_windows_fix.py` 按原始 CPU/GPU 清单生成差异源码包，清单/ZIP 成员统一用正斜杠。临时 ZIP 成员、摘要和校验和全部通过才发布，失败恢复原 ZIP/校验和。安装复用 `scripts/upgrade_windows_gpu.py::apply_update` 的版本、摘要、运行锁校验，不改模型/用户数据。单独打过补丁的版本可能不匹配，此时停止，不强行覆盖。

**中断恢复。** 修复/GPU 升级在替换前写 `data/portable-update.json`；带检查的启动器见标记即停止，不加载模型或打开工程数据库。仅重跑同一升级包可继续，其他包不接管未完成事务。清单发布成功才清标记；清理中断则重跑校验替换文件及删除结果后解除保护。不要手删标记。未带检查的兼容启动器须先完成源码修复或使用完整包；此机制不承诺抵御突然断电。

**`Repair-Runtime.cmd`。** Windows 自带 PowerShell 更新程序目录 VC++ DLL，避免程序 Python 替换已加载库；原文件/manifest 备份到 `data/support_backups/`，不改系统库。运行中、载荷损坏或 DLL 被改动时停止更新。

**`Check-Install.cmd`。** `scripts/check_windows_portable.py` 检查文件、补丁标记、素材库引用及临时图片归档，输出不含 Key/图片内容的 `install-check.json`。缺失/不可读文件、清单或配置错误时保留已收集诊断；报告写入失败则非零退出并在控制台显示。素材路径仅用 SQLite `mode=ro` 查询，不迁表、切日志模式或写已有素材/工程数据；[WAL 只读查询](https://sqlite.org/wal.html#read_only_databases) 可能生成协调用 `-wal`/`-shm`。不执行模型，`missing_files: []` 不证明 GPU 或队列正常。

**`Check-Performance.cmd`。** 保持故障工作台运行再执行。`scripts/collect_windows_performance.py` 只读物理内存、Windows 提交额度、NVIDIA 查询及本地活动任务，输出 `performance-check.json`；不读 `.env`/图片、不另启模型、不调 AI。兼容服务缺 `model_providers/local_inference` 时报告为空，不补造硬件结论。

回归：`tests/test_windows_fix.py`、`tests/test_windows_gpu_upgrade.py`、`tests/test_windows_portable.py`、`tests/test_projects.py`。限定实测：[交付保护](validation/windows-portable-safety-20261008.md)、[收尾记录](validation/windows-closeout-20261010.md)。

## 排查

**HTTP 422。** 请求未通过接口或归档校验，须结合具体 POST/PUT 路由区分缩略图缺失、过期程序、素材不可访问、工程字段无效。状态码本身不证明缺驱动/模型；缺 VC 库通常导致启动或导入失败，也不足以解释所有 422。

**干净包与启动。** 公共干净包只有四张样例，不含个人素材/工程，不用其样例目录覆盖已配置目录。未签名程序的信任提示由 Windows 控制，打包不保证消除，也不关闭系统防护；Python 启动前的解压错误由解压环节处理。

**AI 路由。** 本地 Router 初始化失败（如模型协议识别失败）显示“AI 路由配置错误”，不伪报上游 HTTP 400；修复配置后重启，见 [模型目录](model-catalog.md)。处理依据：[五个 VC++ 组件](../.agents/notes/windows/20260923-vc-runtime-five-components.md)、[中文路径与相机 JPEG](../.agents/notes/windows/20260929-unicode-paths-and-camera-jpeg.md)、[校验后发布](../.agents/notes/windows/20260930-publish-only-verified-builds.md)、[中断保护与只读自检](../.agents/notes/windows/20261008-guard-interrupted-updates-and-readonly-checks.md)。
