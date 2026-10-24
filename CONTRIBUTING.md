# 参与 QuantFactra

优先欢迎能帮助开发者首次跑通、接入平台或理解研究过程的贡献。无需先理解全部上游交易服务。

## 建立开发环境

```bash
git clone https://github.com/Yanxxzz/quantfactra.git
cd quantfactra
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e .
python -m quantfactra demo --output research_runs/contributor_demo
```

Windows 可直接使用 `.venv\Scripts\python.exe`。源码、文档与合成示例可以公开；账户、凭据、原始行情/财报和真实研究账本留在被忽略的运行目录。

## 选择一个入口

- 文档与上手体验：README、[快速开始](docs/quickstart.md)和示例输出。
- 回测平台：[适配合同](docs/backends.md)、可运行的 `DemoAdapter` 与对应合同测试。
- AI/模型：[接入指南](docs/ai-integration.md)、规划接口或任务消息桥接。
- 工作流与本地核算：[架构地图](docs/architecture.md)列出职责与维护位置。

## 提交一个可评审的改动

1. 用 Issue 说明问题、最小复现、预期行为与影响范围。较大的新平台先说明它的状态、计费和报告能力。
2. 创建独立分支，集中解决一个具体问题。
3. 行为变化补能暴露真实缺口的测试；文字或视觉调整做相应预览，不为可逆文案机械增加测试。
4. 运行受影响的回归，以及项目规定的公开源码检查和全套测试。
5. PR 写清具体问题、修改后的行为、验证及仍待核的范围。

```bash
python -B scripts/verify_public_research.py
python -m pytest tests -q
```

测试默认使用合成或本地保留的授权输入。没有明确的独立授权时，测试不能启动真实模型、官网回测、充值或正式池操作。

## 兼容与研究证据

公开名称和新入口使用 QuantFactra；`panda_alpha` 实现和旧入口继续兼容。已有任务的指纹、Factor/Run ID、账本、失败和冻结来源不批量改名。新适配器不能借改名把同一任务重复执行，也不能把新平台当成旧平台来源资格的延伸。

API 与报告字段变更应给出迁移说明。来源不足、执行失败和经济反证分别记录；测试通过不替代研究有效性。
