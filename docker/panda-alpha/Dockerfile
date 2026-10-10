FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# This image runs Panda Alpha research, without the legacy web/trading stack.
COPY requirements-panda-alpha.txt ./
RUN python -m pip install --no-cache-dir -r requirements-panda-alpha.txt

COPY setup.py pyproject.toml MANIFEST.in requirements.txt LICENSE ./
COPY QUANTAXIS ./QUANTAXIS
COPY panda_alpha ./panda_alpha
COPY config/panda-alpha.example.json ./config/panda-alpha.example.json
COPY research_bootstrap ./research_bootstrap
# Editable installation keeps relative research paths anchored at /app.
RUN python -m pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["panda-alpha"]
CMD ["--help"]
