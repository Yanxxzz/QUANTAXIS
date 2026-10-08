# 统一研究数据入口

QUANTAXIS 管理统一研究接口和资料库。`config/panda-alpha.local.json` 的 `data.source_profiles` 登记渠道，`active_market_source` 决定默认行情。`--source` 可显式选择另一来源；一段研究窗口使用同一价格来源，价格与复权证据必须同源。身份、日历、行业和财报从配置的 `reference_database` 读取。

本机渠道分工：

- StockDB：本地日线主渠道。经来源契约、独立价格/成交单位对照和逐行检查后，进入 `quantaxis_stockdb_research`，用于本地研究代理。原始快照和同源复权键清单保留哈希。
- BaoStock：原始行情对照、已有日历和证券身份。黑名单分支保留断点，显式恢复。
- AKShare 腾讯：明确股票与窗口的补缺候选，保留隔离库、原件和旧断点。研究入口不自动切源。
- TDX：明确补采与独立价格对照，隔离库为 `quantaxis_tdx`。
- 巨潮/交易所公告：历史财报版本、披露时间及更正关系。行情渠道中的最新财务快照不替代原始财报 PIT。

## 日常命令

本机先启动 StockDB；已有 Windows Mongo 可用 `powershell -File scripts/start_data_services.ps1` 恢复。该脚本只在本地端口未监听时启动已安装的数据库，不下载或重置。

```powershell
python -m panda_alpha data-status
```

本机在来源配置的 `sync_plan` 中保存代码范围、SDK、来源契约、窗口和输出目录之后，日常同步可直接使用 `python -m panda_alpha data-sync --source stockdb`；命令行参数只在需要明确覆盖计划时提供。

首次 StockDB 接入需要哈希绑定的来源契约 `acceptance.json`、匹配 SDK 和明确代码范围。本机契约包含独立原始价格/成交单位对照、接口文档及 SDK SHA256，保存在私有研究目录。

```powershell
python -m panda_alpha data-sync --source stockdb --codes-file scope_codes.json --start 2021-09-20 --end 2026-09-18 --sdk-dir PATH_TO_PYBAO --acceptance acceptance.json --output research_runs/stockdb_research
```

采集按月保存原始完整投影，独立连接复查代码/日期集合和首末日。已完成月份复用原件。失败暂停并保留进度；核验恢复后使用相同参数及 `--resume-after-source-unblock`。供应包变更需要新采集批次，保留旧协议。价格/单位异常及零成交记录单独隔离，不制造停牌或零收益。累计复权因子只用研究终点以前的事件，前复权在窗口终点重定基准。

准备研究输入，不计算因子、不增加经济实验：

```powershell
python -m panda_alpha data-export --source stockdb --codes-file scope_codes.json --start 2025-08-14 --end 2026-09-03 --output research_runs/prepared_market
python -m panda_alpha coverage --source stockdb --codes 000001 600000 600519 --start 2025-08-14 --end 2026-09-03
```

导出按 100 个证券分区保存 Parquet、来源选择、覆盖和 SHA256。`evaluate` 与 `initial_axis_test.py` 使用相同来源配置；仍检查交易日历、封存窗口、既有池和实验登记。

## 验收范围

StockDB 来源契约和研究快照用于本地代理，不认证官网价格/股票池等价、完整历史全 A、分钟或财报 PIT。来源完整查询、有效报价覆盖、可交易状态分别记录。换行情源不解除财报版本、更正和披露时间屏障；未通过全量验收时保留旧原件和渠道。

原采集脚本继续处理已冻结任务和旧断点。日常先看 `data-status`，再显式选源；不要把不同渠道同码价格直接写入主库填洞。
