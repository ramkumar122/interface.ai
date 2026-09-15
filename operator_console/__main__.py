"""Launch the operator console.

Separate process, separate port.  CoreDesk is a target it drives over HTTP
and knows nothing about this.

    python -m operator_console            # :8010, CoreDesk on :8001
    OPERATOR_PORT=8011 python -m operator_console
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("OPERATOR_PORT", "8010"))
    uvicorn.run(
        "operator_console.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
