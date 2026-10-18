# 四角色无人值守宿主

`host-serve` 复用 Host、文件邮箱、Coordinator、ExperimentLedger、ResearchRegistry 和 ExistingExecutor。新增部分只负责进程、模型会话和交付重试，不复制预算或研究判定逻辑。

当前 Host 按证据依赖顺序交接，一个时刻只有一个待回答动作；四个常驻角色进程不表示同时发送四次模型请求。不同方向保留独立上下文，通过任务板交换挑战、回应及结论。

## 运行与权限

安装 `requirements-panda-alpha.txt`，在操作者账户登录 Codex CLI，初始化有明确截止时间的 host session。命令见 README。当前接入验证使用 Codex CLI `0.163.0-alpha.10.2`，需要 `exec` / `exec resume`、JSON 事件、结构化输出、`--ignore-user-config` 和工具开关；不兼容的 CLI 会保留失败现场，不降级到放宽权限。

配置必须显式给出可信 `codex_command` argv，不经过 shell。首次启动只继承用户模型及推理强度，忽略用户插件、MCP 和其他默认配置，显式指定只读沙箱、禁止审批式执行并关闭模型工具。认证继续由 Codex 本机登录管理，不拷贝 token。四个模型会话分别绑定 lead、market、fundamental、skeptic；它们不是四个独立账号，也不保证统计独立性。

角色只能分析传入工作包。`context_files` 是操作者明确提供的 UTF-8 文本，单文件最多 256 KB，总计 512 KB；内容与哈希在首次启动固化。只传适合发送给所选模型服务的材料。工作包中的引用路径不等于已读原件；缺少内容时必须保留 pending。[来源接收入口](source_intake.md)将后来提交的明确文本放入新请求，并支持每会话至多四个指定公开文档的只读检查；角色没有自由联网或登录态数据权限。

不加 `--execute --config` 时不创建官网执行器。启用后仍由现有网关核验原 grant、冻结定义、来源资格、累计用量和资金来源。角色回复不能开启执行、修改授权、重置分母或自行创建 Run。

## 运行文件

| 文件 | 用途 |
| --- | --- |
| `manifest.json` | 绑定原 ledger、registry、session、限额、模型配置和证据快照 |
| `supervisor.json` / `control.json` | 组件启动历史、运行结果、宿主心跳与停止信号 |
| `components/<name>/` | 协调器或角色的进程身份、租约、退出结果和私有日志 |
| `workers/<role>/session.json` | 该角色唯一的 Codex thread ID |
| `workers/<role>/work/<work_id>/` | 请求哈希、累计尝试、原始模型事件和结构化答案 |
| `mailbox/<role>/` | 复用原请求/回复协议；只有协调器提交业务效果 |
| `host-status.json` | 当前业务待办或停止原因 |
| `operator-retries/` | 修复模型登录/额度后的人工重试审计 |

运行目录应放在忽略提交的 `research_runs/` 中；原始模型回复、工作包和账户相关日志不提交到公共仓库。文件使用原子替换并处理 Windows 共享句柄冲突。进程租约由操作系统释放，PID 连同创建时间校验，避免对复用 PID 的其他进程发信号。

## 故障与恢复

| 情况 | 行为 |
| --- | --- |
| 正常启动 | 自动启动四角色及协调器；无任务的角色等待 |
| 网络断开、输出不完整 | 原 work_id 有限指数退避；已获 thread ID 时显式 resume，不使用 `--last` |
| 单次模型超时 | 终止对应进程树，在原重试上限内恢复 |
| 工作者崩溃 | 自动重启，接管已有模型进程和输出；先恢复原尝试再考虑重试 |
| 协调器崩溃 | 自动重启；复用 SQLite 待办、已记录回复和既有 Run |
| 宿主中断 | 在聊天中明确继续，沿原部署恢复并接管仍存活组件。超过心跳宽限，子进程自行停下 |
| Ctrl+C / 服务 Stop / 截止时间 | 停止派发，终止本宿主拥有的子进程；保留全部待办和账本 |
| 登录或模型额度失败 | 停在 attention；修复后 `host-retry --output DIR --reason TEXT`，再重跑原命令 |
| 启动记录缺少 PID 回执 | 停在 attention，先人工核对实际进程；不承诺外部模型恰好调用一次 |
| 来源或资金不满足 | 沿用原执行网关阻点，不购买数据或扩大授权 |

`max_attempts` 按 work_id 持久累计，`max_component_restarts` 按组件累计，均跨进程重启保留。人工重试仅适用于已修复的模型认证/额度问题，不能放宽原期限或耗尽的尝试预算，不能解除来源及官网派发阻点。CLI 内部可能重连同一次请求；应用层日志区分这种行为与新一次尝试。

同一运行目录使用 supervisor 租约，同一 ledger 使用服务准入及协调写入租约。不要同时运行旧 `host-run` 或手工修改待办；团队 `pause` 只暂停新派发，停止整个进程组应使用 `host-control --action stop`。此处为单机监管与恢复，不是分布式主节点切换。默认通过[聊天入口](chat_start.md)显式恢复原部署；只有操作者重新选择并安装[Windows 登录服务](login_service.md)后，系统触发才参与恢复。

参考：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[结构化输出示例](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex)。接口以部署机器的 `codex exec --help` / `codex exec resume --help` 实测为准。
