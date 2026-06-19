"""
Configuration and environment variable management.
Handles API keys and settings securely without exposing secrets.
"""

import os
import json
import logging
from typing import Any, Dict, Optional
from pathlib import Path

log = logging.getLogger(__name__)

# Base directory (where the app/exe is running from)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")


def _load_env_file(path: str = ENV_PATH) -> Dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    if not os.path.exists(path):
        return env_vars
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Parse KEY=VALUE
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    if key:
                        env_vars[key] = value
    except Exception as e:
        log.warning(f"Failed to load .env file: {e}")
    
    return env_vars


# API keys may change in .env while the sidecar runs — always refresh these on reload.
_RELOADABLE_ENV_KEYS = frozenset({
    "ASI_ONE_API_KEY",
    "ASI_ONE_API_KEY_FALLBACK",
    "ASI1_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
})


def bootstrap_env(path: Optional[str] = None, *, reload_file: bool = False) -> str:
    """
    Load repo-root .env into os.environ for child modules that read os.environ directly.
    On first load, does not override variables already set in the process environment.
    When reload_file=True, API key vars are always refreshed from .env.
    """
    env_path = path or ENV_PATH
    if reload_file and hasattr(_get_env, "_env_cache"):
        delattr(_get_env, "_env_cache")

    for key, value in _load_env_file(env_path).items():
        if not value:
            continue
        if reload_file and key in _RELOADABLE_ENV_KEYS:
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value

    if hasattr(_get_env, "_env_cache"):
        delattr(_get_env, "_env_cache")

    return env_path


def get_asi1_fallback_api_key() -> Optional[str]:
    """Secondary ASI:One key for parallel coach/inference workloads."""
    key = _get_env("ASI_ONE_API_KEY_FALLBACK")
    if _is_valid_api_key(key):
        return key.strip()
    return None


def asi1_routing_mode() -> str:
    """'split' when primary + fallback keys are both configured."""
    if get_api_key("asi1") and get_asi1_fallback_api_key():
        return "split"
    return "single"


def env_keys_detected() -> Dict[str, Any]:
    """Return which API key env vars are set (values never exposed)."""
    asi1_primary = bool(get_api_key("asi1"))
    asi1_fallback = bool(get_asi1_fallback_api_key())
    return {
        "asi1": asi1_primary or asi1_fallback,
        "asi1_primary": asi1_primary,
        "asi1_fallback": asi1_fallback,
        "openai": bool(get_api_key("openai")),
        "gemini": bool(get_api_key("gemini")),
        "anthropic": bool(get_api_key("anthropic")),
        "deepseek": bool(get_api_key("deepseek")),
    }


def _is_valid_api_key(key: Optional[str]) -> bool:
    if not key:
        return False
    value = key.strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith("your-"):
        return False
    if lowered in {"changeme", "xxx", "sk-your-key-here", "your-api-key-here"}:
        return False
    return True


def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable from system or .env file."""
    # Priority: system env > .env file > default
    if key in os.environ:
        return os.environ[key]
    
    # Load from .env if not already cached
    if not hasattr(_get_env, '_env_cache'):
        _get_env._env_cache = _load_env_file()
    
    return _get_env._env_cache.get(key, default)


def get_api_key(provider: str) -> Optional[str]:
    """
    Get API key for a provider securely from environment.
    
    Args:
        provider: 'openai', 'gemini', 'anthropic', 'asi1', 'deepseek'
    
    Returns:
        API key or None if not set
    """
    env_map = {
        'openai': 'OPENAI_API_KEY',
        'gemini': 'GEMINI_API_KEY',
        'anthropic': 'ANTHROPIC_API_KEY',
        'asi1': 'ASI_ONE_API_KEY',
        'deepseek': 'DEEPSEEK_API_KEY',
    }

    if provider not in env_map:
        return None

    key = _get_env(env_map[provider])
    if provider == 'gemini' and not key:
        key = _get_env('GOOGLE_API_KEY')
    if provider == 'asi1' and not key:
        key = _get_env('ASI1_API_KEY')
    if _is_valid_api_key(key):
        return key.strip()
    return None


def get_ollama_base() -> str:
    """Get Ollama base URL from environment or default."""
    return _get_env('OLLAMA_BASE_URL', 'http://localhost:11434')


def get_tesseract_cmd() -> Optional[str]:
    """Get Tesseract OCR command path from environment or default."""
    return _get_env('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')


def load_settings() -> Dict[str, Any]:
    """
    Load settings from settings.json.
    Falls back to defaults if file doesn't exist.
    """
    default_settings = {
        "hero_names": {
            "CoinPoker": "",
            "BetACR": "",
            "GGPoker": "",
            "ReplayPoker": "",
            "ClubGG": "",
            "PokerStars": "",
            "888poker": "",
            "Ignition": "",
        },
        "scan_dirs": [],
        "auto_refresh": True,
        "refresh_interval": 5,
        "theme": "Slate Blue",
        "advanced_mode": False,
        "live_hud_enabled": False,
        "live_hud_backend": "python",
        "hud_opacity": 0.75,
        "hud_seat_layout": "9max",
        "hud_density": "compact",
        "hud_anchor": "top-left",
        "hud_offset_x": 0,
        "hud_offset_y": 0,
        "hud_edge_margin_pct": 0.12,
        "hud_badge_scale": 1.5,
        "hud_locked": True,
        "hud_slot_positions": {},
        "hud_site_profiles": {},
        "ai_provider": "asi1",
        "ollama_model": "",
        "ai_include_dataset_context": True,
        "ai_web_search_mode": "on_demand",
        "ai_include_web_context": True,
        # ASI:One personalization / agentic capabilities
        "ai_personalization": True,   # durable per-hero coach memory (local SQLite)
        "ai_agentic_tools": True,     # let the model call live DB-query tools
        "asi1_model": "asi1",         # primary ASI:One chat/tools model
        # Primary hero identity for coach memory + ASI:One session id. Stats and
        # dataset context still cover ALL hero aliases; this only scopes memory.
        "coach_memory_hero": "JohnDaWalka",
    }
    
    if not os.path.exists(SETTINGS_PATH):
        log.info("settings.json not found, using defaults")
        return default_settings
    
    try:
        with open(SETTINGS_PATH, 'r') as f:
            settings = json.load(f)
        
        # Remove any API keys that might be in settings.json (security)
        for key in ['openai_api_key', 'gemini_api_key', 'anthropic_api_key']:
            settings.pop(key, None)
        
        # Merge with defaults (in case new keys were added)
        merged = {**default_settings, **settings}
        mode = (merged.get("ai_web_search_mode") or "").strip().lower()
        if mode not in ("off", "on_demand", "always"):
            if merged.get("ai_include_web_context") is False:
                merged["ai_web_search_mode"] = "off"
            else:
                merged["ai_web_search_mode"] = "on_demand"
        merged["ai_include_web_context"] = merged.get("ai_web_search_mode") != "off"
        # Legacy installs kept "auto" — when ASI:One key is present, treat as explicit asi1
        # so Settings UI and AIProcessor stay aligned with the recommended cloud provider.
        if merged.get("ai_provider") == "auto" and get_api_key("asi1"):
            merged["ai_provider"] = "asi1"
            if settings.get("ai_provider") == "auto":
                save_settings(merged)
        # Map retired model names to the current ASI:One family.
        legacy_asi1 = {
            "asi1-fast": "asi1-mini",
            "asi1-extended": "asi1-ultra",
            "asi1-agentic": "asi1",
        }
        asi1_pick = (merged.get("asi1_model") or "").strip()
        if asi1_pick in legacy_asi1:
            merged["asi1_model"] = legacy_asi1[asi1_pick]
            if settings.get("asi1_model") in legacy_asi1:
                save_settings(merged)
        return merged
    except Exception as e:
        log.error(f"Failed to load settings.json: {e}")
        return default_settings


def save_settings(settings: Dict[str, Any]) -> bool:
    """
    Save settings to settings.json.
    NEVER saves API keys to this file.
    """
    try:
        # Ensure no API keys are saved
        safe_settings = {k: v for k, v in settings.items() 
                        if not k.endswith('_api_key')}
        
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(safe_settings, f, indent=2)
        return True
    except Exception as e:
        log.error(f"Failed to save settings.json: {e}")
        return False


def validate_config() -> bool:
    """
    Check if configuration is valid.
    Returns True if settings file exists and is valid.
    """
    if not os.path.exists(SETTINGS_PATH):
        log.warning("settings.json not found")
        return False
    
    try:
        with open(SETTINGS_PATH, 'r') as f:
            json.load(f)
        return True
    except Exception as e:
        log.error(f"Invalid settings.json: {e}")
        return False
