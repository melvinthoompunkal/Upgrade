"""Stateful GitHub transport: real preimages, content-addressed blobs, uncertain writes."""

import base64
import hashlib
import json
from urllib.parse import unquote

import httpx

from app.auth.github import GitHubClient
from app.publishing.github import blob_sha


class PublishingProvider:
    def __init__(self, client, report, directory, owner, monkeypatch):
        self.calls, self.blobs, self.trees, self.commits, self.pulls = [], {}, {}, {}, []
        self.push, self.permissions, self.fail, self.move_on_pr = True, "write", None, False
        self.on_call = None
        self.repo = {
            "id": 101,
            "full_name": "team/sequence-target",
            "owner": {"login": "team"},
            "private": False,
            "default_branch": "main",
            "archived": False,
        }
        files = {
            "library/" + p.relative_to(directory / "original").as_posix(): p.read_bytes()
            for p in (directory / "original").rglob("*")
            if p.is_file()
        }
        self.base = report["target"]["commit_sha"]
        self.base_tree = self.build_tree(files)
        self.commits[self.base] = {"sha": self.base, "tree": {"sha": self.base_tree}, "parents": []}
        self.refs = {"main": self.base}
        settings = client.app.state.settings.model_copy(
            update={
                "github_publishing_enabled": True,
                "github_app_id": 19,
            }
        )
        client.app.state.settings = settings
        client.app.state.github = GitHubClient(settings, transport=httpx.MockTransport(self.handle))
        monkeypatch.setattr(client.app.state.github, "app_jwt", lambda: "fixture-app-jwt")
        with client.app.state.runs.connect() as db:
            db.execute(
                "INSERT INTO repository_cache VALUES (?,?,?,?,?,?)",
                (
                    owner,
                    101,
                    "publishing-fixture",
                    7,
                    self.repo["full_name"],
                    json.dumps({**self.repo, "installation_id": 7}),
                ),
            )

    def build_tree(self, files):
        entries, directories = [], {}
        for path, raw in sorted(files.items()):
            if "/" in path:
                name, rest = path.split("/", 1)
                directories.setdefault(name, {})[rest] = raw
            else:
                sha = blob_sha(raw)
                self.blobs[sha] = raw
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        for name, children in directories.items():
            entries.append(
                {"path": name, "mode": "040000", "type": "tree", "sha": self.build_tree(children)}
            )
        entries.sort(key=lambda item: item["path"] + ("/" if item["type"] == "tree" else ""))
        raw = b"".join(
            e["mode"].lstrip("0").encode()
            + b" "
            + e["path"].encode()
            + b"\0"
            + bytes.fromhex(e["sha"])
            for e in entries
        )
        sha = hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self.trees[sha] = {"sha": sha, "tree": entries, "truncated": False}
        return sha

    def flatten(self, tree, prefix=""):
        result = {}
        for e in self.trees[tree]["tree"]:
            if e["type"] == "tree":
                result.update(self.flatten(e["sha"], prefix + e["path"] + "/"))
            else:
                result[prefix + e["path"]] = self.blobs[e["sha"]]
        return result

    def handle(self, request):
        self.calls.append(request)
        if self.on_call:
            self.on_call(request)
        path, method = unquote(request.url.path), request.method
        body = json.loads(request.content) if request.content else {}
        assert method in {"GET", "POST"}, "Publishing must never merge, update or delete refs."
        prefix = "/repos/team/sequence-target"
        if method == "GET" and path in {prefix, "/repositories/101"}:
            return httpx.Response(200, json={**self.repo, "permissions": {"push": self.push}})
        if method == "GET" and path == prefix + "/installation":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "app_id": 19,
                    "suspended_at": None,
                    "permissions": {
                        "contents": self.permissions,
                        "pull_requests": self.permissions,
                    },
                },
            )
        suffix = path.removeprefix(prefix)
        if method == "GET" and suffix.startswith("/git/ref/heads/"):
            branch = suffix.removeprefix("/git/ref/heads/")
            if branch not in self.refs:
                return httpx.Response(404, json={"message": "Not found"})
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/" + branch,
                    "object": {"type": "commit", "sha": self.refs[branch]},
                },
            )
        for kind, values in (("commits", self.commits), ("trees", self.trees)):
            if method == "GET" and suffix.startswith(f"/git/{kind}/"):
                return httpx.Response(200, json=values[suffix.rsplit("/", 1)[-1]])
        if method == "GET" and suffix.startswith("/git/blobs/"):
            sha = suffix.rsplit("/", 1)[-1]
            raw = self.blobs[sha]
            return httpx.Response(
                200,
                json={
                    "sha": sha,
                    "encoding": "base64",
                    "size": len(raw),
                    "content": base64.b64encode(raw).decode(),
                },
            )
        if method == "POST" and suffix == "/git/blobs":
            raw = base64.b64decode(body["content"], validate=True)
            sha = blob_sha(raw)
            self.blobs[sha] = raw
            result = {"sha": sha}
        elif method == "POST" and suffix == "/git/trees":
            assert body["base_tree"] == self.base_tree
            files = self.flatten(body["base_tree"])
            for entry in body["tree"]:
                assert entry["mode"] == "100644" and entry["type"] == "blob"
                files[entry["path"]] = self.blobs[entry["sha"]]
            result = {"sha": self.build_tree(files)}
        elif method == "POST" and suffix == "/git/commits":
            assert body["parents"] == [self.base]
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            result = {"sha": sha, "tree": {"sha": body["tree"]}, "parents": [{"sha": self.base}]}
            self.commits[sha] = result
        elif method == "POST" and suffix == "/git/refs":
            branch = body["ref"].removeprefix("refs/heads/")
            assert branch.startswith("upgrade/") and branch not in self.refs
            self.refs[branch] = body["sha"]
            result = {"ref": body["ref"], "object": {"type": "commit", "sha": body["sha"]}}
        elif method == "GET" and suffix == "/pulls":
            return httpx.Response(200, json=self.pulls)
        elif method == "POST" and suffix == "/pulls":
            assert body["draft"] is True and body["maintainer_can_modify"] is False
            result = {
                "number": 23,
                "html_url": "https://github.com/team/sequence-target/pull/23",
                "head": {"ref": body["head"], "sha": self.refs[body["head"]], "repo": {"id": 101}},
                "base": {"ref": body["base"], "sha": self.base, "repo": {"id": 101}},
                "body": body["body"],
            }
            self.pulls.append(result)
            if self.move_on_pr:
                self.refs["main"] = "f" * 40
        else:
            raise AssertionError(f"Unexpected GitHub request: {method} {suffix}")
        if self.fail == suffix:
            self.fail = None
            raise httpx.ReadError("provider response containing a fixture secret", request=request)
        return httpx.Response(201, json=result)
