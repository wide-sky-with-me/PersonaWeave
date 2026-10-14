# HTTP 入口仍须校验工程图片并生成强随机请求 ID

状态：有效
日期：2026-10-14

## 背景

Mac 容器网页经 Tailscale HTTP IP 入口访问。实际 Chromium 的 `isSecureContext=false`，`crypto.subtle` 与 `crypto.randomUUID` 均缺失；真实上传工程保存成功，刷新却在图片摘要校验处失败。该环境保留 `crypto.getRandomValues`。API 冒烟与浏览器初始渲染都没有覆盖这一工程恢复分支。

## 决策

由成熟零依赖 `@noble/hashes` 提供纯 JavaScript SHA-256，对下载的原始字节继续强制校验摘要；不依赖它的 Web Crypto 入口。请求 ID 优先原生 UUID，HTTP 中用 `getRandomValues` 填充 16 字节并设置 RFC 4122 v4 的 version/variant 位。缺少强随机接口时明确失败，不降低熵。

健康接口的 AI 就绪字段进入前端类型与控制流。未确认就绪时禁用 AI 接续；服务失去就绪后清除选中状态，恢复就绪不自动重新启用，保留显式付费选择边界。

## 被放弃的方案

跳过摘要或吞掉错误会接受损坏工程资产，违反现有恢复契约。仅要求用户更换 HTTPS 入口没有修复用户正在使用的实际部署。手写 SHA-256 增加密码算法维护负担；依赖的纯 SHA-256 子模块比引入完整兼容框架更直接。UUID 不使用 `Math.random`。

## 影响与验证

实现：[browserCrypto.ts](../../../web/src/utils/browserCrypto.ts)、[projectSnapshot.ts](../../../web/src/features/projects/projectSnapshot.ts)、[useSessionWorkbench.ts](../../../web/src/features/refinement/hooks/useSessionWorkbench.ts)、[useBatchPipeline.ts](../../../web/src/hooks/useBatchPipeline.ts)。

回归：[browserCrypto.test.ts](../../../web/src/utils/browserCrypto.test.ts)、[largeProjectSnapshot.test.ts](../../../web/src/features/projects/largeProjectSnapshot.test.ts)、[ParameterPanel.test.tsx](../../../web/src/components/studio/ParameterPanel.test.tsx)、[BatchControls.test.tsx](../../../web/src/components/studio/BatchControls.test.tsx)。浏览器兼容性不代表 AI 上游已配置或付费流程已验收；原图编码、保存 revision 与原子恢复语义保持不变。
