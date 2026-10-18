# Panda Alpha：四角色因子研究

本项目在 [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) 上构建 PandaAI 因子研究工作流。两个研究员探索不同经济机制，独立证伪员提出竞争解释，负责人统一冻结定义、分配算力和推进任务。

**默认不执行官网回测。只有明确、有限的研究与资金授权和来源核验同时满足后，才安排官网试点。** `host-serve` 自动启动四个真实 Codex 角色及统一协调器，使用操作者已登录的模型账户和额度。合成演练、真实模型协作、官网报告交付、来源资格和经济有效性分别验收；不会自动改变正式因子池。

## 四个角色

- **lead：研究负责人。** 分配任务与预算，仲裁重复机制，冻结研究版本，维护共享状态，通过现有执行网关派发和恢复任务。
- **market：交易与价格发现研究员。** 探索交易行为、量价信息及价格吸收机制。
- **fundamental：经营变化与资本配置研究员。** 探索经营信息、盈利质量和资本配置机制。
- **skeptic：独立证伪员。** 先登记替代解释和失败条件，再审查结果及其证据范围。

角色配置位于 [.codex/agents/](.codex/agents/)。无人值守宿主为四角色分别保留模型会话，禁用 shell、插件、应用、浏览器及子 agent 工具，并显式使用只读沙箱。角色只返回 JSON；协调器拥有任务板及官网执行入口。

两个研究方向分别准备，当前调度按依赖顺序交接，官网任务统一串行执行。官网原生回测承担主要经济验证；本地负责免费兼容性检查、既有结果复用、成本归因与证据复核。协作必须形成“假设 → 挑战 → 答复 → 冻结 → 实验 → 复核 → 决定”的闭环。

## 离线运行

推荐 Python 3.11。研究层不要求部署上游交易、Web 或 Rust 服务。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-panda-alpha.txt
.\.venv\Scripts\python.exe -m panda_alpha.team demo --output research_runs/team_demo
.\.venv\Scripts\python.exe -m panda_alpha.team host-demo --output research_runs/team_host_demo
```

`demo` 演练两条方向的基本闭环。`host-demo` 连续推进三轮，覆盖跨方向控制、同任务补证再审、科学记忆回流，以及“回复已保存但尚未落板”和“科学登记已提交但任务尚未关闭”两处中断恢复。两者均拦截网络和子进程，使用明确标记的脚本角色及合成执行器；通过只说明管线可运行。输出目录须尚不存在。

命令帮助：

```powershell
.\.venv\Scripts\python.exe -m panda_alpha.team --help
```

任务板使用 SQLite；`status`、`packet`、`plan` 查看任务、角色材料与待办。真实角色通过 `host-serve` 自动运行；`host-run` 保留外部宿主接入，`submit/message/freeze` 保留手动导入。结算后必须经过带范围和证据绑定的 `result_review`，再由 `conclude` 同步科学登记及任务结论。`pause` 停止新增派发。

## 持续角色调度

先安装并登录 Codex CLI，确认 `codex login status` 成功。复制[工作者配置](config/team-workers.example.json)，填写本机 `codex_command`；可显式选择 `model` 和 `reasoning_effort`，省略时在首次启动固定当前 Codex 配置中的选择。模型调用会使用该账户额度，官网算力另行核算。

再填写[有限会话模板](config/team-host.example.json)的目标和截止时间，复用既有实验账本和科学登记：

```powershell
python -m panda_alpha.team host-init --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --session review-01 --input host-session.json
python -m panda_alpha.team host-serve --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --session review-01 --workers workers.local.json --output research_runs/host-review
```

一条命令启动四角色进程和一个协调器；每个角色使用自己的持久 Codex 会话。按 `work_id` 保存请求、模型事件、原始回复与尝试记录。角色读取工作包、显式 `context_files` 及受控来源接收的正文；路径本身不表示已读取原件。新材料在工作边界进入新请求，正在处理的请求保持原快照。

模型超时会终止本次进程；暂时断线按原 work_id 和会话有限重连。工作者或协调器崩溃会自动重启；宿主被终止后恢复同一运行目录可接管尚存子进程并恢复原待办。重试次数、轮数、动作数、截止时间都不会重置。

登录或模型额度问题会停在 `attention`。修复后可用 `host-retry --output research_runs/host-review --reason "说明已修复的问题"` 记入审计，再重跑原命令；仅使用剩余重试次数。启动身份不明、额度耗尽或来源阻点均不会盲目重派。详细状态与恢复方法见[无人值守宿主](docs/unattended_host.md)。

### Windows 登录自启

Windows 任务计划程序可在**当前用户登录后**隐藏启动宿主，并在原会话截止前每分钟检查恢复；正在运行时忽略重复触发。未登录时不运行。使用当前账户和普通权限，不保存 Windows 密码、不切换到 SYSTEM，不拷贝 Codex 登录凭据。重启电脑后须先登录该账户。

```powershell
python -m panda_alpha.team service-prepare --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --session review-01 --workers workers.local.json --runtime research_runs/host-review --output research_runs/service-review --name PandaAlpha-Research
.\scripts\team-service.ps1 -Action Plan -Config research_runs/service-review/service.json
.\scripts\team-service.ps1 -Action Install -Config research_runs/service-review/service.json
.\scripts\team-service.ps1 -Action Start -Config research_runs/service-review/service.json
```

准备和 Plan 不启动模型。**Install 会启用后台运行**：当前已登录时下一分钟开始，也可用 Start 立即开始。服务接入目前限定为离线研究与来源审查，不能启用官网执行器。`Stop` 正常停止全部子进程，并保持暂停直到显式 Start；`Status` 查看系统任务及业务停止原因，`Uninstall` 移除启动项并保留研究历史。会话完成、过期或需要处理时不会由系统触发自动放宽；实际宿主启动次数也持久累计（默认最多 10 次）。部署路径、账户和验收细节见[登录自启服务](docs/login_service.md)。

校验失败的角色回复保留原件，携带具体错误重新交给该角色。既有 Run 的恢复先于新派发，模糊创建响应停在对账入口。会话限额不替代累计研究配额，也不会激活或扩大 grant。上面的命令未启用执行；官网执行仍需显式 `--execute --config FILE` 和有效授权。

`reopen-evidence` 允许同任务补充真实新增证据并重新审查，保留 request hash、Run、账单和策略分母；`evidence` 从缓存生成新快照，保留原 manifest。来源缺口只在明确解决后清除，经济失败进入后续提案约束，人工退役不由补证解除。完整契约见[补证与科学记忆](docs/team_workflow.md#同任务补证与科学记忆)。

### 免费补证与就绪检查

`source-capture` 只读抓取一个指定的公开官方指南；`source-submit` 固化明确提交的正文并排队；活跃的有限 Host 对 `seed_tasks` 中的原任务自动补证再审。可在会话中配置至多四个 `source_watches`，各检查一次，未变化跳过，失败留下阻点。登录后动态数据和客服回函仍需明确提供。旧 Run 的报告验收摘要由缓存补建，保留原结果。

```powershell
python -m panda_alpha.team readiness --ledger research_runs/experiments.sqlite3
```

这一只读命令汇总来源阻点、证据队列、原预算和宿主边界，不授予执行权限。接收格式、自动检查配置及完整可用的剩余条件见[来源补证](docs/source_intake.md)。

## 当前来源状态

已有 A/B 小型官网试点的缓存报告可用于免费复核。登录后的免费文档确认财务字段区分净额/合计，并提供季度和公告日期参数；尚未证明旧回测裸字段映射及历史 PIT。A 的原生时钟、共同支持集及 worker 绑定也仍待核验，详见[本次来源核验](docs/source_review_20261018.md)。本阶段不消耗剩余 46 点，授权及余额以原账本为准。

## 维护入口

- [协作流程与正式试点边界](docs/team_workflow.md)：角色交互、请求协议、预算分配及恢复。
- [架构与维护边界](docs/architecture.md)：维护核心、研究组件、历史运行脚本和上游保留层。
- [三轮协作与恢复验收](docs/team_lifecycle_validation.md)：脚本回归、真实角色交接、来源状态及验证范围。
- [官网研究与预算](docs/official_compute.md)：累计配额、资金授权、预留、结算和阶段复盘。
- [正式入池规则](docs/pool_admission.md)：经济验证、成本、稳定性及实际组合增量。
- [已有研究与操作记录](RESEARCH.md)、[公共研究工具](docs/panda_alpha_workflow.md)、[数据渠道](docs/data_sources.md)、[证据存储](docs/research_storage.md)。

共享任务板与既有实验账本并存。团队预算不创建虚拟子账户余额，也不重置已有研究轮消费；资金范围授权必须经现有预算检查派生精确实验许可。实际扣费与预留共同占用额度，模糊派发只恢复原 Run。研究额度不是收费授权。

需要增加原研究轮总额时，使用 `amend-budget` 读取快照、预览并按明确授权追加审计记录；保留全部旧作业与用量，且不激活 grant。操作与请求格式见[额度扩展](docs/official_compute.md#同研究轮额度扩展)。

每次官网创建和启动前重新核验授权的有效期与暂停状态；授权过期或被替换就停止新的远端动作。已有 Run 仍可查询和结算，不能因恢复请求而重复创建或启动。一次有限试点的额度、截止时间和资金来源保存在对应授权中，不成为公共默认值。

历史原件、协议、结果和失败原因保留。研究准备、执行成功与正式入池是不同状态；来源缺口不能登记为经济证伪。

## 验证与上游

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_public_research.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

公开源码检查与合成测试不替代真实来源和因子质量验收。上游说明保存在 [README_UPSTREAM.md](README_UPSTREAM.md)，版权与许可见 [LICENSE](LICENSE)。上游源码、研究组件及历史脚本的路径保持可追溯。
