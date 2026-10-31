# 人工验证报告: remove-standalone-stop-button

**日期**: 2026-10-31
**验证人**: agent（ZCode 会话，用户委托收口轮）
**关联 delta**: openspec/changes/remove-standalone-stop-button/（PR #531，merge 3e35e9de）
**E2E 门禁**: CI stub 套件全绿（run 41052770498：stub 92 passed；message-actions 定位符迁移已纳入 timeline 套件 35 passed）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 页面 SHALL NOT 渲染页面级独立停止按钮（空闲态） | 部分（stub 无空闲断言专项） | 任何状态下无 fixed 底部独立停止按钮 | 生产栈（重建后镜像含 3e35e9de）实测 DOM：页面无独立停止按钮，仅侧边栏 + Composer；截图见同目录 `2026-10-31-remove-standalone-stop-button-idle.png` | ✅ |
| 源码层：独立按钮容器已删除 | 否（静态） | App.tsx 无旧按钮容器 | 容器 `/app/src/App.tsx` 与宿主 md5 一致（c2b80678），`grep 停止生成` 0 命中；composer-stop 两分支（quick 显式按钮 / deep ComposerPrimitive.Cancel）代码在位 | ✅ |
| 运行态停止入口唯一且可点（quick run） | 是（stub 套件 composer-stop 可见/消失语义） | 运行中仅 composer-stop，run 结束消失 | ⚠️ 实机运行态未能观察：quick run 两次均在秒级失败（见异常记录），running 状态一闪而过不可采样 | ⚠️ 环境受限 |
| 消息流式停止入口（deep run） | 是（stub 套件 message-actions 迁移用例） | 同上 | 未实机触发（深度运行 2-5 分钟 + 消耗真实额度，且同受模型 403 影响） | ⚠️ 环境受限 |

## 异常记录

1. **quick run 两次秒级失败（环境问题，与本 delta 无关）**：
   - 第一次「阿里云 DeepSeek V4 Flash」：`403 AccessDenied.Unpurchased`（会话 4eb3ae30，created 09:49:12）
   - 第二次切「DeepSeek 办公」：`Your account (2108484499) does not have a valid Agen...`
   - 两个已配置 chat 模型均被 provider 拒绝（订阅/额度），与 PR #531 前端改动无关；**建议用户尽快续费/更换 chat 模型**，否则快问入口当前不可用。
2. **IAB 点击超时**：wrapper 层 click 对该页按钮一律 3s 超时（含 force），改用页面内原生 `click()`（React handler 等效触发）完成交互；截图同样需先 `getAnimations().finish()`（已知 IAB 冻结动画时钟问题）。

## 结论

- [x] 静态/空闲态验证全部通过；运行态语义由 CI stub 套件确定性覆盖（92 passed 含迁移用例），实机运行态因两个 chat 模型均 403 无法采样，记为环境限制。
- [x] 可关闭 delta 的剩余人工验证项（本报告即 PR #531 描述中「人工验证（合并部署后补）」项的落盘）。
- [ ] tasks.md 中对应项勾选随本报告更新（见 PR 分支合并后的 delta tasks）。

## 部署备注

- 重建方式：`docker compose up -d --build`（backend 挂载态 src 直通 + frontend 镜像态重建，2026-10-31 09:40 前后完成）。
- 同批生效：PR #532 池分析标题带股票名（backend 容器 line 339 `f"{stock_name}({symbol}) {AUTO_POOL_DISPLAY_SUFFIX}"` 已验证在位；标题实际效果待下一批自动池分析（周一 11-02 16:22）产生新会话后可见，属预期时点）。
