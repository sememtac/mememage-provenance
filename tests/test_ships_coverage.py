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


class TestReleaseVerifyChecksArtifacts(unittest.TestCase):
    """A release step must verify the ARTIFACT, not just the release object.

    v0.1.10 (2026-08-06) was cut as a prerelease, and its desktop build ran
    under workflow_dispatch rather than the release event, so no binaries were
    ever attached. The old probe asked "does the release exist?", which was
    true, so ships.py reported it shipped. /releases/latest skips prereleases
    and both installers pull from there, so every desktop user stayed on v0.1.9
    for six weeks with nothing reporting a problem.

    These pin the shape of the check, not the network result.
    """

    def _provenance_release_step(self):
        steps = ships.TARGETS["provenance"]["steps"]
        step = next(s for s in steps if s["id"] == "release")
        return step

    def test_asset_names_come_from_the_installers(self):
        # Read from docs/install.*, never hardcoded — a frozen copy here would
        # drift exactly like the sources lists this file already guards.
        names = ships._desktop_assets()
        for fn in ("install.sh", "install.ps1"):
            src = open(os.path.join(ROOT, "docs", fn), encoding="utf-8").read()
            for asset in re.findall(r"Mememage-Provenance-[A-Za-z]+(?:\.[A-Za-z]+)?", src):
                self.assertIn(asset, names,
                              f"{fn} downloads {asset} but the release check ignores it")

    def test_every_installer_asset_is_in_the_probe(self):
        verify = self._provenance_release_step()["verify"]
        for asset in ships._desktop_assets():
            self.assertIn(asset, verify,
                          f"release verify does not require {asset}")

    def test_probe_rejects_a_prerelease(self):
        # The other half of the v0.1.10 failure: a prerelease is invisible to
        # /releases/latest, which is what install.sh and install.ps1 fetch.
        verify = self._provenance_release_step()["verify"]
        self.assertIn("isPrerelease", verify)

    def test_probe_is_not_merely_release_exists(self):
        # The regression itself. `gh release view <tag>` alone passes for a
        # release with zero assets.
        verify = self._provenance_release_step()["verify"]
        self.assertIn("assets", verify)
        self.assertNotEqual(
            verify.strip(),
            "gh release view v{ver} -R sememtac/mememage-provenance",
            "existence-only probe is what let an empty release report as shipped")

    def test_hint_does_not_teach_prerelease(self):
        hint = self._provenance_release_step().get("hint") or ""
        self.assertNotIn("--prerelease", hint.split("(")[0],
                         "the hint must not suggest the flag that hid v0.1.10")
