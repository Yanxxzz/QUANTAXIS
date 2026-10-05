# Panda Alpha research image

Build from the repository root:

```bash
docker build -f docker/panda-alpha/Dockerfile -t panda-alpha:local .
docker run --rm --network none panda-alpha:local --help
```

The image installs this fork and `requirements-panda-alpha.txt`. It contains the
public example configuration and compact research bootstrap. The separate
Dockerfile ignore file excludes local databases, migration runs, credentials and
other workspace files from the build context. Editable installation anchors the
configuration's relative bootstrap paths at `/app`.

Pull requests and branch/tag builds build an amd64 image and run offline CLI and
bootstrap-path checks. They do not log in to a registry or publish an image.
Publishing requires an explicit manual workflow run with `publish=true`, Docker
Hub `DOCKER_USERNAME` / `DOCKER_PASSWORD` secrets and permission for the target
image. `DOCKER_IMAGE` can override the default `<fork-owner>/panda-alpha`; names
are converted to lowercase. The published tag is the tested commit SHA.

This is the research runtime. The checks do not certify the full legacy trading
stack, live MongoDB/RabbitMQ connections, market-data availability or official
PandaAI execution. External data and platform credentials are supplied at runtime.
