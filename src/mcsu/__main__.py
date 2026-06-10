"""Enable ``python -m mcsu`` as an alias for the ``mcsu`` console script."""

from __future__ import annotations

from mcsu.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
