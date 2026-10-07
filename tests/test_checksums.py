import hashlib
import os
import py_compile
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.checksums import verify_checksums

# The checkout this suite lives in: src/ to import jimemo in a fresh
# interpreter, vendor/ for real vendored packages to import in one.
REPO = Path(__file__).resolve().parents[1]


def make_vendor(tmp_path: Path) -> Path:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    return vendor


def test_clean_vendor_verifies(tmp_path):
    assert verify_checksums(make_vendor(tmp_path)) == []


def test_tampered_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "mod.py").write_text("x = 2\n")
    problems = verify_checksums(vendor)
    assert len(problems) == 1
    assert "mismatch" in problems[0]
    assert "pkg/mod.py" in problems[0]


def test_missing_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "mod.py").unlink()
    assert any("missing" in p for p in verify_checksums(vendor))


def test_unlisted_python_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "sneaky.py").write_text("import os\n")
    assert verify_checksums(vendor) == ["unlisted file: pkg/sneaky.py"]


def test_unlisted_native_extension_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "_speedups.so").write_bytes(b"\x00junk\x01")
    assert any("unlisted" in p for p in verify_checksums(vendor))


def test_unlisted_pycache_bytecode_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    pycache = vendor / "pkg" / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-39.pyc").write_bytes(b"\x00junk\x01")
    # Still reported, never exempted: CPython imports a cached .pyc
    # INSTEAD of the listed .py when the header matches, so a planted
    # cache must stay visible to doctor. But with add_vendor_to_path()
    # keeping bytecode out of vendor/, any cache that IS there is a
    # leftover, so the message says so instead of reading like tamper.
    assert verify_checksums(vendor) == [
        "unlisted stale bytecode cache (safe to delete): "
        "pkg/__pycache__/mod.cpython-39.pyc"
    ]


def test_symlink_to_listed_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "alias.py").symlink_to(vendor / "pkg" / "mod.py")
    problems = verify_checksums(vendor)
    assert any("symlink not allowed" in p and "alias.py" in p for p in problems)


def test_symlinked_directory_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("import os\n")
    (vendor / "evilpkg").symlink_to(outside)
    problems = verify_checksums(vendor)
    assert any("symlink not allowed" in p and "evilpkg" in p for p in problems)


def test_listed_symlink_is_reported_not_followed(tmp_path):
    vendor = make_vendor(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("import os\n")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    (vendor / "pkg" / "link.py").symlink_to(outside)
    with (vendor / "SHA256SUMS").open("a") as fh:
        fh.write(f"{digest}  ./pkg/link.py\n")
    problems = verify_checksums(vendor)
    link_problems = [p for p in problems if "pkg/link.py" in p]
    assert link_problems == ["symlink not allowed: pkg/link.py"]


def test_listed_file_under_symlinked_directory_is_not_followed(tmp_path):
    vendor = make_vendor(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("import os\n")
    digest = hashlib.sha256((outside / "evil.py").read_bytes()).hexdigest()
    (vendor / "evilpkg").symlink_to(outside)
    with (vendor / "SHA256SUMS").open("a") as fh:
        fh.write(f"{digest}  ./evilpkg/evil.py\n")
    problems = verify_checksums(vendor)
    assert any("symlink not allowed" in p and "evilpkg" in p for p in problems)
    assert not any("checksum mismatch" in p and "evilpkg/evil.py" in p for p in problems)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo")
def test_fifo_is_reported_not_opened(tmp_path):
    vendor = make_vendor(tmp_path)
    fifo = vendor / "pkg" / "pipe.py"
    os.mkfifo(fifo)
    with (vendor / "SHA256SUMS").open("a") as fh:
        fh.write("0" * 64 + "  ./pkg/pipe.py\n")
    problems = verify_checksums(vendor)
    assert any("special file not allowed" in p and "pkg/pipe.py" in p for p in problems)


def test_malformed_line_is_reported_not_raised(tmp_path):
    vendor = make_vendor(tmp_path)
    with (vendor / "SHA256SUMS").open("a") as fh:
        fh.write("junklinewithnowhitespace\n")
    problems = verify_checksums(vendor)
    assert any(
        "malformed SHA256SUMS line" in p and "junklinewithnowhitespace" in p
        for p in problems
    )


def test_missing_sums_file_is_reported(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    assert any("SHA256SUMS" in p for p in verify_checksums(vendor))


def test_real_repo_vendor_is_clean():
    repo_vendor = Path(__file__).resolve().parents[1] / "vendor"
    assert verify_checksums(repo_vendor) == []


def test_real_repo_charts_vendor_is_clean():
    repo_charts_vendor = Path(__file__).resolve().parents[1] / "charts" / "vendor"
    assert verify_checksums(repo_charts_vendor) == []


def test_real_vendored_file_tamper_is_caught(tmp_path):
    # Doctor-tamper coverage over a real vendored file: copy the real
    # vendor/ tree (SHA256SUMS included), tamper one file, and confirm
    # verify_checksums -- the same function doctor calls -- reports it.
    # Demonstrates the generic vendor-tamper check (already exercised by
    # test_real_repo_vendor_is_clean above) covers every package, since
    # SHA256SUMS is a complete allowlist over all of vendor/. The target
    # was tomli/_parser.py until jimemo#bgaw dropped vendored tomli.
    real_vendor = Path(__file__).resolve().parents[1] / "vendor"
    copy = tmp_path / "vendor"
    shutil.copytree(real_vendor, copy)
    target = copy / "markdown" / "serializers.py"
    assert target.is_file()
    target.write_text(target.read_text() + "\n# tampered\n")
    problems = verify_checksums(copy)
    assert any("checksum mismatch" in p and "markdown/serializers.py" in p for p in problems)


def _fresh_interpreter(
    tmp_path: Path, code: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run `code` in a fresh python3 and return the CompletedProcess.

    The vendor-bytecode rules only bite when CPython actually reads and
    writes caches, and conftest sets sys.dont_write_bytecode for this
    process -- so these tests must import in a child interpreter. The
    child must not inherit a bytecode policy from the outer run either
    (PYTHONDONTWRITEBYTECODE / PYTHONPYCACHEPREFIX env, or PYTHONOPTIMIZE,
    which makes it look for .opt-N.pyc names and ignore a plain planted
    cache), and its HOME /
    XDG_CACHE_HOME point under tmp_path: jimemo redirects bytecode to a
    per-user cache dir, and the test needs it written somewhere it can
    inspect, not into a real home directory.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("PYTHONOPTIMIZE", None)
    env["HOME"] = str(home)
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )


def test_add_vendor_import_writes_no_pyc_under_vendor(tmp_path):
    """Importing a vendored package after add_vendor_to_path() must not
    leave bytecode under vendor/: a __pycache__/ dir there is an unlisted
    file in the SHA256SUMS scan (test_unlisted_pycache_bytecode_is_reported
    above) and reads like a tamper alarm. CPython must still cache the
    import -- just in sys.pycache_prefix, outside the repo."""
    vendor = tmp_path / "vendor"
    shutil.copytree(REPO / "vendor" / "yaml", vendor / "yaml")
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from jimemo import _vendor\n"
        f"_vendor.VENDOR_DIR = {str(vendor)!r}\n"
        "_vendor.add_vendor_to_path()\n"
        "import yaml\n"
        "assert Path(yaml.__file__).resolve().is_relative_to("
        "Path(sys.path[0]).resolve())\n"
        "print('PYCACHE_PREFIX', sys.pycache_prefix)\n"
    )
    result = _fresh_interpreter(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert not list(vendor.rglob("*.pyc")), "bytecode written under vendor/"
    prefix_line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith("PYCACHE_PREFIX ")
    )
    prefix = Path(prefix_line.split(" ", 1)[1])
    assert not prefix.is_relative_to(vendor)
    # The import really was cached (not silently uncached), just elsewhere.
    assert [p for p in prefix.rglob("*.pyc") if "tomli" in p.parts]


def test_planted_vendor_pyc_with_matching_header_is_not_used(tmp_path):
    """A planted vendor/pkg/__pycache__/mod.<tag>.pyc whose 16-byte header
    matches mod.py -- so CPython would accept it as fresh -- but whose code
    differs must NOT run. sys.pycache_prefix redirects reads as well as
    writes, so the listed source is what executes. This is the case the
    pycache_prefix change exists for: exempting __pycache__ from the scan
    instead would let this cache run while doctor reports clean."""
    vendor = tmp_path / "vendor"
    pkg = vendor / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    source = pkg / "mod.py"
    source.write_text('MARKER = "source"\n')

    # Compile DIFFERENT code, then re-head it to look fresh for mod.py:
    # magic and flags stay (same interpreter; TIMESTAMP is explicit because
    # py_compile writes a checked-hash pyc when SOURCE_DATE_EPOCH is set,
    # and patching that would corrupt the hash, not fake freshness); the
    # mtime (offset 8) and source size (offset 12) are patched to
    # mod.py's stat, exactly what CPython validates.
    planted_body = tmp_path / "planted_body.py"
    planted_body.write_text('MARKER = "planted"\n')
    cfile = py_compile.compile(
        str(planted_body),
        cfile=str(tmp_path / "planted.pyc"),
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
    )
    data = bytearray(Path(cfile).read_bytes())
    st = source.stat()
    struct.pack_into("<I", data, 8, int(st.st_mtime))
    struct.pack_into("<I", data, 12, st.st_size)
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc").write_bytes(
        bytes(data)
    )

    # Control: the same fixture, imported with vendor/ on sys.path but no
    # add_vendor_to_path(), must run the plant. Without it, a fixture
    # CPython rejects for some other reason would pass the check below.
    control = (
        "import sys\n"
        f"sys.path.insert(0, {str(vendor)!r})\n"
        "import pkg.mod\n"
        "print('MARKER', pkg.mod.MARKER)\n"
    )
    result = _fresh_interpreter(tmp_path, control)
    assert result.returncode == 0, result.stderr
    assert "MARKER planted" in result.stdout, result.stdout

    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from jimemo import _vendor\n"
        f"_vendor.VENDOR_DIR = {str(vendor)!r}\n"
        "_vendor.add_vendor_to_path()\n"
        "import pkg.mod\n"
        "print('MARKER', pkg.mod.MARKER)\n"
    )
    result = _fresh_interpreter(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert "MARKER source" in result.stdout, result.stdout


def test_preset_pycache_prefix_is_left_as_caller_set_it(tmp_path):
    """add_vendor_to_path() must not replace a pycache_prefix the caller
    set -- nor one a user exported as PYTHONPYCACHEPREFIX, which the
    interpreter installs as sys.pycache_prefix before any code runs. In
    both cases the vendor path is still added."""
    header = f"import sys\nsys.path.insert(0, {str(REPO / 'src')!r})\n"
    tail = (
        "from jimemo import _vendor\n"
        "_vendor.add_vendor_to_path()\n"
        "print('PYCACHE_PREFIX', sys.pycache_prefix)\n"
        "print('VENDOR_ON_PATH', str(_vendor.VENDOR_DIR) in sys.path)\n"
    )

    preset = tmp_path / "preset-pycache"
    result = _fresh_interpreter(
        tmp_path, header + f"sys.pycache_prefix = {str(preset)!r}\n" + tail
    )
    assert result.returncode == 0, result.stderr
    assert f"PYCACHE_PREFIX {preset}" in result.stdout
    assert "VENDOR_ON_PATH True" in result.stdout

    exported = tmp_path / "exported-pycache"
    result = _fresh_interpreter(
        tmp_path, header + tail, extra_env={"PYTHONPYCACHEPREFIX": str(exported)}
    )
    assert result.returncode == 0, result.stderr
    assert f"PYCACHE_PREFIX {exported}" in result.stdout
    assert "VENDOR_ON_PATH True" in result.stdout
