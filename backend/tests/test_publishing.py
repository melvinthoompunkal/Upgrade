"""F13 consumes real F7 artifacts and exercises authenticated, durable publication."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from adaptation_fixtures import context, execute
from publishing_fixtures import PublishingProvider
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate, token_data
from test_change_review import attach
from test_projects import independent as independent

from app.api.artifacts import digest
from app.api.change_review import FileMapping, parse_file
from app.api.storage import RunStore
from app.auth.github import GitHubTokens, GitHubUser
from app.auth.store import SESSION_COOKIE
from app.config import Settings
from app.deployment import execution_lock_path
from app.execution import ProcessLock
from app.jobs.retention import Retention
from app.publishing.patch import PublishPatch
from app.publishing.store import PublicationStore


@pytest.fixture
def publication(independent, tmp_path, monkeypatch, authenticated_app):
    inputs = context(independent, tmp_path, monkeypatch)
    report, path, _, _ = execute(inputs, tmp_path, monkeypatch)
    client, account = authenticated_app
    session, _ = login(client)
    owner = session["user"]["id"]
    url = attach(client, report, path, owner) + "/pull-request"
    provider = PublishingProvider(client, report, path.parent, owner, monkeypatch)
    view = client.get(url)
    assert view.status_code == 200, view.text
    view = view.json()
    body = {k: view[k] for k in ("repository_id", "base_branch", "patch_sha256", "notices_sha256")}
    body["reviewed"] = True
    return client, session, url, body, provider, path, account


def test_exact_patch_notices_and_duplicate_keys_survive_restart(publication):
    client, session, url, body, provider, path, _ = publication
    before = client.get(url).json()
    assert before["enabled"] and not provider.calls
    response = mutate(client, url, session, json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["state"] == "published" and receipt["pr_number"] == 23
    assert receipt["patch_sha256"] == body["patch_sha256"]
    assert receipt["notices_sha256"] == body["notices_sha256"]
    original = provider.flatten(provider.base_tree)
    published = provider.flatten(receipt["tree_sha"])
    mappings = json.loads((path.parent / "rounds/round-0-mapping.json").read_bytes())
    actual = {p for p in published if published[p] != original.get(p)}
    assert actual == {"library/" + m["path"] for m in mappings}
    for mapping in mappings:
        assert digest(published["library/" + mapping["path"]]) == mapping["after_sha256"]
    assert (path.parent / "notices/source-NOTICE.txt").read_text().splitlines()[0] in published[
        "library/src/sequence.py"
    ].decode()
    assert provider.refs["main"] == provider.base and len(provider.pulls) == 1
    # Reopen the actual SQLite file, then replay with the same and a different UI key.
    client.app.state.runs = RunStore(client.app.state.runs.path, client.app.state.settings)
    assert mutate(client, url, session, json=body).json() == receipt
    assert (
        client.post(
            url,
            json=body,
            headers={
                "Origin": "http://127.0.0.1:5173",
                "X-CSRF-Token": session["csrf_token"],
                "Idempotency-Key": "different-retry-key",
            },
        ).json()
        == receipt
    )
    assert len(provider.pulls) == 1 and len(provider.refs) == 2
    assert client.get(url).json()["publication"] == receipt
    serialized = json.dumps(receipt)
    assert "fixture-app-jwt" not in serialized and "private-access-fixture" not in serialized


@pytest.mark.parametrize(
    "stage", ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
)
def test_uncertain_provider_writes_reconcile_without_duplicate_pr(publication, stage):
    client, session, url, body, provider, _, _ = publication
    provider.fail = stage
    failed = mutate(client, url, session, json=body)
    assert failed.status_code == 409 and "fixture secret" not in failed.text
    assert client.get(url).json()["publication"]["state"] == "retryable"
    result = mutate(client, url, session, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "published"
    assert len(provider.pulls) == 1 and len(provider.refs) == 2


@pytest.mark.parametrize("denial", ["user", "app", "archived", "private", "wrong_repo"])
def test_fresh_user_and_app_rights_before_any_write(publication, denial):
    client, session, url, body, provider, _, _ = publication
    if denial == "user":
        provider.push = False
    elif denial == "app":
        provider.permissions = "read"
    elif denial in {"archived", "private"}:
        provider.repo[denial] = True
    else:
        body["repository_id"] = 102
    response = mutate(client, url, session, json=body)
    assert response.status_code in {403, 404, 409}, response.text
    assert not any(r.method == "POST" for r in provider.calls)


def test_moved_base_and_move_during_pr_are_durable(publication):
    client, session, url, body, provider, _, _ = publication
    provider.refs["main"] = "f" * 40
    response = mutate(client, url, session, json=body)
    assert response.status_code == 409 and "base branch moved" in response.text
    assert not any(r.method == "POST" for r in provider.calls)
    assert client.get(url).json()["publication"]["state"] == "stale"
    provider.refs["main"] = provider.base
    assert mutate(client, url, session, json=body).status_code == 409


def test_concurrent_base_move_retains_actual_pr(publication):
    client, session, url, body, provider, _, _ = publication
    provider.move_on_pr = True
    response = mutate(client, url, session, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "stale" and response.json()["pr_url"]
    assert mutate(client, url, session, json=body).status_code == 409
    assert len(provider.pulls) == 1


def test_lost_pr_response_and_deleted_branch_do_not_create_a_second_pr(publication):
    client, session, url, body, provider, _, _ = publication
    provider.fail = "/pulls"
    assert mutate(client, url, session, json=body).status_code == 409
    branch = next(b for b in provider.refs if b != "main")
    del provider.refs[branch]
    provider.refs["main"] = "f" * 40
    response = mutate(client, url, session, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "stale" and response.json()["pr_url"]
    assert len(provider.pulls) == 1 and len(provider.refs) == 1


def test_moved_base_after_commit_prevents_branch_creation(publication):
    client, session, url, body, provider, _, _ = publication

    def move(request):
        if request.method == "POST" and request.url.path.endswith("/git/commits"):
            provider.refs["main"] = "f" * 40

    provider.on_call = move
    assert mutate(client, url, session, json=body).status_code == 409
    assert len(provider.refs) == 1 and not provider.pulls


def test_changed_inputs_cannot_reuse_a_publication(publication):
    client, session, url, body, provider, _, _ = publication
    assert mutate(client, url, session, json=body).status_code == 200
    body["base_branch"] = "another-base"
    assert mutate(client, url, session, json=body).status_code == 409
    assert len(provider.pulls) == 1


@pytest.mark.parametrize("field", ["patch_sha256", "notices_sha256", "reviewed", "base_branch"])
def test_review_is_explicit_and_bound_to_exact_bytes(publication, field):
    client, session, url, body, provider, _, _ = publication
    body[field] = (
        False if field == "reviewed" else "../main" if field == "base_branch" else "0" * 64
    )
    assert mutate(client, url, session, json=body).status_code in {409, 422}
    assert not provider.calls


def test_changed_artifact_and_forged_csrf_fail_before_provider(publication):
    client, session, url, body, provider, path, _ = publication
    assert client.post(url, json=body, headers={"Idempotency-Key": "forged-key"}).status_code == 403
    assert not provider.calls
    (path.parent / "notices/target-notices.json").write_bytes(b"changed")
    assert mutate(client, url, session, json=body).status_code == 409
    assert not provider.calls


def test_other_owner_cannot_publish_or_read(publication):
    client, _, url, body, provider, _, _ = publication
    token = client.app.state.auth.sign_in(
        GitHubUser(id=312, login="another-publisher"), GitHubTokens(**token_data())
    )
    client.cookies.set(SESSION_COOKIE, token, domain="127.0.0.1", path="/")
    other = client.get("/api/session").json()
    assert client.get(url).status_code == 404
    assert mutate(client, url, other, json=body).status_code == 404
    assert not provider.calls


@pytest.mark.parametrize("event", ["rights", "disconnect"])
def test_access_changes_during_publication_prevent_branch_and_pr_writes(publication, event):
    client, session, url, body, provider, _, _ = publication

    def change_access(request):
        if request.method == "POST" and request.url.path.endswith("/git/commits"):
            if event == "rights":
                provider.push = False
            else:
                with client.app.state.runs.connect() as db:
                    db.execute(
                        "UPDATE users SET connected=0,access_epoch=access_epoch+1 WHERE id=?",
                        (session["user"]["id"],),
                    )

    provider.on_call = change_access
    assert mutate(client, url, session, json=body).status_code in {403, 404, 409}
    assert len(provider.refs) == 1 and not provider.pulls


def test_changed_branch_is_never_overwritten(publication):
    client, session, url, body, provider, _, _ = publication
    provider.fail = "/pulls"
    assert mutate(client, url, session, json=body).status_code == 409
    branch = next(b for b in provider.refs if b != "main")
    provider.refs[branch] = "f" * 40
    assert mutate(client, url, session, json=body).status_code == 409
    assert provider.refs[branch] == "f" * 40 and len(provider.pulls) == 1


def test_execution_and_deletion_share_publication_lock(publication):
    client, session, url, body, provider, _, _ = publication
    with ProcessLock(execution_lock_path(client.app.state.settings)):
        response = mutate(client, url, session, json=body)
    assert response.status_code == 409 and not provider.calls


def test_two_simultaneous_requests_publish_once(publication):
    client, session, url, body, provider, _, _ = publication
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: mutate(client, url, session, json=body), range(2)))
    assert all(r.status_code in {200, 409} for r in responses)
    assert any(r.status_code == 200 for r in responses)
    assert len(provider.pulls) == 1


def test_owner_purge_removes_publication_metadata(publication):
    client, session, url, body, _, _, _ = publication
    assert mutate(client, url, session, json=body).status_code == 200
    owner = session["user"]["id"]
    with client.app.state.runs.connect() as db:
        Retention.purge_in(db, owner)
    assert PublicationStore(client.app.state.runs).get(owner, url.split("/")[3]) is None


def test_publishing_configuration_is_opt_in():
    assert not Settings(_env_file=None).github_publishing_enabled
    with pytest.raises(ValueError, match="PR publishing requires"):
        Settings(_env_file=None, github_publishing_enabled=True)


@pytest.mark.parametrize(
    "before,after",
    [
        (None, b"new\n"),
        (b"old", b"new\n"),
        (b"old\r\n", b"new\r\n"),
        (b"a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\n", b"A\nb\nc\nd\ne\nf\ng\nh\ni\nj\nK\n"),
    ],
)
def test_patch_reconstruction_handles_new_files_eof_crlf_and_separate_hunks(before, after):
    import difflib

    mapping = FileMapping(
        path="src/module.py",
        before_sha256=digest(before) if before is not None else None,
        after_sha256=digest(after),
    )
    lines = ["diff --git a/src/module.py b/src/module.py\n"]
    if before is None:
        lines.append("new file mode 100644\n")
    lines.extend(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(
            (before or b"").decode().splitlines(keepends=True),
            after.decode().splitlines(keepends=True),
            fromfile="a/src/module.py" if before is not None else "/dev/null",
            tofile="b/src/module.py",
        )
    )
    patch = PublishPatch.__new__(PublishPatch)
    parsed = parse_file([line + "\n" for line in "".join(lines).split("\n")[:-1]], mapping)
    patch.saved = SimpleNamespace(lines={"file": parsed})
    file = SimpleNamespace(id="file", **mapping.model_dump())
    assert patch.apply(file, before) == after
