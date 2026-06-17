"""
AI Processor for LeakSnipe.
Primary:     Ollama (local — default, no API key)
Cloud:       ASI:One, OpenAI, Google Gemini, Claude (optional .env keys)
"""
import json, os, re, sqlite3, logging, threading, urllib.request
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)

# ── Stored prompt (Responses API) ─────────────────────────────────────────────
RESPONSES_PROMPT_ID      = "pmpt_69cc16a996c4819395f781d7f35c07670a024ccb547603dd"
RESPONSES_PROMPT_VERSION = "2"

OPENAI_CHAT_MODEL    = "gpt-4o-mini"      # live chat: fast + cheap
OPENAI_SESSION_MODEL = "gpt-4o"           # session fallback if no stored prompt
CLAUDE_MODEL         = "claude-3-5-sonnet-20241022"
# ASI:One (Fetch.ai) — OpenAI-compatible chat completions API
# Docs: https://docs.asi1.ai/documentation/build-with-asi-one/openai-compatibility
ASI1_BASE_URL        = os.environ.get("ASI1_BASE_URL", "https://api.asi1.ai/v1")
ASI1_MODEL           = "asi1"
GEMINI_MODELS        = ["gemini-2.0-flash", "gemini-1.5-flash"]  # free-tier friendly
GEMINI_CHAT_MODEL    = GEMINI_MODELS[0]
OLLAMA_MODEL             = "qwen2.5:1.5b"           # fast chat fallback (~1 GB)
OLLAMA_MODEL_DEEPSEEK    = "deepseek-r1:8b"          # primary analysis — chain-of-thought
OLLAMA_MODEL_QWEN        = "qwen2.5:7b"              # secondary analysis — best JSON
OLLAMA_MODEL_LARGE       = "gemma3:latest"           # tertiary reasoning
OLLAMA_MODEL_NEMOTRON    = "nemotron-cascade-2:latest" # premium (large VRAM)
OLLAMA_RECOMMENDED_PULL  = OLLAMA_MODEL_DEEPSEEK

# Preference order for deep analysis (hand/session). First installed wins.
_OLLAMA_ANALYSIS_PRIORITY = [
    OLLAMA_MODEL_DEEPSEEK,
    OLLAMA_MODEL_QWEN,
    OLLAMA_MODEL_LARGE,
    OLLAMA_MODEL_NEMOTRON,
    OLLAMA_MODEL,
    "qwen3.6:latest",
    "qwen3:latest",
]

_ollama_installed_cache: set | None = None   # populated lazily


def _ollama_base() -> str:
    try:
        from config import get_ollama_base

        return get_ollama_base().rstrip("/")
    except ImportError:
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _reset_ollama_cache() -> None:
    global _ollama_installed_cache
    _ollama_installed_cache = None


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_BASE_DIR, "poker_hands.db")


def _ollama_installed_models() -> set:
    """Return set of model names currently installed in Ollama (cached per process)."""
    global _ollama_installed_cache
    if _ollama_installed_cache is not None:
        return _ollama_installed_cache
    try:
        resp = urllib.request.urlopen(f"{_ollama_base()}/api/tags", timeout=3)
        data = json.loads(resp.read().decode())
        _ollama_installed_cache = {m["name"] for m in data.get("models", [])}
    except Exception:
        _ollama_installed_cache = set()
    return _ollama_installed_cache


def _match_installed_model(installed: set, want: str) -> Optional[str]:
    """Match a preferred model name to an installed tag (exact or base name)."""
    if want in installed:
        return want
    base = want.split(":")[0]
    for name in installed:
        if name.split(":")[0] == base:
            return name
    return None


def _best_analysis_model() -> str:
    """Return the best installed Ollama model for deep analysis."""
    installed = _ollama_installed_models()
    if not installed:
        return ""
    for model in _OLLAMA_ANALYSIS_PRIORITY:
        hit = _match_installed_model(installed, model)
        if hit:
            return hit
    return sorted(installed)[0]

try:
    from openai import OpenAI as _OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic as _anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import google.generativeai as _genai
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False


def _gemini_api_key(settings: Optional[dict] = None) -> str:
    settings = settings or {}
    try:
        from config import get_api_key

        return (
            settings.get("gemini_api_key")
            or get_api_key("gemini")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        ).strip()
    except ImportError:
        return (
            settings.get("gemini_api_key")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        ).strip()


def _asi1_api_key(settings: Optional[dict] = None) -> str:
    """ASI:One API key — official env is ASI_ONE_API_KEY; ASI1_API_KEY also accepted."""
    settings = settings or {}
    try:
        from config import get_api_key

        return (
            settings.get("asi1_api_key")
            or get_api_key("asi1")
            or os.environ.get("ASI_ONE_API_KEY", "")
            or os.environ.get("ASI1_API_KEY", "")
        ).strip()
    except ImportError:
        return (
            settings.get("asi1_api_key")
            or os.environ.get("ASI_ONE_API_KEY", "")
            or os.environ.get("ASI1_API_KEY", "")
        ).strip()


def _gemini_generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 600,
    temperature: float = 0.3,
    api_key: Optional[str] = None,
) -> tuple[str, str]:
    """Call Gemini (SDK or REST). Returns (text, model_used)."""
    key = api_key or _gemini_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not set")

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    last_err = ""

    if HAS_GEMINI_SDK:
        for model in GEMINI_MODELS:
            try:
                _genai.configure(api_key=key)
                gm = _genai.GenerativeModel(model)
                resp = gm.generate_content(
                    full_prompt,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    },
                )
                text = (resp.text or "").strip()
                if text:
                    return text, model
            except Exception as exc:
                last_err = str(exc)
                log.warning("[AI] Gemini SDK %s failed: %s", model, exc)

    for model in GEMINI_MODELS:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )
            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode())
            parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts).strip()
            if text:
                return text, model
        except Exception as exc:
            last_err = str(exc)
            log.warning("[AI] Gemini REST %s failed: %s", model, exc)

    raise RuntimeError(last_err or "Gemini request failed")

# ── Prompts ───────────────────────────────────────────────────────────────────
HAND_PROMPT = """You are an elite poker coach. Analyze ONLY Hero decisions.
Hero: {hero}

Return ONLY valid JSON — no markdown:
{{
  "play_style": "TAG|LAG|Passive|Aggro|Tight-Passive|Maniac",
  "mistakes_found": <0-10>,
  "tags": ["leak_tag"],
  "summary": "1-2 sentence coaching note",
  "ev_estimate": "+EV|-EV|Neutral|Marginal +EV|Marginal -EV",
  "street_notes": {{"preflop":"..","flop":"..","turn":"..","river":".."}},
  "confidence": <0.0-1.0>
}}

Hand History:
{hand}
"""

SESSION_PROMPT = """You are an elite poker coach for hero '{hero}'.

Stats:
{stats}

Hands sample:
{hands}

Write a coaching report: play style, key leaks (specific), top 3 improvements,
positional weaknesses, tilt patterns. Direct, use poker terminology, clear sections."""

CHAT_SYSTEM = """You are an elite poker coach inside LeakSnipe poker tracking software.
Give specific advice based on the player data. Be concise. Use poker terminology.

Player context:
{context}"""

# ── Ollama helpers ────────────────────────────────────────────────────────────
def _ollama_available():
    try:
        urllib.request.urlopen(f"{_ollama_base()}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def _ollama_post(endpoint, payload, timeout=180):
    import urllib.error

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_ollama_base()}{endpoint}", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {err_body[:400]}") from e


def _ollama_uses_thinking(model: str) -> bool:
    base = model.split(":")[0].lower()
    return any(tag in base for tag in ("qwen3", "deepseek-r1", "r1", "nemotron"))


def _ollama_extract_text(response: dict) -> str:
    """Pull assistant text from Ollama chat or generate responses."""
    if not response:
        return ""
    if "message" in response:
        msg = response.get("message") or {}
        for field in ("content", "thinking"):
            text = (msg.get(field) or "").strip()
            if text:
                return text
    for field in ("response", "thinking"):
        text = (response.get(field) or "").strip()
        if text:
            return text
    return ""


def _ollama_complete(
    prompt: str,
    model: str,
    *,
    system: str = "",
    max_tokens: int = 600,
    temperature: float = 0.2,
    timeout: int = 600,
) -> str:
    """Run a completion via Ollama chat API (handles thinking models)."""
    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    attempts: List[dict] = []
    if _ollama_uses_thinking(model):
        attempts.append({"think": True, "num_predict": max(max_tokens, 1600)})
    attempts.append({"think": False, "num_predict": max_tokens})

    last_error: Optional[Exception] = None
    for attempt in attempts:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": attempt["num_predict"],
                "temperature": temperature,
            },
        }
        if "think" in attempt:
            payload["think"] = attempt["think"]
        try:
            response = _ollama_post("/api/chat", payload, timeout=timeout)
            text = _ollama_extract_text(response)
            if text:
                return text
            last_error = RuntimeError(f"Ollama model {model} returned an empty response")
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"Ollama model {model} returned no text")

def _clean_model_text(text: str) -> str:
    """Strip chain-of-thought wrappers and keep user-facing coaching text."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    for marker in (
        "Here's a thinking process:",
        "Here is a thinking process:",
        "Thinking process:",
        "Let me think",
    ):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[-1].strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    coaching = [
        sentence.strip()
        for sentence in sentences
        if len(sentence.strip()) > 24
        and not sentence.strip().startswith(("1.", "2.", "3.", "4.", "-", "*", "**"))
        and "Analyze User Input" not in sentence
        and "Task:" not in sentence
        and "Format:" not in sentence
    ]
    if coaching:
        return " ".join(coaching[-2:])[:1200]
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    for paragraph in reversed(paragraphs):
        if paragraph.startswith("{") or paragraph.startswith("```"):
            continue
        if len(paragraph) > 30 and "Analyze User Input" not in paragraph:
            return paragraph[:1200]
    return cleaned[:1200]


def _parse_json_response(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"analysis": "", "confidence": 0.5, "depth": "shallow"}

    for pattern in (r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```"):
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

    for opener in re.finditer(r"\{", text):
        start = opener.start()
        depth = 0
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and parsed:
                            return parsed
                    except Exception:
                        break

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"analysis": text.strip(), "confidence": 0.5, "depth": "shallow"}

# ── DB helpers ────────────────────────────────────────────────────────────────
def _ensure_ai_table(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_analysis (
        hand_id TEXT PRIMARY KEY, llm_provider TEXT,
        play_style TEXT, mistakes_found INTEGER,
        tags TEXT, summary TEXT, ev_estimate TEXT,
        raw_response TEXT,
        analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

def save_analysis(db_path, hand_id, analysis, provider="gpt"):
    if not analysis: return
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        conn.execute("""INSERT OR REPLACE INTO ai_analysis
            (hand_id,llm_provider,play_style,mistakes_found,tags,summary,ev_estimate,raw_response)
            VALUES (?,?,?,?,?,?,?,?)""",
            (hand_id, provider,
             analysis.get("play_style", "Unknown"),
             analysis.get("mistakes_found", 0),
             json.dumps(analysis.get("tags", [])),
             analysis.get("summary", ""),
             analysis.get("ev_estimate", "Unknown"),
             json.dumps(analysis)))
        conn.commit()
    finally:
        conn.close()

def get_analysis(db_path, hand_id):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM ai_analysis WHERE hand_id=?", (hand_id,)).fetchone()
        if row:
            d = dict(row)
            try: d["tags"] = json.loads(d.get("tags") or "[]")
            except: d["tags"] = []
            return d
    finally:
        conn.close()
    return None

# ── AIProcessor ───────────────────────────────────────────────────────────────
class AIProcessor:
    """
    Primary:        Ollama (local — default in settings)
    Cloud:          ASI:One, OpenAI, Gemini, Claude (.env keys)
    """

    def __init__(self, settings: dict = None, db_path: str = None):
        self._settings       = settings or {}
        self._db_path        = db_path or self._settings.get("db_path", _DEFAULT_DB)
        self._asi1_client    = None
        self._openai_client  = None
        self._anthropic_client = None
        self._gemini_ready   = False
        self._active_provider = None
        self._chat_history: List[dict] = []
        self._context        = ""
        self._chat_context_ready = False
        self._last_error: Optional[str] = None
        self._init()
        try:
            _ensure_ai_table(self._db_path)
        except Exception:
            pass

    def _asi1_complete(
        self,
        messages: List[dict],
        *,
        max_tokens: int = 600,
        temperature: float = 0.3,
    ) -> str:
        if not self._asi1_client:
            raise RuntimeError("ASI1 client not configured")
        resp = self._asi1_client.chat.completions.create(
            model=ASI1_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def _provider_chain(self, explicit: Optional[str] = None) -> List[str]:
        """Ordered provider names for inference."""
        pref = (explicit or self._settings.get("ai_provider") or "ollama").lower()
        has_asi1 = bool(self._asi1_client)
        has_openai = bool(self._openai_client)
        has_gemini = self._gemini_ready
        has_ollama = _ollama_available()
        has_claude = bool(self._anthropic_client)

        availability = {
            "asi1": has_asi1,
            "openai": has_openai,
            "gemini": has_gemini,
            "ollama": has_ollama,
            "anthropic": has_claude,
        }

        if pref == "asi1":
            chain = ["asi1", "openai", "gemini", "ollama", "anthropic"]
        elif pref == "openai":
            chain = ["openai", "asi1", "gemini", "ollama", "anthropic"]
        elif pref == "gemini":
            chain = ["gemini", "asi1", "openai", "ollama", "anthropic"]
        elif pref == "ollama":
            chain = ["ollama"]
        elif pref == "anthropic":
            chain = ["anthropic", "asi1", "openai", "gemini", "ollama"]
        else:
            if has_ollama:
                chain = ["ollama", "asi1", "openai", "gemini", "anthropic"]
            elif has_asi1:
                chain = ["asi1", "openai", "gemini", "ollama", "anthropic"]
            elif has_openai:
                chain = ["openai", "gemini", "ollama", "anthropic"]
            elif has_gemini:
                chain = ["gemini", "openai", "ollama", "anthropic"]
            else:
                chain = ["anthropic", "gemini", "openai", "ollama"]

        return [p for p in chain if availability.get(p, False)]

    def _init(self):
        asi1_key = _asi1_api_key(self._settings)
        if asi1_key and HAS_OPENAI:
            try:
                self._asi1_client = _OpenAI(api_key=asi1_key, base_url=ASI1_BASE_URL)
                log.info("[AI] ASI:One ready — model: %s @ %s", ASI1_MODEL, ASI1_BASE_URL)
            except Exception as e:
                log.warning("[AI] ASI1 init failed: %s", e)

        try:
            from config import get_api_key

            openai_key = get_api_key("openai")
        except ImportError:
            openai_key = None
        api_key = (
            self._settings.get("openai_api_key")
            or openai_key
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()
        if api_key and HAS_OPENAI:
            try:
                self._openai_client = _OpenAI(api_key=api_key)
                log.info("[AI] OpenAI ready")
            except Exception as e:
                log.warning(f"[AI] OpenAI init failed: {e}")

        gemini_key = _gemini_api_key(self._settings)
        if gemini_key:
            self._gemini_ready = True
            log.info("[AI] Gemini ready — models: %s", ", ".join(GEMINI_MODELS))

        try:
            from config import get_api_key

            ant_key = get_api_key("anthropic")
        except ImportError:
            ant_key = None
        ant_key = (
            self._settings.get("anthropic_api_key")
            or ant_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
        ).strip()
        if ant_key and HAS_ANTHROPIC:
            try:
                self._anthropic_client = _anthropic.Anthropic(api_key=ant_key)
                log.info(f"[AI] Claude ready — model: {CLAUDE_MODEL}")
            except Exception as e:
                log.warning(f"[AI] Claude init failed: {e}")

        chain = self._provider_chain()
        if chain:
            self._active_provider = chain[0]
            if chain[0] == "ollama":
                selected = _best_analysis_model()
                self._active_provider = f"ollama:{selected}"
                log.info("[AI] Ollama selected model: %s", selected or "<none>")
            elif chain[0] == "asi1":
                self._active_provider = f"asi1:{ASI1_MODEL}"
        elif _ollama_available():
            self._active_provider = f"ollama:{_best_analysis_model()}"
            log.info(f"[AI] Ollama only — model: {_best_analysis_model()}")
        else:
            log.warning(
                "[AI] No provider configured — set ASI_ONE_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY"
            )

    def is_available(self) -> bool:
        if _ollama_available():
            return True
        return bool(
            self._asi1_client
            or self._openai_client
            or self._gemini_ready
            or self._anthropic_client
        )

    def get_last_error(self) -> Optional[str]:
        return self._last_error

    def get_status(self) -> dict:
        _reset_ollama_cache()
        chain = self._provider_chain()
        ollama_running = _ollama_available()
        installed = sorted(_ollama_installed_models()) if ollama_running else []
        active_model = _best_analysis_model() if ollama_running else ""
        llm_available = self.is_available()
        llm_provider = self._active_provider or "none"
        if ollama_running and active_model:
            llm_provider = f"ollama:{active_model}"
            self._active_provider = llm_provider
        return {
            "llm_available": llm_available,
            "llm_provider": llm_provider,
            "provider_chain": chain,
            "ai_provider_pref": self._settings.get("ai_provider", "ollama"),
            "asi1_ready": bool(self._asi1_client),
            "asi1_model": ASI1_MODEL,
            "asi1_base_url": ASI1_BASE_URL,
            "asi1_rate_note": "Rate limits apply per ASI:One account tier — see docs.asi1.ai",
            "openai_ready": bool(self._openai_client),
            "gemini_ready": self._gemini_ready,
            "gemini_models": GEMINI_MODELS,
            "gemini_rate_note": "Free tier: ~15 RPM on gemini-2.0-flash — pace requests",
            "claude_ready": bool(self._anthropic_client),
            "ollama_ready": ollama_running,
            "ollama_base_url": _ollama_base(),
            "ollama_model": active_model,
            "ollama_models_installed": installed,
            "ollama_recommended_pull": OLLAMA_RECOMMENDED_PULL,
            "ollama_pull_alternatives": [
                OLLAMA_MODEL_QWEN,
                OLLAMA_MODEL,
            ],
            "ollama_setup_note": (
                "Install Ollama from https://ollama.com, keep it running, then: "
                f"ollama pull {OLLAMA_RECOMMENDED_PULL}"
            ),
            "vector_store_count": 0,
        }

    # ── Context / chat ────────────────────────────────────────────────────────
    def set_context(self, context: str):
        self._context     = context
        self._chat_history = []

    def clear_chat(self):
        self._chat_history = []

    def chat(self, user_message: str, provider: Optional[str] = None) -> str:
        self._chat_history.append({"role": "user", "content": user_message})
        system = CHAT_SYSTEM.format(context=self._context[:2000] or "No stats loaded.")
        messages = [{"role": "system", "content": system}] + self._chat_history[-12:]

        reply = ""
        used = None
        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_client:
                    reply = self._asi1_complete(messages, max_tokens=700, temperature=0.4)
                    used = f"asi1:{ASI1_MODEL}"
                    break
                if prov == "gemini" and self._gemini_ready:
                    prompt = "\n".join(
                        f"{m['role'].upper()}: {m['content']}" for m in messages if m["role"] != "system"
                    )
                    reply, used = _gemini_generate(
                        prompt, system=system, max_tokens=700, temperature=0.4
                    )
                    used = f"gemini:{used}"
                    break
                if prov == "openai" and self._openai_client:
                    resp = self._openai_client.chat.completions.create(
                        model=OPENAI_CHAT_MODEL, messages=messages,
                        temperature=0.4, max_tokens=600)
                    reply = resp.choices[0].message.content.strip()
                    used = OPENAI_CHAT_MODEL
                    break
                if prov == "anthropic" and self._anthropic_client:
                    resp = self._anthropic_client.messages.create(
                        model=CLAUDE_MODEL, max_tokens=600,
                        system=system,
                        messages=[m for m in messages if m["role"] != "system"])
                    reply = resp.content[0].text
                    used = CLAUDE_MODEL
                    break
                if prov == "ollama" and _ollama_available():
                    model = _best_analysis_model()
                    if not model:
                        raise RuntimeError(
                            "Ollama is running but no models are installed. "
                            f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
                        )
                    chat_prompt = "\n".join(
                        f"{m['role'].upper()}: {m['content']}"
                        for m in messages if m["role"] != "system"
                    )
                    reply = _ollama_complete(
                        chat_prompt,
                        model,
                        system=system,
                        max_tokens=600,
                        temperature=0.4,
                        timeout=300,
                    ).strip()
                    used = f"ollama:{model}"
                    break
            except Exception as e:
                self._last_error = str(e)
                log.warning("[AI] chat via %s failed: %s", prov, e)
                continue

        if not reply:
            if _ollama_available() and not _ollama_installed_models():
                reply = (
                    "Ollama is running but no models are installed.\n"
                    f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}\n"
                    "Or pull any model you prefer, then retry."
                )
            elif not self.is_available():
                reply = (
                    "No AI provider connected.\n"
                    "• Start Ollama (default — no API key)\n"
                    "• Or set OPENAI_API_KEY / GEMINI_API_KEY in .env at repo root"
                )
            else:
                reply = self._last_error or "AI request failed — check sidecar logs and retry."

        self._chat_history.append({"role": "assistant", "content": reply})
        return reply

    # ── Responses API helper ──────────────────────────────────────────────────
    def _responses_call(self, input_text: str) -> str:
        """Call the stored prompt via Responses API. Returns raw text."""
        resp = self._openai_client.responses.create(
            prompt={"id": RESPONSES_PROMPT_ID, "version": RESPONSES_PROMPT_VERSION},
            input=input_text,
        )
        return resp.output_text

    # ── Hand analysis ─────────────────────────────────────────────────────────
    def analyze_hand(self, raw_text, hero_name: str = "Hero",
                     hand_id: str = "", provider: str = None,
                     temperature: float = 0.2, max_tokens: int = 600) -> Optional[dict]:
        self._last_error = None
        hand_excerpt = str(raw_text)[:1200]
        prompt = HAND_PROMPT.format(hero=hero_name, hand=hand_excerpt)
        result = None
        used_prov = "none"

        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_client:
                    text = self._asi1_complete(
                        [
                            {"role": "system", "content": "You are an elite poker coach. Return only valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    result = _parse_json_response(text)
                    used_prov = f"asi1:{ASI1_MODEL}"
                    break
                if prov == "openai" and self._openai_client:
                    input_text = f"Hero: {hero_name}\n\n{str(raw_text)[:3000]}"
                    try:
                        text = self._responses_call(input_text)
                        result = _parse_json_response(text)
                        used_prov = "responses-api"
                    except Exception as e:
                        log.error(f"[AI] Responses API hand error: {e}")
                        r = self._openai_client.chat.completions.create(
                            model=OPENAI_CHAT_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=temperature, max_tokens=max_tokens)
                        result = _parse_json_response(r.choices[0].message.content)
                        used_prov = OPENAI_CHAT_MODEL
                    break
                if prov == "gemini" and self._gemini_ready:
                    text, model = _gemini_generate(
                        prompt,
                        system="You are an elite poker coach. Return only valid JSON.",
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    result = _parse_json_response(text)
                    used_prov = f"gemini:{model}"
                    break
                if prov == "anthropic" and self._anthropic_client:
                    resp = self._anthropic_client.messages.create(
                        model=CLAUDE_MODEL, max_tokens=max_tokens,
                        system="You are an elite poker coach. Return only JSON.",
                        messages=[{"role": "user", "content": prompt}])
                    result = _parse_json_response(resp.content[0].text)
                    used_prov = CLAUDE_MODEL
                    break
                if prov == "ollama" and _ollama_available():
                    model = _best_analysis_model()
                    if not model:
                        raise RuntimeError(
                            "Ollama is running but no models are installed. "
                            f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
                        )
                    raw = _ollama_complete(
                        prompt,
                        model,
                        system="You are an elite poker coach. Return only valid JSON.",
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=600,
                    )
                    result = _parse_json_response(raw)
                    # Some thinking models produce skeletal JSON; recover a usable summary.
                    if isinstance(result, dict) and not str(result.get("summary", "")).strip():
                        fallback = _ollama_complete(
                            (
                                "Reply in exactly 2 short sentences. No lists, no markdown, no preamble.\n\n"
                                f"Hero: {hero_name}\n\nHand:\n{hand_excerpt[:700]}\n\n"
                                "What is Hero's biggest leak in this hand?"
                            ),
                            model,
                            system="You are an elite poker coach. Output only two plain sentences.",
                            max_tokens=220,
                            temperature=0.2,
                            timeout=300,
                        ).strip()
                        cleaned = _clean_model_text(fallback)
                        if cleaned:
                            result["summary"] = cleaned
                    used_prov = f"ollama:{model}"
                    break
            except Exception as e:
                self._last_error = str(e)
                log.warning("[AI] analyze_hand via %s failed: %s", prov, e)
                continue

        if result:
            # If model output wasn't strict JSON, preserve textual insight for UI.
            if not result.get("summary") and result.get("analysis"):
                result["summary"] = _clean_model_text(str(result.get("analysis", "")))
            result.setdefault("play_style", "Unknown")
            result.setdefault("mistakes_found", 0)
            result.setdefault("tags", [])
            result.setdefault("summary", "")
            result.setdefault("ev_estimate", "Unknown")
            result.setdefault("confidence", 0.5)
            result["provider"] = used_prov
            if hand_id:
                try:
                    save_analysis(self._db_path, hand_id, result, used_prov)
                except Exception:
                    pass
        return result

    # ── Session analysis ──────────────────────────────────────────────────────
    def analyze_session(self, hands_text, hero_name: str = "Hero",
                        stats: dict = None, provider: Optional[str] = None) -> str:
        stats = stats or {}
        skip = {"biggest_wins", "biggest_losses", "by_position", "by_site", "alerts", "sessions_by_date"}
        stats_str = json.dumps({k: v for k, v in stats.items() if k not in skip}, indent=2)
        prompt = SESSION_PROMPT.format(
            hero=hero_name, stats=stats_str, hands=str(hands_text)[:2000]
        )

        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_client:
                    return self._asi1_complete(
                        [
                            {"role": "system", "content": "You are an elite poker coach."},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=1200,
                        temperature=0.4,
                    )
                if prov == "openai" and self._openai_client:
                    input_text = (
                        f"Hero: {hero_name}\n\nStats:\n{stats_str}\n\n"
                        f"Hands sample:\n{str(hands_text)[:2000]}"
                    )
                    try:
                        return self._responses_call(input_text)
                    except Exception:
                        r = self._openai_client.chat.completions.create(
                            model=OPENAI_SESSION_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.4, max_tokens=1200)
                        return r.choices[0].message.content
                if prov == "gemini" and self._gemini_ready:
                    text, _model = _gemini_generate(
                        prompt,
                        system="You are an elite poker coach.",
                        max_tokens=1200,
                        temperature=0.4,
                    )
                    return text
                if prov == "anthropic" and self._anthropic_client:
                    resp = self._anthropic_client.messages.create(
                        model=CLAUDE_MODEL, max_tokens=1200,
                        system="You are an elite poker coach.",
                        messages=[{"role": "user", "content": prompt}])
                    return resp.content[0].text
                if prov == "ollama" and _ollama_available():
                    model = _best_analysis_model()
                    if not model:
                        raise RuntimeError(
                            "Ollama is running but no models are installed. "
                            f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
                        )
                    text = _ollama_complete(
                        prompt,
                        model,
                        system="You are an elite poker coach.",
                        max_tokens=1200,
                        temperature=0.4,
                        timeout=600,
                    )
                    if text:
                        return text
                    raise RuntimeError(f"Ollama model {model} returned an empty response")
            except Exception as e:
                self._last_error = str(e)
                log.warning("[AI] analyze_session via %s failed: %s", prov, e)
                continue

        if self._last_error:
            return f"AI analysis failed: {self._last_error}"
        if _ollama_available() and not _ollama_installed_models():
            return (
                f"Ollama is running but no models are installed. "
                f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
            )
        return (
            "No AI provider available. Start Ollama (default), or set cloud API keys in .env "
            "(repo root)."
        )

    def find_similar(self, hand_text: str, hero_name: str = "Hero") -> str:
        input_text = (f"Poker hand pattern for hero '{hero_name}'.\n\n{hand_text[:1500]}\n\n"
                      "1. Core pattern/archetype\n2. Population tendencies\n3. Study recommendations")
        if self._openai_client:
            try:
                return self._responses_call(input_text)
            except Exception as e:
                return f"[GPT error: {e}]"
        return "[No AI provider available]"

    def get_cached_analysis(self, hand_id: str) -> Optional[dict]:
        try: return get_analysis(self._db_path, hand_id)
        except: return None

    # Backward compat
    def summarize_session(self, session_id: str = "",
                          hand_results: list = None, stats: dict = None, **kw) -> dict:
        # Extract hero name from the stats dict so analyze_session() uses the right name
        _stats = stats or kw.get("stats") or {}
        hero_name = _stats.get("hero", "Hero") or "Hero"

        # Build hand summary lines — combine display metadata with AI verdict so
        # the session prompt has real context instead of an empty hands sample.
        lines = []
        tag_freq: dict = {}
        for r in (hand_results or []):
            if not r:
                continue
            # Collect tags
            for t in (r.get("tags") or []):
                tag_freq[t] = tag_freq.get(t, 0) + 1

            # Compose a single descriptive line per hand
            hid    = r.get("_hand_id", r.get("hand_id", "?"))
            cards  = r.get("_cards",    "??")
            pos    = r.get("_position", "?")
            won    = r.get("_won",       0)
            style  = r.get("play_style", "")
            ev     = r.get("ev_estimate", "")
            summ   = r.get("summary", r.get("analysis", ""))
            result_s = f"+{won:.2f}" if won >= 0 else f"{won:.2f}"
            line = (f"Hand {hid} [{cards}] {pos} {result_s}"
                    + (f" | {style}" if style else "")
                    + (f" | {ev}" if ev else "")
                    + (f": {summ[:120]}" if summ else ""))
            lines.append(line)

        session_text = "\n".join(lines[:20])
        report = self.analyze_session(session_text, hero_name=hero_name, stats=_stats)
        return {"summary": report, "tag_frequency": tag_freq}
