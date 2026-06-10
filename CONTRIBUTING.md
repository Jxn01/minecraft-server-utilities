# Contributing

Thanks for your interest in improving **mcsu**! This project values small,
focused changes with tests.

## Development setup

```bash
git clone https://github.com/jxn01/minecraft_server_utilities
cd minecraft_server_utilities
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you push

The CI runs exactly these; run them locally first:

```bash
ruff check .          # lint
ruff format .         # auto-format
mypy                  # type-check
pytest                # tests (with coverage)
```

All four must be green. The test suite is fast (a few seconds) and requires no
Java or network — the `fake_java` fixture and an in-process mock RCON server
stand in for the real things, and installer tests mock HTTP.

## Project layout

| Path | What lives there |
|---|---|
| `src/mcsu/` | The package. One responsibility per module (see the README architecture diagram). |
| `tests/` | Pytest suite, mirroring the modules. |
| `deploy/` | systemd unit, Dockerfile. |
| `docs/` | Configuration and deployment references. |

## Guidelines

- **Keep the runtime dependency-free.** Core features should use only the
  standard library. Dev-only tools belong in the `dev` optional dependencies.
- **Add a test** for any behavior change. Aim to cover the happy path and at
  least one error path.
- **Cross-platform first.** Avoid POSIX-only assumptions; if something must
  differ by OS, branch on `mcsu.utils.IS_WINDOWS` and test both paths where
  feasible.
- **Match the surrounding style** — type hints, docstrings, and the existing
  naming conventions.

## Adding a new loader to the installer

Implement `_install_<loader>` on `ServerInstaller`, add it to `LOADERS`, and
add a unit test that mocks `_get_json`/`_get`/`_download` (see
`tests/test_installer.py` for the pattern).

## Reporting bugs

Open an issue with your OS, Python version, loader/Minecraft version, your
`mcsu.toml` (redact secrets), and the relevant log output.
