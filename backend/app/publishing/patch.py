"""Reconstruct exact verified bytes from reviewed hunks and immutable Git preimages."""

import json

from fastapi import HTTPException

from app.api.artifacts import digest
from app.api.change_review import HUNK, SavedChanges, conflict
from app.contracts import validate_repo_path
from app.project_contracts import ProjectRequest


class PublishPatch:
    def __init__(self, saved: SavedChanges):
        self.saved = saved
        if saved.review.verdict != "verified" or not saved.review.patch:
            raise HTTPException(409, "Only a verified production patch can be published.")
        bundle = saved.bundle
        try:
            identity = json.loads(bundle.raw("adaptation-identity.json"))
            frozen = json.loads(bundle.raw("frozen-binding.json"))
            target = identity["target"]
            self.target = ProjectRequest(
                url=target["origin"],
                commit_sha=target["sha"],
                project_root=target["root"],
                role="target",
            )
            if (
                frozen["identity"] != identity
                or bundle.report["target"]["origin"] != self.target.url
                or bundle.report["target"]["commit_sha"] != self.target.commit_sha
                or bundle.report["target_path_prefix"] != self.target.project_root
            ):
                raise ValueError
            self.patch_sha256 = saved.review.patch.sha256
            self.notice = bundle.raw("notices/source-NOTICE.txt")
            self.notices_sha256 = digest(
                json.dumps(
                    {
                        "source": digest(self.notice),
                        "target": digest(bundle.raw("notices/target-notices.json")),
                    },
                    sort_keys=True,
                ).encode()
            )
        except (KeyError, ValueError, TypeError, HTTPException):
            raise conflict() from None

    def full_path(self, path):
        root = self.target.project_root
        value = f"{root}/{path}" if root else path
        validate_repo_path(value)
        return value

    def apply(self, file, before: bytes | None) -> bytes:
        if (digest(before) if before is not None else None) != file.before_sha256:
            raise HTTPException(409, "GitHub file bytes differ from the verified base. Revalidate.")
        try:
            original = (before or b"").decode().splitlines(keepends=True)
            lines = self.saved.lines[file.id]
            result, cursor = [], 0
            for index, line in enumerate(lines):
                if line.kind == "hunk":
                    match = HUNK.fullmatch(line.text)
                    count = int(match[2]) if match[2] is not None else 1
                    start = int(match[1]) - bool(count)
                    if not cursor <= start <= len(original):
                        raise ValueError
                    result.extend(original[cursor:start])
                    cursor = start
                    new_count = int(match[4]) if match[4] is not None else 1
                    if len(result) != int(match[3]) - bool(new_count):
                        raise ValueError
                elif line.kind in {"context", "deletion", "addition"}:
                    marker = index + 1 < len(lines) and lines[index + 1].kind == "marker"
                    text = line.text[1:] + ("" if marker else "\n")
                    if line.old_line is not None:
                        if cursor != line.old_line - 1 or original[cursor] != text:
                            raise ValueError
                        cursor += 1
                    if line.new_line is not None:
                        if len(result) != line.new_line - 1:
                            raise ValueError
                        result.append(text)
            result.extend(original[cursor:])
            raw = "".join(result).encode()
            if digest(raw) != file.after_sha256:
                raise ValueError
            return raw
        except (ValueError, IndexError, UnicodeError):
            raise conflict() from None
