// lex-app's shell, as a Reflex dashboard sees it from inside its frame.
//
// * LexThemeFollower: the dashboard takes lex-app's light/dark mode -- from `?lex_theme=` when the
//   frame opens, then from `{source: "lex-app-host", type: "theme", payload: {mode}}` messages the
//   shell posts whenever its mode changes. Only the parent window, and only at one of the origins
//   lex-app is served from, is listened to.
// * LexAuthNotice: tells the shell, in the lex-auth v2 contract, that this frame cannot sign in
//   without a click (`required`) and, once it has, that it is back (`restored`). The messages
//   carry no secret, so they go to the frame's parent at its exact origin when the browser names
//   it, and to "*" when it does not.
import { useContext, useEffect } from "react";
import { ColorModeContext } from "$/utils/context-registry";

const MODES = ["light", "dark"];
const AUTH_FLAG = "lex.reflex.authRequired";

// `allowed` holds exact origins, or `scheme://host:*` for any port of a host.
export function originAllowed(origin, allowed) {
  for (const entry of allowed || []) {
    if (entry === origin) return true;
    if (typeof entry === "string" && entry.endsWith(":*")) {
      const base = entry.slice(0, -2);
      if (origin === base || origin.startsWith(base + ":")) return true;
    }
  }
  return false;
}

function framed() {
  try {
    return window.self !== window.top;
  } catch (e) {
    return true;
  }
}

export function LexThemeFollower({ allowedOrigins }) {
  const { setColorMode } = useContext(ColorModeContext);
  useEffect(() => {
    try {
      const mode = new URLSearchParams(window.location.search).get("lex_theme");
      if (MODES.includes(mode)) setColorMode(mode);
    } catch (e) {
      /* no query string to read */
    }
    if (!framed()) return undefined;
    const onMessage = (event) => {
      if (event.source !== window.parent) return;
      if (!originAllowed(event.origin, allowedOrigins)) return;
      const data = event.data;
      if (!data || data.source !== "lex-app-host" || data.type !== "theme") return;
      if (typeof data.version === "number" && data.version > 1) return;
      const mode = data.payload && data.payload.mode;
      if (MODES.includes(mode)) setColorMode(mode);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [setColorMode, allowedOrigins]);
  return null;
}

function parentOrigin() {
  try {
    const ancestors = window.location.ancestorOrigins;
    if (ancestors && ancestors.length > 0 && ancestors[0] !== "null") return ancestors[0];
  } catch (e) {
    /* not supported */
  }
  try {
    // Firefox has no `ancestorOrigins`: the referrer names the framing page.
    if (document.referrer) return new URL(document.referrer).origin;
  } catch (e) {
    /* no usable referrer */
  }
  return "*";
}

export function LexAuthNotice({ event }) {
  useEffect(() => {
    if (!framed()) return;
    try {
      if (event === "restored") {
        if (!window.sessionStorage.getItem(AUTH_FLAG)) return;
        window.sessionStorage.removeItem(AUTH_FLAG);
      } else if (event === "required") {
        window.sessionStorage.setItem(AUTH_FLAG, "1");
      } else {
        return;
      }
    } catch (e) {
      if (event === "restored") return;
    }
    const message = { type: "lex-auth", v: 2, event, surface: "reflex", at: Date.now() };
    try {
      window.parent.postMessage(message, parentOrigin());
    } catch (e) {
      /* a detached frame */
    }
  }, [event]);
  return null;
}
