"""Tests for warm transfer state management."""

import pytest
from voice_ai_governance.state import (
    ConversationState,
    HandoffPayload,
    TransferStatus,
    WarmTransferStateManager,
)


class TestConversationState:
    def test_create_state(self):
        state = ConversationState(call_sid="CA123")
        assert state.call_sid == "CA123"
        assert state.status == TransferStatus.ACTIVE
        assert state.turn_count == 0

    def test_add_turn_increments_count(self):
        state = ConversationState()
        state.add_turn("user", "Hello", intent="greeting", confidence=0.9)
        assert state.turn_count == 1

    def test_intent_updated_on_higher_confidence(self):
        state = ConversationState()
        state.add_turn("user", "I need billing help", intent="billing", confidence=0.7)
        state.add_turn("user", "Invoice question", intent="invoice_inquiry", confidence=0.95)
        assert state.primary_intent == "invoice_inquiry"
        assert state.intent_confidence == pytest.approx(0.95)

    def test_intent_not_updated_on_lower_confidence(self):
        state = ConversationState()
        state.add_turn("user", "High confidence intent", intent="billing", confidence=0.9)
        state.add_turn("user", "Low confidence", intent="other", confidence=0.3)
        assert state.primary_intent == "billing"

    def test_entity_accumulation(self):
        state = ConversationState()
        state.add_turn("user", "My name is John", entities_detected={"name": "John"})
        state.add_turn("user", "My account is 12345", entities_detected={"account_number": "12345"})
        assert "name" in state.entities
        assert "account_number" in state.entities

    def test_sentiment_tracking(self):
        state = ConversationState()
        state.add_turn("user", "Great service!", sentiment_score=0.8)
        state.add_turn("user", "Very helpful", sentiment_score=0.6)
        assert state.current_sentiment == "positive"

    def test_negative_sentiment(self):
        state = ConversationState()
        state.add_turn("user", "This is terrible", sentiment_score=-0.8)
        assert state.current_sentiment == "negative"

    def test_serialization_roundtrip(self):
        state = ConversationState(call_sid="CA456")
        state.add_turn("user", "Hello", intent="greeting", confidence=0.8)
        state.entities["name"] = __import__("voice_ai_governance.state", fromlist=["EntityValue"]).EntityValue(
            name="name", value="Alice", confidence=0.9, turn_captured=1
        )
        serialized = state.to_dict()
        restored = ConversationState.from_dict(serialized)
        assert restored.call_sid == state.call_sid
        assert restored.turn_count == state.turn_count
        assert restored.primary_intent == state.primary_intent
        assert "name" in restored.entities


class TestWarmTransferStateManager:
    def test_create_session(self):
        manager = WarmTransferStateManager()
        session_id = manager.create_session(call_sid="CA789")
        assert session_id is not None
        state = manager.get_state(session_id)
        assert state is not None
        assert state.call_sid == "CA789"

    def test_update_state(self):
        manager = WarmTransferStateManager()
        session_id = manager.create_session()
        manager.update_state(
            session_id,
            lambda s: s.add_turn("user", "Hello", intent="greeting", confidence=0.85),
        )
        state = manager.get_state(session_id)
        assert state.turn_count == 1
        assert state.primary_intent == "greeting"

    def test_build_handoff_payload(self):
        manager = WarmTransferStateManager()
        session_id = manager.create_session(call_sid="CA100")
        manager.update_state(
            session_id,
            lambda s: s.add_turn(
                "user", "I have a billing question",
                intent="billing_inquiry",
                confidence=0.88,
                entities_detected={"topic": "billing"},
            ),
        )
        payload = manager.build_handoff_payload(session_id, reason="low_confidence")
        assert payload is not None
        assert payload.primary_intent == "billing_inquiry"
        assert "topic" in payload.collected_entities

    def test_initiate_transfer_updates_status(self):
        manager = WarmTransferStateManager()
        session_id = manager.create_session()
        result = manager.initiate_transfer(session_id)
        assert result is True
        state = manager.get_state(session_id)
        assert state.status == TransferStatus.TRANSFERRED

    def test_get_nonexistent_session_returns_none(self):
        manager = WarmTransferStateManager()
        state = manager.get_state("nonexistent-id")
        assert state is None

    def test_handoff_payload_twilio_format(self):
        manager = WarmTransferStateManager()
        session_id = manager.create_session(call_sid="CA200")
        manager.update_state(
            session_id,
            lambda s: s.add_turn("user", "Help me", intent="support", confidence=0.7),
        )
        payload = manager.build_handoff_payload(session_id, reason="test")
        task_attrs = payload.to_twilio_task_attributes()
        assert "session_id" in task_attrs
        assert "ai_summary" in task_attrs
        assert "intent" in task_attrs
        assert "sentiment" in task_attrs
