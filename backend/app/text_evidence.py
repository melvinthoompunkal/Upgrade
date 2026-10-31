"""Controller-owned text provenance, lexical retrieval and exact unread-range coverage."""

import json
import re
from time import monotonic

from app.contracts import Ref, validate_repo_path
from app.errors import EvidenceError, GraftError
from app.evidence_tools import EvidenceTools
from app.text_contracts import (
    CoverageV2,
    ListPaths,
    ReadRangesV2,
    ReadRangeV2,
    TextRefV2,
    TextSearch,
)
from app.tools.evidence import EvidenceSession, text_window


def text_input_reservation(messages, definitions, formatting) -> int:
    """Conservative byte preflight plus framing reserve; returned usage remains authoritative."""
    payload = {"messages": messages, "tools": definitions, "response_format": formatting}
    return len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) + 8192


class TextEvidenceSession(EvidenceSession):
    def __init__(self, repo, project_id):
        super().__init__(repo)
        self.project_id = project_id
        self.max_files = 64
        self.limits = set()
        self.searches = []
        self.search_bytes = 0
        self.previous_read_ranges = []
        self.list_calls = 0

    def index(self, path):
        raise EvidenceError("Optional parsers are disabled for this text session.")

    def ref(self, path, start_line, end_line, symbol=None):
        return TextRefV2(
            schema_version="text-ref-v2",
            project_id=self.project_id,
            project_root=self.repo.prefix,
            role=self.repo.snapshot.repo_id,
            repo_id=self.repo.snapshot.repo_id,
            commit_sha=self.repo.snapshot.commit_sha,
            path=path,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            blob_sha256=None,
            excerpt_sha256=None,
        )

    def _record_range(self, path, lines, start_line, end_line):
        if (
            path not in {w["path"] for w in self.windows}
            and len({w["path"] for w in self.windows}) >= self.max_files
        ):
            self.limits.add("distinct_evidence_files")
            raise EvidenceError("Text evidence session reached its 64-file exposure limit.")
        try:
            window = super()._record_range(path, lines, start_line, end_line)
        except EvidenceError:
            self.limits.add("read_window_or_byte_limit")
            raise
        window["ref"] = self._window_ref(window)
        return window

    def _window_ref(self, window):
        blob = next(f.sha256 for f in self.repo.inventory.files if f.path == window["path"])
        return (
            self.ref(window["path"], window["start_line"], window["end_line"])
            .model_copy(
                update={
                    "blob_sha256": blob,
                    "excerpt_sha256": window["sha256"],
                }
            )
            .model_dump(mode="json")
        )

    def read_ranges(self, ranges):
        windows, errors = [], []
        for index, request in enumerate(ranges):
            try:
                lines = self.repo.read(request.path).splitlines()
                preview = text_window(request.path, lines, request.start_line, request.end_line)
                preview["ref"] = self._window_ref(preview)
                if len(json.dumps([*windows, preview], ensure_ascii=True).encode()) > 44_000:
                    self.limits.add("batch_result_bytes")
                    raise EvidenceError(
                        "Batch response limit reached; request fewer/smaller windows."
                    )
                windows.append(
                    self._record_range(request.path, lines, request.start_line, request.end_line)
                )
            except GraftError as error:
                errors.append(
                    {"index": index, "error_code": error.code, "detail": str(error)[:160]}
                )
        result = {"windows": windows, "errors": errors, "result_byte_limit": 48_000}
        if len(json.dumps(result, ensure_ascii=True).encode()) > 48_000:
            for error in errors:
                error.pop("detail", None)
        return result

    def validate_ref(self, ref):
        if not isinstance(ref, TextRefV2):
            raise EvidenceError("Text analysis requires an explicit v2 citation.")
        if (ref.project_id, ref.project_root, ref.role) != (
            self.project_id,
            self.repo.prefix,
            self.repo.snapshot.repo_id,
        ):
            raise EvidenceError("Citation belongs to a different project, root or role.")
        if self.repo.prefix and not ref.path.startswith(self.repo.prefix + "/"):
            raise EvidenceError("Citation escaped the hard project-root boundary.")
        # Reuse the legacy exact-byte/read checks, without interpreting a display symbol as AST.
        plain = Ref.model_validate(ref.model_dump(include=set(Ref.model_fields)))
        excerpt = super().validate_ref(plain.model_copy(update={"symbol": None}))
        blob = next(f.sha256 for f in self.repo.inventory.files if f.path == ref.path)
        if (ref.blob_sha256 is not None and ref.blob_sha256 != blob) or (
            ref.excerpt_sha256 is not None and ref.excerpt_sha256 != excerpt["sha256"]
        ):
            raise EvidenceError("Citation blob or excerpt hash is stale.")
        excerpt["ref"] = ref.model_copy(
            update={
                "blob_sha256": blob,
                "excerpt_sha256": excerpt["sha256"],
            }
        ).model_dump(mode="json")
        return excerpt

    def production_ref(self, ref):
        entry = next((f for f in self.repo.inventory.files if f.path == ref.path), None)
        return bool(entry and entry.role == "source")

    def operative_ref(self, ref):
        """Reject obvious comments/imports; exact citations still need semantic review."""
        if not self.production_ref(ref):
            return False
        self.validate_ref(ref)
        text = self.repo.read(ref.path)
        # Keep original line positions while excluding multiline prose, even when a
        # citation starts inside a comment/docstring. This is a lexical guard, not parsing.
        masked = re.sub(
            r'/\*.*?(?:\*/|$)|<!--.*?(?:-->|$)|("""|\x27\x27\x27).*?(?:\1|$)',
            lambda match: "\n" * match.group().count("\n"),
            text,
            flags=re.DOTALL,
        )
        import_depth = 0
        # An excerpt may begin inside a multiline import. Track its opening lines
        # before testing the cited slice, just as the prose mask preserves context.
        for number, line in enumerate(masked.splitlines(), 1):
            if number > ref.end_line:
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith(("//", "#", "/*", "*", "<!--", "--", ";")):
                continue
            if re.fullmatch(r"(['\"`]).*\1\s*;?", line):
                continue
            is_import = bool(re.match(r"^(import\b|from\b|using\b|use\b|export\s+[\{*])", line))
            is_import |= bool(re.match(r"^(const|let|var)\s+[^=]+\s*=\s*require\s*\(", line))
            if import_depth or is_import:
                import_depth = max(
                    0,
                    import_depth
                    + sum(line.count(c) for c in "({[")
                    - sum(line.count(c) for c in ")}]"),
                )
                continue
            if number < ref.start_line:
                continue
            if not line.strip("{}[]();, "):
                continue
            return True
        return False

    def validate_card(self, card):
        from app.selection_artifacts import feature_refs

        refs = feature_refs(card)
        if len(refs) > 24 or len({r.path for r in refs}) > 15:
            raise EvidenceError("Text trace exceeded its 24-reference/15-file limit.")
        if card.structural_validation != "text_only" or any(
            a.validation != "unvalidated"
            or a.parser_version
            or a.grammar_version
            or a.query_version
            for a in card.symbol_anchors
        ):
            raise EvidenceError("No registered parser validated this text-only trace.")
        for ref in (*refs, *(a.ref for a in card.symbol_anchors)):
            self.validate_ref(ref)
        if card.evidence_status == "supported" and not all(
            any(self.operative_ref(ref) for ref in group)
            for group in (card.entry_points, card.implementation)
        ):
            raise EvidenceError("Supported text capability requires operative production evidence.")
        entries = {f.path: f for f in self.repo.inventory.files}
        if any(entries[r.path].role != "test" for r in card.source_tests):
            raise EvidenceError("Source test citations must identify inventoried test text.")
        if any(
            r.path not in {n.path for n in self.repo.snapshot.license_refs}
            for r in card.license_refs
        ):
            raise EvidenceError("Notices must identify inventoried root license/notice files.")
        if card.license_spdx and not card.license_refs:
            raise EvidenceError("License identification requires cited notices.")
        for observation in card.dependencies:
            if (
                observation.kind == "inferred_relationship"
                and observation.resolution != "unresolved"
            ):
                raise EvidenceError("Inferred relationships must remain unresolved.")
            if observation.kind == "manifest_declaration" and any(
                entries[r.path].role != "manifest" for r in observation.refs
            ):
                raise EvidenceError("Manifest observations must cite a manifest.")

        # Bind typed references explicitly; a JSON shape walk cannot confer provenance.
        def bound(ref):
            return TextRefV2.model_validate(self.validate_ref(ref)["ref"])

        return card.model_copy(
            update={
                **{
                    name: tuple(bound(r) for r in getattr(card, name))
                    for name in (
                        "entry_points",
                        "implementation",
                        "source_tests",
                        "license_refs",
                    )
                },
                "dependencies": tuple(
                    d.model_copy(update={"refs": tuple(bound(r) for r in d.refs)})
                    for d in card.dependencies
                ),
                "symbol_anchors": tuple(
                    a.model_copy(update={"ref": bound(a.ref)}) for a in card.symbol_anchors
                ),
            }
        )

    def coverage(self, *, sampled_modules=(), frontier=()):
        inventory = self.repo.inventory
        paths = {f.path for f in inventory.files}
        windows = [*self.previous_read_ranges, *self.windows]
        examined = {w["path"] for w in windows}
        partial = []
        for entry in inventory.files:
            intervals = sorted(
                (w["start_line"], w["end_line"]) for w in windows if w["path"] == entry.path
            )
            if not intervals:
                continue
            cursor, unread = 1, []
            for start, end in intervals:
                if start > cursor:
                    unread.append({"start_line": cursor, "end_line": start - 1})
                cursor = max(cursor, end + 1)
            if cursor <= entry.lines:
                unread.append({"start_line": cursor, "end_line": entry.lines})
            if unread:
                partial.append({"path": entry.path, "unread_ranges": unread})
        return CoverageV2(
            tree_enumeration_complete=inventory.enumeration_complete,
            admitted_paths=sorted(paths),
            deferred_paths=inventory.deferred_paths,
            excluded_paths=inventory.excluded_paths,
            sampled_modules=sorted(set(sampled_modules)),
            examined_paths=sorted(examined),
            read_ranges=[
                {key: window[key] for key in ("path", "start_line", "end_line", "sha256")}
                for window in windows
            ],
            partially_read_files=partial,
            unexamined_areas=sorted(paths - examined),
            remaining_frontier=sorted(set(frontier) | set(inventory.deferred_paths)),
            limits_reached=sorted(self.limits | set(inventory.limits_reached)),
        )


class TextEvidenceTools(EvidenceTools):
    input_reservation = staticmethod(text_input_reservation)

    def __init__(self, evidence):
        super().__init__(evidence, ())
        self.registry = {
            "read_range": (
                ReadRangeV2,
                "Read up to 200 lines / 24,000 bytes at original repository paths.",
            ),
            "read_ranges": (
                ReadRangesV2,
                "Batch up to eight windows in one turn, at most 48,000 returned bytes; "
                "shares all session budgets.",
            ),
            "list_paths": (
                ListPaths,
                "List 80 admitted paths with roles/languages and line counts; offset pages.",
            ),
            "search_text": (
                TextSearch,
                "Literal search of admitted pinned text; returned hit lines are read evidence.",
            ),
        }

    def dispatch(self, name, raw):

        from pydantic import ValidationError

        if name not in self.registry or len(raw.encode()) > 4096:
            raise EvidenceError("Unregistered text tool or oversized arguments.")
        try:
            args = self.registry[name][0].model_validate_json(raw)
        except ValidationError:
            raise EvidenceError("Text tool arguments failed their strict schema.") from None
        if isinstance(args, ReadRangeV2):
            return self.evidence.read_range(args.path, args.start_line, args.end_line)
        if isinstance(args, ReadRangesV2):
            return self.evidence.read_ranges(args.ranges)
        if args.path_prefix:
            try:
                validate_repo_path(args.path_prefix)
            except ValueError:
                raise EvidenceError(
                    "Search/list prefix must be a normalized repository path."
                ) from None
        files = [
            f
            for f in self.evidence.repo.inventory.files
            if not args.path_prefix
            or f.path == args.path_prefix
            or f.path.startswith(args.path_prefix + "/")
        ]
        files.sort(key=lambda f: f.path)
        if isinstance(args, ListPaths):
            self.evidence.list_calls += 1
            if self.evidence.list_calls > 20:
                raise EvidenceError("Path-list request limit reached.")
            page = files[args.offset : args.offset + 80]
            return {
                "paths": [
                    f.model_dump(include={"path", "role", "language", "lines"}) for f in page
                ],
                "next_offset": args.offset + 80 if len(files) > args.offset + 80 else None,
            }
        assert isinstance(args, TextSearch)
        session = self.evidence
        if len(session.searches) >= 20 or session.search_bytes >= 16_000_000:
            session.limits.add("search_budget")
            raise EvidenceError("Text search query/byte budget exhausted.")
        started, consumed, hits, scanned = monotonic(), 0, [], []
        capped, next_line = False, 1
        for entry in files[args.offset : args.offset + 100]:
            if consumed + entry.bytes > 4_000_000 or monotonic() - started > 2:
                break
            if session.search_bytes + entry.bytes > 16_000_000:
                session.limits.add("search_budget")
                break
            consumed += entry.bytes
            session.search_bytes += entry.bytes
            scanned.append(entry.path)
            text = session.repo.read(entry.path, timeout=max(0.01, 2 - (monotonic() - started)))
            lines = text.splitlines()
            for line_number, line in enumerate(lines, 1):
                if monotonic() - started > 2:
                    capped, next_line = True, line_number
                    break
                if len(scanned) == 1 and line_number < args.start_line:
                    continue
                if args.query.casefold() not in line.casefold():
                    continue
                if len(line.encode()) + 1 > 24_000:
                    session.limits.add("oversized_search_line")
                    continue
                if sum(len(h["text"].encode()) for h in hits) + len(line.encode()) + 1 > 24_000:
                    capped, next_line = True, line_number
                    break
                try:
                    hits.append(session._record_range(entry.path, lines, line_number, line_number))
                except EvidenceError:
                    if not hits:
                        raise
                    capped, next_line = True, line_number
                    break
                if len(hits) >= 20:
                    capped, next_line = True, line_number + 1
                    break
            if capped:
                break
        receipt = {
            "query": args.query,
            "path_prefix": args.path_prefix,
            "offset": args.offset,
            "scanned_paths": scanned,
            "scanned_bytes": consumed,
            "hits": len(hits),
            "incomplete": capped or len(scanned) < len(files) - args.offset,
        }
        session.searches.append(receipt)
        # A result cap can stop inside a file; resume at that file, not after unread matches.
        receipt["next_offset"] = (
            args.offset + len(scanned) - (1 if capped else 0) if receipt["incomplete"] else None
        )
        receipt["next_start_line"] = next_line if capped else 1
        return {"matches": hits, **receipt}


def evidence_session(selected, repo):
    """Single profile dispatcher used by discovery and target comparison."""
    from app.project_adapters import python_facts
    from app.text_contracts import TEXT_PROFILE

    if selected.support.profile_version == TEXT_PROFILE:
        return TextEvidenceSession(repo, selected.id)
    indexes, errors = python_facts(repo)
    if errors:
        raise EvidenceError("Project failed current Python analysis preflight.")
    return EvidenceSession(repo, indexes=indexes)
