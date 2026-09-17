"""Vercel serverless entrypoint.

Vercel's Python runtime auto-detects an ASGI `app` object exported from the
entrypoint module. The real app lives in backend/app, so we add `backend` to
sys.path and re-export it rather than duplicating any application code here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402
