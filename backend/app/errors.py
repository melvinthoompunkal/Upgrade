"""Public errors deliberately omit raw provider responses and credentials."""


class GraftError(Exception):
    code = "graft_error"


class ConfigurationError(GraftError):
    code = "missing_configuration"


class MetaRequestError(GraftError):
    code = "meta_request_failed"


class ModelOutputError(GraftError):
    code = "invalid_model_output"


class SmokeCheckError(GraftError):
    code = "smoke_check_failed"
