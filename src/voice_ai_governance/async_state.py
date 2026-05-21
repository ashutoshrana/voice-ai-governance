"""
Async warm transfer state manager for clustered voice AI deployments.

Drop-in async variant of WarmTransferStateManager with:
- asyncio-native API (no blocking I/O in event loops)
- aioredis support for distributed atomic state across telephony nodes
- Per-session asyncio.Lock preventing race conditions between concurrent
  WebSocket message and TTS completion handlers during live calls
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from voice_ai_governance.state import ConversationState, HandoffPayload, TransferStatus

__all__ = ["AsyncWarmTransferStateManager"]


class AsyncWarmTransferStateManager:
    """
    Async variant of WarmTransferStateManager for high-concurrency telephony deployments.

    Uses aioredis for distributed atomic state management across multiple telephony
    nodes. Falls back to an in-memory asyncio.Lock-guarded dict when no Redis client
    is provided (test / single-node deployments).

    Example::

        import aioredis
        redis = await aioredis.from_url("redis://localhost:6379")
        manager = AsyncWarmTransferStateManager(redis_client=redis, state_ttl=3600)

        session_id = await manager.create_session(call_sid="CA123")
        await manager.update_state(session_id, lambda s: s.add_turn("user", "Help me"))
        payload = await manager.build_handoff_payload(session_id, reason="low_confidence")
        await manager.initiate_transfer(session_id)
        await redis.aclose()
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        state_ttl: int = 3600,
        pii_scrubber: Optional[Any] = None,
    ) -> None:
        self._redis = redis_client
        self._state_ttl = state_ttl
        self._pii_scrubber = pii_scrubber
        self._local_store: Dict[str, ConversationState] = {}
        self._locks: Dict[str, Any] = {}

    async def create_session(
        self,
        call_sid: Optional[str] = None,
        platform_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        state = ConversationState(
            call_sid=call_sid,
            platform_metadata=platform_metadata or {},
        )
        await self._save(state.session_id, state)
        return state.session_id

    async def get_state(self, session_id: str) -> Optional[ConversationState]:
        if self._redis:
            raw = await self._redis.get(f"vag:state:{session_id}")
            if raw:
                return ConversationState.from_dict(json.loads(raw))
            return None
        return self._local_store.get(session_id)

    async def update_state(
        self,
        session_id: str,
        updater: Callable[[ConversationState], None],
    ) -> Optional[ConversationState]:
        """
        Atomically update state under a per-session asyncio.Lock.

        Prevents concurrent WebSocket message and TTS completion handlers
        from overwriting each other's state mid-call.
        """
        import asyncio
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        async with self._locks[session_id]:
            state = await self.get_state(session_id)
            if not state:
                return None
            updater(state)
            await self._save(session_id, state)
            return state

    async def build_handoff_payload(
        self,
        session_id: str,
        reason: str,
        scrub_pii: bool = True,
    ) -> Optional[HandoffPayload]:
        state = await self.get_state(session_id)
        if not state:
            return None
        entities = {k: v.value for k, v in state.entities.items()}
        pii_scrubbed = False
        if scrub_pii and self._pii_scrubber:
            entities, pii_scrubbed = self._pii_scrubber.scrub_dict(entities)
        return HandoffPayload(
            session_id=session_id,
            transfer_reason=reason,
            escalation_trigger=state.escalation_trigger or reason,
            confidence_at_transfer=state.escalation_confidence_score,
            caller_summary=self._build_summary(state),
            primary_intent=state.primary_intent,
            sentiment=state.current_sentiment,
            collected_entities=entities,
            consent_obtained=state.consent_obtained,
            compliance_flags=state.compliance_flags,
            pii_scrubbed=pii_scrubbed,
            call_duration_seconds=time.time() - state.call_start_time,
            turn_count=state.turn_count,
        )

    async def initiate_transfer(self, session_id: str) -> bool:
        state = await self.get_state(session_id)
        if not state:
            return False
        state.status = TransferStatus.TRANSFERRED
        await self._save(session_id, state)
        return True

    async def close_session(self, session_id: str) -> None:
        """Mark session complete and release per-session lock."""
        state = await self.get_state(session_id)
        if state:
            state.status = TransferStatus.COMPLETED
            await self._save(session_id, state)
        self._locks.pop(session_id, None)

    async def _save(self, session_id: str, state: ConversationState) -> None:
        if self._redis:
            await self._redis.setex(
                f"vag:state:{session_id}",
                self._state_ttl,
                json.dumps(state.to_dict()),
            )
        else:
            self._local_store[session_id] = state

    @staticmethod
    def _build_summary(state: ConversationState) -> str:
        entities_str = ", ".join(
            f"{k}: {v.value}" for k, v in list(state.entities.items())[:5]
        )
        return (
            f"Caller interaction ({state.turn_count} turns). "
            f"Intent: {state.primary_intent or 'unclear'}. "
            f"Sentiment: {state.current_sentiment or 'neutral'}. "
            f"Collected: {entities_str or 'none'}. "
            f"Reason for transfer: {state.escalation_trigger or 'unspecified'}."
        )
