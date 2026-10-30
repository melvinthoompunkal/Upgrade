"""Inspect saved patches only: no repository reads, inference, execution or writes."""

import re

from fastapi import HTTPException
from pydantic import BaseModel, TypeAdapter

from app.adaptation_contracts import AdaptationResult, Digest
from app.adaptation_policy import production_path
from app.api.artifacts import RunArtifacts, digest
from app.change_review_contracts import ChangedFile, ChangeReview, DiffLine, DiffPage
from app.contracts import RepoPath

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@$")
MAX_PAGE_BYTES = 32_000


class FileMapping(BaseModel):
    path: RepoPath
    before_sha256: Digest | None
    after_sha256: Digest


class ReviewBinding(BaseModel):
    target_input_hashes: dict[RepoPath, Digest]
    acceptance_hashes: dict[RepoPath, Digest]
    contract_sha256: Digest
    spec_sha256: Digest
    notice_sha256: Digest


def conflict():
    return HTTPException(
        409, "The saved change receipts are incomplete or inconsistent. Check exports."
    )


def parse_file(text: list[str], mapping: FileMapping) -> list[DiffLine]:
    """Decode the controller's unified format, preserving source line numbers and EOF markers."""
    path = mapping.path
    headers = [f"diff --git a/{path} b/{path}\n"]
    if mapping.before_sha256 is None:
        headers.append("new file mode 100644\n")
    headers += [
        f"--- a/{path}\n" if mapping.before_sha256 else "--- /dev/null\n",
        f"+++ b/{path}\n",
    ]
    if text[: len(headers)] != headers:
        raise ValueError("Invalid file headers")
    result = [DiffLine(kind="header", text=line.removesuffix("\n")) for line in headers]
    old, new, old_left, new_left = 0, 0, 0, 0
    hunks = 0
    for raw in text[len(headers) :]:
        line = raw.removesuffix("\n")
        match = HUNK.fullmatch(line)
        if match:
            if old_left or new_left:
                raise ValueError("Incomplete hunk")
            old, new = int(match[1]), int(match[3])
            old_left = int(match[2]) if match[2] is not None else 1
            new_left = int(match[4]) if match[4] is not None else 1
            hunks += 1
            result.append(DiffLine(kind="hunk", text=line))
        elif line == "\\ No newline at end of file":
            if not result or result[-1].kind not in {"addition", "deletion", "context"}:
                raise ValueError("Unexpected EOF marker")
            result.append(DiffLine(kind="marker", text=line))
        elif hunks and line.startswith(("+", "-", " ")):
            prefix = line[0]
            takes_old, takes_new = prefix != "+", prefix != "-"
            old_left -= takes_old
            new_left -= takes_new
            if old_left < 0 or new_left < 0:
                raise ValueError("Unexpected hunk line")
            result.append(
                DiffLine(
                    kind={"+": "addition", "-": "deletion", " ": "context"}[prefix],
                    text=line,
                    old_line=old if takes_old else None,
                    new_line=new if takes_new else None,
                )
            )
            old += takes_old
            new += takes_new
        else:
            raise ValueError("Unsupported patch line")
    if not hunks or old_left or new_left:
        raise ValueError("Incomplete file diff")
    return result


class SavedChanges:
    def __init__(self, bundle: RunArtifacts):
        self.bundle = bundle
        if bundle.summary.kind != "verified_adaptation":
            raise HTTPException(404, "This run has no adaptation change review.")
        if bundle.summary.status != "finished":
            raise HTTPException(409, "Change review is available when this adaptation finishes.")
        self.lines: dict[str, list[DiffLine]] = {}
        try:
            self.result = AdaptationResult.model_validate(bundle.report["adaptation"])
            self.review = ChangeReview(
                verdict=self.result.verdict,
                outcome=bundle.report.get("status"),
                protected_tests_unchanged=self.result.protected_tests_unchanged,
                original_target_unchanged=bundle.report.get("original_target_unchanged") is True,
                repair_count=self.result.repair_count,
            )
            evidence = (
                "report.json",
                "adaptation-identity.json",
                "behavior-contract.json",
                "frozen-binding.json",
                "acceptance-spec.json",
                "acceptance/test_feature.py",
                "adaptation-gates.json",
                "notices/source-NOTICE.txt",
                "notices/target-notices.json",
            )
            self.review.evidence = [bundle.descriptor(p) for p in evidence if p in bundle.hashes]
            if self.result.verdict == "verified" and not (
                self.review.outcome == "verified"
                and self.review.protected_tests_unchanged
                and self.review.original_target_unchanged
            ):
                raise ValueError("Inconsistent verification verdict")
            self.frozen = (
                ReviewBinding.model_validate_json(bundle.raw("frozen-binding.json"))
                if self.result.protected_tests_unchanged or self.result.patch_artifact
                else None
            )
            if self.result.protected_tests_unchanged:
                frozen = self.frozen
                for name, expected in (
                    ("acceptance/test_feature.py", frozen.acceptance_hashes["test_feature.py"]),
                    ("behavior-contract.json", frozen.contract_sha256),
                    ("acceptance-spec.json", frozen.spec_sha256),
                    ("notices/source-NOTICE.txt", frozen.notice_sha256),
                ):
                    if digest(bundle.raw(name)) != expected:
                        raise ValueError("Changed frozen input")
            if self.result.patch_artifact is not None:
                self._patch(self.result.patch_artifact)
            elif self.result.verdict == "verified" or self.result.patch_sha256 is not None:
                raise ValueError("Missing patch")
        except (ValueError, KeyError, TypeError, UnicodeError, HTTPException):
            raise conflict() from None

    def _patch(self, name: str):
        bundle, result = self.bundle, self.result
        if not re.fullmatch(r"rounds/round-[0-3]\.patch", name):
            raise ValueError("Invalid patch name")
        patch = bundle.raw(name)
        if digest(patch) != result.patch_sha256:
            raise ValueError("Patch does not match result")
        mappings = TypeAdapter(list[FileMapping]).validate_json(
            bundle.raw(name[:-6] + "-mapping.json")
        )
        if not 1 <= len(mappings) <= 5 or len({m.path.casefold() for m in mappings}) != len(
            mappings
        ):
            raise ValueError("Invalid patch mapping")
        parts = []
        text = patch.decode("utf-8")
        if not text.endswith("\n"):
            raise ValueError("Unterminated patch")
        for value in text.split("\n")[:-1]:
            line = value + "\n"
            if line.startswith("diff --git "):
                parts.append([])
            if not parts:
                raise ValueError("Missing file header")
            parts[-1].append(line)
        if len(parts) != len(mappings):
            raise ValueError("Patch and mapping differ")
        for mapping, lines in zip(mappings, parts, strict=True):
            if not production_path(mapping.path):
                raise ValueError("Protected file in patch")
            if mapping.before_sha256 != self.frozen.target_input_hashes.get(mapping.path):
                raise ValueError("Preimage differs from frozen target")
            parsed = parse_file(lines, mapping)
            file = ChangedFile(
                id=digest(mapping.path.encode())[:24],
                **mapping.model_dump(),
                status="added" if mapping.before_sha256 is None else "modified",
                additions=sum(line.kind == "addition" for line in parsed),
                deletions=sum(line.kind == "deletion" for line in parsed),
                diff_lines=len(parsed),
            )
            self.lines[file.id] = parsed
            self.review.files.append(file)
        if (
            len(mappings) != result.changed_files
            or sum(f.additions + f.deletions for f in self.review.files) != result.changed_lines
            or sum(f.status == "added" for f in self.review.files) != result.new_files
        ):
            raise ValueError("Scope differs from result")
        self.review.patch = bundle.descriptor(name)
        self.review.mapping = bundle.descriptor(name[:-6] + "-mapping.json")

    def page(self, file_id: str, offset: int, limit: int) -> DiffPage:
        file = next((f for f in self.review.files if f.id == file_id), None)
        if file is None:
            raise HTTPException(404, "File is not in this saved patch.")
        lines = self.lines[file_id]
        if offset >= len(lines):
            raise HTTPException(422, "Diff offset is outside this file.")
        page, size = [], 0
        for line in lines[offset : offset + limit]:
            raw = line.text.encode()
            # One pathological source line must not exceed the page or DOM budget.
            if len(raw) > 8000:
                line = line.model_copy(
                    update={"text": raw[:8000].decode("utf-8", "ignore"), "truncated": True}
                )
            cost = len(line.model_dump_json().encode())
            if page and size + cost > MAX_PAGE_BYTES:
                break
            page.append(line)
            size += cost
        end = offset + len(page)
        return DiffPage(
            file=file,
            patch_sha256=self.result.patch_sha256,
            offset=offset,
            next_offset=end if end < len(lines) else None,
            total_lines=len(lines),
            lines=page,
        )
