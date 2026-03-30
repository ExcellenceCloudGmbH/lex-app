# Signals & WebSocket Updates

Search keywords: signals, dependency cascade, update_calculation_status, channels

## Scope

- Dependency-triggered updates after model changes
- Real-time status/event broadcasting

## Key Points

- Django signals drive recalculation cascades and consistency updates.
- WebSocket channels broadcast calculation progress and log/status events.
- Server-side status updates should align with persisted `is_calculated` state transitions.

## Where to Expand

- `lex_context.md`: Signals & Calculated Updates
- `lex_context_repo.md`: Signals & Dependency Cascade; WebSocket Consumers

## LLM Prompt Starters

- "Add a post-save dependency trigger for this model and describe expected cascade effects."
- "Show how to emit calculation status updates to websocket consumers after state changes."
