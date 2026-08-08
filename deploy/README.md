# deploy — air-gapped ("red zone") sync pipeline

The red zone is an isolated Linux box: **no network, no git, no pip, no venv.**
So it cannot `git pull`, and nothing can be installed on it. This pipeline ships
the committed code across the air gap as a plain tarball, and then tells you what
that particular box is able to run.

```
dev / yellow (Windows, has git)  ──upload──▶  red zone (Linux, isolated)
      deploy\pack.ps1                          ./deploy.sh  →  deploy/doctor.sh
```

The package is built with `git archive`, so it is **100% git-free** (no `.git/`,
no `.gitattributes`/`.gitignore`, no `CLAUDE*.md`, no `pack.ps1` — all
`export-ignore`d) and immune to the classic Windows→Linux traps: paths are always
`/`, text is LF (read from committed blobs, not the Windows working tree), and the
exec bit is preserved.

**What ships is a blacklist, not a whitelist.** Everything in the repo crosses the
gap unless it is `export-ignore`d in `.gitattributes` — so new scripts you add are
packaged automatically, with no change to this pipeline.

**Nothing is ever written outside the install directory.** No `/tmp`, no `/opt`, no
`/var`, no `mktemp`. Every staging, backup and scratch path lives under
`<install>/.deploy/`.

## The red-zone layout

```
.../Snp_analyzer/                 ← the install dir; call it whatever you like
├── deploy.sh                     ← update entry point (top level, on purpose)
├── Snp_analyzer_<short>.tar.gz   ← you upload the package here
├── Snp_analyzer_<short>.tar.gz.sha256
├── pkg_rlc_extractor.py, reduce_snp.py, ... , tests/, docs/
├── deploy/{doctor.sh, _env_check.py, README.md}
└── .deploy/                      ← all runtime state, never leaves the box
    ├── incoming/                 # uploaded tarball + .sha256
    ├── staging/                  # full extract before swap
    ├── backups/<timestamp>/      # previous installs (last 3)
    ├── tmp/                      # scratch
    └── preserve.list             # optional, see below
```

Nothing depends on the install dir being named `Snp_analyzer` — `deploy.sh`
locates the install as *its own directory*, and the package root is auto-detected
from the archive. Rename either freely.

## 1. Windows — pack

After committing (and ideally pushing):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

Produces exactly two files, under `deploy\dist\`:

| file | for |
|---|---|
| `Snp_analyzer_<short>.tar.gz` | the whole install (code + tests + docs) |
| `Snp_analyzer_<short>.tar.gz.sha256` | integrity check on the far side |

Upload both. That is the entire delivery — there is deliberately nothing else to
copy and nothing to choose between.

Use `-Name <dir>` to change the package root directory name (default
`Snp_analyzer`, i.e. what you get after `tar -xzf`).

Needs only **git + PowerShell** (no Python, no external tar). It packages the
committed `HEAD` — uncommitted changes are *not* included (you get a warning).

It also **preflights the shell scripts**: it archives them on their own and scans
the raw bytes for CR. If `deploy.sh` would ship as CRLF, packing aborts. That one
mistake is what bricks a red-zone deploy (`bash: $'\r': command not found`), so it
is caught here rather than over there where you cannot debug it.

## 2. Red zone — deploy

Upload the tarball **and** its `.sha256` into the install dir, then:

```tcsh
cd .../Snp_analyzer
bash deploy.sh
```

No argument needed — it picks up the newest `*.tar.gz` sitting in the install dir
and says which one it chose (and which it ignored, if several are lying around).
Pass a path explicitly to override.

> Invoke with **`bash`**, not `./deploy.sh`: the red zone's login shell is often
> **tcsh/csh**, and an upload channel may drop the exec bit — `bash` needs
> neither. Run it as a script; don't `source` it.

It verifies the sha256, extracts to staging, **backs up** the current install to
`.deploy/backups/<timestamp>/` (keeps the newest 3), then swaps the new content in
place. **Only the install dir is touched — the parent dir is never modified.** On
any failure during the swap it auto-rolls-back.

## 3. Red zone — doctor (this is the step that matters here)

Because nothing can be installed on that box, the real question after a deploy is
*what can it already run*. `doctor.sh` probes every candidate interpreter and
reports three tiers:

| tier | capability | needs |
|---|---|---|
| 1 | `reduce_snp.py` — port reduction | numpy |
| 2 | CLI extraction → CSV | numpy |
| 3 | GUI | numpy + matplotlib + tkinter + `$DISPLAY` |

```bash
bash deploy/doctor.sh --test
```

`--test` additionally runs the shipped unit-test suite. A green suite is the
strongest evidence available on an isolated box: it proves the package landed
intact, numpy works, and the numerics are right — all without a network.

A missing tier-3 dependency is a **degrade, not a failure**; tiers 1–2 still run.
Only numpy is a hard requirement. If the default `python3` lacks it, an EDA-bundled
interpreter often has it:

```bash
bash deploy/doctor.sh --python /path/to/that/python3
# or: export SNP_PYTHON=/path/to/that/python3
```

Then run it (doctor prints these exact lines with the right interpreter):

```bash
python3 reduce_snp.py --help
python3 pkg_rlc_extractor.py --help
```

## 4. Just `reduce_snp.py` on a sim server

`reduce_snp.py` imports nothing from this repo by design, so it runs anywhere
numpy does — including a simulation server that has no install of this tool.
It ships inside the package like everything else; copy it out of an install, or
straight out of the tarball:

```bash
tar -xzf Snp_analyzer_<short>.tar.gz Snp_analyzer/reduce_snp.py
scp Snp_analyzer/reduce_snp.py user@simserver:~/
```

`pack.ps1` used to emit a second, hash-named loose copy for this. It did not
earn the confusion of a `dist/` with four files in it — the delivery is one
tarball.

## First-time bootstrap (no `deploy.sh` on the box yet)

`deploy.sh` ships *inside* the package, so it self-refreshes on every update — but
the very first time there is nothing to run it with. Once:

```tcsh
cd .../                        # wherever the install should live
tar -xzf Snp_analyzer_<short>.tar.gz    # yields ./Snp_analyzer/
cd Snp_analyzer
bash deploy/doctor.sh --test
```

That extracted directory **is** the install. From then on every update is just
"upload the tarball into it, run `bash deploy.sh`".

## Keeping your own data across deploys

A deploy replaces every top-level entry except `.deploy/`. If you keep your own
`.sNp` files or results *inside* the install dir, list those top-level names in
`.deploy/preserve.list` (one per line, `#` comments allowed):

```
data
results
```

They are then left untouched by the swap. A name that the package itself ships
(e.g. `docs`) is rejected with a clear error rather than silently nested.

Uploaded `*.tar.gz` files are left where you put them (a copy goes to
`.deploy/incoming/`). Deleting them afterwards is optional — the next deploy just
takes the newest one.

## Rollback

Each deploy backs up the previous install to `.deploy/backups/<timestamp>/`.
To revert manually:

```bash
cd .../Snp_analyzer
# remove current contents (everything except .deploy), then:
mv .deploy/backups/<timestamp>/* .
```

Or simply re-deploy an older tarball: `bash deploy.sh <older>.tar.gz`.

If a deploy fails partway it rolls back on its own. The rollback distinguishes a
failure *during backup* (where the backup is incomplete, so the originals still in
place must not be deleted) from one *during install* (where the backup is complete
and the new files can be cleared) — getting that distinction wrong destroys the
install, so don't simplify it.
