# 架构与维护边界

入口是 `python -m panda_alpha.team`。它在既有研究内核外增加四角色协作，复用官网执行、预算、科学登记及回测核算。当前具备有限持续调度、缓存报告验收、补证再审和科学记忆回流；既有 A/B 小型官网试点的来源资格仍待核，不因此扩大执行范围。

## 维护核心

维护核心负责跨研究方向一致的约束与状态，修改时应覆盖实际边界行为和恢复路径。

- `panda_alpha/team/contracts.py`：角色、研究请求、消息、冻结定义与授权契约。
- `panda_alpha/team/board.py`：SQLite 任务板、协作记录与团队授权状态。
- `panda_alpha/team/budget.py`：在真实研究轮和资金约束下形成资源计划；不维护四份模拟账户余额。
- `panda_alpha/team/coordinator.py`：协调任务、检查冻结版本，并连接既有执行和恢复入口。
- `panda_alpha/team/host.py`：有轮数、动作数和截止时间的调度、持久化交接，以及文件邮箱/显式命令适配器。
- `panda_alpha/team/runtime.py`、`codex_worker.py`、`runtime_io.py`：四角色进程与协调器监管、受限 Codex 会话、有限重连及跨进程恢复。
- `panda_alpha/team/evidence.py`：缓存报告结构验收与带范围的独立审查，区分交付、来源和经济结论。
- `panda_alpha/team/conclusions.py`：持久化结论意图、幂等科学事实和跨库恢复，保持原试验身份。
- `panda_alpha/team/demo.py`、`panda_alpha/team/rehearsal.py`：基本演练与三轮中断恢复验收；脚本角色只用于测试。
- `panda_alpha/team/__main__.py`：薄命令入口。
- `.codex/agents/panda-{lead,market,fundamental,skeptic}.toml`：四角色职责、交接规则与工作边界。
- `panda_alpha/platform.py`、`panda_alpha/research_budget.py`：实验指纹、原子预留、官网状态转换、共享研究轮配额及结算。
- `panda_alpha/registry.py`、`panda_alpha/memory.py`、`panda_alpha/evolution.py`：唯一经济实验、历史约束、来源与执行修复、反思及机制重开。
- `panda_alpha/study.py`、`panda_alpha/execution.py`、`panda_alpha/attribution.py`、`panda_alpha/portfolio.py`、`panda_alpha/admission.py`：已有公共核算、归因、组合与准入接口。

团队层只增加协作和资源决策。原生兼容性由 `native_preflight.py` 等既有接口检查；行情及财报来源由 `data.py`、`sources.py` 和相关来源契约负责。不要在角色提示词或团队预算模块内复制这些计算和判定规则。

角色提示词不是安全沙箱。`host-serve` 通过 Codex 只读沙箱和工具开关限制角色，只向模型传递工作包及显式证据文本；仅协调器拥有执行入口。外部 `host-run` 适配器的隔离仍由接入者落实。本机各进程使用同一操作系统账号；这不是跨账号、容器或分布式安全隔离。

## 状态各有职责

1. **团队任务板**保存负责人、挑战、冻结版本、资源请求、授权和有限宿主会话。`team_host_work` 保存原始工作包及回复，`team_finalizations` 保存跨库结论断点。
2. **ExperimentLedger**保存真实实验指纹、预算预留、Factor/Run ID、结算及执行断点。团队层复用它，不创建另一套平台余额。
3. **ResearchRegistry**保存唯一经济定义及来源、执行、经济判断等科学事实。两档费用或软件修复不增加新的策略分母。
4. **原件与结果文件**保存可复核证据；记录引用路径及 SHA256。热记忆和角色工作包是可再生成的阅读视图。

同一个事实不由多份可写 JSON 独立裁决。lead 通过团队协调入口维护共享状态，研究员写各自候选及附件，skeptic 提交挑战与审查证据。既有 `ResearchEngine.save_state` 不支持多个进程合并写入同一状态文件，不能让四个 agent 各自运行同一路径的 `plan/evolve`。

预算决策与执行约束分开：lead 选择有研究价值的实验，确定性代码检查真实授权、余额与预留、累计配额、冻结指纹和复盘节点。授权范围 grant 只能在已允许范围内派生精确实验 permit；它不充值、不伪造账户余额，也不扩大既有研究轮上限。

## 可复用研究组件

`panda_alpha/` 中的具体机制、财务科目和策略模块属于研究组件，例如年度盈利、营运资本、经营现金、融资行为和涨停事件研究。它们不是每次团队协作都必须加载的公共内核。

组件进入维护范围的依据是当前任务确实依赖、接口清晰且有相应证据或测试。新研究先判断已有组件能否复用，再增加必要接口；不要把每次参数试验、单次修复或一个候选都包装成常驻框架。

经营信息研究应先核对官网原生字段、期间和时点；有界来源诊断后再决定本地补源范围。市场行为研究也必须明确行情、复权、交易日历和成交假设。任一组件的来源验收不能扩大成完整全 A、完整 PIT 或生产可用认证。

## 历史运行脚本与研究证据

`scripts/` 同时包含仍维护的公共入口、数据操作、验证工具和历史研究运行器。`scripts/Invoke-PandaResearch.ps1`、`scripts/verify_public_research.py` 等公共入口继续保留；具体研究脚本以其绑定协议和既有证据范围理解，不默认升级为新的团队入口。

`research_runs/` 保存私有或部分公开的冻结协议、运行脚本、输入凭证、账本、结果、修复版本及断点。`docs/` 内带日期的研究报告、`docs/results/` 和 `research_bootstrap/` 也是历史证据及冷启动材料。

本次把 10 个研究、恢复和汇总脚本的重复 I/O 收敛到 `scripts/research_io.py`；保留旧入口、JSON 格式和历史输出语义。原 CLI 与团队入口共用 `ResearchRegistry.reconciled_memory` 的记忆对齐检查，不再各写一份。旧脚本保留用于复现和恢复，不能因新入口存在就重新执行其收费调用。新旧证据关系通过替代记录和哈希绑定说明，不能原位改写历史结果后沿用旧凭证。

存储释放、原始文件保留及脱敏规则继续采用 [research_storage.md](research_storage.md)。公开仓库不会自动包含用户行情库、账号配置和全部私有原件；合成演练也不依赖这些资料。

## 上游保留层

`QUANTAXIS/`、`qapro-rs/`、`qabook/`、`doc/`、`examples/` 等上游内容保持原路径。研究层可独立使用，新增协作功能不要求部署整套上游服务，也不调整上游许可与发布职责。

[README_UPSTREAM.md](../README_UPSTREAM.md) 和 [LICENSE](../LICENSE) 保留上游说明、版权与许可。架构整理不是重写上游框架，也不是清理历史研究材料的授权。

## 当前交付与后续维护

维护验证包括三轮断网演练、同身份补证、原生形状报告负例、跨方向异议处置、科学记忆约束，以及真实子进程的超时、有限重连、进程中断与接管。`host-serve` 自动创建四个实际模型会话，文件邮箱仍是可复用传输层。单机进程租约避免同一服务重复写入，尚未实现分布式主节点切换；进程验收不保证模型结论正确。

跨库采用先持久化意图、再幂等写科学事实、最后关闭任务的恢复协议。外部角色请求可能重复送达，后端应按 `work_id` 去重；只承诺已记录回复和状态效果的幂等，不承诺外部模型调用恰好一次。改动这些边界时重放相应中断测试。

继续来源核验时保留原实验账本与研究轮累计，不通过新任务板、日期、方向或资金批次清零历史。真实经济研究仍需获证的输入、明确资金范围、有效 grant 和独立结果审查。具体流程见 [team_workflow.md](team_workflow.md)。
