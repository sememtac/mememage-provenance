// =====================================================================
// Surfaces — one shared renderer for the "where the soul landed" list.
// Used by the dashboard mint-result card AND the conception page, so the
// layout is designed (and fixed) in exactly one place.
//
// Two lines per row — [✓ name] on top, the full URL (or error) wrapping
// below — so two variable-length strings never share a row and nothing
// truncates. Colors INHERIT from the host page; only the ok/fail accents
// are CSS tokens (--surface-ok / --surface-fail), so the same markup
// renders correctly on the light dashboard and the dark conception plate.
//
//   Surfaces.render(container, entries [, opts])
//     entries : [{ name, url, ok, error }]
//     opts    : { linkUrls: true }   // URLs clickable (<a>) vs plain text
// =====================================================================
(function () {
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function rowHTML(e, linkUrls) {
    var ok = !!e.ok;
    var status = ok ? '✓' : '✗';           // ✓ / ✗
    var stateClass = ok ? 'surface-row-ok' : 'surface-row-fail';
    var head =
      '<div class="surface-row-head">' +
        '<span class="surface-status" aria-hidden="true">' + status + '</span>' +
        '<span class="surface-name">' + esc(e.name) + '</span>' +
      '</div>';

    var detail = '';
    if (!ok) {
      var msg = e.error || 'failed';
      detail = '<span class="surface-detail surface-err" title="' + esc(e.error || '') +
               '">' + esc(msg) + '</span>';
    } else if (e.url && linkUrls) {
      detail = '<a class="surface-detail surface-url" href="' + esc(e.url) +
               '" target="_blank" rel="noopener">' + esc(e.url) + '</a>';
    } else if (e.url) {
      detail = '<span class="surface-detail surface-url">' + esc(e.url) + '</span>';
    }
    return '<li class="surface-row ' + stateClass + '">' + head + detail + '</li>';
  }

  window.Surfaces = {
    render: function (container, entries, opts) {
      if (!container) return;
      opts = opts || {};
      var linkUrls = opts.linkUrls !== false;
      var rows = (entries || []).map(function (e) { return rowHTML(e, linkUrls); });
      container.innerHTML = '<ul class="surfaces-list">' + rows.join('') + '</ul>';
    }
  };
})();
