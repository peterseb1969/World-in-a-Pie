"""ID algorithm configuration and validation.

Defines per-namespace, per-entity-type ID strategies.

Generation algorithms (WIP mints the ID): uuid7, uuid4, prefixed, nanoid.
Validation-only algorithms (the CALLER must supply the entry_id; WIP validates
it against the config but will NOT generate one): pattern, any. Configuring a
validation-only algorithm for an entity type whose IDs WIP is expected to mint
fails at generation time with an actionable error (see IdGenerator.generate).
"""

import re
import secrets
import string
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class IdAlgorithmConfig(BaseModel):
    """Configuration for ID generation within a namespace for a specific entity type."""

    algorithm: str = Field(
        default="uuid7",
        description="ID algorithm. Generation (WIP mints the ID): uuid7, uuid4, prefixed, nanoid. Validation-only (caller must supply entry_id; WIP won't generate): pattern, any."
    )
    prefix: str | None = Field(
        default=None,
        description="Prefix for 'prefixed' algorithm (e.g., 'TERM-')"
    )
    pad: int = Field(
        default=6,
        description="Zero-padding width for 'prefixed' algorithm"
    )
    length: int = Field(
        default=21,
        description="Character length for 'nanoid' algorithm"
    )
    pattern: str | None = Field(
        default=None,
        description="Regex pattern for 'pattern' algorithm validation"
    )


# Default: UUID7 for all entity types
DEFAULT_ID_CONFIG: dict[str, IdAlgorithmConfig] = {
    "terminologies": IdAlgorithmConfig(algorithm="uuid7"),
    "terms": IdAlgorithmConfig(algorithm="uuid7"),
    "templates": IdAlgorithmConfig(algorithm="uuid7"),
    "documents": IdAlgorithmConfig(algorithm="uuid7"),
    "files": IdAlgorithmConfig(algorithm="uuid7"),
}

VALID_ENTITY_TYPES = {"terminologies", "terms", "templates", "documents", "files"}

IdEntityType = Literal["terminologies", "terms", "templates", "documents", "files"]


class IdAlgorithmWrite(BaseModel):
    """An id_config entry as a namespace write accepts it.

    Strict where IdAlgorithmConfig is lenient. IdAlgorithmConfig reads what is
    already stored, and must keep reading legacy values instead of failing on
    them. A write must refuse anything that would be stored and then ignored
    or misapplied: an unknown field is dropped on read, an unknown algorithm
    fails every later mint, and a prefixed config without a prefix mints bare
    numbers that collide across entity types.
    """

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["uuid7", "uuid4", "prefixed", "nanoid", "pattern", "any"] = "uuid7"
    prefix: str | None = None
    pad: int = Field(default=6, ge=0)
    length: int = Field(default=21, ge=1)
    pattern: str | None = None

    @model_validator(mode="after")
    def algorithm_fields_present(self) -> "IdAlgorithmWrite":
        if self.algorithm == "prefixed" and not self.prefix:
            raise ValueError("algorithm 'prefixed' needs a non-empty prefix")
        if self.algorithm == "pattern":
            if not self.pattern:
                raise ValueError("algorithm 'pattern' needs a pattern")
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"pattern is not a valid regular expression: {e}") from e
        return self


# What a namespace write accepts as id_config: only the five entity types.
IdConfigWrite = dict[IdEntityType, IdAlgorithmWrite]
_ID_CONFIG_WRITE: TypeAdapter[IdConfigWrite] = TypeAdapter(IdConfigWrite)


def validate_id_config_write(
    submitted: dict[str, Any], stored: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The id_config to store for a namespace write, or pydantic.ValidationError.

    A config re-sent exactly as stored (a bootstrap resume, a same-name
    restore) is returned unchanged, even if it predates this validation:
    refusing it would break those writes for a namespace that already
    holds the value. The same rule grandfathers the prefix-uniqueness
    check (services/id_prefixes.claim_for_write). Anything else is
    validated, and returned with only the keys it was submitted with.
    """
    if stored is not None and submitted == stored:
        return submitted
    validated: dict[str, Any] = _ID_CONFIG_WRITE.dump_python(
        _ID_CONFIG_WRITE.validate_python(submitted), exclude_unset=True
    )
    return validated


class IdFormatValidator:
    """Validates IDs against a configured algorithm format."""

    @staticmethod
    def validate(id_string: str, config: IdAlgorithmConfig) -> bool:
        """Check if id_string matches the expected format for config."""
        algo = config.algorithm

        if algo in ("uuid7", "uuid4"):
            try:
                uuid.UUID(id_string)
                return True
            except ValueError:
                return False

        elif algo == "prefixed":
            prefix = config.prefix or ""
            pat = re.compile(rf"^{re.escape(prefix)}\d{{{config.pad}}}$")
            return bool(pat.match(id_string))

        elif algo == "nanoid":
            pat = re.compile(rf"^[A-Za-z0-9_-]{{{config.length}}}$")
            return bool(pat.match(id_string))

        elif algo == "pattern":
            if not config.pattern:
                return True
            return bool(re.match(config.pattern, id_string))

        elif algo == "any":
            return bool(id_string)

        return False


class IdGenerator:
    """Generates IDs based on algorithm configuration.

    For prefixed IDs, requires an external counter (IdCounter).
    For UUID/nanoid, generates locally without external dependencies.
    """

    @staticmethod
    def generate_uuid4() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def generate_uuid7() -> str:
        """Generate a UUID7 (time-ordered)."""
        # The embedded timestamp must be true UTC epoch millis. A naive
        # utcnow() here would be reinterpreted as LOCAL time by
        # .timestamp(), skewing the time-ordering bits by the host's UTC
        # offset on any non-UTC machine — silently breaking cross-host
        # ordering of minted IDs.
        timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
        time_bytes = timestamp_ms.to_bytes(6, byteorder="big")
        random_bytes = secrets.token_bytes(10)

        uuid_bytes = bytearray(16)
        uuid_bytes[0:6] = time_bytes
        uuid_bytes[6] = 0x70 | (random_bytes[0] & 0x0F)
        uuid_bytes[7] = random_bytes[1]
        uuid_bytes[8] = 0x80 | (random_bytes[2] & 0x3F)
        uuid_bytes[9:16] = random_bytes[3:10]

        hex_str = uuid_bytes.hex()
        return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"

    @staticmethod
    def generate_nanoid(length: int = 21) -> str:
        alphabet = string.ascii_letters + string.digits + "_-"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def generate_prefixed(prefix: str, seq: int, pad: int = 6) -> str:
        return f"{prefix}{seq:0{pad}d}"

    @classmethod
    def generate(cls, config: IdAlgorithmConfig, seq: int | None = None) -> str:
        """Generate an ID based on config. For prefixed, seq must be provided."""
        algo = config.algorithm

        if algo == "uuid7":
            return cls.generate_uuid7()
        elif algo == "uuid4":
            return cls.generate_uuid4()
        elif algo == "nanoid":
            return cls.generate_nanoid(config.length)
        elif algo == "prefixed":
            if seq is None:
                raise ValueError("Sequence number required for prefixed algorithm")
            return cls.generate_prefixed(config.prefix or "", seq, config.pad)
        else:
            raise ValueError(
                f"Algorithm '{algo}' is validation-only — WIP does not generate IDs "
                f"for it. The caller must supply an explicit entry_id, or the "
                f"namespace must configure a generation algorithm for this entity "
                f"type (uuid7, uuid4, prefixed, nanoid)."
            )
