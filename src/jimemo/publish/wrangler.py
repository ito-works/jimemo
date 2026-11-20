"""Wrangler seam: wraps ``wrangler ...`` invocations for the
``cloudflare`` publish backend (``cloudflare_backend.py``).

Auth: wrangler resolves its own Cloudflare API token -- from the
``CLOUDFLARE_API_TOKEN`` environment variable, or its own ``wrangler
login`` credential store. jimemo never reads, stores, forwards, or logs
a token itself: every method here just execs ``wrangler <subcommand> ...``
as a plain subprocess and lets wrangler's own process resolve auth
from its environment. If jimemo doesn't touch the token, it can't leak
the token.

Project creation uses the stable Pages REST API: recent Wrangler releases
redirect ``pages project create`` to Workers. Wrangler also cannot read or
set a Pages project's
``deployment_configs.*.fail_open`` (Cloudflare's default,
true, serves the static files WITHOUT the tombstone middleware whenever
Functions cannot run, so purged hashes come back). Those calls go to
the Pages project REST API through ``curl``, run via the same injectable
runner -- and curl, not jimemo, imports the token from ITS environment
(``--variable %CLOUDFLARE_API_TOKEN`` + ``--expand-header``, curl >= 8.3),
so the token is never in Python, in argv, or in a log; ``-q`` is curl's
first argument so no ``.curlrc`` can switch on ``--trace``. jimemo checks
only that the variable is PRESENT, never its value. Consequence: the
environment token is mandatory for every deploy of this backend --
``wrangler login`` credentials cannot call the project API.

Every wrangler invocation goes through an injectable runner (default:
a real subprocess, list-form argv, NEVER ``shell=True``) so tests can
swap in a fake without ever touching a real wrangler process or the
network. ``MockWrangler`` below goes a step further for backend tests:
it skips subprocess entirely and just records calls against an in-memory
KV dict, so ``cloudflare_backend.py`` tests never need a ``Wrangler`` +
fake-runner pair at all.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..errors import PublishError

Runner = Callable[..., CompletedProcess]

#: Raised (as a PublishError) by any Wrangler method when the ``wrangler``
#: binary itself can't be found. Exported so cloudflare_backend.py can
#: raise the identical message from its own upfront check_available()
#: gate, rather than waiting for a subprocess to fail.
NO_WRANGLER_MESSAGE = (
    "cloudflare backend needs Wrangler on PATH or JIMEMO_WRANGLER set to "
    "an executable path; install a chosen Wrangler version or provide a "
    "wrapper (jimemo does not download Wrangler)"
)

CF_API = "https://api.cloudflare.com/client/v4"

#: curl gained ``--variable`` / ``--expand-header`` in 8.3.0 (2023-09).
MIN_CURL = (8, 3, 0)

TOKEN_ENV_REQUIRED_MESSAGE = (
    "the Pages project API needs CLOUDFLARE_API_TOKEN in the environment "
    "(wrangler login credentials cannot read the Pages project config); "
    "export it and retry"
)

CURL_TOO_OLD_MESSAGE = (
    "cloudflare backend needs curl >= 8.3 (found {found}) to read or set "
    "fail_open on the Pages project; upgrade curl and retry"
)

#: Cloudflare API error codes for a missing/invalid token (10000) or a
#: token without the needed scope (9106): the one refusal where the fix
#: is the token, so the message says so. Only the code is inspected.
AUTH_ERROR_CODES = frozenset({9106, 10000})

#: PATCH body that makes a Functions outage an outage, not a leak.
FAIL_CLOSED_BODY = json.dumps(
    {"deployment_configs": {"production": {"fail_open": False},
                            "preview": {"fail_open": False}}},
    separators=(",", ":"),
)


def _run(argv: List[str], env: Optional[Dict[str, str]] = None,
         *, cwd: Optional[Path] = None) -> CompletedProcess:
    """Default runner: a real subprocess, list-form argv only, never
    shell=True. ``env=None`` (the default) means inherit the parent
    process's environment unchanged -- Wrangler only passes a non-None
    env when an account_id is configured, to layer
    CLOUDFLARE_ACCOUNT_ID on top of that inherited environment (see
    Wrangler._invoke)."""
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd)


#: Post-deploy confirmation (pages_project_require_functions): how many
#: times the project is read, and the pause between reads, while waiting
#: for the deployment this upload created to become the production one.
VERIFY_ATTEMPTS = 10
VERIFY_DELAY_S = 3.0


class DeployUnverifiedError(PublishError):
    """The upload ran, but the new production deployment could not be
    confirmed to carry the Functions bundle. Unlike every other
    PublishError from a deploy, the uploaded tree may be live."""


def _deployment_id_from_output(path: Path) -> Optional[str]:
    """The ``deployment_id`` of the last ``pages-deploy`` entry in the
    newline-delimited JSON file wrangler appends to when
    ``WRANGLER_OUTPUT_FILE_PATH`` is set, or None when there is no such
    entry (file missing, unreadable, not JSON, or no usable id)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    found = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "pages-deploy":
            dep_id = entry.get("deployment_id")
            if isinstance(dep_id, str) and dep_id:
                found = dep_id
    return found


def _wrangler_reported_failure(path: Path) -> bool:
    """True if wrangler's own error handler appended a ``command-failed``
    entry to the WRANGLER_OUTPUT_FILE_PATH file. Its absence (a killed
    process, or a record write that itself failed) proves nothing."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "command-failed":
            return True
    return False


#: The line wrangler prints once the Pages deployment exists, before it
#: appends its ``pages-deploy`` record to WRANGLER_OUTPUT_FILE_PATH.
DEPLOY_COMPLETE_MARKER = "Deployment complete"

#: The line wrangler prints when the file upload finishes, before it
#: creates the deployment. A failure reported without it came earlier.
UPLOAD_COMPLETE_MARKER = "Success! Uploaded"

#: WRANGLER_LOG levels at which wrangler still prints the two lines above.
_LOUD_ENOUGH = ("log", "debug")


def _failure_text(action: str, result: CompletedProcess) -> str:
    stderr = (result.stderr or "").strip()
    return f"wrangler {action} failed (exit {result.returncode}): {stderr}"


class PagesProjectExistsError(PublishError):
    """The Pages create endpoint reports duplicate project code 8000002."""


class KVBindingError(PublishError):
    """The binding PATCH answered success, but reading the project back
    does not show the binding under the exact name and namespace id on
    both environments, or shows another deployment_configs setting
    dropped or changed. A refusal: the project must not be deployed as
    if purge worked."""


#: KV namespaces requested per page when listing (Cloudflare's maximum).
KV_LIST_PER_PAGE = 100
#: Upper bound on pages read while listing, so a malformed result_info
#: can never loop forever (100 x 100 namespaces is far past any account).
KV_LIST_MAX_PAGES = 100


class Wrangler:
    """Thin argv-building wrapper around ``wrangler``, one method per
    subcommand the cloudflare backend needs: listing Cloudflare Pages
    projects for setup, deploying a directory to Cloudflare Pages,
    and reading/writing/listing the tombstone KV namespace. Plus two
    Pages project-config calls (fail_open read/set) and project creation,
    made through ``curl`` with the same runner -- see the
    module docstring for why curl and not Python holds the token.

    Account scoping: CloudflareConfig carries an ``account_id``. None of
    these subcommands take an ``--account-id`` flag (checked against
    ``wrangler pages deploy --help`` / ``wrangler kv key {put,get,
    list} --help``), so it's threaded through via the
    ``CLOUDFLARE_ACCOUNT_ID`` environment variable instead -- the same
    variable wrangler already reads directly from its own environment,
    which is what makes it work when a friend's API token is scoped without
    ``User:Memberships:Read`` and wrangler's own account-discovery call
    would otherwise fail or guess wrong. When ``account_id`` is set,
    every subprocess call this class makes runs with
    ``CLOUDFLARE_ACCOUNT_ID`` layered on top of the inherited environment
    (see ``_invoke``). It is never used to touch the Cloudflare API
    token itself, which stays wherever it already was -- inherited from
    the parent environment, untouched and unread by this module.
    """

    def __init__(
        self,
        runner: Runner = _run,
        executable: Optional[str] = None,
        account_id: Optional[str] = None,
        curl: str = "curl",
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._run = runner
        self._sleep = sleep
        # Resolve once, before deploy can change subprocess cwd. A wrapper
        # path is one argv element, never a shell command or an npm package.
        selected = (executable if executable is not None
                    else os.environ.get("JIMEMO_WRANGLER", "wrangler"))
        resolved = shutil.which(selected) or selected
        self.executable = (str(Path(resolved).absolute())
                           if os.path.dirname(resolved) else resolved)
        self._curl = curl
        #: Non-secret Cloudflare account id. Public and mutable (not
        #: constructor-only) because setup.py's `jimemo publish setup`
        #: wizard only learns it partway through the wizard -- after
        #: this Wrangler is already constructed -- and sets it here once
        #: known, before the first real call (see setup.py's run_setup).
        self.account_id = account_id

    def check_available(self) -> bool:
        """True if the selected executable is available.

        JIMEMO_WRANGLER overrides PATH discovery; no npx fallback is used.

        A cheap presence check -- it does not invoke wrangler itself, so
        it needs no network access and no auth. Callers that skip this
        and call a method anyway still get NO_WRANGLER_MESSAGE from
        _invoke if the selected executable cannot run; this just lets a caller
        (cloudflare_backend.py, or the setup wizard) fail fast with
        the same clear message before doing any other work.
        """
        return shutil.which(self.executable) is not None

    def _invoke(self, argv: List[str], action: str,
                *, cwd: Optional[Path] = None,
                extra_env: Optional[Dict[str, str]] = None,
                check: bool = True) -> CompletedProcess:
        extra = dict(extra_env or {})
        if self.account_id:
            extra["CLOUDFLARE_ACCOUNT_ID"] = self.account_id
        # None means "inherit unchanged"; otherwise the inherited
        # environment is copied whole with these non-secret names layered
        # on top. No value in it is read here.
        env = dict(os.environ, **extra) if extra else None
        try:
            command = [self.executable] + argv
            result = (self._run(command, env, cwd=cwd) if cwd is not None
                      else self._run(command, env))
        except OSError:
            raise PublishError(NO_WRANGLER_MESSAGE)
        if check and result.returncode != 0:
            raise PublishError(_failure_text(action, result))
        return result

    def pages_deploy(
        self, project: str, directory: Union[str, Path], branch: str = "main"
    ) -> CompletedProcess:
        """Deploy every file under ``directory`` as the named project's
        ``branch`` deployment. ``branch`` defaults to "main" (the usual
        Cloudflare Pages production branch) -- explicit, not left to
        wrangler's own git-branch inference, since ``directory`` is
        jimemo's own local state directory rather than a git checkout.

        The returned CompletedProcess carries one extra attribute,
        ``deployment_id``: the id of the deployment this upload created,
        as wrangler itself reports it (it appends a ``pages-deploy``
        record to the file ``WRANGLER_OUTPUT_FILE_PATH`` names), or None
        when wrangler wrote no such record. The file lives in its own
        throwaway directory, outside the deployed tree.

        A nonzero exit is classified by what wrangler left behind
        (jimemo#cf0d). It is a plain PublishError, read as a failure
        before the upload, only on positive evidence: wrangler's own
        ``command-failed`` record, and no "Success! Uploaded" line, which
        wrangler prints after the file upload and before it creates the
        deployment. Auth, network, a missing project and a failed file
        upload all end that way. Every other nonzero exit raises
        DeployUnverifiedError, because the page may be live: a
        ``pages-deploy`` record or a "Deployment complete" line proves the
        deployment exists, and a run with no ``command-failed`` record (a
        failed write of the record file, a killed process) or with the
        upload line printed cannot rule it out. WRANGLER_LOG is raised to
        "log" for this call when the inherited level would hide those
        lines.
        """
        # Wrangler discovers functions/ relative to cwd, not the static
        # output argument. Resolve first so relative paths survive the cwd change.
        directory = Path(directory).resolve()
        with tempfile.TemporaryDirectory(prefix="jimemo-wrangler-out-") as out_dir:
            out_file = Path(out_dir) / "output.ndjson"
            extra_env = {"WRANGLER_OUTPUT_FILE_PATH": str(out_file)}
            if os.environ.get("WRANGLER_LOG", "").lower() not in _LOUD_ENOUGH:
                extra_env["WRANGLER_LOG"] = "log"
            result = self._invoke(
                ["pages", "deploy", str(directory), "--project-name", project,
                 "--branch", branch],
                "pages deploy",
                cwd=directory,
                extra_env=extra_env,
                check=False,
            )
            result.deployment_id = _deployment_id_from_output(out_file)
            reported = _wrangler_reported_failure(out_file)
        if result.returncode != 0:
            failure = _failure_text("pages deploy", result)
            output = f"{result.stdout or ''}\n{result.stderr or ''}"
            if result.deployment_id or DEPLOY_COMPLETE_MARKER in output:
                created = (f"deployment {result.deployment_id}"
                           if result.deployment_id else "a deployment")
                raise DeployUnverifiedError(
                    f"{failure}; wrangler had already created {created}, "
                    "so the upload ran"
                )
            if reported and UPLOAD_COMPLETE_MARKER not in output:
                raise PublishError(failure)
            raise DeployUnverifiedError(
                f"{failure}; wrangler did not show that it stopped before "
                "creating the deployment, so the upload may have run"
            )
        return result

    def pages_project_names(self) -> List[str]:
        """Return the names of the account's Cloudflare Pages projects.

        Setup uses this before its first deploy so it can create a
        missing project explicitly. Current Wrangler prompts to create a
        missing Pages project during ``pages deploy --project-name``; that
        prompt fails under jimemo's captured, non-interactive subprocess.
        """
        result = self._invoke(
            ["pages", "project", "list", "--json"],
            "pages project list",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"wrangler pages project list: could not parse JSON output: {e}"
            )

        if isinstance(payload, list):
            projects = payload
        elif isinstance(payload, dict):
            projects = (
                payload.get("result")
                or payload.get("projects")
                or payload.get("items")
            )
        else:
            projects = None
        if not isinstance(projects, list):
            raise PublishError(
                "wrangler pages project list: expected a JSON list of projects"
            )

        return [
            item["name"]
            for item in projects
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]

    @staticmethod
    def pages_project_create_body(project: str, branch: str = "main") -> str:
        return json.dumps({"name": project, "production_branch": branch},
                          separators=(",", ":"))

    def pages_project_create(
        self, project: str, branch: str = "main"
    ) -> Dict[str, Any]:
        """Create classic Pages via REST, independent of Wrangler's CLI routing."""
        return self._cf_api("POST", "", self.pages_project_create_body(project, branch))

    def curl_version(self) -> Optional[Tuple[int, int, int]]:
        """(major, minor, patch) of the curl on PATH, or None when curl is
        missing, cannot be started (any OSError: not found, permission
        denied, exec-format error), or its ``--version`` line is
        unparseable."""
        try:
            result = self._run([self._curl, "-q", "--version"], None)
        except OSError:
            return None
        if result.returncode != 0:
            return None
        first = (result.stdout or "").splitlines()[:1]
        m = re.match(r"curl (\d+)\.(\d+)\.(\d+)", first[0]) if first else None
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    def _cf_api_argv(self, method: str, project: str,
                     body: Optional[str] = None) -> List[str]:
        suffix = f"/{project}" if project else ""
        return self._cf_account_argv(method, f"pages/projects{suffix}", body)

    def _cf_account_argv(self, method: str, path: str,
                         body: Optional[str] = None) -> List[str]:
        """curl argv for ``{CF_API}/accounts/<account_id>/<path>``. The
        Authorization header is a curl template: curl expands the token
        from its own environment, so it is never in this list."""
        argv = [
            self._curl, "-q", "-sS", "--max-time", "30",
            "--variable", "%CLOUDFLARE_API_TOKEN",
            "--expand-header", "Authorization: Bearer {{CLOUDFLARE_API_TOKEN}}",
            "-X", method, "-H", "Content-Type: application/json",
        ]
        if body is not None:
            argv += ["-d", body]
        argv.append(f"{CF_API}/accounts/{self.account_id}/{path}")
        return argv

    def _cf_api(self, method: str, project: str,
                body: Optional[str] = None) -> Dict[str, Any]:
        """GET/PATCH a Pages project or POST to the project collection via curl.
        Returns the API ``result`` object. Every failure mode is a PublishError (a refusal upstream):
        no account id, no token in the environment (presence only -- the
        value is never read), curl missing, nonzero exit, non-JSON, or an
        envelope whose ``success`` is not the literal boolean true."""
        suffix = f"/{project}" if project else ""
        return self._cf_request(
            method, f"pages/projects{suffix}", body,
            what=f"Pages project {project!r}",
            api=f"Pages project API {method} {project!r}",
            scope="Pages:Edit", result_type=dict,
            exists_code=8000002 if (method == "POST" and not project) else None,
        )

    def _cf_request(self, method: str, path: str, body: Optional[str],
                    *, what: str, api: str, scope: str, result_type: type,
                    exists_code: Optional[int] = None) -> Any:
        """One account-scoped Cloudflare API call through curl; returns the
        envelope's ``result`` when it is a ``result_type``. ``what`` and
        ``api`` name the call in errors; ``scope`` is the token scope the
        auth hint names."""
        return self._cf_envelope(method, path, body, what=what, api=api,
                                 scope=scope, result_type=result_type,
                                 exists_code=exists_code)["result"]

    def _cf_envelope(self, method: str, path: str, body: Optional[str],
                     *, what: str, api: str, scope: str, result_type: type,
                     exists_code: Optional[int] = None) -> Dict[str, Any]:
        if not self.account_id:
            raise PublishError(
                "cloudflare backend needs account_id to read the Pages project "
                "config (set [publish.cloudflare].account_id in "
                "~/.jimemo/config.toml or re-run `jimemo publish setup`)"
            )
        if "CLOUDFLARE_API_TOKEN" not in os.environ:
            raise PublishError(TOKEN_ENV_REQUIRED_MESSAGE)
        try:
            result = self._run(self._cf_account_argv(method, path, body), None)
        except OSError as e:
            # Any process-start failure (not found, permission denied,
            # exec-format error) is a refusal, never a traceback. str(e)
            # carries only errno text and the executable name -- never
            # argv or the environment.
            raise PublishError(
                f"could not run curl for the Pages project API "
                f"({e.__class__.__name__}: {e}); "
                f"{CURL_TOO_OLD_MESSAGE.format(found='none usable on PATH')}"
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise PublishError(
                f"curl {method} {what} failed "
                f"(exit {result.returncode}): {stderr}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"{api}: could not parse JSON output: {e}"
            )
        if (not isinstance(payload, dict) or payload.get("success") is not True
                or not isinstance(payload.get("result"), result_type)):
            errors = payload.get("errors") if isinstance(payload, dict) else payload
            hint = ""
            if isinstance(errors, list) and any(
                isinstance(e, dict) and e.get("code") in AUTH_ERROR_CODES
                for e in errors
            ):
                hint = f"; check that CLOUDFLARE_API_TOKEN is valid and scoped {scope}"
            error_type = PublishError
            if exists_code is not None and isinstance(errors, list) and any(
                isinstance(e, dict) and e.get("code") == exists_code for e in errors
            ):
                error_type = PagesProjectExistsError
            raise error_type(
                f"{api} did not answer "
                f"success=true: {errors!r}{hint}"
            )
        return payload

    @staticmethod
    def _open_flags(result: Dict[str, Any]) -> Dict[str, bool]:
        """Strict: an environment is CLOSED only when fail_open is the
        literal JSON boolean false (``is False``; 0 == False in Python).
        Missing keys, null, numbers, strings all read as open."""
        dc = result.get("deployment_configs")
        if not isinstance(dc, dict):
            dc = {}
        flags = {}
        for env in ("production", "preview"):
            cfg = dc.get(env)
            flags[env] = not (isinstance(cfg, dict) and cfg.get("fail_open") is False)
        return flags

    def pages_project_fail_open(self, project: str) -> Dict[str, bool]:
        """``{"production": is_open, "preview": is_open}`` for the project."""
        return self._open_flags(self._cf_api("GET", project))

    def _production_deployment(self, project: str) -> Dict[str, Any]:
        """The project's ``canonical_deployment`` (its current production
        deployment), or ``{}`` when the project has none."""
        deployment = self._cf_api("GET", project).get("canonical_deployment")
        return deployment if isinstance(deployment, dict) else {}

    def pages_project_require_functions(
        self, project: str, deployment_id: Optional[str]
    ) -> None:
        """Refuse unless the deployment THIS upload created is the
        production deployment and reports a Functions bundle.

        ``deployment_id`` is ``pages_deploy(...).deployment_id``. The
        project's production deployment must carry exactly that id before
        its ``uses_functions`` is believed: an earlier deployment, or
        another machine's concurrent one, reporting true proves nothing
        about this upload. The project is re-read up to VERIFY_ATTEMPTS
        times while production is some other deployment. Raises
        DeployUnverifiedError when wrangler reported no id, when this
        deployment does not report uses_functions=true, or when it is not
        the production deployment by the last read (not yet active, or
        already replaced by a later upload).

        This checks deployment metadata, not the live TOMBSTONES binding.
        """
        if not (isinstance(deployment_id, str) and deployment_id):
            raise DeployUnverifiedError(
                f"Pages project {project!r}: wrangler did not report the id of "
                "the deployment it created, so uses_functions=true cannot be "
                "confirmed for this upload; tombstone/purge protection may be "
                "absent. Check the project's deployments and retry "
                "`jimemo publish setup --assets-only`"
            )
        seen = "the project reports no production deployment"
        for attempt in range(VERIFY_ATTEMPTS):
            if attempt:
                self._sleep(VERIFY_DELAY_S)
            deployment = self._production_deployment(project)
            dep_id = deployment.get("id")
            if dep_id != deployment_id:
                if isinstance(dep_id, str) and dep_id:
                    seen = f"production is deployment {dep_id}"
                continue
            if deployment.get("uses_functions") is True:
                return
            raise DeployUnverifiedError(
                f"Pages project {project!r}: production deployment {dep_id} does "
                "not confirm uses_functions=true; tombstone/purge protection may "
                "be absent. Check the deployment's Functions bundle and retry "
                "`jimemo publish setup --assets-only`"
            )
        raise DeployUnverifiedError(
            f"Pages project {project!r}: could not confirm uses_functions=true "
            f"for this upload (deployment {deployment_id}): after "
            f"{VERIFY_ATTEMPTS} reads {seen}. Tombstone/purge protection may be "
            "absent; check the project's deployments and retry "
            "`jimemo publish setup --assets-only`"
        )

    def pages_project_set_fail_closed(self, project: str) -> None:
        """PATCH fail_open=false on both environments, then re-read and
        verify; a PATCH that does not stick is a PublishError."""
        self._cf_api("PATCH", project, FAIL_CLOSED_BODY)
        flags = self.pages_project_fail_open(project)
        if flags["production"] or flags["preview"]:
            raise PublishError(
                f"Pages project {project!r} is still fail-open after the PATCH "
                f"(production={'open' if flags['production'] else 'closed'}, "
                f"preview={'open' if flags['preview'] else 'closed'})"
            )

    def kv_namespace_find(self, title: str) -> Optional[str]:
        """The id of the account's KV namespace titled exactly ``title``,
        or None. Reads every page of the namespace list, so an existing
        namespace is found (and reused) rather than duplicated."""
        for page in range(1, KV_LIST_MAX_PAGES + 1):
            envelope = self._cf_envelope(
                "GET", f"storage/kv/namespaces?per_page={KV_LIST_PER_PAGE}&page={page}",
                None, what="KV namespace list", api="KV namespace API GET (list)",
                scope="Workers KV Storage:Edit", result_type=list,
            )
            for item in envelope["result"]:
                if (isinstance(item, dict) and item.get("title") == title
                        and isinstance(item.get("id"), str) and item["id"]):
                    return item["id"]
            info = envelope.get("result_info")
            total = info.get("total_pages") if isinstance(info, dict) else None
            if not (isinstance(total, int) and page < total):
                return None
        return None

    @staticmethod
    def kv_namespace_create_body(title: str) -> str:
        return json.dumps({"title": title}, separators=(",", ":"))

    def kv_namespace_create(self, title: str) -> str:
        """Create a KV namespace titled ``title``; returns its id."""
        result = self._cf_request(
            "POST", "storage/kv/namespaces", self.kv_namespace_create_body(title),
            what=f"KV namespace create {title!r}",
            api=f"KV namespace API POST {title!r}",
            scope="Workers KV Storage:Edit", result_type=dict,
        )
        ns_id = result.get("id")
        if not (isinstance(ns_id, str) and ns_id):
            raise PublishError(
                f"KV namespace API POST {title!r} answered success but carried "
                "no namespace id"
            )
        return ns_id

    @staticmethod
    def _env_configs(result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        dc = result.get("deployment_configs")
        dc = dc if isinstance(dc, dict) else {}
        return {env: (dc.get(env) if isinstance(dc.get(env), dict) else {})
                for env in ("production", "preview")}

    @staticmethod
    def _kv_map(cfg: Dict[str, Any]) -> Dict[str, Any]:
        kv = cfg.get("kv_namespaces")
        return kv if isinstance(kv, dict) else {}

    @classmethod
    def _bound_id(cls, cfg: Dict[str, Any], binding: str) -> Optional[str]:
        entry = cls._kv_map(cfg).get(binding)
        ns_id = entry.get("namespace_id") if isinstance(entry, dict) else None
        return ns_id if isinstance(ns_id, str) and ns_id else None

    def pages_project_kv_binding(
        self, project: str, binding: str
    ) -> Dict[str, Optional[str]]:
        """``{"production": id_or_None, "preview": id_or_None}``: the KV
        namespace id bound under exactly ``binding`` on each environment."""
        configs = self._env_configs(self._cf_api("GET", project))
        return {env: self._bound_id(cfg, binding) for env, cfg in configs.items()}

    @staticmethod
    def kv_binding_body(
        binding: str, namespace_id: str,
        existing: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        """PATCH body binding ``namespace_id`` as ``binding`` on both
        environments. It sends ``kv_namespaces`` and nothing else, and
        repeats each environment's ``existing`` bindings, so other
        bindings survive whether the API merges or replaces that map."""
        existing = existing or {}
        configs = {}
        for env in ("production", "preview"):
            kv = dict(existing.get(env) or {})
            kv[binding] = {"namespace_id": namespace_id}
            configs[env] = {"kv_namespaces": kv}
        return json.dumps({"deployment_configs": configs}, separators=(",", ":"))

    def pages_project_bind_kv(
        self, project: str, binding: str, namespace_id: str
    ) -> None:
        """Bind ``namespace_id`` as ``binding`` on production and preview,
        then read the project back. Raises KVBindingError unless the
        read-back shows exactly that binding on both environments and
        every other deployment_configs setting present before the PATCH
        is still present and unchanged (keys the API adds are allowed)."""
        before = self._env_configs(self._cf_api("GET", project))
        existing = {env: self._kv_map(cfg) for env, cfg in before.items()}
        self._cf_api("PATCH", project,
                     self.kv_binding_body(binding, namespace_id, existing))
        after = self._env_configs(self._cf_api("GET", project))
        problems = []
        for env in ("production", "preview"):
            bound = self._bound_id(after[env], binding)
            if bound != namespace_id:
                problems.append(
                    f"{env}: {binding} is "
                    + (f"bound to {bound}" if bound else "not bound")
                    + f" (expected {namespace_id})"
                )
            changed = sorted(
                key for key, value in before[env].items()
                if key != "kv_namespaces" and after[env].get(key) != value
            )
            changed += sorted(
                f"kv_namespaces.{name}"
                for name, value in existing[env].items()
                if name != binding and self._kv_map(after[env]).get(name) != value
            )
            if changed:
                problems.append(f"{env}: the PATCH dropped or changed "
                                + ", ".join(changed))
        if problems:
            raise KVBindingError(
                f"Pages project {project!r}: after binding {binding} the "
                "project reads back wrong -- " + "; ".join(problems)
                + ". Purge would not work; fix the project's KV namespace "
                "bindings in the dashboard (Settings -> Functions) and re-run "
                "`jimemo publish setup`"
            )

    def kv_put(self, namespace_id: str, key: str, value: str) -> None:
        """Write ``value`` under ``key`` in the KV namespace. ``--remote``
        is explicit so this always hits the real (production) namespace,
        never a `wrangler dev`-style local-persisted store."""
        self._invoke(
            ["kv", "key", "put", key, value, "--namespace-id", namespace_id,
             "--remote"],
            "kv key put",
        )

    def kv_get(self, namespace_id: str, key: str) -> str:
        """Read the value stored under ``key``. Raises PublishError (via
        _invoke) if wrangler exits non-zero for any reason, including a
        missing key -- callers that need "does this key exist" semantics
        should consult kv_list first (cloudflare_backend.list() does
        exactly that, only calling kv_get for keys kv_list already
        confirmed are present)."""
        result = self._invoke(
            ["kv", "key", "get", key, "--namespace-id", namespace_id,
             "--text", "--remote"],
            "kv key get",
        )
        return result.stdout.strip()

    def kv_list(self, namespace_id: str) -> List[Dict[str, Any]]:
        """Return every key in the namespace as wrangler's own JSON shape
        (a list of ``{"name": ..., "expiration": ..., "metadata": ...}``
        dicts; only "name" is guaranteed present)."""
        result = self._invoke(
            ["kv", "key", "list", "--namespace-id", namespace_id, "--remote"],
            "kv key list",
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"wrangler kv key list: could not parse JSON output: {e}"
            )


class MockWrangler:
    """Test double standing in for Wrangler: no subprocess, no npx, no
    network. Records every call (as a tuple, call name first) in
    ``.calls`` and serves kv_put/kv_get/kv_list from an in-memory dict,
    so a purge followed by a list/gc round-trips realistically without
    a real KV namespace. ``fail_open`` mirrors the project's flags: a
    fresh mock is an already-provisioned (closed) project;
    ``pages_project_create`` opens both, like Cloudflare;
    ``fail_open=True`` starts open for refusal tests.
    """

    def __init__(
        self,
        deploy_stdout: str = "Deployment complete!\n",
        projects: Optional[List[str]] = None,
        fail_open: bool = False,
        executable: str = "wrangler",
        kv_namespaces: Optional[Dict[str, str]] = None,
        bindings: Optional[Dict[str, str]] = None,
    ):
        self.executable = executable
        self._deploys = 0
        self.calls: List[tuple] = []
        self._kv: Dict[str, str] = {}
        self._deploy_stdout = deploy_stdout
        self._projects = set(projects or [])
        self.fail_open: Dict[str, bool] = {
            "production": bool(fail_open), "preview": bool(fail_open)}
        #: title -> id of the account's KV namespaces.
        self.kv_namespaces: Dict[str, str] = dict(kv_namespaces or {})
        #: env -> {binding name: namespace id} on the Pages project.
        self.kv_bindings: Dict[str, Dict[str, str]] = {
            env: dict(bindings or {}) for env in ("production", "preview")}

    def check_available(self) -> bool:
        self.calls.append(("check_available",))
        return True

    def pages_deploy(
        self, project: str, directory: Union[str, Path], branch: str = "main"
    ) -> CompletedProcess:
        self.calls.append(("pages_deploy", project, str(directory), branch))
        self._deploys += 1
        result = CompletedProcess([], 0, stdout=self._deploy_stdout, stderr="")
        result.deployment_id = f"deploy-{self._deploys}"
        return result

    def pages_project_names(self) -> List[str]:
        self.calls.append(("pages_project_names",))
        return sorted(self._projects)

    def pages_project_create(
        self, project: str, branch: str = "main"
    ) -> Dict[str, Any]:
        self.calls.append(("pages_project_create", project, branch))
        self._projects.add(project)
        # Cloudflare's default for a new project: both environments open.
        self.fail_open = {"production": True, "preview": True}
        return {"name": project, "production_branch": branch}

    def curl_version(self) -> Optional[Tuple[int, int, int]]:
        self.calls.append(("curl_version",))
        return (8, 7, 1)

    def pages_project_fail_open(self, project: str) -> Dict[str, bool]:
        self.calls.append(("pages_project_fail_open", project))
        return dict(self.fail_open)

    def pages_project_require_functions(
        self, project: str, deployment_id: Optional[str]
    ) -> None:
        self.calls.append(("pages_project_require_functions", project))

    def pages_project_set_fail_closed(self, project: str) -> None:
        self.calls.append(("pages_project_set_fail_closed", project))
        self.fail_open = {"production": False, "preview": False}

    def kv_namespace_find(self, title: str) -> Optional[str]:
        self.calls.append(("kv_namespace_find", title))
        return self.kv_namespaces.get(title)

    def kv_namespace_create(self, title: str) -> str:
        self.calls.append(("kv_namespace_create", title))
        ns_id = f"ns-{len(self.kv_namespaces) + 1}"
        self.kv_namespaces[title] = ns_id
        return ns_id

    def pages_project_kv_binding(
        self, project: str, binding: str
    ) -> Dict[str, Optional[str]]:
        self.calls.append(("pages_project_kv_binding", project, binding))
        return {env: self.kv_bindings[env].get(binding)
                for env in ("production", "preview")}

    def pages_project_bind_kv(
        self, project: str, binding: str, namespace_id: str
    ) -> None:
        self.calls.append(("pages_project_bind_kv", project, binding, namespace_id))
        for env in ("production", "preview"):
            self.kv_bindings[env][binding] = namespace_id

    def kv_put(self, namespace_id: str, key: str, value: str) -> None:
        self.calls.append(("kv_put", namespace_id, key, value))
        self._kv[key] = value

    def kv_get(self, namespace_id: str, key: str) -> str:
        self.calls.append(("kv_get", namespace_id, key))
        return self._kv.get(key, "")

    def kv_list(self, namespace_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("kv_list", namespace_id))
        return [{"name": name} for name in sorted(self._kv)]
