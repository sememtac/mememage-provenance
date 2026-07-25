"""Every file that ships somewhere must be owned by a ships.py target.

`tools/ships.py` answers "I changed X — what has to ship?". Its `sources` lists
were hand-written and drifted from what the publish scripts actually ship, so
the tool answered "nothing to ship" for files that were live on the VPS. Audit
findings (2026-07-24), all now fixed and locked here:

  * docs/css/dashboard.css, docs/dashboard.html, docs/conception.html,
    docs/css/theme.css and every image asset matched NO target — editing them
    deployed nothing, while the cachebust step was already re-stamping
    dashboard.html.
  * docs/privacy.html, robots.txt, sitemap.xml and img/og.png are copied by
    sync-product.sh but were missing from `product`.
  * `core` claimed ALL of mememage/ (~50 modules) though publish-core.sh ships
    five; every workshop edit prescribed a pointless PyPI release.
  * Narrowing `core` would have orphaned the workshop modules the VPS runs, so
    `vps` now owns mememage/ outright.

The remaining rule is bundling: the desktop app packs the whole web UI and the
provenance tree carries all of mememage/, so both go stale without a release.
That is reported as a note, not a step.
"""
import importlib.util
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("ships", os.path.join(ROOT, "tools", "ships.py"))
ships = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ships)


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [f for f in out.stdout.split() if f]


def _claims(f):
    return [t for t in ships.TARGETS if ships.match(t, [f])]


class TestEveryLiveFileIsOwned(unittest.TestCase):

    def test_served_docs_files_have_a_target(self):
        """docs/ is served by the VPS, so every file in it must deploy —
        except docs/plans/, which is workshop-only."""
        orphans = [f for f in _tracked()
                   if f.startswith("docs/") and not f.startswith("docs/plans/")
                   and not _claims(f)]
        self.assertEqual(orphans, [], f"{len(orphans)} docs files ship nowhere: {orphans[:10]}")

    def test_workshop_python_reaches_the_vps(self):
        orphans = [f for f in _tracked()
                   if f.startswith("mememage/") and f.endswith(".py") and not _claims(f)]
        self.assertEqual(orphans, [], f"modules the VPS runs but nothing deploys: {orphans[:10]}")

    def test_internal_plans_never_deploy(self):
        for f in ("docs/plans/roadmap.md", "docs/plans/roadmap.html",
                  "docs/plans/photoshop-uxp.md"):
            with self.subTest(path=f):
                self.assertEqual(_claims(f), [], f"{f} is workshop-only and must not ship")


class TestCoreOwnsExactlyWhatItPublishes(unittest.TestCase):

    def test_core_sources_match_the_publish_allow_list(self):
        """ships.py derives the module list from publish-core.sh; prove they agree."""
        script = open(os.path.join(ROOT, "tools", "publish-core.sh"), encoding="utf-8").read()
        mods = re.search(r"CORE_MODULES=\(([^)]*)\)", script).group(1).split()
        expected = {f"mememage/{m}.py" for m in mods}
        got = {s for s in ships.TARGETS["core"]["sources"] if s.startswith("mememage/")}
        self.assertEqual(got, expected)

    def test_workshop_only_modules_do_not_trigger_a_core_release(self):
        for f in ("mememage/server.py", "mememage/mint.py", "mememage/celestial.py",
                  "mememage/chains.py", "mememage/tray.py"):
            with self.subTest(path=f):
                self.assertNotIn("core", _claims(f),
                                 f"{f} ships nothing to PyPI but would prescribe a core release")

    def test_core_modules_do_trigger_core(self):
        for f in ("mememage/api.py", "mememage/bar.py", "mememage/hashing.py"):
            with self.subTest(path=f):
                self.assertIn("core", _claims(f))


class TestBundlingIsReported(unittest.TestCase):

    def test_docs_change_marks_the_desktop_bundle_stale(self):
        """The gap that motivated this: a docs-only fix never reached desktop users."""
        notes = dict(ships.bundle_notes(["docs/js/dashboard.js"]))
        self.assertIn("provenance", notes)

    def test_workshop_python_marks_the_provenance_tree_stale(self):
        notes = dict(ships.bundle_notes(["mememage/celestial.py"]))
        self.assertIn("provenance", notes)

    def test_internal_plans_are_not_bundled(self):
        self.assertEqual(ships.bundle_notes(["docs/plans/roadmap.md"]), [])


if __name__ == "__main__":
    unittest.main()
