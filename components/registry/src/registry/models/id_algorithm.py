"""ID algorithm configuration and validation.

Defines per-namespace, per-entity-type ID generation strategies.
Supported algorithms: uuid7, uuid4, prefixed, nanoid, pattern, any.
"""

import re
import secrets
import string
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class IdAlgorithmConfig(BaseModel):
    """Configuration for ID generation within a namespace for a specific entity type."""

    algorithm: str = Field(
        default="uuid7",
        description="ID generation algorithm: uuid7, uuid4, prefixed, nanoid, pattern, any"
    )
    prefix: str | None = Field(
        None,
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
        None,
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
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
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
            raise ValueError(f"Cannot generate IDs for algorithm: {algo}")
