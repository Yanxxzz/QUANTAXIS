# Panda Alpha：四角色因子研究

本项目在 [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS) 上构建 PandaAI 因子研究工作流。两个研究员探索不同经济机制，独立证伪员提出竞争解释，负责人统一冻结定义、分配算力和推进任务。

**默认离线。只有取得明确、有限的研究与资金授权，完成角色审查和版本冻结后，才可激活官网试点。** 工作流不调用付费 LLM，不自动改变正式因子池；合成演练、官网执行成功和因子经济有效分别验收。

## 四个角色

- **lead：研究负责人。** 分配任务与预算，仲裁重复机制，冻结研究版本，维护共享状态，通过现有执行网关派发和恢复任务。
- **market：交易与价格发现研究员。** 探索交易行为、量价信息及价格吸收机制。
- **fundamental：经营变化与资本配置研究员。** 探索经营信息、盈利质量和资本配置机制。
- **skeptic：独立证伪员。** 先登记替代解释和失败条件，再审查结果及其证据范围。

角色配置位于 [.codex/agents/](.codex/agents/)。角色提示词用于分工；它们不构成进程、文件系统或账号权限的强隔离。

两个研究方向并行准备，官网任务统一串行执行。官网原生回测承担主要经济验证；本地负责免费兼容性检查、既有结果复用、成本归因与证据复核。协作必须形成“假设 → 挑战 → 答复 → 冻结 → 实验 → 复核 → 决定”的闭环。

## 离线运行

推荐 Python 3.11。研究层不要求部署上游交易、Web 或 Rust 服务。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-panda-alpha.txt
.\.venv\Scripts\python.exe -m panda_alpha.team demo --output research_runs/team_demo
```

`demo` 使用合成数据和离线执行替身，演练任务、消息、版本冻结与预算计划；不读取真实账户，不派发真实 Run。演练通过只说明协作管线可运行。

命令帮助：

```powershell
.\.venv\Scripts\python.exe -m panda_alpha.team --help
```

任务板使用 SQLite；`status` 查看状态，`packet` 生成角色工作包，`submit`、`message`、`freeze` 推进真实角色交接，`plan` 列出资源计划与待办。明确授权后由 `activate` 启用有限试点，`dispatch` 派发，`resume` 查询原 Run 并结算，skeptic 独立审查结果后由 lead 决定下一步。`pause` 停止新增派发。参数与恢复流程见[协作说明](docs/team_workflow.md)。

## 维护入口

- [协作流程与正式试点边界](docs/team_workflow.md)：角色交互、请求协议、预算分配及恢复。
- [架构与维护边界](docs/architecture.md)：维护核心、研究组件、历史运行脚本和上游保留层。
- [官网研究与预算](docs/official_compute.md)：累计配额、资金授权、预留、结算和阶段复盘。
- [正式入池规则](docs/pool_admission.md)：经济验证、成本、稳定性及实际组合增量。
- [已有研究与操作记录](RESEARCH.md)、[公共研究工具](docs/panda_alpha_workflow.md)、[数据渠道](docs/data_sources.md)、[证据存储](docs/research_storage.md)。

共享任务板与既有实验账本并存。团队预算不创建虚拟子账户余额，也不重置已有研究轮消费；资金范围授权必须经现有预算检查派生精确实验许可。实际扣费与预留共同占用额度，模糊派发只恢复原 Run。研究额度不是收费授权。

需要增加原研究轮总额时，使用 `amend-budget` 读取快照、预览并按明确授权追加审计记录；保留全部旧作业与用量，且不激活 grant。操作与请求格式见[额度扩展](docs/official_compute.md#同研究轮额度扩展)。

每次官网创建和启动前重新核验授权的有效期与暂停状态；授权过期或被替换就停止新的远端动作。已有 Run 仍可查询和结算，不能因恢复请求而重复创建或启动。一次有限试点的额度、截止时间和资金来源保存在对应授权中，不成为公共默认值。

历史原件、协议、结果和失败原因保留。研究准备、执行成功与正式入池是不同状态；来源缺口不能登记为经济证伪。

## 验证与上游

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_public_research.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

公开源码检查与合成测试不替代真实来源和因子质量验收。上游说明保存在 [README_UPSTREAM.md](README_UPSTREAM.md)，版权与许可见 [LICENSE](LICENSE)。上游源码、研究组件及历史脚本的路径保持可追溯。
