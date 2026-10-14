# PersonaWeave 运行维护手册

本文负责实例登记、资产移交、停机备份、恢复、更新与排障。产品及代码地图见 [项目交接说明](project-handover.md)，平台安装步骤以 [README](../README.md)、[Docker 部署](docker-deployment.md)、[Windows 便携版](windows-portable.md)为准。以下命令均以安装/仓库根目录为工作目录；命令示例没有读取真实凭据或业务数据的执行声明。

## 实例登记与启动检查

每个实例建立内部登记记录，保存实际值而非只写“最新版本”。登记记录由移交方与接任方共同补齐，不把秘密加入仓库或公开文档。

| 登记项 | 移交内容 |
| --- | --- |
| 责任归属 | 移交人、接任人、维护联系人、备份保管人、AI 服务账号与费用责任人 |
| 代码与交付 | Git commit、工作区补丁、锁文件；镜像 ID 或便携 manifest/ZIP SHA-256 |
| 环境 | OS/CPU 架构、Python/Node/pnpm、运行方式、驱动与实际模型 Provider |
| 入口 | 启动/停止方式、工作目录、端口/访问地址、CORS、外部认证或访问隔离 |
| 数据 | 实际 models/data/outputs 根目录、额外素材库路径、容量及写权限 |
| 配置 | 生效 AI YAML 路径、环境变量名称、视觉/图像服务归属；秘密单独移交 |
| 保留与恢复 | 备份位置、周期、保存期限、恢复时间/数据损失目标、最近演练结果 |
| 验收 | 目标样本、模型摘要、检查命令、运行与画质结论、未执行项 |

源码版本与工作区检查：

```bash
git rev-parse HEAD
git status --short
uv --version
node --version
pnpm --version
```

依赖恢复沿用 [Python 清单](../pyproject.toml)/[锁文件](../uv.lock)和 [前端清单](../web/package.json)/[锁文件](../web/pnpm-lock.yaml)。启动脚本不安装依赖或下载模型；容器入口只在必需模型缺失时触发下载，文件非空并不证明权重完整。下载来源、摘要与模型许可统一查 [模型说明](../models/README.md)和 [下载器](../scripts/download_models.py)。

启动后访问生产页面和 `/docs`，检查运行中的健康接口：

```bash
curl -fsS http://127.0.0.1:8000/health
```

源码/容器默认 API 端口为 8000；改过端口或绑定到其他接口时用实际地址，Windows 使用启动窗口显示的地址。核对 `pipeline_ready`、`model_providers`和 `local_inference`；Provider 列表或 `gpu_available`不能单独证明 CUDA kernel 执行。`ai_refinement_ready`只表示本地配置/初始化就绪，不证明远端鉴权、额度、实际价格或图像编辑 endpoint 可用。实现见 [system.py](../api/routes/system.py)，真实 GPU 核验见 [Windows 指南](windows-portable.md#gpu-选择)。

## 访问与进程边界

[应用装配](../api/app.py)配置 CORS，但没有应用登录隔离；CORS 不是身份认证或 HTTP 访问控制。[源码开发器](../scripts/dev.py)与 [Compose](../docker-compose.yml)默认启动/发布全接口，Windows [便携入口](../api/portable.py)使用回环地址。Docker 仅本机使用时可配置 `PERSONAWEAVE_BIND_ADDRESS=127.0.0.1`；内部访问使用明确的接口和网络策略，外部访问控制在入口层落实。

[文件路由](../api/routes/files.py)的 `/api/v1/data/{subpath}`返回 `data/`根目录内现存文件，仅限制目录穿越，没有“仅四张样例”的白名单或登录校验；`/api/v1/files/{subpath}`同样开放 `outputs/api_results/`内现存文件。其他领域的 ID/摘要检查也不等于用户权限隔离。将服务访问权按可读取业务图片和数据库的能力对待，不能根据路由注释把它理解为公开样例站点。

生产共享流水线只允许 `PERSONAWEAVE_INFERENCE_CONCURRENCY=1`，其他值启动校验失败；精修会话恢复没有跨进程 worker 租约。单进程运行一个实例，避免增加 uvicorn workers 或让两个服务共享同一组持久目录。依据见 [共享推理槽位](../api/inference_limiter.py)与 [精修存储契约](refinement-session-api.md)。

## AI 配置与调用费用

只从 [.env.example](../.env.example)核对变量名称；已有 `.env`保持原样。视觉与图像服务独立配置，实际秘密仅存部署环境，网关 YAML 使用环境引用。重启后端后重新核对就绪状态；尺寸、fallback、默认零重试和 endpoint 支持查 [模型目录](model-catalog.md)，不在操作手册复制配置字段表。

**关闭批任务 AI 精修不等于整个准备流程没有远端调用。** 多目标图片自动走跨图聚类；孤立簇与不完整簇满足条件时可进入 VLM 仲裁。生产入口见 [useIdentityClustering](../web/src/hooks/useIdentityClustering.ts)、[faces 路由](../api/routes/faces.py)、[arbiter](../core/ai/arbiter.py)。VLM 建议仍经 ArcFace 复核，视觉仲裁与后置整图精修是不同调用。

仅验证本地能力时，使用无有效 AI 连接/凭据的隔离副本，或使用明确本地检测/换脸入口，关闭后置精修；不要只依据 `ai_refinement=false`声称零 AI 调用。付费验证单独记录实际 endpoint、请求次数和结果；取消已发出的上游请求不保证免计费，重试由人明确决定。

## 持久数据与资产清单

下表是**备份对象清单**，不是第二套业务状态机。默认路径相对运行工作目录；若设置 `PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT`或自定义存储/配置路径，把实际目录列入同一批备份。

| 资产 | 默认位置 | 移交与恢复要点 | 事实来源 |
| --- | --- | --- | --- |
| 批任务及旧精修档案 | `data/task/tasks.sqlite3` | 元数据/请求与结果文件成套保留 | [persistence.py](../api/tasks/persistence.py)、[legacy.py](../api/refinement/legacy.py) |
| 编辑工程 | `data/projects.sqlite3` | 工程文档、关联与工程拥有的媒体副本一起保留 | [ProjectStore](../api/projects/store.py) |
| 精修会话 | `data/refinement_sessions.sqlite3` | 轮次、版本、事件、接受指针及私有证据成套保留 | [SessionStore](../api/refinement/store.py) |
| 素材库 | `data/portrait_library/` | `library.sqlite3`、originals、thumbnails、quarantine 与归档身份完整保留，不替换 ID | [素材库](portrait-library.md) |
| 批结果、单图结果、工程媒体、接受结果 | `outputs/api_results/` | 保留原画布、执行结果、工程独立副本与正式发布图片 | [任务历史](task-history.md)、[工程契约](studio-projects.md) |
| 精修私有证据与候选 | `outputs/refinement_staging/sessions/` | 原图、基线、参考、所有版本；只留 accepted 图片不足以恢复对话 | [精修存储](refinement-session-api.md) |
| 旧档案的评价证据 | `outputs/refinement_evidence/` | 按旧记录归属保留，不解释为新会话事实源 | [质量档案](delivery-quality.md) |
| 权重与配置 | `models/`、`config/`、独立环境配置 | 模型保留实际摘要，未托管可选权重另登记；凭据独立受控保存 | [模型说明](../models/README.md)、[模型目录](model-catalog.md) |
| 便携交付程序 | runtime、api/core/utils、web/dist、manifest 等 | 使用完整匹配版本，具体目录和清单以平台指南为准 | [Windows 指南](windows-portable.md) |

服务可按需生成目录；空库或缺失素材不是完整素材包。源码 Git clone、wheel、容器镜像不包含所有权重、素材库和运行结果。空实例素材安装/追加导入见 [素材库的新实例与迁移](portrait-library.md#新实例与迁移)。

浏览器 localStorage/sessionStorage 只保存轻量关联/选择，不是服务端数据库替代品；跨浏览器从工程列表显式选择工程。图片缺失或摘要损坏不能用缩略图或猜测路径补成“恢复成功”。工程与 session 独立副本的保证范围见 [工程契约](studio-projects.md)。

## 停机与一致性备份

备份前停止新操作，等待保存完成及活动本地/AI 任务收尾，再停止服务。强行停止可能使正在执行的任务进入中断状态；恢复逻辑不代表上游请求被取消或免费。

- 源码开发：启动终端 `Ctrl+C`，确认 API 与 Vite 子进程结束。
- Docker：沿用启动时的 Compose 文件组合，执行 `stop personaweave`，确认容器已停止。
- Windows：启动窗口 `Ctrl+C`，等待原生线程和进程退出；不要边运行边复制目录。

CPU 容器停机示例：

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml stop personaweave
docker compose -f docker-compose.yml -f docker-compose.cpu.yml ps -a
```

默认 Compose 是宿主目录 bind mount，停机后从宿主机备份。SQLite 与图片跨多个存储对象，没有跨库/文件的在线全局事务；停机整套复制是这里的恢复基线。数据库的 `-wal`、`-shm`或 journal 若存在，应随目录一起保留，不手动删除它们，也不只拷贝某一个 `.sqlite3`。

下面是 Linux 默认目录的一致性归档示例。先在终端设置 `BACKUP_DIR`为仓库外的绝对路径；路径不存在则创建，归档名重复则停止。使用同一时点的 `data/outputs/config/models`，外置素材/配置另纳入清单；有符号链接时确认指向的实际数据是否也已备份，默认 tar 不追随链接。

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

归档含用户图片、请求和对话，按内部数据权限加密保管，不作为公共交付物；`.env`不进入上述归档，秘密用独立受控渠道交接。Windows 使用停机完整目录复制并保留 manifest/摘要；备份任务的存储、频率、保留期和恢复目标由实例负责人落实。

历史验收中引用的 `start.sh`、`smoke.py`、`restart-vm.sh`以及 `outputs/deployment/`原始报告是实例/诊断资产，不是 Git clone 自带文件；需要复核同次实验时须单独移交它们及校验和。新实例启动采用 [README](../README.md)和 [Docker 部署](docker-deployment.md)中的托管命令；登录守卫的模板与生成步骤见 [安装指南](macos-boot-guard.md)。没有取得原始证据时只引用历史记录的范围，不声称已重放同一实验。

备份完成后核对命令退出码、归档可读性、摘要和文件清单；再用原启动方式恢复服务。仅生成归档不算恢复演练，实际演练按下一节执行。

## 恢复与迁移

1. 核对备份摘要、源码/镜像/便携 manifest 版本、模型摘要与外置路径；保留故障实例，不在唯一原件上试恢复。
2. 在独立目录或隔离部署副本解包。使用与备份匹配的程序和数据，重建依赖或复制整套匹配 runtime；实际环境配置由受控渠道重新设置。
3. 在副本中检查 SQLite 完整性、图片/模型清单与写权限。SQLite 完整不证明引用图片完整，二者分别验证。
4. 启动副本，读取历史和工程、打开原图/本地结果/候选/接受版，下载并核对摘要；刷新与重启后再验证。
5. 演练通过后停止目标实例，保留其完整回退副本，再切换完整数据集；不能把新安装的空 `data/`覆盖旧实例。

下面的 Linux 解包命令只接受尚不存在的恢复目录；`BACKUP_FILE`为已核验的本次内部归档，`RESTORE_DIR`为独立绝对路径。归档来自可信备份，解包后不要直接让生产服务使用它。

```bash
set -eu
: "${BACKUP_FILE:?请指定已核验的备份文件}"
: "${RESTORE_DIR:?请指定独立恢复目录}"
test -f "$BACKUP_FILE"
test ! -e "$RESTORE_DIR"
mkdir -p "$RESTORE_DIR"
tar -xzf "$BACKUP_FILE" -C "$RESTORE_DIR"
```

在源码环境检查恢复副本的 SQLite，`RESTORE_DIR`需 export 给子进程；命令只对副本做只读完整性查询，不打开应用或触发表迁移。

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

外置素材库不在这个默认枚举中，按登记清单单独检查。服务启动可能执行建表、兼容迁移和中断恢复，因此首次启动也应在副本上进行；跨版本迁移/回退须验证 schema 兼容，不混配新数据库与旧程序。

原有外部图片 URL 可能失效，既有记录未保存的媒体和草稿也不会因复制数据库重新出现。恢复不会自动补发付费轮次；有候选的中断会话与没有候选的中断任务按 [精修契约](ai-refinement.md)、[任务历史](task-history.md)解释。演练须验证实际访问和媒体字节，不能只看数据库 `integrity_check`为 ok。

## 更新与回滚

更新前记录代码/交付标识，完成同批数据备份并核对修改范围；保留未提交补丁，避免把修改过的实例误认为干净发行版。读取 [CHANGELOG](../CHANGELOG.md)与当前专题的迁移说明，尤其是配置、数据和接受语义。

| 形态 | 更新方式 | 回滚边界 |
| --- | --- | --- |
| 源码 | 停服务；同步锁定依赖，更新匹配配置/资产，构建生产前端，再按原入口启动 | 保留匹配代码、锁文件、前端产物与数据快照；不要只回退一份 Python 文件 |
| Docker | 用原 Compose 组合重新构建，同一组持久挂载；记录实际 image ID，`latest`不是不可变版本 | 保留原镜像与同版本数据；避免测试容器并行写生产目录 |
| Windows | 按已交付 manifest 使用完整包、源码修复包或 GPU 差分包；运行锁/摘要校验先通过 | 停机恢复完整匹配副本；版本不匹配时停止更新，不强行覆盖 |

生产前端由 `web/dist/`提供；更改源码后需重新构建相应产物，启动旧 bundle 不会自动变成新页面。Windows 修复/GPU 升级中断时保留 `data/portable-update.json`，重跑同一载荷，不手动删标记；机制和删除边界见 [Windows 指南](windows-portable.md#升级修复与自检)。

源码没有通用的数据库 downgrade 承诺。回滚到旧程序前确认其能读对应快照，优先恢复程序与数据的匹配备份；业务新增结果如需保留，先另存完整事故副本，不直接把两个数据库合并。

## 故障排查

先记录实际版本、操作入口、task/session/project ID、状态/时间和脱敏错误；按健康信息、领域详情、部署日志逐层定位，不上传图片、凭据或原始上游响应。只读诊断与带费用的重试分开。

| 症状 | 先检查 | 处理原则/依据 |
| --- | --- | --- |
| 启动失败或页面打不开 | 根目录、端口、模型文件、依赖导入、web/dist、目录写权限、容器状态 | [启动器](../scripts/dev.py)、[Docker](docker-deployment.md)、[Windows](windows-portable.md) |
| GPU 存在但处理慢/走 CPU | 各模型 `model_providers`、驱动/runtime、运行库来源和真实 kernel profile | 不以 wheel Provider 列表宣称 GPU 成功；[Windows GPU 检查](windows-portable.md#gpu-选择) |
| 排队长或取消后仍忙 | `local_inference`的操作/阶段，是否 AI 轮次占槽、等待内存或原生线程收尾 | 不提高并发或重复提交；[内存与排队](memory-scheduling.md) |
| HTTP 503 | 具体路由；AI 配置就绪、内存余量、持久存储失败 | AI 配置错误与工程内存不足不同；[模型目录](model-catalog.md)、[内存](memory-scheduling.md) |
| HTTP 409 | revision、request_id 与载荷、活动任务、多标签页保存 | 刷新实际快照再明确动作，不用新幂等键掩盖冲突；[精修接口](refinement-session-api.md)、[工程](studio-projects.md) |
| HTTP 422 或人物匹配失败 | 字段校验、人物索引、来源互斥、输入限制、逐人物 outcome | 按具体路由和消息定位，不一律归因驱动；[schema](../api/schemas.py)、[本地换脸](facefusion-local-swap.md) |
| 历史图片 404/410 | 记录是否保留、文件与摘要、同批 data/outputs、旧外部 URL | 缺图保持不可用，不用缩略图重建结果；旧生成入口 410 与丢图需区分 |
| AI 生成失败/复评失败 | 本地路由配置、上游状态、超时、结构化契约、候选是否已保存 | 候选保留后人工判断；重试另有费用，见 [生成与复评](refinement-generation.md) |
| SQLite 锁冲突 | 是否多个进程共享目录、存储权限、WAL 协调、长写入 | 先停重复实例并保留副本，不删锁协调文件或改写状态 |
| Windows 升级后启动被拦 | manifest/载荷版本和 portable-update 标记 | 重跑同一升级包，见 [更新规则](windows-portable.md#升级修复与自检) |

Docker 查看 `ps`和 `logs --tail=100`；Windows 的 `Check-Install.cmd`检查安装完整性，不执行模型，`Check-Performance.cmd`在故障实例运行时采集内存与队列，不加载第二套模型。错误报告先脱敏再分享，具体工具、输出和局限见平台指南。

pytest 的 FastAPI TestClient 停滞与 asyncio socketpair 跨线程唤醒限制有关时，先用有时限最小复现确认执行环境，保留原断言；依据见 [测试环境决策](../.agents/notes/platform/20261010-testclient-requires-socketpair-wakeup.md)。测试执行环境失败与业务失败分别记录，不放宽验收标准。

## 运维验收与交接收尾

[项目交接验收清单](project-handover.md#10-接手验收清单)涵盖功能；本手册的运维完成条件是：实例可定位、程序/模型/数据版本可复现、备份与恢复已演练、失败有排查入口、外部服务与数据保留有明确责任人。每次实测在 [validation](validation/)留下范围与证据，未知项保持未知，不引用别的机器历史成绩替本机签收。
