# Windows x64 便携版

提供 CPU 和 NVIDIA GPU 两种便携版，均内置 Python、依赖、六个生产基线模型及前端。GPU 版内置 CUDA/cuDNN 运行库，只要求电脑已有兼容的 NVIDIA 显卡驱动，不要求安装 Python、Node、Docker、PyTorch 或 CUDA Toolkit。未检测到可用 NVIDIA 驱动或缺失运行库时选择 CPU；界面就绪状态可查看执行后端。原生 Windows 实测见 `docs/windows-portable-validation.md`，最终文件摘要见压缩包旁的 `.sha256` 文件。

本次 GPU 交付面向 NVIDIA；AMD/Intel 显卡不由此 CUDA 包加速。已验证 RTX 4070 Laptop、驱动 591.59；其他硬件的速度和显存需求需实际测量。只随包提供稳定 InSwapper 基线，未包含可选研究/修复模型。

Windows 中文目录下的素材、参考图和结果文件统一由 Python 打开，再通过 OpenCV 内存编解码；不直接把路径传给 `cv2.imread/imwrite`。这修复了文件存在但提示 `Failed to read managed local image`、工程素材保存返回 422 和结果写入失败的问题。实现见 [utils/image_io.py](../utils/image_io.py)，回归见 `tests/test_image_io.py`、`tests/test_projects.py`、`tests/test_package_boundaries.py`。2026-09-11 已交付包需要应用此修复并重启，旧 ZIP 不会自动更新。

2026-09-14 在包含“材料/图片换脸任务”的原生 Windows 测试目录复现并验证：同一素材直接 `imread` 返回空，字节解码为 1024×1024；修复后读取素材、PNG 无损往返、CLI 共用参考特征读取、真实本地换脸与结果保存均通过。隔离 CPU 测试检测 5 人、换脸 1 人；工程素材接口分别导入素材 ID、受控原图 URL 和结果 URL 均返回 200。输入素材 SHA-256 为 `4e1e10c5cf63dd8b3af45b616003a99e6b1cec56e87e4597017aebce74804dad`，目标为随包 `data/test_images/group-01.png`；JPEG 结果摘要为 `8ccf2881ce472ab43b9e1d5e653c6471f01b0cc36708b5d5c30ac8dde0e9fa24`。此项验证图片 I/O 和本地流程，不代表新增 GPU 性能或 AI 服务验证。

1. 将整个压缩包解压到 Windows 本地磁盘的可写目录，例如 `D:\PersonaWeave`；不要在压缩包内、网络共享或 WSL 共享目录中直接运行。
2. 双击 `Start.cmd`。模型加载完成后自动打开浏览器；窗口显示本机访问地址。
3. 可载入随包样例，或上传自己的参考照片和目标图。工程、历史、素材与结果保存于包内 `data/`、`outputs/`。
4. 使用 AI 精修前，双击 `Configure-AI.cmd`，输入自己的视觉模型、图像模型、API 地址和 Key，然后重启。模型名称需要服务支持的 LiteLLM provider 前缀；OpenAI 兼容服务填写 `openai/实际模型名`，视觉和生图模型都要填写。前缀由 LiteLLM 处理，不会作为模型名称发送给服务商。AI 服务需要联网并由服务商计费；启动程序不会调用模型。
5. 停止程序请回到启动窗口按 `Ctrl+C`；第二次启动会打开已有实例，不会创建另一个后台任务服务。

升级前停止程序并备份整个旧目录；保留 `data/`、`outputs/` 和 `.env`。分发包不含开发者凭据或工作记录。内置模型保持原许可证约束，详情见 `models/README.md`；依赖许可证保留于 `runtime/Lib/site-packages/`，Python 许可证位于 `runtime/`。

## 构建

构建入口为 `scripts/build_windows_portable.py`。使用现有 `uv.lock` 导出固定依赖，Windows ONNX runtime 固定在构建脚本中；CPU/GPU 使用独立输出目录且禁止混装。GPU 的 NVIDIA 依赖保留锁文件版本。OpenCV headless 提供唯一 `cv2` 实现。Linux 开发环境与依赖不更改。

```sh
pnpm --dir web build
uv run python scripts/build_windows_portable.py --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_portable.py --edition gpu --msvc-dir /path/to/verified/microsoft-vc-runtime
uv run python scripts/build_windows_gpu_upgrade.py
```

CPU 产物为 `dist/PersonaWeave-Windows-x64.zip`，GPU 为 `dist/PersonaWeave-Windows-GPU-x64.zip`，均附 SHA-256；`manifest.json` 包含文件及模型摘要。模型只从已验证的 `scripts/download_models.py::MODEL_SHA256` 清单复制生产必需的六个 ONNX 文件。移除可重建字节码、NVIDIA 开发头文件/静态库/调试符号，保留 DLL 和许可证；使用标准 ZIP Deflate 6 压缩，Windows 可直接解压。Python 来源与官方摘要固定在构建脚本中。Microsoft VC DLL 应由构建者提供可再分发、来源已核实的运行库；不要求最终用户安装编译工具。差分包必须使用用户已经拿到的 CPU manifest 构建，不重建并替换基准版本。

Windows 必须实际验证：内置 Python 导入、原生 CPU 模型加载、换脸 API、网页打开、工程恢复及结果下载。构建成功本身不等于 Windows 运行验证通过。`scripts/smoke_windows_portable.py` 使用一次性测试副本执行真实换脸、单实例保护、重启恢复、逐字节下载和删除验证，输出 JSON 记录及结果图片；不对用户正在使用的目录运行此脚本。

GPU 测试还须运行 `scripts/verify_windows_gpu.py`，逐一检查六个模型的真实 CUDA kernel profile；`smoke_windows_portable.py --gpu` 强制要求 CUDA，并核对进程加载的 NVIDIA 运行库位于便携目录内。普通 CPU 冒烟和 GPU 内核审计分别保留，禁止仅据健康检查宣布 GPU 验证成功。

## 从已交付 CPU 版升级（下载较小）

关闭工作台，将 `PersonaWeave-Windows-GPU-Upgrade.zip` 解压到现有 CPU 程序目录，让 `Upgrade-GPU.cmd` 与原有 `Start.cmd` 放在一起，然后双击 `Upgrade-GPU.cmd`。升级完成后仍使用 `Start.cmd`。

升级包不重复携带未变化的模型、Python 和前端。升级器核对已交付 CPU manifest、现有程序和全部升级文件摘要，运行中拒绝替换；只移除旧 CPU 包文件，保留 `.env`、工程、历史、素材与结果。中断时重新执行可继续，版本不匹配或程序文件被人为修改时拒绝覆盖，应改用完整 GPU 包。成功后自动清理升级载荷，可删除剩余 `gpu-update/` 与 `Upgrade-GPU.cmd`。

Windows 驱动检查使用系统 `nvcuda.dll` 的初始化和设备数；`utils/gpu_helper.py` 通过 ONNX Runtime `preload_dlls(directory="")` 和进程内 DLL 搜索目录加载随包运行库，并验证 CUDA provider 的 DLL 依赖链。[官方预加载与 CUDA/cuDNN 兼容说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#preload-dlls)是依据；不把 wheel 列出的 provider 当作真实执行证据。

本地 Router 初始化失败（例如无法识别模型协议）会单独显示为 AI 路由配置错误，不再误报为上游 HTTP 400；实际服务端错误仍显示 HTTP 状态码。修复配置后须重启程序；旧失败记录保留原错误，不会自动重试。实现与回归见 [模型目录](model-catalog.md)。

工程保存会分别归档素材库参考原图和界面预览缩略图，修复预览地址被误认为本地路径而返回 422 的问题；恢复后的图片独立于原素材库。路径校验与回归见 [工程历史契约](task-history.md)、`api/projects/recovery.py` 和 `tests/test_projects.py`。

## 跨机器复制与 422 排查

复制已配置目录时，需保留同层的 `runtime/`、`api/`、`core/`、`utils/`、`config/`、`models/`、`web/dist/` 和 `manifest.json`。要带走现有素材/工程/结果，必须连同 `data/` 与 `outputs/` 一起复制，复制前停止程序以保证 SQLite 与图片一致。原始干净包不含个人素材库和工程，仅带四张示例图；不能用干净包的示例目录覆盖已配置目录。复制包含 `.env` 的已配置目录会同时携带本机 AI 凭据，应只交给获授权使用这些配置的人。

HTTP 422 表示请求被接口校验或归档逻辑拒绝，不能单凭状态码认定缺驱动或缺模型。原图上传支持 JPEG/PNG/WebP，最大 20 MiB、4000 万像素（`core/image_security.py`）；后续工程图片仍需满足校验。缺失缩略图、过期程序、无法访问的素材及无效工程字段需要结合具体 POST/PUT 路由区分。

`scripts/build_windows_fix.py` 为已有原始 CPU/GPU 清单生成仅含差异源码的修复包，复用 `scripts/upgrade_windows_gpu.py::apply_update` 的版本/摘要/运行锁校验，源码更新不修改模型或用户数据；独立的 `Repair-Runtime.cmd` 只更新程序目录的 VC++ DLL 并备份原文件，不改 Windows 系统库。已单独打过补丁的版本可能不匹配，必须停止而非强行覆盖。`Check-Install.cmd` 可独立执行 `scripts/check_windows_portable.py`，核对文件、补丁标记、素材库引用和临时图片归档，输出不含 Key/图片内容的 `install-check.json`；不调用 AI，也不证明真实图片或全部接口都可用。相关回归见 `tests/test_windows_fix.py`、`tests/test_windows_gpu_upgrade.py` 和 `tests/test_projects.py`。

开发环境依赖核查脚本为 `scripts/verify_windows_dependencies.py`：须由包内 `runtime/python.exe` 执行，在子进程清除 Python/Conda/CUDA Toolkit 路径、仅保留 Windows System32 PATH 后，运行示例换脸并枚举已加载 DLL 的来源。它区分包内、Windows 系统及外部模块；不能把同一台机器的环境隔离测试称为另一台全新 Windows 的验收。GPU 仍需要机器具有可用 NVIDIA 显卡及驱动，不需要额外安装 CUDA Toolkit。

### 2026-09-23 VC++ 漏项核查与修复

原始 CPU/GPU 包均遗漏 `msvcp140_1.dll`。原包摘要核验可全部通过，因为该文件从未进入旧清单；检查 ONNX Runtime 的 PE 导入表（例如 `objdump -p runtime/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd`）确认它是直接依赖。实际清空开发环境后的 Windows 运行从 System32 加载了该文件，因此本机成功不能证明未安装 VC 运行库的机器可用。

构建清单 `scripts/build_windows_portable.py::MSVC_FILES` 现在包含五个组件：`msvcp140.dll`、`msvcp140_1.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`、`concrt140.dll`；构建前先检查是否齐全。修复包使用同版本 14.51.36247.0、Microsoft 数字签名验证为 Valid 的五个组件；`vc-runtime.json` 记录每个文件 SHA-256。不要混用不同版本的零散 DLL。

从已修复 E 盘目录复制的版本可先关闭后端、保留网页，将自检修复包解压到与 `Start.cmd` 同层，执行 `Repair-Runtime.cmd`。它通过 Windows 自带 PowerShell 更新应用目录 DLL，避免使用程序自身 Python 更新其已加载的运行库造成文件锁；锁定运行中的应用、损坏载荷或未记录的 DLL 改动均拒绝更新。原文件及 manifest 备份到 `data/support_backups/`，已有源码补丁、配置和工程保留。原始发行包需修复源码时，顺序为 `Apply-Fix.cmd` 后 `Repair-Runtime.cmd`。

原生验证：隔离开发路径后 CPU/GPU 均检测 5 人、实际换脸 1 人；补丁前后中文受控图片和素材库缩略图接口由 422 变为 200，工程保存/恢复通过。报告为 `build/windows/dependencies-{cpu,gpu}-20260923.json`、`vc-import-audit-20260923.json` 与 `runtime-repair-validation-20260923.json`。环境隔离并不是一台全新 Windows 的验收；另一台机器的 422 仍需检查具体请求，缺库通常也会造成启动或导入失败，不能将所有 422 都归因于此漏项。

## 个人素材包

当前个人完整包含自然上镜风格的虚构中国年轻人素材，男女各 32 位、每人 16 张独立参考。
解压到新目录即可使用。为控制体积，完整包只带当前可选素材；已有安装目录更新时则保留旧素材，
以支持历史工程。迁移旧工程必须复制其完整工作目录，不能用新包的 `data/` 覆盖旧目录。
素材总览见包内 `素材总览.jpg`，制作与验证见 [当前素材实测记录](portrait-chinese-validation.md)。

## 多图排队与内存不足

可一次选入多张图片，程序会逐张读取和处理。右侧或底部若显示等待内存，请先关闭占内存的软件，任务会在内存恢复后继续；等待超过两分钟会停止并弹窗说明，已完成图片保留。不会自动缩小原图或重试付费 AI。旧工作区要使用新增侧脸参考，需要重新从素材库选入人物。详细规则见 [内存与排队](memory-scheduling.md)。
