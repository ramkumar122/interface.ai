"""Launcher for CoreDesk.

Reads TENANT and PORT from the environment (defaulting to riverbend/8001)
and starts uvicorn. Run one instance per tenant in its own terminal.
"""

import os

import uvicorn

from coredesk.config import get_tenant

if __name__ == "__main__":
    tenant = os.environ.get("TENANT", "riverbend")
    if tenant not in ("riverbend", "summit"):
        tenant = "riverbend"

    # Make sure the app process sees the resolved tenant.
    os.environ["TENANT"] = tenant

    default_port = get_tenant(tenant)["port"]
    port = int(os.environ.get("PORT", default_port))

    uvicorn.run("coredesk.app:app", host="127.0.0.1", port=port, reload=False)
