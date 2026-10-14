# 公开项目说明

先选择部署方式，准备模型和独立持久目录，再用公开样例完成一次本地换脸。预期得到可检查、可下载的本地结果；身份与自然度仍由人核对。启动或处理失败时，按 [维护手册](operations-runbook.md#故障排查)定位，不反复提交或直接开启 AI。

PersonaWeave 是可自行部署的批量定点换脸工作台。本地换脸与可选整图 AI 精修分开。功能摘要和启动命令见 [README](../README.md)，领域事实见 [Wiki 导航](../wiki/Home.md)。

## 仓库提供什么

- **源码与锁文件**：[后端清单](../pyproject.toml)、[前端清单](../web/package.json)。源码开发锁面向 Linux/CUDA。
- **容器构建说明**：[Docker 部署](docker-deployment.md)提供 CPU/ARM64 与默认 CUDA 路径。镜像须自行构建，源码仓库不是镜像仓库。
- **Windows 使用与构建说明**：[Windows 指南](windows-portable.md)。完整便携包含运行时和模型，源码 clone 不等于便携包。
- **公开最小样例**：[四张 API 夹具](../tests/fixtures/README.md)用于回归，不是完整人物素材包。
- **交接与实测证据**：[项目交接](project-handover.md)、[公开核对记录](validation/20261014-public-repository.md)。单次样本通过不保证任意照片或网络条件。

获取发行附件时，以 [GitHub Releases](https://github.com/wide-sky-with-me/PersonaWeave/releases)中的实际文件及对应摘要为准。实例内有包、镜像或素材，不代表它已在 Releases 发布。

源码树不含权重、64 人物素材库、用户照片、数据库、结果或 AI 凭据。模型按 [资产说明](../models/README.md)准备，人物素材按 [素材库](portrait-library.md)准备，二者独立管理。

## 先运行本地流程

**前提**：选择 [README 源码入口](../README.md#本地启动)或 [CPU 容器入口](docker-deployment.md#镜像选择)，准备模型和独立持久目录。仅验证本地处理时不要配置 AI 服务，费用边界见 [维护手册](operations-runbook.md#ai-配置与调用费用)。

1. 用 [source-01.png](../tests/fixtures/source-01.png)作人物参考，上传后检查检测结果。
2. 用 [group-01.png](../tests/fixtures/group-01.png)作目标图，明确选择目标人物编号。
3. 执行一次本地换脸，目视核对身份、遮挡和自然度，再下载结果。

**结果与失败**：应得到可检查、可下载的图片。检测或人物对应不明确时，先核对输入和编号，不凭文件名猜身份。处理失败按 [维护手册](operations-runbook.md#故障排查)查看具体诊断。这是入门方法，不是画质验收记录。

Mac/ARM64 CPU、Windows CPU/GPU 与 Linux/CUDA 各用对应 runtime 准备路径。CPU 容器不使用 Apple Metal/CoreML。未知硬件没有统一最低容量或吞吐保证，诊断见 [内存与排队](memory-scheduling.md)。

## AI 与数据边界

启用 AI 前，单独确认服务、费用和输入范围。连接与模型示例不是已验证服务清单。视觉仲裁、诊断、生成和复评均可能收费；失败或取消不保证撤销上游请求，重试由人决定。

生成只接收当前整图和文字，参考仅用于诊断与复评，结果须由人明确接受。详见 [精修契约](ai-refinement.md)与 [网关配置](model-catalog.md)。多图准备的视觉仲裁与后置精修是不同调用，关闭精修不代表全流程零远端调用。

公开仓库不提供公共在线处理服务。应用没有完整登录和多租户隔离，部署方须控制网络与访问范围，见 [访问与进程边界](operations-runbook.md#访问与进程边界)。问题报告只用可公开夹具和脱敏日志；漏洞或数据暴露走 [私密报告](../SECURITY.md)。

## 源码与资产许可

**源码许可尚未指定。** 仓库没有顶层 LICENSE。公开可见性不等于 MIT、Apache-2.0 或商用授权，包作者字段也不是许可证。许可由维护者明确选择，并同步许可证文件、元数据和本节。

模型、依赖、测试图片与个人素材各有来源及条款。源码许可不决定它们的分发或商业使用范围。使用与分发前核对 [模型来源、摘要及限制](../models/README.md)和 [夹具来源要求](../tests/fixtures/README.md)。生成素材也不自动成为项目源码的一部分。

## 参与与支持

程序问题与功能建议使用 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)表单；修改前阅读 [贡献说明](../CONTRIBUTING.md)。检查某个提交的 CI，以对应 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)结果为准，不把静态徽章当作通过证据。

Wiki 源文件可在仓库直接阅读。独立页面的状态与同步要求见 [发布说明](wiki-publication.md)。许可选择、资产准备和历史保留的取舍见 [决策笔记](../.agents/notes/platform/20261014-public-project-and-ci-assets.md)。
