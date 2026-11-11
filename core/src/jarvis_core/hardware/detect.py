"""Liest CPU, RAM, GPU und VRAM. Nicht ermittelbare Werte bleiben None (nie raten)."""

import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareInfo:
    cpu: str | None = None
    ram_gb: float | None = None
    gpu: str | None = None
    vram_gb: float | None = None
    is_mac: bool = False  # Apple-Chip mit geteiltem Arbeitsspeicher


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _powershell(expression: str) -> str | None:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", expression])


def _bytes_to_gb(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return int(raw.strip()) / 1024**3
    except ValueError:
        return None


def parse_nvidia_smi(output: str | None) -> tuple[str, float] | None:
    """Parst `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`.

    Bei mehreren GPUs gewinnt die mit dem meisten Speicher.
    """
    if not output:
        return None
    best: tuple[str, float] | None = None
    for line in output.splitlines():
        name, sep, mem = line.rpartition(",")
        if not sep:
            continue
        try:
            vram_gb = float(mem.strip()) / 1024
        except ValueError:
            continue
        if best is None or vram_gb > best[1]:
            best = (name.strip(), vram_gb)
    return best


def _nvidia() -> tuple[str, float] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    return parse_nvidia_smi(
        _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    )


def _detect_macos() -> HardwareInfo:
    cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    ram_gb = _bytes_to_gb(_run(["sysctl", "-n", "hw.memsize"]))
    apple_silicon = platform.machine() == "arm64"
    return HardwareInfo(
        cpu=cpu,
        ram_gb=ram_gb,
        gpu=f"{cpu} (integriert)" if apple_silicon and cpu else None,
        vram_gb=None,  # geteilter Speicher: zählt über ram_gb
        is_mac=apple_silicon,
    )


def _detect_windows() -> HardwareInfo:
    cpu = _powershell("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
    ram_gb = _bytes_to_gb(_powershell("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"))
    nvidia = _nvidia()
    if nvidia is not None:
        return HardwareInfo(cpu=cpu, ram_gb=ram_gb, gpu=nvidia[0], vram_gb=nvidia[1])
    # Ohne nvidia-smi ist der VRAM nicht verlässlich lesbar (WMI meldet max. 4 GB).
    gpu = _powershell("(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name")
    return HardwareInfo(cpu=cpu, ram_gb=ram_gb, gpu=gpu, vram_gb=None)


def _detect_linux() -> HardwareInfo:
    cpu: str | None = None
    ram_gb: float | None = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    ram_gb = int(line.split()[1]) / 1024**2
                    break
    except (OSError, ValueError, IndexError):
        pass
    nvidia = _nvidia()
    gpu, vram = nvidia if nvidia else (None, None)
    return HardwareInfo(cpu=cpu, ram_gb=ram_gb, gpu=gpu, vram_gb=vram)


def detect_hardware() -> HardwareInfo:
    system = platform.system()
    if system == "Darwin":
        return _detect_macos()
    if system == "Windows":
        return _detect_windows()
    return _detect_linux()
