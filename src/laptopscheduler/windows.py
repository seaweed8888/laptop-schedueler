"""Windows ownership, power/prerequisite probes, and Task Scheduler bridge."""

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .models import StartupStatus


def require_windows():
    if sys.platform != "win32":
        raise OSError("LaptopScheduler 1.0 requires Windows")


class Ownership:
    """Kernel object existence owns app + database across sessions, without leases."""

    def __init__(self, app_id, path):
        require_windows()
        self.handles = []
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        try:
            for identity in (
                "app:" + app_id.casefold(),
                "db:" + os.path.normcase(str(path)),
            ):
                name = (
                    "Global\\LaptopScheduler-"
                    + hashlib.sha256(identity.encode()).hexdigest()
                )
                handle = self.kernel.CreateMutexW(None, False, name)
                error = ctypes.get_last_error()
                if not handle:
                    raise ctypes.WinError(error)
                self.handles.append(handle)
                if error == 183:  # ERROR_ALREADY_EXISTS
                    raise RuntimeError(
                        "Another scheduler owns this application or database"
                    )
        except BaseException:
            self.close()
            raise

    def close(self):
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles.clear()


class Windows:
    def __init__(self):
        require_windows()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.SetThreadExecutionState.argtypes = [wintypes.DWORD]
        self.kernel.SetThreadExecutionState.restype = wintypes.DWORD

    def awake(self):
        previous = self.kernel.SetThreadExecutionState(0x80000001)
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
        return previous

    def restore(self, previous):
        if not self.kernel.SetThreadExecutionState(previous | 0x80000000):
            raise ctypes.WinError(ctypes.get_last_error())

    def ac_ready(self):
        class PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = PowerStatus()
        if not self.kernel.GetSystemPowerStatus(ctypes.byref(status)):
            return False
        return status.ACLineStatus == 1

    def idle_ready(self, seconds):
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(info)
        user = ctypes.WinDLL("user32", use_last_error=True)
        if not user.GetLastInputInfo(ctypes.byref(info)):
            return False
        self.kernel.GetTickCount.restype = wintypes.DWORD
        elapsed = (self.kernel.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed / 1000 >= seconds

    def ready(self, job):
        try:
            if job.requires_ac and not self.ac_ready():
                return False
            if job.idle_seconds and not self.idle_ready(job.idle_seconds):
                return False
            if (
                job.requires_network
                and not powershell({"operation": "network"})["ready"]
            ):
                return False
            return True
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            RuntimeError,
        ):
            return False


def powershell(payload):
    require_windows()
    executable = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(__file__).with_name("startup.ps1")),
        ],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return json.loads(result.stdout.decode("utf-8-sig"))


def startup(operation, app_id, token, module=None, working_directory=None):
    payload = {
        "operation": operation,
        "name": "LaptopScheduler-" + app_id,
        "owner": "LaptopScheduler:" + token,
    }
    if operation == "install":
        if not isinstance(module, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module, re.ASCII
        ):
            raise ValueError("module must be an importable dotted Python module name")
        directory = Path(working_directory).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("working_directory must be a directory")
        executable = Path(sys.executable).with_name("pythonw.exe")
        if not executable.is_file():
            raise FileNotFoundError(
                f"Console-free startup requires pythonw.exe in this Python environment: {executable}"
            )
        payload.update(
            python=str(executable),
            arguments="-m " + module,
            working_directory=str(directory),
        )
    return StartupStatus(**powershell(payload))
