# Contributing

## Development setup

```bash
pip install -e ".[dev]"
pytest
ruff check yoreni/
```

Strict type checking covers optional providers and channels:

```bash
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright
```

Keep `--no-sync` on the final commands: channel dependencies come from their package manifests and are installed explicitly by the setup step.

React/TypeScript work follows the [WebUI development guide](webui/README.md) for HMR, source layout, and frontend checks.

## Code style

- Line length: 100 characters (`ruff`)
- Target: Python 3.11+
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored)
- Async: `asyncio` throughout; pytest with `asyncio_mode = "auto"`
- Prefer focused patches over broad rewrites
- Do not mix mechanical formatting, line wrapping, import sorting, or quote churn into a feature or bugfix change

Do not run `ruff format` across the tree; the existing sources predate it and broad formatting obscures history.

## CI workflows

Keep CI within GitHub Actions' free tier:

- Use only standard GitHub-hosted runners (`ubuntu-latest`, `windows-latest`)
- Avoid macOS runners, larger runners, and self-hosted runners
- Avoid large artifacts, long retention, and paid Marketplace actions

## Contribution license

By submitting a contribution, you confirm that you have the right to submit it and agree that it will be licensed under the project's MIT License.
