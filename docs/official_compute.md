# 官网研究与预算

官网探索、确认性复测与正式入池是不同决策。研究问题明确、定义和方向冻结、字段算子能执行、Python免费原生预检通过后，可以用官网结果减少不确定性。先写`research_question`、`decision_if_pass`、`decision_if_fail`，按决策价值排序；不要求先满足200周期、24月或池积分增益5%。正式新增或替换入池采用30bp净日收益Sharpe≥0.5底线，官网探索不以此作为前提。运行成功本身不授予入池资格。

## 共享预算

`compute.allocation_mode=shared_priority`把原30%/40%/30%类别比例作为分配目标。当前有价值的候选可以借用其他类别空闲额度，包括比例为零类别；比例不再导致算力闲置。`research_priority`为可选排序值，相同优先级保持计划顺序。预算总额与已消费金额仍是约束，不以用完赠送为研究目标。

`source_probe`、`exploration`、`validation`都是官网研究类别。声明了研究问题和两种后续决定时，`full_a_coverage_pending`、`official_pool_increment_pending`、`formal_admission_pending`、`post_effective_B_pending`不阻止核查这些未知事项。真实字段映射、输入索引、方向、历史来源重验证等可执行性缺口仍先解决；研究覆盖待核不认证全A/PIT。

三项决策字段是研究记录规范；派发代码在放行上述待核事项时强制检查它们，其他候选尚未强制这三项。Python源码候选有原生免费预检；公式的字段与算子检查属于准备步骤，`dispatch`本身不额外发起公式试跑。

## 两种计费模型

- `enforced_cap`：有真实服务端上限及其来源。超上限不能当成正常预估误差。
- `authorized_estimate`：本批已明确接受估算扣费风险，用`planning_credit_estimate`及`planning_cost_source`、`estimate_verified_at`作为预留。`cost_ceiling`可以为空；不会把预估写成服务端硬上限。

示例配置支持估算模式，但不包含实际授权；开启`allow_estimated_billing`布尔值不会自行产生收费权限。本批授权记录仍需明确总额度、定义指纹、有效期、估算风险及可选运行次数。赠送优先，充值每批单独确认；代码修改不会增加已批准金额或复用旧单次许可。

新估算批次总额可小于每日上限，例如批准4点批次，无需为了每日上限10点而批准整日10点。批内每次串行派发，结算后更新剩余额；总实扣、充值实扣及运行次数跨作业累计。`valid_from/valid_until`可表达明确批准的有限跨日批次；未提供它们则保留原当天授权语义。跨日授权不代表赠送结转，跨午夜余额变化仍需要真实账单核对。

实扣超预留但仍在批次总额、当日额和充值许可内，且各余额分类勾稽明确时，估算模型可以正常结算，记录`estimate_overrun`并上调下一次预留。合法的已授权充值兜底不再一律标为异常。未授权充值、超批额度或无法归因的余额变化仍停派。

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
