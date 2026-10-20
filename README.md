# Panda Alpha：用有限资源研究好因子

目标是提高 PandaAI 官网比赛积分，找到经得起复核的优质因子。复用 [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) 的取数、回测和执行能力；本地先筛，必要时官网验证。保留成本、样本和失败证据，避免把一个漂亮指标当成目标已经实现。

**日常只需要一个聊天入口。** 告诉 Codex 研究目标、预算、尝试上限及绝对截止，即可启动有限研究。默认一个主研究员、按需独立 Skeptic；计划、固定检查、预算和结案由程序完成。打开聊天、登录或重启电脑不会自动运行，不自动修改正式池。

## 先跑通离线演示

推荐 Python 3.11；3.12 也在 CI 验证范围。研究层无需部署上游 Web、交易、Rust 或 Docker 服务。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-panda-alpha.txt
.\.venv\Scripts\python.exe -m panda_alpha.team --help
.\.venv\Scripts\python.exe -m panda_alpha.team host-demo --output research_runs/team_demo
```

演示使用合成角色和隔离账本，覆盖协作及中断恢复，不调用真实模型或官网。输出目录须尚不存在。

## 一次真实研究怎样推进

1. **选一个机制并冻结。** 先查看原始账本、代码、失败和可用数据，固定定义、方向、窗口、分组、成本、推进与停止条件；读取收益后不反号、换窗或改名挽救。
2. **免费检查和本地筛选。** 先检查研究窗、实际暖机窗和封存范围，再取数及读取标签。StockDB／AKShare、独立交易日历、数值与原生预检，以及 `study.py` 的持仓账本负责实现、同样本对照和成本分析。模型不重复执行固定检查。
3. **只审会改变决定的问题。** 本地证据明确弱时停放；值得推进或存在关键歧义时，交独立 Skeptic。默认主研究员 `high`、审查 `xhigh`，最多16次真实 worker 尝试含重试，按任务隔离会话；主聊天也计入共享额度。
4. **必要时官网。** 输入难以免费取得时，写清缺口和一次 Run 能回答的问题；通过来源、代码及资金门槛后才派发。优先核实过的活动算力，为同代码确认保留预算。已有 Run 按原 ID 恢复，不重复创建。
5. **记录决定并停止。** 原报告、代码、实际扣款和失败保留。区分“有研究价值”“待影子验证”“可提交入池审查”；满足停止条件或截止即停止。

本地有限样本不能代替全市场官网表现。外部数据不冒充官网实际输入；汇总 Sharpe 不冒充真实净成本或现池增量。财务报告期、公告边界及历史 PIT 门槛保持。

探索积分与正式入池分阶段判断：缺少真实池共同账本不能写成“因子已经失败”，也不能宣称组合增量通过。正式入池使用 [v3 准入政策](config/pool-admission.v3.json)；每次研究的推进条件在当次协议冻结，不把旧研究阈值套成所有新因子的统一规则。

## 启动、停止和恢复

Codex 先核对权威账本与旧部署，再通过 `host-init`、`service-prepare` 准备新有限会话。人只需在聊天明确启动；底层统一使用：

```powershell
python -m panda_alpha.team host-control --deployment DEPLOYMENT.json --action status
python -m panda_alpha.team host-control --deployment DEPLOYMENT.json --action start
python -m panda_alpha.team host-control --deployment DEPLOYMENT.json --action stop
```

官网部署准备时绑定执行配置，启动时另带 `--execute`；该标志不激活或扩大资金授权。普通查询不联网、不启动模型。停止关闭本机派发和轮询，不表示远端取消或退款。

超时和断线沿原工作、模型会话、截止及累计次数恢复。完成或过期会话保持终态；未知创建状态先对账。**只维护聊天启动，不保留 Windows 登录／分钟自启安装器。** 详见[操作说明](docs/chat_start.md)和[宿主恢复](docs/unattended_host.md)。

## 维护者先看哪里

- **流程与协作**：`panda_alpha/team/`。`host.py` 决定下一步，`coordinator.py` 连接执行，`operator.py → service.py → runtime.py` 分别管理聊天命令、部署生命周期和子进程恢复。
- **官网与预算**：`platform.py`、`research_budget.py`。原实验账本记录预留、真实 Run、扣款与共享累计；配置示例不是当前授权或余额。
- **本地证据**：`data.py`、`study.py`、`execution.py`、`native_preflight.py`。复用已有计算内核，不另造回测器。
- **科学记录与准入**：`registry.py`、`memory.py`、`quality.py`、`pool_admission.py`。保留定义、失败、待核及证据替代；研究质量与正式准入分别判断。
- **入口与材料**：`config/` 是模板，`tests/` 是软件回归，`docs/` 是专题说明。一次候选留在运行目录，不自动增加常驻模块。

目录取舍、修改顺序与验证范围见[架构和维护地图](docs/architecture.md)。上游源码及历史组件供复用和复现，不是日常启动步骤；上游说明见 [README_UPSTREAM.md](README_UPSTREAM.md)，许可见 [LICENSE](LICENSE)。

权威状态包括实验账本、科学登记及原件。任务板与实验账本共库，JSON 包是带哈希的视图和断点，不另维护余额或裁决。私有账户配置、行情和模型原文不提交 Git；清理针对可再生缓存和已校验的重复副本。见[证据存储](docs/research_storage.md)。

## 检查改动

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_public_research.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

CI 的行为回归集中在一份工作流，覆盖 Linux 3.11／3.12 和 Windows 3.11；包装工作流检查 sdist、wheel 和安装后的入口。研究分支由 PR 检查，主线与版本标签检查 push，避免同一分支重复跑两套。Docker 仅在相关文件变化或手动请求时构建；上游发布保持手动。软件通过不能证明因子质量。

## 研究记录与当前边界

截至2026-10-21，[H20换手集中度研究](docs/competition_focus_research.md)已停止：唯一官网主窗方向RankIC **0.045648**、预选group1 Sharpe **0.8361**、报告年化超额 **+9.02%**，通过事前报告推进门槛。早期确认的暖机与封存区间冲突，被离线拦截；保留全部失败及部分输出，不换窗。当前有研究价值，尚未确认、影子验证或入池，净成本与来源待核保持。

本夜7次worker尝试、1次官网Run、记账10点活动算力，充值变化0；累计官网34次／82点，研究轮余22点／5次、未结算0，全部所属进程已停止。**余额不构成新的启动许可。** 旧 A/A控制/B 待核6/0/10保留，原因见[来源审视](docs/research_depth_audit.md)。官网不提供逐日输入或参与股票导出，不反复索取不存在的接口。

[负协偏度](docs/coskewness_research.md)、[同伴传导](docs/peer_diffusion_research.md)及 [RESEARCH.md](RESEARCH.md) 中此前研究的失败保留，不据本次主窗覆盖旧结论。

后续结果集中在 [RESEARCH.md](RESEARCH.md) 与冻结运行目录，不把每日过程逐段堆回入门说明。精简流程见[lean 研究](docs/lean_research.md)，原生约束见[预检契约](docs/panda_alpha_native_contract.md)及[日期表示](docs/native_date_wire.md)。
