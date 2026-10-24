# 接入自己的回测平台

QuantFactra 把回测平台作为执行适配器。研究工作流、实验身份、预算、科学登记和证据审查继续复用；平台的任务语言、状态、计费和原始报告由适配器处理。

内置真实平台为 PandaAI。`quantfactra-v1` 是新增中立合同，已用持久化离线适配器验证；其他官网需要使用者实现并验证自己的 SDK/API 接入。

## 先选一种合同

- `pandaai`：内置 PandaAI 适配器，原钱包、数字状态、预检和报告路径保持。
- `module:factory` + `contract: quantfactra-v1`：新适配器推荐入口，使用文字状态、独立成本单位和中立报告。
- `module:factory` + `contract: pandaai-v1`：兼容早期插件；仍需要原钱包/状态/报告格式。

省略 `backend` 时仍选择原 PandaAI。新接口不会重写旧任务定义、指纹或累计账本。

## 运行一个完整例子

```bash
python -m quantfactra demo --output research_runs/adapter_demo
```

参考 [DemoAdapter](../quantfactra/adapters/demo.py) 的八个方法和[对应合同测试](../tests/test_quantfactra_backend.py)。演示使用文件保存状态，并实际覆盖创建、启动、换客户端恢复和零费用回执结算。它只支持明确声明的演示公式，不做投资回测。

## 配置自己的插件

在完整研究配置的 `platform` 项中指定已安装的 Python 工厂：

```json
{
  "backend": "my_backtest_adapter:build_client",
  "contract": "quantfactra-v1",
  "connection_id": "my-research-workspace",
  "cost_unit": "credits",
  "endpoint": "https://your-backtest-service.example",
  "api_key_env": "MY_BACKTEST_API_KEY"
}
```

`my_backtest_adapter` 是你自己实现的模块，不是内置或自动下载的软件。工厂 `build_client(platform_config)` 只构造客户端；模块导入与构造阶段不访问服务。凭据由适配器读取环境变量或本机凭据存储。

公开类型位于 `quantfactra.NativeExecutionBackend`。`create_backend(config['platform'])` 负责加载与检查接口，并在每次方法调用前执行宿主的暂停和截止检查。

## 八个方法的职责

1. `balance()`：返回账户当前可用额与真实计费单位；免费服务可明确返回 `available: null`。
2. `preflight(candidate, window, cycle, groups)`：验证本平台实际接收的定义、语言、字段和参数，返回本地预检文件的路径与 SHA256。
3. `create(candidate, window, cycle, groups, name)`：仅创建一次精确定义，返回对象 ID。
4. `start(factor_id)`：仅启动一次并保存 Run ID；丢失响应时不能自动重试。
5. `status(run_id)`：返回 `pending`、`running`、`succeeded`、`failed` 或 `cancelled`。
6. `result(run_id)`：把原平台结果转换成 `quantfactra-report-v1`，保留原件绑定及平台出处。
7. `billing(run_id)`：返回已确认计费回执的路径与 SHA256；账单尚未知时返回 `None`。
8. `logs(run_id)`：返回原 Run 的日志、`complete` 标记与 `errors` 列表。

ID 会成为缓存文件名，需使用字母、数字、点、下划线或连字符，首字符为字母或数字。平台原始 ID 若不符合该格式，在适配器内部保留一个稳定映射。

## 余额与费用

中立余额不需要伪造赠送/充值钱包：

```json
{
  "schema": "quantfactra-balance-v1",
  "available": null,
  "unit": "credits",
  "billing_mode": "free"
}
```

`compute.billing_mode` 必须与观察一致，取 `free` 或 `prepaid`。`cost_unit`、余额 `unit` 和账单 `unit` 必须相同。不同单位不能混进原实验账本；换单位不能清除或重标旧研究用量。

平台点数没有自动等价关系。若不同供应商都把点数叫 credits，应使用能区分它们的单位标签或独立授权账本；本版本不进行换汇或跨平台点数换算。PandaAI 原账本默认单位仍为 credits。

免费服务仍保留一个正数的规划预留，用于限制未结算任务和实验次数；最终费用只能由明确零费用回执结算为零。`None` 账单不会释放预留，也不会创建第二个 Run。

预付费服务使用既有每日/批次/累计研究上限，必须有明确的 paid-credit 授权。兼容字段 `recharge_credit_limit` 与 `recharge_batch_authorization` 在这里表示付费额度许可，不代表平台存在 PandaAI 式充值钱包。估算费用还须有原 `estimated_billing_batch_authorization`；配置来源明确的服务端硬上限可沿原 cap 路径。实际超限或免费服务出现非零账单时进入人工可核验的 `BILLING_REVIEW`，保留预留。

## 预检与账单原件

方法返回的文件绑定统一为：

```json
{"path": "/absolute/path/receipt.json", "sha256": "文件内容的真实 SHA256"}
```

预检原件至少包含：

```json
{
  "schema": "quantfactra-preflight-v1",
  "fingerprint": "精确定义、窗口、周期、组数的原指纹",
  "passed": true,
  "fields": ["close"]
}
```

账单原件至少包含：

```json
{
  "schema": "quantfactra-billing-v1",
  "backend_identity": {"backend": "my_backtest_adapter:build_client", "connection_id": "my-research-workspace", "contract": "quantfactra-v1", "cost_unit": "credits"},
  "run_id": "owned-run-id",
  "status": "succeeded",
  "unit": "credits",
  "actual": 0,
  "confirmed": true
}
```

`confirmed` 由适配器根据真实平台回执确定。字段和哈希校验只证明身份及文件一致性，不会独立证明供应商或费用记录的真实性。

## 中立报告

`result()` 返回 `schema: quantfactra-report-v1`，绑定原 `run_id`、`fingerprint`、`backend_identity`、`window`、`cycle` 和 `groups`。主要内容为：

- `source`：原平台名称与原始报告引用。
- `raw_artifacts`：原始报告文件绑定；真实平台的转换报告必须保留原件。
- `series.dates`、`series.groups`、`series.ic`、`series.rank_ic`：日期及分组/相关序列。
- `metrics.groups`：每组的 `group`、`annualized_return`、`excess_annualized_return`、`max_drawdown`、`turnover`、`sharpe`。
- `code_sha256`：平台实际部署代码的哈希，若确实取得。
- `synthetic`：演示/合成结果必须为 `true`。

单位、成本与口径由原报告明确声明。无法取得的字段保留缺失，不用虚构值补齐。结构检查会分别记录交付完整性、来源待核与经济结论，不能由一个成功 JSON 自动证明收益或 PIT。

## 身份与恢复

新任务绑定后端入口、`connection_id`、合同版本与成本单位。重复派发、任务板接管和恢复均先比较身份，再访问原平台。改平台、账户、合同或单位后不能把原 Run 发到新连接，也不能借此自动重跑同一任务。

`connection_id` 是公开、稳定、非敏感的账户/工作空间标签，不是密码或 API key。标签必须如实代表目标连接；代码检查不能证明两组凭据属于同一账户。

原 PandaAI 任务继续视为默认身份；原 `panda_alpha.platform.PandaClient`、原钱包、原预检和原报告原语保持兼容。数据供应商与模型也分别配置，不随回测平台自动替换。

## 验收顺序

先完成本地预检、持久化、模糊响应与接口测试，再用明确有限授权验证一次真实创建、启动、原 Run 恢复、报告和账单。合成演示不代表第二个真实平台已接通。
