"""F8 consumes real F7 receipts; controlled providers never run during review."""

import json

import pytest
from adaptation_fixtures import AdaptationProvider, Captures, context, execute
from fastapi.testclient import TestClient
from test_auth import authenticated_app as authenticated_app
from test_auth import login
from test_meta_client import settings
from test_projects import independent as independent

from app.api.artifacts import RunArtifacts, digest, summary_from_report
from app.api.change_review import FileMapping, SavedChanges, parse_file
from app.change_review_contracts import DiffLine
from app.main import create_app


@pytest.fixture
def inputs(independent, tmp_path, monkeypatch):
    return context(independent, tmp_path, monkeypatch)


def attach(client, report, path, owner=None):
    summary = summary_from_report("change-review", "verified_adaptation", report)
    client.app.state.artifact_root = path.parent.parent
    client.app.state.runs.import_report(
        summary, f"{path.parent.name}/report.json", digest(path.read_bytes())
    )
    if owner:
        with client.app.state.runs.connect() as db:
            db.execute("UPDATE runs SET owner_id=? WHERE id=?", (owner, summary.id))
    return f"/api/runs/{summary.id}"


def test_real_f7_review_pages_and_exact_download(inputs, tmp_path, monkeypatch):
    report, path, provider, captures = execute(inputs, tmp_path, monkeypatch)
    calls = len(provider.calls), len(captures.calls)
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        url = attach(client, report, path)
        summary = client.get(url + "/changes")
        assert summary.status_code == 200, summary.text
        review = summary.json()
        assert review["verdict"] == "verified" and review["protected_tests_unchanged"]
        assert review["original_target_unchanged"] and not review["publishing_enabled"]
        assert client.get("/api/capabilities").json()["publishing_enabled"] is False
        assert len(review["files"]) == 1 and "lines" not in review["files"][0]
        assert len(summary.content) < 16_000
        file = review["files"][0]
        assert file["path"] == "src/sequence.py" and file["status"] == "modified"
        assert file["additions"] + file["deletions"] == report["adaptation"]["changed_lines"]
        assert any(e["label"] == "notices/source-NOTICE.txt" for e in review["evidence"])
        reconstructed, offset = [], 0
        while offset is not None:
            page = client.get(url + f"/changes/files/{file['id']}?offset={offset}&limit=7")
            assert page.status_code == 200, page.text
            body = page.json()
            assert body["offset"] == offset and len(body["lines"]) <= 7
            assert body["patch_sha256"] == review["patch"]["sha256"]
            assert all(
                line["new_line"] is not None for line in body["lines"] if line["kind"] == "addition"
            )
            reconstructed.extend(line["text"] for line in body["lines"])
            offset = body["next_offset"]
        patch = client.get(url + f"/artifacts/{review['patch']['id']}")
        assert digest(patch.content) == report["adaptation"]["patch_sha256"]
        assert "\n".join(reconstructed) + "\n" == patch.text
        assert patch.headers["cache-control"] == "no-store"
        assert client.get(url + "/changes/files/unknown").status_code == 404
        assert client.get(url + f"/changes/files/{file['id']}?offset=9999").status_code == 422
        assert client.get(url + f"/changes/files/{file['id']}?limit=201").status_code == 422
        assert client.get(url + f"/changes/files/{file['id']}?offset=-1").status_code == 422
        # The optional write path remains disabled by default.
        for _ in range(2):
            assert (
                client.post(
                    url + "/pull-request",
                    headers={
                        "Origin": "http://127.0.0.1:5173",
                        "Idempotency-Key": "disabled-publish",
                    },
                    json={
                        "repository_id": 1,
                        "base_branch": "main",
                        "reviewed": True,
                        "patch_sha256": review["patch"]["sha256"],
                        "notices_sha256": "a" * 64,
                    },
                ).status_code
                == 409
            )
        assert client.get(url + "/changes").json() == review
    assert (len(provider.calls), len(captures.calls)) == calls


@pytest.mark.parametrize("present", [True, False])
def test_already_present_and_exhausted_remain_honest(inputs, tmp_path, monkeypatch, present):
    report, path, _, _ = execute(
        inputs,
        tmp_path,
        monkeypatch,
        provider=AdaptationProvider(bad_rounds=0 if present else 4),
        captures=Captures(red="green" if present else "red", bad_rounds=0 if present else 4),
    )
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        url = attach(client, report, path)
        response = client.get(url + "/changes")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["verdict"] == ("already_present" if present else "unverified")
        assert bool(data["patch"]) is not present
        assert bool(data["files"]) is not present


@pytest.mark.parametrize(
    "name", ["patch", "mapping", "frozen-binding.json", "acceptance/test_feature.py", "report.json"]
)
def test_missing_or_changed_receipts_fail_closed(inputs, tmp_path, monkeypatch, name):
    report, path, _, _ = execute(inputs, tmp_path, monkeypatch)
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        url = attach(client, report, path)
        file = client.get(url + "/changes").json()["files"][0]
        patch = report["adaptation"]["patch_artifact"]
        target = {"patch": patch, "mapping": patch[:-6] + "-mapping.json"}.get(name, name)
        (path.parent / target).write_bytes(b"changed")
        for endpoint in ("/changes", f"/changes/files/{file['id']}"):
            response = client.get(url + endpoint)
            assert response.status_code == 409, response.text
            assert "Traceback" not in response.text and str(tmp_path) not in response.text


def test_two_owners_cannot_review_each_others_changes(
    inputs, tmp_path, monkeypatch, authenticated_app
):
    report, path, provider, _ = execute(inputs, tmp_path, monkeypatch)
    client, state = authenticated_app
    session, _ = login(client)
    url = attach(client, report, path, session["user"]["id"])
    response = client.get(url + "/changes")
    assert response.status_code == 200, response.text
    file = response.json()["files"][0]
    assert client.get(url + f"/changes/files/{file['id']}").status_code == 200
    state.update(id=312, login="other-reviewer")
    login(client)
    for suffix in ("/changes", f"/changes/files/{file['id']}"):
        assert client.get(url + suffix).status_code == 404
    assert len(provider.calls) == 4


def test_bounded_long_lines_and_page_window(inputs, tmp_path, monkeypatch):
    report, path, _, _ = execute(inputs, tmp_path, monkeypatch)
    summary = summary_from_report("review", "verified_adaptation", report)
    bundle = RunArtifacts(
        path.parent.parent,
        {
            "summary": summary.model_dump_json(),
            "report": f"{path.parent.name}/report.json",
            "digest": digest(path.read_bytes()),
        },
    )
    saved = SavedChanges(bundle)
    file = saved.review.files[0]
    saved.lines[file.id] = [
        DiffLine(kind="addition", text="+" + "界" * 10_000, new_line=i) for i in range(1, 501)
    ]
    first = saved.page(file.id, 0, 200)
    assert len(first.model_dump_json().encode()) < 34_000
    assert first.next_offset and first.next_offset < 200
    assert all(line.truncated and len(line.text.encode()) <= 8000 for line in first.lines)
    assert len(saved.page(file.id, first.next_offset, 200).lines) == len(first.lines)


@pytest.mark.parametrize("change", ["scope", "preimage", "hunk", "path"])
def test_hash_registered_but_inconsistent_mapping_or_diff_rejected(
    inputs, tmp_path, monkeypatch, change
):
    report, path, _, _ = execute(inputs, tmp_path, monkeypatch)
    name = report["adaptation"]["patch_artifact"]
    mapping_name = name[:-6] + "-mapping.json"
    if change == "scope":
        report["adaptation"]["changed_files"] = 4
    elif change == "preimage":
        mapping = json.loads((path.parent / mapping_name).read_bytes())
        mapping[0]["before_sha256"] = "0" * 64
        raw = json.dumps(mapping).encode()
        (path.parent / mapping_name).write_bytes(raw)
        report["artifact_hashes"][mapping_name] = digest(raw)
    else:
        raw = (path.parent / name).read_bytes()
        raw = (
            raw.replace(b"@@ -", b"@@ -999,", 1)
            if change == "hunk"
            else raw.replace(b"src/sequence.py", b"tests/test_sequence.py")
        )
        (path.parent / name).write_bytes(raw)
        report["artifact_hashes"][name] = report["adaptation"]["patch_sha256"] = digest(raw)
    path.write_text(json.dumps(report))
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        url = attach(client, report, path)
        assert client.get(url + "/changes").status_code == 409


def test_added_file_and_zero_length_old_range():
    lines = parse_file(
        [
            "diff --git a/src/new.py b/src/new.py\n",
            "new file mode 100644\n",
            "--- /dev/null\n",
            "+++ b/src/new.py\n",
            "@@ -0,0 +1,2 @@\n",
            "+def helper():\n",
            "+    return 1\n",
        ],
        FileMapping(path="src/new.py", before_sha256=None, after_sha256="a" * 64),
    )
    assert [(line.old_line, line.new_line) for line in lines[-2:]] == [(None, 1), (None, 2)]


def test_separate_hunks_and_unterminated_original_line():
    lines = parse_file(
        [
            "diff --git a/src/a.py b/src/a.py\n",
            "--- a/src/a.py\n",
            "+++ b/src/a.py\n",
            "@@ -1 +1 @@\n",
            "-original\n",
            "+replacement\n",
            "@@ -9 +9,2 @@\n",
            "-unterminated\n",
            "\\ No newline at end of file\n",
            "+final\n",
            "+line\n",
        ],
        FileMapping(path="src/a.py", before_sha256="b" * 64, after_sha256="a" * 64),
    )
    assert lines[-4].kind == "deletion" and lines[-4].old_line == 9
    assert lines[-3].kind == "marker"
    assert [(line.old_line, line.new_line) for line in lines[-2:]] == [(None, 9), (None, 10)]
