# deploy — air-gapped ("red zone") sync pipeline

The red zone is an isolated Linux box: **no network, no git, no pip, no venv.**
So it cannot `git pull`, and nothing can be installed on it. This pipeline ships
the committed code across the air gap as a plain tarball, and then tells you what
that particular box is able to run.

```
dev / yellow (Windows, has git)  ──upload──▶  red zone (Linux, isolated)
        pack.ps1                               deploy.sh  →  doctor.sh
```

The package is built with `git archive`, so it is **100% git-free** (no `.git/`,
no `.gitattributes`/`.gitignore`, no `CLAUDE*.md`, no `pack.ps1` — all
`export-ignore`d) and immune to the classic Windows→Linux traps: paths are always
`/`, text is LF (read from committed blobs, not the Windows working tree), and the
exec bit is preserved.

**What ships is a blacklist, not a whitelist.** Everything in the repo crosses the
gap unless it is `export-ignore`d in `.gitattributes` — so new scripts you add are
packaged automatically, with no change to this pipeline.

## 1. Windows — pack

After committing (and ideally pushing):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

Produces, under `deploy\dist\`:

| file | for |
|---|---|
| `snp_rlc_extractor_<short>.tar.gz` + `.sha256` | full install (code + tests + docs) |
| `reduce_snp_<short>.py` + `.sha256` | single-file fast lane, see §4 |

Needs only **git + PowerShell** (no Python, no external tar). It packages the
committed `HEAD` — uncommitted changes are *not* included (you get a warning).

It also **preflights the shell scripts**: if `deploy.sh` or `doctor.sh` ever ends
up CRLF in the git index, packing aborts. That one mistake is what bricks a
red-zone deploy (`bash: $'\r': command not found`), so it is caught here rather
than over there where you cannot debug it.

Upload **both** files of whichever route you need, into `.../workarea/snp_rlc_extractor/`.

## 2. Red zone — deploy

```tcsh
cd .../workarea/snp_rlc_extractor
bash deploy/deploy.sh snp_rlc_extractor_<short>.tar.gz
```

> Invoke with **`bash`**, not `./deploy/deploy.sh`: the red zone's login shell is
> often **tcsh/csh**, and an upload channel may drop the exec bit — `bash` needs
> neither. Run it as a script; don't `source` it.

It verifies the sha256, extracts to staging, **backs up** the current install to
`.deploy/backups/<timestamp>/` (keeps the newest 3), then swaps the new content in
place. **Only the install dir is touched — the parent dir is never modified.** On
any failure during the swap it auto-rolls-back to the backup.

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

## 4. Fast lane — just `reduce_snp.py` on a sim server

`reduce_snp.py` imports nothing from this repo by design; it is meant to be copied
onto simulation servers on its own. `pack.ps1` therefore also emits it as a loose
file, byte-identical to the copy inside the tarball, so this case needs no unpack:

```bash
sha256sum -c reduce_snp_<short>.py.sha256
python3 reduce_snp_<short>.py --help
```

## First-time bootstrap (no `deploy.sh` on the box yet)

`deploy.sh` ships *inside* the package, so it self-refreshes on every update — but
the very first time there is nothing to run it with. Once:

```tcsh
cd .../workarea
tar -xzf snp_rlc_extractor_<short>.tar.gz   # yields ./snp_rlc_extractor/
# move/merge ./snp_rlc_extractor into place as .../workarea/snp_rlc_extractor
bash snp_rlc_extractor/deploy/doctor.sh --test
```

After that, `deploy/deploy.sh` is in place and handles every future update.

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

## Rollback

Each deploy backs up the previous install to `.deploy/backups/<timestamp>/`.
To revert manually:

```bash
cd .../workarea/snp_rlc_extractor
# remove current contents (everything except .deploy), then:
mv .deploy/backups/<timestamp>/* .
```

Or simply re-deploy an older tarball.

## Layout (all runtime state stays inside the install dir)

```
snp_rlc_extractor/
├── deploy/{pack.ps1, deploy.sh, doctor.sh, _env_check.py, README.md}
├── VERSION                       # stamped by git archive; `cat` to see commit+date
└── .deploy/                      # runtime only (gitignored), never leaves the red zone
    ├── incoming/                 # uploaded tarball + .sha256
    ├── staging/                  # full extract before swap
    ├── backups/<timestamp>/      # previous installs (last 3)
    └── preserve.list             # optional, see above
```
