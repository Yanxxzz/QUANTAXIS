# 官网研究与预算

官网探索、确认性复测与正式入池是不同决策。研究问题明确、定义和方向冻结、字段算子能执行、Python免费原生预检通过后，可以用官网结果减少不确定性。先写`research_question`、`decision_if_pass`、`decision_if_fail`，按决策价值排序；不要求先满足200周期、24月或池积分增益5%。正式新增或替换入池采用30bp净日收益Sharpe≥0.5底线，官网探索不以此作为前提。运行成功本身不授予入池资格。

## 共享预算

`compute.allocation_mode=shared_priority`把原30%/40%/30%类别比例作为分配目标。当前有价值的候选可以借用其他类别空闲额度，包括比例为零类别；比例不再导致算力闲置。`research_priority`为可选排序值，相同优先级保持计划顺序。预算总额与已消费金额仍是约束，不以用完赠送为研究目标。

`source_probe`、`exploration`、`validation`都是官网研究类别。声明了研究问题和两种后续决定时，`full_a_coverage_pending`、`official_pool_increment_pending`、`formal_admission_pending`、`post_effective_B_pending`不阻止核查这些未知事项。真实字段映射、输入索引、方向、历史来源重验证等可执行性缺口仍先解决；研究覆盖待核不认证全A/PIT。

三项决策字段是研究记录规范；派发代码在放行上述待核事项时强制检查它们，其他候选尚未强制这三项。Python源码候选有原生免费预检；公式的字段与算子检查属于准备步骤，`dispatch`本身不额外发起公式试跑。

## 本地与官网分工（2026-10-12）

启用`compute.official_research_policy_file=config/research-workflow.v1.json`后，所有新官网候选都必须说明研究问题、通过/失败后的决定、稳定经济机制及官网能补充的具体信息。上一段仅三项待核事项时强制的规则属于未启用新政策的旧调用；旧Run查询与恢复不受新元数据要求影响。

本地优先完成语法/索引检查、30/50bp成本复算、价格损益与费用归因、行业及持仓集中度、信号/收益相关、真实信号组合增量和已有数据上的阶段稳定性检查。相同定义、窗口、形成股票、持有日期与口径的已完成对照直接复用；不为费用档或读取已有结果再次派发。

官网只接收三种`official_task`：`native_screen`对应`exploration`，`source_diagnostic`对应`source_probe`，`key_confirmation`对应`validation`。前两者解决原生输入下的初筛和可执行来源诊断；后者只确认已有本地或官网证据中的关键未知事项，必须提供`local_review_evidence`中的既有文件路径/SHA256。这不要求先通过正式入池Sharpe、200周期或全本地季度数据认证。

原生财务期间、实值等价和完整PIT尚未知时，可在明确`source_diagnostic`问题下保留`native_financial_period_pending`、`native_financial_value_parity_pending`、`full_pit_certification_pending`。字段实际缺失、历史SOURCE未核、方向未知、索引错误和失败原生预检仍不能派发；目录字段存在不认证金额或生产PIT。先做有界诊断，再决定哪些来源值得补，不先批量重建全部财务历史。

## 跨方向累计门槛

默认一个寻找候选的研究轮共用60点、最多20次官网尝试，覆盖多个方向、日期及资金批次。单经济机制初探最多2次（含来源探测及必要对照），总计最多4次；后续调用须为有证据的关键确认。名字变化、窗口变化、数值参数变化都不重新得到初探额度：以稳定`budget_mechanism_id`（省略时使用`mechanism`）和忽略数值参数的代码族并行归组。这只是预算归组，不表示不同参数经济等价。

总额按已结算实扣加未结算预留计算，失败尝试计运行次数，失败扣费计金额；已确认零扣费且未创建Factor/Run的取消才不占额度。派发在SQLite原子预留中再次检查，不能通过换资金batch、跨午夜、改研究ID、提高配置上限或关闭政策绕过已绑定研究轮。第一笔新研究预留才绑定本轮；旧结算账本保留，不伪造为本轮消费。

每累计20点，下一笔派发前要求一次免费本地复盘：说明已获得的信息、已关闭/保留的机制和下一步，绑定已有结果文件，并选择`continue`、`redirect`或`local_only`。这不是逐次人工审批，研究流程可以在授权范围内完成；未结算执行先结算，未达到节点不能预填复盘，复盘不会增加算力授权、次数上限或新经济context。达到60点/20次停止新增官网派发，额度不能因换方向自动重置；后续提高总预算或新开研究轮需新的明确安排。

这套门槛控制本地派发。平台实际单次扣费可能超估算，原批次授权、资金来源及异常结算锁定继续适用；它不构成服务端硬封顶。配置并未新增60点收费许可，原赠送优先、充值每批确认及暂停/截止指令保留。

离线查看累计及复盘节点（不访问账号、不创建缺失账本）：

```powershell
python -m panda_alpha --config config/panda-alpha.local.json compute-status
python -m panda_alpha --config config/panda-alpha.local.json compute-review --evidence research_runs/review.json
python -m panda_alpha --config config/panda-alpha.local.json compute-review --evidence research_runs/review.json --execute
```

复盘文件包含`study_id`、`decision`、`findings`、`next_steps`和`artifacts:[{path,sha256}]`。预览只读，`--execute`把一条精简复盘写入同一`experiments.sqlite3`，不复制结果、不启动回测、不更改科学试验分母。

## 两种计费模型

- `enforced_cap`：有真实服务端上限及其来源。超上限不能当成正常预估误差。
- `authorized_estimate`：本批已明确接受估算扣费风险，用`planning_credit_estimate`及`planning_cost_source`、`estimate_verified_at`作为预留。`cost_ceiling`可以为空；不会把预估写成服务端硬上限。

示例配置支持估算模式，但不包含实际授权；开启`allow_estimated_billing`布尔值不会自行产生收费权限。本批授权记录仍需明确总额度、定义指纹、有效期、估算风险及可选运行次数。赠送优先，充值每批单独确认；代码修改不会增加已批准金额或复用旧单次许可。

新估算批次总额可小于每日上限，例如批准4点批次，无需为了每日上限10点而批准整日10点。批内每次串行派发，结算后更新剩余额；总实扣、充值实扣及运行次数跨作业累计。`valid_from/valid_until`可表达明确批准的有限跨日批次；未提供它们则保留原当天授权语义。跨日授权不代表赠送结转，跨午夜余额变化仍需要真实账单核对。

实扣超预留但仍在批次总额、当日额和充值许可内，且各余额分类勾稽明确时，估算模型可以正常结算，记录`estimate_overrun`并上调下一次预留。合法的已授权充值兜底不再一律标为异常。未授权充值、超批额度或无法归因的余额变化仍停派。

## 同研究轮额度扩展

`python -m panda_alpha.team amend-budget` 管理已有研究轮的累计上限。它只允许增加 `max_runs`、`max_total_credits`；研究轮身份、每20点复核和单机制限制保持不变。旧作业中的原limits、Factor/Run ID、账单和失败记录均保留，用量继续按同一study累计。曾经用明确例外超过原次数上限的历史不能因此被删除。

先只读取得权威账本快照（不会创建团队表或审计表）：

```powershell
python -m panda_alpha.team amend-budget --ledger research_runs/experiments.sqlite3
```

申请JSON使用以下字段：`amendment_id`（稳定且唯一）、`status`（draft或authorized）、`ledger_path`（该账本绝对路径）、`expected_snapshot`（完整复制上条命令输出）、`new_policy`（完整新limits）、`approval`（真实操作员批准材料的path/SHA256）及`reason`。例如原轮25次/54点、原上限20次/60点，计划新增最多4次、预留32点，可提出同轮29次/86点；这不是新的研究轮、服务报价或资金授权。

```powershell
# 默认只读预览；draft可以预览。
python -m panda_alpha.team amend-budget --ledger research_runs/experiments.sqlite3 --input research_runs/amendment.json
# 仅明确授权后：输入必须为authorized，并有可验证的批准材料。
python -m panda_alpha.team amend-budget --ledger research_runs/experiments.sqlite3 --input research_runs/amendment.json --execute
```

写入使用同一SQLite事务核对原policy、全部作业摘要、累计用量和阶段复核摘要。账本路径不符、快照已变、有未结算作业或团队仍在pilot模式时拒绝修改。成功后原子更新上限并追加不可改删的审计记录；相同ID及相同内容重放返回原收据，不同内容不能复用该ID。失败会回滚上限和审计写入。

扩展不改暂停状态、不安装或扩大已有grant、不解除local_only复核、不免除下一60点阶段复核，也不查询官网账户。原来绑定旧policy的执行配置和grant不会自动接受新上限；实际试点仍需另行明确授权并保持配置、grant与当前policy一致。批准材料由操作员提供，路径和哈希绑定用于审计，不是用户身份认证或强权限隔离。

## 使用入口

预算预览会免费查询余额，并只读现有实验账本，不创建账本或启动回测：

```powershell
python -m panda_alpha --config config/panda-alpha.local.json schedule `
  --candidates research_runs/candidates.json `
  --ledger research_runs/experiments.sqlite3 `
  --output research_runs/compute_plan.json
```

预览报告`cost_basis`、`reservation_credit_estimate`、类别借用及剩余额。`dispatch --execute`再次在原子事务中检查批次、当日与实际余额，不依赖旧预览。全批通常一次审批，同批固定指纹内不逐次重复审批；新增未批准定义不借旧授权。

相同指纹复用原Run，派发响应不明不自动重试。失败后先免费读原Run日志并本地修复；新定义必须重新冻结并完成预检。该政策没有创建新批次、发起收费、修改参赛池、恢复取消的2023回测或发布可视化。
