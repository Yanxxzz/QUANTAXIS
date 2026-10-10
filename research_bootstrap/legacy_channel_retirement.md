# 旧渠道退出与初步执行审计

2026-10-05 的只读代码审计：未改动旧脚本、未删除原始数据、未启动旧取数任务、未调用 PandaAI 付费接口。机器清单 [legacy_channel_retirement.json](legacy_channel_retirement.json) 给出 14 个主要入口及 88 个静态依赖文件的路径、行号、调用名和源文件 SHA256。导入旧 workflow 仅说明依赖；生成想法、登记历史反馈、读取缓存并不等同于实时取数。

| 退出的执行入口 | 调用关系 | 保留内容或替代前提 |
| --- | --- | --- |
| `factor_workflow/stockdb_research_loop.py:212` 的 `simulate --execute` / `cycle --execute` | `run_simulation` → `run_<batch_id>.py`（存在时）或 `research_pipeline.py run --execute` | 退出新研究调度；保留 idea/feedback、历史登记和冻结身份 |
| `factor_workflow/research_pipeline.py:761` 的 `run --execute`，及对应 `run_batch*.py` 包装器 | `evaluator_command` → 配置 `paths.evaluator`；2653/2783 行 `run_logged` 执行 | 个别 evaluator 仅读缓存，应从新任务调度退出，保留旧回放代码与审计器 |
| `stockdb_factor_eval/evaluate.py:244`、`evaluate_batch18.py:146`、`evaluate_batch19.py:258` | `init_stockdb` → `stockdb.init(127.0.0.1:7899)` → 日线/日历/市场上下文 `rd.vals` | 退出价格取数渠道；保留价格缓存、完整因子值面板、冻结参数、费用假设和历史结果 |
| `stockdb_factor_eval/fetch_batch28_warmup.py:25` | `evaluate_batch19.fetch_prefix` → `engine_v2.cache.write_bar_cache` | 封存历史 warmup 不重跑；保留已有 parquet 与 manifest |
| `stockdb_factor_eval/minute_kurtosis_feasibility.py:34`，`minute_rank_memory_2025.py:263`、`minute_rank_memory_2026.py:263` | `init_stockdb` → 日历/分钟 `rd.vals` | 退出分钟渠道；AXIS 日线迁移不能证明分钟字段、单位与历史可见性已经替代 |
| `factor_workflow/filings_stockdb_universe.py:40` 的 `main` / `snapshot` | `_connect` → StockDB → 季末实际交易股票集 | 新历史上市/退市与公告覆盖分母经验证后退出；保留已冻结分母快照 |
| `work/c05_order_measurement_20261005/probe_child.py:29` | 原生 `stockdb.pyd` → 单次 `get_ticks(**frozen_request)` | 停止继续探测；保留 `native_receipt.json` 和有限 quote 样本，不能把它们称为完整订单源 |
| `factor_workflow/probe_akshare_contract_liability.py:16` | `akshare.stock_balance_sheet_by_report_em` → 离线财报规范化 | 退出 AKShare 探针；保留原始响应及版本信息，最新修订值不能反推历史 PIT |
| `factor_workflow/filings_akshare_index.py:106` 的 `discover`，`filings_pit_cli.py:114` 的 `fetch` | AKShare `stock_zh_a_disclosure_report_cninfo` → 公告索引 → PDF 下载及 SHA256 manifest | 公告时间、修订版本、历史退市 orgId 的新索引验证后退出发现渠道；直接下载原始 PDF 与离线 `extract/value_asof` 继续有用 |

外部 StockDB 安装、共享服务和整个旧工作区不属于这次代码退出清单的删除对象。新体系不得静默回退到上述旧渠道。原始数据、源文件、冻结配置、哈希索引和旧官方平台结果共同构成历史证据；compact 热记忆不能代替它们。`sealed_windows.json` 及封存 OOS 原始结果保持冷存储，不重新评估。

初步执行审计入口为 `panda_alpha.execution.audit_next_open`，与收益研究代理指标分离：

```python
from panda_alpha.execution import ExecutionPolicy, audit_next_open, reviewed_2026_rulebook

report = audit_next_open(
    raw_frame, factor_values, reviewed_2026_rulebook(),
    ExecutionPolicy(cycle=5, groups=10, direction=1, one_way_cost=0.003),
    calendar=verified_sessions,
)
```

`raw_frame` 需要 `date/symbol/raw_open/raw_high/raw_low/raw_close/raw_preclose/volume/trade_status/is_st`，还需要有历史证据的 `board/listing_state/security_status_verified/limit_reference_verified`。IPO 需要准确上市交易日序号；复牌、重新上市、退市整理和退市状态不能通过代码前缀或当前名称猜测。`factor_values` 为真正的每日期、每股票因子值面板；`calendar` 为独立验证且有序完整的交易日集，不能用源数据出现的日期压缩缺失交易日。方向为平台 0/1；30bp 与 50bp 成本为独立压力假设。

研究窗口是 2026-06-18 至 2026-09-18。窗口内沪深主板 ST 涨跌幅在 7 月 6 日从 5% 调整为 10%，已按日期分段。[上交所修订说明](https://star.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)、[深交所 2026 规则](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)

科创板/创业板 20%、北交所 30% 使用独立主来源规则。2026 北交所公开发行上市首日、退市整理首日的无价格涨跌幅限制例外已核对；分厘限价舍入未取得明确主来源，非整数 tick 的北交所限价保持 pending。详细规则来源、条款与适用日期见 [execution_rules_2026.json](D:/PandaAI-Alpha/QUANTAXIS/research_bootstrap/execution_rules_2026.json)；其中 SHA 是审阅摘要哈希，并非交易所原始文件字节哈希。[上交所 2026 规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml)、[北交所 2026 规则](https://www.bse.cn/jygl_list/200028217.html)

日线 OHLC 可以否定某些成交假设，不能证明我方开盘集合竞价排队位置和实际数量。缺准确开盘成交回执的订单保持 pending，即使当日成交量正常。`assess_next_open_order` 仅在带匹配日期、股票、方向、开盘价、数量及实际 JSON 文件哈希的经核实券商/交易所回执存在时标记 confirmed；部分成交不能充当完整请求成交。停牌订单 blocked；`mark_position` 可用已经知道的上一交易日 close 估值，但 `executable_exit=False`。缺失 stock-day、退市终值及未核实特殊规则保持 pending；收益 NaN 不填 0。

`audit_next_open` 只审计目标组的换仓意图，不能把上次目标组当作已成交持仓。确认订单回执也不直接放行经济验收：持仓、现金流、公司行动、交易数量规则、容量与实际费用仍需独立对账，因此报告不生成 NAV/Sharpe。专属测试覆盖生效日切换、不同市场、IPO 例外、北交所舍入缺证据、涨跌停排队、停牌估值、缺失日、退市缺口、回执哈希及部分数量；14 条通过。
