"""Operator-only paid diagnostics; configuration is never editable through the browser."""


def operator_allowed(store, owner, settings):
    if settings.auth_mode == "local":
        return owner == "local-workspace"
    with store.connect() as db:
        row = db.execute("SELECT github_id FROM users WHERE id=?", (owner,)).fetchone()
    return bool(row and row[0] in settings.operator_github_ids)
