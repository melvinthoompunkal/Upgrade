"""GitHub writes use the App user token's permission intersection; no Git credentials."""

import base64
import hashlib
import re
from urllib.parse import quote

from fastapi import HTTPException

from app.auth.access import RepositoryAccess
from app.auth.github import GitHubError


def oid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise GitHubError("GitHub returned an invalid Git object identity.")
    return value


def blob_sha(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class BaseMoved(HTTPException):
    def __init__(self):
        super().__init__(409, "The base branch moved. Revalidate the adaptation before publishing.")


class PublishingGitHub:
    def __init__(self, state, owner, repository_id, target_url):
        self.state, self.owner = state, owner
        self.repository_id, self.target_url = repository_id, target_url
        self.access = RepositoryAccess(state.runs, state.auth, state.github)
        self.grant = None
        self.refresh()

    def refresh(self):
        state = self.state
        grant = self.access.authorize(self.owner, self.repository_id)
        if self.grant and (grant.epoch, grant.installation) != (
            self.grant.epoch,
            self.grant.installation,
        ):
            raise HTTPException(409, "Repository authorization changed. Reconnect and revalidate.")
        self.token = state.auth.access_token(self.owner, state.github)
        repo = state.github.request("GET", f"/repositories/{self.repository_id}", token=self.token)
        installation = state.github.request(
            "GET", f"/repos/{grant.repository.full_name}/installation", token=state.github.app_jwt()
        )
        permissions = installation.get("permissions", {})
        if (
            repo.get("id") != self.repository_id
            or repo.get("full_name") != grant.repository.full_name
            or self.target_url.removeprefix("https://github.com/").removesuffix(".git").casefold()
            != grant.repository.full_name.casefold()
            or repo.get("private") is not False
            or repo.get("archived") is not False
            or repo.get("disabled", False) is not False
            or not isinstance(repo.get("permissions"), dict)
            or repo["permissions"].get("push") is not True
            or installation.get("id") != grant.installation
            or installation.get("app_id") != state.settings.github_app_id
            or installation.get("suspended_at") is not None
            or not isinstance(permissions, dict)
            or any(permissions.get(p) != "write" for p in ("contents", "pull_requests"))
        ):
            raise HTTPException(
                403,
                "Publishing needs current user and App write rights on the public target.",
            )
        self.grant = grant
        self.access.check(grant)

    def call(self, method, path, **kwargs):
        self.access.check(self.grant)
        return self.state.github.request(
            method, f"/repos/{self.grant.repository.full_name}{path}", token=self.token, **kwargs
        )

    def ref(self, branch):
        value = self.call("GET", f"/git/ref/heads/{quote(branch, safe='')}", expected=(200, 404))
        if not value:
            return None
        obj = value.get("object")
        if (
            value.get("ref") != f"refs/heads/{branch}"
            or not isinstance(obj, dict)
            or obj.get("type") != "commit"
        ):
            raise GitHubError("GitHub returned a different branch reference.")
        return oid(value["object"].get("sha"))

    def base(self, receipt):
        if self.ref(receipt.base_branch) != receipt.base_sha:
            raise BaseMoved()

    def tree(self, commit):
        body = self.call("GET", f"/git/commits/{oid(commit)}")
        tree = body.get("tree")
        if body.get("sha") != commit or not isinstance(tree, dict):
            raise GitHubError("GitHub returned a different base commit.")
        return oid(tree.get("sha"))

    def file(self, tree, path):
        """Walk trees explicitly so symlinks, submodules and truncated trees cannot pass."""
        parts = path.split("/")
        for index, name in enumerate(parts):
            body = self.call("GET", f"/git/trees/{oid(tree)}")
            entries = body.get("tree")
            if (
                body.get("sha") != tree
                or body.get("truncated") is not False
                or not isinstance(entries, list)
            ):
                raise GitHubError("GitHub returned an incomplete Git tree.")
            matches = [
                e
                for e in entries
                if isinstance(e, dict)
                and isinstance(e.get("path"), str)
                and e["path"].casefold() == name.casefold()
            ]
            if not matches:
                if index == len(parts) - 1:
                    return None
                raise HTTPException(
                    409, "The production directory is absent from the verified base."
                )
            if len(matches) != 1 or matches[0].get("path") != name:
                raise HTTPException(409, "The production path has a case collision.")
            entry = matches[0]
            if index < len(parts) - 1:
                if (entry.get("mode"), entry.get("type")) != ("040000", "tree"):
                    raise HTTPException(
                        409, "Linked or special production directories cannot be published."
                    )
                tree = oid(entry.get("sha"))
            else:
                if (entry.get("mode"), entry.get("type")) != ("100644", "blob"):
                    raise HTTPException(
                        409, "Linked, executable or special files cannot be published."
                    )
                body = self.call("GET", f"/git/blobs/{oid(entry.get('sha'))}")
                try:
                    if body.get("encoding") != "base64" or body.get("sha") != entry["sha"]:
                        raise ValueError
                    raw = base64.b64decode("".join(body["content"].split()), validate=True)
                    if (
                        len(raw) > 256_000
                        or body.get("size") != len(raw)
                        or blob_sha(raw) != entry["sha"]
                    ):
                        raise ValueError
                    return raw
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise GitHubError("GitHub returned invalid production file bytes.") from None

    def commit(self, patch, receipt):
        base_tree = self.tree(receipt.base_sha)
        entries = []
        for file in patch.saved.review.files:
            path = patch.full_path(file.path)
            raw = patch.apply(file, self.file(base_tree, path))
            # The exact patch already contains complete source notices in a tested integration file.
            blob = self.call(
                "POST",
                "/git/blobs",
                json_body={
                    "content": base64.b64encode(raw).decode(),
                    "encoding": "base64",
                },
                expected=(201,),
            )
            if blob.get("sha") != blob_sha(raw):
                raise GitHubError("GitHub did not retain the exact verified file bytes.")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.call(
            "POST",
            "/git/trees",
            json_body={
                "base_tree": base_tree,
                "tree": entries,
            },
            expected=(201,),
        )
        tree_sha = oid(tree.get("sha"))
        identity = {
            "name": "Upgrade",
            "email": "upgrade@users.noreply.github.com",
            "date": receipt.created_at,
        }
        commit = self.call(
            "POST",
            "/git/commits",
            json_body={
                "message": (
                    "Apply verified adaptation\n\n"
                    f"Patch-SHA256: {receipt.patch_sha256}\n"
                    f"Notices-SHA256: {receipt.notices_sha256}\n"
                ),
                "tree": tree_sha,
                "parents": [receipt.base_sha],
                "author": identity,
                "committer": identity,
            },
            expected=(201,),
        )
        receipt.tree_sha, receipt.commit_sha = tree_sha, oid(commit.get("sha"))

    def verify_commit(self, receipt):
        value = self.call("GET", f"/git/commits/{oid(receipt.commit_sha)}")
        tree, parents = value.get("tree"), value.get("parents")
        if (
            value.get("sha") != receipt.commit_sha
            or not isinstance(tree, dict)
            or tree.get("sha") != receipt.tree_sha
            or not isinstance(parents, list)
            or len(parents) != 1
            or not isinstance(parents[0], dict)
            or parents[0].get("sha") != receipt.base_sha
        ):
            raise HTTPException(409, "The publishing commit differs from the verified patch.")
