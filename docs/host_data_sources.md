# 宿主自动读取免费数据

使用原四角色宿主、同一有限会话和累计预算。角色没有任意执行或联网工具；操作者明确选择的数据请求由协调器在交接边界执行。取数成功仅表示生成了可复核的外部输入，不自动通过原生来源、历史 PIT、经济或入池审查。

## 配置

可选依赖安装到新部署使用的解释器，已有部署继续使用其冻结解释器：

```powershell
python -m pip install -r requirements-panda-alpha-akshare.txt
```

在新的 host-init 会话 JSON 中添加 data_requests，至多两个请求。每个请求固定最多 128 只证券、最多三年的日期范围；不得由模型改成全市场扫描或扩大窗口。下面是财报当前快照示例：

```json
{
  "data_requests": [{
    "id": "financial-witnesses",
    "kind": "akshare_financial",
    "codes": ["000039", "600006"],
    "start": "2025-12-31",
    "end": "2026-06-30",
    "akshare_version": "1.19.1",
    "timeout_seconds": 180,
    "query_timeout_seconds": 60
  }]
}
```

stockdb_market 使用同样的日期、证券和限额字段，另加 sdk 对象，包含操作者已有本地 stockdb.pyd 的绝对 path 和 sha256。它只读连接 127.0.0.1:7899 的日 k 数据，用独立连接重复核对 date/code 身份投影，同时通过固定 AKShare/Sina 接口获取交易日历。不会安装数据库服务、变更账户、写入行情或自动寻找凭据。

按既有 service-prepare 和 host-control 启动。会话原截止和操作暂停同时约束取数，无需额外计划任务。request 的配置一旦持久化不可更换。

## 回执与恢复

产物存于实验账本旁 data-captures/，以会话和请求内容哈希定位。每个证券查询至多两次，每次保留原输入、输出、HTTP 回执（如有）和错误；单查询上限 90 秒、每请求上限 900 秒。协调器中断后复用已完成的查询；在结果已写出、提交回执尚未完成处恢复，不重新抓取。已有文件的哈希变化会阻止继续。

正常停止会终止所属取数进程。协调器意外退出时，取数子进程检测父进程身份并退出；进程出生时间防止误杀复用 PID。恢复沿用原请求截止和尝试数，过期或失败的请求不会偷偷更换输出目录重试。失败显示 data_source_failed，模型和官网派发都停在该边界；修复需要明确的新材料或新的有限请求。

data_material 向角色传递采集状态、范围、质量摘要、规范化文件绑定及完整回执哈希；原始大表保存在回执，不灌入提示词。模型只能据已提供内容判断，文件路径不能替代已读原件。

## 数据口径

- 市场输入：固定证券 × 独立交易日历形成完整网格；缺证券日和整个交易日缺数均保留 NaN，另列 source_row_present。未复权外部价格不代表 PandaAI 原生输入或历史可交易集合。
- 财务输入：保存报告期、提供方公告/更新时间、合并范围、币种和元单位。当前快照设置 historical_backfill_allowed=false；不把当前公告日期当作旧值首次可得时间，也不覆盖既有原公告版本。
- StockDB、AKShare 和 PandaAI 是不同来源。外部共同支持、代码兼容或数值一致不能单独证明旧官网 Run 逐键相同，也不免除修订边界和部署适用性核验。
- 取数不创建官网 Run、不激活 grant、不改变累计试验分母。来源通过后的官网验证仍走原算力管家、预算和原 Run 恢复流程。

## 验证

```powershell
python -m pytest tests/test_team_data_sources.py tests/test_team_sources.py tests/test_team_lifecycle.py -q
```

离线测试拦截网络，覆盖整日缺数、财务快照边界、停止、部分查询恢复、原截止和哈希变化。真实免费数据验收与模型研究结果另行记录，不能用这些夹具代替。
