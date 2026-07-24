"""Built-in LLM vendor presets for the admin console."""

from __future__ import annotations

from typing import Any

LLM_PRESETS: list[dict[str, Any]] = [
    {
        "code": "openai",
        "display_name": "OpenAI",
        "protocol": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "o4-mini"],
        "default_model": "gpt-4.1-mini",
    },
    {
        "code": "deepseek",
        "display_name": "DeepSeek",
        "protocol": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    {
        "code": "dashscope",
        "display_name": "通义千问 (DashScope)",
        "protocol": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "default_model": "qwen-plus",
    },
    {
        "code": "zhipu",
        "display_name": "智谱 GLM",
        "protocol": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-flash", "glm-4-plus", "glm-4.5"],
        "default_model": "glm-4-flash",
    },
    {
        "code": "moonshot",
        "display_name": "Moonshot (Kimi)",
        "protocol": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-8k",
    },
    {
        "code": "anthropic",
        "display_name": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5"],
        "default_model": "claude-sonnet-4-5",
    },
    {
        "code": "custom_openai",
        "display_name": "自定义 OpenAI 兼容",
        "protocol": "openai_compatible",
        "base_url": "http://127.0.0.1:8001/v1",
        "models": [],
        "default_model": "",
    },
    {
        "code": "deterministic",
        "display_name": "本地确定性 Stub",
        "protocol": "deterministic",
        "base_url": "",
        "models": ["deterministic-v1"],
        "default_model": "deterministic-v1",
    },
]


def get_preset(code: str):
    return next((item for item in LLM_PRESETS if item["code"] == code), None)
