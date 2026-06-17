MODELS = [
    {"id": "gateway-claude-opus-4-7",        "name": "Claude Opus 4.7",    "provider": "anthropic", "tier": "flagship"},
    {"id": "gateway-claude-opus-4-6",        "name": "Claude Opus 4.6",    "provider": "anthropic", "tier": "flagship"},
    {"id": "gateway-claude-opus-4-5",        "name": "Claude Opus 4.5",    "provider": "anthropic", "tier": "flagship"},
    {"id": "gateway-claude-opus-4-1",        "name": "Claude Opus 4.1",    "provider": "anthropic", "tier": "standard"},
    {"id": "gateway-claude-sonnet-4",        "name": "Claude Sonnet 4",    "provider": "anthropic", "tier": "standard"},
    {"id": "gateway-claude-sonnet-4-6",      "name": "Claude Sonnet 4.6",  "provider": "anthropic", "tier": "standard"},
    {"id": "gateway-gpt-5",                  "name": "GPT-5",              "provider": "openai",    "tier": "flagship"},
    {"id": "gateway-gpt-5-1",                "name": "GPT-5.1",            "provider": "openai",    "tier": "flagship"},
    {"id": "gateway-gpt-5-3",                "name": "GPT-5.3",            "provider": "openai",    "tier": "flagship"},
    {"id": "gateway-gpt-5-4",                "name": "GPT-5.4",            "provider": "openai",    "tier": "flagship"},
    {"id": "gateway-gpt-5-5",                "name": "GPT-5.5",            "provider": "openai",    "tier": "flagship"},
    {"id": "gateway-gpt-o3",                 "name": "o3",                 "provider": "openai",    "tier": "reasoning"},
    {"id": "gateway-gpt-o3-mini",            "name": "o3 Mini",            "provider": "openai",    "tier": "reasoning"},
    {"id": "gateway-gpt-o4-mini",            "name": "o4-mini",            "provider": "openai",    "tier": "reasoning"},
    {"id": "gateway-gpt-4o",                 "name": "GPT-4o",             "provider": "openai",    "tier": "standard"},
    {"id": "gateway-gpt-4-1-mini",           "name": "GPT-4.1 Mini",       "provider": "openai",    "tier": "fast"},
    {"id": "gateway-gpt-4-1-nano",           "name": "GPT-4.1 Nano",       "provider": "openai",    "tier": "fast"},
    {"id": "gateway-gpt-5-mini",             "name": "GPT-5 Mini",         "provider": "openai",    "tier": "fast"},
    {"id": "gateway-gpt-5-nano",             "name": "GPT-5 Nano",         "provider": "openai",    "tier": "fast"},
    {"id": "gateway-gpt-5-online",           "name": "GPT-5 Online",       "provider": "openai",    "tier": "standard"},
    {"id": "gateway-google-2.5-pro",         "name": "Gemini 2.5 Pro",     "provider": "google",    "tier": "flagship"},
    {"id": "gateway-gemini-3-pro",           "name": "Gemini 3 Pro",       "provider": "google",    "tier": "flagship"},
    {"id": "gateway-gemini-3-1-pro",         "name": "Gemini 3.1 Pro",     "provider": "google",    "tier": "flagship"},
    {"id": "gateway-gemini-2.5-flash",       "name": "Gemini 2.5 Flash",   "provider": "google",    "tier": "fast"},
    {"id": "gateway-deepseek-v4-pro",        "name": "DeepSeek V4 Pro",    "provider": "deepseek",  "tier": "flagship"},
    {"id": "gateway-deepseek-v4-flash",      "name": "DeepSeek V4 Flash",  "provider": "deepseek",  "tier": "fast"},
    {"id": "gateway-deepseek-r1",            "name": "DeepSeek R1",        "provider": "deepseek",  "tier": "reasoning"},
    {"id": "gateway-deepseek-v3",            "name": "DeepSeek V3",        "provider": "deepseek",  "tier": "standard"},
    {"id": "gateway-grok-4",                 "name": "Grok 4",             "provider": "xai",       "tier": "flagship"},
    {"id": "gateway-grok-3",                 "name": "Grok 3",             "provider": "xai",       "tier": "standard"},
    {"id": "gateway-qwen-3-max",             "name": "Qwen 3 Max",         "provider": "alibaba",   "tier": "standard"},
    {"id": "gateway-qwen-qwq-32b",           "name": "Qwen QwQ 32B",       "provider": "alibaba",   "tier": "reasoning"},
    {"id": "gateway-deepinfra-kimi-k2",      "name": "Kimi K2",            "provider": "moonshot",  "tier": "standard"},
    {"id": "gateway-llama-3-3-70b-versatile","name": "Llama 3.3 70B",      "provider": "meta",      "tier": "standard"},
]

PROVIDERS = {
    "anthropic": {"name": "Anthropic", "color": "dark_orange"},
    "openai":    {"name": "OpenAI",    "color": "green"},
    "google":    {"name": "Google",    "color": "blue"},
    "deepseek":  {"name": "DeepSeek",  "color": "cyan"},
    "xai":       {"name": "xAI",       "color": "red"},
    "alibaba":   {"name": "Alibaba",   "color": "magenta"},
    "moonshot":  {"name": "Moonshot",  "color": "bright_magenta"},
    "meta":      {"name": "Meta",      "color": "bright_blue"},
}

TIER_COLORS = {
    "flagship":  "yellow",
    "reasoning": "magenta",
    "standard":  "blue",
    "fast":      "green",
}

DEFAULT_MODEL = "gateway-claude-opus-4-7"


def get_model(model_id: str) -> dict:
    return next((m for m in MODELS if m["id"] == model_id), MODELS[0])


def find_model(query: str):
    q = query.lower().strip()
    return next(
        (m for m in MODELS if q in m["name"].lower() or q in m["id"].lower() or q in m["provider"].lower()),
        None,
    )
