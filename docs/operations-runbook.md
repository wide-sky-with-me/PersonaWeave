# PersonaWeave 运行维护手册

值班遇到故障，先记录版本、入口、记录 ID 和脱敏错误，再按 [故障排查](#故障排查)定位。准备备份、迁移或更新时，先确认责任人、实际数据目录和停机窗口，再执行对应章节。检查失败就保留原实例与副本，停止切换；命令成功不等于恢复验收通过。

产品与代码地图见 [项目交接](project-handover.md)，平台安装见 [README](../README.md)、[Docker 部署](docker-deployment.md)、[Windows 便携版](windows-portable.md)。以下命令在安装/仓库根目录执行，地址与目录按实例登记核对；本文没有命令执行或目标实例验收声明。

## 实例登记与启动检查

**先登记，再启动。** 移交双方共同补齐内部记录，填写实际值，不只写“最新版本”，也不把秘密加入仓库。

- **责任与入口**：移交人、接任人、维护联系人、备份保管人、AI 账号及费用责任人；启动/停止方式、工作目录、端口、地址、CORS 与外部访问隔离。
- **版本与环境**：Git commit、工作区补丁、锁文件、镜像 ID 或便携 manifest/ZIP SHA-256；OS/CPU 架构、Python/Node/pnpm、驱动与实际模型 Provider。
- **数据与配置**：实际 models/data/outputs 根目录、外置素材库、容量和写权限；生效 AI YAML 路径、环境变量名称与服务归属。秘密单独移交。
- **恢复与验收**：备份位置、周期、保留期、恢复时间/数据损失目标和最近演练；样本、模型摘要、检查命令、运行/画质结论与未执行项。

源码版本与工具检查：

```bash
git rev-parse HEAD
git status --short
uv --version
node --version
pnpm --version
```

依赖沿用 [Python 清单](../pyproject.toml)/[锁文件](../uv.lock)和 [前端清单](../web/package.json)/[锁文件](../web/pnpm-lock.yaml)。[源码启动器](../scripts/dev.py)不安装依赖或下载模型；容器入口在必需模型缺失时触发下载。非空文件不证明权重完整，来源、摘要与许可查 [模型说明](../models/README.md)和 [下载器](../scripts/download_models.py)。

启动后打开页面和 `/docs`，再检查健康接口：

```bash
curl -fsS http://127.0.0.1:8000/health
```

源码/容器默认 API 端口 8000；自定义绑定使用实际地址，Windows 使用启动窗口地址。核对 `pipeline_ready`、`model_providers`和 `local_inference`。缺项或启动失败先查 [故障排查](#故障排查)，不把返回 HTTP 成功当作模型就绪。

`gpu_available`或 Provider 列表不证明 CUDA kernel 执行，核验见 [Windows GPU 指南](windows-portable.md#gpu-选择)。`ai_refinement_ready`只表示本地配置/初始化就绪，不证明远端鉴权、额度、价格或图像编辑 endpoint 可用。字段实现见 [system.py](../api/routes/system.py)。

## 访问与进程边界

部署前落实入口访问控制。服务没有应用登录隔离，[应用装配](../api/app.py)中的 CORS 不是认证或 HTTP 访问控制。

[开发器](../scripts/dev.py)与 [Compose](../docker-compose.yml)默认启动/发布全接口，[Windows 入口](../api/portable.py)使用回环地址。Docker 仅本机使用可设 `PERSONAWEAVE_BIND_ADDRESS=127.0.0.1`；内部访问使用明确接口与网络策略，外部访问控制在入口层落实。

**服务访问权包含业务数据读取能力。** [文件路由](../api/routes/files.py)的 `/api/v1/data/{subpath}`返回 `data/`内现存文件，`/api/v1/files/{subpath}`返回 `outputs/api_results/`内现存文件。目录穿越检查不提供登录隔离，data 路由也没有“仅四张样例”白名单；领域 ID/摘要检查不是用户权限控制。

生产共享流水线要求 `PERSONAWEAVE_INFERENCE_CONCURRENCY=1`，其他值启动校验失败。单进程运行一个实例；不增加 uvicorn workers，也不让两个服务共享持久目录。精修恢复没有跨进程租约，见 [共享槽位](../api/inference_limiter.py)与 [精修存储契约](refinement-session-api.md)。

## AI 配置与调用费用

**先确认服务归属、费用与验证范围。** 只读 [.env.example](../.env.example)核对变量名称，已有 `.env`保持原样。视觉与图像服务独立配置，秘密只存部署环境，YAML 使用环境引用。改配置后重启后端并查就绪状态；尺寸、fallback、默认零重试和 endpoint 支持见 [模型目录](model-catalog.md)。

关闭批任务 AI 精修不等于准备流程没有远端调用。多目标图片自动跨图聚类，孤立簇与不完整簇满足条件时进入 VLM 仲裁；建议仍经 ArcFace 复核。生产入口见 [useIdentityClustering](../web/src/hooks/useIdentityClustering.ts)、[faces 路由](../api/routes/faces.py)、[arbiter](../core/ai/arbiter.py)。视觉仲裁与后置精修是不同调用。

仅验本地时，使用无有效 AI 连接/凭据的隔离副本，或明确本地检测/换脸入口，并关闭后置精修；不要只凭 `ai_refinement=false`声明零 AI 调用。

付费验证单独记录 endpoint、实际请求次数和结果。失败或取消不保证已发上游请求免计费；先检查候选是否保留，再由人决定是否重试。

## 持久数据与资产清单

按实际目录登记并成套备份。默认路径相对运行工作目录；`PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT`及自定义存储/配置路径另列入同一批清单。下面只列备份对象，不定义业务状态机。

- **批任务与旧精修档案**：`data/task/tasks.sqlite3`，请求/元数据与结果文件一起保留。见 [persistence.py](../api/tasks/persistence.py)、[legacy.py](../api/refinement/legacy.py)。
- **编辑工程**：`data/projects.sqlite3`，连同工程关联及独立媒体副本保留。见 [ProjectStore](../api/projects/store.py)。
- **精修会话**：`data/refinement_sessions.sqlite3`，保留轮次、版本、事件、接受指针与私有证据。见 [SessionStore](../api/refinement/store.py)。
- **素材库**：完整 `data/portrait_library/`，包括 `library.sqlite3`、originals、thumbnails、quarantine 和归档身份，不替换 ID。见 [素材库](portrait-library.md)。
- **正式结果与工程媒体**：`outputs/api_results/`，保留原画布、批/单图执行结果、工程副本和接受图片。见 [任务历史](task-history.md)、[工程契约](studio-projects.md)。
- **精修证据与候选**：`outputs/refinement_staging/sessions/`，包括原图、基线、参考与全部版本。只有 accepted 图片不足以恢复对话，见 [精修存储](refinement-session-api.md)。
- **旧档案评价证据**：`outputs/refinement_evidence/`，按旧记录归属保留，不当作新会话事实源。见 [质量档案](delivery-quality.md)。
- **权重与配置**：`models/`、`config/`与独立环境配置。保留实际模型摘要，未托管可选权重另登记，凭据独立受控保管。见 [模型说明](../models/README.md)、[模型目录](model-catalog.md)。
- **便携程序**：runtime、api/core/utils、web/dist、manifest 等，使用完整匹配版本，目录与清单见 [Windows 指南](windows-portable.md)。

服务可按需生成目录，但空库或缺失素材不是完整素材包。Git clone、wheel 和镜像不含全部权重、素材及结果。空实例与追加导入见 [素材库迁移](portrait-library.md#新实例与迁移)。

浏览器 localStorage/sessionStorage 只存轻量关联与选择，不代替数据库。跨浏览器从工程列表显式选择；缺图或摘要损坏不得用缩略图、猜测路径补成成功。独立副本的保证范围见 [工程契约](studio-projects.md)。

## 停机与一致性备份

**前提**：确认停机窗口、备份责任人和外置目录。停止新操作，等保存和活动本地/AI 任务收尾，再停止服务。强行停止可能留下中断状态，恢复逻辑不保证上游请求取消或免费。

- 源码：启动终端 `Ctrl+C`，确认 API 与 Vite 子进程结束。
- Docker：沿用启动时的 Compose 文件组合，执行 `stop personaweave`并确认停止。
- Windows：启动窗口 `Ctrl+C`，等原生线程和进程退出，不边运行边复制。

CPU 容器停机示例：

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml stop personaweave
docker compose -f docker-compose.yml -f docker-compose.cpu.yml ps -a
```

默认 Compose 使用宿主 bind mount，停机后从宿主备份。SQLite 与图片没有跨库/文件的在线全局事务，停机整套复制是恢复基线。随目录保留存在的 `-wal`、`-shm`或 journal，不手动删除，也不只复制一个 `.sqlite3`。

下面归档 Linux 默认目录。先设置仓库外绝对 `BACKUP_DIR`；目录不存在则创建，重名归档使命令停止。同一时点复制 `data/outputs/config/models`，外置素材/配置单独纳入；tar 默认不追随符号链接，须核对链接实际数据是否备份。

```bash
set -eu
: "${BACKUP_DIR:?请设置仓库外的绝对备份目录}"
mkdir -p "$BACKUP_DIR"
backup_file="$BACKUP_DIR/personaweave-$(date +%Y%m%d-%H%M%S).tar.gz"
test ! -e "$backup_file"
tar -czf "$backup_file" data outputs config models
sha256sum "$backup_file" > "$backup_file.sha256"
printf '%s\n' "$backup_file"
```

**完成条件**：退出码成功，归档可读，摘要与清单核对通过。任一失败都先保留现场、修正备份，不把不完整归档用于迁移。备份后按原入口恢复服务；是否可恢复须按下一节演练。

归档含用户图片、请求与对话，按内部权限加密保管，不作公共交付物。上述归档不含 `.env`，秘密通过独立受控渠道移交。Windows 停机复制完整目录并保留 manifest/摘要。存储、频率、保留期与恢复目标由实例负责人落实。

历史验收中的 `start.sh`、`smoke.py`、`restart-vm.sh`和 `outputs/deployment/`原报告是实例/诊断资产，Git clone 不自带。重放同次实验需另移交原件与摘要；没有原件只引用记录范围。新实例采用 [README](../README.md)与 [Docker 托管命令](docker-deployment.md)，登录守卫模板见 [安装指南](macos-boot-guard.md)。

## 恢复与迁移

**前提**：备份摘要可信，程序/模型/数据版本匹配，有独立恢复目录。保留故障实例，不在唯一原件上演练。

1. 核对备份、源码/镜像/便携 manifest、模型摘要与外置路径。
2. 在隔离副本解包，重建匹配依赖或复制完整 runtime，通过受控渠道设置配置。
3. 检查 SQLite、图片/模型清单与写权限；数据库完整与图片完整分别验证。
4. 启动副本，打开工程、历史、原图、本地结果、候选及接受版，下载核对摘要；刷新/重启后再查。
5. 全部通过后停目标实例，保留完整回退副本，再切换同批数据集；不以空 `data/`覆盖原实例。

**失败分支**：摘要、版本、图片或实际访问任一不符，停止切换，保留原件与演练副本。`integrity_check`为 ok 不是恢复成功的充分条件。

Linux 解包示例只接受尚不存在的目录。`BACKUP_FILE`须为已核验的内部归档，`RESTORE_DIR`须为独立绝对路径；解包后不直接接入生产服务。

```bash
set -eu
: "${BACKUP_FILE:?请指定已核验的备份文件}"
: "${RESTORE_DIR:?请指定独立恢复目录}"
test -f "$BACKUP_FILE"
test ! -e "$RESTORE_DIR"
mkdir -p "$RESTORE_DIR"
tar -xzf "$BACKUP_FILE" -C "$RESTORE_DIR"
```

下面在源码环境中只读检查副本的 SQLite。将 `RESTORE_DIR`export 给子进程；不打开应用或触发表迁移。

```bash
export RESTORE_DIR
uv run --no-sync python - <<'PY'
import os
import sqlite3
from contextlib import closing
from pathlib import Path

root = Path(os.environ["RESTORE_DIR"]).resolve()
databases = sorted((root / "data").rglob("*.sqlite3"))
if not databases:
    raise SystemExit("No SQLite databases found; check the backup inventory")
for path in databases:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        result = db.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise SystemExit(f"Integrity check failed: {path.name}")
    print(path.relative_to(root), "ok")
PY
```

外置素材库不在默认枚举中，按登记另查。首次启动可能建表、兼容迁移和恢复中断状态，须在副本进行。跨版本迁移/回退先验证 schema，不混配新数据库与旧程序。

外部图片 URL 可能失效，未保存媒体和草稿不会因复制数据库出现。恢复不补发付费轮次；有候选的中断会话和无候选任务按 [精修契约](ai-refinement.md)、[任务历史](task-history.md)解释。验收须验证实际媒体字节与访问。

## 更新与回滚

**更新前**：记录代码/交付标识，保留未提交补丁，完成同批数据备份。读 [CHANGELOG](../CHANGELOG.md)及当前专题的迁移要求，重点核对配置、数据和接受语义。

| 形态 | 更新要点 | 回滚保留 |
| --- | --- | --- |
| 源码 | 停机；同步锁定依赖、配置/资产，构建生产前端 | 匹配代码、锁文件、前端产物与数据快照 |
| Docker | 原 Compose 组合重建，保留持久挂载，记录 image ID | 原镜像及同版本数据；`latest`不是固定版本 |
| Windows | 按 manifest 用完整包/修复包/GPU 差分包，先过运行锁与摘要检查 | 停机完整匹配副本；版本不符就停止 |

按原入口启动并复核。测试容器不得并行写生产目录，源码回滚不能只替换一份 Python 文件。

生产页面来自 `web/dist/`，改源码后须重建，启动旧 bundle 不会更新页面。Windows 修复/GPU 升级中断保留 `data/portable-update.json`，重跑同一载荷，不删标记。机制见 [更新规则](windows-portable.md#升级修复与自检)。

**失败与回滚**：没有通用数据库 downgrade 承诺。先确认旧程序能读匹配快照，再恢复程序与数据的成套备份。需保留升级后新增结果时，另存完整事故副本，不直接合并数据库。

## 故障排查

记录实际版本、操作入口、task/session/project ID、状态/时间及脱敏错误。按健康信息、领域详情、部署日志定位；不上传图片、凭据或原始上游响应。只读诊断与付费重试分开。

- **启动失败/页面打不开**：查工作目录、端口、模型、依赖、web/dist、写权限与容器状态。入口见 [启动器](../scripts/dev.py)、[Docker](docker-deployment.md)、[Windows](windows-portable.md)。
- **GPU 存在但走 CPU/处理慢**：查 `model_providers`、驱动/runtime、库来源与真实 kernel profile，不凭 Provider 列表签收。见 [GPU 检查](windows-portable.md#gpu-选择)。
- **排队长/取消后仍忙**：查 `local_inference`的操作和阶段、AI 占槽、内存等待与线程收尾；不调大并发或重复提交。见 [内存与排队](memory-scheduling.md)。
- **HTTP 503**：先区分路由与错误消息，再查 AI 就绪、内存或持久存储。见 [模型目录](model-catalog.md)、[内存](memory-scheduling.md)。
- **HTTP 409**：查 revision、request_id/载荷、活动任务与多标签保存。刷新真实快照后再明确动作，不用新幂等键掩盖冲突。见 [精修接口](refinement-session-api.md)、[工程](studio-projects.md)。
- **HTTP 422/匹配失败**：查字段、人物索引、来源互斥、输入限制与逐人物 outcome，不一律归因驱动。见 [schema](../api/schemas.py)、[本地换脸](facefusion-local-swap.md)。
- **历史图片 404/410**：查记录、文件、摘要、同批 data/outputs 与外部 URL。缺图保持不可用，不用缩略图重建；区分旧生成入口 410 与丢图。
- **AI 生成/复评失败**：查配置、上游状态、超时、结构化契约及候选是否保存。保留候选后人工判断，重试另计费用，见 [生成与复评](refinement-generation.md)。
- **SQLite 锁冲突**：查重复进程、目录权限、WAL 与长写入。先停重复实例并保留副本，不删协调文件或改写状态。
- **Windows 升级后启动被拦**：查 manifest、载荷版本与 portable-update 标记，重跑同一升级包。见 [更新规则](windows-portable.md#升级修复与自检)。

Docker 先看 `ps`与 `logs --tail=100`。Windows `Check-Install.cmd`查安装完整性，不执行模型；`Check-Performance.cmd`在故障实例运行时采集内存/队列，不另加载模型。脱敏后分享报告，输出及局限见平台指南。

pytest TestClient 停滞如与 asyncio socketpair 跨线程唤醒有关，先做有时限最小复现并保留原断言。依据见 [测试环境决策](../.agents/notes/platform/20261010-testclient-requires-socketpair-wakeup.md)。环境失败与业务失败分别记录，不放宽验收。

## 运维验收与交接收尾

运维签收要求实例可定位，程序/模型/数据版本可复现，备份实际恢复通过，故障有入口，外部服务和数据保留有责任人。功能签收按 [接手清单](project-handover.md#10-接手验收清单)。

实测在 [validation](validation/)记录范围、结果与未执行项。未知项保持未知，别的机器或历史成绩不替本机签收。
