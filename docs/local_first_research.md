# 本地优先、必要时官网验证

研究目标是找到能持续贡献官网积分的优质因子。程序验收、任务数和单项统计量用于判断证据可信度，不是研究产出本身。沿既有科学登记保留所有实现、失败和标签暴露；不以增加同机制微调次数制造赢家。

## 复用现有回测

旧 `python -m panda_alpha evaluate` 保留。四角色宿主现在也能调用同一 `study.prepare_study/evaluate_study` 和 `quality.assess_research_quality`：冻结信号在下一交易日开盘形成潜在持仓，以两档固定单边成本重放，保留分组、现金、交易费用、同支持集市场对照和时间分段。它仍是指定来源下的研究账本，不认证真实竞价成交、完整历史财务PIT或官网生产输入一致。

`panda_alpha.team.local_screen.local_first` 提供统一路由：

- `official_validation`：本地研究已运行，继续用官网检验原生行为；不是经济或入池全通过。
- `local_reject`：固定定义在完整本地证据下确实弱，停止新增官网派发，保留报告供独立复核。不反号、换窗补跑。
- `official_fallback`：数据或执行输入缺失，保留原错误、缺键和数据证据，直接进入原官网资格与算力检查。缺失不是收益为零，也不是经济否决。
- `await_local_data`：本地证据不足且本轮未允许官网替代。

公式实现错误、输入哈希变化、身份错误和损坏的行情不会转换成付费回测理由。转官网不会豁免财务报告期/PIT、冻结定义、授权、累计预算或次数；已知 Run 恢复不重新进行本地选优或创建另一个 Run。

## 配置与恢复

研究配置的 `local_screen` 包含 `enabled=true`、`allow_official_fallback`、`output_dir` 和 `inputs`。`inputs` 以冻结任务的 fingerprint 为键，值是输入清单 `{path, sha256}`。清单绑定 `fingerprint`、`request_hash`、`availability=ready|unavailable` 及实际来源证据 `artifacts`。

数据就绪时，清单提供 `quotes`、`values`、`calendar` 三个文件的路径/哈希、`price_adjustment=qfq|hfq` 和 `calendar_verified=true`。行情与信号为 Parquet；信号列为 date/symbol/value，日历 JSON 包含有序唯一的 dates。财务方向还需真实 `financial_pit_verified=true`。Python 信号使用已审查实现生成的绑定面板；本模块不会执行 agent 提供的任意 Python。原有公式解释器与数据采集器继续负责其适配。

缺数据时，清单使用 `availability=unavailable`，提供具体 `reason` 和探测/缺口证据；不能只写“无数据”跳过尝试。完成的输入和探测回执可直接复用，不因模型重连重复取数。本地报告键绑定原请求、输入、成本及实际研究/归因引擎；恢复复用同一报告与事件，不新增登记分母。

离线会话可设置 `local_screen_config={path,sha256}`，绑定上述研究配置，宿主在任务 ready 后运行本地阶段，不需要官网授权或执行器。新官网派发在协调器中复核同一阶段，再经过原算力管家。历史部署缺省维持原冻结设置，不原地改写；新部署显式启用。

## 双重额度与研究决策

Codex 角色调用受单轮模型动作、原绝对截止、超时和重连次数约束；实际账户用量另行观察。只让模型处理新增材料、竞争解释和结果决策，数据重放、数值检查、取数及路由使用确定性程序。不同角色的审查保持职责差异，不以互相批准次数衡量研究质量。

官网额度按活动/充值来源分开核验，预留后执行、真实消费后结算。预算和运行次数累计，不因新会话、代码后继或失败而清零。完成后停止；额度尚有剩余不构成实验理由。

积分验证复用 `pool_scoring` 与 `pool_admission` 的既有规则和证据要求。探索期可以大胆提出可检验机制，但不把 IC、样本内夏普、测试数量或代理积分当成真实持续贡献；保留失败、成本压力、同支持集对照和未暴露区间，避免围绕一个代理指标反复调参。
