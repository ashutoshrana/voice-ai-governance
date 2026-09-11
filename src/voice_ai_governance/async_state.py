"""
Async warm transfer state manager for clustered voice AI deployments.

Drop-in async variant of WarmTransferStateManager with:
- asyncio-native API (no blocking I/O in event loops)
- redis.asyncio support for distributed atomic state across telephony nodes
- Per-session asyncio.Lock preventing race conditions between concurrent
  WebSocket message and TTS completion handlers during live calls
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from voice_ai_governance.state import ConversationState, HandoffPayload, TransferStatus, WarmTransferStateManager

__all__ = ["AsyncWarmTransferStateManager"]


class AsyncWarmTransferStateManager:
    """
    Async variant of WarmTransferStateManager for high-concurrency telephony deployments.

    Uses redis.asyncio for distributed atomic state management across multiple telephony
    nodes. Falls back to an in-memory asyncio.Lock-guarded dict when no Redis client
    is provided (test / single-node deployments).

    Example::

        import redis.asyncio as redis_async
        redis = redis_async.from_url("redis://localhost:6379")
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
        Atomically update state with Redis WATCH/MULTI or a local per-session lock.
        Updaters must be side-effect-free because conflicts replay them.

        Prevents concurrent WebSocket message and TTS completion handlers
        from overwriting each other's state mid-call.
        """
        if self._redis is not None:
            from redis.exceptions import WatchError
            key = f"vag:state:{session_id}"
            for _ in range(100):
                async with self._redis.pipeline() as pipe:
                    try:
                        await pipe.watch(key)
                        raw = await pipe.get(key)
                        if raw is None:
                            return None
                        state = ConversationState.from_dict(json.loads(raw))
                        updater(state)
                        pipe.multi()
                        pipe.setex(key, self._state_ttl, json.dumps(state.to_dict()))
                        await pipe.execute()
                        return state
                    except WatchError:
                        continue
            raise RuntimeError("State update contention exceeded retry limit")
        import asyncio
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        async with self._locks[session_id]:
            state = await self.get_state(session_id)
            if not state:
                return None
            state = ConversationState.from_dict(state.to_dict())
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
        return WarmTransferStateManager._make_payload(self, state, reason, scrub_pii)

    async def initiate_transfer(self, session_id: str) -> bool:
        def transfer(state: ConversationState) -> None:
            if state.status != TransferStatus.COMPLETED:
                state.status = TransferStatus.TRANSFERRED
        state = await self.update_state(session_id, transfer)
        return state is not None and state.status == TransferStatus.TRANSFERRED

    async def close_session(self, session_id: str) -> None:
        """Mark complete atomically; retain lock identity for already waiting callers."""
        await self.update_state(session_id, lambda state: setattr(state, "status", TransferStatus.COMPLETED))

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
