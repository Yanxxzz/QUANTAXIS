<p align="center"><img src="docs/assets/quantfactra-hero.svg" alt="QuantFactra — build research agents, keep the evidence" width="100%"></p>

<p align="center"><b>A factor research workflow for AI developers</b><br>Connect your agents and backtesting platform. Keep definitions, costs and evidence traceable.</p>

<p align="center"><a href="README.md">中文</a> · <a href="docs/quickstart.md">Quick start</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/backends.md">Backtest adapters</a> · <a href="docs/ai-integration.md">AI integration</a> · <a href="CONTRIBUTING.md">Contributing</a></p>

## What is QuantFactra?

QuantFactra helps developers build and modify quantitative research agents. It connects hypotheses, factor implementation, local evaluation, external backtesting and independent review while retaining what was tested, why a decision was made and which evidence is still missing.

- **Traceable research**: frozen definitions, shared comparisons, failures, costs and retained artifacts.
- **Replaceable execution**: PandaAI is the built-in provider; custom Python adapters can use neutral statuses, reports and explicit billing receipts.
- **Composable AI**: use the existing OpenAI-compatible planning interface or implement the workflow's task-message protocol with your own agent.

Data access currently uses QUANTAXIS-compatible structures. A new adapter is not a claim that another website, market or financial data source has been verified.

## Run an offline demo

Use Python 3.11 or 3.12 in a virtual environment:

```bash
git clone https://github.com/Yanxxzz/quantfactra.git
cd quantfactra
python -m pip install -e .
python -m quantfactra demo --output research_runs/quickstart
```

Open `research_runs/quickstart/report.html`. The run creates an isolated ledger, configuration, preflight receipt, report and zero-cost billing receipt. It exercises create → start → resume → settlement without model requests, market data or website access.

<p align="center"><img src="docs/assets/demo-receipt.svg" alt="Actual offline run: settled, zero cost, no external or model calls" width="780"></p>

This is an interface rehearsal, not an investment backtest. Existing output directories are never overwritten.

## Extend the project

1. Implement a backtest adapter from the [working example](quantfactra/adapters/demo.py) and [v1 contract](docs/backends.md).
2. Connect a model or agent through the [AI integration guide](docs/ai-integration.md).
3. Find the responsible code through the [architecture map](docs/architecture.md).
4. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for a focused, reproducible contribution.

```bash
python -m quantfactra --help
python -m quantfactra research --help
python -m quantfactra team --help
python -m quantfactra team host-demo --output research_runs/team_demo
```

## Current support and compatibility

PandaAI retains its original wallet, native preflight, report and resume behavior. The provider-neutral contract supports persistent recovery, separate cost units, free/prepaid receipts and normalized reports; it has been tested with an offline adapter. Real providers still require their own implementation and validation.

The existing local holdings, cash and transaction-cost engine is reused. Software success, source/PIT verification, economic validity and portfolio admission remain separate decisions.

The public package and command are `quantfactra`. Legacy `panda_alpha`, `panda-alpha` and `quant-research` entry points remain available. Historical definitions, Run IDs, ledgers and upstream code are retained.

```bash
python -B scripts/verify_public_research.py
python -m pytest tests -q
```

New-user configuration: [config/quantfactra.example.json](config/quantfactra.example.json). Existing studies must keep their original configuration and cumulative history.

## Contributing and roadmap

Useful contributions include minimal bug reproductions, adapter examples, documentation and contract tests. See the [project plan](docs/project-plan.md) and [open-source comparison](docs/open-source-review.md).

If QuantFactra helps you build a research agent, consider starring the repository or sharing a reproducible use case.

## Attribution

Built on [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS). Its [original documentation](README_UPSTREAM.md), copyright and [MIT license](LICENSE) are retained. Method references and third-party notices remain with the corresponding plugins and research documents. Historical research is recorded in [RESEARCH.md](RESEARCH.md).
