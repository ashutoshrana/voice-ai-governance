"""
Twilio ConversationRelay warm transfer adapter.

Integrates voice-ai-governance warm transfer state management with
Twilio ConversationRelay WebSocket protocol, producing compliant
handoffData payloads for Twilio Flex TaskRouter.

Reference: https://www.twilio.com/en-us/blog/developers/tutorials/integrations/conversationrelay-flex-contextual-escalations
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from voice_ai_governance.state import HandoffPayload, WarmTransferStateManager


class TwilioWarmTransferAdapter:
    """
    Twilio ConversationRelay warm transfer adapter.

    Converts WarmTransferStateManager payloads to Twilio-compatible
    WebSocket end-session messages with handoffData for Flex TaskRouter.

    The handoffData contains summary, sentiment, reason, and all
    collected entities (PII-scrubbed) so the receiving Flex agent
    does not need to re-ask any questions.

    Example:
        adapter = TwilioWarmTransferAdapter(
            state_manager=WarmTransferStateManager(redis_client=r),
            queue_sid="WQ123",
            workflow_sid="WW456",
        )

        # In your WebSocket handler when escalation triggers:
        message = adapter.build_end_session_message(session_id=session_id)
        await websocket.send_text(json.dumps(message))

    Produces:
        {
          "type": "end",
          "handoffData": {
            "reasonCode": "warm_transfer",
            "reason": "Caller requested agent | low confidence: 0.42",
            "conversationSummary": "...",
            "intent": "billing_inquiry",
            "sentiment": "neutral",
            "entities": {"account_number": "[ACCOUNT_REDACTED]"},
            "consentObtained": true,
            "complianceFlags": []
          }
        }
    """

    def __init__(
        self,
        state_manager: Optional[WarmTransferStateManager] = None,
        queue_sid: Optional[str] = None,
        workflow_sid: Optional[str] = None,
        default_reason_code: str = "warm_transfer",
    ):
        self.state_manager = state_manager or WarmTransferStateManager()
        self.queue_sid = queue_sid
        self.workflow_sid = workflow_sid
        self.default_reason_code = default_reason_code

    def build_end_session_message(
        self,
        session_id: str,
        reason: str = "confidence_escalation",
        scrub_pii: bool = True,
    ) -> Dict[str, Any]:
        """
        Build a Twilio ConversationRelay WebSocket end-session message.

        The returned dict should be JSON-serialized and sent over the
        ConversationRelay WebSocket to trigger warm transfer.
        """
        payload = self.state_manager.build_handoff_payload(
            session_id=session_id,
            reason=reason,
            scrub_pii=scrub_pii,
        )

        if not payload:
            return {"type": "end", "handoffData": {"reasonCode": self.default_reason_code}}

        task_attributes = payload.to_twilio_task_attributes()

        # Add routing if configured
        if self.queue_sid:
            task_attributes["targetQueueSid"] = self.queue_sid
        if self.workflow_sid:
            task_attributes["workflowSid"] = self.workflow_sid

        return {
            "type": "end",
            "handoffData": json.dumps({
                "reasonCode": self.default_reason_code,
                "reason": f"{payload.transfer_reason} | confidence: {payload.confidence_at_transfer:.2f}",
                "conversationSummary": payload.caller_summary,
                "intent": payload.primary_intent,
                "sentiment": payload.sentiment,
                "entities": payload.collected_entities,
                "consentObtained": payload.consent_obtained,
                "complianceFlags": payload.compliance_flags,
                "piiScrubbed": payload.pii_scrubbed,
                "callDurationSeconds": payload.call_duration_seconds,
                "turnCount": payload.turn_count,
                "escalationTrigger": payload.escalation_trigger,
                **task_attributes,
            }),
        }

    def build_taskrouter_task_attributes(
        self,
        session_id: str,
        scrub_pii: bool = True,
    ) -> Dict[str, Any]:
        """Build Twilio TaskRouter task attributes for programmatic transfer."""
        payload = self.state_manager.build_handoff_payload(
            session_id=session_id,
            reason="warm_transfer",
            scrub_pii=scrub_pii,
        )
        if not payload:
            return {}
        return payload.to_twilio_task_attributes()
