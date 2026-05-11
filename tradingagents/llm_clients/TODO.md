# LLM Clients - Consistency Improvements

All known issues fixed.

## History

### 1. ~~`validate_model()` is never called~~ (Fixed)
- `BaseLLMClient.warn_if_unknown_model()` is invoked at the top of each
  client's `get_llm()`; emits a `RuntimeWarning` for unknown models and
  continues. openrouter/ollama always pass.

### 2. ~~Inconsistent parameter handling~~ (Fixed)
- GoogleClient now accepts unified `api_key` and maps it to `google_api_key`

### 3. ~~`base_url` accepted but ignored~~ (Fixed)
- All clients now pass `base_url` to their respective LLM constructors

### 4. ~~Update validators.py with models from CLI~~ (Fixed)
- Synced in v0.2.2
