# Public Roadmap

## Current state (v0.2.0)

Core voice compliance enforcement is implemented for Pipecat and Twilio,
covering TCPA, HIPAA, FERPA, and EU AI Act Articles 13–14. 79 tests passing.

---

## Near-term milestones

### 1. LiveKit Agents adapter

Integration with LiveKit's agent framework for WebRTC-based voice AI
deployments. Targets LiveKit Agents SDK v1.x.

### 2. Amazon Connect adapter

Integration with Amazon Connect contact flows for enterprise contact
center deployments. Covers HIPAA BAA-eligible use cases.

### 3. CCPA opt-out enforcement

California Consumer Privacy Act § 1798.120 — consumer right to opt out
of sale or sharing of personal information collected during voice interactions.

### 4. Twilio Programmable Messaging bulk compliance

Bulk SMS campaign compliance layer: rate limiting, DNC list checking,
time-window enforcement, and opt-out database synchronization.

### 5. Real-time PHI streaming scrubber

Stream-safe PHI detection for real-time transcription pipelines where
text arrives token-by-token rather than as complete utterances.
