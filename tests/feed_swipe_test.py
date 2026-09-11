"""Feed lightbox: swipe navigation on a touch screen.

Driven against a RUNNING local mint server (the feed needs /api/feed), so it is a
manual harness rather than a pytest case:

    python3 tests/feed_swipe_test.py          # server on https://localhost:8443

The body sits under ``if __name__ == "__main__"`` because pytest collects
``*_test.py`` by default, and this file only LOOKS like a test. With the browser
driven at module scope, merely IMPORTING it opened Chromium and hit
localhost:8443 — so `pytest tests/` aborted during collection with
ERR_CONNECTION_REFUSED wherever no server was up (exit 2, whole suite lost), and
silently drove a real browser against the live server wherever one was. Keep any
future harness in here behind the same guard.

Swipe navigation, with the click emitted only when a real browser would emit it.

A browser suppresses the trailing click once a touch has travelled past its slop
threshold (~10px), so a drag produces NO click. Modelling that is the difference
between a realistic test and one that invents a close.
"""
from playwright.sync_api import sync_playwright

SWIPE = """
([x0, y0, x1, y1, steps, emitClick]) => {
  const box = document.querySelector('.feed-lightbox');
  const mk = (x, y) => new Touch({ identifier: 1, target: box, clientX: x, clientY: y });
  const fire = (type, x, y) => {
    const t = mk(x, y);
    box.dispatchEvent(new TouchEvent(type, {
      touches: type === 'touchend' ? [] : [t], changedTouches: [t],
      bubbles: true, cancelable: true }));
  };
  fire('touchstart', x0, y0);
  for (let i = 1; i <= steps; i++)
    fire('touchmove', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps);
  fire('touchend', x1, y1);
  if (emitClick) box.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}
"""

def moved(a, b): return f"→ {b}" if a != b else "(unchanged)"

if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--ignore-certificate-errors"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                            is_mobile=True, ignore_https_errors=True)
        pg = ctx.new_page()
        pg.goto("https://localhost:8443/", wait_until="networkidle")
        pg.wait_for_timeout(800)
        shown = lambda: (pg.locator(".feed-lightbox-img").get_attribute("src") or "").split("/")[-1][-8:]
        is_open = lambda: pg.locator(".feed-lightbox.open").count() == 1
        pg.locator(".feed-tile").first.click(); pg.wait_for_timeout(500)
        print("opened:", shown())

        # (label, x0,y0,x1,y1,steps, browser emits click?, should navigate, should stay open)
        cases = [
            ("swipe LEFT (next)",       [300,400,120,410,6, False], True,  True),
            ("swipe LEFT (next)",       [300,400,120,415,6, False], True,  True),
            ("swipe RIGHT (previous)",  [120,400,300,405,6, False], True,  True),
            ("vertical drag",           [200,300,210,620,6, False], False, True),
            ("small nudge + click",     [200,400,188,402,3, True ], False, False),
        ]
        ok = True
        for label, args, should_nav, should_stay in cases:
            if not is_open():                      # reopen after an intentional close
                pg.locator(".feed-tile").first.click(); pg.wait_for_timeout(400)
            before = shown()
            pg.evaluate(SWIPE, args); pg.wait_for_timeout(400)
            after, open_now = shown(), is_open()
            # A closed lightbox has no src, so "" is not a navigation — only compare
            # while it is still open.
            navd = open_now and after != before
            good = (navd == should_nav) and (open_now == should_stay)
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'}  {label:22} {before} {moved(before, after):14} "
                  f"lightbox {'open' if open_now else 'closed'}")

        # the regression this round found: after a swipe with NO trailing click, the
        # very next tap must still close.
        if not is_open():
            pg.locator(".feed-tile").first.click(); pg.wait_for_timeout(400)
        pg.evaluate(SWIPE, [300,400,120,405,6, False]); pg.wait_for_timeout(350)
        pg.evaluate("""() => { const b=document.querySelector('.feed-lightbox');
          const t=new Touch({identifier:1,target:b,clientX:200,clientY:400});
          b.dispatchEvent(new TouchEvent('touchstart',{touches:[t],changedTouches:[t],bubbles:true}));
          b.dispatchEvent(new TouchEvent('touchend',{touches:[],changedTouches:[t],bubbles:true}));
          b.dispatchEvent(new MouseEvent('click',{bubbles:true})); }""")
        pg.wait_for_timeout(300)
        closed = not is_open()
        ok &= closed
        print(f"  {'PASS' if closed else 'FAIL'}  tap after a click-less swipe still closes")
        print("\nALL PASS" if ok else "\nFAILURES ABOVE")
        b.close()
