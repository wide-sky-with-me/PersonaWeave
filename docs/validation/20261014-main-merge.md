# 2026-10-14 结构与文档分支合并验收

## 范围与基线

用户要求全量校验通过后，将 `refactor/organization-and-boundaries` 合并到 `main` 并推送。合并前远程 main 为 `66b587bf402353c58d9b0f46b9090357da3733f8`，结构整理分支为 `d6b63763fcd86ac841478a3ed3df3a7738407243`，另有文档可读性工作区改动。远程 main 是该分支祖先，无并行上游改动。

本记录属于合并阶段，不改写 [结构分支验收](20261014-organization-branch.md)或 [文档阶段验收](20261014-documentation-readability.md)；后者“未提交/未全量回归”描述的是文档整理阶段，不是此次用户授权后的交付状态。

## 隔离与复现

后端从 `git ls-files --cached --others --exclude-standard` 复制源码到临时目录，共享现有 `.venv` 但不同步依赖。仅挂入六个经下载器 SHA-256 验证的 ONNX 模型；禁用 dotenv、使用本地 LiteLLM 价格表和明确的 `*.test` AI 连接，数据库与输出只在临时副本产生。结束后删除临时目录，逐文件摘要确认原工作区未被检查改写。Linux 本地工具为 Python 3.11.11、uv 0.12.23、Node 24.21.0、pnpm 11.7.0；远程 CI 的 Node 22/pnpm 10 兼容性另由对应 Actions 核对。

前端复制 `web/` 源码和现有依赖到独立临时目录，设置 `CI=true`；格式化、TypeScript、Vitest 和生产构建均在那里执行。原源码按 SHA-256 核对，构建产物和依赖副本自动清理。

## 检查结果

- 前端 `pnpm --dir web format`、`format:check`、`lint`、`test`、`build`：全部通过，44 个测试文件、246 项测试；生产构建成功。
- 首轮后端全量：966 通过、1 失败。`test_strength_contract.py` 的默认值契约要求短句 **its default is `0.9`**；中文改写遗漏此短句。补回文档绑定及回归链接，未修改断言或默认值。
- 针对强度、文档一致性/治理和导航复测：27 项通过。
- 后端 `uv run --no-sync ruff format .`、`ruff format --check .`、`ruff check .`：全部通过，未改格式；`uv run --no-sync pytest -q`：**967 passed in 40.01s**。
- `uv run --no-sync python scripts/download_models.py`：六个必需模型摘要有效，未发起下载。
- `uv build --wheel`、`uv pip install --no-deps --target <临时目录>`：成功；隔离导入七个包/模块，确认来自安装目标；`personaweave --help` 与 `personaweave-import-portraits --help`：通过。
- 两套检查均确认原工作区文件 SHA-256 不变，临时副本已清理。验收正文补齐后再检查文档、强度与 Git 差异；不为纯记录补全重复推理与前端构建。

Vitest 的 jsdom canvas 提示和模拟 HTTP 失败日志来自测试环境，不是测试失败；本轮不把这些组件测试当作真实浏览器渲染验收。

## 合并与推送核对

仅在检查全部通过后提交文档改动，更新原分支，并以保留分支历史的方式合并 main。合并前重新 fetch；合并后的树须与已验证分支一致。推送为正常快进，不强制覆盖；远程 HEAD 与本地 SHA、工作区状态和对应 Actions 结果分别核对。

本记录保留合并前的实际检查。最终合并 SHA 由 Git 历史确定，远程 CI 由 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)按对应提交核对，避免在记录内自引用包含自身的提交哈希。

## 明确边界

本轮不修改、重建、重启或升级 Mac 部署，不执行 SSH、Colima、launchd 或实际 Docker 部署。分支相对 main 的 Compose、Dockerfile、容器入口和 Mac 网络/启动脚本没有改动。独立 Wiki 保持 `37fed96b5a78fbcc35e0e8dcb7027e48216f04d2`，只合并仓库里的 Wiki 源稿。

不读取真实 `.env`、个人照片、业务库或凭据；不调用收费 AI、不宣称 GPU/原生 Windows/Mac/画质或业务恢复验收。夹具 ONNX 集成、安装包导入、组件测试和生产构建各只证明自己的范围。
