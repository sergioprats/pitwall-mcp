"""Interactive OAuth device-code login.

Run by hand. Opens nothing on its own: it prints a URL and a code, you finish
the flow in a browser, and the resulting tokens are written to the token file
outside the repository with mode 600.

    python scripts/login.py

This is a thin wrapper: the flow itself lives in the package, as
`pitwall-mcp --login`, so that someone who installed from PyPI and has no
`scripts/` directory can still obtain a token. One flow, one definition.

It talks to the GCDM OAuth endpoint, NOT to the CarData REST API, so it does
not consume any of the 50 daily requests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--login"]))
