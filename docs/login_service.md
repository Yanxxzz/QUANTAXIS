# Windows 当前用户登录自启

这是可选部署方式。本机操作者已于 2026-10-18 改为[通过 Codex 对话启动](chat_start.md)，原登录及分钟触发任务已卸载；只有重新明确选择登录自启后才执行本页的 Install。

此方式使用 Windows 任务计划程序，触发条件是当前用户登录。它不等同于“开机后未登录也运行”的 Windows SCM 服务。机器休眠、关闭或未登录时不能推进研究；这里没有自动登录、唤醒或管理员提权设置。

## 部署

使用 Python 3.11 及 `requirements-panda-alpha.txt`，在将要运行任务的 Windows 账户下登录 Codex。`service-prepare` 接受已经初始化、有明确动作数、轮数和截止时间的 host session；参数示例见 README。它调用原 runtime 的准备逻辑，固定 ledger、registry、workers、session、运行目录和上下文；不新建研究授权。

`service.json` 固定解释器和仓库绝对路径、运行清单哈希及累计启动上限。任务必须由同一账户安装和操作。选择稳定的本地 checkout 和 Python 环境；不要在任务运行时切换分支、移动目录或更新解释器。配置、日志及模型材料存入私有 `research_runs/`，不加入 Git。凭据仍由 Codex 登录存储管理，安装器不会读取或复制凭据。

PowerShell 安装器提供 `Plan / Install / Start / Stop / Status / Uninstall`。Plan 展示具体操作；Install 注册当前用户的交互登录触发器、截止前每分钟的恢复触发器、Limited 权限、隐藏窗口和 IgnoreNew 单实例策略。Install 启用后台运行，当前已登录时下一分钟开始，也可 Start 立即开始。已有同名任务先核验账户 SID、命令和绑定，不覆盖无关任务。Uninstall 不删除任何 ledger、模型会话或审计记录。

这是对既有 `host-serve` 的薄封装，没有第二套任务板、预算或决策引擎。服务入口使用绝对路径，不依赖 Task Scheduler 的默认工作目录或交互式终端环境。**本登录适配器**仅接受 `execution_config=null`；官网部署使用[聊天入口](chat_start.md)显式启动和恢复，Plan/Install/Start/Run 均拒绝官网配置。A/B 来源核验和剩余算力授权的限制继续有效。

## 停止与恢复

| 场景 | 验收依据与行为 |
| --- | --- |
| 登录触发或手工 Start | 同一个 service entry point 启动四角色和协调器 |
| Stop | 持久关闭运行开关并向当前启动 ID 写入停止请求；宿主清理所属子进程，保留 paused，等待显式 Start |
| 旧停止文件留存 | 下一次启动 ID 不同，不会误停新进程 |
| 宿主或整个启动器强制终止 | 截止前下一次系统恢复触发重新拉起，接管尚存组件或从磁盘恢复 |
| 所有进程丢失后恢复 | 从相同 runtime、SQLite 待办、模型 thread ID 和尝试记录恢复；不复制或清空状态 |
| 机器重启后登录 | 登录触发同一命令；已有截止时间、模型重试、组件重启和服务启动上限继续有效 |
| complete、deadline、attention 或显式 Stop | 后续触发只检查状态，不启动模型；不清掉业务阻点或重试次数 |

`service-state.json` 保存每次服务启动的进程身份、时间、观测到的系统启动时间及结果。被强杀的运行记录在恢复时标为 interrupted。Windows 的系统启动时间估计有秒级抖动，只把超过五秒的变化记录为 `boot_time_changed`；它是审计线索，不能单独当作整机重启验收证明。

应用总启动次数默认最多 10 次，包括手工启动和系统自动恢复；完成、暂停、attention 的空检查不计入实际宿主启动。达到上限后停在 attention，不自动重置。Task Scheduler 还设置了三次失败重启，但本机实测仅此设置没有恢复被强杀的按需启动任务，因此恢复依赖有截止时间的分钟触发器。该触发器使用相同账户、命令和单实例策略，不新增模型调用权限。`host-retry` 仅在修复模型认证/额度后使用，服务仍须遵守原 deadline 和累计次数。

新研究会话需要新的有界 host session、runtime 和 deployment。先 Stop/Uninstall 旧任务，再明确绑定新的配置；旧目录及研究分母保留。代码没有“重置服务顺便清空研究历史”的入口。

## 验收范围

`tests/test_team_service.py` 使用真实子进程和合成角色协议，覆盖正常停止/恢复、服务强杀后接管、进程全部退出后的磁盘恢复、原模型会话与预算保留、过期和启动上限、带空格/非 ASCII 路径的 Windows 启动计划。原 runtime 回归继续覆盖模型超时、认证失败、重连、worker/coordinator 崩溃、PID 复用及模糊启动。

本机另以真正注册的 Windows 计划任务验收系统拉起、正常 Stop 和分钟触发恢复。实际注销/重启登录触发需要独立记录：任务导出的 XML、重启前后 Windows 启动时间、登录后 LastRunTime、服务启动记录及原 ledger/runtime 摘要。进程冷启动测试不会写作“已重启电脑”。

参考：[Microsoft 的登录类型定义](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-logontype-principaltype-element)、[OpenAI Docs 的认证说明](https://learn.chatgpt.com/docs/auth)。
