"""Vulture allowlist — false positives from FastAPI DI, Beanie ODM, and framework patterns."""

# FastAPI dependency injection — these are used via Depends() but vulture can't trace that
from wip_auth.auth import require_api_key, get_current_user, require_role  # noqa
require_api_key
get_current_user
require_role

# Beanie document models — __init_subclass__, Settings inner class, etc.
# These are used by the ODM framework at runtime
_.Settings  # type: ignore
_.Collection  # type: ignore

# FastAPI lifespan / startup / shutdown hooks
_.lifespan  # type: ignore

# Pydantic model_config / model_validator / field_validator
_.model_config  # type: ignore
_.model_validator  # type: ignore
_.field_validator  # type: ignore

# FastAPI router callbacks referenced by string or decorator
_.on_event  # type: ignore
_.middleware  # type: ignore

# Legacy AuthService.initialize(master_key=...) — kept for backward compat, callers pass this kwarg
_.master_key  # type: ignore
