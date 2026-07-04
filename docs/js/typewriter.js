// Typewriter idle animations for the MEMEMAGE title
// Click to trigger. Plays random animations at idle intervals.
(function() {
  var h1 = document.querySelector('.page-header h1');
  if (!h1) return;

  var TITLE = 'Mememage';
  var busy = false;
  var displayed = TITLE;

  // --- Cursor ---
  var cursor = document.createElement('span');
  cursor.style.cssText = 'display:inline-block;width:2px;height:0.8em;background:currentColor;margin-left:2px;vertical-align:baseline;opacity:0;transition:opacity 0.1s;';
  h1.appendChild(cursor);
  setInterval(function() { cursor.style.opacity = cursor.style.opacity === '0.4' ? '0' : '0.4'; }, 530);

  // --- Engine ---
  function set(s) { displayed = s; h1.textContent = s; h1.appendChild(cursor); }
  function wait(ms) { return new Promise(function(r) { setTimeout(r, ms); }); }
  function jitter(base, range) { return base + Math.random() * (range || base * 0.5); }

  async function type(str, speed) {
    for (var i = 0; i < str.length; i++) { set(displayed + str[i]); await wait(jitter(speed || 80, 60)); }
  }

  async function erase(n, speed) {
    for (var i = 0; i < n; i++) { set(displayed.slice(0, -1)); await wait(jitter(speed || 50, 40)); }
  }

  async function clear(speed) { await erase(displayed.length, speed || 35); }

  async function retype(str, speed) { await clear(speed); await wait(jitter(350)); await type(str, speed || 90); }

  // --- Animations ---
  // Each is an async function. Add new ones here.

  var ANIMS = {

    // Erase all, retype from scratch
    retype: { weight: 1, fn: function() { return retype(TITLE); } },

    // Typo: types a fumbled version, pauses, corrects
    mistype: { weight: 3, fn: async function() {
      var typos = ['Memeage', 'Memeemage', 'Mememmage', 'Memege', 'Mememge', 'Memamage'];
      var wrong = typos[Math.floor(Math.random() * typos.length)];
      var common = 0;
      while (common < TITLE.length && common < wrong.length &&
             TITLE[common].toLowerCase() === wrong[common].toLowerCase()) common++;
      await erase(displayed.length - common, 45);
      await wait(100);
      await type(wrong.slice(common), 70);
      await wait(jitter(600, 400));
      await erase(wrong.length - common, 35);
      await wait(200);
      await type(TITLE.slice(common), 80);
    }},

    // Rapid character scramble then settle
    glitch: { weight: 1, fn: async function() {
      var pool = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%&';
      for (var r = 0; r < 6; r++) {
        var g = '';
        for (var i = 0; i < TITLE.length; i++)
          g += Math.random() < 0.4 ? pool[Math.floor(Math.random() * pool.length)] : TITLE[i];
        set(g); await wait(jitter(50, 30));
      }
      for (var j = 0; j < TITLE.length; j++) {
        set(TITLE.slice(0, j + 1) + displayed.slice(j + 1)); await wait(30);
      }
      set(TITLE);
    }},

    // Erase to "Meme", pause thoughtfully, complete
    contemplate: { weight: 3, fn: async function() {
      await erase(displayed.length - 4, 45);
      await wait(jitter(1200, 800));
      await type(TITLE.slice(4), 100);
    }},

    // M...e...(pause)...memage
    stutter: { weight: 2, fn: async function() {
      await clear();
      await wait(300);
      await type('M', 80); await wait(200);
      await type('e', 80); await wait(jitter(400, 300));
      await type('memage', 70);
    }},

    // Types "image", erases back, retypes as Mememage
    imageFix: { weight: 2, fn: async function() {
      await clear();
      await wait(300);
      await type('image', 80);
      await wait(jitter(800, 400));
      await erase(3, 40); await wait(150); // "im"
      await erase(1, 40); await wait(100); // "i"
      await erase(1, 40); await wait(200); // ""
      await type('M', 90); await wait(100);
      await type('eme', 70); await wait(300);
      await type('mage', 75);
    }},

    // Types "Meme age" with space, erases space+age, completes
    memeAge: { weight: 2, fn: async function() {
      await clear();
      await wait(300);
      await type('Meme', 80); await wait(200);
      await type(' age', 80);
      await wait(jitter(900, 400));
      await erase(4, 40); await wait(300);
      await type('mage', 75);
    }},

    // Autocorrect refuses to believe "Mememage" is a real word.
    autocorrect: { weight: 2, fn: async function() {
      var guesses = ['Mirage', 'Message', 'Montage', 'Memento'];
      var wrong = guesses[Math.floor(Math.random() * guesses.length)];
      await erase(displayed.length - 1, 40);   // back to "M"
      await wait(120);
      await type(wrong.slice(1), 70);          // the "helpful" suggestion
      await wait(jitter(950, 500));
      await erase(wrong.length - 1, 45);        // no.
      await wait(250);
      await type('ememage', 80);                // Mememage. As stated.
    }},

    // A lowercase "meme" grows up into Mememage.
    evolve: { weight: 2, fn: async function() {
      await clear();
      await wait(300);
      await type('meme', 90);
      await wait(jitter(700, 400));
      set('Meme');                              // the M straightens up
      await wait(350);
      await type('mage', 75);
    }},

    // Wait — how many "me"s are in this thing?
    doubleMeme: { weight: 2, fn: async function() {
      await clear();
      await wait(250);
      await type('Meme', 85); await wait(220);
      await type('me', 85); await wait(jitter(550, 300));   // "Mememe"…
      await erase(2, 45); await wait(300);                   // one too many
      await type('mage', 75);
    }},

    // Each letter winks in like a star.
    cosmic: { weight: 1, fn: async function() {
      await clear();
      await wait(250);
      for (var i = 0; i < TITLE.length; i++) {
        set(displayed + '*'); await wait(jitter(90, 50));    // a spark
        set(TITLE.slice(0, i + 1));                          // resolves to the letter
        await wait(jitter(55, 40));
      }
    }},

    // Overexcited, then composes itself.
    caps: { weight: 1, fn: async function() {
      await clear();
      await wait(250);
      await type('MEMEMAGE', 95);               // SHOUTING
      await wait(jitter(650, 300));
      set('Mememage');                          // …sorry.
    }},

    // The mage was at the end all along — cast slowly.
    mage: { weight: 1, fn: async function() {
      await erase(displayed.length - 4, 50);    // down to "Meme"
      await wait(jitter(600, 300));
      await type('m', 210); await wait(160);    // an incantation
      await type('a', 210); await wait(160);
      await type('g', 210); await wait(160);
      await type('e', 210);
    }},

    // MEM = memory. It pauses to recall the rest.
    memory: { weight: 1, fn: async function() {
      await erase(displayed.length - 3, 45);    // "Mem"
      await wait(300);
      await type('…', 90);                  // "Mem…"
      await wait(jitter(1000, 600));
      await erase(1, 40);                         // drop the …
      await type('emage', 85);                   // recalled
    }},

    // Teases being a palindrome. It is not.
    palindrome: { weight: 1, fn: async function() {
      await clear(); await wait(250);
      await type('Meme', 80); await wait(150);
      await type('emeM', 80);                     // "MemeemeM" — mirror
      await wait(jitter(850, 400));
      await erase(4, 40); await wait(250);
      await type('mage', 75);
    }},

    // Streeetch.
    stretch: { weight: 1, fn: async function() {
      await clear(); await wait(200);
      await type('Me', 80);
      await type('eeee', 60);                     // Meeeee
      await wait(jitter(550, 300));
      await erase(4, 45); await wait(200);         // snap back to "Me"
      await type('memage', 70);
    }},

    // Mememage is all of these:  😂 meme · 🖼️ image · ⏳ age · 🧙 mage
    emoji: { weight: 1, fn: async function() {
      var faces = ['😂', '🖼️', '⏳', '🧙'];
      await clear(); await wait(200);
      for (var i = 0; i < faces.length; i++) { set(faces[i]); await wait(jitter(480, 240)); }
      set(''); await wait(150);
      await type('Mememage', 85);
      await wait(jitter(650, 300));
      set('Mememage ✨');                          // ta-da
      await wait(jitter(750, 350));
      set('Mememage');
    }},

    // A little cat wanders in, blinks, and meows. it all started with a cat.
    cat: { weight: 1, fn: async function() {
      await clear(); await wait(250);
      await type('=^..^=', 95);                   // here, kitty
      await wait(jitter(600, 300));
      set('=^--^='); await wait(170);             // blink
      set('=^..^='); await wait(jitter(500, 300));
      set('=^..^= meow'); await wait(jitter(850, 350));
      await clear(); await wait(200);
      await type('Mememage', 80);
    }}
  };

  // --- Scheduler ---
  // Animation probability weights are themable via docs/js/theme.js
  // (Theme.typewriterWeights). The weights baked into ANIMS above are
  // the vanilla skin defaults; theme.js can override per Age.
  var keys = Object.keys(ANIMS);
  var twWeights = (typeof Theme !== 'undefined') && Theme.typewriterWeights;
  if (twWeights) {
    keys.forEach(function(k) {
      if (typeof twWeights[k] === 'number') ANIMS[k].weight = twWeights[k];
    });
  }
  var totalWeight = keys.reduce(function(s, k) { return s + ANIMS[k].weight; }, 0);

  function pick() {
    var r = Math.random() * totalWeight, s = 0;
    for (var i = 0; i < keys.length; i++) {
      s += ANIMS[keys[i]].weight;
      if (r < s) return ANIMS[keys[i]].fn;
    }
    return ANIMS[keys[0]].fn;
  }

  async function run(anim) {
    if (busy) return;
    busy = true;
    try { await anim(); } catch(e) { set(TITLE); }
    busy = false;
  }

  function schedule() {
    setTimeout(function() { run(pick()).then(schedule); }, jitter(12000, 18000));
  }

  // Click to trigger
  h1.style.cursor = 'default';
  h1.addEventListener('click', function() { run(pick()); });

  // Expose the REAL registry so the showcase (docs/typewriter-lab.html) can
  // drive these exact animations by name — one source of truth, no drift.
  // run(fn) plays one and resolves when it finishes.
  window.MememageTitle = { run: run, anims: ANIMS, names: keys };

  // Start idle loop — unless a host page opts out (the showcase triggers
  // animations by name instead of leaving them to the random scheduler).
  if (!window.MEMEMAGE_NO_AUTOPLAY) setTimeout(schedule, jitter(5000, 5000));
})();
