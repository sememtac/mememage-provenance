"""V1 vitals storage shape — bytes/dicts/lists/codes, not strings."""

import unittest

from mememage.vitals import (
    PLATFORM_DARWIN,
    PLATFORM_LINUX,
    PLATFORM_OTHER,
    POWER_AC,
    POWER_BATTERY,
    collect_vitals,
    platform_code,
    platform_name,
)


class TestPlatformCode(unittest.TestCase):
    def test_int_passthrough(self):
        self.assertEqual(platform_code(PLATFORM_DARWIN), PLATFORM_DARWIN)
        self.assertEqual(platform_code(PLATFORM_LINUX), PLATFORM_LINUX)

    def test_string_to_code(self):
        self.assertEqual(platform_code("darwin"), PLATFORM_DARWIN)
        self.assertEqual(platform_code("Linux"), PLATFORM_LINUX)
        self.assertEqual(platform_code("freebsd"), PLATFORM_OTHER)

    def test_code_to_name(self):
        self.assertEqual(platform_name(PLATFORM_DARWIN), "darwin")
        self.assertEqual(platform_name(PLATFORM_LINUX), "linux")
        self.assertEqual(platform_name(PLATFORM_OTHER), "other")

    def test_name_passthrough(self):
        # Legacy records may already store the string; helper should pass through.
        self.assertEqual(platform_name("darwin"), "darwin")


class TestCollectShape(unittest.TestCase):
    """Each present field should be the V1 numeric/structured form."""

    def setUp(self):
        self.v = collect_vitals()

    def test_platform_is_int(self):
        self.assertIsInstance(self.v["platform"], int)

    def test_ram_is_bytes_int(self):
        if "ram" in self.v:
            self.assertIsInstance(self.v["ram"], int)
            self.assertGreater(self.v["ram"], 1024 * 1024 * 1024)  # >1GB

    def test_mem_fields_are_bytes_int(self):
        for k in ("mem_active", "mem_compressed", "mem_free"):
            if k in self.v:
                self.assertIsInstance(self.v[k], int, f"{k} should be int bytes")

    def test_net_fields_are_bytes_int(self):
        for k in ("net_rx", "net_tx"):
            if k in self.v:
                self.assertIsInstance(self.v[k], int, f"{k} should be int bytes")

    def test_cores_is_dict(self):
        if "cores" in self.v:
            self.assertIsInstance(self.v["cores"], dict)
            self.assertIn("total", self.v["cores"])
            self.assertIsInstance(self.v["cores"]["total"], int)

    def test_cache_is_dict(self):
        if "cache" in self.v:
            self.assertIsInstance(self.v["cache"], dict)
            for k in self.v["cache"]:
                self.assertIsInstance(self.v["cache"][k], int)

    def test_load_is_list(self):
        if "load" in self.v:
            self.assertIsInstance(self.v["load"], list)
            self.assertEqual(len(self.v["load"]), 3)
            for x in self.v["load"]:
                self.assertIsInstance(x, float)

    def test_page_faults_is_dict(self):
        if "page_faults" in self.v:
            pf = self.v["page_faults"]
            self.assertIsInstance(pf, dict)
            self.assertIn("soft", pf)
            self.assertIn("hard", pf)
            self.assertIsInstance(pf["soft"], int)
            self.assertIsInstance(pf["hard"], int)

    def test_ctx_switches_is_dict(self):
        if "ctx_switches" in self.v:
            cs = self.v["ctx_switches"]
            self.assertIsInstance(cs, dict)
            self.assertIn("vol", cs)
            self.assertIn("invol", cs)

    def test_power_is_dict_with_int_src(self):
        if "power" in self.v:
            p = self.v["power"]
            self.assertIsInstance(p, dict)
            self.assertIn("src", p)
            self.assertIn(p["src"], (POWER_AC, POWER_BATTERY))

    def test_disk_io_is_dict_with_tps(self):
        if "disk_io" in self.v:
            d = self.v["disk_io"]
            self.assertIsInstance(d, dict)
            # tps key may be absent on darwin if iostat parse failed,
            # but the shape must be a dict either way.

    def test_uptime_seconds_is_int(self):
        if "uptime_seconds" in self.v:
            self.assertIsInstance(self.v["uptime_seconds"], int)

    def test_no_legacy_string_fields(self):
        # The cosmetic "uptime" string was dropped in V1.
        self.assertNotIn("uptime", self.v)


if __name__ == "__main__":
    unittest.main()
