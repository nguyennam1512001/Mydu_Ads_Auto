from __future__ import annotations

"""Backward-compatible entrypoint for workflow 11.

The implementation lives in run.py so every execution uses the same current
Meta Graph API v26 path.
"""

from run import main


if __name__ == "__main__":
    main()

