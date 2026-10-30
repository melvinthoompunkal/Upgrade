"""Persist before writes and reconcile uncertain branch/PR responses on explicit retry."""

from urllib.parse import urlencode

from fastapi import HTTPException

from app.auth.github import GitHubError
from app.publishing.contracts import PublishedPull
from app.publishing.github import BaseMoved, PublishingGitHub
from app.publishing.store import PublicationStore


def pr_body(receipt):
    return (
        "Applies a reviewed production patch that passed frozen acceptance "
        "and native regression.\n\n"
        f"Verified base: `{receipt.base_sha}`\n\n"
        f"Patch SHA-256: `{receipt.patch_sha256}`\n\n"
        f"Notices SHA-256: `{receipt.notices_sha256}`\n\n"
        "Complete source notices are included in the tested production files. "
        "Verification applies to the recorded base only. "
        "Review any later base changes before merging.\n\n"
        f"<!-- upgrade-publication:{receipt.branch} -->"
    )


def accept_pr(value, receipt):
    try:
        pull = PublishedPull.model_validate(value)
    except ValueError:
        raise GitHubError("GitHub returned invalid pull request metadata.") from None
    number = pull.number
    expected_url = f"https://github.com/{receipt.repository}/pull/{number}"
    if (
        pull.html_url != expected_url
        or pull.head.sha != receipt.commit_sha
        or pull.head.ref != receipt.branch
        or pull.base.ref != receipt.base_branch
        or pull.base.repo.id != receipt.repository_id
        or pull.head.repo.id != receipt.repository_id
        or pull.body != pr_body(receipt)
    ):
        raise HTTPException(409, "The provider pull request differs from this exact publication.")
    receipt.pr_number, receipt.pr_url = number, expected_url
    receipt.state, receipt.failure_code = "published", None


def retain_pr(value, remote, store, owner, run_id, receipt):
    accept_pr(value, receipt)
    # GitHub cannot atomically check a base and create a PR. Retain the actual PR
    # before detecting a concurrent move, including recovery of a lost response.
    store.save(owner, run_id, receipt)
    try:
        remote.base(receipt)
    except BaseMoved:
        receipt.state, receipt.failure_code = "stale", "base_moved"
        store.save(owner, run_id, receipt)
    return receipt


def publish(state, owner, run_id, key, body, patch):
    remote = PublishingGitHub(state, owner, body.repository_id, patch.target.url)
    store = PublicationStore(state.runs)
    receipt = store.prepare(owner, run_id, key, body, patch, remote.grant.repository.full_name)
    if receipt.state == "published":
        return receipt
    if receipt.state == "stale":
        raise HTTPException(
            409, "This publication is stale. Revalidate the adaptation against the current base."
        )
    try:
        if receipt.commit_sha:
            remote.verify_commit(receipt)
            head = remote.ref(receipt.branch)
            if head is not None and head != receipt.commit_sha:
                raise HTTPException(
                    409, "The publishing branch changed. It will not be overwritten."
                )
            query = urlencode(
                {
                    "state": "all",
                    "head": f"{receipt.repository.split('/')[0]}:{receipt.branch}",
                    "base": receipt.base_branch,
                    "per_page": 100,
                }
            )
            values = remote.call("GET", f"/pulls?{query}", list_response=True)
            if len(values) > 1:
                raise HTTPException(
                    409, "Multiple pull requests match this publication. Review them on GitHub."
                )
            if values:
                return retain_pr(values[0], remote, store, owner, run_id, receipt)
        remote.base(receipt)
        if not receipt.commit_sha:
            remote.commit(patch, receipt)
            receipt.state = "committed"
            store.save(owner, run_id, receipt)
            remote.verify_commit(receipt)
        remote.refresh()
        remote.base(receipt)
        head = remote.ref(receipt.branch)
        if head is None:
            result = remote.call(
                "POST",
                "/git/refs",
                json_body={
                    "ref": f"refs/heads/{receipt.branch}",
                    "sha": receipt.commit_sha,
                },
                expected=(201,),
            )
            obj = result.get("object")
            if (
                result.get("ref") != f"refs/heads/{receipt.branch}"
                or not isinstance(obj, dict)
                or obj.get("sha") != receipt.commit_sha
                or obj.get("type") != "commit"
            ):
                raise GitHubError("GitHub did not confirm the exact publishing branch.")
        elif head != receipt.commit_sha:
            raise HTTPException(409, "The publishing branch changed. It will not be overwritten.")
        receipt.state = "branched"
        store.save(owner, run_id, receipt)
        remote.refresh()
        remote.base(receipt)
        if remote.ref(receipt.branch) != receipt.commit_sha:
            raise HTTPException(409, "The publishing branch changed. Revalidation is required.")
        value = remote.call(
            "POST",
            "/pulls",
            json_body={
                "title": "Apply verified adaptation",
                "head": receipt.branch,
                "base": receipt.base_branch,
                "body": pr_body(receipt),
                "draft": True,
                "maintainer_can_modify": False,
            },
            expected=(201,),
        )
        return retain_pr(value, remote, store, owner, run_id, receipt)
    except (GitHubError, HTTPException) as error:
        receipt.state = "stale" if isinstance(error, BaseMoved) else "retryable"
        receipt.failure_code = (
            "base_moved" if receipt.state == "stale" else "publishing_interrupted"
        )
        store.save(owner, run_id, receipt)
        raise
