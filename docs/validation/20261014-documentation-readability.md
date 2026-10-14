# 2026-10-14 文档可读性整理验收

## 范围与基线

分支 `refactor/organization-and-boundaries`，基线 `d6b63763fcd86ac841478a3ed3df3a7738407243`。本轮整理 40 篇既有现行/导航文档，新增 [文档总览](../README.md)、阅读入口回归及本记录；更新 Changelog。规则、旧决策和冻结实测保持原貌。

## 阅读结构

- 首页按运行方式和用户动作导流，不把细节变更堆成功能清单；首次配置命令保留已有 `.env`。
- 总览按先运行、使用恢复、部署排错、开发维护组织全部现行专题，解释参考/目标、工程/任务、会话/轮次/版本、查看/基图/接受的区别。
- 业务、平台、交接及运维正文优先中文，分开前提、动作、预期结果与失败处理；工程页按责任拆段，治理的实现矩阵按需展开。
- Wiki 源稿使用同样的目标导航；源稿修改和独立 Wiki 发布分开，不向现有 main 链接添加尚未合并的新文件。

## 事实纠正与兼容

- HyperSwap：[`model_router.py`](../../core/model_router.py) 与 [`路由回归`](../../tests/test_model_quality_routing.py) 证明，研究开关开启且满足几何/置信边界时，auto 可以优先选择可用 HyperSwap；默认未开启研究模型的行为未变。文案不能写成所有 auto 都只用 InSwapper，也不能把研究开关当作完成许可审查。
- 旧档案：质量证据随所属任务显式删除；无独立证据清理页或自动保留期限，不等于没有任务删除入口。现行说明见 [质量档案](../delivery-quality.md)。
- 工程保存：已保存是界面提示，不是新增 `saved` 代码状态；成功写入后控制器使用 `idle`。说明见 [工程](../studio-projects.md)，实现见 [`projectController.ts`](../../web/src/features/projects/projectController.ts)。

逐篇相对基线比较：40 篇既有文档的原章节锚点均仍可达；模型 SHA-256 集合保留。52 篇旧 notes/验收/live-test 文档逐字节不变。21 段 bash/sh 围栏仅经 `bash -n` 解析，未执行部署、下载、启动、同步或恢复命令。

## 本轮验证

执行时禁用 dotenv、使用本地 LiteLLM 价格表、uv 离线且不同步依赖；不借测试访问部署数据。

- `uv run --no-sync ruff format .`：276 个文件无需格式修改。
- `uv run --no-sync ruff check .`：通过。
- `uv run --no-sync pytest -q -p no:cacheprovider` 加以下九个文件：文档一致性、治理、链接、阅读总览、公开项目、Wiki、Mac 守卫安装模板、交付契约及模型质量路由，合计 **71 passed**。Mac 模板测试只生成临时 plist，不操作 launchd。
- `git diff --check`：通过。旧章节锚点、模型摘要、冻结历史和命令语法检查均通过。

本轮检查的测试文件为 `test_documentation_consistency.py`、`test_documentation_governance.py`、`test_documentation_links.py`、`test_documentation_readability.py`、`test_public_project_documentation.py`、`test_wiki_documentation.py`、`test_macos_boot_guard_installation.py`、`test_delivery_contracts.py`、`test_model_quality_routing.py`，均位于 `tests/`。

并行改稿的早期检查发现任务历史正文含治理禁词“此前”；修正后统一回归通过。只读审查另发现工程恢复的比对对象压缩成歧义，已据 [`recovery.py`](../../api/projects/recovery.py)明确为原图/结果分别对编辑器对应画布验证，不要求原图与换脸结果彼此相同。

本轮未执行全量产品回归或前端构建；生产实现和前端未变，不引用基线的通过成绩充当本轮成绩。

## 交付边界

本轮是文档与文档回归改动。没有修改生产代码、依赖、Compose、Mac 守卫或运行数据；没有读取实际 `.env`/用户图片/数据库或凭据，没有启动服务、真实模型推理、收费 AI、GPU/原生平台/画质验收、备份恢复演练或独立 Wiki 发布。历史性能与样本数字不宣称本轮重测。

改动交付在现有分支的工作区，本轮未提交或推送，未合并 main；远程结构整理提交仍为基线。
