#!/bin/sh
# gate.sh — jimemo's gate: the test suite, with pytest's exit 2 read as a
# branch failure (kata jibot-code#qe35).
#
# ci/run-gate.sh and repoman's drain._gate_cannot_run read exit 2 as "the gate
# could not run": one retry, then outcome=deferred, and the producer is not
# told. That code is cell-fleet test-gate.sh's. A bare `python3 -m pytest`
# inherits pytest's codes instead, and pytest exits 2 on a collection error —
# a test module that does not import or does not parse, which is a defect in
# the branch. So a branch that broke a test module's import would leave the
# queue as deferred, with an infrastructure alert, and nobody asked to fix it.
#
# Here pytest's 2 leaves as 1. pytest also exits 2 on a KeyboardInterrupt;
# nothing in either gate sends SIGINT (repoman's supervisor kills and flags
# the timeout itself; Actions cancels the step), so that case is not a defer
# either. The cases that ARE could-not-run — no python3 that starts, a python3
# below the floor or without pytest — are answered before this runs, by
# run-gate.sh's preflight and ci/gate-ready.sh, and a python3 that cannot be
# found exits 127 here, never 2.
cd "$(dirname "$0")/.." || exit 2
rc=0
python3 -m pytest tests -q || rc=$?
if [ "$rc" -eq 2 ]; then
    echo "gate: pytest exited 2 (a collection error or an interrupt) — a branch failure, reported as 1" >&2
    exit 1
fi
exit "$rc"
