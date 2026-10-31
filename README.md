# yoreni

yoreni is a Python agent runtime derived from [nanobot](https://github.com/HKUDS/nanobot). The upstream product documentation, branding assets, container images, hosting manifests, and publication workflows have been removed; this repository is the working tree for the new project.

## Layout

| Path | Purpose |
| --- | --- |
| `yoreni/` | Python gateway, agent loop, tools, skills, channels, and WebUI backend |
| `webui/` | React/TypeScript browser client |
| `tests/` | Python test suite |
| `.agent/` | Engineering constraints for contributors and coding agents |

Runtime boundaries and extension rules are described in [AGENTS.md](AGENTS.md) and [.agent/design.md](.agent/design.md).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Agent execution runs inside a persistent Linux microVM. Install the backend with `uv sync --extra sandbox` (`pip install -e ".[sandbox]"`); Linux requires `/dev/kvm`. The first gateway start provisions the VM image and needs network access. See [.agent/gotchas.md](.agent/gotchas.md#execution-plane) for platform details.

Build the browser client before packaging:

```bash
cd webui && bun run build
```

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
