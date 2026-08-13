# Hiding the GUI tests: Win32 desktop objects

**Verdict: YES.** A Win32 **desktop object** hides this suite's Tk windows
completely, does not move a single measured pixel, cannot steal the keyboard
focus, and costs no wall time — it is slightly faster. The launcher is
`tests/_isolated_desktop.py`. It is deliberately **not** wired into
`tests/run_parallel.py`; see [Wiring it in](#wiring-it-in).

The question this answers: 87% of the suite drives real Tk, so a full run
throws hundreds of windows onto the screen and takes the keyboard away from
whoever is using the machine, for the length of the run.

---

## 1. What was tested, and what was ruled out first

**Ruled out without testing: Windows 11 "virtual desktops" (Win+Ctrl+D).** They
do not isolate. A new window lands on the *active* virtual desktop, and focus
stealing crosses between them. This was already known and was not re-measured.

**Tested: Win32 desktop objects.** A different and much older mechanism — a
separate namespace for windows, hooks and the clipboard's window set. It is
what UAC's secure desktop uses, so a window on one is genuinely unreachable
from the interactive desktop. The whole mechanism is two calls:

```c
CreateDesktopW(name, ...)                    /* user32  */
STARTUPINFOW.lpDesktop = name;               /* kernel32 CreateProcessW */
```

Reached through stdlib `ctypes` — **no new dependency**, which the red-zone
numpy-only constraint requires.

Box for every number below: Windows 11 Pro 26100, Python 3.11.7, Tk 8.6,
vista theme, `TkDefaultFont` = Microsoft YaHei UI 9, `tk scaling` 1.333005,
screen 2048x1152.

---

## 2. Does Tk run there at all?

Yes. A mapped 1500x900 `tk.Tk()` root launched with `lpDesktop` set reports its
own desktop through `GetUserObjectInformationW(UOI_NAME)`:

```
default desktop   ->  desktop = "Default"
desktop object    ->  desktop = "ClaudeTestDesk"      rc 0
```

---

## 3. Do the geometry tests pass, and do the numbers move?

**They pass, and nothing moves.** This is the real question — several of these
modules assert pixel geometry and one builds a second `App` at `tk scaling 2.0`
with every named font x1.5.

### 3a. Pass/fail, the four modules the plan named

| module | tests | default desktop | desktop object |
|---|---|---|---|
| `test_plot_controls`   |  13 | OK | **OK** |
| `test_editor_scroll`   |   7 | OK | **OK** |
| `test_multifile_table` | 100 | OK | **OK** |
| `test_attrib_window`   | 212 | OK | **OK** |

`test_attrib_window` matters most: it is the module that builds the second App
at 150% (`TestTheDeclaredMinimumShowsContent`) and the one that reads the
clipboard (`clipboard_get`, line 2328). Both work on the desktop object.

### 3b. All 23 Tk-driven modules

Not just those four. **Every Tk-driven module in the suite was run both ways**,
in parallel at `-j6`, and compared on pass/fail *and* on test count:

```
modules where isolation changed the outcome: NONE
```

| module | tests | default | isolated | | module | tests | default | isolated |
|---|---|---|---|---|---|---|---|---|
| `test_attrib_golden` | 12 | OK | OK | | `test_parse_diagnostics` | 44 | OK | OK |
| `test_attrib_gui_integration` | 70 | OK | OK | | `test_plot_controls` | 13 | OK | OK |
| `test_attrib_window` | 212 | OK | OK | | `test_port_roles` | 100 | OK | OK |
| `test_conn_rowshape` | 78 | OK | OK | | `test_report_readability` | 47 | OK | OK |
| `test_editor_autoapply` | 41 | OK | OK | | `test_results_notebook` | 34 | OK | OK |
| `test_editor_scroll` | 7 | OK | OK | | `test_results_views` | 74 | OK | OK |
| `test_freeze_trace` | 65 | OK | OK | | `test_row_table` | 38 | OK | OK |
| `test_freq_label` | 64 | OK | OK | | `test_run_history` | 97 | OK | OK |
| `test_layering` | 21 | OK | OK | | `test_run_snapshot` | 28 | OK | OK |
| `test_mode5_editor` | 79 | OK | OK | | `test_session` | 48 | OK | OK |
| `test_multifile_engine` | 63 | OK | OK | | | | | |
| `test_multifile_session` | 66 | OK | OK | | | | | |
| `test_multifile_table` | 100 | OK | OK | | | | | |

**1401 tests, 23 modules, no difference.**

**"OK" is not by itself evidence that Tk ran, and neither is the test count.**
These modules guard themselves with `@unittest.skipUnless(TK_OK, ...)`, so if
Tk could not open a display on the desktop object every Tk class would SKIP —
and `unittest` counts a skipped test in its `Ran N tests` line and still prints
`OK`. A green run with a matching count is exactly what a total failure to
reach Tk would look like. Checked directly instead:

```
test_plot_controls | skipped tokens: 0 | OK
test_editor_scroll | skipped tokens: 0 | OK
```

Zero skips, plus §3c below, where a real `App` on the desktop object returns
real pixel values — neither is possible unless Tk genuinely ran there.

The two arms' totals were 707.0 s (default) and 294.9 s (isolated) at `-j6`,
but they ran one after the other on a box with sibling agents on it, so that
2.4x is **not** a speedup claim — see §5 for why sequential arms are worthless
here. It is reported only as being consistent with "no penalty".

### 3c. The numbers themselves, not just pass/fail

Pass/fail alone is weak evidence — a test can pass for the wrong reason. So the
real `App` was built on both desktops and 20 values read off it, at 100% **and**
at this repo's 150% definition (`tk scaling` 2.0 with every named font x1.5):

```
compared 20 keys per scaling, DIFFS = 0
```

**40 of 40 identical.** They include the figures CLAUDE.md pins by name:

| value | measured, both desktops |
|---|---|
| `_ed_canvas.winfo_width()` | **431** |
| `_ed_form.winfo_reqwidth()` | **417** |
| left panel width / `outer.sashpos(0)` | **460** / **460** |
| `results_nb.winfo_reqheight()` | **172** |
| connections table `winfo_reqwidth()` | **400** |
| measurement-port table `winfo_reqwidth()` | **288** |
| `winfo_screenwidth/height` | **2048 x 1152** |
| `tk scaling` (100% / 150%) | **1.333005** / **2.001354** |
| `TkDefaultFont` | Microsoft YaHei UI 9, linespace **17** |
| TkDefaultFont glyphs `' ' - + . 0 M X` | **4 5 9 3 7 12 8** |
| Consolas 9 glyphs `' ' 0 - + . M` | **7 7 7 7 7 7** |
| at 150%: font / linespace / conn table | YaHei 14 / **36** / **794** |

Those glyph widths are the ones the whole table layer is built on
(CLAUDE.md: "in Consolas 9 every glyph the tables emit measures exactly 7 px").
They do not move, which is why the tables do not move.

---

## 4. Does it steal focus?

**No** — and this was measured with a control, so the measurement can fail.

A deliberately rude Tk window (`deiconify` + `lift` + `-topmost` +
`focus_force`, 30 times at 100 ms) was run against a victim window on the
interactive desktop, sampling `GetForegroundWindow()` 40 times:

| stealer runs on | foreground window titles seen | stole focus? |
|---|---|---|
| the SAME desktop (**control**) | `{'STEALER': 30, 'VICTIM': 10}` | **yes** |
| a desktop **object** | `{'VICTIM': 40}` | **no** |

The control is the point: without it, "no steal" is indistinguishable from a
broken test. A process on the desktop object also reads
`GetForegroundWindow()` as **0** — it cannot see the interactive foreground,
let alone claim it. `winfo_screenwidth`/`screenheight` are unchanged
(2048x1152), so nothing about screen metrics shifts either.

---

## 5. What does it cost in wall time?

**Nothing. It is slightly faster** — there is no compositor to paint through.

Microbenchmark, 12 **interleaved** samples of 5 `App` build/settle/destroy
cycles each:

```
default   min 0.849 s   median 0.932 s
isolated  min 0.618 s   median 0.704 s
ratio           0.73x           0.76x
```

The box settled after sample 5, and across the stable tail (samples 6-12) the
isolated arm was lower on **every** sample — 0.618-0.704 s against
0.849-0.932 s, with no overlap between the two ranges at all.

Module level, interleaved. The two runs of each arm, in the order they were
taken — the desktop object was faster in **every** one of these 6 pairs
(0.3x to 0.9x):

| module | default, 2 runs | isolated, 2 runs |
|---|---|---|
| `test_plot_controls` | 9.20, 8.97 s | 8.31, 7.70 s |
| `test_editor_scroll` | 46.92, 33.11 s | 36.30, 26.11 s |
| `test_multifile_table` | 88.72, 85.90 s | 72.29, 29.80 s |

**The noise is large and is stated rather than smoothed away.** An earlier
2-round pass on `test_multifile_table`, taken while the box was busier, read
`default 51.26 / isolated 95.10` then `default 94.79 / isolated 71.06` — a 2x
spread *within one arm*, and one pair pointing the other way. That pair is the
reason the microbenchmark above exists: 12 short samples resolve what 2 long
ones cannot. The conclusion this supports is **"no time penalty"**, not a
precise speedup figure.

### MEASURE THIS INTERLEAVED OR NOT AT ALL

A naive "run arm A, then run arm B" gave the **opposite** answer on this box:

```
                    default      isolated (sequential A-then-B)
test_plot_controls     4.72 s      12.34 s     "2.6x slower"
test_editor_scroll     8.89 s      86.42 s     "9.7x slower"
test_multifile_table  27.26 s     167.53 s     "6.1x slower"
test_attrib_window   157.07 s     416.18 s     "2.7x slower"
```

Every one of those is an artifact. Sibling agents' load ramped up between the
two arms — 18 concurrent `python` processes on 20 cores were measured on the
box at the time. Interleaving the arms and taking the **minimum** reverses the
sign of the result. Anyone re-checking this must alternate the arms; a
sequential A/B on a shared box is worthless here.

---

## 6. Reproducing all of it

The probes live in the scratchpad, not the repo, so they are reproduced here as
commands rather than referenced as files. Run from the repo root.

**Is a desktop object usable here, and does the launcher work?**

```bash
python tests/_isolated_desktop.py -c "import tkinter as tk; r=tk.Tk(); print(r.winfo_screenwidth())"
python -c "import sys; sys.path.insert(0,'tests'); import _isolated_desktop as i; print(i.available())"
```

**Which desktop is a child really on?**

```bash
python tests/_isolated_desktop.py -c "
import ctypes; u=ctypes.WinDLL('user32')
h=u.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
b=ctypes.create_unicode_buffer(256); n=ctypes.c_ulong(0)
u.GetUserObjectInformationW(h,2,b,ctypes.sizeof(b),ctypes.byref(n)); print(b.value)"
```

**Run a geometry module on it** (compare against the same command without the
wrapper):

```bash
python tests/_isolated_desktop.py -m unittest tests.test_plot_controls
python -m unittest tests.test_plot_controls
```

**The pixel comparison of section 3c** — build the real `App` on each desktop
and diff the readings. Read the values with a script that prints
`app._ed_canvas.winfo_width()`, `app._ed_form.winfo_reqwidth()`,
`outer.sashpos(0)`, `results_nb.winfo_reqheight()`, the two table
`winfo_reqwidth()`s and the font metrics, once plainly and once after
`app.tk.call("tk","scaling",2.0)` with every named font x1.5, then run it both
ways and compare. `outer` is reached the way `test_results_notebook.py` does
it: walk `app.results_text.master` up to the first `TPanedwindow`, then take
*its* master.

**The focus test of section 4** needs three pieces: a victim `tk.Tk()` on the
interactive desktop sampling `user32.GetForegroundWindow()` on a timer, a
stealer that loops `lift`/`-topmost`/`focus_force`, and both arms —
**including the control**, with the stealer on the default desktop, which must
show the steal happening.

**The timing of section 5** — alternate the arms in one loop, three rounds,
report the minimum. Do not run one arm to completion and then the other.

---

## 7. Caveats and operational notes

* **Windows only.** `available()` returns False off Windows and in a session
  with no window station (a service). A caller must fall back to running
  children normally, not fail.
* **A hung child is invisible.** There is no window to notice and nothing to
  click. `run(..., timeout=)` terminates the child and raises
  `subprocess.TimeoutExpired`; prefer it to relying on seeing a stuck process.
* **Hold the handle open.** A desktop object dies once no handle and no process
  references it. `desktop()` is a context manager for exactly that; keep it
  open around the whole run rather than per shard.
* **Concurrent runners may share one name.** `CreateDesktopW` opens the
  existing desktop when the name is taken, so two runners with the same name
  cooperate rather than collide.
* **Output capture uses temp files, not pipes.** A pipe needs a reader thread
  per stream or the child blocks when it fills, and a shard's stderr is exactly
  what fills one.
* **Quoting goes through `subprocess.list2cmdline`.** A hand-rolled "quote it
  if it contains a space" is wrong: `-c print("hi")` has no space, goes through
  unquoted, and the child receives `print(hi)` — measured, rc 1 and an empty
  stdout with a `NameError` nobody wrote.

---

## Wiring it in

Not done here, on purpose — `tests/run_parallel.py` was owned by another agent
during this work. The change is one call site. `run_shard` currently does:

```python
p = subprocess.run([sys.executable, "-m", "unittest", name],
                   capture_output=True, text=True, cwd=str(REPO))
```

`_isolated_desktop.run` returns a `subprocess.CompletedProcess`, so it is a
drop-in:

```python
from _isolated_desktop import available, desktop, run as run_isolated
# ... open ONE `desktop()` around the whole run, then per shard:
p = run_isolated([sys.executable, "-m", "unittest", name], cwd=str(REPO),
                 **_priority_kwargs())          # <- see below, do not drop this
```

Guard it on `available()` and keep the plain `subprocess.run` as the fallback,
and put it behind a flag so a developer who *wants* to watch the windows still
can.

**Carry `_priority_kwargs()` through, or you silently undo the other half.**
`run_shard` spawns every shard `BELOW_NORMAL_PRIORITY_CLASS` so the suite
gives way to the user at the CPU — the same concern this document is about,
solved at the other end. `CreateProcessW` takes its own `dwCreationFlags`, and
a launcher that hardcodes 0 there puts every shard silently back to NORMAL. So
`run()` takes a `creationflags` argument and passes it straight through.
Verified rather than assumed — the child's `PriorityClass` read through
`Get-Process`:

```
run(..., creationflags=0)                            -> Normal
run(..., creationflags=BELOW_NORMAL_PRIORITY_CLASS)  -> BelowNormal
```

The two changes are complementary and independent: priority is about CPU
contention, the desktop object is about windows and focus. Neither replaces
the other.
