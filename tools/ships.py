#!/usr/bin/env python3
"""ships — the Mememage release captain.

Give it a range of commits. It works out which publishable units the change touches,
adds everything downstream (parity mirrors, vendored copies, web deploys), orders it
upstream-first, and walks the plan: it RUNS the automatable steps and PAUSES at the
steps a human must do (npm 2FA, a Chrome Web Store upload, a version bump, an Age seal),
then waits for the human to finish and verifies before it goes on.

    tools/ships.py plan                 # what a change since the last tag would ship
    tools/ships.py plan --since v0.1.7  # ...since a specific ref
    tools/ships.py plan --targets detector,resolver   # force explicit units
    tools/ships.py run  --targets detector,resolver   # execute the plan
    tools/ships.py resume               # continue a paused run after a manual step
    tools/ships.py status               # show a paused run

Design notes:
  • The DAG lives in TARGETS below — one entry per publishable unit, with the source
    paths that trigger it, the units downstream of it, and its ordered steps.
  • A step is `auto` (ships.py runs the command) or `manual` (a human runs it; ships.py
    waits). A manual step with a `verify` probe is polled until it passes; without one,
    it needs an explicit "done".
  • The extension VENDORS the SDK/detector/resolver (copies source; it does not
    npm-install them), so shipping it always re-vendors + runs the machine test first —
    the guard that keeps the vendored copies honest. See docs/plans/release-propagation.md.
  • Nothing irreversible runs without a confirmation (TTY) or --yes (non-TTY).
  • A run persists to .ships/state.json so it can pause for a human and `resume`.
"""
import argparse, json, os, re, select, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".ships", "state.json")

# ---- version readers -------------------------------------------------------
def _pyproject_version(rel):
    txt = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "?"

def _json_version(rel):
    return json.load(open(os.path.join(ROOT, rel), encoding="utf-8")).get("version", "?")


def _core_modules():
    """The mememage/*.py files publish-core.sh actually copies to PyPI.

    Read from the script itself, so the graph cannot drift from the allow-list.
    Falls back to the known set if the script is unreadable.
    """
    try:
        src = open(os.path.join(ROOT, "tools", "publish-core.sh"), encoding="utf-8").read()
        mods = re.search(r"CORE_MODULES=\(([^)]*)\)", src).group(1).split()
        return [f"mememage/{m}.py" for m in mods]
    except Exception:
        return [f"mememage/{m}.py" for m in ("api", "bar", "rs", "hashing", "crypto")]

# ---- the dependency graph --------------------------------------------------
# Each target: title, `sources` (path prefixes that trigger it), `version` (callable ->
# str), `downstream` (units to also ship), and `steps`. `parity` (core only) marks the
# files whose change forces the JS SDK to mirror. A step: id, kind, desc, and either
# `run` (auto shell command) or `hint`/`verify` (manual instructions + a probe).
CORE_CONTRACT = (
    "mememage/bar.py", "mememage/hashing.py", "mememage/core.py", "mememage/watermark.py",
    "mememage/access.py", "mememage/crypto.py", "mememage/rs.py", "mememage/api.py",
)

TARGETS = {
    "core": {
        "title": "PyPI mememage (core) + sememtac/mememage",
        # Exactly what publish-core.sh copies — read from the script so the two
        # cannot drift. `mememage/` as a whole is NOT the core: the workshop
        # package holds ~50 more modules (server, mint, chains, celestial…) that
        # ship nothing to PyPI, and listing the directory made every workshop
        # edit prescribe a pointless core release.
        "sources": ["packaging/core/"] + _core_modules(),
        "version": lambda: _pyproject_version("packaging/core/pyproject.toml"),
        "parity": CORE_CONTRACT,                 # if a contract file changed → mirror the SDK
        "downstream": ["provenance", "vps"],
        "parity_downstream": ["sdk"],
        "steps": [
            {"id": "gate+push", "kind": "auto", "irreversible": True,
             "desc": "build core, fresh-venv test gate, append-push to sememtac/mememage",
             "run": "VERIFY=1 PUSH=1 bash tools/publish-core.sh"},
            {"id": "release", "kind": "manual", "irreversible": True,
             "desc": "cut the GitHub release that trips PyPI trusted-publishing",
             "hint": "gh release create v{ver} -R sememtac/mememage --generate-notes",
             "verify": "curl -sfo /dev/null https://pypi.org/pypi/mememage/{ver}/json"},
        ],
    },
    "sdk": {
        "title": "npm mememage (JS SDK)",
        "sources": ["packaging/js/"],
        "version": lambda: _json_version("packaging/js/package.json"),
        "downstream": ["extension"],
        "steps": [
            {"id": "gate", "kind": "auto",
             "desc": "SDK test gate (Python↔JS parity + smoke)",
             "run": "npm test", "cwd": "packaging/js"},
            {"id": "publish", "kind": "manual",
             "desc": "npm publish the SDK (browser 2FA)",
             "hint": "cd packaging/js && npm publish",
             "verify": "npm view mememage@{ver} version"},
        ],
    },
    "detector": {
        "title": "npm mememage-detector",
        "sources": ["packaging/detector/"],
        "version": lambda: _json_version("packaging/detector/package.json"),
        "downstream": ["extension"],
        "steps": [
            {"id": "build+test", "kind": "auto",
             "desc": "rebuild the plain-script global + smoke test",
             "run": "node build.mjs && node test/smoke.mjs", "cwd": "packaging/detector"},
            {"id": "publish", "kind": "manual",
             "desc": "npm publish mememage-detector (browser 2FA)",
             "hint": "npm publish /Users/andyxiao/Developer/Mememage/packaging/detector",
             "verify": "npm view mememage-detector@{ver} version"},
            {"id": "mirror", "kind": "auto", "irreversible": True,
             "desc": "append-push source to sememtac/mememage-detector",
             "run": "bash tools/publish-push.sh packaging/detector "
                    "git@github.com:sememtac/mememage-detector.git 'Release mememage-detector v{ver}'"},
        ],
    },
    "resolver": {
        "title": "npm mememage-resolver",
        "sources": ["packaging/resolver/"],
        "version": lambda: _json_version("packaging/resolver/package.json"),
        "downstream": ["extension"],
        "steps": [
            {"id": "test", "kind": "auto",
             "desc": "resolver unit tests (every verdict branch)",
             "run": "node test/resolver.test.mjs", "cwd": "packaging/resolver"},
            {"id": "publish", "kind": "manual",
             "desc": "npm publish mememage-resolver (browser 2FA)",
             "hint": "npm publish /Users/andyxiao/Developer/Mememage/packaging/resolver",
             "verify": "npm view mememage-resolver@{ver} version"},
            {"id": "mirror", "kind": "auto", "irreversible": True,
             "desc": "append-push source to sememtac/mememage-resolver",
             "run": "bash tools/publish-push.sh packaging/resolver "
                    "git@github.com:sememtac/mememage-resolver.git 'Release mememage-resolver v{ver}'"},
        ],
    },
    "extension": {
        "title": "Chrome Web Store + sememtac/mememage-chrome",
        "sources": ["packaging/extension/"],
        "version": lambda: _json_version("packaging/extension/manifest.json"),
        "downstream": [],
        "steps": [
            {"id": "vendor", "kind": "auto",
             "desc": "re-vendor SDK + detector + resolver (coupling ② — keep copies fresh)",
             "run": "bash sync-vendor.sh", "cwd": "packaging/extension"},
            {"id": "gate", "kind": "auto",
             "desc": "the machine test — the gate that keeps vendored copies honest",
             "run": "python3 gen-testpage.py >/dev/null && python3 machine-test.py",
             "cwd": "packaging/extension"},
            {"id": "zip", "kind": "auto",
             "desc": "assemble the clean store zip",
             "run": "bash build-store-zip.sh", "cwd": "packaging/extension"},
            {"id": "push", "kind": "auto", "irreversible": True,
             "desc": "append-push to sememtac/mememage-chrome",
             "run": "PUSH=1 bash tools/publish-chrome.sh"},
            {"id": "cws", "kind": "manual",
             "desc": "upload the zip to the Chrome Web Store and submit for review",
             "hint": "Upload packaging/extension/store/mememage-chrome-{ver}.zip at\n"
                     "https://chrome.google.com/webstore/devconsole → Package → Submit."},
        ],
    },
    "comfy": {
        "title": "Comfy registry mememage-comfy",
        "sources": ["packaging/comfy/"],
        "version": lambda: _pyproject_version("packaging/comfy/pyproject.toml"),
        "downstream": [],
        "steps": [
            {"id": "push", "kind": "auto", "irreversible": True,
             "desc": "append-push to sememtac/mememage-comfy",
             "run": "bash tools/publish-push.sh packaging/comfy "
                    "git@github.com:sememtac/mememage-comfy.git 'Release mememage-comfy v{ver}'"},
            {"id": "release", "kind": "manual", "irreversible": True,
             "desc": "cut the GitHub release (tag → registry publish workflow)",
             "hint": "gh release create v{ver} -R sememtac/mememage-comfy --generate-notes",
             "verify": "gh release view v{ver} -R sememtac/mememage-comfy"},
        ],
    },
    "provenance": {
        "title": "sememtac/mememage-provenance → desktop binaries",
        "sources": ["pyproject.toml"],       # root pyproject = provenance/desktop version
        # The demo BUNDLES both trees: publish-provenance.sh ships all of
        # mememage/, and the desktop binary packs the whole web UI
        # (tools/mememage_app.spec: datas=[(DOCS,"docs")]). Changing either does
        # not demand a release, but the shipped copy IS stale until one — so it
        # is reported as a note. Before this, a docs-only fix never reached a
        # desktop user and nothing said so.
        "bundles": ["mememage/", "docs/"],
        "exclude": ["docs/plans/"],
        "version": lambda: _pyproject_version("pyproject.toml"),
        "downstream": [],
        "steps": [
            {"id": "push", "kind": "auto", "irreversible": True,
             "desc": "append-push the provenance tree",
             "run": "PUSH=1 bash tools/publish-provenance.sh"},
            {"id": "release", "kind": "manual", "irreversible": True,
             "desc": "cut the GitHub release (triggers build-desktop.yml → binaries)",
             "hint": "gh release create v{ver} -R sememtac/mememage-provenance --generate-notes",
             "verify": "gh release view v{ver} -R sememtac/mememage-provenance"},
        ],
    },
    "product": {
        "title": "mememage.art (product + install page)",
        # Everything sync-product.sh copies. privacy.html, robots.txt,
        # sitemap.xml and the og card were being copied by the script but were
        # missing here, so editing them shipped nothing.
        "sources": ["docs/product.html", "docs/install.html", "docs/install.sh",
                    "docs/install.ps1", "docs/css/product.css", "docs/privacy.html",
                    "docs/robots.txt", "docs/sitemap.xml", "docs/img/og.png",
                    "docs/img/mememage-icon.png"],
        "version": lambda: "",
        "downstream": [],
        "steps": [
            {"id": "sync", "kind": "auto", "irreversible": True,
             "desc": "publish the product/install page to mememage.art",
             "run": "bash tools/sync-product.sh"},
        ],
    },
    "webface": {
        "title": "souls/mint.mememage.art (decoder, validator, feed)",
        # The VPS serves docs/ directly, so the whole directory is the deploy
        # unit — not a hand-listed subset. The old list missed dashboard.html,
        # conception.html, dashboard.css, theme.css, layout.css and every image
        # asset: editing any of them deployed NOTHING, while the cachebust step
        # was already re-stamping dashboard.html. docs/plans/ is workshop-only
        # and must never trigger a deploy.
        "sources": ["docs/"],
        "exclude": ["docs/plans/"],
        "version": lambda: "",
        "downstream": [],
        "steps": [
            {"id": "cachebust", "kind": "auto",
             "desc": "re-stamp the ?v= cache-bust to HEAD and commit",
             "run": "perl -pi -e \"s/\\?v=[a-z0-9]+/?v=$(git rev-parse --short HEAD)/g\" "
                    "docs/index.html docs/validator.html docs/feed.html docs/dashboard.html && "
                    "git add docs/*.html && git commit -q -m 'deploy: re-stamp cache-bust' || true"},
            {"id": "push", "kind": "auto", "irreversible": True,
             "desc": "push to the workshop origin",
             "run": "git push origin main"},
            {"id": "vpspull", "kind": "auto", "irreversible": True,
             "desc": "pull the VPS (souls face serves docs/ directly)",
             "run": "ssh -o ConnectTimeout=12 axiao@160.153.182.117 "
                    "'cd /home/axiao/mememage-src && git pull --ff-only origin main'"},
            {"id": "drift", "kind": "manual",
             "desc": "clear the mint dashboard chain-drift badge",
             "hint": "In the mint dashboard, run payload.build() to clear the 'Update pending' "
                     "badge (payload sources changed). Re-inlined into chunks at the next SEAL."},
        ],
    },
    "vps": {
        "title": "VPS mint server (server/mint Python)",
        # The VPS runs the WHOLE workshop package, not just the server modules.
        # Listing only server/mint/channels/notifiers left every other module
        # (chains, celestial, rarity, payload…) reaching the VPS with nothing
        # telling you to pull. It used to be covered by accident, because `core`
        # claimed all of `mememage/` and the VPS is downstream of core; narrowing
        # core to its real allow-list would have opened that hole.
        "sources": ["mememage/"],
        "version": lambda: "",
        "downstream": [],
        "steps": [
            {"id": "pull+restart", "kind": "auto", "irreversible": True,
             "desc": "pull the VPS and restart the mint service",
             "run": "ssh -o ConnectTimeout=12 axiao@160.153.182.117 "
                    "'cd /home/axiao/mememage-src && git pull --ff-only origin main && "
                    "systemctl --user restart mememage-mint'"},
        ],
    },
}

# Upstream-first execution order.
ORDER = ["core", "sdk", "detector", "resolver", "provenance", "comfy",
         "extension", "product", "webface", "vps"]

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "0": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}

def sh(cmd, cwd=None):
    return subprocess.run(cmd, shell=True, cwd=os.path.join(ROOT, cwd) if cwd else ROOT)

def probe(cmd):
    return subprocess.run(cmd, shell=True, cwd=ROOT,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

# ---- change detection + planning -------------------------------------------
def changed_files(base):
    out = subprocess.run(["git", "diff", "--name-only", base + "...HEAD"],
                         cwd=ROOT, capture_output=True, text=True)
    files = [f for f in out.stdout.splitlines() if f.strip()]
    # include uncommitted, so a mid-work `plan` is honest
    out2 = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    files += [l[3:] for l in out2.stdout.splitlines() if l[3:].strip()]
    return sorted(set(files))

def match(target, files):
    """Files that belong to a target: a source prefix hit, minus any exclusion.

    `exclude` exists because a target can own a whole directory except for the
    internal corner of it (docs/ is the live web UI; docs/plans/ is workshop-only
    and must never trigger a deploy).
    """
    src = TARGETS[target]["sources"]
    exc = TARGETS[target].get("exclude", ())
    hits = [f for f in files if any(f == s or f.startswith(s) for s in src)]
    return [f for f in hits if not any(f == e or f.startswith(e) for e in exc)]


def bundle_notes(files):
    """Targets whose SHIPPED COPY of a changed file is now stale.

    A bundling target embeds files it does not list as sources — the desktop app
    packs the whole web UI (tools/mememage_app.spec: datas=[(DOCS,"docs")]), and
    the provenance tree carries all of mememage/. Those copies go stale the
    moment the workshop changes, but staleness does not demand an immediate
    release, so this reports instead of adding a step. Without it a docs-only fix
    (a dashboard bug, say) silently never reaches desktop users.
    """
    out = []
    for t, tgt in TARGETS.items():
        pats = tgt.get("bundles", ())
        hit = [f for f in files
               if any(f == p or f.startswith(p) for p in pats)
               and not any(f == e or f.startswith(e) for e in tgt.get("exclude", ()))]
        if hit:
            out.append((t, hit))
    return out

def plan_targets(base=None, forced=None, no_downstream=False):
    """Return an ordered list of (target_id, reason)."""
    selected = {}
    if forced:
        for t in forced:
            selected.setdefault(t, "requested")
    else:
        files = changed_files(base)
        for t in TARGETS:
            hits = match(t, files)
            if hits:
                selected.setdefault(t, "changed: " + ", ".join(hits[:3]) + ("…" if len(hits) > 3 else ""))
    if no_downstream:                       # leaf-only: ship exactly what was named/changed
        return [(t, selected[t]) for t in ORDER if t in selected]
    # transitive downstream closure (+ parity for core)
    queue = list(selected)
    while queue:
        t = queue.pop()
        tgt = TARGETS[t]
        extra = list(tgt.get("downstream", []))
        if tgt.get("parity") and not forced:
            files = changed_files(base)
            if any(f in tgt["parity"] for f in files):
                extra += tgt.get("parity_downstream", [])
        elif tgt.get("parity") and forced and t in forced:
            extra += tgt.get("parity_downstream", [])   # explicit core → assume parity
        for d in extra:
            if d not in selected:
                selected[d] = "downstream of " + t
                queue.append(d)
    return [(t, selected[t]) for t in ORDER if t in selected]

def fmt(target, step):
    ver = TARGETS[target]["version"]()
    def sub(s): return s.replace("{ver}", ver) if s else s
    return sub(step.get("run")), sub(step.get("hint")), sub(step.get("verify"))

# ---- state -----------------------------------------------------------------
def save_state(order, done, base):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"order": order, "done": done, "base": base}, open(STATE, "w"), indent=2)

def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else None

def clear_state():
    if os.path.exists(STATE):
        os.remove(STATE)

# ---- the manual-step wait --------------------------------------------------
def wait_manual(target, step, poll=20, timeout=3600):
    run, hint, verify = fmt(target, step)
    print(f"\n{C['y']}┌─ MANUAL STEP · {target} · {step['id']}{C['0']}")
    print(f"{C['y']}│{C['0']} {step['desc']}")
    for line in (hint or "").splitlines():
        print(f"{C['y']}│{C['0']}   {C['b']}{line}{C['0']}")
    if verify:
        print(f"{C['y']}│{C['0']} {C['d']}verify: {verify}{C['0']}")
    print(f"{C['y']}└─{C['0']}")

    tty = sys.stdin.isatty()
    if verify and probe(verify):
        print(f"{C['g']}✓ already satisfied{C['0']}")
        return True

    if not tty:
        # Non-interactive (e.g. run from an agent): pause the run so a human can act,
        # then `ships.py resume` re-checks and continues.
        print(f"{C['c']}⏸ paused. Do the step above, then: tools/ships.py resume{C['0']}")
        return False

    # Interactive: wait for it to happen. Poll the probe; Enter re-checks now.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if verify:
            print(f"{C['d']}waiting… (Enter = re-check now · s = skip · a = abort){C['0']}", flush=True)
            r, _, _ = select.select([sys.stdin], [], [], poll)
            if r:
                line = sys.stdin.readline().strip().lower()
                if line == "a":
                    print(f"{C['r']}aborted{C['0']}"); sys.exit(1)
                if line == "s":
                    print(f"{C['y']}skipped (unverified){C['0']}"); return True
            if probe(verify):
                print(f"{C['g']}✓ verified{C['0']}"); return True
        else:
            ans = input(f"{C['c']}Press Enter when done (a = abort): {C['0']}").strip().lower()
            if ans == "a":
                print(f"{C['r']}aborted{C['0']}"); sys.exit(1)
            return True
    print(f"{C['r']}timed out waiting for verification{C['0']}"); return False

def confirm_irreversible(target, step, run, yes):
    if not step.get("irreversible"):
        return True
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"{C['r']}refusing irreversible step {target}/{step['id']} without --yes "
              f"(non-interactive){C['0']}")
        return False
    ans = input(f"{C['y']}⚠ irreversible: {run}\n  proceed? [y/N] {C['0']}").strip().lower()
    return ans == "y"

# ---- the runner ------------------------------------------------------------
def do_step(target, step, yes):
    run, hint, verify = fmt(target, step)
    if step["kind"] == "auto":
        print(f"\n{C['c']}▶ {target} · {step['id']}{C['0']} — {step['desc']}")
        print(f"{C['d']}  $ {run}{C['0']}")
        if not confirm_irreversible(target, step, run, yes):
            return False
        rc = sh(run, step.get("cwd")).returncode
        if rc != 0:
            print(f"{C['r']}✗ step failed (exit {rc}) — stopping{C['0']}")
            return False
        print(f"{C['g']}✓ {target} · {step['id']}{C['0']}")
        return True
    return wait_manual(target, step)

def run_plan(order, done, base, yes):
    for target in order:
        steps = TARGETS[target]["steps"]
        start = done.get(target, 0)
        if start >= len(steps):
            continue
        print(f"\n{C['b']}══ {target} — {TARGETS[target]['title']}{C['0']}")
        for i in range(start, len(steps)):
            ok = do_step(target, steps[i], yes)
            if not ok:
                done[target] = i           # leave this step pending
                save_state(order, done, base)
                return False
            done[target] = i + 1
            save_state(order, done, base)
    clear_state()
    print(f"\n{C['g']}✔ all targets shipped{C['0']}")
    return True

# ---- CLI -------------------------------------------------------------------
def default_base():
    r = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "HEAD~1"

def print_notes(base):
    """Report bundling targets whose shipped copy just went stale."""
    try:
        files = changed_files(base)
    except Exception:
        return
    notes = bundle_notes(files)
    if not notes:
        return
    print(f"\n{C['b']}Stale bundled copies (no release required, but they carry it):{C['0']}")
    for t, hit in notes:
        ver = TARGETS[t]["version"]()
        print(f"  {C['y']}·{C['0']} {t}{' v' + ver if ver else ''} bundles "
              f"{len(hit)} changed file(s), e.g. {hit[0]} — its shipped copy is stale "
              f"until the next {t} release.")


def print_plan(pairs):
    if not pairs:
        print("nothing to ship."); return
    print(f"{C['b']}Ships, upstream-first:{C['0']}")
    for t, why in pairs:
        ver = TARGETS[t]["version"]()
        vtag = f" v{ver}" if ver else ""
        print(f"\n  {C['b']}{t}{vtag}{C['0']}  {C['d']}({why}){C['0']}")
        print(f"    {TARGETS[t]['title']}")
        for s in TARGETS[t]["steps"]:
            run, hint, verify = fmt(t, s)
            tag = f"{C['g']}auto{C['0']}" if s["kind"] == "auto" else f"{C['y']}MANUAL{C['0']}"
            warn = f" {C['r']}⚠irreversible{C['0']}" if s.get("irreversible") else ""
            print(f"      [{tag}]{warn} {s['id']}: {s['desc']}")

def main():
    ap = argparse.ArgumentParser(prog="ships", description="Mememage release captain")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--since", help="base ref (default: last tag)")
        p.add_argument("--targets", help="comma-list of unit ids to force")
        p.add_argument("--no-downstream", action="store_true",
                       help="ship only the named/changed units, skip the downstream closure")
        if name == "run":
            p.add_argument("--yes", action="store_true", help="don't prompt on irreversible auto steps")
    sub.add_parser("resume").add_argument("--yes", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()

    if a.cmd == "status":
        st = load_state()
        if not st:
            print("no paused run."); return
        print(f"paused run (base {st['base']}):")
        for t in st["order"]:
            n, total = st["done"].get(t, 0), len(TARGETS[t]["steps"])
            mark = "✓" if n >= total else f"{n}/{total}"
            print(f"  {mark:>4}  {t}")
        return

    if a.cmd == "resume":
        st = load_state()
        if not st:
            print("no paused run to resume."); return
        run_plan(st["order"], st["done"], st["base"], getattr(a, "yes", False))
        return

    base = a.since or default_base()
    forced = [t.strip() for t in a.targets.split(",")] if a.targets else None
    if forced:
        bad = [t for t in forced if t not in TARGETS]
        if bad:
            print(f"unknown targets: {', '.join(bad)}\nknown: {', '.join(TARGETS)}"); sys.exit(2)
    pairs = plan_targets(base if not forced else None, forced, a.no_downstream)

    if a.cmd == "plan":
        print(f"{C['d']}base: {base}{'  (forced targets)' if forced else ''}{C['0']}\n")
        print_plan(pairs)
        if not forced:
            print_notes(base)
        return

    # run
    order = [t for t, _ in pairs]
    if not order:
        print("nothing to ship."); return
    print_plan(pairs)
    if sys.stdin.isatty():
        if input(f"\n{C['b']}run this plan? [y/N] {C['0']}").strip().lower() != "y":
            print("cancelled."); return
    run_plan(order, {}, base, a.yes)

if __name__ == "__main__":
    main()
