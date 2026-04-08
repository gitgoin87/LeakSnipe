"""
AI Processor for LeakSnipe.
Analysis:    OpenAI Responses API  (stored prompt pmpt_69cc16...)
Chat:        gpt-4o-mini chat completions  (dynamic conversation)
Fallback:    Claude 3.5 Sonnet  (ANTHROPIC_API_KEY)
Local:       Ollama nemotron-cascade-2  (primary analysis, ~24 GB, premium reasoning)
             Ollama gemma3              (secondary analysis, ~3.3 GB, GPU reasoning)
             Ollama deepseek-r1         (backup, ~4.7 GB, chain-of-thought)
             Ollama qwen2.5:1.5b        (fast chat, ~1 GB, CPU)
"""
import json, os, re, sqlite3, logging, urllib.request
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)

# ── Stored prompt (Responses API) ─────────────────────────────────────────────
RESPONSES_PROMPT_ID      = "pmpt_69cc16a996c4819395f781d7f35c07670a024ccb547603dd"
RESPONSES_PROMPT_VERSION = "2"

OPENAI_CHAT_MODEL    = "gpt-4o-mini"      # live chat: fast + cheap
OPENAI_SESSION_MODEL = "gpt-4o"           # session fallback if no stored prompt
CLAUDE_MODEL         = "claude-3-5-sonnet-20241022"
OLLAMA_BASE              = "http://localhost:11434"
OLLAMA_MODEL             = "qwen2.5:1.5b"           # fast chat on CPU (~1 GB)
OLLAMA_MODEL_NEMOTRON    = "nemotron-cascade-2:latest" # primary analysis (~24 GB, premium)
OLLAMA_MODEL_LARGE       = "gemma3:latest"           # secondary analysis (~3.3 GB, GPU)
OLLAMA_MODEL_BACKUP      = "deepseek-r1:7b"          # backup reasoning / chain-of-thought (~4.7 GB)

# Preference order for deep analysis (hand/session). First available wins.
_OLLAMA_ANALYSIS_PRIORITY = [
    OLLAMA_MODEL_NEMOTRON,
    OLLAMA_MODEL_LARGE,
    OLLAMA_MODEL_BACKUP,
    OLLAMA_MODEL,
]

_ollama_installed_cache: set | None = None   # populated lazily

def _ollama_installed_models() -> set:
    """Return set of model names currently installed in Ollama (cached per process)."""
    global _ollama_installed_cache
    if _ollama_installed_cache is not None:
        return _ollama_installed_cache
    try:
        resp = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3)
        data = json.loads(resp.read().decode())
        _ollama_installed_cache = {m["name"] for m in data.get("models", [])}
    except Exception:
        _ollama_installed_cache = set()
    return _ollama_installed_cache

def _best_analysis_model() -> str:
    """Return the best installed Ollama model for deep analysis."""
    installed = _ollama_installed_models()
    for model in _OLLAMA_ANALYSIS_PRIORITY:
        if model in installed:
            return model
    return OLLAMA_MODEL   # last resort

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
        urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def _ollama_post(endpoint, payload, timeout=180):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}{endpoint}", data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def _parse_json_response(text: str) -> dict:
    try:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
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
    Hand analysis:   gpt-4o-mini  (OPENAI_API_KEY)
    Session reports: gpt-4o       (OPENAI_API_KEY)
    Fallback:        Claude 3.5 Sonnet (ANTHROPIC_API_KEY)
    Local/offline:   Ollama qwen3.5 (no key)
    """

    def __init__(self, settings: dict = None, db_path: str = "poker_hands.db"):
        self._settings       = settings or {}
        self._db_path        = db_path
        self._openai_client  = None
        self._anthropic_client = None
        self._active_provider = None
        self._chat_history: List[dict] = []
        self._context        = ""
        self._chat_context_ready = False
        self._init()
        try: _ensure_ai_table(db_path)
        except Exception: pass

    def _init(self):
        # 1. Ollama local (primary when available)
        if _ollama_available():
            best = _best_analysis_model()
            self._active_provider = f"ollama:{best}"
            log.info(f"[AI] Ollama ready — analysis model: {best}  chat model: {OLLAMA_MODEL}")
            return

        # 2. OpenAI cloud (when Ollama not running)
        api_key = (self._settings.get("openai_api_key") or
                   os.environ.get("OPENAI_API_KEY", "")).strip()
        if api_key and HAS_OPENAI:
            try:
                self._openai_client   = _OpenAI(api_key=api_key)
                self._active_provider = "openai"
                log.info(f"[AI] OpenAI ready — responses API")
            except Exception as e:
                log.warning(f"[AI] OpenAI init failed: {e}")

        # 3. Claude fallback
        ant_key = (self._settings.get("anthropic_api_key") or
                   os.environ.get("ANTHROPIC_API_KEY", "")).strip()
        if ant_key and HAS_ANTHROPIC and not self._active_provider:
            try:
                self._anthropic_client = _anthropic.Anthropic(api_key=ant_key)
                self._active_provider  = CLAUDE_MODEL
                log.info(f"[AI] Claude ready — model: {CLAUDE_MODEL}")
            except Exception as e:
                log.warning(f"[AI] Claude init failed: {e}")

    def is_available(self) -> bool:
        return bool(self._openai_client or self._anthropic_client or _ollama_available())

    def get_status(self) -> dict:
        return {
            "llm_available":  self.is_available(),
            "llm_provider":   self._active_provider or "none",
            "openai_ready":   bool(self._openai_client),
            "claude_ready":   bool(self._anthropic_client),
            "ollama_ready":   _ollama_available(),
            "vector_store_count": 0,
        }

    # ── Context / chat ────────────────────────────────────────────────────────
    def set_context(self, context: str):
        self._context     = context
        self._chat_history = []

    def clear_chat(self):
        self._chat_history = []

    def chat(self, user_message: str) -> str:
        self._chat_history.append({"role": "user", "content": user_message})
        system = CHAT_SYSTEM.format(context=self._context[:2000] or "No stats loaded.")
        messages = [{"role": "system", "content": system}] + self._chat_history[-12:]

        reply = ""
        if _ollama_available():
            try:
                r = _ollama_post("/api/chat",
                    {"model": OLLAMA_MODEL, "messages": messages, "stream": False},
                    timeout=90)
                reply = r.get("message", {}).get("content", "").strip()
                if not reply:
                    reply = "[Ollama returned empty response — model may still be loading, try again]"
            except Exception as e:
                err = str(e)
                if "timed out" in err.lower() or "timeout" in err.lower():
                    reply = (f"⏱️ Ollama timed out — '{OLLAMA_MODEL}' is still loading into memory.\n"
                             "Wait 10 seconds and try again.")
                else:
                    reply = f"[Ollama error: {e}]"
        elif self._openai_client:
            try:
                resp = self._openai_client.chat.completions.create(
                    model=OPENAI_CHAT_MODEL, messages=messages,
                    temperature=0.4, max_tokens=600)
                reply = resp.choices[0].message.content.strip()
            except Exception as e:
                reply = f"[GPT error: {e}]"
        elif self._anthropic_client:
            try:
                resp = self._anthropic_client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=600,
                    system=system,
                    messages=[m for m in messages if m["role"] != "system"])
                reply = resp.content[0].text
            except Exception as e:
                reply = f"[Claude error: {e}]"
        else:
            reply = ("⚠️ No AI provider connected.\n"
                     f"• Start Ollama — model '{OLLAMA_MODEL}' is ready\n"
                     "• Or update billing and set OPENAI_API_KEY")

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
        input_text = f"Hero: {hero_name}\n\n{str(raw_text)[:3000]}"
        result     = None
        used_prov  = "responses-api"

        if self._openai_client:
            try:
                text   = self._responses_call(input_text)
                result = _parse_json_response(text)
            except Exception as e:
                log.error(f"[AI] Responses API hand error: {e}")
                # fallback to chat completions
                try:
                    prompt = HAND_PROMPT.format(hero=hero_name, hand=str(raw_text)[:3000])
                    r = self._openai_client.chat.completions.create(
                        model=OPENAI_CHAT_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature, max_tokens=max_tokens)
                    result   = _parse_json_response(r.choices[0].message.content)
                    used_prov = OPENAI_CHAT_MODEL
                except Exception as e2:
                    result = {"summary": f"[GPT error: {e2}]"}
        elif self._anthropic_client:
            try:
                prompt = HAND_PROMPT.format(hero=hero_name, hand=str(raw_text)[:3000])
                resp = self._anthropic_client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=max_tokens,
                    system="You are an elite poker coach. Return only JSON.",
                    messages=[{"role": "user", "content": prompt}])
                result    = _parse_json_response(resp.content[0].text)
                used_prov = CLAUDE_MODEL
            except Exception as e:
                result = {"summary": f"[Claude error: {e}]"}
        elif _ollama_available():
            try:
                prompt = HAND_PROMPT.format(hero=hero_name, hand=str(raw_text)[:3000])
                model = _best_analysis_model()
                r = _ollama_post("/api/generate",
                    {"model": model, "prompt": prompt, "stream": False})
                result    = _parse_json_response(r.get("response", ""))
                used_prov = f"ollama:{model}"
            except Exception as e:
                model = _best_analysis_model()
                msg = (f"Model '{model}' not ready — run: ollama pull {model}"
                       if "404" in str(e) else f"[Ollama error: {e}]")
                result = {"summary": msg}

        if result:
            result.setdefault("play_style",     "Unknown")
            result.setdefault("mistakes_found",  0)
            result.setdefault("tags",            [])
            result.setdefault("summary",         "")
            result.setdefault("ev_estimate",     "Unknown")
            result.setdefault("confidence",       0.5)
            if hand_id:
                try: save_analysis(self._db_path, hand_id, result, used_prov)
                except Exception: pass
        return result

    # ── Session analysis ──────────────────────────────────────────────────────
    def analyze_session(self, hands_text, hero_name: str = "Hero",
                        stats: dict = None) -> str:
        stats     = stats or {}
        skip      = {"biggest_wins","biggest_losses","by_position","by_site",
                     "alerts","sessions_by_date"}
        stats_str = json.dumps(
            {k: v for k, v in stats.items() if k not in skip}, indent=2)
        input_text = (f"Hero: {hero_name}\n\nStats:\n{stats_str}\n\n"
                      f"Hands sample:\n{str(hands_text)[:2000]}")

        if self._openai_client:
            try:
                return self._responses_call(input_text)
            except Exception as e:
                # fallback to chat completions
                try:
                    prompt = SESSION_PROMPT.format(
                        hero=hero_name, stats=stats_str, hands=str(hands_text)[:2000])
                    r = self._openai_client.chat.completions.create(
                        model=OPENAI_SESSION_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=1200)
                    return r.choices[0].message.content
                except Exception as e2:
                    return f"[GPT error: {e2}]"
        if self._anthropic_client:
            try:
                prompt = SESSION_PROMPT.format(
                    hero=hero_name, stats=stats_str, hands=str(hands_text)[:2000])
                resp = self._anthropic_client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=1200,
                    system="You are an elite poker coach.",
                    messages=[{"role": "user", "content": prompt}])
                return resp.content[0].text
            except Exception as e:
                return f"[Claude error: {e}]"
        if _ollama_available():
            try:
                prompt = SESSION_PROMPT.format(
                    hero=hero_name, stats=stats_str, hands=str(hands_text)[:2000])
                model = _best_analysis_model()
                r = _ollama_post("/api/generate",
                    {"model": model, "prompt": prompt, "stream": False}, 180)
                return r.get("response", "")
            except Exception as e:
                return f"[Ollama error: {e}]"
        return "No AI provider available. Set OPENAI_API_KEY or start Ollama."

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
                          hand_results: list = None, **kw) -> dict:
        texts = [r.get("analysis","") for r in (hand_results or []) if r]
        return {"summary": self.analyze_session("\n\n".join(texts[:20]))}
