"""Semantic comparison over role-bound text evidence; no implementation-fit scores."""

import json
import re

from app.catalog_contracts import EvidenceExcerpt
from app.catalog_discovery import is_test
from app.contracts import Ref
from app.errors import EvidenceError
from app.evidence_tools import EvidenceTools, collect_evidence
from app.relevance_contracts import ComparisonJudgment, FeatureAssessment
from app.selection_artifacts import feature_refs
from app.text_contracts import TextRefV2
from app.text_discovery import BOUNDARY_PROMPT, overview, text_discovery_paths, text_packet
from app.text_evidence import TextEvidenceSession, TextEvidenceTools, text_input_reservation

PROMPT = (
    "Assess exactly the supplied source behavior against the exact goal and optional target. "
    "Return SemanticJudgmentV2. Relevance 0..5 means unrelated/unknown, weak, indirect, useful, "
    "strong, central. Compare behavior/architectural roles even across languages. "
    "Positive relevance requires read-backed production source code. A known target outcome "
    "requires relevant read-backed target production code. With insufficient target evidence "
    "use unknown; source usefulness to the goal may still be assessed without integration claims. "
    "Use source_/target_ tools to search behavior terms/import text and retrieve counterparts "
    "beyond filename matches and file heads. already_present means examined target code has "
    "the behavior; potential_gap is a bounded opportunity, never proof of repository-wide absence. "
    "Use unknown for insufficient evidence and no_target with empty target_refs for goal-only "
    "analysis. For relevance>=3 and potential_gap provide an expansion: existing behavior, "
    "proposed extension, benefit, 1..4 concrete existing-code integration points, constraints, "
    "at least one uncertainty and exact production source/target references. Otherwise expansion "
    "must be null. Cross-language transfer must be labeled conceptual_cross_language; it does "
    "not establish an executable port. Fit, stack/dependency compatibility, acceptance and "
    "adaptation remain unassessed; do not invent scores or execution evidence. "
)


class ComparisonTools(EvidenceTools):
    input_reservation = staticmethod(text_input_reservation)

    def __init__(self, source, target):
        super().__init__(source, ())
        self.routes, self.registry = {}, {}
        for role, session in (("source", source), ("target", target)):
            if session is None:
                continue
            tools = (
                TextEvidenceTools(session)
                if isinstance(session, TextEvidenceSession)
                else EvidenceTools(session, tuple(f.path for f in session.repo.inventory.files))
            )
            for name, definition in tools.registry.items():
                self.routes[f"{role}_{name}"] = (tools, name)
                self.registry[f"{role}_{name}"] = definition

    def dispatch(self, name, raw):
        if name not in self.routes:
            raise EvidenceError("Unregistered semantic comparison tool.")
        tools, original = self.routes[name]
        return tools.dispatch(original, raw)


def text_target_context(target, goal):
    if target is None:
        return []
    if not isinstance(target, TextEvidenceSession):
        from app.relevance_analysis import target_context

        return target_context(target, goal)
    terms = set(re.findall(r"[a-z0-9_]{3,}", goal.casefold()))
    paths = text_discovery_paths(target.repo, "")
    paths.sort(key=lambda path: -sum(term in path.casefold() for term in terms))
    return {"overview": overview(target), "packet": text_packet(target, paths[:4])}


def production(session, ref):
    if isinstance(session, TextEvidenceSession):
        return session.operative_ref(ref)
    return ref.path.endswith(".py") and not is_test(ref.path)


def validate_reference(session, ref):
    from app.text_contracts import TextRefV2

    if not isinstance(session, TextEvidenceSession) and isinstance(ref, TextRefV2):
        raise EvidenceError(
            "Legacy Python sessions require legacy references; no v2 identity was validated."
        )
    return session.validate_ref(ref)


async def assess_text_feature(
    client, source, target, feature, view, target_packet, *, execution_supported
):
    refs = {
        (ref.path, ref.start_line, ref.end_line): ref
        for ref in [
            *feature.discovery_refs,
            *(feature_refs(feature.card) if feature.card else ()),
        ]
    }
    excerpts = []
    for ref in list(refs.values())[:24]:
        source.read_range(ref.path, ref.start_line, ref.end_line)
        excerpts.append(source.validate_ref(ref))
    tools = ComparisonTools(source, target)
    source_ref_type = TextRefV2 if isinstance(source, TextEvidenceSession) else Ref
    target_ref_type = (
        TextRefV2 if target is None or isinstance(target, TextEvidenceSession) else Ref
    )
    judgment = await collect_evidence(
        client,
        tools,
        [
            {"role": "developer", "content": BOUNDARY_PROMPT + PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": view.goal,
                        "candidate": {
                            "feature_id": feature.feature_id,
                            "name": feature.name,
                            "summary": feature.summary,
                            "evidence_status": feature.evidence_status,
                            "behavior": feature.card.behavior if feature.card else feature.summary,
                            "acceptance_behaviors": list(feature.card.acceptance_behaviors)
                            if feature.card
                            else [],
                            "missing_evidence": feature.missing_evidence,
                        },
                        "source": source.repo.snapshot.model_dump(mode="json"),
                        "source_excerpts": excerpts,
                        "source_overview": overview(source)
                        if isinstance(source, TextEvidenceSession)
                        else None,
                        "target": target.repo.snapshot.model_dump(mode="json") if target else None,
                        "target_packet": target_packet,
                        "session_allowances": {
                            "source": {"files": 64, "bytes": 500_000},
                            "target": {"files": 64, "bytes": 500_000} if target else None,
                        },
                    }
                ),
            },
        ],
        ComparisonJudgment[source_ref_type, target_ref_type],
        stage="relevance.text_assess",
        turns=6,
        output_tokens=8000,
    )
    if judgment.feature_id != feature.feature_id:
        raise EvidenceError("Semantic judgment changed the admitted feature identity.")
    for ref in judgment.source_refs:
        validate_reference(source, ref)
    if judgment.relevance and not any(production(source, r) for r in judgment.source_refs):
        raise EvidenceError("Positive semantic relevance requires production source evidence.")
    if target:
        if judgment.target_status == "no_target":
            raise EvidenceError("A pinned target cannot be assessed as no_target.")
        for ref in judgment.target_refs:
            validate_reference(target, ref)
        if judgment.target_status != "unknown" and not any(
            production(target, r) for r in judgment.target_refs
        ):
            raise EvidenceError("Target outcome requires production target evidence.")
    elif judgment.target_refs or judgment.target_status != "no_target":
        raise EvidenceError("Goal-only relevance cannot claim target evidence.")
    expected = bool(
        target and judgment.relevance >= 3 and judgment.target_status == "potential_gap"
    )
    if bool(judgment.expansion) != expected:
        raise EvidenceError(
            "Useful potential gaps require a cited expansion; other outcomes cannot claim one."
        )
    export_refs = [*judgment.source_refs, *judgment.target_refs]
    if judgment.expansion:
        expansion = judgment.expansion
        target_refs = [
            *expansion.target_refs,
            *(p.target_ref for p in expansion.integration_points),
        ]
        for session, group in ((source, expansion.source_refs), (target, target_refs)):
            for ref in group:
                validate_reference(session, ref)
                if not production(session, ref):
                    raise EvidenceError("Expansion locations must cite operative production text.")
        source_languages = {
            f.language.casefold()
            for f in source.repo.inventory.files
            if f.path in {r.path for r in expansion.source_refs}
        }
        target_languages = {
            f.language.casefold()
            for f in target.repo.inventory.files
            if f.path in {r.path for r in target_refs}
        }
        if (
            source_languages.isdisjoint(target_languages)
            and expansion.transfer_mode != "conceptual_cross_language"
        ):
            raise EvidenceError("Cross-language transfer must be explicitly conceptual.")
        export_refs += [*expansion.source_refs, *target_refs]

    def bound(ref):
        session = source if ref.repo_id == source.repo.snapshot.repo_id else target
        if session is None:
            raise EvidenceError("Unknown citation role.")
        return type(ref).model_validate(validate_reference(session, ref)["ref"])

    expansion = judgment.expansion
    if expansion:
        expansion = expansion.model_copy(
            update={
                "source_refs": tuple(bound(r) for r in expansion.source_refs),
                "target_refs": tuple(bound(r) for r in expansion.target_refs),
                "integration_points": tuple(
                    point.model_copy(update={"target_ref": bound(point.target_ref)})
                    for point in expansion.integration_points
                ),
            }
        )
    judgment = judgment.model_copy(
        update={
            "source_refs": tuple(bound(r) for r in judgment.source_refs),
            "target_refs": tuple(bound(r) for r in judgment.target_refs),
            "expansion": expansion,
        }
    )
    result = FeatureAssessment(
        schema_version="feature-assessment-v2",
        feature_id=feature.feature_id,
        relevance_status="relevant" if judgment.relevance >= 3 else "less_relevant",
        relevance_score=judgment.relevance,
        reason=judgment.reason,
        target_status=judgment.target_status,
        judgment=judgment,
        expansion=judgment.expansion,
        adaptation_reason=(
            "Generic semantic analysis has no reviewed adaptation profile; "
            "implementation fit is unassessed."
        ),
    )
    size = 0
    for ref in dict.fromkeys(export_refs):
        session = source if ref.repo_id == source.repo.snapshot.repo_id else target
        excerpt = EvidenceExcerpt.model_validate(session.validate_ref(ref))
        size += len(excerpt.text.encode())
        if len(result.evidence) >= 24 or size > 60_000:
            result.evidence_limit_reached = True
            break
        result.evidence.append(excerpt)
    return result
