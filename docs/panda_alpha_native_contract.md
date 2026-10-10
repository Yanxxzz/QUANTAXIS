# PandaAI 原生输入和失败日志核验

2026-10-06 的实际运行和公开源码核验得到两项兼容约定。

`FactorDataHandler.get_base_factors_pro` 按 `[date, symbol]` 创建输入索引，
`FactorSeries` 包装器将属性访问委托给 pandas Series。因子内部可以按需要重排，
输出对齐必须先恢复输入的真实索引顺序，再回对齐原索引。将 `[symbol, date]`
结果直接对齐 `[date, symbol]` 输入会使全部数值变成 NaN。旧文档中的顺序示例
不能替代实际输入核验。`native_commonality_code()` 和对应测试覆盖两个顺序及包装器。

日志接口 `/quantflow/api/workflow/run/log` 返回 `data.logs` 列表，默认仅有5条，
`has_more` 和 `next_sequence` 指示后续页面。查询下一页使用包含起点的
`last_sequence=next_sequence`。当前 CLI 0.1.7 的旧节点字典解析没有覆盖该格式，
因此可能漏报真实错误。`PandaClient.logs(run_id)` 按公开接口读取本人运行的日志，
保留节点错误和分页完整性，输出去除用户ID等无关账户元数据。读取日志不启动回测。

来源：

- [官方输入与结果处理代码](https://github.com/PandaAI-Tech/panda_factor/blob/main/panda_factor/panda_factor/generate/factor_data_handler.py)
- [官方包装器](https://github.com/PandaAI-Tech/panda_factor/blob/a783e69732da1f9ffc93844dc522375a1f67c507/panda_factor/panda_factor/generate/factor_wrapper.py)
- [公开日志参数](https://github.com/PandaAI-Tech/panda_quantflow/blob/688b90e74a738b84567efe622a2d9c1e5ce10e00/src/panda_server/routes/workflow_routes.py)

经济定义、方向和窗口不因兼容修复而改变。原始失败和扣费记录保留；执行失败不等于
经济证伪，也不应消耗因子调查次数。已批准的单次运行失败后，复测须服从该次运行授权
及用户的每批预算约定，不能默认为自动重跑授权。

新 Python 派发通过 `build_native_preflight` 生成证据，再提供
`candidate.native_preflight_path`。回执绑定代码、方向、请求窗口、调仓周期、分组数、
日期在前的来源 parquet、来源证据、接口证据及本地执行器的哈希。派发前重新执行回放，
不接受只有 `passed=true` 的手写回执；缺少回执、证据变更、错位、全空、常量、覆盖不足
或请求分组无法形成，都在访问账户及创建工作流之前拦截。已有账本作业继续按原 Run ID
查询，不补造一次新派发。

分组核验参考公开 SDK 固定版本：先要求不同值数量不少于请求分组数。本地另外要求
无随机扰动的 `qcut` 能形成全部请求组，是保守烟测；不模拟 SDK 的微小随机扰动、
远端行情清洗或无法交易筛选。这是已知输入契约的本地检查，不是远端全 A 数据等价、
计费保证或服务端永不失败的证明。不支持的 SDK 算子与未安装的远端依赖保留阻断。

默认覆盖策略仍为 `full_universe_strict`：首个有效日期之后至少80%的日期可分组，
整个来源 fixture 的每日有效证券比例中位数至少80%。这两个80%是本地检查策略，
不能据此宣称官网要求80%覆盖。

转移已核验的命名财报来源子集时，可在候选中明确设置
`native_preflight_purpose="source_qualified_transfer_research"`，重新生成回执。
这个固定研究用途要求请求窗口内每个 fixture 日期至少2000个有效资产、能以原来的
无扰动 `qcut` 形成全部请求组；早期空日期不能裁成“暖启动”后放行。
全证券分母继续保留，实际覆盖比例仍报告，例如58%不会写成100%。
原来源资格掩码和逐值核验 manifest 作为 `source_evidence` 绑定；此策略不扩展原资格池，
也不替代候选 manifest 的原值／掩码机械对照。

用途绑定候选定义、回执及派发前的实际重放；改用途、覆盖策略、来源掩码或研究范围
均需重新核验，不能手改旧回执放行。派发记录明确携带 `research_only=true`、
`full_A_certified=false`、`admission_qualified=false`。这只是命名来源子集的研究试算，
不证明远端全A等价、官网执行必然成功或组合入池合格；计费及原有资格规则继续适用。
