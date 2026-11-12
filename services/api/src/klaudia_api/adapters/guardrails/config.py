from dataclasses import dataclass


@dataclass
class GuardrailsConfig:
    groq_api_key: str
    enabled: bool = True
    groq_model: str = "meta-llama/llama-prompt-guard-2-86m"
    guardrails_model: str = "gemini-3.1-flash-lite-preview"
    guardrails_provider: str = "google"
    deepseek_base_url: str = ""
    deepseek_api_key: str = ""
    disable_thinking: bool = True
