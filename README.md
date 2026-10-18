# Panda Alpha：四角色因子研究

本项目在 [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) 上构建 PandaAI 因子研究工作流。两个研究员探索不同经济机制，独立证伪员提出竞争解释，负责人统一冻结定义、分配算力和推进任务。

**默认离线。只有明确、有限的研究与资金授权和来源核验同时满足后，才安排官网试点。** 调度器通过显式配置的角色宿主取得真实回复，没有默认模型或付费 LLM 服务。合成演练、官网报告交付、来源资格和经济有效性分别验收；不会自动改变正式因子池。

## 四个角色

- **lead：研究负责人。** 分配任务与预算，仲裁重复机制，冻结研究版本，维护共享状态，通过现有执行网关派发和恢复任务。
- **market：交易与价格发现研究员。** 探索交易行为、量价信息及价格吸收机制。
- **fundamental：经营变化与资本配置研究员。** 探索经营信息、盈利质量和资本配置机制。
- **skeptic：独立证伪员。** 先登记替代解释和失败条件，再审查结果及其证据范围。

角色配置位于 [.codex/agents/](.codex/agents/)。角色提示词用于分工；它们不构成进程、文件系统或账号权限的强隔离。

两个研究方向并行准备，官网任务统一串行执行。官网原生回测承担主要经济验证；本地负责免费兼容性检查、既有结果复用、成本归因与证据复核。协作必须形成“假设 → 挑战 → 答复 → 冻结 → 实验 → 复核 → 决定”的闭环。

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

任务板使用 SQLite；`status`、`packet`、`plan` 查看任务、角色材料与待办。真实角色通过 `host-run` 交接；也保留 `submit/message/freeze` 手动导入入口。结算后必须经过带范围和证据绑定的 `result_review`，再由 `conclude` 同步科学登记及任务结论。`pause` 停止新增派发。参数与恢复流程见[协作说明](docs/team_workflow.md)。

## 持续角色调度

宿主提供四个实际角色，使用文件邮箱或显式本地命令适配器。任务板只有一个协调写入者；研究员和证伪员提交原始回复。先复制并填写[有限会话模板](config/team-host.example.json)的截止时间，复用既有实验账本和科学登记：

```powershell
python -m panda_alpha.team host-init --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --session review-01 --input host-session.json
python -m panda_alpha.team host-run --ledger research_runs/experiments.sqlite3 --registry research_runs/research_trials.sqlite3 --session review-01 --mailbox research_runs/role-mailbox --output research_runs/host-review --watch
```

角色宿主读取 `role-mailbox/<role>/requests/<work_id>.json`，独立产出 `{work_id, output}`，原子写入同角色的 `responses/`。没有工作者时明确等待，不生成假回复。命令适配器用 `--commands FILE` 替换 `--mailbox`，配置四角色各自的 argv，JSON 从 stdin 输入、stdout 输出。后端需按 `work_id` 去重。

会话持久化轮数、动作数、截止时间和待办。重启复用相同 session 和数据库；校验失败的角色回复保留原件，携带具体错误重新交给该角色。既有 Run 的恢复先于新派发，模糊创建响应停在对账入口。会话限额不替代累计研究配额，也不会激活或扩大 grant。上面的命令未启用执行；官网执行仍需显式 `--execute --config FILE` 和有效授权。

`reopen-evidence` 允许同任务补充真实新增证据并重新审查，保留 request hash、Run、账单和策略分母；`evidence` 从缓存生成新快照，保留原 manifest。来源缺口只在明确解决后清除，经济失败进入后续提案约束，人工退役不由补证解除。完整契约见[补证与科学记忆](docs/team_workflow.md#同任务补证与科学记忆)。

## 当前来源状态

已有 A/B 小型官网试点的缓存报告可用于免费复核。A 的局部暖机对照和报告日期解释已有支持，但原生时钟、共同支持集及 worker 绑定尚未完全确认；B 的字段映射、报告期及 PIT 仍待核验。报告交付通过不授予这些来源资格。本阶段不消耗剩余 46 点，授权及余额以原账本为准。

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
