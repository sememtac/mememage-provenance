// =====================================================================
// theme.js — Mememage L1 theme knobs (JS side)
//
// Companion to docs/css/theme.css. Same rules: presentation only,
// never verification. Loaded BEFORE every other JS module so other
// files can reference Theme.* at module load.
//
// What lives here:
//   - subtitle troves (decoder + validator voice)
//   - starfield warmth per page theme
//   - typewriter title animation weights (probability dial)
//
// What does NOT live here (L0 spine, locked):
//   - tab labels, MEMEMAGE wordmark
//   - badge labels (WITNESSED / AUTHENTICATED / EMBODIED)
//   - M/Y/C bar colors
//   - per-record audio derivation — L3, lives in cosmic-audio.js
//
// Themable items that live in HTML (edit the HTML to swap):
//   - Drop-zone glyph: <span class="drop-icon">&#9883;</span>
//     (U+269B atom symbol, in index.html and validator.html)
//   - Footer note text: <p class="note">…</p> (per page, near the
//     bottom of index.html / validator.html)
//   - Favicon SVG: <link rel="icon" href="data:image/svg+xml…">
// =====================================================================
var Theme = {
  // Subtitle troves — picked uniformly at random on page load. The
  // page's default is one entry in each, so it surfaces ~1/N visits.
  // Decoder voice tilts cosmic / biblical / witty / funny.
  // Validator voice tilts forensic / judicial.
  taglines: {
    decoder: [
      'decode the origin story of an image',
      'every image is a star with a birth certificate',
      'the sky at the moment of conception, in two pixels',
      'every image was conceived; few were witnessed',
      'by their hashes ye shall know them',
      'in the beginning was the image',
      'the body remembers; the soul testifies',
      'between conception and the world, a certificate',
      'every pixel has a lineage',
      'the bar in the pixels never lies',
      'two pixels, one truth',
      'the machine remembers what the moment forgot',
      'every image is a witness to its own birth',
      'what the machine made, the bar remembered',
      'the soul beneath the body of the image',
      'every conception leaves a record',
      'the spirit moved over the pixels',
      'for when "trust me bro" isn\u2019t enough',
      'every image has a back side',
      'every image confesses its origin',
      'the pixels confess their making'
    ],
    validator: [
      'validate the living memory of the cosmic chain',
      'the chain remembers, the bar testifies',
      'weigh the heart against the feather',
      'every record shall be tried by hash',
      'the witnesses are three: body, soul, signature',
      'judge not by sight, but by hash',
      'every record holds its place in the orbit',
      'the chain breathes; the records remember',
      'where the soul meets the witness',
      'the universe keeps its receipts',
      'validate, then trust',
      'trust by hash, not by hearsay',
      'the math remembers what the eye forgets',
      'tamper detection, in three colors',
      'every pixel under oath',
      'every record cross-examined',
      'auditor of the cosmic chain',
      'where the body meets its soul',
      'three witnesses, one truth',
      'the chain is watching',
      'every record confesses to the hash',
      'every forgery shall be made known'
    ],
    dashboard: [
      'configure, stage, and conceive',
      'the lock, the key, and the word \u2014 in one place',
      'where the creator setteth the seal',
      'mint, payload, identity \u2014 your control room',
      'the architecture, made plain',
      'stage the payload, sign the soul, send the body',
      'the chain begins where you stand',
      'every conception starts at this desk',
      'the workshop beneath the witness',
      'drop the body, the soul follows',
      'one click from pixels to provenance',
      'the workbench of the creator',
      'where presence becomes record',
      'the loom of the chain'
    ]
  },

  // Starfield atmosphere. Higher warmFreq sprinkles golden stars;
  // 0 keeps the field cool. Yang = decoder (light theme), yin =
  // validator (dark theme).
  starfield: {
    yangWarmFreq: 0.25,
    yinWarmFreq: 0
  },

  // Typewriter title animation weights — bigger weight = more
  // frequent. Animation BODIES live in typewriter.js; this is just
  // the probability dial. Vanilla weights below.
  typewriterWeights: {
    retype: 1,
    mistype: 3,
    glitch: 1,
    contemplate: 3,
    stutter: 2,
    imageFix: 2,
    memeAge: 2
  }
};
