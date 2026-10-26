"""Hardware and execution environment capture for scale benchmarks."""

from dataclasses import asdict, dataclass
import os
import platform
import sqlite3
import sys


@dataclass(frozen=True)
class HardwareEnvironment:
    """Hardware and runtime specs pinned for reproducible benchmark runs."""

    os_system: str
    os_release: str
    os_version: str
    machine: str
    processor: str
    logical_cpus: int
    python_version: str
    python_executable: str
    sqlite_version: str
    memory_total_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _get_total_memory_bytes() -> int | None:
    """Safely retrieves total system physical memory across platforms."""
    # Try Windows GlobalMemoryStatusEx via ctypes
    if sys.platform == "win32":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            pass

    # Try Linux /proc/meminfo or sysconf
    try:
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        pass

    return None


def capture_hardware_environment() -> HardwareEnvironment:
    """Captures and returns current machine hardware and runtime metadata."""
    return HardwareEnvironment(
        os_system=platform.system(),
        os_release=platform.release(),
        os_version=platform.version(),
        machine=platform.machine(),
        processor=platform.processor() or platform.machine(),
        logical_cpus=os.cpu_count() or 1,
        python_version=platform.python_version(),
        python_executable=sys.executable,
        sqlite_version=sqlite3.sqlite_version,
        memory_total_bytes=_get_total_memory_bytes(),
    )
