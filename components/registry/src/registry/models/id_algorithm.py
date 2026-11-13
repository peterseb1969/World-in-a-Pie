"""ID algorithm configuration and validation.

Defines per-namespace, per-entity-type ID strategies.

Generation algorithms (WIP mints the ID): uuid7, uuid4, prefixed, nanoid.
Validation-only algorithms (the CALLER must supply the entry_id; WIP will NOT
generate one): pattern, any. Configuring a validation-only algorithm for an
entity type whose IDs WIP is expected to mint fails at generation time with an
actionable error (see IdGenerator.generate).

Where caller IDs are checked against the config: /entries/reserve validates
each one. /entries/register does not: it is how a restore writes back the IDs
it backed up, and a namespace's entries keep the IDs minted under earlier
configs when its id_config changes. A prefixed ID written either way raises
its prefix's counter (services/id_generator.py), so it is never minted again.
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


_PREFIX_CHARS = re.compile(r"[A-Za-z0-9_.-]+")


class IdAlgorithmWrite(BaseModel):
    """An id_config entry as a namespace write accepts it.

    Strict where IdAlgorithmConfig is lenient. IdAlgorithmConfig reads what is
    already stored, and must keep reading legacy values instead of failing on
    them. A write refuses what would be misapplied or would mint unusable IDs:
    an unknown field (dropped on read), an unknown algorithm (fails every
    later mint), a prefixed config without a prefix (bare numbers that collide
    across entity types), a prefix with characters an ID cannot carry in a URL
    path or a qualified name, and pad or length values outside the ranges
    below. The ranges hold whatever the algorithm: a pad of 50 on a uuid7
    config is refused like on a prefixed one, while an in-range field of
    another algorithm is stored and has no effect. A config stored before
    these rules, re-sent as stored or restored from an archive, is accepted
    unchanged by validate_id_config_write, not by this model.
    """

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["uuid7", "uuid4", "prefixed", "nanoid", "pattern", "any"] = "uuid7"
    # Letters, digits, '_', '.', '-': a minted ID is a path segment
    # (GET /entries/{entry_id}) and must not contain '/', '#', '?', '%' or
    # the ':' of a qualified name.
    prefix: str | None = Field(default=None, max_length=32)
    # The zero-padding width of a prefixed ID. A wider value only builds a
    # longer string per mint.
    pad: int = Field(default=6, ge=0, le=20)
    # A nanoid's length: 64^8 IDs keeps a collision unlikely for millions of
    # mints (length 3 collides within a few hundred); 64 is ample.
    length: int = Field(default=21, ge=8, le=64)
    pattern: str | None = None

    @model_validator(mode="after")
    def algorithm_fields_present(self) -> "IdAlgorithmWrite":
        if self.algorithm == "prefixed" and not self.prefix:
            raise ValueError("algorithm 'prefixed' needs a non-empty prefix")
        if self.prefix and not _PREFIX_CHARS.fullmatch(self.prefix):
            raise ValueError(
                f"prefix {self.prefix!r} may contain only letters, digits, "
                "'_', '.' and '-'"
            )
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


def _as_read(config: dict[str, Any], entity_type: str) -> Any:
    """One entity type's config as a read serves it: defaults merged in, and
    an absent type as the platform default. A value that cannot be read is
    compared as it is."""
    value = config.get(entity_type)
    if value is None:
        return DEFAULT_ID_CONFIG.get(entity_type, IdAlgorithmConfig()).model_dump()
    if not isinstance(value, dict):
        return value
    try:
        return IdAlgorithmConfig(**value).model_dump()
    except (TypeError, ValueError):
        return value


def same_id_config(submitted: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Whether two configs mint the same IDs: equal per entity type once
    defaults are merged in, so a config read back (GET serves every entity
    type, defaults filled) and re-sent matches the stored one it came from."""
    return all(
        _as_read(submitted, t) == _as_read(stored, t) for t in set(submitted) | set(stored)
    )


def validate_id_config_write(
    submitted: dict[str, Any], stored: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The id_config to store for a namespace write, or pydantic.ValidationError.

    `stored` is the config the namespace already holds, or, for a namespace
    re-created from an archive, the archive's: either is a value the platform
    accepted when it was written. A config that mints the same IDs as it
    (re-sent as stored, or read back and re-sent) is returned as stored, even
    if it predates this validation: refusing it would break a bootstrap
    resume, a restore or a reserved app's upsert for a namespace that already
    holds the value. The same rule grandfathers the prefix-uniqueness check
    (services/id_prefixes.claim_for_write). Anything else is validated, and
    returned with only the keys it was submitted with.
    """
    if stored is not None and same_id_config(submitted, stored):
        return stored
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
            # The canonical spelling only: IDs are looked up by exact string,
            # so 'ABC…', '{…}' or 'urn:uuid:…' would be reservable and never
            # found. Any UUID version passes: apps reserve their own v4 IDs
            # under the default uuid7 config.
            try:
                return str(uuid.UUID(id_string)) == id_string
            except ValueError:
                return False

        elif algo == "prefixed":
            # The prefix, then digits: at least one, and possibly more than
            # pad, which the generator emits once the counter outgrows it, and
            # which an ID minted under an earlier pad may hold.
            prefix = config.prefix or ""
            pat = re.compile(rf"{re.escape(prefix)}\d+")
            return bool(pat.fullmatch(id_string))

        elif algo == "nanoid":
            pat = re.compile(rf"[A-Za-z0-9_-]{{{config.length}}}")
            return bool(pat.fullmatch(id_string))

        elif algo == "pattern":
            if not config.pattern:
                return True
            return bool(re.fullmatch(config.pattern, id_string))

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
