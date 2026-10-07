# Panda Alpha 公共工作流

本页记录2026-10-07的工作流优化。真实历史数据和旧研究结果仍在本地私有目录，未重新测试封存窗口，未派发官网任务。

## 试验登记与热记忆

`ResearchRegistry`兼容原`TrialLedger.record/total/new_tested/close`。策略身份由规范化定义、方向、窗口、调仓及分组组成；来源修复、执行修复、不同费用档为证据事件。旧SQLite的`trials`和历史基数保留，新增事件受不可变约束与哈希链检查。

本机迁移逐项绑定7个已冻结策略协议：三项F141组合政策、一个回购增量政策、CB01、GP01、GP02。与旧27条登记核对为415＋34＝449。GP01来源不足仍单独记录，不能伪造经济否决；反思次数不等于新增策略数。

CLI采用相同账本，所有命令均离线且不会创建官网任务：

```powershell
python -m panda_alpha --config config/panda-alpha.local.json registry status
python -m panda_alpha --config config/panda-alpha.local.json registry plan-import `
  --manifest research_runs/import_manifest.json --output research_runs/import_plan.json
python -m panda_alpha --config config/panda-alpha.local.json registry import `
  --plan research_runs/import_plan.json --execute --output research_runs/import_receipt.json
python -m panda_alpha --config config/panda-alpha.local.json registry export-memory `
  --base-memory research_runs/live_memory.json --output research_runs/live_memory.next.json
```

迁移文件必须给出真实协议路径、SHA256、策略定义、原始登记时点声明和累计覆盖身份。`plan-import`不推算缺少的试验；未对齐、输入哈希变化或计划已过期时，`import`拒绝执行。备份和审阅派生记忆后再明确选择正式文件。

`research.live_memory`与初始bootstrap同时加载，注册表派生当前累计与约束。记忆声明高于事实登记时，新研究会被阻断，要求先核对；不会把449再作为历史基数加新事件。人工淘汰只有明确人工重开事件可以解除。已验证的新材料重开可持久化，不能绕过人工淘汰。

## 财报字段契约

`financial_statements.py`共享金额、单位、公告可用日和期间检查。CB与GP解析器输出schema2，保持旧public函数及schema1读取兼容：

- 当前与比较金额以原文人民币单位转换，分别保存印刷精度。
- 资产等存量使用明确时点；利润等流量使用明确起止期间。
- 比较期不明确则标为待核，不能根据猜测年份补日期。
- H1必须是1月1日至6月30日；Q1和九个月不能用于半年TTM拼接。
- 已解析的资产比较列直接连接年度重述检查。两打印量的精度区间重叠不会误报实质变化。
- 旧snapshot缺少这些字段时仍支持来源绑定sidecar，旧SHA及原始文件不会原位改写。

这套接口认证提供的原件/版本范围。完整季度历史、全市场修订关系及历史身份仍需独立来源验收。

## 公共实验运行器

`study.py`提供：

```python
from panda_alpha.study import prepare_study, validate_study, evaluate_study
from panda_alpha.quality import assess_research_quality

prepared = prepare_study(protocol, factor_values, quotes, comparator_values,
                         source_receipt=source_evidence)
checked = validate_study(prepared, protocol, factor_values, quotes,
                         comparator_values, source_receipt=source_evidence)
assert checked["verified"]
result = evaluate_study(prepared, protocol, factor_values, quotes,
                        comparator_values, source_receipt=source_evidence)
review = assess_research_quality(result, diversity_evidence)
```

`factor_values`接受显式`date/symbol/value/pit_usable/source_status`长表或价量公式宽面板。财务/事件因子必须由自己的来源模块生成已知可用日值；有限的价量公式计算不认证财务PIT。

`protocol`冻结候选ID、方向、分组、周期、最低股票数、两档费用和窗口。必须提供独立的有序`calendar`及`calendar_verified`声明，也可通过`source_receipt`提供；观察到的报价日期不能自行升级为日历认证。真实研究由外部来源验收这一声明，合成测试明确标注其fixture范围。

`quotes`包括复权`open/close`、`raw_open/high/low/close`、`volume/amount`和明确交易状态。已知停牌可以用既有标记保留持仓；未知价或交易状态不能虚构成交，更不能当作因子经济失败。

运行器对所有组、共同市场和显式固定比较因子使用同一股票/形成日支持，记录数量、现金、交易费用、价格损益及净值。组合以两腿各半初始本金分别运行，不隐式定期重置权重或抵消费用。任意候选名称使用同一实现。

可选风险字段为`sector/beta`、`sector_asof/beta_asof`及对应`*_pit_usable=True`。当前行业列、缺日期或未来日期不会伪装成历史风险事实。风险输出是按收盘财富的描述性持仓暴露，缺失保持未知；不影响因子值，也不证明因果Alpha。

部分形成日无法完整分组时，保留诊断但状态为`source_pending`；缺失日不会压缩调仓周期。准备记录绑定实际输入、目标及记账/绩效代码；改变后必须重新准备。

## CLI联合反馈

通用`evaluate`已使用公共运行器，报告包含`.preparation.json`、`.study.json`和联合质量证据。`--benchmark-id`显式选择现有池中的因子，方向与面板缺失时不能宣称组合增量；省略基准时只保留市场和单因子研究。

`--warmup-start`为源查询提供早于研究窗口的暖机数据，并接受封存窗口检查。已知DSL滚动/延迟依赖也会传给准备步骤。无法形成完整调仓周期时保持待核。

`quality.py`区分来源、潜在执行、经济、风险、实际去相关和组合证据。两档成本、价格损益、市场相对收益、阶段及集中度同时保留；没有加权总分。完整来源下两费档净收益、价格损益和共同市场相对表现均弱，可以停止本条固定定义，不扩大为整类机制证伪。缺数据不会生成经济拒绝。

正式`admission`仍由独立证据审阅决定。本地股票数量、日线潜在成交、原文存档或正代理收益均不代表完整股票池、真实竞价、容量、独立OOS或官网积分已通过。

新标签和官网派发前执行人工/登记约束检查。官网真正派发前先登记策略；模糊服务响应不会因缺Run ID而遗漏计数，也不会重复启动任务。预算、预检和用户授权条件仍独立保留。

## 验证

```powershell
python -B scripts/verify_public_research.py
python -B scripts/verify_public_research.py --clean-tree .runtime/public-copy-new
python -m pytest tests -q
```

`--clean-tree`仅创建不存在的新目录，复制公共源码与合成fixture，明确排除私有数据库、行情、runtime和本地配置。它使用已装研究依赖的解释器，不下载数据或安装依赖。

公共fixture为36个合成名称、72个声明交易日，以非历史候选名验证两费档、全部组、市场和固定比较的42条路径。财报小fixture验证原文比较列经过CB和GP接入年度重述检查；它们不使用真实价格或封存OOS。

CI使用`requirements-panda-alpha.txt`作为同一依赖入口，并执行公共冒烟和离线回归。Docker及远程CI须分别报告实际执行状态，不能由本地合成通过代替。

本机最终验收：534项离线测试通过；干净复制111个公共源文件，34个模块可导入，42条合成路径及两档成本通过，官网运行/LLM调用/真实行情读取均为0。远端CI状态另行检查。
