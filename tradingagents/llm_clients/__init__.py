from .base_client import BaseLLMClient
from .cache import enable_llm_cache
from .factory import create_llm_client

# Enable the SQLite cache once when this package is imported. This is a no-op
# if it's already set. Cache lookups are keyed on (prompt, model, params), so
# re-runs with identical prompts return instantly — most useful when the same
# ticker is analyzed twice or when a Reflector revisits prior decisions.
enable_llm_cache()

__all__ = ["BaseLLMClient", "create_llm_client", "enable_llm_cache"]
