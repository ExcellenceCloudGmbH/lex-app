/*
 * When the lex_view shim posts the host's filters into the embedded app.
 *
 * Kept apart from index.html so it can be run and tested on its own. The first
 * load carries the filters in the iframe URL; after that a change is POSTED, so
 * the frame keeps its state instead of reloading. A post happens only once the
 * frame has loaded, only when the host named filters, and only when they differ
 * from the ones the frame already has: feeding an embed's own filter event back
 * into lex_view(filters=...) must settle, not loop.
 */
(function (root) {
  function shouldPost(state) {
    if (!state || !state.loaded) return false;
    if (state.desired === null || state.desired === undefined) return false;
    var current = state.current === undefined ? null : state.current;
    return state.desired !== current;
  }

  var api = { shouldPost: shouldPost };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.LexViewFilterSync = api;
  }
})(this);
