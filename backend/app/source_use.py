"""Exact, bounded source-use profiles; complete original notices travel with tested patches.

BSD text: https://spdx.org/licenses/BSD-2-Clause.html. Variants remain unsupported.
"""

from app.errors import EvidenceError

SOURCE_USE_PROFILES = {"MIT": "standard-mit-v1", "BSD-2-Clause": "standard-bsd-2-clause-v1"}
BSD_BODY = """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this
list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""


def validate_notice(text, spdx, mit_body):
    body = {"MIT": mit_body, "BSD-2-Clause": BSD_BODY}.get(spdx)
    if body is None:
        raise EvidenceError("Source license has no reviewed reuse profile.")
    marker = (
        "Redistribution and use in source and binary forms"
        if spdx == "BSD-2-Clause"
        else ("Permission is hereby granted")
    )
    if marker not in text:
        raise EvidenceError("Source has additional unreviewed notice obligations.")
    heading, rest = text.split(marker, 1)
    if "copyright" not in heading.lower() or " ".join((marker + rest).split()) != " ".join(
        body.split()
    ):
        raise EvidenceError("Source license differs from the reviewed standard grant.")
