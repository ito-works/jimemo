import json
import os
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PublishError
from jimemo.publish.wrangler import NO_WRANGLER_MESSAGE, MockWrangler, Wrangler


@pytest.fixture(autouse=True)
def mock_wrangler_choice(monkeypatch):
    # Keep argv tests independent of any globally installed Wrangler.
    monkeypatch.setenv("JIMEMO_WRANGLER", "mock-wrangler")


class FakeRunner:
    """Records every argv (and the env dict passed alongside it, if any)
    it's called with and returns a scripted result (or the next of
    several, in order) instead of touching a real subprocess."""

    def __init__(self, *results):
        self.calls = []
        self.envs = []
        self.cwds = []
        self._results = list(results)

    def __call__(self, argv, env=None, *, cwd=None):
        self.calls.append(argv)
        self.envs.append(env)
        self.cwds.append(cwd)
        if len(self._results) == 1:
            return self._results[0]
        return self._results.pop(0)


class RaisingRunner:
    """Simulates the selected Wrangler being absent from PATH."""

    def __call__(self, argv, env=None, *, cwd=None):
        raise FileNotFoundError("mock-wrangler: command not found")


def test_pages_deploy_builds_expected_argv(tmp_path):
    runner = FakeRunner(CompletedProcess([], 0, stdout="Deployment complete!\n", stderr=""))
    w = Wrangler(runner=runner)

    w.pages_deploy("my-project", tmp_path)

    assert runner.calls == [
        ["mock-wrangler", "pages", "deploy", str(tmp_path),
         "--project-name", "my-project", "--branch", "main"]
    ]


def test_pages_deploy_accepts_a_different_branch(tmp_path):
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.pages_deploy("my-project", tmp_path, branch="preview")

    assert runner.calls == [
        ["mock-wrangler", "pages", "deploy", str(tmp_path),
         "--project-name", "my-project", "--branch", "preview"]
    ]


def test_pages_project_names_builds_expected_argv_and_parses_json():
    runner = FakeRunner(
        CompletedProcess(
            [],
            0,
            stdout=json.dumps([{"name": "one"}, {"name": "two"}]),
            stderr="",
        )
    )
    w = Wrangler(runner=runner)

    names = w.pages_project_names()

    assert runner.calls == [
        ["mock-wrangler", "pages", "project", "list", "--json"]
    ]
    assert names == ["one", "two"]


def test_pages_project_names_accepts_wrapped_result_shape():
    runner = FakeRunner(
        CompletedProcess(
            [],
            0,
            stdout=json.dumps({"result": [{"name": "wrapped"}]}),
            stderr="",
        )
    )
    w = Wrangler(runner=runner)

    assert w.pages_project_names() == ["wrapped"]


def test_pages_project_names_malformed_json_raises_publish_error():
    runner = FakeRunner(CompletedProcess([], 0, stdout="not json", stderr=""))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.pages_project_names()
    assert "pages project list" in str(exc.value)


@pytest.mark.parametrize("branch", ["main", "production"])
def test_pages_project_create_uses_classic_pages_rest(monkeypatch, branch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token")
    runner = FakeRunner(CompletedProcess([], 0, stdout=json.dumps({
        "success": True, "result": {"name": "my-project"}}), stderr=""))
    w = Wrangler(runner=runner, account_id="acct-1")

    assert w.pages_project_create("my-project", branch) == {"name": "my-project"}

    argv, = runner.calls
    assert argv[:3] == ["curl", "-q", "-sS"]
    assert argv[argv.index("-X") + 1] == "POST"
    assert json.loads(argv[argv.index("-d") + 1]) == {
        "name": "my-project", "production_branch": branch}
    assert argv[-1] == "https://api.cloudflare.com/client/v4/accounts/acct-1/pages/projects"
    assert "%CLOUDFLARE_API_TOKEN" in argv
    assert "super-secret-token" not in " ".join(argv)
    assert runner.envs == [None]


def test_kv_put_builds_expected_argv():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.kv_put("ns123", "abc123", "2026-07-05T00:00:00.000Z")

    assert runner.calls == [
        ["mock-wrangler", "kv", "key", "put", "abc123", "2026-07-05T00:00:00.000Z",
         "--namespace-id", "ns123", "--remote"]
    ]


def test_kv_get_builds_expected_argv_and_returns_stripped_value():
    runner = FakeRunner(CompletedProcess([], 0, stdout="2026-07-05T00:00:00.000Z\n", stderr=""))
    w = Wrangler(runner=runner)

    value = w.kv_get("ns123", "abc123")

    assert runner.calls == [
        ["mock-wrangler", "kv", "key", "get", "abc123",
         "--namespace-id", "ns123", "--text", "--remote"]
    ]
    assert value == "2026-07-05T00:00:00.000Z"


def test_kv_list_builds_expected_argv_and_parses_json():
    stdout = json.dumps([{"name": "abc123"}, {"name": "def456"}])
    runner = FakeRunner(CompletedProcess([], 0, stdout=stdout, stderr=""))
    w = Wrangler(runner=runner)

    entries = w.kv_list("ns123")

    assert runner.calls == [
        ["mock-wrangler", "kv", "key", "list", "--namespace-id", "ns123", "--remote"]
    ]
    assert entries == [{"name": "abc123"}, {"name": "def456"}]


def test_kv_list_malformed_json_raises_publish_error():
    runner = FakeRunner(CompletedProcess([], 0, stdout="not json", stderr=""))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.kv_list("ns123")
    assert "kv key list" in str(exc.value)


def test_nonzero_exit_raises_publish_error_with_action_and_stderr():
    runner = FakeRunner(CompletedProcess([], 3, stdout="", stderr="boom: not authorized"))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.pages_deploy("p", "/tmp/whatever")
    msg = str(exc.value)
    assert "pages deploy" in msg
    assert "boom: not authorized" in msg


def test_missing_wrangler_raises_clear_error_from_every_method(tmp_path):
    w = Wrangler(runner=RaisingRunner())

    for call in (
        lambda: w.pages_project_names(),
        lambda: w.pages_deploy("p", tmp_path),
        lambda: w.kv_put("ns", "abc123", "ts"),
        lambda: w.kv_get("ns", "abc123"),
        lambda: w.kv_list("ns"),
    ):
        with pytest.raises(PublishError) as exc:
            call()
        assert str(exc.value) == NO_WRANGLER_MESSAGE


def test_check_available_true_when_wrangler_resolves(monkeypatch):
    import jimemo.publish.wrangler as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/wrangler")
    assert Wrangler().check_available() is True


def test_check_available_false_when_wrangler_missing(monkeypatch):
    import jimemo.publish.wrangler as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    assert Wrangler().check_available() is False


def test_default_runner_is_real_subprocess_argv_no_shell():
    import jimemo.publish.wrangler as mod

    result = mod._run([sys.executable, "-c", "print('hello from subprocess')"])
    assert result.returncode == 0
    assert "hello from subprocess" in result.stdout


def test_mock_wrangler_records_calls_and_round_trips_kv(tmp_path):
    mock = MockWrangler()

    assert mock.pages_project_names() == []
    mock.pages_project_create("proj")
    assert mock.pages_project_names() == ["proj"]
    mock.pages_deploy("proj", tmp_path)
    mock.kv_put("ns", "abc123", "2026-07-05T00:00:00.000Z")
    value = mock.kv_get("ns", "abc123")
    entries = mock.kv_list("ns")

    assert value == "2026-07-05T00:00:00.000Z"
    assert entries == [{"name": "abc123"}]
    assert ("pages_project_names",) in mock.calls
    assert ("pages_project_create", "proj", "main") in mock.calls
    assert ("pages_deploy", "proj", str(tmp_path), "main") in mock.calls
    assert ("kv_put", "ns", "abc123", "2026-07-05T00:00:00.000Z") in mock.calls
    assert mock.check_available() is True


def test_mock_wrangler_kv_get_unknown_key_returns_empty_string():
    mock = MockWrangler()
    assert mock.kv_get("ns", "never-written") == ""


def test_account_id_threads_cloudflare_account_id_into_subprocess_env():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    assert len(runner.envs) == 1
    env = runner.envs[0]
    assert env is not None
    assert env["CLOUDFLARE_ACCOUNT_ID"] == "acct-123"


def test_account_id_env_is_inherited_environment_plus_account_id(monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VAR", "keep-me")
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    env = runner.envs[0]
    assert env["SOME_UNRELATED_VAR"] == "keep-me"
    assert env["CLOUDFLARE_ACCOUNT_ID"] == "acct-123"


def test_no_account_id_passes_no_env_override():
    """Without an account_id, Wrangler must not touch the subprocess
    environment at all -- env stays None, i.e. subprocess.run's own
    default of inheriting the parent process environment unchanged."""
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.kv_get("ns123", "abc123")

    assert runner.envs == [None]


def test_account_id_settable_after_construction():
    """setup.py's wizard only learns the account id partway through --
    after the Wrangler is already constructed -- so account_id must be
    settable as a plain attribute, not fixed at __init__ time."""
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.account_id = "acct-456"
    w.kv_get("ns123", "abc123")

    assert runner.envs[0]["CLOUDFLARE_ACCOUNT_ID"] == "acct-456"


def test_account_id_never_carries_the_cf_api_token(monkeypatch):
    """account_id is a non-secret identifier threaded through the
    subprocess env; it must never carry the Cloudflare API token itself
    -- that stays wherever it already was, in the inherited environment,
    untouched and unread by this module."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token")
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    env = runner.envs[0]
    # Inherited unchanged (subprocess would see it exactly as it exists
    # in this process's own environment) -- not read, copied, or altered
    # by Wrangler itself.
    assert env["CLOUDFLARE_API_TOKEN"] == "super-secret-token"


def test_module_never_reads_the_cf_api_token_value():
    """jimemo must never read the value of CLOUDFLARE_API_TOKEN --
    wrangler resolves its own auth directly, from its own subprocess
    environment (inherited unchanged) or its own credential store.
    Passing CLOUDFLARE_ACCOUNT_ID through the subprocess env for account
    scoping is fine and expected (a non-secret identifier) -- os /
    os.environ are now legitimately used for that -- but nothing in this
    module may ever look up the token's value."""
    import jimemo.publish.wrangler as mod

    src = Path(mod.__file__).read_text()
    for pattern in (
        'os.environ.get("CLOUDFLARE_API_TOKEN")',
        "os.environ.get('CLOUDFLARE_API_TOKEN')",
        'os.environ["CLOUDFLARE_API_TOKEN"]',
        "os.environ['CLOUDFLARE_API_TOKEN']",
        'os.getenv("CLOUDFLARE_API_TOKEN")',
        "os.getenv('CLOUDFLARE_API_TOKEN')",
    ):
        assert pattern not in src


# ---------------------------------------------------------------------------
# Fail-closed: Pages project fail_open via curl (jibot-code#efw6). Wrangler
# has no subcommand for it; curl imports the token from ITS environment
# (--variable %NAME / --expand-header), so jimemo still never reads it.
# ---------------------------------------------------------------------------

from jimemo.publish.wrangler import (  # noqa: E402
    FAIL_CLOSED_BODY, TOKEN_ENV_REQUIRED_MESSAGE,
)

PROJECT_URL = "https://api.cloudflare.com/client/v4/accounts/acct-1/pages/projects/friend-notes"


def _project_json(prod, prev, success=True):
    return json.dumps({"success": success, "errors": [], "result": {
        "name": "friend-notes",
        "deployment_configs": {"production": {"fail_open": prod},
                               "preview": {"fail_open": prev}}}})


def _ok(stdout):
    return CompletedProcess([], 0, stdout=stdout, stderr="")


def _wrangler_with_token(monkeypatch, *results):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    runner = FakeRunner(*results)
    return Wrangler(runner=runner, account_id="acct-1"), runner


def test_fail_open_get_argv_keeps_the_token_out_of_argv(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token")
    runner = FakeRunner(_ok(_project_json(False, False)))
    w = Wrangler(runner=runner, account_id="acct-1")

    flags = w.pages_project_fail_open("friend-notes")

    argv = runner.calls[0]
    assert flags == {"production": False, "preview": False}
    assert argv[0] == "curl"
    assert argv[1] == "-q"                      # first: no .curlrc (could enable --trace)
    assert "--max-time" in argv and argv[argv.index("--max-time") + 1] == "30"
    assert argv[argv.index("--variable") + 1] == "%CLOUDFLARE_API_TOKEN"
    assert argv[argv.index("--expand-header") + 1] == "Authorization: Bearer {{CLOUDFLARE_API_TOKEN}}"
    assert argv[argv.index("-X") + 1] == "GET"
    assert "-sS" in argv
    assert argv[-1] == PROJECT_URL
    assert "-d" not in argv
    assert all("super-secret-token" not in a for a in argv)
    assert runner.envs[0] is None               # inherited env, never a copy


def test_set_fail_closed_patches_then_verifies(monkeypatch):
    w, runner = _wrangler_with_token(
        monkeypatch, _ok(_project_json(False, False)), _ok(_project_json(False, False)))

    w.pages_project_set_fail_closed("friend-notes")

    patch_argv, get_argv = runner.calls
    assert patch_argv[1] == "-q"
    assert patch_argv[patch_argv.index("-X") + 1] == "PATCH"
    assert patch_argv[patch_argv.index("-d") + 1] == FAIL_CLOSED_BODY
    assert json.loads(FAIL_CLOSED_BODY) == {"deployment_configs": {
        "production": {"fail_open": False}, "preview": {"fail_open": False}}}
    assert get_argv[get_argv.index("-X") + 1] == "GET"
    assert runner.envs == [None, None]


def test_set_fail_closed_that_does_not_stick_raises(monkeypatch):
    w, _ = _wrangler_with_token(
        monkeypatch, _ok(_project_json(False, False)), _ok(_project_json(True, False)))

    with pytest.raises(PublishError) as exc:
        w.pages_project_set_fail_closed("friend-notes")
    assert "still fail-open" in str(exc.value)


@pytest.mark.parametrize("value", [True, None, 0, "", "false", 1])
def test_only_literal_false_reads_as_closed(monkeypatch, value):
    w, _ = _wrangler_with_token(monkeypatch, _ok(_project_json(value, value)))

    assert w.pages_project_fail_open("friend-notes") == {"production": True, "preview": True}


def test_missing_deployment_configs_reads_as_open(monkeypatch):
    w, _ = _wrangler_with_token(
        monkeypatch, _ok(json.dumps({"success": True, "errors": [], "result": {"name": "friend-notes"}})))

    assert w.pages_project_fail_open("friend-notes") == {"production": True, "preview": True}


@pytest.mark.parametrize("success", [1, "true", None, False])
def test_success_must_be_literal_true(monkeypatch, success):
    w, _ = _wrangler_with_token(monkeypatch, _ok(_project_json(False, False, success=success)))

    with pytest.raises(PublishError):
        w.pages_project_fail_open("friend-notes")


def test_non_json_output_raises(monkeypatch):
    w, _ = _wrangler_with_token(monkeypatch, _ok("<html>502</html>"))

    with pytest.raises(PublishError):
        w.pages_project_fail_open("friend-notes")


def test_curl_failure_raises_with_stderr(monkeypatch):
    w, _ = _wrangler_with_token(
        monkeypatch, CompletedProcess([], 2, stdout="", stderr="curl: variable expansion failure"))

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert "exit 2" in str(exc.value)
    assert "curl: variable expansion failure" in str(exc.value)   # stderr preserved


def test_missing_token_env_refuses_without_calling_curl(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    runner = FakeRunner(_ok(_project_json(False, False)))
    w = Wrangler(runner=runner, account_id="acct-1")

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert str(exc.value) == TOKEN_ENV_REQUIRED_MESSAGE
    assert runner.calls == []


def test_missing_account_id_refuses_without_calling_curl(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    runner = FakeRunner(_ok(_project_json(False, False)))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert "account_id" in str(exc.value)
    assert "[publish.cloudflare].account_id" in str(exc.value)
    assert "jimemo publish setup" in str(exc.value)
    assert runner.calls == []


def test_missing_curl_binary_raises(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    w = Wrangler(runner=RaisingRunner(), account_id="acct-1")

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert "curl" in str(exc.value)


def test_curl_version_parses_first_line():
    runner = FakeRunner(_ok("curl 8.7.1 (x86_64-apple-darwin25.0) libcurl/8.7.1\nRelease-Date: 2024-03-27\n"))
    w = Wrangler(runner=runner)

    assert w.curl_version() == (8, 7, 1)
    assert runner.calls == [["curl", "-q", "--version"]]
    assert runner.envs == [None]


def test_curl_version_none_when_missing_or_unparseable():
    assert Wrangler(runner=RaisingRunner()).curl_version() is None
    assert Wrangler(runner=FakeRunner(_ok("not curl"))).curl_version() is None
    assert Wrangler(runner=FakeRunner(CompletedProcess([], 1, stdout="", stderr=""))).curl_version() is None


def test_cf_api_never_passes_an_env_dict(monkeypatch):
    """The token must stay in the inherited environment: jimemo never
    materializes a copy of os.environ for the curl call (dict(os.environ)
    would be a read of the token value)."""
    w, runner = _wrangler_with_token(monkeypatch, _ok(_project_json(False, False)))
    w.pages_project_fail_open("friend-notes")
    assert runner.envs == [None]


def test_mock_wrangler_fail_open_state():
    m = MockWrangler()
    assert m.pages_project_fail_open("p") == {"production": False, "preview": False}
    m.pages_project_create("p")
    assert m.pages_project_fail_open("p") == {"production": True, "preview": True}
    m.pages_project_set_fail_closed("p")
    assert m.pages_project_fail_open("p") == {"production": False, "preview": False}
    assert MockWrangler(fail_open=True).pages_project_fail_open("p") == {"production": True, "preview": True}
    assert m.curl_version() == (8, 7, 1)
    assert ("pages_project_set_fail_closed", "p") in m.calls
    assert ("pages_project_fail_open", "p") in m.calls


class PermissionRunner:
    """Simulates a curl that exists but cannot be executed (mode 000,
    wrong architecture, ...): process start raises an OSError that is
    NOT FileNotFoundError."""

    def __call__(self, argv, env=None, *, cwd=None):
        raise PermissionError(13, "Permission denied", "curl")


def test_curl_version_none_on_permission_error():
    assert Wrangler(runner=PermissionRunner()).curl_version() is None


def test_fail_open_get_permission_error_is_a_publish_error(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    w = Wrangler(runner=PermissionRunner(), account_id="acct-1")

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    msg = str(exc.value)
    assert "curl" in msg
    assert "PermissionError" in msg


def test_set_fail_closed_permission_error_is_a_publish_error(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    w = Wrangler(runner=PermissionRunner(), account_id="acct-1")

    with pytest.raises(PublishError) as exc:
        w.pages_project_set_fail_closed("friend-notes")
    assert "curl" in str(exc.value)


@pytest.mark.parametrize("stdout", [
    "[]",                                          # valid JSON, non-object root
    json.dumps({"success": True, "result": []}),   # object root, non-object result
])
def test_non_object_response_raises(monkeypatch, stdout):
    w, _ = _wrangler_with_token(monkeypatch, _ok(stdout))

    with pytest.raises(PublishError):
        w.pages_project_fail_open("friend-notes")


TOKEN_HINT = "check that CLOUDFLARE_API_TOKEN is valid and scoped Pages:Edit"


@pytest.mark.parametrize("code", [9106, 10000])
def test_auth_error_codes_add_a_token_scope_hint(monkeypatch, code):
    envelope = json.dumps({"success": False, "result": None, "errors": [
        {"code": code, "message": "Authentication error"}]})
    w, _ = _wrangler_with_token(monkeypatch, _ok(envelope))

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert TOKEN_HINT in str(exc.value)


def test_other_error_codes_get_no_token_hint(monkeypatch):
    envelope = json.dumps({"success": False, "result": None, "errors": [
        {"code": 8000007, "message": "Project not found"}]})
    w, _ = _wrangler_with_token(monkeypatch, _ok(envelope))

    with pytest.raises(PublishError) as exc:
        w.pages_project_fail_open("friend-notes")
    assert TOKEN_HINT not in str(exc.value)
    assert "8000007" in str(exc.value)


@pytest.mark.parametrize("relative", [False, True])
def test_deploy_discovers_functions_from_deploy_directory(tmp_path, monkeypatch, relative):
    """A mock executable models Wrangler's cwd-relative Functions discovery."""
    caller = tmp_path / "caller"
    caller.mkdir()
    deploy = caller / "deploy dir"
    (deploy / "functions").mkdir(parents=True)
    (deploy / "functions" / "_middleware.js").write_text("tombstone middleware")
    executable = tmp_path / "mock-wrangler"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "print(json.dumps({'cwd': str(Path.cwd()), 'args': sys.argv[1:],\n"
        " 'middleware': Path('functions/_middleware.js').read_text()}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.chdir(caller)

    from jimemo.publish.wrangler import _run

    def runner(argv, env=None, *, cwd=None):
        # Keep this cwd regression independent of how Wrangler is selected.
        return _run([str(executable), *argv[argv.index("pages"):]], env, cwd=cwd)

    result = Wrangler(runner=runner).pages_deploy(
        "my-project", deploy.relative_to(caller) if relative else deploy
    )

    output = json.loads(result.stdout)
    assert output["cwd"] == str(deploy)
    assert output["middleware"] == "tombstone middleware"
    assert output["args"] == ["pages", "deploy", str(deploy),
                              "--project-name", "my-project", "--branch", "main"]
    assert Path.cwd() == caller


def _deployment_json(deployment):
    return _ok(json.dumps({"success": True, "result": {"canonical_deployment": deployment}}))


def _verifying_wrangler(monkeypatch, *results):
    """A Wrangler whose post-deploy waits are recorded, not slept."""
    from jimemo.publish.wrangler import Wrangler as W

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    runner = FakeRunner(*results)
    sleeps = []
    return W(runner=runner, account_id="acct-1", sleep=sleeps.append), runner, sleeps


@pytest.mark.parametrize("deployment", [
    {"id": "mine", "uses_functions": False}, {"id": "mine", "uses_functions": "true"},
    {"id": "mine", "uses_functions": 1}, {"id": "mine"}])
def test_functions_check_refuses_this_deployment_without_the_bundle(monkeypatch, deployment):
    from jimemo.publish.wrangler import DeployUnverifiedError

    w, runner, sleeps = _verifying_wrangler(monkeypatch, _deployment_json(deployment))
    with pytest.raises(DeployUnverifiedError, match="uses_functions=true"):
        w.pages_project_require_functions("friend-notes", "mine")
    # This upload's deployment answered: a definite refusal, no waiting.
    assert len(runner.calls) == 1 and sleeps == []
    assert runner.calls[0][-1] == PROJECT_URL


@pytest.mark.parametrize("deployment", [
    None, {}, {"uses_functions": True}, {"id": "", "uses_functions": True},
    # The deployment from BEFORE this upload proves nothing about it ...
    {"id": "old", "uses_functions": True},
    # ... and neither does another machine's concurrent one.
    {"id": "theirs", "uses_functions": True}])
def test_functions_check_refuses_when_production_is_not_this_upload(monkeypatch, deployment):
    from jimemo.publish.wrangler import (
        VERIFY_ATTEMPTS, VERIFY_DELAY_S, DeployUnverifiedError)

    w, runner, sleeps = _verifying_wrangler(monkeypatch, _deployment_json(deployment))
    with pytest.raises(DeployUnverifiedError, match="uses_functions=true"):
        w.pages_project_require_functions("friend-notes", "mine")
    assert len(runner.calls) == VERIFY_ATTEMPTS
    assert sleeps == [VERIFY_DELAY_S] * (VERIFY_ATTEMPTS - 1)


def test_functions_check_is_not_satisfied_by_another_upload(monkeypatch):
    """Three ids: A was production, B (someone else's, with Functions)
    takes over, then this upload C activates WITHOUT Functions. B must not
    pass the check on C's behalf."""
    from jimemo.publish.wrangler import DeployUnverifiedError

    a = _deployment_json({"id": "A", "uses_functions": True})
    b = _deployment_json({"id": "B", "uses_functions": True})
    c = _deployment_json({"id": "C", "uses_functions": False})
    w, runner, sleeps = _verifying_wrangler(monkeypatch, a, b, b, c)
    with pytest.raises(DeployUnverifiedError, match="deployment C does not confirm"):
        w.pages_project_require_functions("friend-notes", "C")
    assert len(runner.calls) == 4


def test_functions_check_waits_for_delayed_activation(monkeypatch):
    old = _deployment_json({"id": "old", "uses_functions": True})
    mine = _deployment_json({"id": "mine", "uses_functions": True})
    w, runner, sleeps = _verifying_wrangler(monkeypatch, old, old, mine)
    w.pages_project_require_functions("friend-notes", "mine")
    assert len(runner.calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize("deployment_id", [None, "", 7])
def test_functions_check_refuses_without_a_deployment_id(monkeypatch, deployment_id):
    from jimemo.publish.wrangler import DeployUnverifiedError

    w, runner, _ = _verifying_wrangler(
        monkeypatch, _deployment_json({"id": "x", "uses_functions": True}))
    with pytest.raises(DeployUnverifiedError, match="did not report the id"):
        w.pages_project_require_functions("friend-notes", deployment_id)
    assert runner.calls == []


def _output_writing_wrangler(tmp_path, body):
    """A mock wrangler that appends ``body`` to WRANGLER_OUTPUT_FILE_PATH,
    as the real one does, and prints where that file is."""
    executable = tmp_path / "mock-wrangler"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "path = os.environ['WRANGLER_OUTPUT_FILE_PATH']\n"
        f"open(path, 'a').write({body!r})\n"
        "print(path)\n"
    )
    executable.chmod(0o755)
    return Wrangler(executable=str(executable))


def test_pages_deploy_reads_the_deployment_id_wrangler_reports(tmp_path):
    body = (
        'not json\n'
        '{"type": "wrangler-session", "deployment_id": "no"}\n'
        '{"type": "pages-deploy", "deployment_id": "dep-123", "url": "u"}\n'
        '{"type": "pages-deploy-detailed", "deployment_id": "other"}\n'
    )
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    result = _output_writing_wrangler(tmp_path, body).pages_deploy("p", deploy)

    assert result.deployment_id == "dep-123"
    out_file = Path(result.stdout.strip())
    # Outside the deployed tree, and gone once the deploy returns.
    assert deploy.resolve() not in out_file.resolve().parents
    assert not out_file.exists()


@pytest.mark.parametrize("body", [
    "", "garbage\n", '{"type": "pages-deploy"}\n',
    '{"type": "pages-deploy", "deployment_id": ""}\n',
    '{"type": "pages-deploy", "deployment_id": 5}\n', '[1]\n'])
def test_pages_deploy_reports_no_id_when_wrangler_wrote_none(tmp_path, body):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    result = _output_writing_wrangler(tmp_path, body).pages_deploy("p", deploy)
    assert result.deployment_id is None


def test_pages_deploy_env_adds_only_the_output_path_and_log_level(
        tmp_path, monkeypatch):
    monkeypatch.delenv("WRANGLER_LOG", raising=False)
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    Wrangler(runner=runner, account_id="acct-9").pages_deploy("p", tmp_path)
    env, = runner.envs
    added = {k: v for k, v in env.items() if os.environ.get(k) != v}
    assert set(added) == {"WRANGLER_OUTPUT_FILE_PATH", "CLOUDFLARE_ACCOUNT_ID",
                          "WRANGLER_LOG"}
    assert added["CLOUDFLARE_ACCOUNT_ID"] == "acct-9"
    assert added["WRANGLER_LOG"] == "log"


@pytest.mark.parametrize("stdout,code,match", [
    ("not JSON", 0, "could not parse JSON"),
    ('{"success": false, "errors": [{"code": 9106}], "result": null}', 0, "scoped Pages:Edit"),
    ('{"success": true, "result": []}', 0, "success=true"),
    ("", 7, "exit 7"),
])
def test_project_create_refuses_api_failures(monkeypatch, stdout, code, match):
    w, _ = _wrangler_with_token(monkeypatch, CompletedProcess([], code, stdout=stdout, stderr="oops"))
    with pytest.raises(PublishError, match=match):
        w.pages_project_create("friend-notes")


def test_project_create_duplicate_has_a_specific_error(monkeypatch):
    from jimemo.publish.wrangler import PagesProjectExistsError
    response = json.dumps({"success": False, "result": None,
                           "errors": [{"code": 8000002, "message": "Project exists"}]})
    w, _ = _wrangler_with_token(monkeypatch, _ok(response))
    with pytest.raises(PagesProjectExistsError):
        w.pages_project_create("friend-notes")


def test_project_create_requires_token_and_account_id(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    runner = FakeRunner(_ok("{}"))
    with pytest.raises(PublishError, match="account_id"):
        Wrangler(runner=runner).pages_project_create("p")
    with pytest.raises(PublishError, match="CLOUDFLARE_API_TOKEN"):
        Wrangler(runner=runner, account_id="acct-1").pages_project_create("p")
    assert runner.calls == []


def _mock_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    path.chmod(0o755)
    return path


def test_wrangler_on_path_is_used_directly(tmp_path, monkeypatch):
    monkeypatch.delenv("JIMEMO_WRANGLER")
    executable = _mock_executable(tmp_path / "wrangler")
    monkeypatch.setenv("PATH", str(tmp_path))
    w = Wrangler()
    assert w.check_available()
    assert w.executable == str(executable)
    assert json.loads(w.kv_get("ns", "key")) == [
        "kv", "key", "get", "key", "--namespace-id", "ns", "--text", "--remote"]


def test_env_override_wins_over_path_and_survives_cwd_change(tmp_path, monkeypatch):
    _mock_executable(tmp_path / "wrangler")
    executable = _mock_executable(tmp_path / "tools" / "reviewed wrangler")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JIMEMO_WRANGLER", "tools/reviewed wrangler")
    w = Wrangler()
    assert w.executable == str(executable)
    monkeypatch.chdir(tmp_path.parent)
    assert w.check_available()
    assert json.loads(w.kv_get("ns", "key"))[0:4] == ["kv", "key", "get", "key"]


@pytest.mark.parametrize("override", ["missing-wrangler", "", "npx wrangler@4.153.0"])
def test_invalid_override_refuses_without_falling_back(tmp_path, monkeypatch, override):
    _mock_executable(tmp_path / "wrangler")
    _mock_executable(tmp_path / "npx")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("JIMEMO_WRANGLER", override)
    w = Wrangler()
    assert not w.check_available()
    with pytest.raises(PublishError, match="JIMEMO_WRANGLER"):
        w.kv_get("ns", "key")


def test_npx_alone_does_not_make_wrangler_available(tmp_path, monkeypatch):
    _mock_executable(tmp_path / "npx")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("JIMEMO_WRANGLER")
    w = Wrangler()
    assert not w.check_available()
    with pytest.raises(PublishError, match="does not download Wrangler"):
        w.kv_get("ns", "key")


def test_constructor_executable_overrides_environment(tmp_path):
    executable = _mock_executable(tmp_path / "chosen-wrangler")
    w = Wrangler(executable=str(executable))
    assert w.check_available()
    assert w.executable == str(executable)
    assert json.loads(w.kv_get("ns", "key"))[0] == "kv"


def _failing_after_output_wrangler(tmp_path, body, stdout="", code=1):
    """A mock wrangler that appends ``body`` to WRANGLER_OUTPUT_FILE_PATH,
    prints ``stdout``, and then exits ``code`` -- a deploy whose upload
    ran but whose process failed afterwards (jimemo#cf0d)."""
    executable = tmp_path / "mock-wrangler-fails"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "path = os.environ['WRANGLER_OUTPUT_FILE_PATH']\n"
        f"open(path, 'a').write({body!r})\n"
        f"sys.stdout.write({stdout!r})\n"
        "sys.stderr.write('ENOSPC: no space left on device')\n"
        f"sys.exit({code})\n"
    )
    executable.chmod(0o755)
    return Wrangler(executable=str(executable))


def test_pages_deploy_nonzero_exit_after_a_record_is_unverified(tmp_path):
    from jimemo.publish.wrangler import DeployUnverifiedError

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    w = _failing_after_output_wrangler(
        tmp_path, '{"type": "pages-deploy", "deployment_id": "dep-9"}\n')
    with pytest.raises(DeployUnverifiedError) as exc:
        w.pages_deploy("p", deploy)
    assert "dep-9" in str(exc.value)
    assert "exit 1" in str(exc.value)
    assert "ENOSPC" in str(exc.value)


def test_pages_deploy_nonzero_exit_after_completion_line_is_unverified(tmp_path):
    """The record write itself can be what failed (ENOSPC on wrangler's
    append), so wrangler's own completion line is evidence too."""
    from jimemo.publish.wrangler import DeployUnverifiedError

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    w = _failing_after_output_wrangler(
        tmp_path, "",
        stdout="✨ Deployment complete! Take a peek over at https://x.pages.dev\n")
    with pytest.raises(DeployUnverifiedError, match="exit 1"):
        w.pages_deploy("p", deploy)


@pytest.mark.parametrize("body", ["", '{"type": "wrangler-session"}\n'])
def test_pages_deploy_nonzero_exit_with_no_evidence_is_unverified(tmp_path, body):
    """No record and no line either way: wrangler may have died after
    creating the deployment (a failed record write, a kill, a silenced
    log), so the page may be live (jimemo#cf0d review)."""
    from jimemo.publish.wrangler import DeployUnverifiedError

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    w = _failing_after_output_wrangler(tmp_path, body, stdout="Uploading...\n")
    with pytest.raises(DeployUnverifiedError, match="exit 1"):
        w.pages_deploy("p", deploy)


COMMAND_FAILED = ('{"type": "command-failed", "version": 1, '
                  '"message": "Authentication error"}\n')


def test_pages_deploy_failure_before_the_upload_is_a_plain_failure(tmp_path):
    """wrangler's own command-failed record, with no upload-complete
    line, is positive evidence the failure preceded the deployment."""
    from jimemo.publish.wrangler import DeployUnverifiedError

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    w = _failing_after_output_wrangler(
        tmp_path, '{"type": "wrangler-session"}\n' + COMMAND_FAILED,
        stdout="Uploading... (0/1)\n")
    with pytest.raises(PublishError, match="exit 1") as exc:
        w.pages_deploy("p", deploy)
    assert not isinstance(exc.value, DeployUnverifiedError)


def test_pages_deploy_failure_after_the_upload_line_is_unverified(tmp_path):
    """Once wrangler reports the file upload complete it goes on to create
    the deployment, so a command-failed record no longer proves it did not."""
    from jimemo.publish.wrangler import DeployUnverifiedError

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    w = _failing_after_output_wrangler(
        tmp_path, COMMAND_FAILED,
        stdout="✨ Success! Uploaded 1 files (0.42 sec)\n")
    with pytest.raises(DeployUnverifiedError, match="exit 1"):
        w.pages_deploy("p", deploy)


@pytest.mark.parametrize("inherited, seen", [
    (None, "log"), ("error", "log"), ("none", "log"), ("warn", "log"),
    ("info", "log"), ("log", "log"), ("debug", "debug"), ("DEBUG", "DEBUG"),
])
def test_pages_deploy_keeps_wrangler_log_at_least_log(tmp_path, monkeypatch,
                                                      inherited, seen):
    """The classification reads wrangler's log lines, so an inherited
    WRANGLER_LOG below "log" must not silence them."""
    if inherited is None:
        monkeypatch.delenv("WRANGLER_LOG", raising=False)
    else:
        monkeypatch.setenv("WRANGLER_LOG", inherited)
    record = tmp_path / "level.txt"
    executable = tmp_path / "mock-wrangler-level"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        f"open({str(record)!r}, 'w').write(os.environ.get('WRANGLER_LOG', ''))\n"
    )
    executable.chmod(0o755)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    Wrangler(executable=str(executable)).pages_deploy("p", deploy)
    assert record.read_text() == seen


# ---------------------------------------------------------------------------
# KV namespace + TOMBSTONES binding through the REST API (jimemo#1wa9).
# Same curl seam as the Pages project calls: the token is a curl template.
# ---------------------------------------------------------------------------

KV_URL = "https://api.cloudflare.com/client/v4/accounts/acct-1/storage/kv/namespaces"


def _kv_page(namespaces, page=1, total_pages=1):
    return _ok(json.dumps({"success": True, "errors": [], "result": namespaces,
                           "result_info": {"page": page, "total_pages": total_pages}}))


def _configs_json(production, preview):
    return _ok(json.dumps({"success": True, "errors": [], "result": {
        "name": "friend-notes",
        "deployment_configs": {"production": production, "preview": preview}}}))


def test_kv_namespace_find_lists_and_matches_the_exact_title(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token")
    runner = FakeRunner(_kv_page([
        {"id": "other", "title": "friend-notes-tombstones-old"},
        {"id": "ns-1", "title": "friend-notes-tombstones"},
    ]))
    w = Wrangler(runner=runner, account_id="acct-1")

    assert w.kv_namespace_find("friend-notes-tombstones") == "ns-1"

    argv = runner.calls[0]
    assert argv[:2] == ["curl", "-q"]
    assert argv[argv.index("-X") + 1] == "GET"
    assert argv[-1] == f"{KV_URL}?per_page=100&page=1"
    assert argv[argv.index("--expand-header") + 1] == "Authorization: Bearer {{CLOUDFLARE_API_TOKEN}}"
    assert all("super-secret-token" not in a for a in argv)
    assert runner.envs == [None]


def test_kv_namespace_find_pages_through_every_page(monkeypatch):
    w, runner = _wrangler_with_token(
        monkeypatch,
        _kv_page([{"id": "a", "title": "x"}], page=1, total_pages=2),
        _kv_page([{"id": "ns-2", "title": "friend-notes-tombstones"}], page=2, total_pages=2),
    )

    assert w.kv_namespace_find("friend-notes-tombstones") == "ns-2"
    assert [a[-1] for a in runner.calls] == [
        f"{KV_URL}?per_page=100&page=1", f"{KV_URL}?per_page=100&page=2"]


def test_kv_namespace_find_returns_none_when_absent(monkeypatch):
    w, _ = _wrangler_with_token(monkeypatch, _kv_page([{"id": "a", "title": "x"}]))
    assert w.kv_namespace_find("friend-notes-tombstones") is None


def test_kv_namespace_create_posts_the_title_and_returns_the_id(monkeypatch):
    w, runner = _wrangler_with_token(monkeypatch, _ok(json.dumps({
        "success": True, "errors": [],
        "result": {"id": "ns-new", "title": "friend-notes-tombstones"}})))

    assert w.kv_namespace_create("friend-notes-tombstones") == "ns-new"

    argv = runner.calls[0]
    assert argv[argv.index("-X") + 1] == "POST"
    assert json.loads(argv[argv.index("-d") + 1]) == {"title": "friend-notes-tombstones"}
    assert argv[-1] == KV_URL


@pytest.mark.parametrize("stdout,match", [
    ('{"success": false, "errors": [{"code": 10000}], "result": null}', "Workers KV Storage:Edit"),
    ('{"success": true, "result": {"title": "t"}}', "no namespace id"),
])
def test_kv_namespace_create_refuses_failures(monkeypatch, stdout, match):
    w, _ = _wrangler_with_token(monkeypatch, _ok(stdout))
    with pytest.raises(PublishError, match=match):
        w.kv_namespace_create("friend-notes-tombstones")


def test_pages_project_kv_binding_reads_both_environments(monkeypatch):
    w, _ = _wrangler_with_token(monkeypatch, _configs_json(
        {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}},
        {"kv_namespaces": {"tombstones": {"namespace_id": "ns-1"}}}))

    assert w.pages_project_kv_binding("friend-notes", "TOMBSTONES") == {
        "production": "ns-1", "preview": None}


def test_bind_kv_patches_only_kv_namespaces_and_keeps_other_bindings(monkeypatch):
    before = {"fail_open": False, "compatibility_date": "2026-01-01",
              "kv_namespaces": {"OTHER": {"namespace_id": "ns-other"}}}
    after = {"fail_open": False, "compatibility_date": "2026-01-01",
             "kv_namespaces": {"OTHER": {"namespace_id": "ns-other"},
                               "TOMBSTONES": {"namespace_id": "ns-1"}}}
    w, runner = _wrangler_with_token(
        monkeypatch, _configs_json(before, dict(before)), _configs_json(after, after),
        _configs_json(after, after))

    w.pages_project_bind_kv("friend-notes", "TOMBSTONES", "ns-1")

    get_before, patch, get_after = runner.calls
    assert get_before[get_before.index("-X") + 1] == "GET"
    assert patch[patch.index("-X") + 1] == "PATCH"
    assert patch[-1] == PROJECT_URL
    # Only kv_namespaces is sent, and it carries the existing bindings too,
    # so the map survives whether Cloudflare merges or replaces it.
    expected_kv = {"OTHER": {"namespace_id": "ns-other"},
                   "TOMBSTONES": {"namespace_id": "ns-1"}}
    assert json.loads(patch[patch.index("-d") + 1]) == {"deployment_configs": {
        "production": {"kv_namespaces": expected_kv},
        "preview": {"kv_namespaces": expected_kv}}}
    assert get_after[get_after.index("-X") + 1] == "GET"


@pytest.mark.parametrize("after_kv,match", [
    ({}, "TOMBSTONES"),
    ({"tombstones": {"namespace_id": "ns-1"}}, "TOMBSTONES"),
    ({"TOMBSTONES": {"namespace_id": "ns-wrong"}}, "ns-wrong"),
])
def test_bind_kv_read_back_refuses_a_missing_or_misnamed_binding(monkeypatch, after_kv, match):
    from jimemo.publish.wrangler import KVBindingError
    good = {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}}
    w, _ = _wrangler_with_token(
        monkeypatch, _configs_json({}, {}), _configs_json(good, good),
        _configs_json(good, {"kv_namespaces": after_kv}))

    with pytest.raises(KVBindingError, match=match) as exc:
        w.pages_project_bind_kv("friend-notes", "TOMBSTONES", "ns-1")
    assert "preview" in str(exc.value)


def test_bind_kv_refuses_when_the_patch_dropped_an_omitted_key(monkeypatch):
    # If Cloudflare replaced each environment's config with the body
    # instead of merging, keys the body omits (fail_open, other settings)
    # would vanish: the read-back names them and refuses.
    from jimemo.publish.wrangler import KVBindingError
    before = {"fail_open": False, "compatibility_date": "2026-01-01"}
    replaced = {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}}
    w, _ = _wrangler_with_token(
        monkeypatch, _configs_json(before, before), _configs_json(replaced, replaced),
        _configs_json(replaced, replaced))

    with pytest.raises(KVBindingError, match="compatibility_date") as exc:
        w.pages_project_bind_kv("friend-notes", "TOMBSTONES", "ns-1")
    assert "fail_open" in str(exc.value)


def test_bind_kv_tolerates_keys_the_server_adds(monkeypatch):
    bound = {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}}
    added = dict(bound, usage_model="standard")
    w, _ = _wrangler_with_token(
        monkeypatch, _configs_json({}, {}), _configs_json(added, added),
        _configs_json(added, added))

    w.pages_project_bind_kv("friend-notes", "TOMBSTONES", "ns-1")


def test_bind_kv_body_is_the_seam_builder(monkeypatch):
    body = Wrangler.kv_binding_body("TOMBSTONES", "ns-1")
    assert json.loads(body) == {"deployment_configs": {
        "production": {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}},
        "preview": {"kv_namespaces": {"TOMBSTONES": {"namespace_id": "ns-1"}}}}}


def test_mock_wrangler_kv_namespace_and_binding_state():
    m = MockWrangler()
    assert m.kv_namespace_find("p-tombstones") is None
    ns = m.kv_namespace_create("p-tombstones")
    assert m.kv_namespace_find("p-tombstones") == ns
    assert m.pages_project_kv_binding("p", "TOMBSTONES") == {"production": None, "preview": None}
    m.pages_project_bind_kv("p", "TOMBSTONES", ns)
    assert m.pages_project_kv_binding("p", "TOMBSTONES") == {"production": ns, "preview": ns}
