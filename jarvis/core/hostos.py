"""
What differs between Windows and Linux, in one place — feature modules never ask os.name.
Jarvis is built on Windows and moves to Ubuntu later (PORTING.md); on Windows every
function here does exactly what the modules did before.

  QUIET              subprocess keyword arguments: no console window on Windows
  DETACHED           subprocess keyword arguments for a program that outlives the call
  venv_python(root)  the Python of a virtual environment (.venv\\Scripts\\python.exe | .venv/bin/python)
  open_target(t)     a file, folder, URL or URI with the system's default program
  ping_args(addr)    one ping with a short timeout
  shell_tool()       the Claude Code tool for shell commands (PowerShell | Bash)
"""
import math
import os
import pathlib
import subprocess

IS_WIN = os.name == "nt"
QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}
DETACHED = {"creationflags": subprocess.DETACHED_PROCESS} if IS_WIN else {"start_new_session": True}


def venv_python(root, name=".venv"):
    root = pathlib.Path(root) / name
    return str(root / "Scripts" / "python.exe") if IS_WIN else str(root / "bin" / "python")


def open_target(target):
    if IS_WIN:
        os.startfile(target)
    else:
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **DETACHED)


def ping_args(addr, timeout_ms=1500):
    # Windows waits per reply in milliseconds; Linux takes a deadline in whole seconds
    if IS_WIN:
        return ["ping", "-n", "1", "-w", str(timeout_ms), addr]
    return ["ping", "-c", "1", "-w", str(max(1, math.ceil(timeout_ms / 1000))), addr]


def shell_tool():
    return "PowerShell" if IS_WIN else "Bash"
