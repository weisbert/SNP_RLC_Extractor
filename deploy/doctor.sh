#!/usr/bin/env bash
#
# doctor.sh -- red-zone environment check for SNP_RLC_Extractor.
#
# The red zone has no network: nothing can be pip-installed and no venv is
# created. So the only question that matters after a deploy is "what can THIS
# box already run?". This script answers it per interpreter, in three tiers:
#
#   tier 1  reduce_snp.py     port reduction on a sim server   needs numpy
#   tier 2  CLI extraction    R/L/C/Q -> CSV                   needs numpy
#   tier 3  GUI               Tkinter + Matplotlib             needs both + X11
#
# A missing tier-3 dependency is a DEGRADE, not a failure: tiers 1-2 still run.
#
# Usage (login shell is often tcsh -- always invoke with bash):
#   bash deploy/doctor.sh                 probe every candidate interpreter
#   bash deploy/doctor.sh --test          ... and run the unit-test suite
#   bash deploy/doctor.sh --python /path/to/python3
#   SNP_PYTHON=/path/to/python3 bash deploy/doctor.sh
#
# Exit code: 0 if at least one interpreter reaches tier 2, else 1.
#
set -uo pipefail

SELF="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SELF")"
ROOT="$(dirname "$SCRIPT_DIR")"
PROBE="$SCRIPT_DIR/_env_check.py"

RUN_TESTS=0
FORCED_PY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --test|-t)    RUN_TESTS=1; shift ;;
    --python|-p)  FORCED_PY="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '2,26p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

[[ -f "$PROBE" ]] || { echo "ERROR: $PROBE missing -- incomplete install." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

getval() { sed -n "s/^$1=//p" "$2" | head -1; }

echo "=== SNP_RLC_Extractor -- red-zone environment doctor ==="
echo "install : $ROOT"
if [[ -f "$ROOT/VERSION" ]]; then
  echo "version :"
  sed -n '1,5p' "$ROOT/VERSION" | sed 's/^/     /'
fi
echo

# --- collect candidate interpreters -----------------------------------------
CANDIDATES=()
add_candidate() {
  local _c="$1" _r _e
  [[ -n "$_c" ]] || return 0
  _r="$(command -v "$_c" 2>/dev/null || true)"
  [[ -n "$_r" ]] || return 0
  _r="$(readlink -f "$_r" 2>/dev/null || echo "$_r")"
  for _e in ${CANDIDATES[@]+"${CANDIDATES[@]}"}; do
    if [[ "$_e" == "$_r" ]]; then return 0; fi
  done
  CANDIDATES+=("$_r")
}

if [[ -n "$FORCED_PY" ]]; then
  add_candidate "$FORCED_PY"
  if (( ${#CANDIDATES[@]} == 0 )); then
    echo "ERROR: --python '$FORCED_PY' is not executable." >&2; exit 1
  fi
else
  add_candidate "${SNP_PYTHON:-}"
  for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python \
           /usr/bin/python3 /usr/local/bin/python3; do
    add_candidate "$c"
  done
fi

if (( ${#CANDIDATES[@]} == 0 )); then
  echo "ERROR: no Python interpreter found on PATH." >&2
  echo "       Point at one explicitly:  bash deploy/doctor.sh --python /path/to/python3" >&2
  exit 1
fi

# --- probe each --------------------------------------------------------------
BEST=""; BEST_TIER=0; BEST_VENV="NO"
mark() { if [[ "$1" == "OK" || "$1" == "YES" ]]; then echo "OK "; else echo "-- "; fi; }

idx=0
for py in "${CANDIDATES[@]}"; do
  idx=$((idx + 1))
  out="$TMP/probe.$idx"
  if ! "$py" "$PROBE" > "$out" 2> "$TMP/err.$idx"; then
    if [[ "$(getval PY_OK "$out")" == "NO" ]]; then
      printf '>> %-38s %s  SKIP (%s)\n' "$py" "$(getval PY_VERSION "$out")" "$(getval PY_WHY "$out")"
    else
      printf '>> %-38s SKIP (probe failed)\n' "$py"
      sed 's/^/     /' "$TMP/err.$idx" | tail -3
    fi
    echo
    continue
  fi

  pyver="$(getval PY_VERSION "$out")"
  echo ">> $py  ($pyver)"

  printf '     %s numpy         %s\n' "$(mark "$(getval MOD_numpy "$out")")" "$(getval MOD_numpy_detail "$out")"
  warn="$(getval MOD_numpy_warn "$out")"
  if [[ -n "$warn" ]]; then printf '        WARN: numpy %s\n' "$warn"; fi
  printf '     %s matplotlib    %s\n' "$(mark "$(getval MOD_matplotlib "$out")")" "$(getval MOD_matplotlib_detail "$out")"

  tkline="$(getval MOD_tkinter "$out")"
  disp="$(getval TK_DISPLAY "$out")"
  envdisp="$(getval ENV_DISPLAY "$out")"
  if [[ "$tkline" == "OK" && "$disp" == "NO" ]]; then
    if [[ -n "$envdisp" ]]; then
      printf '     %s tkinter       present, but cannot open a window (DISPLAY=%s)\n' "$(mark OK)" "$envdisp"
    else
      printf '     %s tkinter       present, but $DISPLAY is unset (headless -- no GUI)\n' "$(mark OK)"
    fi
  elif [[ "$disp" == "YES" ]]; then
    if [[ -n "$envdisp" ]]; then
      printf '     %s tkinter       window OK (DISPLAY=%s)\n' "$(mark OK)" "$envdisp"
    else
      printf '     %s tkinter       window OK\n' "$(mark OK)"
    fi
  else
    printf '     %s tkinter       %s\n' "$(mark "$tkline")" "$(getval MOD_tkinter_detail "$out")"
  fi
  if [[ "$(getval PY_VENV "$out")" == "YES" ]]; then
    printf '        NOTE: this is a virtualenv, not a system interpreter.\n'
  fi

  printf '     %s import pkg_rlc_core\n' "$(mark "$(getval IMP_pkg_rlc_core "$out")")"
  printf '     %s import reduce_snp\n'   "$(mark "$(getval IMP_reduce_snp "$out")")"
  printf '     %s import pkg_rlc_plot\n' "$(mark "$(getval IMP_pkg_rlc_plot "$out")")"
  for k in IMP_pkg_rlc_core IMP_reduce_snp; do
    if [[ "$(getval "$k" "$out")" == "FAIL" ]]; then
      printf '        why: %s\n' "$(getval "${k}_detail" "$out")"
    fi
  done

  cr="$(getval CAP_reduce "$out")"; cc="$(getval CAP_cli "$out")"; cg="$(getval CAP_gui "$out")"
  echo "     ------------------------------------------------"
  printf '     tier 1  reduce_snp.py (port reduction)   %s\n' "$([[ "$cr" == YES ]] && echo AVAILABLE || echo "NOT AVAILABLE")"
  printf '     tier 2  CLI extraction -> CSV            %s\n' "$([[ "$cc" == YES ]] && echo AVAILABLE || echo "NOT AVAILABLE")"
  if [[ "$cg" == YES && "$disp" != YES ]]; then
    printf '     tier 3  GUI                              code OK, needs X11 ($DISPLAY)\n'
  else
    printf '     tier 3  GUI                              %s\n' "$([[ "$cg" == YES ]] && echo AVAILABLE || echo "NOT AVAILABLE")"
  fi
  echo

  tier=0
  if [[ "$cr" == YES ]]; then tier=1; fi
  if [[ "$cc" == YES ]]; then tier=2; fi
  if [[ "$cg" == YES ]]; then tier=3; fi
  if [[ "$cg" == YES && "$disp" == YES ]]; then tier=4; fi
  if (( tier > BEST_TIER )); then
    BEST_TIER=$tier; BEST="$py"; BEST_VENV="$(getval PY_VENV "$out")"
  fi
done

# --- verdict -----------------------------------------------------------------
if [[ -z "$BEST" || $BEST_TIER -lt 2 ]]; then
  echo "VERDICT: no interpreter on this box can run the extractor."
  if [[ -n "$BEST" && $BEST_TIER -eq 1 ]]; then
    echo "         ($BEST can run reduce_snp.py only.)"
  fi
  echo
  echo "  numpy is the hard requirement. With no network you cannot install it here;"
  echo "  find an interpreter that already has it (a Cadence/EDA bundled python3 often"
  echo "  does) and re-run:   bash deploy/doctor.sh --python /path/to/that/python3"
  exit 1
fi

echo "RECOMMENDED: $BEST"
if [[ "$BEST_VENV" == "YES" ]]; then
  echo "  (heads-up: that is a virtualenv. Fine if it works, but the red-zone"
  echo "   baseline is a system interpreter -- a venv may not exist for others.)"
fi
echo
echo "  cd $ROOT"
echo "  port reduction : $BEST reduce_snp.py --help"
echo "  CLI extraction : $BEST pkg_rlc_extractor.py --help"
if (( BEST_TIER >= 3 )); then
  echo "  GUI            : $BEST pkg_rlc_extractor.py"
fi
echo "  self-test      : $BEST -m unittest discover -s tests"
echo

# --- optional self-test ------------------------------------------------------
if (( RUN_TESTS )); then
  echo "=== running the shipped unit-test suite with $BEST ==="
  if ( cd "$ROOT" && "$BEST" -m unittest discover -s tests ); then
    echo
    echo "OK  self-test passed -- the package landed intact and this interpreter runs it."
  else
    echo
    echo "FAIL  self-test failed. The package or the environment is not sound;"
    echo "      do not trust results from this install until it passes." >&2
    exit 1
  fi
fi
