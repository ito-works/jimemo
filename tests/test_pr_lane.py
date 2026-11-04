"""The PR-lane machinery under ci/ runs inside the registry gate (kata jibot-code#bden).

The row is `python3 -m pytest tests -q` (gate.yml runs it as ci/gate.sh, which
reports pytest's exit 2 as 1; kata jibot-code#qe35); these three tests are how
that row also gates a change to a workflow or to ci/, on both lanes. Each one
is the exact command jibot-ops's gate runs. The bridge suite is verbatim from
jibot-ops 8f46f7e and needs nothing beyond the stdlib; the shell suite wants
/bin/bash (3.2 or later) and runs everywhere the gate does, including the
hosted gate job on Ubuntu, where /bin/sh is dash (jimemo#n5tz).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _run(*argv):
    proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


@pytest.mark.parametrize("suite", ["ci/scripts", "ci/bridge"])
def test_pr_lane_unit_suites(suite):
    _run(sys.executable, "-m", "unittest", "discover", "-s", suite, "-t", suite)


def test_pr_lane_gate_wrapper_and_lane_guard():
    _run("/bin/bash", "tests/test_ci_gate.sh")


# ci/gate-ready.sh: the readiness probe gate.yml names in RUN_GATE_PREFLIGHT.
# run-gate.sh calls it as `ci/gate-ready.sh --version` under `env -i` with the
# gate's PATH, so it is exercised the same way here, with a PATH whose python3
# is a shim.

def _ready_with(tmp_path, python3_body):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text("#!/bin/sh\n" + python3_body)
    (shim / "python3").chmod(0o755)
    return subprocess.run(
        ["env", "-i", "PATH=%s:/usr/bin:/bin" % shim, "HOME=%s" % tmp_path,
         "ci/gate-ready.sh", "--version"],
        cwd=REPO, capture_output=True, text=True, timeout=60)


def test_gate_ready_accepts_a_python_that_can_run_the_gate(tmp_path):
    # This interpreter, with pytest reachable the way the gate's interpreter
    # reaches it (in site-packages, not under a HOME the probe's scratch HOME
    # hides): the shim names pytest's own location, so the test holds wherever
    # this machine keeps it.
    site = str(Path(pytest.__file__).resolve().parent.parent)
    proc = _ready_with(tmp_path, 'PYTHONPATH="%s" exec "%s" "$@"\n'
                       % (site, sys.executable))
    assert proc.returncode == 0, proc.stderr


def test_gate_ready_refuses_a_python_below_the_floor(tmp_path):
    # A system /usr/bin/python3 below the floor (3.9 on a Mac, 3.12 on
    # ubuntu-latest): starts, satisfies the wrapper's own probe, cannot run
    # this gate. Skipped on a host whose system python is already at the floor.
    if not Path("/usr/bin/python3").exists():
        pytest.skip("no /usr/bin/python3")
    ver = subprocess.run(["/usr/bin/python3", "-c",
                          "import sys; print(sys.version_info >= (3, 13, 6))"],
                         capture_output=True, text=True).stdout.strip()
    if ver == "True":
        pytest.skip("/usr/bin/python3 satisfies the floor here")
    proc = _ready_with(tmp_path, 'exec /usr/bin/python3 "$@"\n')
    assert proc.returncode != 0
    assert "need a final release" in proc.stderr


def test_gate_ready_refuses_a_python_without_pytest(tmp_path):
    # Same interpreter, pytest hidden: the other way the gate ends up with a
    # python3 that starts and a gate that exits 1.
    proc = _ready_with(
        tmp_path,
        'exec "%s" -S -c "import sys; sys.argv[0]=\'-\'; exec(sys.stdin.read())"\n'
        % sys.executable)
    assert proc.returncode != 0
    assert "has no pytest" in proc.stderr


def test_gate_ready_describes_the_hosted_runner():
    # gate.yml runs on GitHub-hosted ubuntu-latest (jimemo#twvr). The header
    # once described the self-hosted Mac runner -- its bare PATH, Homebrew's
    # python3, a 3.9 /usr/bin/python3 -- and none of that applies now
    # (jimemo#n5tz item 3).
    header = (REPO / "ci" / "gate-ready.sh").read_text()
    for stale in ("runner host", "Homebrew", "is 3.9", "mujin-gate"):
        assert stale not in header, stale
    assert "setup-python" in header


# ci/gate.sh: the gate command gate.yml runs (kata jibot-code#qe35). pytest's
# exit 2 is a collection error, a branch defect; run-gate.sh and repoman read
# 2 as "could not run" and defer it, so the script reports it as 1. A shim
# python3 stands in for pytest, so this never runs the suite inside itself.

def _gate_with(tmp_path, rc):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text(
        '#!/bin/sh\necho "$@" > "%s"\nexit %d\n' % (tmp_path / "argv", rc))
    (shim / "python3").chmod(0o755)
    proc = subprocess.run(
        ["env", "-i", "PATH=%s:/usr/bin:/bin" % shim, "HOME=%s" % tmp_path,
         "/bin/sh", "-c", "ci/gate.sh"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    return proc, (tmp_path / "argv").read_text().strip()


@pytest.mark.parametrize("pytest_rc,gate_rc", [(0, 0), (1, 1), (2, 1),
                                               (4, 4), (5, 5)])
def test_gate_reports_pytest_exit_2_as_a_failure(tmp_path, pytest_rc, gate_rc):
    proc, argv = _gate_with(tmp_path, pytest_rc)
    assert proc.returncode == gate_rc, proc.stderr
    assert argv == "-m pytest tests -q"
    assert ("reported as 1" in proc.stderr) == (pytest_rc == 2)


def test_gate_runs_from_the_repo_root(tmp_path):
    # gate.yml and repoman both start at the checkout's root, but the script
    # must not depend on it: `tests` is relative.
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text('#!/bin/sh\npwd -P > "%s"\n'
                                  % (tmp_path / "cwd"))
    (shim / "python3").chmod(0o755)
    proc = subprocess.run(
        ["env", "-i", "PATH=%s:/usr/bin:/bin" % shim,
         str(REPO / "ci" / "gate.sh")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "cwd").read_text().strip() == str(REPO.resolve())


def test_pytest_still_exits_2_on_a_collection_error(tmp_path):
    # The premise of ci/gate.sh, measured: a test module that does not import
    # makes pytest exit 2. If a pytest release changes that, this says so.
    (tmp_path / "test_broken.py").write_text("import nonexistent_mod_qe35\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stdout[-2000:]
