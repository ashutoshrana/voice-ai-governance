"""Check an isolated installed base wheel without development dependencies."""

import asyncio
import compileall
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
import sys

import voice_ai_governance as package
from voice_ai_governance.async_state import AsyncWarmTransferStateManager

assert package.__version__ == version("voice-ai-governance")
assert Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert find_spec("pytest") is None
assert compileall.compile_dir(Path(package.__file__).parent, quiet=1)

manager = package.WarmTransferStateManager()
assert manager.build_handoff_payload("missing", "handoff") is None
session = manager.create_session()
try:
    manager.build_handoff_payload(session, "handoff")
except ValueError as exc:
    assert "scrubber" in str(exc)
else:
    raise AssertionError("Requested redaction accepted a missing scrubber")
assert manager.build_handoff_payload(session, "handoff", scrub_pii=False) is not None

manager = package.WarmTransferStateManager(pii_scrubber=package.PIIScrubber())
session = manager.create_session()
manager.update_state(
    session,
    lambda state: state.add_turn("user", "Account question", entities_detected={"ssn": "123-45-6789"}),
)
payload = manager.build_handoff_payload(session, "handoff")
assert payload.pii_scrubbed
assert "123-45-6789" not in str(payload.to_dict())


async def check_async():
    manager = AsyncWarmTransferStateManager()
    session = await manager.create_session()
    try:
        await manager.build_handoff_payload(session, "handoff")
    except ValueError as exc:
        assert "scrubber" in str(exc)
    else:
        raise AssertionError("Async requested redaction accepted a missing scrubber")
    assert await manager.build_handoff_payload(session, "handoff", scrub_pii=False) is not None


asyncio.run(check_async())
print("Installed base runtime: missing-session, redaction and sync/async configuration checks passed")
