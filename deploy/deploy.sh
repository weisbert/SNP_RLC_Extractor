#!/usr/bin/env bash
#
# deploy.sh -- red-zone (Linux) deployer for SNP_RLC_Extractor.
#
# Verifies a packed tarball (from deploy/pack.ps1), backs up the current
# install, and swaps in the new one *in place*. Everything stays under the
# install dir -- the PARENT directory is NEVER touched. No git, no network, no
# pip, no venv, no Python required: only bash + tar + sha256sum (base RHEL).
#
# Managed layout (all inside the install dir):
#   <install>/.deploy/incoming/             uploaded tarball + .sha256
#   <install>/.deploy/staging/              full extract happens here first
#   <install>/.deploy/backups/<timestamp>/  previous install (keeps last N)
#   <install>/.deploy/preserve.list         optional: top-level names to KEEP
#                                           across deploys (your own data dirs)
#
# Usage (after the one-time bootstrap below):
#   cd .../workarea/snp_rlc_extractor
#   bash deploy/deploy.sh snp_rlc_extractor_<hash>.tar.gz
#   bash deploy/doctor.sh --test        # then check the box can actually run it
#
# Invoke via `bash` (not ./deploy.sh): the red zone's login shell is often
# tcsh/csh, and an upload channel may drop the exec bit. `bash` needs neither.
# Run it as a script -- never `source` it (it is bash, and it exits on success).
#
# The tarball + its .sha256 sidecar should be uploaded together (typically into
# the install dir itself); both are copied into .deploy/incoming/ before the swap.
#
# FIRST-TIME BOOTSTRAP (no deploy.sh on the box yet):
#   tar -xzf snp_rlc_extractor_<hash>.tar.gz     # yields ./snp_rlc_extractor/
#   # move ./snp_rlc_extractor into place as .../workarea/snp_rlc_extractor
#   # thereafter deploy/deploy.sh lives in place and handles every update.
#
# ROLLBACK: each run backs up the previous install to .deploy/backups/<ts>/.
# To revert: delete the new contents (everything but .deploy) and
#   mv .deploy/backups/<ts>/* .  (or just re-deploy an older tarball).
#
set -euo pipefail

KEEP_BACKUPS=3
SENTINEL="pkg_rlc_extractor.py"  # must exist at the install root -- guards against
                                 # running in the wrong dir or extracting a bad archive
ARCHIVE_ROOT="snp_rlc_extractor" # --prefix used by pack.ps1

# --- locate ourselves: deploy.sh lives at <install>/deploy/deploy.sh ---------
SELF="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SELF")"
TARGET="$(dirname "$SCRIPT_DIR")"     # .../snp_rlc_extractor
DEPLOY="$TARGET/.deploy"
INCOMING="$DEPLOY/incoming"
STAGING="$DEPLOY/staging"
BACKUPS="$DEPLOY/backups"

die() { echo "ERROR: $*" >&2; exit 1; }
print_version() { while IFS= read -r _l; do echo "     $_l"; done < "$1"; }

# --- args --------------------------------------------------------------------
[[ $# -ge 1 ]] || die "usage: $(basename "$0") <tarball.tar.gz>"
TARBALL_SRC="$(readlink -f "$1")"
[[ -f "$TARBALL_SRC" ]] || die "tarball not found: $1"
[[ -f "$TARGET/$SENTINEL" ]] || die \
  "$TARGET/$SENTINEL missing -- not an SNP_RLC_Extractor install. Do the one-time bootstrap first (see header)."

mkdir -p "$INCOMING" "$STAGING" "$BACKUPS"

# --- preserve list: top-level names that survive the swap --------------------
# Always .deploy; plus anything the operator listed (their own .sNp data dirs,
# result folders, ...). One name per line, '#' starts a comment.
PRESERVE=(".deploy")
if [[ -f "$DEPLOY/preserve.list" ]]; then
  while IFS= read -r _line || [[ -n "$_line" ]]; do
    _line="${_line%%#*}"
    _line="${_line#"${_line%%[![:space:]]*}"}"   # ltrim
    _line="${_line%"${_line##*[![:space:]]}"}"   # rtrim
    _line="${_line%/}"                            # tolerate a trailing slash
    if [[ -n "$_line" ]]; then PRESERVE+=("$_line"); fi
  done < "$DEPLOY/preserve.list"
fi
is_preserved() {
  local _n="$1" _p
  for _p in "${PRESERVE[@]}"; do
    if [[ "$_n" == "$_p" ]]; then return 0; fi
  done
  return 1
}

# --- stage the tarball + sidecar into .deploy/incoming -----------------------
TAR_NAME="$(basename "$TARBALL_SRC")"
rm -rf "${INCOMING:?}/"* 2>/dev/null || true
cp -f "$TARBALL_SRC" "$INCOMING/$TAR_NAME"
if [[ -f "$TARBALL_SRC.sha256" ]]; then cp -f "$TARBALL_SRC.sha256" "$INCOMING/$TAR_NAME.sha256"; fi

# --- verify checksum (abort before touching the install) ---------------------
if [[ -f "$INCOMING/$TAR_NAME.sha256" ]]; then
  sed -i 's/\r$//' "$INCOMING/$TAR_NAME.sha256"   # tolerate a CRLF sidecar (Windows-edited)
  if command -v sha256sum >/dev/null 2>&1; then
    echo ">> verifying sha256..."
    ( cd "$INCOMING" && sha256sum -c "$TAR_NAME.sha256" ) \
      || die "checksum FAILED -- aborting, install untouched."
  else
    echo "WARN: sha256sum not found; skipping checksum verification" >&2
  fi
else
  echo "WARN: no .sha256 sidecar next to the tarball; skipping checksum verification" >&2
fi

# --- extract to staging (full extract BEFORE touching the install) -----------
echo ">> extracting to staging..."
rm -rf "${STAGING:?}/"*
tar -xzf "$INCOMING/$TAR_NAME" -C "$STAGING"
NEW="$STAGING/$ARCHIVE_ROOT"
[[ -d "$NEW" ]]           || die "archive has no $ARCHIVE_ROOT/ root."
[[ -f "$NEW/$SENTINEL" ]] || die "staged package missing $SENTINEL -- bad archive."

if [[ -f "$NEW/VERSION" ]]; then echo ">> incoming version:"; print_version "$NEW/VERSION"; fi

# --- guard: a preserved name must not also be shipped by the package ---------
# Otherwise the swap's `mv` would nest the new copy INSIDE the kept directory.
shopt -s dotglob nullglob
for _it in "$NEW"/*; do
  _n="$(basename "$_it")"
  if is_preserved "$_n"; then
    shopt -u dotglob nullglob
    die "'$_n' is in .deploy/preserve.list but the package also ships it. Remove it from preserve.list, or rename your local copy."
  fi
done
shopt -u dotglob nullglob

# --- backup + swap (the only non-atomic window; rollback on any failure) -----
TS="$(date +%Y%m%d-%H%M%S)"
BK="$BACKUPS/$TS"
mkdir -p "$BK"

shopt -s dotglob nullglob

# PHASE tells rollback() how much of the install is old vs new, which decides
# what it is allowed to delete. Getting this wrong destroys the install: if the
# BACKUP loop dies partway, the backup is INCOMPLETE and the files still sitting
# in TARGET are the only copy of the originals -- they must not be touched.
PHASE="backup"

rollback() {
  trap - ERR
  echo "!! swap failed during '$PHASE' -- rolling back from $BK" >&2

  # Only in the install phase is TARGET's content known to come from the new
  # package (the backup is complete by then), so only then may we clear it.
  if [[ "$PHASE" == "install" ]]; then
    for _it in "$TARGET"/*; do
      if is_preserved "$(basename "$_it")"; then continue; fi
      rm -rf "$_it"
    done
  fi

  # Restore whatever actually made it into the backup, overwriting same-name
  # collisions. During a backup-phase failure the names in BK and TARGET are
  # disjoint (mv moves), so nothing in TARGET is lost.
  for _it in "$BK"/*; do
    _n="$(basename "$_it")"
    rm -rf "${TARGET:?}/$_n"
    mv "$_it" "$TARGET"/
  done
  echo "!! rollback complete -- install restored." >&2
  exit 1
}
trap rollback ERR

# NOTE: this moves deploy/ -- the very directory this script lives in. That is
# safe on Linux: mv within one filesystem is a rename, the inode is untouched,
# and bash keeps reading the script through its open fd. (It does fail on
# Windows, which locks the running script's directory -- but the red zone is
# Linux.) Do not "fix" this by copying the script elsewhere first.
echo ">> backing up current install -> $BK"
for _it in "$TARGET"/*; do
  if is_preserved "$(basename "$_it")"; then continue; fi
  mv "$_it" "$BK"/
done

PHASE="install"
echo ">> installing new version..."
for _it in "$NEW"/*; do
  mv "$_it" "$TARGET"/
done

# post-swap integrity check -- a bare failing test trips the ERR trap -> rollback
if [[ ! -f "$TARGET/$SENTINEL" ]]; then echo "post-swap sentinel missing" >&2; false; fi

trap - ERR
shopt -u dotglob nullglob

# --- rotate backups ----------------------------------------------------------
echo ">> rotating backups (keeping newest $KEEP_BACKUPS)..."
_i=0
while IFS= read -r _d; do
  _i=$((_i + 1))
  if (( _i > KEEP_BACKUPS )); then rm -rf "$_d"; fi
done < <(ls -1dt "$BACKUPS"/*/ 2>/dev/null || true)

rm -rf "${STAGING:?}/"*

# --- done --------------------------------------------------------------------
echo
echo "OK  deployed."
if [[ -f "$TARGET/VERSION" ]]; then echo "    installed version:"; print_version "$TARGET/VERSION"; fi
echo "    previous install backed up at: $BK"
if (( ${#PRESERVE[@]} > 1 )); then
  echo "    preserved across swap: ${PRESERVE[*]}"
fi
echo
echo "    NEXT -- check this box can actually run it (no network / no venv needed):"
echo "       bash $TARGET/deploy/doctor.sh --test"
