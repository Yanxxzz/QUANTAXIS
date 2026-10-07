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

### 年度贸易营运资本来源

`trade_accrual.py`提供`load_trade_accrual_report`、`select_trade_accrual_asof`和`trade_accrual_score`。其固定定义为同一年报的`-(Δ应收账款+Δ存货-Δ应付账款)/平均总资产`，是三科目贸易营运资本投资代理，不是完整Sloan应计、完整现金经营盈利或真实现金回笼。

四科目必须来自同一份年度合并资产负债表的两列，原件和原文分别绑定哈希。明确上年12月31日或当年1月1日可作为期初存量；已核实年度、表内当前日期和明确期末/期初列角色可以绑定该年度期初，保存原始日期及绑定方法。独立上年报告不替代本期比较列。科目中文后缀、年报摘要和年报工作制度不能冒认目标字段或完整年报。

来源选择检查最新已公开年度版本，未知、更正范围不明或后续比较量矛盾保持待核。半年度报告只验证选中年度的后续期初比较量及已知重述，不能成为年度分数。独立研究仍须提供完整性范围、金融行业排除和逐日来源状态，公共运行器不替调用者认证公告全集。

`trade_efficiency.py`提供同原件的年度营业收入附件和WC02选择器。分数为`-(期末贸易营运资本-期初贸易营运资本×本年收入/上年收入)/平均总资产`，高值表示相对固定销售规模参照的占用更低。收入与股票余额必须匹配公告ID、PDF/原文SHA，收入两列都严格为正，并经共享接口绑定两个完整年度的流量跨度。无法绑定的重复标题、泛本期/上期、空白及独立旧年收入不能代替证明。

营业收入有独立的本年及比较年度更正依赖。“年末资产余额无影响”不等于“年度收入无影响”；总额转净额会改变收入增长参照。新增来源要求改变可用范围时，重新准备原WC01和F141的完全相同支持对照；它是控制，不能将不同股票池的收益差归为改进。事后市场回归残差及持仓重叠用于描述，不参与打分或生成对冲交易。

`component_efficiency.py`增加`attach_component_cost`、`component_efficiency_score`和`select_component_efficiency_asof`。WC03使用期初应收乘收入比率、期初存货减应付乘营业成本比率，再减期末三科目净额，除以平均资产。四个余额对、两个收入和两个成本金额须来自同一年度原件；营业成本两列严格为正，不能使用营业总成本或其他年度的厂商快照。

成本附件另需有界合并利润表证据，原文SHA、表首尾、目标行偏移、印刷两列、表头及年度跨度均须对应，单有同PDF不能排除母公司报表。输入中预制的合格状态或金额不能覆盖原文核验。更正依赖扩展到六字段；未知季度更正的标题链接不能证明它不影响年度比较值。所有未知屏障仍保留，控制面板也使用新策略的完全相同准入掩码。

后续补数复核发现三列裸数字不能仅凭首项较小及表头含附注就删首项，否则会误读多列重述或后置附注。公共存量/收入解析现保留这种情况为未知，显式中文附注标记仍有独立解析路径。列坐标及原报告审计期间的补证另存版本化sidecar；日期必须直接修饰相应合并报表，不能跨母公司日期借用。新解析使用新缓存版本，旧数值、原件与收据不套新SHA。

营业成本只是成本活动参照，不等于采购额。该因子不是完整现金转换周期或实际现金释放；其相对WC02的代数差还包含收入与成本增长差异，须检查毛利变化及行业结构，不能自动解释成纯效率Alpha。

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

带明确可用时点的新风险快照若声明未知（`*_pit_usable=False`），会覆盖旧已知值。最新公开行业名单缺证券，或最新Beta估计缺失时，不再回退旧记录并把它报告为当前已知风险。

部分形成日无法完整分组时，保留诊断但状态为`source_pending`；缺失日不会压缩调仓周期。准备记录绑定实际输入、目标及记账/绩效代码；改变后必须重新准备。

来源分类证明也属于收据绑定。全部来源与审查收据完成后才准备；若准备后新增证明导致收据哈希变化，即使数值和分组未变，旧准备仍不能直接评估。保留旧版，用版本化的新准备及执行修复事件重绑；未生成经济标签的校验失败与来源修复不增加策略数。

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

首次优化验收：534项离线测试通过；干净复制111个公共源文件，34个模块可导入。随后WC01来源与最新未知风险快照修复完成，579项离线测试通过；WC02收入依赖加入后604项离线测试通过、36个公共模块可导入。WC03成本来源加入24项测试后，完整628项通过、37个公共模块可导入。42条合成路径及两档成本通过，合成验证中的官网运行/LLM调用/真实行情读取均为0。真实WC03的46条路径另经独立账本核验；输入、覆盖及经济结果另留冻结收据，远端CI状态另行检查。

补数期间三列整数误读修复后，新增7项来源回归，当前完整635项通过；37模块和42条公共合成路径仍通过。旧真实收益未按新解析器重算，已知修订正文缺口使当前来源保持待核。

2026-10-08来源扩展收尾：固定18096任务全部遍历，18095原件及正文就绪，16408正式报告最终解析错误0。来源布尔结果按5252个采集代码和257日记录，798583条通过命名版本内的来源门；它不认证完整PIT、行情支持、动态股票池或因子表现。已知更正范围、原版元数据、真实版本替换及字段证据缺口继续登记为`source_pending`，不增加经济试验或触发经济放弃。未知披露时间不能绕过较新报告屏障；费用负号展示与真实非正收入分别分类。采集进度文件的临时Windows读锁采用有限重试，写入竞争不计入来源失败熔断。原件、原始尝试和旧研究输出保留，本轮官网/付费LLM消费0，详细计数及剩余数据见[RESEARCH.md](../RESEARCH.md)。
