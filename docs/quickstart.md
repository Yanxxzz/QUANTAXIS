# 第一次运行 QuantFactra

目标是先获得一个可打开、可核验的本地结果，再选择自己的 AI 和回测平台。首次演示不需要官网账户、API key、MongoDB、模型订阅或行情下载。

## 安装

支持 Python 3.11 / 3.12。克隆到一个普通开发目录：

```bash
git clone https://github.com/Yanxxzz/quantfactra.git
cd quantfactra
python -m venv .venv
```

Linux/macOS 激活环境：

```bash
source .venv/bin/activate
```

Windows PowerShell 可直接使用虚拟环境解释器，无需修改系统执行策略：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m quantfactra demo --output research_runs/quickstart
```

已激活环境的终端使用：

```bash
python -m pip install -e .
python -m quantfactra demo --output research_runs/quickstart
```

这是源码安装；项目并未要求一个已经在 PyPI 发布的同名版本。上游 Web、Rust、交易服务不属于首次演示的启动条件。

## 查看结果

用浏览器打开 `research_runs/quickstart/report.html`。`summary.json` 给出实际状态和输出位置；`experiments.sqlite3` 保存任务身份与结算；`adapter/` 保留预检及模拟服务状态；`reports/` 保留中立结果与计费回执。

预期为 `state: SETTLED`、`actual: 0.0`、`external_calls: 0`、`llm_calls: 0`。来源仍为 pending、经济结论仍未评估。它证明接口流程跑通，不生成投资收益。

输出目录已存在时会拒绝覆盖。换一个目录名即可进行新的演示，原件继续保留。

## 选择下一步

- 想把自己的回测平台接进来：读[回测适配指南](backends.md)，改一份 [DemoAdapter](../quantfactra/adapters/demo.py)。
- 想让自己的模型参与研究：读[AI 接入指南](ai-integration.md)。
- 想了解整个代码库：读[架构地图](architecture.md)，按职责定位，不必先读完上游组件。
- 想运行完整协作演练：`python -m quantfactra team host-demo --output research_runs/team_demo`。

真实研究从 [config/quantfactra.example.json](../config/quantfactra.example.json) 准备新用户配置；填好数据、日期、模型和平台后再运行。新用户模板中的零历史基数不能覆盖已有研究；本项目现存的历史、失败、预算与来源资格继续沿原配置和账本。

常用帮助入口：

```bash
python -m quantfactra --help
python -m quantfactra research --help
python -m quantfactra team --help
```

## 开发验证

```bash
python -B scripts/verify_public_research.py
python -m pytest tests -q
```

请将个人配置保存为被忽略的 `config/quantfactra.local.json`。凭据、账户、原始行情和真实研究账本不提交到公开仓库。
