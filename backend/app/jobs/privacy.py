"""Model transmission policy: reject likely secrets before any provider attempt."""

import json
import re

from app.errors import GraftError
from app.jobs.control import CURRENT

POLICY = "meta-private-excerpts-v1"
SENSITIVE = re.compile(
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})\b|"
    r"(?i:(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    r"\s*[=:]\s*[\"'][^\"'\s]{12,}[\"'])"
)


class SensitiveContent(GraftError):
    code = "sensitive_content"


class PrivateConsentRequired(GraftError):
    code = "private_code_consent_required"


def check_transmission(messages):
    context = CURRENT.get()
    if context and context.private_identity:
        context.check()
        with context.store.connect() as db:
            consent = db.execute(
                "SELECT 1 FROM model_consents WHERE owner_id=? AND identity=? AND policy=?",
                (context.owner, context.private_identity, POLICY),
            ).fetchone()
        if not consent:
            raise PrivateConsentRequired(
                "Review and accept the private-code disclosure before analysis."
            )

    def strings(value):
        if isinstance(value, str):
            yield value
            if value.lstrip().startswith(("{", "[")):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    pass
                else:
                    yield from strings(decoded)
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from strings(item)

    if any(SENSITIVE.search(text) for text in strings(messages)):
        raise SensitiveContent("Potential sensitive content was excluded from model transmission.")
