"""
pkg_rlc_extractor.py  --  THE ENTRY POINT.  A shim, and nothing else.

    python pkg_rlc_extractor.py                 # the GUI
    python pkg_rlc_extractor.py --cli ...       # one-shot extraction

The command line itself is `pkg_rlc/frontend/cli.py` (`pkg_rlc.frontend.cli`),
which is where the argparser, the flag refusals, the CSV writers, the report
drivers and `_emit` now live.  Read that file; this one has no logic in it.

WHY IT STAYS AT THE ROOT.  The name is the published way to run this tool: it
is in the README, in every Help tab, in the CLI's own `--help` examples, in
`deploy/doctor.sh`'s closing advice, and it is the SENTINEL both `deploy.sh`
and `deploy/pack.ps1` check for to confirm they are pointing at an install root
rather than at its parent.  Moving it would break all of that for no gain --
the reason the 25 modules moved into `pkg_rlc/` is that their nine-character
shared prefix was doing a package's job badly, and an entry point has no such
prefix problem.

`main` is re-exported by name because it is driven IN-PROCESS: `tests/
_cli_capture.py` calls it over 143 invocations to build
`tests/fixtures/cli_reference/`, and `tests/test_attrib_cli.py` runs this file
as a subprocess.  Both must keep working.

The star import carries the rest of the public surface, so
`pkg_rlc_extractor.<name>` keeps resolving for a caller that had it.  A private
name (`_attr_zt`, `_make_arg_parser`) lives on `pkg_rlc.frontend.cli` and
should be imported from there -- star import skips underscore names, and
listing forty of them here would be a second copy of the CLI's surface that
could come to disagree with it.
"""

from __future__ import annotations

import sys

from pkg_rlc.frontend.cli import *      # noqa: F401,F403  (re-export)
from pkg_rlc.frontend.cli import main   # noqa: F401       (the entry point)

if __name__ == "__main__":
    sys.exit(main())
