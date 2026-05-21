"""Stub optional heavy dependencies before any test module is imported."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

for _mod in (
    "boto3",
    "botocore",
    "botocore.exceptions",
    "livekit",
    "livekit.agents",
    "livekit.rtc",
):
    sys.modules.setdefault(_mod, MagicMock())
