// Run Reflex's OWN getBackendURL against the api_url lex_config resolves, on
// each hostname an instance answers on. The function is lifted verbatim out of
// the installed state.js -- its source text, not a re-implementation.
import fs from "node:fs";

const stateJs = fs.readFileSync(process.argv[2], "utf8");

const sameDomain = stateJs.match(/const SAME_DOMAIN_HOSTNAMES\s*=\s*\[[^\]]*\];/s);
const fn = stateJs.match(/export const getBackendURL\s*=\s*\(url_str\)\s*=>\s*\{[\s\S]*?\n\};/);
if (!sameDomain || !fn) {
  console.error("could not lift getBackendURL out of state.js");
  process.exit(2);
}

const API_URL = process.argv[3];          // what lex_config() resolved
const EVENT = API_URL + "/_event";
const source = `
  ${sameDomain[0]}
  const env = { PING: ${JSON.stringify(API_URL + "/ping")} };
  ${fn[0].replace("export const", "const")}
  globalThis.__getBackendURL = getBackendURL;
`;

let failures = 0, checks = 0;
const check = (cond, msg) => { checks++; if (!cond) { console.error("  - " + msg); failures++; } };

for (const host of process.argv.slice(4)) {
  globalThis.window = { location: { hostname: host, protocol: "https:" } };
  new Function(source)();
  const ws = globalThis.__getBackendURL(EVENT.replace(/^http/, "ws"));
  check(ws.hostname === host,
    `a page on ${host} addresses the backend at ${ws.hostname} -- the hostname is baked in`);
  check(ws.protocol === "wss:", `${host}: protocol was ${ws.protocol}, expected wss:`);
  check(ws.port === "", `${host}: port was ${ws.port!=="" ? ws.port : "(none)"}, expected none behind TLS`);
  console.log(`  ${host} -> ${ws.toString()}`);
}

// And the shape the old deployment compiled in: a public URL, which is NOT
// rewritten, so every other hostname talks to that one.
globalThis.window = { location: { hostname: "reflex.app.customer.com", protocol: "https:" } };
new Function(source)();
const pinned = globalThis.__getBackendURL("wss://inst-reflex.lit.excellence-cloud.de/_event");
check(pinned.hostname === "inst-reflex.lit.excellence-cloud.de",
  "a pinned public URL should NOT be rewritten (that is the bug)");
console.log(`  pinned api_url from reflex.app.customer.com -> ${pinned.toString()} (cross-origin: the bug)`);

console.log(`ran ${checks} getBackendURL checks`);
process.exit(failures ? 1 : 0);
