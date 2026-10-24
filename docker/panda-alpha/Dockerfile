FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# This image runs QuantFactra research, without the legacy web/trading stack.
COPY requirements-panda-alpha.txt ./
RUN python -m pip install --no-cache-dir -r requirements-panda-alpha.txt

COPY setup.py pyproject.toml MANIFEST.in requirements.txt requirements-quantfactra.txt LICENSE ./
COPY README.md README_EN.md README_UPSTREAM.md CONTRIBUTING.md CHANGELOG.md AGENTS.md ./
COPY QUANTAXIS ./QUANTAXIS
COPY panda_alpha ./panda_alpha
COPY quant_research ./quant_research
COPY quantfactra ./quantfactra
COPY config/panda-alpha.example.json config/quantfactra.example.json config/quantfactra-policy.example.json config/research-workflow.v1.json config/pool-admission.v3.json ./config/
COPY research_bootstrap ./research_bootstrap
# Editable installation keeps relative research paths anchored at /app.
RUN python -m pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["quantfactra"]
CMD ["--help"]
