#!/usr/bin/env python
"""
Run a child process on a Win32 DESKTOP OBJECT, so its windows are invisible on
the interactive desktop and cannot take the keyboard focus.

NOT auto-discovered (leading underscore), exactly like `_golden_capture.py`,
`_render_capture.py` and `_smoke.py`.  It is deliberately NOT wired into
`tests/run_parallel.py`; see "Invocation" below and `docs/test_isolation.md`.

WHY
    87% of this suite drives real Tk.  A full run therefore throws hundreds of
    windows onto the screen and takes the keyboard focus away from whatever the
    user is doing, for the length of the run.

    Windows 11's "virtual desktops" (Win+Ctrl+D) do NOT fix this -- a new
    window lands on the ACTIVE virtual desktop and focus stealing crosses
    between them.  A Win32 desktop OBJECT is a different and much older
    mechanism: it is a separate namespace for windows and hooks, it is what
    UAC's secure desktop uses, and a window on one is genuinely unreachable
    from another.  `CreateDesktopW` plus `STARTUPINFO.lpDesktop` on
    `CreateProcessW` is the whole of it, via stdlib `ctypes` -- no new
    dependency, which the red-zone numpy-only constraint requires.

MEASURED ON THIS BOX (Windows 11 Pro 26100, Python 3.11.7, Tk 8.6, vista
theme, TkDefaultFont = Microsoft YaHei UI 9, `tk scaling` 1.333005, screen
2048x1152).  Reproduction commands are in `docs/test_isolation.md`.

  1. Tk RUNS THERE.  A mapped 1500x900 root reports its desktop as
     `ClaudeTestDesk` via `GetUserObjectInformationW(UOI_NAME)`, rc 0.

  2. THE GEOMETRY TESTS PASS AND THE NUMBERS DO NOT MOVE.  All four
     pixel-measuring modules the plan named, on the desktop object:

         test_plot_controls     13 tests  OK
         test_editor_scroll      7 tests  OK
         test_multifile_table  100 tests  OK
         test_attrib_window    212 tests  OK

     and so do ALL 23 Tk-driven modules in the suite -- 1401 tests, run both
     ways at -j6, with "modules where isolation changed the outcome: NONE"
     (the table is in `docs/test_isolation.md`).  A green "OK" is not on its
     own evidence that Tk ran, because these modules `skipUnless(TK_OK)` and
     `unittest` counts a skipped test in `Ran N` and still prints OK -- so
     the skip count was checked separately and is ZERO.  Beyond pass/fail, 40 measured
     values -- 20 per scaling, at 100% AND at this repo's 150% definition
     (`tk scaling` 2.0 with every named font x1.5) -- are IDENTICAL between
     the two desktops, DIFFS = 0.  They include the figures CLAUDE.md pins:
     `_ed_canvas` width 431, `_ed_form` requested width 417, left panel 460,
     `outer.sashpos(0)` 460, `results_nb.winfo_reqheight()` 172, connections
     table 400, and every glyph width the tables are built on (TkDefaultFont
     ' ' 4, '-' 5, '+' 9, '.' 3, digit 7, 'M' 12, 'X' 8; Consolas 9 all 7).
     `winfo_screenwidth`/`screenheight` are unchanged at 2048x1152.

  3. IT DOES NOT STEAL FOCUS.  A deliberately rude Tk window -- `deiconify` +
     `lift` + `-topmost` + `focus_force`, 30 times at 100 ms -- was run
     against a victim window on the interactive desktop while the foreground
     window was sampled 40 times:

         stealer on the SAME desktop  (control)  {'STEALER': 30, 'VICTIM': 10}
         stealer on the desktop OBJECT           {'VICTIM': 40}

     The control is what proves the measurement can fail.  A process on the
     desktop object also reads `GetForegroundWindow()` as 0 -- it cannot see
     the interactive foreground, let alone claim it.

  4. IT COSTS NOTHING IN WALL TIME; it is slightly FASTER, having no
     compositor to paint through.  12 interleaved samples of 5 App
     build/settle/destroy cycles: default min 0.849 s / median 0.932 s
     against isolated min 0.618 s / median 0.704 s, i.e. 0.73x / 0.76x.  Six
     paired module runs were faster on the desktop object every time (0.3x to
     0.9x).

     MEASURE THIS INTERLEAVED OR NOT AT ALL.  A naive "run arm A, then run
     arm B" gave the OPPOSITE answer on this box -- isolated 2.6x to 9.7x
     SLOWER -- purely because sibling agents' load ramped up between the two
     arms.  Alternate the arms and take the minimum.

INVOCATION
    python tests/_isolated_desktop.py -m unittest tests.test_plot_controls
    python tests/_isolated_desktop.py tests/run_parallel.py --fast

    As a library, `run()` is a drop-in for the `subprocess.run(...,
    capture_output=True, text=True)` call in `run_parallel.run_shard`:

        from _isolated_desktop import desktop, run
        with desktop():
            p = run([sys.executable, "-m", "unittest", name], cwd=REPO)
            p.returncode, p.stdout, p.stderr

    The handle returned by `desktop()` must stay open for as long as children
    are being launched: a desktop object is destroyed once no handle and no
    process references it, and re-creating it per shard costs a syscall per
    shard for nothing.  Concurrent runners may share one name safely --
    `CreateDesktopW` opens the existing desktop when the name is taken.

CAVEATS
    * Windows only.  `available()` reports whether this route can be used at
      all; on any other platform, and in a session with no window station (a
      service), it returns False and the caller must fall back to running
      normally.
    * A child that HANGS on the desktop object is invisible -- there is no
      window to notice and nothing to click.  Use the `timeout` argument,
      which terminates the child, rather than relying on seeing it.
"""

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile

__all__ = ["available", "desktop", "run", "DEFAULT_DESKTOP"]

#: Any name works; it is a namespace, not a path.  Sharing one name between
#: concurrent runners is safe and is cheaper than a desktop each.
DEFAULT_DESKTOP = "SnpRlcTestDesk"

_GENERIC_ALL = 0x10000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_STARTF_USESTDHANDLES = 0x00000100
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x00000102

_LPVOID = ctypes.c_void_p
_LPBYTE = ctypes.POINTER(ctypes.c_ubyte)


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD),
                ("lpSecurityDescriptor", _LPVOID),
                ("bInheritHandle", wt.BOOL)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD),
                ("lpReserved", wt.LPWSTR),
                ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", _LPBYTE),
                ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def _bind():
    """Resolve the Win32 entry points.  Returns None off Windows."""
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.CreateDesktopW.restype = wt.HANDLE
    user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, _LPVOID,
                                      wt.DWORD, wt.DWORD, _LPVOID]
    user32.CloseDesktop.restype = wt.BOOL
    user32.CloseDesktop.argtypes = [wt.HANDLE]

    kernel32.CreateProcessW.restype = wt.BOOL
    kernel32.CreateProcessW.argtypes = [
        wt.LPCWSTR, wt.LPWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wt.BOOL, wt.DWORD, _LPVOID, wt.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION)]
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                     ctypes.POINTER(_SECURITY_ATTRIBUTES),
                                     wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE,
                                            ctypes.POINTER(wt.DWORD)]
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    return user32, kernel32


_API = _bind()


def available() -> bool:
    """True when a desktop object can actually be created here.

    False off Windows, and false in a session with no window station -- a
    caller that gets False must run its children normally.
    """
    if _API is None:
        return False
    user32 = _API[0]
    h = user32.CreateDesktopW(DEFAULT_DESKTOP + "Probe", None, None, 0,
                              _GENERIC_ALL, None)
    if not h:
        return False
    user32.CloseDesktop(h)
    return True


class desktop:
    """Context manager holding a desktop object open.

    The object lives while a handle or a process references it, so the handle
    has to outlive every child launched onto it.
    """

    def __init__(self, name: str = DEFAULT_DESKTOP):
        self.name = name
        self.handle = None

    def __enter__(self) -> "desktop":
        if _API is None:
            raise RuntimeError("Win32 desktop objects need Windows")
        user32 = _API[0]
        # Opens the existing desktop when the name is already taken, which is
        # what makes concurrent runners able to share one.
        self.handle = user32.CreateDesktopW(self.name, None, None, 0,
                                            _GENERIC_ALL, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self, *exc):
        if self.handle:
            _API[0].CloseDesktop(self.handle)
            self.handle = None
        return False

    def run(self, argv, cwd=None, timeout=None, creationflags=0):
        return run(argv, cwd=cwd, timeout=timeout, name=self.name,
                   creationflags=creationflags)


def run(argv, cwd=None, timeout=None, name: str = DEFAULT_DESKTOP,
        creationflags: int = 0):
    """Run `argv` on the desktop object `name`; capture stdout and stderr.

    Returns a `subprocess.CompletedProcess` so this is a drop-in for
    `subprocess.run(argv, capture_output=True, text=True, cwd=cwd)`.

    `creationflags` is passed straight to `CreateProcessW`'s
    `dwCreationFlags` and exists so that the caller's process-priority choice
    SURVIVES the move onto the desktop object.  `run_parallel.run_shard`
    spawns every shard `BELOW_NORMAL_PRIORITY_CLASS` so the suite gives way to
    the user; routing that spawn through here with the flag dropped would put
    it silently back to NORMAL, i.e. undo one of the two things being done for
    the same reason.  Pass `**_priority_kwargs()`-worth of flags through this
    argument, not by editing the call.

    Output goes to inheritable temporary FILES rather than pipes: a pipe needs
    a reader thread per stream or the child blocks once it fills, and a test
    shard's stderr is exactly the kind of output that fills one.
    """
    if _API is None:
        raise RuntimeError("Win32 desktop objects need Windows")
    user32, kernel32 = _API

    tmp = tempfile.mkdtemp(prefix="isodesk_")
    out_path = os.path.join(tmp, "stdout.txt")
    err_path = os.path.join(tmp, "stderr.txt")
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    h_out = h_err = None
    try:
        h_out = kernel32.CreateFileW(out_path, _GENERIC_WRITE,
                                     _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                                     ctypes.byref(sa), _CREATE_ALWAYS,
                                     _FILE_ATTRIBUTE_NORMAL, None)
        h_err = kernel32.CreateFileW(err_path, _GENERIC_WRITE,
                                     _FILE_SHARE_READ | _FILE_SHARE_WRITE,
                                     ctypes.byref(sa), _CREATE_ALWAYS,
                                     _FILE_ATTRIBUTE_NORMAL, None)
        if h_out == _INVALID_HANDLE_VALUE or h_err == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())

        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        si.lpDesktop = name           # <- the whole mechanism
        si.dwFlags = _STARTF_USESTDHANDLES
        si.hStdInput = None
        si.hStdOutput = wt.HANDLE(h_out)
        si.hStdError = wt.HANDLE(h_err)
        pi = _PROCESS_INFORMATION()

        argv = [str(a) for a in argv]
        # `subprocess.list2cmdline`, not a hand-rolled "quote it if it has a
        # space": an argument containing quotes but no space -- `-c
        # print("hi")` -- goes through unquoted under that rule and the child
        # receives `print(hi)`.  Measured: rc 1, empty stdout, a NameError
        # nobody wrote.  list2cmdline implements the MSVCRT rules exactly and
        # is what `subprocess` itself passes to `CreateProcessW`.
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        ok = kernel32.CreateProcessW(None, cmdline, None, None, True,
                                     wt.DWORD(creationflags), None,
                                     str(cwd) if cwd else None,
                                     ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        ms = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        timed_out = kernel32.WaitForSingleObject(pi.hProcess, ms) == _WAIT_TIMEOUT
        if timed_out:
            # A hung child here has no window to notice, so it is killed
            # rather than left behind.
            kernel32.TerminateProcess(pi.hProcess, 1)
            kernel32.WaitForSingleObject(pi.hProcess, 5000)
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(pi.hThread)
        kernel32.CloseHandle(pi.hProcess)
    finally:
        for h in (h_out, h_err):
            if h and h != _INVALID_HANDLE_VALUE:
                kernel32.CloseHandle(wt.HANDLE(h))

    def _read(p):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    stdout, stderr = _read(out_path), _read(err_path)
    shutil.rmtree(tmp, ignore_errors=True)
    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout,
                                        stderr=stderr)
    return subprocess.CompletedProcess(argv, code.value, stdout, stderr)


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if not available():
        print("isolated desktop unavailable here; running normally",
              file=sys.stderr)
        p = subprocess.run([sys.executable] + argv)
        return p.returncode
    with desktop() as d:
        p = d.run([sys.executable] + argv, cwd=os.getcwd())
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
