# 公开项目说明

PersonaWeave 是可自行部署的批量定点换脸工作台，面向明确源人物参考、稳定目标映射和人工结果检查的流程。它将本地换脸与可选整图 AI 精修分开；功能摘要和启动命令在 [README](../README.md)，领域事实通过 [Wiki 导航](../wiki/Home.md)查阅。

## 仓库提供什么

| 内容 | 入口与边界 |
| --- | --- |
| 源码与锁文件 | [后端清单](../pyproject.toml)、[前端清单](../web/package.json)；源码开发锁面向 Linux/CUDA |
| CPU/ARM64 与默认 CUDA 容器 | [Docker 部署](docker-deployment.md)；镜像须自行构建，不把源码仓库当成镜像仓库 |
| Windows 使用与构建说明 | [Windows 指南](windows-portable.md)；包内含运行时/模型，源码 clone 不等于完整便携包 |
| 可公开的最小样例 | [四张 API 夹具](../tests/fixtures/README.md)；用于回归，不是完整人物素材包 |
| 配套文档与有限范围实测 | [项目交接](project-handover.md)、[公开核对记录](validation/20261014-public-repository.md)；一次样本通过不保证任意照片或网络条件 |

[GitHub Releases](https://github.com/wide-sky-with-me/PersonaWeave/releases)是公开发行附件的核验入口：只下载实际发布的文件并校验对应摘要。源码树不含权重、64 人物素材库、用户照片/数据库/结果或 AI 凭据；实例内存在某个包、镜像或素材不代表它已在 Releases 发布。素材准备见 [素材库](portrait-library.md)，模型由 [下载器与资产说明](../models/README.md)分别管理。

## 先运行本地流程

选择 [README](../README.md#本地启动)中的源码入口或 [CPU 容器](docker-deployment.md#镜像选择)入口，准备模型与独立持久目录。仅验证本地处理时不要配置 AI 服务；跨图视觉仲裁和后置精修的费用边界见 [维护手册](operations-runbook.md#ai-配置与调用费用)。

最小样例可使用 [source-01.png](../tests/fixtures/source-01.png)作为人物参考、[group-01.png](../tests/fixtures/group-01.png)作为目标图：上传参考并检查检测结果，上传目标后明确选择人物编号，执行一次本地换脸，目视检查后下载。多人映射由使用者明确配置，不凭样例文件名猜身份对应。该步骤是入门方法，不是本次新执行的画质验收。

Mac/ARM64 CPU、Windows CPU/GPU 与 Linux/CUDA 使用不同 runtime 准备路径。CPU 容器不使用 Apple Metal/CoreML；未知硬件的最低容量和吞吐没有统一保证，运行诊断见 [内存与排队](memory-scheduling.md)。

## AI 与数据边界

AI 是可选功能，连接与模型示例不是已验证的服务清单。视觉仲裁、诊断、生成和复评都可能产生费用，失败/取消不保证撤销上游请求；重试由人决定。生成只接收当前整图和文字，参考仅用于诊断和复评，接受结果由人明确确认，见 [精修契约](ai-refinement.md)与 [网关配置](model-catalog.md)。

公开仓库不提供公共在线处理服务。应用没有完整登录和多租户隔离，生产入口需控制网络和访问边界，见 [维护手册](operations-runbook.md#访问与进程边界)。讨论问题请使用可公开夹具和脱敏日志；漏洞或数据暴露走 [私密报告](../SECURITY.md)。

## 源码与资产许可

**源码许可尚未指定。** 仓库没有顶层 LICENSE；公开可见性不是 MIT、Apache-2.0 或商用授权，包作者字段也不能代替许可证。维护者选择源码许可证后须同步许可证文件、元数据及本节；本次公开设置不擅自作这一选择。

模型、依赖、测试图片和个人素材各自有来源及条款，不能从项目源码许可推定它们的分发/商业使用范围。[模型说明](../models/README.md)记录模型来源、摘要及研究/非商用等限制，[夹具说明](../tests/fixtures/README.md)约束测试素材来源。生成素材也不自动变成项目源码的一部分，使用和分发前按对应来源核对。

## 参与与支持

程序问题和功能建议使用 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)表单；修正文档或代码前阅读 [贡献说明](../CONTRIBUTING.md)。CI 查看对应提交的 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)，不是静态通过徽章。Wiki 源文件在仓库直接可读，独立 Wiki 页的初始化/同步边界见 [发布说明](wiki-publication.md)。许可选择、资产准备和历史保留的取舍见 [决策笔记](../.agents/notes/platform/20261014-public-project-and-ci-assets.md)。
