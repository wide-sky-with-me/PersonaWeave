# 人脸框快速指派弹层走 portal，与标记互为兄弟节点

状态：有效
日期：2026-10-04

## 背景

人脸框上的快速指派弹层最初是标记元素（`role="button"`）的子节点、绝对定位在画布容器里。审阅时用 Playwright 在 1440×900 和 390×844 下把人脸放在画布四角，发现：弹层高约 338px，被画布容器裁掉，靠下的脸只剩一条标题，手机宽度下选择列表基本不可见；弹层里按钮的 Enter/空格会冒泡到标记自己的按键处理器，被 `preventDefault` 并切换弹层，键盘根本选不了人；Esc 关闭后焦点落在 `body`。

## 决策

- 弹层抽成 `FaceAssignPopover`，用 `createPortal` 渲染到 `document.body`，`position: fixed`，由纯函数 `computePlacement` 按标记位置计算：优先放在下方，放不下就翻到上方，再不行钉在视口边缘并限制高度，水平方向夹紧；视口窄于 640px 时变成底部抽屉。
- 弹层在 React 树里是标记的**兄弟**而不是子节点（portal 的合成事件仍会冒泡到 React 父节点，放在子节点下问题依旧）；标记上的徽标改成纯文本，标记是唯一的交互元素，不再在 `button` 里嵌 `button`。
- 键盘与焦点：打开时焦点进入弹层（`role="dialog"`），Esc 或点外部关闭，关闭时把焦点还给标记。
- 回归：`CanvasFaceMarkersOverlay.test.tsx`（Enter/空格、焦点、外部点击、非嵌套结构）、`FaceAssignPopover.test.ts`（位置计算）、`scripts/smoke_studio_ui.py --verify`（四个角落两种宽度，弹层完整在视口内且可命中）。

## 被放弃的方案

- **继续用绝对定位，只在容器上去掉 `overflow: hidden`**：画布容器要靠裁切实现缩放和平移，去掉会破坏画布。
- **只给弹层容器加 `stopPropagation` 修键盘问题**：能修冒泡，但裁切和 `button` 嵌套的 ARIA 问题仍在。
- **引入 `@radix-ui/react-popover`**：项目只装了 dialog、slider、switch；这个需求只要“放得下、不遮挡、键盘可用”，几十行自写逻辑加测试足够，新增依赖按 AGENTS 的依赖评估不划算。
- **所有宽度都用固定浮层**：240–260px 的卡片放不进手机画布，抽屉更可用。

## 影响与验证

行为描述见 [engineering-architecture.md](../../../docs/engineering-architecture.md) 的工作台任务控制一节。徽标同样会被画布容器裁掉：它以人脸框为中心，靠边的脸会露出一半。测量徽标锚点与画布容器的矩形，只平移锚点内的徽标（锚点自身不动，保证测量的是自然位置），使其始终留在图内并保留 4px 边距；放弃的做法是按脸框位置百分比选择左/中/右对齐——CSS 不知道徽标宽度，映射了长标签的徽标仍会越界，顶部边缘也无法判断。回归：`keepInside` 的纯函数测试、jsdom 里模拟矩形的组件测试，以及 UI 审计中四个角落徽标完整在图内的断言。
