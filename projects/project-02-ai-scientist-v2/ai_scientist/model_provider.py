"""Select the model client without importing SDKs or constructing clients."""

DEEPSEEK_MODELS = (
    "deepseek-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
)


def llm_provider(model: str) -> str | None:
    if model in DEEPSEEK_MODELS:
        return "deepseek_compatible"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("bedrock") and "claude" in model:
        return "bedrock"
    if model.startswith("vertex_ai") and "claude" in model:
        return "vertex"
    if model.startswith("ollama/"):
        return "ollama"
    if any(marker in model for marker in ("gpt", "o1", "o3")):
        return "openai"
    if model == "deepseek-coder-v2-0724":
        return "deepseek"
    if model == "deepcoder-14b":
        return "huggingface"
    if model == "llama3.1-405b":
        return "openrouter"
    if "gemini" in model:
        return "gemini"
    return None


def vision_provider(model: str) -> str | None:
    if model in (
        "gpt-4o-2024-05-13",
        "gpt-4o-2024-08-06",
        "gpt-4o-2024-11-20",
        "gpt-4o-mini-2024-07-18",
        "o3-mini",
    ):
        return "openai"
    if model.startswith("ollama/"):
        return "ollama"
    return None
