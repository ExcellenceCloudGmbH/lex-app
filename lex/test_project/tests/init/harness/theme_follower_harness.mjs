// DOM-double harness for lex/streamlit_theme.py's follower script.
//
// This repository has no JS runtime in CI, so the follower's LOGIC (read the
// agreement -> compare with what the page shows -> decide) is covered here by
// hand rather than by pytest, which can only assert on the emitted text.
//
// It exists because of one specific bug class: a reload loop. Nothing you can
// assert about a string proves a page settles, so the doubles below model the
// only thing that matters -- state that survives location.reload() and state
// that does not.
import fs from "node:fs";

const SRC = process.argv[2];

function makeStorage() {
  const m = new Map();
  return {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k),
    _dump: () => Object.fromEntries(m),
  };
}

/** A tab: localStorage and sessionStorage persist across reloads, the window does not. */
function makeTab({ pathname = "/", prefersDark = false } = {}) {
  const local = makeStorage();
  const session = makeStorage();
  return {
    local, session, pathname, prefersDark,
    reloads: 0,
    listeners: {},
    logs: [],
    newWindow() {
      const tab = this;
      const host = {
        localStorage: local,
        sessionStorage: session,
        location: { pathname, reload() { tab.reloads += 1; tab.reloading = true; } },
        matchMedia: q => ({ matches: q.includes("dark") ? tab.prefersDark : !tab.prefersDark }),
        addEventListener(name, fn) { (tab.listeners[name] ||= []).push(fn); },
        requestAnimationFrame: fn => fn(),
        setInterval: () => 0,
        document: { createElement: () => ({ style: {} }), body: { appendChild() {} } },
      };
      return host;
    },
  };
}

function run(js, tab) {
  tab.reloading = false;
  tab.listeners = {};                       // listeners die with the window
  const host = tab.newWindow();
  const win = { parent: host };
  const console_ = {
    info: (...a) => tab.logs.push(["info", a.join(" ")]),
    warn: (...a) => tab.logs.push(["warn", a.join(" ")]),
    log: (...a) => tab.logs.push(["log", a.join(" ")]),
  };
  new Function("window", "console", "document", js)(win, console_, host.document);
  return host;
}

const html = fs.readFileSync(SRC, "utf8");
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); }
  else { failures += 1; console.log(`  FAIL ${name}${detail ? "  -- " + detail : ""}`); }
}

const KEY = "lex.theme.mode";
const stKey = p => `stActiveTheme-${p}-v2`;

// ── 1. A plain agreement is adopted, once. ───────────────────────────────
{
  console.log("\n1. agreement=dark, page shows light");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  run(js, tab);
  check("reloads once", tab.reloads === 1, `got ${tab.reloads}`);
  check("wrote Streamlit's own key", tab.local.getItem(stKey("/")) === '"Dark"',
        String(tab.local.getItem(stKey("/"))));
  // second load: the page now shows dark, so nothing more happens
  run(js, tab);
  check("settles on the next load", tab.reloads === 1, `got ${tab.reloads}`);
}

// ── 2. The reported loop: widgets contradict the agreement, forever. ─────
{
  console.log("\n2. agreement=dark, widgets keep reporting light (the reported bug)");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  for (let load = 0; load < 12; load += 1) {
    const host = run(js, tab);
    if (!tab.reloading) host.__lexThemeFollow("light");   // a widget announces itself
  }
  check("terminates", tab.reloads <= 2, `reloaded ${tab.reloads} times over 12 loads`);
  check("says why", tab.logs.some(([lvl, m]) => lvl === "warn" && m.includes("NOT reloading")));
}

// ── 3. Someone is USING the page: no reload may happen mid-session. ──────
{
  console.log("\n3. a settled page, widgets reporting light for a while");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  run(js, tab);                              // boot: adopts dark
  const before = tab.reloads;
  const host = run(js, tab);                 // the load that shows dark
  for (let tick = 0; tick < 20; tick += 1) host.__lexThemeFollow("light");
  check("no reload while in use", tab.reloads === before, `${tab.reloads - before} interruptions`);
}

// ── 4. A permanent contradiction the page CAN'T win: bounded, then quiet. ─
{
  console.log("\n4. agreement=dark but the page can never show dark (pinned elsewhere)");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  // effectiveMode() reads Streamlit's key; pin it to Light after every write.
  const realSet = tab.local.setItem;
  tab.local.setItem = (k, v) => realSet.call(tab.local, k, k === stKey("/") ? '"Light"' : v);
  for (let load = 0; load < 10; load += 1) run(js, tab);
  check("bounded", tab.reloads <= 2, `reloaded ${tab.reloads} times over 10 loads`);
  check("stands down permanently", tab.session.getItem("lex.theme.standown") !== null);
  check("explains the stand-down", tab.logs.some(([lvl, m]) => lvl === "warn" && m.includes("standing down")));
  tab.local.setItem = realSet;
}

// ── 5. The escape hatch: a deliberate change is still honoured. ──────────
{
  console.log("\n5. after a stand-down, the user changes the theme for real");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  const realSet = tab.local.setItem;
  tab.local.setItem = (k, v) => realSet.call(tab.local, k, k === stKey("/") ? '"Light"' : v);
  for (let load = 0; load < 6; load += 1) run(js, tab);
  const stuck = tab.reloads;
  tab.local.setItem = realSet;               // the pin is gone
  const host = run(js, tab);
  check("still stood down on a bare load", tab.reloads === stuck, `got ${tab.reloads}`);
  // a storage event = somebody changed it somewhere else, on purpose
  realSet.call(tab.local, KEY, "dark");
  for (const fn of (tab.listeners.storage || [])) fn({ key: KEY, newValue: "dark" });
  check("a deliberate change is honoured", tab.reloads === stuck + 1, `got ${tab.reloads}`);
  check("stand-down cleared", tab.session.getItem("lex.theme.standown") === null);
}

// ── 6. Nothing at all agreed: the default is adopted, not the OS. ────────
{
  console.log("\n6. nothing stored anywhere, OS prefers dark");
  const tab = makeTab({ prefersDark: true });
  run(js, tab);
  check("adopts the light default", tab.local.getItem(stKey("/")) === '"Light"',
        String(tab.local.getItem(stKey("/"))));
  run(js, tab);
  check("settles", tab.reloads === 1, `got ${tab.reloads}`);
}

// ── 7. The shim's OTHER road: its storage write reaches the page too. ───
{
  console.log("\n7. a settled page; the shim publishes widget reports via storage");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  run(js, tab);                              // boot: adopts dark
  const host = run(js, tab);                 // the load that shows dark
  const before = tab.reloads;
  for (let tick = 0; tick < 20; tick += 1) {
    // exactly what _widget_host_component/frontend/index.html does, in order
    host.__lexThemeSelfReport = { mode: "light", at: Date.now() };
    tab.local.setItem(KEY, "light");
    for (const fn of (tab.listeners.storage || [])) fn({ key: KEY, newValue: "light" });
    host.__lexThemeFollow("light");
  }
  check("no reload while in use", tab.reloads === before, `${tab.reloads - before} interruptions`);
}

// ── 8. ...but a real change from a sibling tab still lands. ─────────────
{
  console.log("\n8. a settled page; somebody switches the theme in lex-app");
  const tab = makeTab();
  tab.local.setItem(KEY, "dark");
  run(js, tab);
  run(js, tab);                              // settled on dark
  const before = tab.reloads;
  tab.local.setItem(KEY, "light");           // no self-report mark: a real change
  for (const fn of (tab.listeners.storage || [])) fn({ key: KEY, newValue: "light" });
  check("honoured", tab.reloads === before + 1, `got ${tab.reloads - before}`);
  check("wrote Light", tab.local.getItem(stKey("/")) === '"Light"',
        String(tab.local.getItem(stKey("/"))));
}

// ── 9. The change ARRIVES as a widget report. It must be acted on. ──────
{
  console.log("\n9. same-site: lex-app switches to dark, a widget carries the news");
  const tab = makeTab();
  tab.local.setItem(KEY, "light");
  run(js, tab);                              // boot: page settles light
  const host = run(js, tab);
  const before = tab.reloads;

  // lex-app writes its preference; the widget frame shares that origin, reads
  // it, and tells the shim. This is the ONLY messenger in a same-site
  // deployment -- the relay's own write is a no-op because the shim already
  // wrote the same value, and storage events do not fire for unchanged values.
  host.__lexThemeSelfReport = { mode: "dark", at: Date.now() };
  tab.local.setItem(KEY, "dark");
  for (const fn of (tab.listeners.storage || [])) fn({ key: KEY, newValue: "dark" });
  host.__lexThemeFollow("dark");

  check("the page follows", tab.reloads === before + 1, `got ${tab.reloads - before}`);
  check("wrote Dark", tab.local.getItem(stKey("/")) === '"Dark"',
        String(tab.local.getItem(stKey("/"))));
}

console.log(failures ? `\n${failures} FAILED` : "\nall harness checks passed");
process.exit(failures ? 1 : 0);
