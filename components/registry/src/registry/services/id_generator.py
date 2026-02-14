"""ID generation service using IdAlgorithmConfig."""

from ..models.id_algorithm import IdAlgorithmConfig, IdGenerator
from ..models.id_counter import IdCounter
from ..models.namespace import Namespace


class IdGeneratorService:
    """Service for generating IDs based on namespace configuration."""

    @classmethod
    async def generate(cls, namespace: str, entity_type: str) -> str:
        """Generate an ID for the given namespace and entity type.

        Looks up the namespace's ID config and generates accordingly.
        For prefixed algorithms, uses atomic MongoDB counter.
        For UUID/nanoid, generates locally.
        """
        ns = await Namespace.find_one({"prefix": namespace, "status": "active"})
        if not ns:
            # Default to UUID7 if namespace not found
            return IdGenerator.generate_uuid7()

        config = ns.get_id_algorithm(entity_type)
        return await cls.generate_from_config(config, namespace, entity_type)

    @classmethod
    async def generate_from_config(
        cls, config: IdAlgorithmConfig, namespace: str, entity_type: str
    ) -> str:
        """Generate an ID from a specific config."""
        if config.algorithm == "prefixed":
            counter_key = f"{namespace}:{entity_type}:{config.prefix or ''}"
            seq = await IdCounter.next_val(counter_key)
            return IdGenerator.generate_prefixed(config.prefix or "", seq, config.pad)
        else:
            return IdGenerator.generate(config)
