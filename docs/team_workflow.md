# 四角色协作工作流

工作流采用 1 个负责人、2 个方向不同的研究员和 1 个独立证伪员，默认以 offline 模式准备任务。官网原生回测是主要经济验证场；本地用于免费查错、复用结果和深入复核。

**明确有界授权是官网执行的前提。** 未授权时停在可审查的准备材料；授权后可在规定的来源、定义、预算、次数和有效期内自主推进。工作流不调用付费 LLM，也不自动操作正式因子池。合成演练只验证协议，官网运行成功也不等于因子经济有效。

## 角色与写入边界

- **lead**：维护共享任务板；确定负责人；仲裁机制重叠；冻结版本、方向、窗口和必要对照；按信息价值分配资源；调用统一派发与恢复入口。
- **market**：探索交易与价格发现，如交易行为、量价信息、拥挤及价格吸收。提供经济预测、可执行候选、字段要求和证伪问题。
- **fundamental**：探索经营变化与资本配置，如盈利质量、现金与应计、资产负债结构及资本配置。核对官网可用字段，区分字段存在、期间正确、来源时点和经济有效性。
- **skeptic**：在看到新结果前提出竞争解释、对照及失败条件；在结果后独立审查来源、执行、成本、稳定性、集中度和组合增量。

角色配置见 `.codex/agents/panda-{lead,market,fundamental,skeptic}.toml`。角色是协作分工，不是强安全隔离；agent 共享文件系统和工具能力。约定由 lead 写共享状态，其他角色提交各自附件和消息。平台权限仍由宿主环境及现有执行入口约束。

任务板使用同一 SQLite 保存协作记录与团队 grant；真实平台状态继续使用既有 `ExperimentLedger`，科学试验事实继续使用 `ResearchRegistry`。不要让各角色直接修改公共 JSON、各自维护账户余额或重复写入经济结论。

## 一轮协作如何推进

1. **读取工作包。** 从任务板、当前热记忆和相关 evidence ID 取得必要材料。保留人工退休、封存窗口及已暴露样本约束，不重读整个历史目录。
2. **分别提出机制。** 两研究员各自说明经济预测、实际新增信息、官网能解决的未知事项、可执行字段、必要对照及通过/失败后的下一步。
3. **发出具体挑战。** 另一研究员指出重叠或竞争解释；skeptic 在结果前登记最有力的反证。每条挑战明确针对哪条主张，以及什么证据能解决它。
4. **答复并冻结。** 作者提交证据、修改研究问题或撤回。lead 只冻结一个明确版本，并绑定方向、代码或公式、窗口、输入范围、对照和证伪计划。修改冻结内容须新建版本，旧证据不冒认为新版本结果。
5. **准备官网请求。** 免费检查语法、字段、索引和原生接口兼容性。选择原生初筛、来源诊断或关键确认，说明此次费用能减少哪项不确定性。
6. **统一分配与执行。** lead 形成资源计划；真实试点获准后，经既有预算与授权检查串行派发。研究员在等待期间继续准备其他问题，skeptic 复核已有证据。
7. **结果审查与决定。** 先确认运行和结算，再检验冻结问题。决定继续、转向、修复、待补或关闭，并写明适用范围和下一问题。

“已阅读”“建议继续”不构成一次有效交接。消息必须推进具体问题、改变实验设计或解决证据分歧。每轮通常完成一次挑战与一次答复；仅在事实或设计发生变化时继续讨论，避免重复互审。

这些步骤由真实角色分别产出材料：skeptic 提出 challenge，作者通过 response 答复，skeptic 再 review，lead 才 freeze。结果结算后，skeptic 引用原结果 manifest 和证据快照做 result_review，lead 最后 conclude。`host-run` 持续传递工作包、导入原始回复和推进状态；实际角色进程由宿主接入。`plan` 仍是待办视图，合成演练的脚本角色不能冒充实际研究交接。

## 两条方向如何合作

lead 从经济预测、核心新增信息、信号构造和持仓差异判断重叠。共享收盘价、行业或市场控制不等于重复；因子名称、参数或代码不同也不能证明独立。

同一机制只设一个主要负责人，另一研究员转向不同信息。工作包公开另一方向的机制、主张、字段、可用时点、风险和对照；共享风险触发另一研究员的 `cross_review`，绑定双方任务与 request hash。lead 冻结前逐项给出 `cross_line_decisions`（event_id、accept/reject、reason）；未解决就 park。两人可审查经营信号是否只是短期反转暴露，或交易信号是否依赖公告时点。合作必须产生明确问题与对照，不能直接拼接两个分数就称为新机制。

研究员在第一次冻结前不传播事后筛选的赢家指标。skeptic 先写竞争解释和失败条件，再看新结果；独立角色不保证统计独立或免除共同模型偏见。

## 请求、消息与证据

使用 CLI 对应的结构化 JSON 输入，字段和校验以 `panda_alpha/team/contracts.py` 与各子命令帮助为准。研究请求需要表达：

- 谁提出、属于哪个任务和稳定经济机制，当前冻结版本是什么。
- 此次要验证的问题、通过和失败分别如何改变下一步。
- 具体公式或代码、方向、数据与窗口范围、字段要求、必要对照。
- 所需官网任务、预计费用及依据、已完成的免费检查和本地证据。

消息应指向具体任务和版本，并在挑战与答复之间保留关联。证据使用实际路径及 SHA256；关键结论绑定对应输入、冻结定义和结果。原始材料、程序执行成功、来源资格和经济有效性是不同证据层级。

`packet` 产生角色工作包，便于限制本轮阅读材料；它不是权限隔离或防泄露机制。已暴露窗口必须随工作包传递，不能在后续报告中重新称为独立样本外。

## 算力分配与确定性约束

lead 优先考虑能改变研究决定、机制新颖、来源可执行且费用合理的实验。不给四个角色建立模拟资金账户，也不要求独立算力 agent 为每次调用聊天审批。

团队 grant 表达获准的资金和研究范围，不能自行产生收费权限。只有范围、有效期、剩余额度及冻结定义均合格时，协调入口才可派生精确指纹 permit，继续接受现有平台预算检查。研究范围授权不扩大累计研究轮上限，不绕过原充值规则、人工暂停或退役约束。

确定性执行继续复用 `platform.py` 和 `research_budget.py`：

- 真实账户余额、已结算实扣和未结算预留共同约束新派发。
- 单一研究轮、机制族、初探次数和阶段复盘继续累计，换日期、任务名或资金批次不清零。
- 精确指纹绑定已冻结定义；已存在的实验查询原 Factor/Run。
- 原子预留后再派发；模糊响应不自动重发。
- 估算预留不是平台单次扣费硬上限，异常结算保持停派。

默认 60 点/20 次研究上限、每 20 点复盘、单机制初探 2 次/全程 4 次详见 [official_compute.md](official_compute.md) 和 [research-workflow.v1.json](../config/research-workflow.v1.json)。这些是研究配额，不单独授予消费许可。需要扩展原研究轮时，按明确授权使用 `amend-budget` 读取原账本快照、预览并追加审计记录，保留同一 study 的历史作业、消费、预留和复盘；不能通过换 study、重建空账本或修改配置自行扩额。额度修订与 grant 激活是两个独立步骤。

资金不足时，选择排队、复用完全相同口径的既有对照、缩小为有信息价值的来源诊断或暂停该线。可以继续免费准备工作；不能降低预留以挤进额度，也不能把本地代理提升为官网结论。资金范围授权存在时，批内按已允许范围自主推进；超范围的新定义或资金需求另行形成可审查方案。

## 失败、恢复与停止

来源不足记为待补，不登记为经济失败；同时限制来源调查的时间和免费查询范围，避免长期补源挤占研究。免费最小复现证明技术故障后，才进行有明确变更的定向修复。相同故障重复发生时关闭该执行路径，保留证据并转向其他可执行任务。

经济失败保留冻结定义及上下文，不据结果反号、挑组或扫描相邻参数。来源修复和执行重试不是新策略；有真实新增信息时按既有重开规则记录。人工退役只能由明确人工指令重开。

官网轮询超时不等于平台任务取消。恢复先查询原 Run、保存结果、核对账单，再决定任务状态；存在未知扣费、未解决的派发响应或输入哈希变化时停止新派发。`pause` 停止新的派发安排，不能声称取消了已在官网运行的作业。

团队任务在每次官网 `create` 和 `start` 前都重新核验当前 grant 的有效期、暂停状态及绑定身份。预留后过期、暂停或更换授权，会保留原 `RESERVED`／`CREATED` 状态并停止远端动作；不能因预留曾通过就在截止后继续启动。已有 Run 的查询与结算恢复仍可在授权过期后进行，不重复创建或启动。

达到预算或次数上限、出现未授权资金需求、需要越过封存窗口或变更用户范围时，lead 保存断点和具体方案后停下。正式入池仍是独立审查；探索不套用正式准入门槛，通过探索也不自动入池。

## 默认离线操作

从仓库根目录执行：

```powershell
python -m panda_alpha.team demo --output research_runs/team_demo
python -m panda_alpha.team host-demo --output research_runs/team_host_demo
python -m panda_alpha.team --help
```

`demo` 使用合成输入和执行替身，演练两条方向的完整闭环，拦截网络和子进程调用。输出包括 `team.sqlite3`、两份 request JSON、合成结果和 `report.json`；目录必须尚不存在。最终状态 `PILOT_READY` 仅指离线实现验收通过，任务板会暂停，不代表真实候选已通过来源或经济验证。合成 grant/permit 与真实客户端不能混用。

`host-demo` 用脚本角色完成三轮、四次合成执行、一次同任务补证再审和两次跨方向停派，并在两个持久化边界注入中断。关闭并重开数据库后恢复原动作，核对登记分母和模拟调用次数。它验证调度及恢复；不会证明模型能自主发现有效因子。

其余命令的入口如下，具体 JSON 字段以各自 `--help` 和契约为准：

```text
init     --ledger DB
status   --ledger DB
packet   --ledger DB --role ROLE
submit   --ledger DB --request FILE
message  --ledger DB --input FILE
freeze   --ledger DB --task ID --version N --input FILE
decide   --ledger DB --task ID --version N --input FILE
plan     --ledger DB
pause    --ledger DB --reason TEXT
```

`init` 创建默认 offline 的任务板；其他命令遇到不存在的账本直接失败。实际试点复用现有实验账本，不能用新建空库重置历史消费。`packet` 是指定角色的只读材料视图；写入由协调宿主统一调用。研究员用 `cross_review` 提出跨方向控制，正式 challenge、review、result_review 由 skeptic 完成。`decide` 只处理执行前决定；结果结论必须用 `conclude`，避免只关任务而漏写科学记忆。

请求的核心字段为 `task_id/owner/mechanism_id/information_family`、`primary_fields/sources/availability_clock/risk_exposures`、`hypothesis/control/falsifier`、`candidate/window/cycle/groups` 和 `artifacts`。`sources` 使用精确机器标识，解释放在时点和限制字段，不能改名为描述句；角色包提供已登记 grant scope 供核对，展示范围不授予消费权限。公式实际字段必须全部落在 `primary_fields` 与可选 `control_fields` 内；Python 请求须附带已哈希的原生预检 receipt，派发时重放并核对输入契约。主信息与控制字段分别受 grant 限制；Python 仍是受信任代码，字段检查不等于任意代码沙箱。

消息必含 `event_id/task_id/actor/kind/version/request_hash/claim/test/decision`。答复通过 `reply_to` 指向 challenge，review 指向 response；review 选择 `approve/park`。冻结输入包含 `request_hash/priority/allocation_reason/diversity_check` 和每条跨线控制的处置。结果审查引用原 result 事件、manifest 哈希及证据快照，选择 `continue/confirm/source_pending/reject`。lead 的 conclude 绑定 `operation_id/review_event_id/decision/reason`，只能落实独立审查的决定（reject 映射为 close）。修改经济定义须新 task，后续实验仍需重新挑战与冻结。

## 有限持续调度

无人值守部署使用 `host-serve`，自动监管四角色 Codex 进程和协调器。启动、权限、超时、重连及中断恢复见[宿主说明](unattended_host.md)。下面的 `host-run` 邮箱/命令协议继续用于已有外部宿主。

复制 [team-host.example.json](../config/team-host.example.json)，设置实际目标、轮数、动作上限和含时区的截止时间。模板截止时间故意过期，须按当前授权填写；它不包含资金许可。`seed_tasks` 可选择原任务以便恢复或补证。`host-init` 要求科学登记已存在，复用原历史；不能用新空库绕过消费和研究约束。

```text
host-init    --ledger DB --registry DB --session ID --input SPEC
host-next    --ledger DB --registry DB --session ID
host-respond --ledger DB --registry DB --session ID --input ENVELOPE
host-run     --ledger DB --registry DB --session ID --mailbox DIR --output DIR --watch
```

每轮 lead 分配两条不同的研究 brief，作者可以提交完整 request 或 `no_candidate/reason`。宿主自动推进角色交接、证据审查和结论，再将更新后的 `reconciled_memory` 交给下一轮。新提案调用既有 `ResearchEngine.allows_research`，人工退休和固定经济失败不能靠改名绕过。无新增信息时应停止或不提候选。

文件桥把不可变工作包写入 `<mailbox>/<role>/requests/<work_id>.json`。宿主需启动真实工作者，工作者按包内角色说明和 `reply_contract` 独立推理，并原子写入 `<role>/responses/<work_id>.json`：

```json
{"work_id":"包内的原值","output":{"decision":"no_candidate","reason":"没有新增来源证据，不重复原试验"}}
```

这个例子只适用于 propose。其他动作使用包内契约；host 自动绑定 task/version/actor/reply_to，工作者不写共享数据库。命令桥用 `--commands FILE`，文件是四角色到可信 argv 数组的映射；stdin 接收包，stdout 返回 envelope。`host-run` 不替外部宿主启动模型；`host-serve` 显式选择 Codex CLI 并使用操作者现有登录和模型额度，账号与配置由操作者提供。

工作先落盘再投递，回复先落盘再应用。非法业务回复保留并生成新 work_id，连同具体错误发回原角色修正，计入动作上限。重启使用原 session；已记录回复不重新询问模型。外部宿主在丢失确认时可能收到重复 work_id，需自行幂等处理；不保证外部模型调用恰好一次。

默认不接执行器，准备完成后返回 `execution_not_enabled`。只有显式 `--execute --config FILE` 才接既有 dispatch/resume，并仍逐笔检查 grant、配额和暂停。先恢复既有 Run，再释放新派发；未知远端身份返回 `operator_reconciliation`，别自动重发。`--watch` 仅等待角色回复或原 Run，间隔 1–60 秒；预算或执行阻断会返回可审查的原因，不反复创建请求。截止、轮数、动作数或 lead 停止决定结束会话；会话上限不重置全局研究配额。

## 分层证据验收

`evidence` 只读取既有 Run 的缓存，生成新文件，不覆盖原 manifest，也不调用官网。验收终态、十组/约定组数及组表、有限数值、IC/RankIC 日期一致、日期顺序与请求窗口，以及完整日志。Python code echo 若缺失，交付本身仍可识别，但 `candidate_code_binding` 明确待核；回显匹配也不证明 worker 版本与原生输入已获证。

result_review 的 `assessment` 必填 `scope`（capability/source/economic）、`source_status`（pending/verified）、`economic_status`（not_evaluated/supported/rejected）、`claims/pending/resolved/limitations` 和 `pool_admission:false`。证据绑定的 task、request hash、fingerprint、Run、持久化执行环境及文件哈希须一致；纯合成输出不能认证原生来源或经济效果。

来源 verified 需要独立的 `source_evidence` 和 `source_verification`，明确解决冻结请求及观察到的全部核心资格；不能用“报告有数据”替代期间/PIT核验。经济结论还需 economic scope、verified source 及 `economic_verification`。证据文件及结论仍需独立研究判断，适配器不自动证明 PIT 或统计显著性。正式入池始终是后续独立审查。

## 同任务补证与科学记忆

新来源可先经 `source-capture` 或明确文本归档取得，再用 `source-submit` 固化并排队。活跃 Host 在原请求完成后的工作边界接收，复用下述重开与结论协议；`readiness` 以只读方式汇总缺口。有限公开文档检查和输入格式见[来源补证](source_intake.md)。

只补来源、日志或已有结果的解释而不改经济定义时，调用：

```text
evidence             --ledger DB --task ID --output NEW_CONTRACT --input EXTRA_ARTIFACTS
reopen-evidence      --ledger DB --task ID --version N --input REQUEST
conclude             --ledger DB --registry DB --task ID --version N --input DECISION
recover-conclusions  --ledger DB --registry DB
```

补证 REQUEST 含 `operation_id/request_hash/prior_decision_event_id/target_event_id/claim_delta/artifacts`。绑定当前关闭决定和原 result（未执行任务则原 response）；新证据必须有实际内容变化，重命名旧文件不算。保持原 task、request、fingerprint、Run、尝试及唯一策略分母。经济已拒绝的任务不走普通补证；人工退役和实质经济重开继续使用既有科学协议。

重审显式绑定新 `evidence_contract`、原 manifest 和新附件；新快照重算观察结果，包含原 Run 的证据，不替换旧快照。缺失日志后来归档可经此路径重新验收。skeptic 必须回应新增材料，不能只重复旧结论。

conclude 先在任务库记录意图，再幂等追加 registry 的 evaluation/source_pending/source_repair/economic_rejected，最后关闭或停放任务。跨库中断用原 operation_id 恢复，不重复登记策略或调用官网。历史 pending 与冻结要求累计保留，只有显式 resolved 才移除；来源修复不能删除既有经济失败或人工退役。下一轮读取更新后的科学记忆，以及每策略最新、最多八条的 `recent_evaluations`，同时带回正面、负面和待补结论的范围与证据；正面结论不自动创建新试验或授予入池。

资金模板见 [team-grant.example.json](../config/team-grant.example.json)，默认 draft，不能执行。它绑定原研究轮政策、有限有效期、总点数/充值/次数、来源与主/控制字段、窗口和任务类型，以及实际授权证据路径与哈希。模板数字不是报价或授权。官网没有已核实的子账户算力划拨接口，此处控制同一账户的任务许可与预留，不模拟平台转账。

## 明确授权后的有限试点

启用前应具备可审查的冻结候选、来源与反证问题、资源方案，以及覆盖真实资金来源、总额、次数和有效期的明确授权。若原研究轮需要扩额，先完成授权范围内的账本审计修订，再让执行配置与 grant 绑定同一政策；不能用新 grant 绕过原账本限制。

`activate` 安装已授权 grant 并进入 pilot 模式；它本身不启动官网回测。`dispatch` 连接真实配置和科学登记，经原子预留与远端边界复核后执行一个冻结任务。`resume` 只恢复同指纹下的原 Factor/Run，保存结果并核对结算：

```text
activate --ledger DB --grant FILE --confirm-paid-pilot
dispatch --ledger DB --task ID --config FILE --registry DB --execute
resume   --ledger DB --task ID --config FILE --output DIR
```

每次派发前重新检查剩余实际消费与预留、尝试次数、阶段复盘及有效期。同一研究线最多一个 ready 任务，主候选与对照依次冻结和执行；先结算已有 Run，再安排下一笔。失败与必要诊断也占用授权范围，不能为了用完额度不断新增变体。

实际结果进入独立 result_review 和 lead 的最终决定后，才能报告该项研究的结论。输入、成员、费用或时钟证据不足时保留 pending，技术/来源不足不登记为经济证伪；真实结果也不会自动入池。到达授权的停止条件或截止时间后暂停新派发，保留已有 Run 的查询与结算入口。有限试点不自动恢复此前暂停的其他任务或自动化。
