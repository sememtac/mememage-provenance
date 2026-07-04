// The surface — an endless wall of recently-conceived images. Each tile is a
// thumbnail of the actual conceived image (light/public chains only); click it
// to see the full-resolution image in a lightbox. Pure catalog: no links out,
// no nav, no pages — what you scroll is what you get. A conception drops off
// when its image culls (~7 days) or its soul is removed.
(function () {
  // Point the header links at the decode face (decoder + validator pages).
  // These live at the ORIGIN of the souls host — the public souls domain on a
  // split deployment, or the local server itself on a single-domain / desktop
  // install (which serves /decoder + /validator inline). MEMEMAGE_SOULS_BASE is
  // the souls *read* base and can carry a path (…/api/souls/), so take its
  // ORIGIN — a naive string append yields …/api/souls/decoder, which 404s.
  var soulsBase = (window.MEMEMAGE_SOULS_BASE || '').trim();
  var origin;
  try {
    origin = soulsBase ? new URL(soulsBase, location.href).origin : location.origin;
  } catch (e) {
    origin = location.origin;
  }
  var decLink = document.getElementById('feedDecoderLink');
  var valLink = document.getElementById('feedValidatorLink');
  if (decLink) decLink.href = origin + '/decoder';
  if (valLink) valLink.href = origin + '/validator';

  var grid = document.getElementById('feedGrid');
  if (!grid) return;

  // Lightbox, built once. Click anywhere (or Escape) to close.
  var box = document.createElement('div');
  box.className = 'feed-lightbox';
  var boxImg = document.createElement('img');
  boxImg.className = 'feed-lightbox-img';
  boxImg.alt = '';
  box.appendChild(boxImg);
  box.addEventListener('click', close);
  // Append to <html>, not <body>: mememage.css's `body > *` rule forces every
  // body child to position:relative, which would break the fixed full-screen
  // centering. As an html child it keeps position:fixed and centers properly.
  document.documentElement.appendChild(box);

  var currentId = null;
  function open(id) {
    currentId = id;
    boxImg.removeAttribute('src');
    boxImg.src = '/api/feed/full/' + encodeURIComponent(id);
    box.classList.add('open');
  }
  function close() { box.classList.remove('open'); boxImg.removeAttribute('src'); currentId = null; }

  // Arrow keys step through the tiles in DOM order (newest-first) while the
  // lightbox is open. Re-reads the live tile list each press, so anything
  // infinite-scroll appended is in range. Stepping onto the last loaded tile
  // primes the next page so the following press has somewhere to go.
  function nav(delta) {
    var tiles = Array.prototype.slice.call(grid.querySelectorAll('.feed-tile'));
    var idx = -1;
    for (var i = 0; i < tiles.length; i++) {
      if (tiles[i].getAttribute('data-id') === currentId) { idx = i; break; }
    }
    if (idx === -1) return;
    var t = idx + delta;
    if (t < 0 || t >= tiles.length) return;
    open(tiles[t].getAttribute('data-id'));
    if (t >= tiles.length - 1) loadMore();
  }
  document.addEventListener('keydown', function (e) {
    if (!box.classList.contains('open')) return;
    if (e.key === 'Escape') { close(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); nav(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); nav(1); }
  });

  function escAttr(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Delegated click — works for tiles appended later by infinite scroll.
  grid.addEventListener('click', function (e) {
    var tile = e.target.closest('.feed-tile');
    if (tile) open(tile.getAttribute('data-id'));
  });

  function tileHTML(it) {
    var id = encodeURIComponent(it.identifier);
    return '<div class="feed-tile" data-id="' + escAttr(it.identifier) + '">' +
      '<img src="/api/feed/thumb/' + id + '" loading="lazy" alt="" ' +
      'onerror="var t=this.closest(&quot;.feed-tile&quot;); if(t)t.style.display=&quot;none&quot;">' +
      '</div>';
  }

  // --- Infinite scroll -------------------------------------------------
  // Page through the full eligible set by offset; append tiles as the bottom
  // sentinel nears the viewport. `seen` dedupes across pages so a conception
  // minted between two page loads (which shifts the newest-first list) can't
  // double up. Images stay lazy (loading="lazy") — only what's on screen
  // actually fetches, so a long DOM stays cheap.
  var PAGE = 60;
  var offset = 0, loading = false, done = false;
  var seen = Object.create(null);

  var sentinel = document.createElement('div');
  sentinel.className = 'feed-sentinel';
  grid.parentNode.insertBefore(sentinel, grid.nextSibling);

  function loadMore() {
    if (loading || done) return;
    loading = true;
    fetch('/api/feed?offset=' + offset + '&limit=' + PAGE)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var feed = (d && d.feed) || [];
        var html = '';
        for (var i = 0; i < feed.length; i++) {
          var it = feed[i];
          if (!it || !it.identifier || seen[it.identifier]) continue;
          seen[it.identifier] = 1;
          html += tileHTML(it);
        }
        if (html) grid.insertAdjacentHTML('beforeend', html);
        offset = (typeof d.next_offset === 'number') ? d.next_offset : (offset + feed.length);
        if (!d || !d.has_more || feed.length === 0) done = true;
        loading = false;
        // Tall screen / short first page: keep filling until the sentinel is
        // pushed below the viewport (or we run out).
        maybeFill();
      })
      .catch(function () { loading = false; /* quiet — an empty surface is just quiet */ });
  }

  function maybeFill() {
    if (done || loading) return;
    var r = sentinel.getBoundingClientRect();
    if (r.top < window.innerHeight + 400) loadMore();
  }

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) loadMore();
    }, { rootMargin: '800px' });
    io.observe(sentinel);
  } else {
    window.addEventListener('scroll', maybeFill, { passive: true });
    window.addEventListener('resize', maybeFill, { passive: true });
  }

  loadMore();  // first page
})();
