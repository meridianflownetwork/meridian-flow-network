"""Launch the Meridian Flow Network site.

Local default:
  http://127.0.0.1:8765/       public landing
  http://127.0.0.1:8765/desk   operations desk
Cloud hosts set PORT (and should set HOST=0.0.0.0).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    host = os.getenv("HOST") or ("0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
    uvicorn.run("dashboard.app:app", host=host, port=port, reload=False)
