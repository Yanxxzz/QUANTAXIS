# 登录后免费来源复核（2026-10-18）

本次只读官网数据文档并免费导出字段字典，没有订阅数据或启动官网回测，核验前后余额未变；研究授权剩余 46 点继续冻结。字段字典及原始记录保存在私有证据目录，以下为可公开复核的口径摘要。

## B：新增字段证据

[财务季度接口](https://www.pandaaiquant.com/docs/api?id=35)和[回测因子接口](https://www.pandaaiquant.com/docs/api?id=25)导出的资产负债表字典，对下列字段给出相同名称：

| 字段 | 名称 | 两份文件的工作表行号 |
| --- | --- | --- |
| `bs_net_fix_assets` | 固定资产净额 | 36 |
| `bs_total_fix_assets` | 固定资产合计 | 37 |
| `bs_cip` | 在建工程 | 39 |
| `bs_cip_total` | 在建工程合计 | 40 |
| `bs_total_assets` | 总资产 | 78 |

回测字典还说明：财务衍生字段以 `_mrq_n` 表示相对查询日的前 n 期，原字段表示第 0 期。这支持“字段存在季度序列”的解释，但不是历史公告、修订版本及可用时点的保证。

两份字典均未列出已执行 B 的 `construction_in_progress`、`net_fixed_assets`、`total_assets` 裸名称。不能由中文语义相近就自动建立别名映射，也不能把净额和合计互换。冻结原请求保持不变。

财务 API 要求起止季度，`date` 限定公告日期上界，`is_latest` 默认取最新披露；`if_adjusted` 表示当期/非当期财报，不能当作修订版本号。页面示例对同一股票同一季度给出多个公告日期以及重复记录，说明还需明确选期、去重、修订与可用时点。它不是 B 三个实际输入值的 PIT 见证。

文件 SHA256：财务字典 `5029ac7ac7c02caf238a96c6d1efb642e5898b2bf2c901c8602b6154099ec9c0`；回测字典 `a2ad54715ff2b9635a8a6dd3baa97aa95c7db32610abb1b300539c229b40c00e`。未把完整官方字段文件重新发布到仓库。

## A：更新时间与原生时钟仍须区分

[日线 API](https://www.pandaaiquant.com/docs/api?id=156)展示更新区间 16:15–16:35；回测因子 API 展示 05:27–05:47，财务季度 API 展示 04:31–04:51。页面未在这些标签中说明时区、历史 `available_at` 或具体回测 worker 的快照绑定。日线和回测因子属于不同接口，不能从日线更新时间推定原生 close/turnover 的使用时点。

A 的原始逐形成日有限值键、共同支持集与对照 S 的同快照导出能力仍未取得。原报告首标签与本地日期的差异也没有新原件解决。SDK 的接口说明不能替代原 Run 的输入及 worker 证据。

## 下一步门槛

- A：取得原 Run 的输入时钟、形成日支持键及 worker 版本绑定，并免费确认 S 的同快照/共同支持导出可行性。
- B：确认三个裸字段与上述资产负债表字段的实际映射、单位和同报告期关系，取得有界公告/修订样本及历史可用时点，再绑定执行 worker。

页面将三个数据接口标为未购。本轮没有尝试订阅或付费数据调用；已能免费确认的字段分类不升级成原生来源通过。A/B 均保留 source_pending，经济结论未评估。

## 登录服务实现期间的免费补核

继续只读[数据概述](https://www.pandaaiquant.com/docs/api?api=data_overview)、[常见问题](https://www.pandaaiquant.com/docs/api?api=data_faq)和[报告预披露接口](https://www.pandaaiquant.com/docs/api?id=145)，没有调用数据接口。概述列举可选指数股票池，但没有给出 A 原 Run 的逐形成日有限值键或快照导出承诺；常见问题主要是状态码，不能补足原生时钟证据。

报告预披露接口把 `end_date` 定义为报告期，分别提供 `expected_disclosure_date`、`actual_disclosure_date`、三次变更日期、`first_publish_date` 和 `latest_predisclosure_date`。示例中同一股票 2024 年年报的报告期为 20241231、实际披露日为 20250315，而 `first_publish_date` 为 20241231。因此不能把预披露表的首次发布日期当成财务报表值的公告时间，更不能据此推导回测 worker 当时已经拿到数据。

该页面也标为未购，更新区间 22:30–23:30；没有 B 原字段值、修订版本或 `available_at` 样本。下一步可把实际披露日作为交叉检查线索，但仍须取得 B 实际输入的同报告期、公告/修订和可用时点见证。A/B 保持 pending，46 点不动。

## PR #7 合并后的公开指南补核

免费读取[因子开发](https://www.pandaaiquant.com/docs/factor-development)及[因子分析](https://www.pandaaiquant.com/docs/factor-analysis)，并用无登录 GET 将公开文章与哈希留存在私有目录。开发页给出 `Factor.calculate` 和 `factors['close']` 示例，但没有说明暖机输入起点、历史可用时间或原 worker 身份。分析页将“最新数据”限定为最近一期股票及因子值；它不等于原 Run 的完整历史支持集，也没有定义 A 报告日期标签与形成/持有日期的精确映射。

这两份指南没有解决 B 三个裸字段的实际别名、同报告期选择或修订/PIT。财务口径仍以此前字典和公告日期文档的已证范围为限。不能由新的页面哈希推导出新的经济信息或来源资格。

[官方帮助页](https://www.pandaaiquant.com/docs/support)指向官方小助理微信和社区问答；操作者选择自行通过已有客服渠道联系。已备好[不含账户、Run ID 或公式的技术问题草稿](provider_evidence_request.md)，尚未收到回函，也没有代发帖子或设置算力悬赏。后续回函通过受控接收入口进入原任务重审。
