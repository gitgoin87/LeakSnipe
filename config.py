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


def bootstrap_env(path: Optional[str] = None, *, reload_file: bool = False) -> str:
    """
    Load repo-root .env into os.environ for child modules that read os.environ directly.
    Does not override variables already set in the process environment.
    """
    env_path = path or ENV_PATH
    if reload_file and hasattr(_get_env, "_env_cache"):
        delattr(_get_env, "_env_cache")

    for key, value in _load_env_file(env_path).items():
        if value and key not in os.environ:
            os.environ[key] = value

    if hasattr(_get_env, "_env_cache"):
        delattr(_get_env, "_env_cache")

    return env_path


def env_keys_detected() -> Dict[str, bool]:
    """Return which API key env vars are set (values never exposed)."""
    return {
        "asi1": bool(get_api_key("asi1")),
        "openai": bool(get_api_key("openai")),
        "gemini": bool(get_api_key("gemini")),
        "anthropic": bool(get_api_key("anthropic")),
    }


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
        provider: 'openai', 'gemini', 'anthropic', 'asi1'
    
    Returns:
        API key or None if not set
    """
    env_map = {
        'openai': 'OPENAI_API_KEY',
        'gemini': 'GEMINI_API_KEY',
        'anthropic': 'ANTHROPIC_API_KEY',
        'asi1': 'ASI_ONE_API_KEY',
    }

    if provider not in env_map:
        return None

    key = _get_env(env_map[provider])
    if provider == 'gemini' and not key:
        key = _get_env('GOOGLE_API_KEY')
    if provider == 'asi1' and not key:
        key = _get_env('ASI1_API_KEY')
    if key and key != 'your-' + env_map[provider].lower() + '-here':
        return key
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
        "hud_opacity": 0.75,
        "hud_seat_layout": "9max",
        "hud_density": "compact",
        "hud_anchor": "top-left",
        "hud_offset_x": 0,
        "hud_offset_y": 0,
        "hud_site_profiles": {},
        "ai_provider": "ollama",
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
