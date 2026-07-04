// =====================================================================
// CODEC CONSTANTS
// =====================================================================
const SIG_ROWS=2,HEADER_BAND=8,HEADER_PIXELS=24,FOOTER_PIXELS=24,PIXELS_PER_BIT=3,RGB_THRESHOLD=128;
// Bar WRITER constants — must mirror mememage/bar.py exactly (parity-tested).
// PIXELS_PER_BIT(3) is the even-fill crossover; sequential picks the WIDEST ppb
// that fits, up to PIXELS_PER_BIT_MAX(6). Decoders sweep 6..2 widest-first.
// RGB_THRESHOLD(128) is only a benign scalar default for the decode helpers.
const PIXELS_PER_BIT_NARROW=2,PIXELS_PER_BIT_MAX=6,RS_NSYM=6;
// Asym row-3-copy camo: data bits ride a per-column center copying the smoothed
// content one row above the bar ("1"=center invisible, "0"=center-ASYM_DELTA,
// filler="1"). Box-blur radius (NOT Gaussian — exp diverges glibc↔V8, breaking
// byte-exact writer parity). Mirror mememage/bar.py exactly.
const ASYM_DELTA=40,ASYM_FLOOR=50,ASYM_BOX_RADIUS=34,ASYM_SCALE_CAP=2.0;
// Even-fill frame byte-length sweep. Packed frame = 8B header + 20..27B payload +
// 6B parity = 34..41B; ASCII fallback larger, so 33..64B with margin; CRC selects.
const EVENFILL_MIN_BYTES=33,EVENFILL_MAX_BYTES=64;

// Default source URL for record fetches. {id} expands to the
// identifier; the decoder + validator both probe several filename
// variants under this base. Self-hosters override via the Source URL
// input (one per page; persisted in localStorage by SourceConfig).
// A self-hosted mint server injects window.MEMEMAGE_SOULS_BASE (an
// absolute souls read base ending in /) before this script loads, so a
// self-served decoder/validator defaults to its OWN souls face. On GitHub
// Pages nothing is injected, so the reference public surface souls.mememage.art
// is the default (where the reference chain's souls live — IA is no longer the
// reference chain's surface). IA stays a one-click suggestion for legacy/IA-
// hosted records. Either way the Source field is user-overridable — source-
// agnostic by design.
const SOURCE_DEFAULT = (typeof window !== 'undefined' && window.MEMEMAGE_SOULS_BASE)
  ? window.MEMEMAGE_SOULS_BASE
  : 'https://souls.mememage.art/';

// Fill the Source field's <datalist> with this host's default AND Internet
// Archive, so a decoder (Pages or self-hosted) still offers IA as a one-click
// autocomplete option for legacy/IA-hosted records — no extra dropdown, no extra
// copy. When the default already IS IA the list dedupes to one.
function populateSourceSuggestions() {
  if (typeof document === 'undefined') return;
  var dl = document.getElementById('sourceSuggest');
  if (!dl) return;
  var seen = {}, html = '';
  [SOURCE_DEFAULT, 'https://archive.org/download/{id}/'].forEach(function (v) {
    if (v && !seen[v]) { seen[v] = 1; html += '<option value="' + v + '"></option>'; }
  });
  dl.innerHTML = html;
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', populateSourceSuggestions);
  } else {
    populateSourceSuggestions();
  }
}

// =====================================================================
// ASSET RESOLUTION — packer injects INLINE_ASSETS before this script
// =====================================================================
function assetUrl(path) {
  return (typeof INLINE_ASSETS !== 'undefined' && INLINE_ASSETS[path]) || path;
}

// =====================================================================
// CELESTIAL READINGS
// =====================================================================
const READINGS = {
  sun:{Aries:'Born under a fire-starter sun. This image does not ask permission.',Taurus:'The sun held still. Built to last.',Gemini:'Twin-sun energy \u2014 says two things at once and means both.',Cancer:'The sun turned inward. Remembers something you forgot.',Leo:'Sun at full theater. Demands to be seen.',Virgo:'The sun measured twice. Every pixel deliberate.',Libra:'Sun in the scales. Trying to be fair to everyone in it.',Scorpio:'Sun in deep water. Knows more than it shows.',Sagittarius:'Sun aimed for the horizon. Going somewhere.',Capricorn:'Sun climbing the mountain. This image has ambition.',Aquarius:'Sun went sideways. Does not care about your expectations.',Pisces:'Sun dissolved. A feeling more than a place.'},
  moon:{'New Moon':'The moon was dark \u2014 emerged from total absence.','Waxing Crescent':'A sliver of intention. The moon was just beginning to commit.','First Quarter':'Half-lit. The moon was making a decision.','Waxing Gibbous':'Almost full. The moon was holding its breath.','Full Moon':'The moon was completely exposed. Nothing hidden.','Waning Gibbous':'The moon had just exhaled. Created in the afterglow.','Last Quarter':'Half the light was leaving. A sense of release.','Waning Crescent':'The last sliver. Born at the edge of disappearance.'},
  mercury:{Aries:'Mercury thinking fast, breaking things.',Taurus:'Mercury thinking slowly, meaning it.',Gemini:'Mercury was home. Prompt-to-pixel fidelity: maximum.',Cancer:'Mercury feeling the words instead of thinking them.',Leo:'Mercury being dramatic about the prompt.',Virgo:'Mercury editing. Every token weighed.',Libra:'Mercury negotiating between what you said and what you meant.',Scorpio:'Mercury reading between the lines.',Sagittarius:'Mercury paraphrasing freely. Creative license taken.',Capricorn:'Mercury following instructions to the letter.',Aquarius:'Mercury interpreting the prompt in a way nobody expected.',Pisces:'Mercury dreaming the prompt instead of reading it.'},
  venus:{Aries:'Venus wanted bold beauty. Subtlety not invited.',Taurus:'Venus was home. Aesthetic uncompromising.',Gemini:'Venus couldn\'t pick one vibe, picked two.',Cancer:'Venus reached for nostalgia.',Leo:'Venus demanded glamour.',Virgo:'Venus being particular about composition.',Libra:'Venus was home. Harmony non-negotiable.',Scorpio:'Venus went dark. Beauty with teeth.',Sagittarius:'Venus wanted the exotic.',Capricorn:'Venus being elegant. Restrained luxury.',Aquarius:'Venus went weird. An acquired taste.',Pisces:'Venus dissolved into pure atmosphere.'},
  mars:{Aries:'Mars was home and fully armed.',Taurus:'Mars slow but unstoppable.',Gemini:'Mars multitasking the GPU.',Cancer:'Mars protecting something.',Leo:'Mars performing. Main character energy.',Virgo:'Mars precise. Surgical generation.',Libra:'Mars trying diplomacy. Tension under the surface.',Scorpio:'Mars in the dark. Intensity was the only setting.',Sagittarius:'Mars aimed far. Ambitious render.',Capricorn:'Mars disciplined. Generation followed the plan.',Aquarius:'Mars rebelling against the prompt.',Pisces:'Mars fighting ghosts. Spectral energy.'},
  jupiter:{Aries:'Jupiter expanding recklessly.',Taurus:'Jupiter accumulating. Abundance in every pixel.',Gemini:'Jupiter multiplying ideas.',Cancer:'Jupiter nurturing. Grown like something tended.',Leo:'Jupiter amplifying. Everything turned up.',Virgo:'Jupiter optimizing. Expansion through refinement.',Libra:'Jupiter balancing growth.',Scorpio:'Jupiter going deep. Hidden layers.',Sagittarius:'Jupiter was home. Cosmic scope.',Capricorn:'Jupiter building structure.',Aquarius:'Jupiter innovating. Pushes the format.',Pisces:'Jupiter dreaming big. Transcends its medium.'},
  saturn:{Aries:'Saturn testing courage. Earned its existence.',Taurus:'Saturn demanding durability.',Gemini:'Saturn imposing clarity.',Cancer:'Saturn guarding boundaries.',Leo:'Saturn humbling the spotlight.',Virgo:'Saturn enforcing standards.',Libra:'Saturn weighing justice.',Scorpio:'Saturn in the deep. Constraints made it stronger.',Sagittarius:'Saturn limiting the horizon. Focus over freedom.',Capricorn:'Saturn was home. This image is load-bearing.',Aquarius:'Saturn restructuring reality.',Pisces:'Saturn dissolving limits.'},
};
const BODIES=[
  {k:'sun',s:'\u2609',n:'Sun'},
  {k:'moon',s:'\u263D',n:'Moon'},
  {k:'mercury',s:'\u263F',n:'Mercury'},
  {k:'venus',s:'\u2640',n:'Venus'},
  {k:'mars',s:'\u2642',n:'Mars'},
  {k:'jupiter',s:'\u2643',n:'Jupiter'},
  {k:'saturn',s:'\u2644',n:'Saturn'},
];

// =====================================================================
// CELESTIAL HELPERS
//
// V1 souls store planetary positions as {sign: int(0-11), deg: float}
// and moon phase as {phase: int(0-7), illum: float(0-1)}. Display
// layers reconstruct the human strings ("Aries 24.3°", "First Quarter
// (37.4%)") via the helpers below. For backwards compatibility with
// pre-V1 dev records that stored these as strings, each helper accepts
// either shape and Just Works — V4 vintage records still render even
// though their content_hash won't verify.
// =====================================================================
const ZODIAC_NAMES = ['Aries','Taurus','Gemini','Cancer','Leo','Virgo','Libra','Scorpio','Sagittarius','Capricorn','Aquarius','Pisces'];
const MOON_PHASE_NAMES = [
  'New Moon','Waxing Crescent','First Quarter','Waxing Gibbous',
  'Full Moon','Waning Gibbous','Last Quarter','Waning Crescent'
];

// Resolve a position record (V1 dict OR legacy string) to its zodiac
// sign name. Falls back to 'Aries' on anything malformed so callers
// don't crash on garbage records.
function signName(pos) {
  if (pos && typeof pos === 'object' && typeof pos.sign === 'number') {
    return ZODIAC_NAMES[pos.sign] || 'Aries';
  }
  if (typeof pos === 'string' && pos) {
    return pos.split(' ')[0] || 'Aries';
  }
  return 'Aries';
}

// Degree within the sign (0.0–30.0). Returns 0 on garbage.
function signDegree(pos) {
  if (pos && typeof pos === 'object' && typeof pos.deg === 'number') {
    return pos.deg;
  }
  if (typeof pos === 'string' && pos) {
    var d = parseFloat(pos.split(' ')[1]);
    return isNaN(d) ? 0 : d;
  }
  return 0;
}

// Full ecliptic longitude (0–360°) — sign_idx * 30 + deg_in_sign.
// Returns null when the input is unrecognized so callers can skip.
function parseDegrees(pos) {
  if (pos && typeof pos === 'object' && typeof pos.sign === 'number'
      && typeof pos.deg === 'number') {
    return pos.sign * 30 + pos.deg;
  }
  if (typeof pos === 'string' && pos) {
    var parts = pos.split(' ');
    var sign = parts[0];
    var deg = parseFloat(parts[1]);
    var idx = ZODIAC_NAMES.indexOf(sign);
    if (idx < 0 || isNaN(deg)) return null;
    return idx * 30 + deg;
  }
  return null;
}

// Render a position as "Aries 24.3°" for display.
function formatPosition(pos) {
  var name = signName(pos);
  var deg = signDegree(pos);
  return name + ' ' + deg.toFixed(1) + '\u00b0';
}

// Resolve a moon-phase record (V1 dict OR legacy string) to its
// phase name only ("First Quarter", no percentage).
function moonPhaseName(mp) {
  if (mp && typeof mp === 'object' && typeof mp.phase === 'number') {
    return MOON_PHASE_NAMES[mp.phase] || 'New Moon';
  }
  if (typeof mp === 'string' && mp) {
    return mp.split('(')[0].trim() || 'New Moon';
  }
  return 'New Moon';
}

// Illumination percentage (0–100) — present in V1 records (illum
// stored as 0..1 float); for legacy strings parses the "(81.2%)"
// suffix.
function moonIllumPct(mp) {
  if (mp && typeof mp === 'object' && typeof mp.illum === 'number') {
    return mp.illum * 100;
  }
  if (typeof mp === 'string' && mp) {
    var m = mp.match(/\(([\d.]+)%\)/);
    if (m) return parseFloat(m[1]) || 0;
  }
  return 0;
}

// Combined display string: "First Quarter (81.2%)".
function formatMoonPhase(mp) {
  return moonPhaseName(mp) + ' (' + moonIllumPct(mp).toFixed(1) + '%)';
}

// =====================================================================
// VITALS DISPLAY HELPERS
//
// V1 souls store vitals as compact numerics:
//   bytes (int) — ram, mem_*, net_*
//   list[3]     — load
//   dict        — cores, cache, page_faults, ctx_switches, disk_io, power
//   int code    — platform (0=darwin, 1=linux, 2=other)
// Display helpers turn them back into human strings for the cert.
// Each accepts legacy string passthrough so pre-V1 records still render.
// =====================================================================
var PLATFORM_NAMES = ['darwin', 'linux', 'other'];

function platformName(code) {
  if (typeof code === 'string') return code.toLowerCase();
  if (typeof code === 'number') return PLATFORM_NAMES[code] || 'other';
  return 'other';
}

// Format byte count to human-readable GB / MB / KB / B.
function formatBytes(b) {
  if (b === null || b === undefined) return '';
  if (typeof b === 'string') return b;  // legacy passthrough
  if (typeof b !== 'number') return '' + b;
  var units = ['B', 'KB', 'MB', 'GB', 'TB'];
  var i = 0;
  var v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  // GB+ → 1 decimal; MB/KB → 1 decimal; B → integer
  return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
}

// RAM is rounded to whole GB on the cert ("96 GB" not "95.4 GB").
function formatRam(b) {
  if (b === null || b === undefined) return '';
  if (typeof b === 'string') return b;
  if (typeof b !== 'number') return '' + b;
  return (b / (1024 * 1024 * 1024)).toFixed(0) + ' GB';
}

// Cores — V1 dict {total, p, e} or legacy string "16P + 8E" / "24".
function formatCores(c) {
  if (c === null || c === undefined) return '';
  if (typeof c === 'string') return c;
  if (typeof c === 'number') return '' + c;
  if (typeof c === 'object') {
    if (typeof c.p === 'number' && typeof c.e === 'number') {
      return c.p + 'P + ' + c.e + 'E';
    }
    if (typeof c.total === 'number') return '' + c.total;
  }
  return '';
}

// Cache — V1 dict {l1, l2} bytes; legacy string "L1 128K / L2 4M".
function formatCache(c) {
  if (!c) return '';
  if (typeof c === 'string') return c;
  if (typeof c === 'object') {
    var parts = [];
    if (typeof c.l1 === 'number') parts.push('L1 ' + (c.l1 / 1024).toFixed(0) + 'K');
    if (typeof c.l2 === 'number') parts.push('L2 ' + (c.l2 / (1024 * 1024)).toFixed(0) + 'M');
    return parts.join(' / ');
  }
  return '';
}

// Load — V1 list [1m, 5m, 15m]; legacy "2.39 / 2.38 / 2.24" string.
function formatLoad(l) {
  if (!l) return '';
  if (typeof l === 'string') return l;
  if (Array.isArray(l)) {
    return l.map(function(x) { return (+x).toFixed(2); }).join(' / ');
  }
  return '' + l;
}

// Power — V1 dict {src: 0|1|2, pct: int?}; legacy "AC" / "Battery 75%".
function formatPower(p) {
  if (!p) return '';
  if (typeof p === 'string') return p;
  if (typeof p === 'object') {
    var src = p.src;
    if (src === 0) return p.pct != null ? 'AC (' + p.pct + '%)' : 'AC';
    if (src === 1) return p.pct != null ? 'Battery ' + p.pct + '%' : 'Battery';
    return 'Unknown';
  }
  return '';
}

// Disk I/O — V1 dict (varies by platform: {tps,kb_per_t,mb_per_s} or
// {read_kbs,write_kbs} or {tps}); legacy single-line string.
function formatDiskIO(d) {
  if (!d) return '';
  if (typeof d === 'string') return d;
  if (typeof d === 'object') {
    var parts = [];
    if (typeof d.kb_per_t === 'number') parts.push(d.kb_per_t.toFixed(2) + ' KB/t');
    if (typeof d.tps === 'number') parts.push(d.tps.toFixed(0) + ' tps');
    if (typeof d.mb_per_s === 'number') parts.push(d.mb_per_s.toFixed(2) + ' MB/s');
    if (typeof d.read_kbs === 'number') parts.push((d.read_kbs).toFixed(0) + ' KB/s read');
    if (typeof d.write_kbs === 'number') parts.push((d.write_kbs).toFixed(0) + ' KB/s write');
    return parts.join(', ');
  }
  return '';
}

// Page faults — V1 dict {soft, hard}; legacy "10303 soft / 196 hard".
function formatPageFaults(pf) {
  if (!pf) return '';
  if (typeof pf === 'string') return pf;
  if (typeof pf === 'object') {
    return (pf.soft || 0) + ' soft / ' + (pf.hard || 0) + ' hard';
  }
  return '';
}

// Ctx switches — V1 dict {vol, invol}; legacy "39 voluntary / 2500 involuntary".
function formatCtxSwitches(cs) {
  if (!cs) return '';
  if (typeof cs === 'string') return cs;
  if (typeof cs === 'object') {
    return (cs.vol || 0) + ' voluntary / ' + (cs.invol || 0) + ' involuntary';
  }
  return '';
}

// Uptime seconds → "5d 3h" display.
function formatUptime(secs) {
  if (typeof secs !== 'number' || secs < 0) return '';
  var days = Math.floor(secs / 86400);
  var hours = Math.floor((secs % 86400) / 3600);
  return days + 'd ' + hours + 'h';
}

// Convenience: numeric byte value or 0 if missing/legacy unparseable.
// Used by sky-band.js for animation magnitude scaling.
function bytesValue(v) {
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    var m = v.match(/([\d.]+)\s*(TB|GB|MB|KB|B)/i);
    if (m) {
      var n = parseFloat(m[1]);
      var u = m[2].toUpperCase();
      if (u === 'TB') return n * 1024 * 1024 * 1024 * 1024;
      if (u === 'GB') return n * 1024 * 1024 * 1024;
      if (u === 'MB') return n * 1024 * 1024;
      if (u === 'KB') return n * 1024;
      return n;
    }
    return parseFloat(v) || 0;
  }
  return 0;
}

// =====================================================================
// CANVAS HELPERS
// =====================================================================
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function wrapText(ctx, text, maxWidth) {
  var words = text.split(' ');
  var lines = [];
  var line = '';
  for (var i = 0; i < words.length; i++) {
    var test = line ? line + ' ' + words[i] : words[i];
    if (ctx.measureText(test).width > maxWidth && line) {
      lines.push(line);
      line = words[i];
    } else {
      line = test;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function font(weight, size, italic) {
  return (italic ? 'italic ' : '') + weight + ' ' + size + 'px "JetBrains Mono", monospace';
}
function symFont(weight, size) {
  return weight + ' ' + size + 'px "Symbols Nerd Font", "Apple Symbols", serif';
}

// =====================================================================
// BIRTH TRAIT DEFINITIONS — icons, display names, tooltips
// =====================================================================
var BIRTH_TRAITS = {
  // CPU contention — silver
  contested:      { name: 'Contested',      cat: 'CPU',    metal: 'silver', desc: 'Threads jostling at the moment of birth' },
  yielding:       { name: 'Yielding',       cat: 'CPU',    metal: 'silver', desc: 'A brief window of cooperation' },
  uncontested:    { name: 'Uncontested',    cat: 'CPU',    metal: 'silver', desc: 'A calm moment between storms' },
  // Memory faults — bronze
  stumbling:      { name: 'Stumbling',      cat: 'Memory', metal: 'bronze', desc: 'A page fault at the exact moment of conception' },
  sure_footed:    { name: 'Sure-footed',    cat: 'Memory', metal: 'silver', desc: 'The memory was aligned' },
  reaching:       { name: 'Reaching',       cat: 'Memory', metal: 'silver', desc: 'Hard faults echoed through the birth' },
  // Speculation — bronze/gold
  speculative:    { name: 'Speculative',    cat: 'OS',     metal: 'bronze', desc: 'The OS was racing ahead of the program' },
  cautious:       { name: 'Cautious',       cat: 'OS',     metal: 'bronze', desc: 'Taking no risks with memory' },
  restless:       { name: 'Restless',       cat: 'OS',     metal: 'silver', desc: 'Speculating but unsure' },
  // Purgeable pages — bronze/gold
  loosening_grip: { name: 'Loosening Grip', cat: 'Pages',  metal: 'gold',   desc: 'The machine was letting go of memory' },
  holding_tight:  { name: 'Holding Tight',  cat: 'Pages',  metal: 'bronze', desc: 'Every page was precious, nothing to spare' },
  // File descriptors — gold
  entangled:      { name: 'Entangled',      cat: 'I/O',    metal: 'gold',   desc: 'File descriptors aligned at a round number' },
  unraveled:      { name: 'Unraveled',      cat: 'I/O',    metal: 'silver', desc: 'The connections were fraying at the edges' },
  // Load — silver/gold (forged_in_fire is the rare overload tier)
  under_pressure: { name: 'Under Pressure', cat: 'Load',   metal: 'silver', desc: 'The system was straining' },
  forged_in_fire: { name: 'Forged in Fire', cat: 'Load',   metal: 'gold',   desc: 'The machine was overwhelmed at the moment of birth' },
  in_silence:     { name: 'In Silence',     cat: 'Load',   metal: 'silver', desc: 'The machine was barely conscious' },
  // Memory in flux — bronze
  in_flux:        { name: 'In Flux',        cat: 'Pages',  metal: 'bronze', desc: 'The machine was deciding what to keep' },
  // Power — bronze/gold (battery state)
  last_light:     { name: 'Last Light',     cat: 'Power',  metal: 'gold',   desc: 'Born as power was fading' },
  untethered:     { name: 'Untethered',     cat: 'Power',  metal: 'bronze', desc: 'Born free from the wall' },
  // Time — silver/gold
  night_owl:      { name: 'Night Owl',      cat: 'Time',   metal: 'silver', desc: 'The world was asleep' },
  dawn:           { name: 'Dawn',           cat: 'Time',   metal: 'gold',   desc: 'Born at first light' },
};
