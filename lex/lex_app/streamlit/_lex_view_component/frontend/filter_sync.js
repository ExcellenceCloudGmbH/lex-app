/*
 * When the lex_view shim posts the host's filters into the embedded app.
 *
 * Kept apart from index.html so it can be run and tested on its own. The first
 * load carries the filters in the iframe URL (unless the token is too long for
 * it); after that a change is POSTED, so the frame keeps its state instead of
 * reloading. A post happens only once the frame has loaded, only when the host
 * named filters, and only when they differ from the ones the frame already has,
 * so re-passing the same filters on a rerun sends nothing. A page should still
 * pass a table's filters on to another table, not echo them back into the same
 * one: an echo that arrives one rerun late can undo the user's latest tick.
 */
(function (root) {
  function shouldPost(state) {
    if (!state || !state.loaded) return false;
    if (state.desired === null || state.desired === undefined) return false;
    var current = state.current === undefined ? null : state.current;
    return state.desired !== current;
  }

  /*
   * Right after ANY load of the frame -- the first one, a reload, a sign-in
   * round trip -- the app in it holds only the filters its URL carried (none
   * when the token was left out of the URL), whatever was posted to an earlier
   * page. Returns that token as the frame's current filters, and whether the
   * host's filters must be posted now.
   */
  function afterLoad(state) {
    var current = state && state.urlToken ? state.urlToken : null;
    var desired = state ? state.desired : null;
    return {
      current: current,
      post: shouldPost({ loaded: true, desired: desired, current: current }),
    };
  }

  var api = { shouldPost: shouldPost, afterLoad: afterLoad };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.LexViewFilterSync = api;
  }
})(this);
