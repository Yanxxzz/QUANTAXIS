# Panda Alpha 操作约定

本项目的维护核心是 `panda_alpha/team/`，复用既有账本、预算和科学登记。角色分工及验证入口见 README，维护边界见 `docs/architecture.md`。

## 对话操作

- 研究默认由操作者在 Codex 对话中明确启动。打开聊天、查询状态、登录或重启电脑不代表启动授权；不要自行安装登录任务或定时自动化。
- 启动、暂停或查询前阅读 `docs/chat_start.md`。使用 `service-prepare` 和 `host-control` 复用原宿主，不另建调度、模型会话或预算体系。
- 先读取原账本的 `readiness` 和部署状态。可恢复会话沿用原 session/runtime/截止时间与累计次数；完成或过期的会话不能原地重置。
- 来源审查已结束且没有新材料时，等待来源；`review_completed` 不表示来源通过。保留 A/B 的待核结论，不重复空审查。
- 聊天控制默认离线，官网执行需要来源核验及独立、有效的资金授权。任何启动或恢复都不扩大授权、不购买数据或清零历史用量。
- 停止进程组使用 `host-control --action stop` 并核对实际进程；任务板 `pause` 只暂停新派发。

## 维护与交付

改动核心状态或恢复行为后运行相关回归，交付前运行 `python -B scripts/verify_public_research.py` 和 `python -m pytest tests -q`。合成角色测试不能写成真实模型或官网验证。

私有运行配置、账本、客服原件、原始模型事件和账户数据留在忽略提交的运行目录。更新 README 和受影响文档，保留原始研究证据。
