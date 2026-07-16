# `lex_view()` callbacks — the Streamlit ↔ React bridge protocol

This document is the contract between the Streamlit host (`lex_view()` in
`lex/lex_app/streamlit/embed.py`) and the embedded React application
(`src/utils/lexAppBridge.ts` in `process-admin-general-client`). Both sides
reference it; changes here require a coordinated release.

Protocol version: **1**. Additive changes do not bump the version; breaking
changes do. Each side drops messages whose `version` is newer than what it
supports.

## Embedding modes

`lex_view(path, ...)` builds the iframe URL (`embed=true` query parameter
plus the `#embed` fragment) and renders one of:

- **Plain iframe (legacy).** No `on_*` flag set → `components.iframe`;
  returns `None`. One-directional, no events.
- **Bidirectional component.** Any of `on_create`, `on_update`, `on_delete`,
  `on_select`, `on_navigate`, `on_flow_step` set → the `_lex_view_component`
  custom component mounts the iframe and forwards events back to Python.
  `lex_view(...)` returns the latest event envelope (or `None` until the
  first event arrives).

## Child → host events (React → Streamlit)

### Envelope

```json
{
  "source": "lex-app",
  "version": 1,
  "type": "create | update | delete | select | navigate | flow_step",
  "ts": 1730000000000,
  "id": "<ULID-ish unique id>",
  "payload": { "…type-specific…": "…" }
}
```

- `source` — always `"lex-app"`; the shim ignores everything else.
- `id` — unique per emission; the shim dedupes on it so Streamlit re-runs do
  not re-trigger handlers.
- `ts` — milliseconds since epoch, informational.

### Opt-in gating

The React app only emits events the host opted in to. `lex_view()` forwards
each `on_<type>=True` flag as an `emit_<type>=true` query parameter; the
bridge snapshots those parameters from the initial URL (they survive
client-side navigation) and gates every `emitLexEvent` call. Unknown event
types are forwarded ungated so future types don't need a coordinated
release.

### Origin gating

- The shim only accepts messages whose `event.origin` equals the origin of
  the embed base URL (`expected_origin`, resolved Python-side from
  `REACT_APP_URL`/`LEX_FRONTEND_URL`). When no real origin can be resolved
  (dev/file://), it accepts any origin.
- The React side posts to the parent origin parsed from `document.referrer`,
  falling back to `"*"` only when the referrer is empty (dev); production
  hosts should send a referrer policy that exposes their origin.

### Per-type payloads

| type        | payload                                                               |
| ----------- | --------------------------------------------------------------------- |
| `create`    | `{ "resource": string, "id": string \| number, "data": object }`      |
| `update`    | `{ "resource": string, "id": string \| number, "data": object }`      |
| `delete`    | `{ "resource": string, "id": string \| number, "data": object }`      |
| `select`    | `{ "resource": string, "ids": Array<string \| number> }` (debounced)  |
| `navigate`  | `{ "from": string, "to": string }`                                     |
| `flow_step` | reserved — declared in the type union, not emitted yet                 |

(The bridge forwards payloads as-is and performs no schema validation —
treat fields beyond these as additive.)

## Theme handshake (host → iframe)

New in Phase 5 of the LEX Design System adoption: the host keeps the
embedded app's light/dark mode in sync. Two mechanisms, both driven by the
`theme` parameter of `lex_view()` (`"light"` — default — or `"dark"`;
anything else raises `ValueError`):

1. **Boot fallback — URL parameter.** The iframe URL carries
   `?theme=light|dark`. The React app reads it on boot
   (`getHostBootTheme()` in `lexAppBridge.ts`) and applies it before first
   paint of the embedded view. Works in both embedding modes.
2. **postMessage — later changes.** In bidirectional mode the component shim
   posts, after the iframe loads (and again whenever the host theme
   changes in a future host dark mode):

   ```json
   {
     "source": "lex-app-host",
     "version": 1,
     "type": "theme",
     "payload": { "mode": "light" }
   }
   ```

   The message is posted to the iframe's own origin (never `"*"`). The
   React side (`subscribeToHostTheme`) validates `source`, `version ≤ 1`,
   `type`, and `payload.mode ∈ {light, dark}`, checks the sender origin
   against the referrer-derived parent origin when available, and applies
   the mode through the react-admin theme — which the LexProvider then
   propagates to Ant Design and the document (`data-lex-theme` +
   `body.dark`). The theme is cosmetic, never security-relevant.

The host itself has no dark mode today, so it always sends `light`; the
mechanism exists so host and iframe can never disagree once one is added.

## Local smoke procedure

1. Run the React app (`yarn start` in process-admin) and a Streamlit page
   that calls `lex_view("<resource>", on_select=True, theme="light")`.
2. Verify the iframe URL contains `embed=true`, `emit_select=true` and
   `theme=light`.
3. In the iframe's DevTools: `localStorage.setItem('lex.bridge.debug', '1')`
   → select a row → the console logs the posted `select` envelope and the
   Streamlit script re-runs with the event as `lex_view`'s return value.
4. In the host page's DevTools console, the shim posts the `theme` message
   after iframe load; with `theme="dark"` the embedded app renders dark
   (body has the `dark` class inside the iframe).

## Versioning policy

- Additive fields/event types: no version bump; receivers must ignore
  unknown fields and, host-side, forward unknown types.
- Breaking envelope changes: bump `PROTOCOL_VERSION` (shim),
  `LEX_BRIDGE_VERSION` (React) and this document together; ship the
  receivers before the senders.
