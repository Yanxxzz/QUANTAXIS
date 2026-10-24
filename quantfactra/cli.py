"""The public QuantFactra CLI; legacy entry points keep their existing behavior."""


def main(argv=None):
    from quant_research.cli import main as run
    return run(argv, prog="quantfactra")
