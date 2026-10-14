# Mac ARM64 CPU 远程生产流程验收

日期：2026-10-14；实例：`http://100.88.228.28:8000`。证据目录为 `outputs/deployment/20261014-production/`，仅测试仓库许可样本与本次创建的工程、任务，没有执行付费 AI 请求。

## 发布与修复

最终原生 ARM64 镜像 `personaweave:cpu-20261014-production-r3`，摘要 `sha256:86c6482e516e0c74706c9552f4f11595f3277ce3c604c6237853101dcfa54078`，运行别名 `personaweave:cpu`。从保留的 CPU 镜像增加前端源码及产物，不改变后端、模型、持久挂载或推理并发。六模型实际 Provider 均为 `CPUExecutionProvider`。

修复 HTTP 浏览器缺少 `crypto.subtle` 导致工程图片校验中断；保留完整 SHA-256 字节校验，UUID 使用强随机来源。AI 未配置时关闭接续入口，并清除已选择的接续状态。原因及回归见 [前端决策](../../.agents/notes/frontend/20261014-verify-project-assets-without-secure-context.md)。

最终主包 `index-DPHVf6v0.js` SHA-256 为 `4315ce013fc7e29639ee301b1695ceb4e1bf0c92c7371022005642702eaf9091`，CSS 为 `index-wDKP3RVJ.css`；生产 HTTP 获取八个文件逐一验证摘要通过，见 `frontend-mobile-dist-sha256.json`、`final-r3-full-project.json`。手机工具栏修复后重新执行 format、lint、完整前端回归（43 文件、244 测试）和构建，均通过；导出专项复核 18 测试通过。本次未声称执行 Node 22/pnpm 10 完整 Docker 冷构建。

## 素材与真实流程

素材库与冻结发布逐文件、稳定 ID、数据库摘要一致：64 身份、1088 资产、1024 可选参考。当前容器内校验 1088 原图内容摘要及尺寸、2176 原图和缩略图解码全部通过，见 `decode-report.json`。真实素材弹窗 64 身份、81 张显示图片解码通过，见 `library-ui.json`。

手工参考：通过生产网页创建人物、上传 `group-01.png`、检测五脸、明确绑定人物 #1、单图换脸、保存与下载。上传至映射完成 7.838 秒，单图按钮至结果可用 3.388 秒；这些包含网络与 UI，不是纯模型耗时。见 `manual-single.json`、`manual-result-quality.json`。

默认素材：网页选入 F45 八张参考，实际提交八个托管资产 ID，正文 925 字符；响应证实 reference_count=8、人物 #1 换入、五脸检测。UI 到结果 8.235 秒，服务端 cost_ms=2685.43，见 `library-swap-ui.json`。

两条单图的 JPEG 下载均为 1067×1600，已解码及目视核对；下载文件与各自服务端 sync 产物逐字节相同，见 `same-byte-download.json`。目视未见整图错位或明显接缝，不将像素差异或遮罩权重冒充身份或自然度评分。

批次 `pw_task_0efc476441` 通过网页上传两张合影和白色无脸 PNG，配置六个稳定人物绑定；请求 AI=false、正文 5919 字符。终态 PARTIAL，三张处理完成、两张有结果、无脸图失败；实际换入分别三脸、五脸。极端侧脸缺对应方向参考、未产生可见变化、目标身份缺席均保留逐人物诊断，不冒充全成功。服务端开始至完成约 15.77 秒。

ZIP 包含目录及两张成功结果，没有失败图替代产物。Pillow 验证两者均为 PNG、1067×1600、RGB，完整解码与 ZIP CRC 通过；命名与编码一致。ZIP SHA-256 `f73099aaeb99c77ae7c4de813a36c4e4b5a56782ff0816af0928b9a9f636ddeb`。真实历史查看器进入第三图，显示无脸诊断和无成功结果；见 `post-restart-ui.json` 及 `batch-history-ui.png`。

## 持久化与性能

R2 镜像通过现有 Compose 重建，并显式 `docker restart`。工程 revision=16 与批次详情重启前后严格相等，两张服务端 JPEG 可读取；restart policy=unless-stopped，见 `restart-before.json`、`restart-after.json`。全新浏览器从历史按钮恢复完整画布，实际绘制保护笔、撤销、重做与保存成功，见 `restart-ui-final.json`、`restart-restored-ui.png`、`brush-saved-ui.png`。R3 重建后完整多素材工程恢复成功，实测 65.271 秒，无失败响应或页面异常，见 `final-r3-full-project.json`；素材和结果图片全部校验后才应用，不能将该大工程称为秒开。

原始上传工程资产与 3065512 字节 PNG 严格相等；批次冻结原图虽重编码 PNG，解码像素逐点相同，见 `final-remote-detection.json`。五人脸检测容器内 0.131/0.113/0.107 秒，远程托管原图 0.872/0.831/0.823 秒。详见 `final-native-performance.json`，上传与计算取舍见 [识别性能记录](20261014-macos-face-performance.md)。Tailscale 仍使用 DERP 中继，不宣称已直连或保证大图传输速率。

## 测试过程与边界

自动化遇到定位错误：源/目标文件 input 同名、模态提示隐藏背景进度条、ZIP 目录计入条目、历史详情加载、图片内部 ID、工程及手机按钮 aria-label。另一次大工程在 120 秒等待内仍处于图片校验，增加观察预算后实测恢复完成，保留超时证据。依据真实 DOM 修正定位，不改产品断言。手机发现真正的工具栏覆盖模式按钮、控件挤压换行；R3 下移手机工具栏，禁止子控件收缩、允许横向滚动，桌面位置不变。未缩图的单张合影手机复测：实际绘制、六次模式点击、两个抽屉、保存和无横向溢出通过，见 `mobile-toolbar-final.json`、`mobile-toolbar-final.png`。

一次浏览器恢复出现 502，后端日志未见对应恢复请求；同实例 POST 恢复复测 200，其余恢复与资源请求正常。证据不足以归因到某个代理，记录为传输异常，不归因到模型计算，不声称网络零故障。

付费 AI 未配置、未进行生成或人工接受验收。侧脸参考不足是可见的输入条件限制。本记录不是任意照片、任意网络条件、Mac 无用户登录冷启动的保证。本次三项测试工程按各自最新 revision 删除，测试批次经 API 删除并验证 404；两条已验证 sync JPEG 按精确文件名在 `/app/outputs/api_results/sync/` 清理。未删除默认素材、模型和用户数据；完整证据、下载及 ZIP 在本地证据目录保留，见 `cleanup-final.json`。测试资源清理后，记录中的测试工程与结果 URL 返回缺失是预期行为。

## 回滚

保留 `personaweave:cpu-before-20261014` 与首次前端修复镜像，不删除原模型、数据或输出目录。回滚时将保留镜像重新标记 `personaweave:cpu`，用相同 Compose 文件执行 `up --no-build -d`；源码备份位于远端 `production-releases/20261014-http-restore/previous-web-source.tar.gz`。本次未实际切回旧镜像；回滚点存在不等于已验证回滚。
