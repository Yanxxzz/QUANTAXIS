# Panda Alpha：AXIS 数据与轨迹进化研究

这个 Fork 新增 `panda_alpha`，将 PandaAI 研究的默认数据源改为 QUANTAXIS 兼容数据库，统一管理历史记忆、数值去相关、反思进化和官网实验预算。它使用独立的轻量依赖，不要求先部署上游交易、Web 或 Rust 服务。

```mermaid
flowchart LR
    A[QUANTAXIS 数据同步] --> B[覆盖/PIT/复权检查]
    M[历史热记忆与冷索引] --> C[多机制假设与轨迹]
    B --> C
    C --> D[本地代理评估与实际因子值去相关]
    C --> E[官网来源诊断/探索预算]
    D --> F[失败归因与证伪]
    E --> F
    F --> C
    F --> G[独立经济/组合/官网验收]
```

## 已实现的研究变化

- AST 规范化和参数族去重；机制、字段、算子多样化选拔；使用同日期、同股票的实际因子值计算每日绝对 Spearman 相关。常量、稀疏覆盖、缺少池成员因子值时保留 pending。不能用 IC、收益摘要或名字不同代替独立性。
- 候选记录假设、信息机制、字段需求、公式或多步 Python 代码、冻结方向、父轨迹、证伪方案和反思。进化支持跨机制交叉及显式 LLM 后端。源缺口、执行错误、相关性、成本失败和机制证伪分别处理。修复数据源后可凭哈希绑定的新证据与证伪方案重开原机制。
- 官网来源诊断、探索和正式验证分别分配预算。最终入池门槛不阻挡探索。余额、派发前预留、Factor ID、Run ID 和结算写入 SQLite；中断后只查询原 Run ID，模糊派发不自动重试。当前默认串行执行。
- 本地代理严格使用决策后下一交易日开盘成交、单边 30/50bp 成本及固定五日调仓。无法构造完整交易日历、持仓缺退出价格或原始未复权价格时不出收益标签。实际涨跌停、停牌、容量、退市和官网组合验收仍须独立核验。
- 历史累计检验分母保留 415；新尝试跨批累计，相同定义/方向/窗口的两档成本不重复计数。封存窗口检查覆盖 warmup、外部反馈和已有进化状态。

## Windows 首次配置

```powershell
# 在 Fork 根目录执行。按实际环境替换 Python 路径。
.\scripts\setup_axis.ps1 -Python 'D:\Anaconda\envs\open-webui\python.exe' -InstallDependencies -Start
Copy-Item config\panda-alpha.example.json config\panda-alpha.local.json
```

数据库仅监听 `127.0.0.1:27018`，文件位于 `.runtime/mongodb`，下载校验官方 SHA256，不注册系统服务。`.runtime`、个人配置、账号状态、实验原始结果与 SQLite 账本均被 Git 忽略。已有数据库可直接修改 `data.mongo_uri`，不重复启动。

现有 PandaAI CLI 可直接配置 `platform.cli_command`。为支持分离派发与查询及 Windows UTF-8，推荐在私有配置中设置工作 Python 和官方 CLI 的 `site_packages` 路径。账号凭据仍由官方 CLI 从用户自己的配置读取，不复制到 Fork。

```powershell
.\scripts\Invoke-PandaResearch.ps1 account
.\scripts\Invoke-PandaResearch.ps1 plan --count 10 --output research_runs\generation01.json
.\scripts\Invoke-PandaResearch.ps1 schedule --candidates research_runs\generation01.json
```

规划不扣官网算力。离线初始生成器提供历史有用方向与有限的跨机制候选，用于验证管线；它没有无限自主创新能力。自主多步研究使用 `ResearchEngine` 的 `PlanningBackend.complete_json(stage, context)` 接口。`OpenAICompatibleBackend` 接收显式 caller；CLI 的 `llm.enabled=true` 需要配置模型和 `PANDA_ALPHA_LLM_KEY` 等环境变量。密钥不写进配置或历史记忆。当前配置默认禁用付费 LLM 请求。

## AXIS 来源与验收

`scripts/axis_sync.py` 同步 QUANTAXIS 的 Mongo schemas。TDX 旧节点可能返回名单/除权数据而不给 K 线，所以节点验收必须实际查询日 K 线。`scripts/probe_axis_nodes.py` 和 `probe_axis_protocol.py` 是免费、只读诊断工具。

同步任务区分 `stock_day`、`stock_xdxr`、`stock_list` 和 `calendar`。股票样本同步、当前沪深列表、完整历史 A 股、退市股票、分钟数据与财务公告 PIT 是不同的证据层级。复权需要成功来源凭证，不能因为数据库有几行数据就标完整。

首次部署的 60 股验收与 3 股复权对照保留在 [`deployment_validation.json`](research_bootstrap/deployment_validation.json)。2026-10-05 已迁入公告索引 235,188 条、修订映射 20,825 条和三张财务简表 469,736 条。本轮修复 ETL 遗漏的更正映射嵌套提取：25 条可用提取中新增 23 条，2 条重复保留原人工链；财务提取版本从 541 增至 564，在已提供文档范围内核对的金额记录从 503 增至 526。来源索引、公告日期及报告期已核对，原 541 个 ID 均保留。原始 PDF 字节尚未全部本地重验，新增记录的修订链仍为 unreviewed，23 条首次可用日及窗口终点查询均受更正范围屏障阻断，不能称为完整 PIT。财务简表为本次取得的最新重述快照，全部 `pit_usable=false`；不能根据旧 `NOTICE_DATE` 把今天的数值回填到历史。

沪深来源元数据包含 5,561 个身份，目标窗口内 5,452 个，包括 231 个退市身份。全量同步合并断点完成 1,245 只（含 90 只退市）的行情与因子查询，取得 1,944,246 条对应原始行情；来源明确标记的 9,518 个停牌日已保留，未出现无法解释的缺失交易日。实际库存另含较早样本及中断前成功的部分查询，不能把库存行数等同于完整证券数。6 只已下载证券的 IPO 复权基准仍未认证。

北交所官方当前目录 348 只、18 页已全量取得；结合旧新代码和 5 份官方退出公告，保存 353 个历史身份（目标窗口内 349）。5 只退市/转板的实际退出日期已经核对原文 SHA。行情仅保留此前取得的 920002 共 563 日原始记录，后复权面板缺失；当前目录的 2026-09-30 快照不反推目标窗口内的历史状态，也不认证历史全集。

剩余行情目前受外部来源阻断：BaoStock 匿名登录返回 `10001011`，SDK 定义为 `BSERR_BLACKLIST_USER`；北交所行情接口连接失败。用户选择保留断点等待恢复。源级熔断停止新派发、保留成功数据与凭证；失败尝试另存，未访问证券不计作行情缺失。恢复后需显式 `--resume-after-source-unblock`，默认单 worker。完整历史股票池、全市场原始财务 PIT、分钟数据及实际成交验收仍为 pending，因此 `can_retire_legacy=false`。

首次迁移的冻结库存、归档和验收哈希见 [`migration_checkpoint_20261005.json`](research_bootstrap/migration_checkpoint_20261005.json)；本轮最新库存、财务增量、恢复探测和当前断点哈希见 [`data_completion_20261005.json`](research_bootstrap/data_completion_20261005.json)。BaoStock 单次恢复探测仍返回黑名单错误，北交所单次连接失败，两个既有 TDX 节点仅返回不可解析的日 K 载荷，本轮新增行情为 0。当前断点在 `research_runs/market_sync/checkpoint.json`；该次探测更新了断点元数据，首次发布的旧断点字节未留存，此限制已记入新报告，成功结果及原迁移报告仍保留。剩余沪深 4,207 只、6 只 IPO 复权基准、北交所历史与 HFQ、完整财务 PIT 和分钟数据仍待补齐。财务另缺 20 个前置公告的已验证金额、2019 年目标披露窗口及其他字段；不能把本轮局部股票数或旧缓存副本标为全市场覆盖。

```powershell
$env:PYTHONPATH = "$PWD\.runtime\python"
python scripts\axis_sync.py --source baostock --codes 000001 600000 600519 --start 2021-09-20 --end 2026-09-18 --uri mongodb://127.0.0.1:27018 --report research_runs\axis_sample_sync.json
.\scripts\Invoke-PandaResearch.ps1 coverage --codes 000001 600000 600519 --start 2021-09-20 --end 2026-09-18
```

`coverage` 输出逐能力阻塞项和 `can_retire_legacy`。新包没有 StockDB/AKShare fallback。只有对应覆盖、复权、历史股票池及需要的财务/分钟能力通过后，才可归档并删除原渠道；未验收时保留旧原始证据，不能把渠道换名当作缺失问题已解决。

```powershell
# 离线核实实际库存、来源凭证和剩余身份，不创建收益标签。
python scripts\verify_axis_migration.py --output research_runs\migration_acceptance.json
python scripts\migrate_financial.py --help

# 仅在外部来源已经恢复后显式解除本地熔断；成功下载记录会复用。
python scripts\axis_market_sync.py --start 2019-09-20 --end 2026-09-18 --workers 1 --resume-after-source-unblock
# 已下载但 IPO 基准缺证据的证券，另验收同源原生 HFQ 相对窗口。
python scripts\repair_axis_adjustments.py --start 2019-09-20 --end 2026-09-18 --resume-after-source-unblock
```

`verify_axis_migration.py` 是库存审计，不凭旧回执或记录数自行授权删除渠道。财务原始版本通过 `AxisProvider.financial_asof(code, report_date, decision_date)` 查询，报告期、可用日期及修订链分别保存；新公告尚未解析或修订范围不明时阻止沿用旧值。最新财务简表位于独立的 `stock_financial_provider_snapshot`。

旧离线行情迁入独立的 `stock_day_legacy_archive`，原始数值、文件 SHA 和逐来源清单保留；不覆盖新的 `stock_day`，不参与自动 fallback，也不把尚未证明的单位、复权或股票池语义升级为已验证。封存窗口内的数据只归档，不重新评估。旧入口清单与退出前提见 [`legacy_channel_retirement.md`](research_bootstrap/legacy_channel_retirement.md)。

```powershell
python scripts\migrate_legacy_prices.py --files ..\stockdb_factor_eval\cache\execution_bars_20210922_20260921.parquet ..\stockdb_factor_eval\cache\batch28_warmup_20200803_20210921.parquet
```

两份离线行情共 7,214,771 行，先复算文件 SHA、全量逻辑 SHA、schema、日期/证券分母和唯一键，再流式归档并保存行游标。原 source dictionary 连同 null/NaN/0 均保留；单位转换独立对照本机已验证的同日原始成交量/成交额。分层资金流和流通股本缓存另作带哈希的隔离清单，缺历史可见版本或字段映射的特征不进入新研究计算。

BaoStock 通过 QUANTAXIS 已支持的来源接口进入同一 schema。日行情保存原始价格，成交量从 shares 转换为 QA 的手数并保留 shares，成交额为人民币元。累计复权因子与交易日历独立保存。当前列表同步不能替代历史包含退市股票的股票池。

## 本地研究与反思

同族的来源缺失现在累计到 `source_failures`，调查计数 `attempts` 只统计非来源调查。连续来源失败保持 `new_source`，不会因第三次缺失触发调查上限或放弃；真实证伪、未来信息及非来源失败仍按原规则处理。候选自己的 `attempts` 保留全部反思事件数，用于追溯。保存状态升级为 schema 2，并记录计数范围及迁移事件。

加载旧 schema 1 时，只对完整轨迹证明全部为来源失败、经济判断仍 pending、且无历史机制封锁或证据重开记录的族修正旧计数和误封锁。真实否决、混合失败、轨迹不全或历史黑名单会保留原状态；已有旧状态文件不被直接覆盖，需新证据才能重开这类机制。

```powershell
.\scripts\Invoke-PandaResearch.ps1 evaluate --candidates research_runs\generation01.json --codes 000001 600000 600519 --start 2026-06-18 --end 2026-09-18 --output research_runs\local_review
.\scripts\Invoke-PandaResearch.ps1 evolve --parents research_runs\generation01.json --evidence research_runs\local_review\feedback.json --count 10 --output research_runs\generation02.json
```

三个股票只适合来源冒烟测试，不能通过默认至少 30 股票的统计/去相关门槛。正式研究应给完整可交易股票池和已登记池的 `--pool-values`。缺官方池因子值时，数值独立性保持 pending。

2026-10-05 的首轮工程初测固定了 300 股哈希样本（SH 62 / SZ 238，来源同步途中可用池 745，只用于管线验收）。窗口 2026-06-18 至 2026-09-18，HFQ，66 个完整交易日、19,800 条行情，缺失/重复/日历外记录为 0；6 个假设的方向在测试前冻结，累计检验分母为 415 + 13 = 428，本批新增 6，成本档不重复计数。

| 新机制 | 30bp 净累计代理收益 | 50bp 净累计代理收益 |
| --- | ---: | ---: |
| 短期反转 | -4.65% | -8.64% |
| 趋势效率 | -10.86% | -12.73% |
| 跳空恢复 | -7.39% | -10.23% |
| 流动性冲击 | +12.58% | +11.71% |
| 波幅压缩 | +3.61% | +0.35% |
| 参与度背离 | -2.07% | -3.19% |

不同公式的暖机时间和有效持仓日数不同；表中包含暖机期间空仓现金日，不能直接凭短样本排序或入池。真实因子值产生 15 对相关检验：10 对覆盖达标、5 对暖机覆盖不足；最大观察到的每日绝对秩相关均值 0.497，5 对 pending 不视作独立。既有官网池缺实际因子面板，增量独立性尚待核验。1,984 个目标订单全部 pending，未核实实际成交与现金流，代理收益不能作为可执行净值。脱敏摘要及完整面板哈希见 [`initial_test_20261005.json`](research_bootstrap/initial_test_20261005.json)。

```powershell
# 可重复的工程测试；本轮完整明细已保存在 research_runs/initial_axis_300。
python scripts\initial_axis_test.py --sample-size 300 --output research_runs\initial_axis_300
```

再次运行会取得当时的新来源池快照；保留首次 `sample.json`、`candidates_frozen.json`、来源凭证、完整因子面板、成本结果及报告，不能把两次样本变化当作同一实验。反思结果只写本次报告，不更新永久机制黑名单或官方池；本轮未使用封存 OOS、官网回测或付费 LLM。

本地公式解释器仅支持受限的已知算子，不执行任意生成 Python。复杂 Python 因子在平台沙箱测试。历史 F141/F174/F253 等原公式可能包含旧私有算子；只有字段映射、方向、PIT 和官网语义核验通过，才能成为可执行新定义。

## 官网预算边界

用户选择：优先使用当天赠送算力，充值算力每批确认。默认 `daily_credit_limit=10`、`recharge_credit_limit=0`。`planning_credit_estimate` 仅安排队列，不能保证账单。

已核对 CLI 0.1.7：未提供 gift-only 硬隔离或单次最大账单参数，`--timeout` 仅结束客户端轮询。`server_enforced_cap=false` 时默认不启动可能跨入充值余额的任务；需要当前定价/限额证据或针对具体批次的估计计费风险授权。不能把历史 2/4/6 算力收费伪装成硬上限。

```powershell
# 先查看具体定义、窗口、类别和预算；默认不派发。
.\scripts\Invoke-PandaResearch.ps1 dispatch --candidates research_runs\generation01.json --candidate-id F-EXAMPLE --category exploration --start 2026-06-18 --end 2026-09-18
# 加 --execute 才会执行，并仍检查当前预算授权。
.\scripts\Invoke-PandaResearch.ps1 resume --fingerprint EXACT_SAVED_FINGERPRINT
```

预览输出包含精确 `fingerprint`，绑定公式/Python 定义、方向、日期窗口、调仓周期和分组数。每批先向用户展示这些定义、指纹和额度；用户明确确认后，才在私有 `compute` 配置中填入当日授权。例如下面的指纹与日期都应替换为已确认批次的实际值：

```json
{
  "recharge_credit_limit": 5,
  "recharge_batch_authorization": {
    "day": "YYYY-MM-DD",
    "batch_id": "confirmed-batch-01",
    "fingerprints": ["EXACT_FINGERPRINT_FROM_PREVIEW"],
    "max_recharge_credits": 5
  },
  "allow_estimated_billing": true,
  "estimated_billing_batch_authorization": {
    "day": "YYYY-MM-DD",
    "batch_id": "confirmed-batch-01",
    "fingerprints": ["EXACT_FINGERPRINT_FROM_PREVIEW"],
    "max_total_credits": 10,
    "accept_estimated_charge_risk": true
  }
}
```

充值授权额度须覆盖配置的 `recharge_credit_limit`；估计计费风险额度须覆盖 `daily_credit_limit`。二者同时启用时须使用同一批次 ID，每个新任务都必须属于对应指纹清单。修改定义或窗口后需要重新确认，过期授权和单独的 `allow_estimated_billing=true` 都不能派发。风险授权表达用户接受估计账单的范围，不能替代平台硬限额；默认充值额度仍为 0，估计计费仍关闭。

延迟结算、跨午夜、扣充值余额或超出预留金额会保持账本锁定，防止追加消费。零收费失败和其他模糊账单可用 `platform.settle_receipt` 附匹配 Run ID、核验人和真实账单文件哈希核销。余额差只作为串行观察，不能替代官方流水。

## 历史 compact

- `research_bootstrap/memory.json`：约 98 KB 热记忆，重点方向、冻结方向证据与失败规则。
- `memory.md`：人类可读摘要。
- `evidence.jsonl`：4,843 条去重因子证据，另有 34 条来源阻塞，按 evidence ID 检索。
- `source_index.jsonl`：967 个来源的相对路径及 SHA256。
- `pool.json`：脱敏历史池参考，当前实际生效池及后续替换仍须官网核对。

```powershell
python -m panda_alpha compact --workspace D:\PandaAI-Alpha --output research_bootstrap
python -m pytest tests -q
```

原约 10 GB 研究数据留在私人工作目录；compact 压缩的是常用研究上下文，保留来源追溯，未丢弃原始数据。旧门槛失败、来源失败、执行失败、官网验证与代理回测分开记录，不能把它们全部变成永久机制黑名单。

## 上游与方法来源

规划、变异、跨方向交叉和轨迹反思参考 [QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha)。其 [runner](https://github.com/QuantaAlpha/QuantaAlpha/blob/main/quantaalpha/factors/runner.py) 的部分数值去相关被关闭，因此本 Fork 独立实现因子值去相关。上游实验收益不是本 Fork 的效果证明。

数据 schemas、除权公式及来源接口核对 [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS)、[除权算法](https://github.com/yutiansut/QUANTAXIS/blob/master/QUANTAXIS/QAData/data_fq.py) 和 [BaoStock 适配](https://github.com/yutiansut/QUANTAXIS/blob/master/QUANTAXIS/QAFetch/QABaostock.py)。PandaAI 派发桥调用用户已安装的官方 CLI 库，不复制账号凭据或私有源码。
