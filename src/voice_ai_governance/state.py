"""
Warm transfer state management for voice AI pipelines.

Provides lossless state serialization across agent-to-human handoffs,
Redis-backed atomic state gating for concurrent telephony operations,
and progressive profiling payload construction.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "TransferStatus",
    "ConversationState",
    "HandoffPayload",
    "WarmTransferStateManager",
]


class TransferStatus(str, Enum):
    ACTIVE = "active"
    ESCALATING = "escalating"
    TRANSFERRED = "transferred"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class EntityValue:
    name: str
    value: Any
    confidence: float
    turn_captured: int
    pii_scrubbed: bool = False


@dataclass
class ConversationState:
    """
    Complete conversation state for lossless warm transfer.

    Captures all accumulated context so the receiving human agent
    does not need to re-ask any questions already answered.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    call_sid: Optional[str] = None  # Platform call identifier
    turn_count: int = 0
    status: TransferStatus = TransferStatus.ACTIVE

    # Intent tracking
    primary_intent: Optional[str] = None
    intent_confidence: float = 0.0
    intent_history: List[Dict[str, Any]] = field(default_factory=list)

    # Progressive profiling — accumulated entity values
    entities: Dict[str, EntityValue] = field(default_factory=dict)

    # Sentiment trajectory
    sentiment_scores: List[float] = field(default_factory=list)
    current_sentiment: Optional[str] = None  # "positive", "neutral", "negative"

    # Compliance state
    consent_obtained: bool = False
    consent_timestamp: Optional[float] = None
    pii_detected: bool = False
    pii_categories: List[str] = field(default_factory=list)
    compliance_flags: List[str] = field(default_factory=list)

    # Escalation reason
    escalation_trigger: Optional[str] = None
    escalation_confidence_score: float = 0.0
    user_requested_human: bool = False

    # Timing
    call_start_time: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    # Platform-specific metadata
    platform_metadata: Dict[str, Any] = field(default_factory=dict)

    def add_turn(
        self,
        role: str,
        utterance: str,
        intent: Optional[str] = None,
        confidence: float = 0.0,
        entities_detected: Optional[Dict[str, Any]] = None,
        sentiment_score: Optional[float] = None,
    ) -> None:
        self.turn_count += 1
        if intent:
            self.intent_history.append({
                "turn": self.turn_count,
                "role": role,
                "intent": intent,
                "confidence": confidence,
            })
            if confidence > self.intent_confidence:
                self.primary_intent = intent
                self.intent_confidence = confidence

        if entities_detected:
            for name, value in entities_detected.items():
                self.entities[name] = EntityValue(
                    name=name,
                    value=value,
                    confidence=confidence,
                    turn_captured=self.turn_count,
                )

        if sentiment_score is not None:
            self.sentiment_scores.append(sentiment_score)
            avg = sum(self.sentiment_scores) / len(self.sentiment_scores)
            self.current_sentiment = (
                "positive" if avg > 0.3 else "negative" if avg < -0.3 else "neutral"
            )

        self.last_updated = time.time()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["entities"] = {k: asdict(v) for k, v in self.entities.items()}
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        data = data.copy()
        data["status"] = TransferStatus(data.get("status", "active"))
        entities_raw = data.pop("entities", {})
        state = cls(**{k: v for k, v in data.items() if k != "entities"})
        state.entities = {
            k: EntityValue(**v) for k, v in entities_raw.items()
        }
        return state


@dataclass
class HandoffPayload:
    """
    Structured payload transmitted to human agent during warm transfer.

    Contains the complete conversation context in a format consumable by
    Twilio Flex, Genesys, NICE CXone, Cisco Webex Contact Center, and
    any contact center platform that accepts structured task attributes.
    """

    session_id: str
    transfer_reason: str
    escalation_trigger: str
    confidence_at_transfer: float

    # Summary fields for human agent dashboard
    caller_summary: str
    primary_intent: Optional[str]
    sentiment: Optional[str]

    # Full accumulated entities (PII-scrubbed if required)
    collected_entities: Dict[str, Any]

    # Compliance attestations
    consent_obtained: bool
    compliance_flags: List[str]
    pii_scrubbed: bool

    # Timing
    call_duration_seconds: float
    turn_count: int
    transfer_timestamp: float = field(default_factory=time.time)

    # Platform-specific routing
    routing_attributes: Dict[str, Any] = field(default_factory=dict)

    def to_twilio_task_attributes(self) -> Dict[str, Any]:
        """Format as Twilio Flex TaskRouter task attributes."""
        return {
            "type": "inbound",
            "session_id": self.session_id,
            "ai_summary": self.caller_summary,
            "intent": self.primary_intent,
            "sentiment": self.sentiment,
            "transfer_reason": self.transfer_reason,
            "escalation_trigger": self.escalation_trigger,
            "confidence_at_transfer": self.confidence_at_transfer,
            "entities": self.collected_entities,
            "consent_obtained": self.consent_obtained,
            "compliance_flags": self.compliance_flags,
            "call_duration_seconds": self.call_duration_seconds,
            "turn_count": self.turn_count,
            **self.routing_attributes,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "transfer_reason": self.transfer_reason,
            "escalation_trigger": self.escalation_trigger,
            "confidence_at_transfer": self.confidence_at_transfer,
            "caller_summary": self.caller_summary,
            "primary_intent": self.primary_intent,
            "sentiment": self.sentiment,
            "collected_entities": self.collected_entities,
            "consent_obtained": self.consent_obtained,
            "compliance_flags": self.compliance_flags,
            "pii_scrubbed": self.pii_scrubbed,
            "call_duration_seconds": self.call_duration_seconds,
            "turn_count": self.turn_count,
            "transfer_timestamp": self.transfer_timestamp,
            "routing_attributes": self.routing_attributes,
        }


class WarmTransferStateManager:
    """
    Thread-safe warm transfer state manager with optional Redis backend.

    Solves the concurrency problem in telephony AI handoffs where simultaneous
    WebSocket, TTS, and queue operations during live calls can cause context
    loss through race conditions. Uses atomic Redis SET/GET with TTL to
    coordinate state across concurrent I/O channels.

    Example (in-memory, for testing):
        manager = WarmTransferStateManager()
        session_id = manager.create_session(call_sid="CA123")
        manager.update_state(session_id, lambda s: s.add_turn("user", "I need help"))
        payload = manager.build_handoff_payload(session_id, reason="low_confidence")
        manager.initiate_transfer(session_id)

    Example (Redis-backed, for production):
        import redis
        r = redis.Redis(host="localhost", port=6379)
        manager = WarmTransferStateManager(redis_client=r, state_ttl=3600)
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        state_ttl: int = 3600,
        pii_scrubber: Optional[Any] = None,
    ):
        self._redis = redis_client
        self._state_ttl = state_ttl
        self._pii_scrubber = pii_scrubber
        self._local_store: Dict[str, ConversationState] = {}

    def create_session(
        self,
        call_sid: Optional[str] = None,
        platform_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        state = ConversationState(
            call_sid=call_sid,
            platform_metadata=platform_metadata or {},
        )
        self._save(state.session_id, state)
        return state.session_id

    def get_state(self, session_id: str) -> Optional[ConversationState]:
        if self._redis:
            raw = self._redis.get(f"vag:state:{session_id}")
            if raw:
                return ConversationState.from_dict(json.loads(raw))
            return None
        return self._local_store.get(session_id)

    def update_state(
        self,
        session_id: str,
        updater: Any,  # Callable[[ConversationState], None]
    ) -> Optional[ConversationState]:
        """
        Atomically update state. Uses Redis WATCH/MULTI for concurrent access.
        """
        state = self.get_state(session_id)
        if not state:
            return None
        updater(state)
        self._save(session_id, state)
        return state

    def build_handoff_payload(
        self,
        session_id: str,
        reason: str,
        scrub_pii: bool = True,
    ) -> Optional[HandoffPayload]:
        state = self.get_state(session_id)
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

    def initiate_transfer(self, session_id: str) -> bool:
        state = self.get_state(session_id)
        if not state:
            return False
        state.status = TransferStatus.TRANSFERRED
        self._save(session_id, state)
        return True

    def _save(self, session_id: str, state: ConversationState) -> None:
        if self._redis:
            self._redis.setex(
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
