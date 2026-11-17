// lex-app's own pages and controls, framed inside a Reflex dashboard.
//
// One component, two modes, the same postMessage contract the Streamlit shims speak (lex-app's
// bridge, `src/utils/lexAppBridge.ts`, and its widget host, `EmbedWidgetHost.tsx`):
//
// * mode "view" -- a lex-app page (`lex_view`): events from the page arrive as
//   `{source: "lex-app", version: 1, type, id, payload}` and go to the matching handler; the
//   dashboard's filters are posted as `{source: "lex-app-host", type: "filter"}`.
// * mode "widgets" -- lex-app's widget host (`lex_widgets`): it says `widget_host_ready`, gets the
//   manifest posted, reports its height (`resize`), calculation statuses and log requests.
//
// Either way the frame gets lex-app's colour mode (`type: "theme"`) on every load and change, and
// only messages from the frame itself, at lex-app's origin, are believed. When the dashboard is
// itself framed by lex-app, the page it frames is opened on the origin lex-app was reached on --
// an attached customer domain included -- so it carries the user's session there.
import { createElement, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ColorModeContext } from "$/utils/context-registry";
import { originAllowed } from "./lex_host.jsx";

const HANDLERS = {
  create: "onCreate",
  update: "onUpdate",
  delete: "onDelete",
  select: "onSelect",
  navigate: "onNavigate",
  filter: "onFilter",
  flow_step: "onFlowStep",
  calculation_status: "onStatus",
  open_log: "onOpenLog",
};

// Key-sorted, compact JSON as base64url without padding: the token the Python side puts in a URL.
export function filtersToken(filters) {
  if (filters === null || filters === undefined) return null;
  const sorted = (value) => {
    if (Array.isArray(value)) return value.map(sorted);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce((out, key) => ({ ...out, [key]: sorted(value[key]) }), {});
    }
    return value;
  };
  const json = JSON.stringify(sorted(filters));
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// The origin of the page framing this dashboard: `ancestorOrigins` where the browser has it,
// else the referrer's (Firefox has no `ancestorOrigins`); null when not framed or unknown.
export function framingOrigin() {
  try {
    if (window.self === window.top) return null;
  } catch (e) {
    /* a cross-origin top: framed */
  }
  try {
    const ancestors = window.location.ancestorOrigins;
    if (ancestors && ancestors.length > 0 && ancestors[0] !== "null") return ancestors[0];
  } catch (e) {
    /* not supported */
  }
  try {
    if (document.referrer) return new URL(document.referrer).origin;
  } catch (e) {
    /* no usable referrer */
  }
  return null;
}

// The origin lex-app's page is opened on: lex-app's own, when it frames this dashboard.
export function effectiveOrigin(expectedOrigin, appOrigins) {
  const parent = framingOrigin();
  if (parent && originAllowed(parent, appOrigins)) return parent;
  return expectedOrigin;
}

export function rebase(src, expectedOrigin, origin) {
  if (!src || !expectedOrigin || !origin || origin === expectedOrigin) return src;
  return src.startsWith(expectedOrigin) ? origin + src.slice(expectedOrigin.length) : src;
}

export function LexFrame(props) {
  const {
    src, expectedOrigin, appOrigins, mode = "view", manifest, filters, urlFiltersToken,
    height = 800, minHeight = 48, title,
  } = props;
  const frame = useRef(null);
  const loaded = useRef(false);
  const ready = useRef(false);
  const current = useRef(null);
  const lastId = useRef(null);
  const [frameHeight, setFrameHeight] = useState(mode === "widgets" ? minHeight : height);
  const { resolvedColorMode } = useContext(ColorModeContext);
  const origin = useMemo(() => effectiveOrigin(expectedOrigin, appOrigins), [expectedOrigin, appOrigins]);
  const url = useMemo(() => rebase(src, expectedOrigin, origin), [src, expectedOrigin, origin]);
  const desired = useMemo(() => filtersToken(filters), [filters]);
  const latest = useRef(props);
  latest.current = props;

  const post = useCallback((type, payload) => {
    const target = frame.current && frame.current.contentWindow;
    if (!target || !origin) return;
    target.postMessage({ source: "lex-app-host", version: 1, type, payload }, origin);
  }, [origin]);

  const postFilters = useCallback(() => {
    if (!loaded.current || desired === null || desired === current.current) return;
    post("filter", { filters });
    current.current = desired;
  }, [post, desired, filters]);

  // Every load: the theme, and the filters when the URL did not already carry them.
  const onLoad = useCallback(() => {
    loaded.current = true;
    ready.current = false;
    current.current = urlFiltersToken || null;
    post("theme", { mode: resolvedColorMode });
    if (mode === "view") postFilters();
  }, [post, resolvedColorMode, mode, postFilters, urlFiltersToken]);

  useEffect(() => {
    if (loaded.current) post("theme", { mode: resolvedColorMode });
  }, [resolvedColorMode, post]);

  useEffect(() => {
    if (mode === "view") postFilters();
  }, [mode, postFilters]);

  useEffect(() => {
    if (mode === "widgets" && ready.current && manifest) post("manifest", manifest);
  }, [mode, manifest, post]);

  useEffect(() => {
    const onMessage = (event) => {
      const target = frame.current && frame.current.contentWindow;
      if (!target || event.source !== target || event.origin !== origin) return;
      const data = event.data;
      if (!data || data.source !== "lex-app" || typeof data.type !== "string") return;
      if (typeof data.version === "number" && data.version > 1) return;
      // The same message delivered twice in a row is one message. Only the previous id is
      // remembered: a calculation's status id is `<widget>:<status>`, so a re-run's SUCCESS
      // after IN_PROGRESS must come through although an earlier run reported it too.
      if (data.id) {
        if (data.id === lastId.current) return;
        lastId.current = data.id;
      }
      const current = latest.current;
      if (data.type === "widget_host_ready") {
        ready.current = true;
        if (current.manifest) post("manifest", current.manifest);
        post("theme", { mode: resolvedColorMode });
        return;
      }
      if (data.type === "resize") {
        const next = Math.max(current.minHeight || 0, Number(data.payload && data.payload.height) || 0);
        setFrameHeight((h) => (Math.abs(h - next) > 2 ? next : h));
        return;
      }
      if (typeof current.onEvent === "function") current.onEvent(data);
      const handler = current[HANDLERS[data.type]];
      // A log request carries where it came from, so the log opens on the same lex-app.
      const payload = data.type === "open_log" ? { ...(data.payload || {}), base: origin } : data.payload || {};
      if (typeof handler === "function") handler(payload);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [origin, post, resolvedColorMode]);

  return createElement("iframe", {
    ref: frame,
    src: url,
    title: title || "lex-app",
    onLoad,
    loading: "eager",
    sandbox: "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads",
    style: { width: "100%", height: `${mode === "widgets" ? frameHeight : height}px`, border: "none" },
  });
}
