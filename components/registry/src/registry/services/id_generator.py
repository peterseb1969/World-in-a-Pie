"""ID generation service with pluggable strategies."""

import secrets
import string
import uuid
from datetime import datetime
from typing import Optional

from ..models.id_pool import IdGeneratorConfig, IdGeneratorType


class IdGeneratorService:
    """Service for generating IDs based on pool configuration."""

    # Counter for prefixed IDs (in production, use Redis or similar for distributed counter)
    _counters: dict[str, int] = {}

    @classmethod
    def generate(cls, config: IdGeneratorConfig, pool_id: str = "default") -> str:
        """
        Generate an ID based on the provided configuration.

        Args:
            config: ID generator configuration
            pool_id: Pool identifier (used for counter keys)

        Returns:
            Generated ID string
        """
        generator_type = config.type
        if isinstance(generator_type, str):
            generator_type = IdGeneratorType(generator_type)

        if generator_type == IdGeneratorType.UUID4:
            return cls._generate_uuid4()
        elif generator_type == IdGeneratorType.UUID7:
            return cls._generate_uuid7()
        elif generator_type == IdGeneratorType.NANOID:
            return cls._generate_nanoid(config.length)
        elif generator_type == IdGeneratorType.PREFIXED:
            return cls._generate_prefixed(config.prefix or "", pool_id)
        elif generator_type == IdGeneratorType.EXTERNAL:
            raise ValueError("External IDs must be provided by the caller, not generated")
        elif generator_type == IdGeneratorType.CUSTOM:
            return cls._generate_custom(config.pattern or "")
        else:
            # Default to UUID4
            return cls._generate_uuid4()

    @staticmethod
    def _generate_uuid4() -> str:
        """Generate a UUID4 (random)."""
        return str(uuid.uuid4())

    @staticmethod
    def _generate_uuid7() -> str:
        """
        Generate a UUID7 (time-ordered).

        UUID7 format: timestamp (48 bits) + random (74 bits)
        This provides time-sortable IDs while maintaining uniqueness.
        """
        # Get current timestamp in milliseconds
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)

        # UUID7 structure:
        # - 48 bits: Unix timestamp in milliseconds
        # - 4 bits: version (7)
        # - 12 bits: random
        # - 2 bits: variant (10)
        # - 62 bits: random

        # Create the timestamp portion (48 bits)
        time_bytes = timestamp_ms.to_bytes(6, byteorder='big')

        # Generate random bytes for the rest
        random_bytes = secrets.token_bytes(10)

        # Construct the UUID bytes
        uuid_bytes = bytearray(16)

        # First 6 bytes: timestamp
        uuid_bytes[0:6] = time_bytes

        # Bytes 6-7: version (7) in high nibble, then random
        uuid_bytes[6] = 0x70 | (random_bytes[0] & 0x0F)
        uuid_bytes[7] = random_bytes[1]

        # Bytes 8-15: variant (10) in high 2 bits, then random
        uuid_bytes[8] = 0x80 | (random_bytes[2] & 0x3F)
        uuid_bytes[9:16] = random_bytes[3:10]

        # Convert to UUID string
        hex_str = uuid_bytes.hex()
        return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"

    @staticmethod
    def _generate_nanoid(length: int = 21) -> str:
        """
        Generate a nanoid-style ID.

        Uses URL-safe characters: A-Za-z0-9_-
        """
        alphabet = string.ascii_letters + string.digits + "_-"
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    @classmethod
    def _generate_prefixed(cls, prefix: str, pool_id: str) -> str:
        """
        Generate a prefixed ID with sequential number.

        Format: PREFIX-NNNNNN (e.g., TERM-000001)
        """
        counter_key = f"{pool_id}:{prefix}"

        # Increment counter (thread-safe in single process, needs Redis for distributed)
        if counter_key not in cls._counters:
            cls._counters[counter_key] = 0
        cls._counters[counter_key] += 1

        # Format with 6 digits, zero-padded
        return f"{prefix}{cls._counters[counter_key]:06d}"

    @staticmethod
    def _generate_custom(pattern: str) -> str:
        """
        Generate an ID based on a custom pattern.

        Pattern tokens:
        - {uuid4}: Random UUID4
        - {timestamp}: Unix timestamp
        - {random:N}: N random alphanumeric characters
        - {seq:N}: N-digit sequential number (not implemented here)
        """
        import re

        result = pattern

        # Replace {uuid4}
        while "{uuid4}" in result:
            result = result.replace("{uuid4}", str(uuid.uuid4()), 1)

        # Replace {timestamp}
        result = result.replace("{timestamp}", str(int(datetime.utcnow().timestamp())))

        # Replace {random:N}
        random_pattern = re.compile(r'\{random:(\d+)\}')
        for match in random_pattern.finditer(pattern):
            length = int(match.group(1))
            random_str = ''.join(
                secrets.choice(string.ascii_letters + string.digits)
                for _ in range(length)
            )
            result = result.replace(match.group(0), random_str, 1)

        return result

    @classmethod
    def reset_counters(cls) -> None:
        """Reset all counters (useful for testing)."""
        cls._counters.clear()

    @classmethod
    def set_counter(cls, pool_id: str, prefix: str, value: int) -> None:
        """Set a counter value (useful for initialization from database)."""
        counter_key = f"{pool_id}:{prefix}"
        cls._counters[counter_key] = value
