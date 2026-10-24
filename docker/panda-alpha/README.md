# QuantFactra research image

Build from the repository root:

```bash
docker build -f docker/panda-alpha/Dockerfile -t quantfactra:local .
docker run --rm --network none quantfactra:local --help
```

The Dockerfile retains its legacy path so existing build scripts keep working. The public entry point is `quantfactra`; the old research CLI is still available with `--entrypoint panda-alpha`.

Run the isolated demo with a persistent output directory:

```bash
mkdir -p research_runs
docker run --rm --network none -v "$PWD/research_runs:/app/research_runs" quantfactra:local demo --output research_runs/container_demo
```

Open `research_runs/container_demo/report.html` on the host. Use a new output directory for another run. The demo uses a synthetic adapter and no model, website or market-data access.

The image includes the public QuantFactra modules, example configurations and compact historical bootstrap. Its Dockerfile-specific allowlist excludes private runtime files, local configuration, databases and credentials. Editable installation keeps relative paths anchored at `/app`.

Pull requests and relevant branch/tag changes build an amd64 image, run the public demo without network access and check the legacy CLI/bootstrap paths. They do not log in to a registry or publish an image. Publication still requires an explicit manual workflow with `publish=true`, the existing registry secrets and permission for the target image. The default future image name is `<owner>/quantfactra`; an explicit `DOCKER_IMAGE` variable remains authoritative. Published tags use the tested commit SHA.

This is the research runtime. Software checks do not certify the full upstream trading stack, live databases, financial-source availability, economic performance or another real backtesting provider. External data and provider credentials are supplied only for explicitly authorized runtime work.
