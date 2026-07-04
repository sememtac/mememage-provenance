"""Machine vitals — hardware identity and live state snapshot.

V1 storage policy:
- Bytes for memory/network (int). No "GB" / "MB" suffix strings.
- Dicts for paired counters (page_faults, ctx_switches, cache, power, disk_io).
- Lists for ordered tuples (load = [1m, 5m, 15m]).
- Integer codes for enums (platform, power.src).
- Drop derived/cosmetic fields (uptime "5d 3h" — uptime_seconds covers it).

Display helpers (mememage.vitals_display) format these back to human strings
for the cert. Consumers (rarity, temperament, personality) read the
structured form directly.
"""

import os
import platform
import re
import subprocess
import sys


# Platform enum: 0=darwin, 1=linux, 2=other. Indexed by OS, frozen.
PLATFORM_DARWIN = 0
PLATFORM_LINUX = 1
PLATFORM_OTHER = 2

_PLATFORM_NAMES = {
    PLATFORM_DARWIN: "darwin",
    PLATFORM_LINUX: "linux",
    PLATFORM_OTHER: "other",
}

_PLATFORM_FROM_NAME = {v: k for k, v in _PLATFORM_NAMES.items()}


def platform_name(code) -> str:
    """int code → platform name. Accepts int or legacy string passthrough."""
    if isinstance(code, str):
        return code.lower()
    return _PLATFORM_NAMES.get(code, "other")


def platform_code(value) -> int:
    """Accept int (passthrough) or legacy string ("darwin"/"linux") → int code."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _PLATFORM_FROM_NAME.get(value.lower(), PLATFORM_OTHER)
    return PLATFORM_OTHER


# Power source enum: 0=AC, 1=battery, 2=unknown.
POWER_AC = 0
POWER_BATTERY = 1
POWER_UNKNOWN = 2


def _sysctl(key: str) -> str:
    """Read a sysctl value (macOS). Returns empty string on failure."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", key], capture_output=True, text=True, timeout=2
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _run(cmd: list[str]) -> str:
    """Run a command and return stdout. Returns empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


_static_hw_cache = {}


def _static_hardware():
    """Return cached static hardware info (CPU, cores, GPU, RAM, cache).

    These values never change between boots. system_profiler alone takes
    ~900ms so we cache aggressively.
    """
    if _static_hw_cache:
        return dict(_static_hw_cache)

    hw = {}
    cpu = _sysctl("machdep.cpu.brand_string")
    if cpu:
        hw["cpu"] = cpu

    perf_cores = _sysctl("hw.perflevel0.logicalcpu")
    eff_cores = _sysctl("hw.perflevel1.logicalcpu")
    if perf_cores and eff_cores:
        try:
            p = int(perf_cores)
            e = int(eff_cores)
            hw["cores"] = {"total": p + e, "p": p, "e": e}
        except ValueError:
            pass
    if "cores" not in hw:
        total = _sysctl("hw.logicalcpu")
        if total:
            try:
                hw["cores"] = {"total": int(total)}
            except ValueError:
                pass

    gpu_out = _run(["system_profiler", "SPDisplaysDataType"])
    for line in gpu_out.splitlines():
        stripped = line.strip()
        if "Total Number of Cores" in stripped:
            v = stripped.split(":")[-1].strip()
            try:
                hw["gpu"] = int(v)
            except ValueError:
                hw["gpu"] = v

    try:
        mem_bytes = int(_sysctl("hw.memsize"))
        hw["ram"] = mem_bytes
    except (ValueError, TypeError):
        pass

    l1 = _sysctl("hw.l1dcachesize")
    l2 = _sysctl("hw.l2cachesize")
    cache = {}
    try:
        if l1:
            cache["l1"] = int(l1)
        if l2:
            cache["l2"] = int(l2)
    except ValueError:
        cache = {}
    if cache:
        hw["cache"] = cache

    _static_hw_cache.update(hw)
    return dict(hw)


def _machine_vitals_darwin(vitals: dict) -> None:
    """macOS-specific hardware and live state collection."""
    vitals.update(_static_hardware())

    # Live memory state
    try:
        vm_out = _run(["vm_stat"])
        lines = vm_out.splitlines()
        page_size = 16384
        if lines:
            ps_match = re.search(r"page size of (\d+) bytes", lines[0])
            if ps_match:
                page_size = int(ps_match.group(1))
        stats = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip().rstrip(".")
                try:
                    stats[k.strip()] = int(v)
                except ValueError:
                    pass
        active = stats.get("Pages active", 0) * page_size
        wired = stats.get("Pages wired down", 0) * page_size
        compressed = stats.get("Pages occupied by compressor", 0) * page_size
        free = stats.get("Pages free", 0) * page_size
        vitals["mem_active"] = active + wired
        vitals["mem_compressed"] = compressed
        vitals["mem_free"] = free
    except Exception:
        pass

    # Disk I/O
    try:
        io_out = _run(["iostat", "-c", "1"])
        lines = io_out.strip().splitlines()
        if len(lines) >= 3:
            values = lines[-1].split()
            if len(values) >= 3:
                try:
                    vitals["disk_io"] = {
                        "kb_per_t": float(values[0]),
                        "tps": float(values[1]),
                        "mb_per_s": float(values[2]),
                    }
                except ValueError:
                    pass
    except Exception:
        pass

    # Power source
    try:
        pwr = _run(["pmset", "-g", "batt"])
        if "AC Power" in pwr:
            vitals["power"] = {"src": POWER_AC}
        elif "Battery" in pwr:
            for line in pwr.splitlines():
                if "%" in line:
                    m = re.search(r"(\d+)%", line)
                    pct = int(m.group(1)) if m else None
                    vitals["power"] = {"src": POWER_BATTERY, "pct": pct}
                    break
    except Exception:
        pass

    # Uptime — seconds only; cert formats the string.
    try:
        import time as _time
        boottime = _sysctl("kern.boottime")
        m = re.search(r"sec = (\d+)", boottime)
        if m:
            vitals["uptime_seconds"] = int(_time.time()) - int(m.group(1))
    except Exception:
        pass

    # Open file descriptors
    try:
        fds = _sysctl("kern.num_files")
        if fds:
            vitals["open_fds"] = int(fds)
    except (ValueError, TypeError):
        pass

    # VM page classification gauges (instantaneous, high-variance)
    try:
        spec = _sysctl("vm.page_speculative_count")
        if spec:
            vitals["speculative_pages"] = int(spec)
    except (ValueError, TypeError):
        pass

    try:
        purg = _sysctl("vm.page_purgeable_count")
        if purg:
            vitals["purgeable_pages"] = int(purg)
    except (ValueError, TypeError):
        pass

    # Network bytes
    try:
        net_out = _run(["netstat", "-ib"])
        for line in net_out.splitlines():
            parts = line.split()
            if (parts and parts[0] not in ("lo0", "Name", "gif0*", "stf0*")
                    and "<Link" in line and len(parts) >= 10):
                try:
                    ibytes = int(parts[6])
                    obytes = int(parts[9])
                    if ibytes > 0:
                        vitals["net_rx"] = ibytes
                        vitals["net_tx"] = obytes
                        break
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass


def _machine_vitals_linux(vitals: dict) -> None:
    """Linux-specific hardware and live state collection."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read()
        for line in cpuinfo.splitlines():
            if line.startswith("model name"):
                vitals["cpu"] = line.split(":", 1)[1].strip()
                break
        cores = sum(1 for l in cpuinfo.splitlines() if l.startswith("processor"))
        if cores:
            vitals["cores"] = {"total": cores}
    except OSError:
        pass

    gpu_out = _run(["lspci"])
    for line in gpu_out.splitlines():
        if "VGA" in line or "3D controller" in line:
            vitals["gpu"] = line.split(":", 2)[-1].strip()
            break

    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()
        for line in meminfo.splitlines():
            if line.startswith("MemTotal"):
                kb = int(line.split()[1])
                vitals["ram"] = kb * 1024
                break
    except OSError:
        pass

    l1 = _run(["getconf", "LEVEL1_DCACHE_SIZE"])
    l2 = _run(["getconf", "LEVEL2_CACHE_SIZE"])
    cache = {}
    try:
        if l1 and l1.isdigit():
            cache["l1"] = int(l1)
        if l2 and l2.isdigit():
            cache["l2"] = int(l2)
    except ValueError:
        cache = {}
    if cache:
        vitals["cache"] = cache

    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()
        mi = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mi[parts[0].rstrip(":")] = int(parts[1])
        active_kb = mi.get("Active", 0)
        free_kb = mi.get("MemFree", 0)
        buffers_kb = mi.get("Buffers", 0) + mi.get("Cached", 0)
        vitals["mem_active"] = active_kb * 1024
        vitals["mem_compressed"] = buffers_kb * 1024
        vitals["mem_free"] = free_kb * 1024
    except (OSError, ValueError):
        pass

    try:
        io_out = _run(["iostat", "-d", "-x", "1", "1"])
        lines = io_out.strip().splitlines()
        if len(lines) >= 4:
            values = lines[-1].split()
            if len(values) >= 7:
                try:
                    vitals["disk_io"] = {
                        "read_kbs": float(values[5]),
                        "write_kbs": float(values[6]),
                    }
                except ValueError:
                    pass
    except Exception:
        pass

    try:
        bat_path = "/sys/class/power_supply/BAT0"
        if os.path.isdir(bat_path):
            with open(f"{bat_path}/status") as f:
                status = f.read().strip()
            with open(f"{bat_path}/capacity") as f:
                cap = f.read().strip()
            try:
                pct = int(cap)
            except ValueError:
                pct = None
            if status == "Charging" or status == "Full":
                vitals["power"] = {"src": POWER_AC, "pct": pct}
            else:
                vitals["power"] = {"src": POWER_BATTERY, "pct": pct}
        else:
            vitals["power"] = {"src": POWER_AC}
    except OSError:
        pass

    # Uptime — seconds only.
    try:
        with open("/proc/uptime", "r") as f:
            vitals["uptime_seconds"] = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        pass

    try:
        with open("/proc/sys/fs/file-nr", "r") as f:
            fds = f.read().split()[0]
        vitals["open_fds"] = int(fds)
    except (OSError, ValueError):
        pass

    try:
        with open("/proc/net/dev", "r") as f:
            net_out = f.read()
        for line in net_out.splitlines()[2:]:
            if "lo:" in line:
                continue
            parts = line.split(":")
            if len(parts) == 2:
                vals = parts[1].split()
                if len(vals) >= 9:
                    ibytes = int(vals[0])
                    obytes = int(vals[8])
                    if ibytes > 0:
                        vitals["net_rx"] = ibytes
                        vitals["net_tx"] = obytes
                        break
    except (OSError, ValueError):
        pass

    # --- Machine-die signals (Linux equivalents of macOS Mach VM stats) ---
    try:
        with open("/proc/meminfo", "r") as f:
            mi_lines = f.read().splitlines()
        mi = {}
        for line in mi_lines:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    mi[parts[0].rstrip(":")] = int(parts[1])
                except ValueError:
                    pass
        if "Inactive(file)" in mi:
            vitals["speculative_pages"] = mi["Inactive(file)"] // 4
        cached_kb = mi.get("Cached", 0) + mi.get("SReclaimable", 0)
        if cached_kb:
            vitals["purgeable_pages"] = cached_kb // 4
    except (OSError, ValueError):
        pass

    # Pulse: disk transfers/sec from /proc/diskstats. Overwrites the
    # iostat-derived dict above when available with a measured tps.
    try:
        import time as _time
        def _disk_total():
            total = 0
            with open("/proc/diskstats", "r") as f:
                for ln in f:
                    p = ln.split()
                    if len(p) < 14:
                        continue
                    name = p[2]
                    if name.startswith(("loop", "ram", "dm-")):
                        continue
                    if name[-1].isdigit() and not name.startswith("nvme"):
                        continue
                    total += int(p[3]) + int(p[7])
            return total
        t1 = _disk_total()
        _time.sleep(0.4)
        t2 = _disk_total()
        tps = max(0, t2 - t1) * 2.5
        # Standardize on dict shape with tps key so _check_machine reads
        # the same field on both platforms.
        existing = vitals.get("disk_io") or {}
        if isinstance(existing, dict):
            existing["tps"] = float(tps)
            vitals["disk_io"] = existing
        else:
            vitals["disk_io"] = {"tps": float(tps)}
    except Exception:
        pass


def _machine_vitals_windows(vitals: dict) -> None:
    """Windows collector. The *nix branches read Mach / /proc counters that
    don't exist here; Windows exposes its own. The most useful — and the one the
    rarity machine die reads — is physical-memory load via GlobalMemoryStatusEx
    (it hands back used/available directly). Every ctypes call is guarded so a
    missing/odd API degrades to an empty field, never a crash. (Disk I/O and
    a load-average equivalent need perf-counter plumbing and aren't collected
    yet — the die runs on memory pressure for now.)
    """
    import ctypes

    try:
        n = os.cpu_count()
        if n:
            vitals["cores"] = {"total": int(n)}
    except Exception:
        pass
    try:
        # Marketing name ("AMD Ryzen 9 5950X 16-Core Processor") from the
        # registry, like the *nix branches read /proc model name / sysctl. Falls
        # back to platform.processor()'s family/model string if the key's unread.
        cpu = None
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        except Exception:
            cpu = None
        if not cpu:
            cpu = platform.processor() or platform.machine()
        if cpu:
            vitals["cpu"] = str(cpu).strip()
    except Exception:
        pass

    # Physical memory + load (GlobalMemoryStatusEx).
    try:
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(ms)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            total = int(ms.ullTotalPhys)
            avail = int(ms.ullAvailPhys)
            if total > 0:
                vitals["ram"] = total
                vitals["mem_free"] = avail
                vitals["mem_active"] = max(0, total - avail)  # used = total - avail
    except Exception:
        pass

    # Uptime (GetTickCount64 → ms since boot). restype must be 64-bit or it
    # truncates on the default c_int.
    try:
        k32 = ctypes.windll.kernel32
        k32.GetTickCount64.restype = ctypes.c_ulonglong
        ticks = k32.GetTickCount64()
        if ticks:
            vitals["uptime_seconds"] = int(ticks) // 1000
    except Exception:
        pass


def _machine_vitals_generic(vitals: dict) -> None:
    """Catch-all collector for platforms without a dedicated branch.

    Pulls uname + sysctl + cross-platform stdlib so the cert isn't
    empty. Machine die stays silent until a real branch lands.
    """
    try:
        un = os.uname()
        vitals["cpu"] = f"{un.sysname} {un.machine}"
        vitals["kernel"] = un.release
    except Exception:
        pass

    try:
        mem = _sysctl("hw.physmem") or _sysctl("hw.memsize")
        if mem and mem.isdigit():
            vitals["ram"] = int(mem)
    except Exception:
        pass
    try:
        ncpu = _sysctl("hw.ncpu") or _sysctl("hw.ncpuonline")
        if ncpu and ncpu.isdigit():
            vitals["cores"] = {"total": int(ncpu)}
    except Exception:
        pass


def collect_vitals() -> dict:
    """Snapshot the bare metal at the instant of creation.

    V1 record shape: bytes for memory/network, dicts for paired counters,
    lists for ordered tuples, ints for enums. Display helpers format
    these back to human strings for the cert.
    """
    vitals = {}

    system = platform.system()
    if system == "Darwin":
        vitals["platform"] = PLATFORM_DARWIN
        _machine_vitals_darwin(vitals)
    elif system == "Linux":
        vitals["platform"] = PLATFORM_LINUX
        _machine_vitals_linux(vitals)
    elif system == "Windows":
        vitals["platform"] = PLATFORM_OTHER
        _machine_vitals_windows(vitals)
    else:
        vitals["platform"] = PLATFORM_OTHER
        _machine_vitals_generic(vitals)

    # --- Cross-platform fields ---
    try:
        # os.getloadavg() doesn't EXIST on Windows (AttributeError, not
        # OSError) — catching only OSError let it crash the whole vitals
        # snapshot, which broke the forecast on Windows. Load average is
        # best-effort; just skip it where there's no equivalent.
        load = os.getloadavg()
        vitals["load"] = [round(load[0], 2), round(load[1], 2), round(load[2], 2)]
    except (OSError, AttributeError):
        pass

    vitals["entropy"] = os.urandom(32).hex()

    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_CHILDREN)
        vitals["page_faults"] = {"soft": ru.ru_minflt, "hard": ru.ru_majflt}
        vitals["ctx_switches"] = {"vol": ru.ru_nvcsw, "invol": ru.ru_nivcsw}
    except Exception:
        pass

    try:
        marker = bytearray(1)
        vitals["heap_addr"] = hex(id(marker))
        vitals["alloc_blocks"] = sys.getallocatedblocks()
    except Exception:
        pass

    return vitals
