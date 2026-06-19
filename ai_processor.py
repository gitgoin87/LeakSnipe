"""
AI Processor for LeakSnipe.
Primary:     ASI:One (cloud — default when ASI_ONE_API_KEY is set)
Cloud:       OpenAI, DeepSeek, Google Gemini, Claude (optional .env keys)
Fallback:    Ollama (local, last resort)
"""
import hashlib, json, os, re, sqlite3, logging, threading, urllib.request, urllib.error, uuid
from collections import defaultdict
from typing import Optional, Dict, Any, List

import equity as equity_engine
from pot_odds import (
    extract_mtt_ante,
    format_pot_odds_line,
    multiway_equity_note,
    walk_hero_spots,
)

log = logging.getLogger(__name__)

try:
    from coach_memory import CoachMemory, stable_session_id as _memory_session_id
    HAS_COACH_MEMORY = True
except ImportError:  # memory is optional — coach still works statelessly without it
    HAS_COACH_MEMORY = False

# ── Stored prompt (Responses API) ─────────────────────────────────────────────
RESPONSES_PROMPT_ID      = "pmpt_69cc16a996c4819395f781d7f35c07670a024ccb547603dd"
RESPONSES_PROMPT_VERSION = "2"

OPENAI_CHAT_MODEL    = "gpt-4o-mini"      # live chat: fast + cheap
OPENAI_SESSION_MODEL = "gpt-4o"           # session fallback if no stored prompt
CLAUDE_MODEL         = "claude-3-5-sonnet-20241022"
# ASI:One (Fetch.ai) — OpenAI-compatible chat completions API
# Docs: https://docs.asi1.ai/documentation/build-with-asi-one/openai-compatibility
# Models: https://docs.asi1.ai/documentation/models
# AgentVerse marketplace is accessed via this HTTP API; uAgents SDK is not required.
ASI1_BASE_URL        = os.environ.get("ASI1_BASE_URL", "https://api.asi1.ai/v1")
ASI1_MODEL           = os.environ.get("ASI1_MODEL", "asi1")
# Docs recommend ~90s for agentic / multi-hop requests (docs.asi1.ai agentic-llm).
# web_search is a request flag on any model — not a separate model name.
ASI1_CHAT_MODELS     = ["asi1", "asi1-ultra", "asi1-mini"]
_ASI1_LEGACY_MODELS  = {
    "asi1-fast": "asi1-mini",
    "asi1-extended": "asi1-ultra",
    "asi1-agentic": "asi1",
}
ASI1_REQUEST_TIMEOUT = float(os.environ.get("ASI1_REQUEST_TIMEOUT", "180"))
# ASI:One image generation — POST {base}/image/generate, returns {images:[{url}]}
# Docs: https://docs.asi1.ai/api-reference/llm/image-generation
ASI1_IMAGE_MODEL     = os.environ.get("ASI1_IMAGE_MODEL", "asi1-mini")
# Allowed sizes: 1024x1024, 1024x1536, 1024x1792, 1536x1024, 1792x1024, 256x256, 512x512, auto
ASI1_IMAGE_SIZE      = os.environ.get("ASI1_IMAGE_SIZE", "1024x1024")
CLOUD_PROVIDER_ORDER = ("asi1", "openai", "deepseek", "gemini", "anthropic")
DEEPSEEK_BASE_URL    = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_CHAT_MODEL  = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_REASONER_MODEL = "deepseek-reasoner"
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
_ollama_available_cache: bool | None = None


def _ollama_base() -> str:
    try:
        from config import get_ollama_base

        return get_ollama_base().rstrip("/")
    except ImportError:
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _reset_ollama_cache() -> None:
    global _ollama_installed_cache, _ollama_available_cache
    _ollama_installed_cache = None
    _ollama_available_cache = None


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_BASE_DIR, "poker_hands.db")


def _ollama_installed_models() -> set:
    """Return set of model names currently installed in Ollama (cached per process)."""
    global _ollama_installed_cache, _ollama_available_cache
    if _ollama_installed_cache is not None:
        return _ollama_installed_cache
    try:
        resp = urllib.request.urlopen(f"{_ollama_base()}/api/tags", timeout=3)
        data = json.loads(resp.read().decode())
        _ollama_installed_cache = {m["name"] for m in data.get("models", [])}
        _ollama_available_cache = True
    except Exception:
        _ollama_installed_cache = set()
        _ollama_available_cache = False
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


def _best_analysis_model(settings: Optional[dict] = None) -> str:
    """Return the Ollama model to use (settings override, then priority list, then any installed)."""
    installed = _ollama_installed_models()
    if not installed:
        return ""
    settings = settings or {}
    preferred = (settings.get("ollama_model") or "").strip()
    if preferred:
        hit = _match_installed_model(installed, preferred)
        if hit:
            return hit
    for model in _OLLAMA_ANALYSIS_PRIORITY:
        hit = _match_installed_model(installed, model)
        if hit:
            return hit
    return sorted(installed)[0]


def _cloud_first_chain(availability: Dict[str, bool]) -> List[str]:
    """Prefer cloud providers; Ollama is last-resort fallback."""
    chain = [p for p in CLOUD_PROVIDER_ORDER if availability.get(p)]
    if availability.get("ollama"):
        chain.append("ollama")
    return chain


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


def _b64_image_mime(b64: str, output_format: Optional[str] = None) -> str:
    """Infer image MIME from a base64 payload's magic prefix (falls back to output_format/png)."""
    prefix = (b64 or "")[:16]
    if prefix.startswith("/9j/"):
        return "image/jpeg"
    if prefix.startswith("iVBORw0KGgo"):
        return "image/png"
    if prefix.startswith("R0lGOD"):
        return "image/gif"
    if prefix.startswith("UklGR"):
        return "image/webp"
    fmt = (output_format or "png").strip().lower() or "png"
    return f"image/{'jpeg' if fmt in ('jpg', 'jpeg') else fmt}"


def _sanitize_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    try:
        from config import _is_valid_api_key

        return key if _is_valid_api_key(key) else ""
    except ImportError:
        return "" if key.lower().startswith("your-") else key


def _gemini_api_key(settings: Optional[dict] = None) -> str:
    settings = settings or {}
    try:
        from config import get_api_key

        raw = (
            settings.get("gemini_api_key")
            or get_api_key("gemini")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
    except ImportError:
        raw = (
            settings.get("gemini_api_key")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
    return _sanitize_api_key(raw)


def _normalize_asi1_model(name: Optional[str]) -> str:
    """Map settings/env model names to documented ASI:One models."""
    pick = (name or ASI1_MODEL).strip() or ASI1_MODEL
    return _ASI1_LEGACY_MODELS.get(pick, pick)


def _asi1_openai_client(api_key: str):
    """OpenAI SDK client pointed at api.asi1.ai with a generous HTTP timeout."""
    return _OpenAI(
        api_key=api_key,
        base_url=ASI1_BASE_URL,
        timeout=ASI1_REQUEST_TIMEOUT,
        max_retries=2,
    )


def _asi1_api_key(settings: Optional[dict] = None) -> str:
    """ASI:One primary key — ASI_ONE_API_KEY; ASI1_API_KEY also accepted."""
    settings = settings or {}
    try:
        from config import get_api_key

        raw = (
            settings.get("asi1_api_key")
            or get_api_key("asi1")
            or os.environ.get("ASI_ONE_API_KEY", "")
            or os.environ.get("ASI1_API_KEY", "")
        )
    except ImportError:
        raw = (
            settings.get("asi1_api_key")
            or os.environ.get("ASI_ONE_API_KEY", "")
            or os.environ.get("ASI1_API_KEY", "")
        )
    return _sanitize_api_key(raw)


def _asi1_api_key_fallback(settings: Optional[dict] = None) -> str:
    """ASI:One secondary key for parallel coach/inference workloads."""
    settings = settings or {}
    try:
        from config import get_asi1_fallback_api_key

        raw = (
            settings.get("asi1_api_key_fallback")
            or get_asi1_fallback_api_key()
            or os.environ.get("ASI_ONE_API_KEY_FALLBACK", "")
        )
    except ImportError:
        raw = (
            settings.get("asi1_api_key_fallback")
            or os.environ.get("ASI_ONE_API_KEY_FALLBACK", "")
        )
    return _sanitize_api_key(raw)


def _deepseek_api_key(settings: Optional[dict] = None) -> str:
    settings = settings or {}
    try:
        from config import get_api_key

        raw = (
            settings.get("deepseek_api_key")
            or get_api_key("deepseek")
            or os.environ.get("DEEPSEEK_API_KEY", "")
        )
    except ImportError:
        raw = (
            settings.get("deepseek_api_key")
            or os.environ.get("DEEPSEEK_API_KEY", "")
        )
    return _sanitize_api_key(raw)


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

# ── Hand analysis context ─────────────────────────────────────────────────────
_STREET_ORDER = ("Preflop", "Flop", "Turn", "River", "Showdown")


def hand_meta_from_hand(hand) -> Dict[str, Any]:
    """Serialize a Hand model for AI analysis."""
    return {
        "hero_cards": getattr(hand, "hero_cards", "") or "",
        "board_cards": list(getattr(hand, "board_cards", None) or []),
        "hero_won": float(getattr(hand, "hero_won", 0) or 0),
        "pot": float(getattr(hand, "pot", 0) or 0),
        "hero_position": getattr(hand, "hero_position", "") or "",
        "is_tournament": bool(getattr(hand, "is_tournament", False)),
        "game_type": getattr(hand, "game_type", "") or "",
        "winners": list(getattr(hand, "winners", None) or []),
        "streets": list(getattr(hand, "streets", None) or []),
        "players": [
            {"name": info.get("name", ""), "stack": float(info.get("stack") or 0)}
            for info in (getattr(hand, "players", None) or {}).values()
        ],
        "site": getattr(hand, "site", "") or "",
    }


def _outcome_label(hero_won: float) -> str:
    if hero_won > 0:
        return "won"
    if hero_won < 0:
        return "lost"
    return "break_even"


def _format_amount(value: float, is_tournament: bool) -> str:
    try:
        from utils import format_hero_result_plain

        return format_hero_result_plain(float(value or 0), is_tournament)
    except ImportError:
        v = float(value or 0)
        if is_tournament:
            return f"{v:,.0f} chips"
        return f"${v:.2f}"


def _format_pot_size(value: float, is_tournament: bool) -> str:
    v = float(value or 0)
    if is_tournament:
        return f"{v:,.0f} chips"
    return f"${v:.2f}"


def _format_action_log(streets: List[dict], hero_name: str, is_tournament: bool) -> str:
    """Chronological action log for the prompt."""
    lines: List[str] = []
    for street in streets or []:
        name = street.get("name") or "Unknown"
        cards = street.get("cards") or []
        board = " ".join(cards) if cards else "(no board yet)"
        lines.append(f"--- {name} | board: {board} ---")
        for act in street.get("actions") or []:
            player = act.get("player") or "?"
            action = act.get("action") or "?"
            amount = float(act.get("amount") or 0)
            hero_tag = " [HERO]" if hero_name and player == hero_name else ""
            amt = f" {_format_amount(amount, is_tournament)}" if amount > 0 else ""
            lines.append(f"  {player}{hero_tag}: {action}{amt}")
    return "\n".join(lines) if lines else "(no parsed actions — use raw hand history)"


_HERO_DECISIONS = frozenset({"fold", "check", "call", "raise", "bet", "all-in", "allin"})


def _blind_levels(streets: List[dict]) -> set:
    """Posts that belong to the betting line (SB/BB) — the two largest post sizes.

    Antes (smaller, equal posts) are excluded so 'to-call' math is not skewed.
    """
    amounts = set()
    for street in streets or []:
        for act in street.get("actions") or []:
            if (act.get("action") or "").lower() == "post":
                amt = float(act.get("amount") or 0)
                if amt > 0:
                    amounts.add(amt)
    return set(sorted(amounts, reverse=True)[:2])


def _extract_mtt_ante(streets: List[dict]) -> Dict[str, float]:
    """Parse BetACR-style ``posts ante X`` lines from the action log."""
    return extract_mtt_ante(streets)


def _hero_spot_facts(
    streets: List[dict], players: List[dict], hero_name: str
) -> List[Dict[str, Any]]:
    """Pre-compute action legality and pot odds at each hero decision point."""
    return walk_hero_spots(streets, players, hero_name)


def _enrich_spots_with_made_hands(
    streets: List[dict],
    spots: List[Dict[str, Any]],
    hero_name: str,
    hero_cards: str,
) -> List[Dict[str, Any]]:
    """Attach board-at-spot, made-hand labels, and per-spot equity to each hero spot."""
    if not spots:
        return spots
    board_so_far: List[str] = []
    spot_idx = 0
    enriched: List[Dict[str, Any]] = []

    for street in streets or []:
        street_cards = list(street.get("cards") or [])
        if street_cards:
            board_so_far = board_so_far + street_cards

        for act in street.get("actions") or []:
            player = str(act.get("player") or "")
            action = (act.get("action") or "").lower()
            if player != hero_name or action not in _HERO_DECISIONS:
                continue
            if spot_idx >= len(spots):
                break
            spot = dict(spots[spot_idx])
            board_at = list(board_so_far)
            spot["board_at_spot"] = " ".join(board_at)
            made = equity_engine.describe_made_hand(hero_cards, board_at)
            if made:
                spot["made_hand"] = made
                spot["made_hand_label"] = str(made.get("label") or "")
            eq = equity_engine.spot_equity_pct(hero_cards, board_at, iters=1000)
            if eq is not None:
                spot["spot_equity"] = eq
            enriched.append(spot)
            spot_idx += 1

    while spot_idx < len(spots):
        enriched.append(dict(spots[spot_idx]))
        spot_idx += 1
    return enriched


def detect_knockouts(
    streets: List[dict],
    players: List[dict],
    hero_name: str,
    winners: List[dict],
    *,
    hero_won: float = 0.0,
) -> Dict[str, Any]:
    """Detect opponents hero eliminated by winning an all-in pot."""
    stacks = {
        str(p.get("name") or ""): float(p.get("stack") or 0) for p in (players or [])
    }
    blinds = _blind_levels(streets)
    tol = (max(blinds) if blinds else 0.0) or 1.0
    invested: Dict[str, float] = defaultdict(float)
    folded: set = set()
    all_in_players: set = set()
    all_in_by_street: Dict[str, List[str]] = defaultdict(list)

    for street in streets or []:
        sname = (street.get("name") or "").lower()
        bet_line: Dict[str, float] = defaultdict(float)

        for act in street.get("actions") or []:
            player = str(act.get("player") or "")
            action = (act.get("action") or "").lower()
            amount = float(act.get("amount") or 0)
            if not player or player == "Uncalled":
                continue
            if action == "fold":
                folded.add(player)

            if action in ("raise", "bet") and amount > bet_line[player]:
                inc = amount - bet_line[player]
                bet_line[player] = amount
            elif action in ("call", "bet", "post", "all-in", "allin"):
                inc = amount
                if action != "post" or amount in blinds:
                    bet_line[player] += amount
            else:
                inc = 0.0
            if action != "fold":
                invested[player] += inc

            start = stacks.get(player, 0.0)
            if (
                start > 0
                and invested[player] >= start - tol
                and action in ("raise", "bet", "call", "all-in", "allin")
                and player not in folded
            ):
                if player not in all_in_players:
                    all_in_by_street[sname].append(player)
                all_in_players.add(player)

    winner_names = {str(w.get("name") or "") for w in (winners or [])}
    hero_won_pot = hero_name in winner_names or hero_won > 0

    eliminated: List[str] = []
    if hero_won_pot:
        for p in sorted(all_in_players):
            if p and p != hero_name and p not in winner_names:
                eliminated.append(p)

    return {
        "eliminated": eliminated,
        "elimination_count": len(eliminated),
        "all_in_opponents": sorted(p for p in all_in_players if p != hero_name),
        "all_in_by_street": dict(all_in_by_street),
        "multiway_all_in": len(all_in_players) >= 3,
    }


def _format_table_dynamics_block(
    dynamics: Dict[str, Any],
    meta: dict,
    hero_won: float,
    final_made_hand: Optional[Dict[str, Any]] = None,
) -> str:
    """Inject MTT table-dynamics and knockout context for the coach prompt."""
    if not dynamics:
        return ""
    lines = ["TABLE DYNAMICS (authoritative — discuss in summary and relevant spots):"]
    is_tournament = bool(meta.get("is_tournament"))
    pot = float(meta.get("pot") or 0)
    if is_tournament:
        lines.append("- Format: MTT tournament (antes included in pot-odds math).")
    if pot > 0:
        lines.append(f"- Total pot: {_format_pot_size(pot, is_tournament)}.")

    n_elim = int(dynamics.get("elimination_count") or 0)
    eliminated = dynamics.get("eliminated") or []
    if n_elim > 0 and hero_won > 0:
        names = ", ".join(eliminated)
        lines.append(
            f"- KNOCKOUTS: Hero eliminated {n_elim} player(s): {names}. "
            "This is a massive chip gain — table opens up, fewer opponents remain. "
            "Do NOT reduce this to only 'set play' or 'top pair'; emphasize pot size and eliminations."
        )
        if n_elim >= 2:
            lines.append(
                f"- Hero knocked out {n_elim} players in one pot — highlight this in the summary."
            )
    elif dynamics.get("all_in_opponents"):
        opp = ", ".join(dynamics["all_in_opponents"])
        lines.append(f"- All-in opponents in this hand: {opp}.")

    if dynamics.get("multiway_all_in"):
        lines.append("- Multi-way all-in pot — discuss remaining opponents and pot commitment.")

    if final_made_hand:
        lines.append(
            f"- Final made hand (river): {final_made_hand.get('label')} — use this in summary, "
            "not flop-only strength labels."
        )

    lines.append(
        "- Do NOT invent bounty payouts unless explicitly shown in the hand history."
    )
    return "\n".join(lines)


def _format_hero_decisions(spots: List[Dict[str, Any]]) -> str:
    """Render hero decision points with legality + odds for the prompt."""
    if not spots:
        return "  (parse from action log below)"
    lines: List[str] = []
    for i, s in enumerate(spots, 1):
        amt = f" {s['amount']:.0f}" if s.get("amount") else ""
        bits = [f"{s['street']}: {s['action']}{amt}"]
        if s["to_call"] > 0:
            bits.append(f"to_call {s['to_call']:.0f}")
            if s.get("pot_odds"):
                if s.get("multiway"):
                    bits.append(format_pot_odds_line(s))
                    wrong = float(s.get("hu_pot_odds_wrong") or 0)
                    if wrong > float(s["pot_odds"]) + 0.001:
                        bits.append(f"DO NOT use naive HU odds {wrong:.0%}")
                else:
                    bits.append(f"pot_odds {s['pot_odds']:.0%}")
            if s.get("num_callers_facing", 0) > 0:
                bits.append(f"callers {s['num_callers_facing']}")
            if s.get("num_players_in_pot", 0) >= 3:
                bits.append(f"players_in_pot {s['num_players_in_pot']}")
            if s.get("dead_money", 0) > 0:
                bits.append(f"dead_money {s['dead_money']:.0f}")
                bits.append(f"ante {s.get('ante_per_player', 0):.0f}/player")
            bd = s.get("pot_size_breakdown") or {}
            if bd:
                bits.append(
                    f"pot=antes {bd.get('antes', 0):.0f}+blinds {bd.get('blinds', 0):.0f}"
                    f"+bets {bd.get('current_street', 0):.0f}={bd.get('total_pot', 0):.0f}"
                )
        bits.append(f"eff_stack {s['effective_stack']:.0f}")
        if s["facing_all_in"]:
            bits.append("FACING ALL-IN")
        legal = "/".join(s["legal_actions"])
        bits.append(f"LEGAL ACTIONS=[{legal}]")
        if not s["can_raise"]:
            bits.append("raising NOT available")
        eq_note = multiway_equity_note(s)
        if eq_note:
            bits.append(eq_note)
        board_at = s.get("board_at_spot")
        if board_at:
            bits.append(f"board [{board_at}]")
        made_label = s.get("made_hand_label")
        if made_label:
            bits.append(f"MADE HAND={made_label} (authoritative — do NOT downgrade)")
        spot_eq = s.get("spot_equity")
        if spot_eq is not None:
            bits.append(f"equity ~{spot_eq:.0f}%")
        elim_pending = s.get("eliminations_pending")
        if elim_pending:
            bits.append(elim_pending)
        lines.append(f"  {i}. " + " | ".join(bits))
    return "\n".join(lines)


def _format_hole_card_facts(hero_cards: str) -> str:
    """Authoritative hole-card block for the hand-analysis prompt."""
    facts = equity_engine.describe_hole_cards(hero_cards)
    if not facts:
        return ""
    lines = [
        "HOLE CARD FACTS (authoritative — do NOT contradict):",
        f"- Exact cards: {facts['exact_cards']}",
        f"- Canonical notation: {facts['notation']}",
    ]
    if facts["pair"]:
        lines.append("- Pocket pair. Do NOT call it suited or offsuit.")
    elif facts["suited"]:
        lines.append("- Suited: YES. Offsuit: NO. Never use an 'o' suffix or say offsuit.")
    else:
        lines.append("- Suited: NO. Offsuit: YES. Never use an 's' suffix or say suited.")
    if facts["connector"]:
        lines.append("- One-gap connector: yes")
    elif facts.get("one_gapper"):
        lines.append("- One-gapper: yes")
    lines.append(
        f"Hero's exact hole cards are {facts['exact_cards']} ({facts['notation']}). "
        "Use ONLY this notation when naming the hand; never substitute the wrong suit status."
    )
    return "\n".join(lines)


def _scrub_wrong_hand_notation(text: str, facts: Optional[Dict[str, Any]]) -> str:
    """Correct LLM hallucinations that swap suited/offsuit or pair suffixes."""
    if not text or not facts:
        return text
    notation = str(facts.get("notation") or "")
    if not notation:
        return text
    hi_lo = notation.rstrip("so")
    out = text
    if facts.get("pair"):
        out = re.sub(rf"\b{re.escape(hi_lo)}[so]\b", hi_lo, out, flags=re.IGNORECASE)
        out = re.sub(r"\b(suited|offsuit)\s+(pair|hand)\b", r"\2", out, flags=re.IGNORECASE)
        return out
    wrong_suffix = "o" if facts.get("suited") else "s"
    correct_suffix = "s" if facts.get("suited") else "o"
    wrong_notation = f"{hi_lo}{wrong_suffix}"
    correct_notation = notation
    out = re.sub(re.escape(wrong_notation), correct_notation, out, flags=re.IGNORECASE)
    if facts.get("offsuit"):
        out = re.sub(r"\bsuited\s+connectors?\b", "offsuit connector", out, flags=re.IGNORECASE)
        out = re.sub(r"\bmarginal\s+suited\b", "marginal offsuit", out, flags=re.IGNORECASE)
        out = re.sub(r"\bloose\s+suited\b", "loose offsuit", out, flags=re.IGNORECASE)
        out = re.sub(
            rf"\bsuited\s+({re.escape(hi_lo)})\b",
            rf"offsuit \1",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            rf"\b({re.escape(hi_lo)})\s+suited\b",
            rf"\1 offsuit",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            rf"\bopen\s+with\s+{re.escape(hi_lo)}{correct_suffix}\b",
            f"open with {correct_notation}",
            out,
            flags=re.IGNORECASE,
        )
    elif facts.get("suited"):
        out = re.sub(r"\boffsuit\s+connectors?\b", "suited connector", out, flags=re.IGNORECASE)
        out = re.sub(r"\bmarginal\s+offsuit\b", "marginal suited", out, flags=re.IGNORECASE)
        out = re.sub(
            rf"\boffsuit\s+({re.escape(hi_lo)})\b",
            rf"suited \1",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            rf"\b({re.escape(hi_lo)})\s+offsuit\b",
            rf"\1 suited",
            out,
            flags=re.IGNORECASE,
        )
    return out


def _apply_hole_card_guard(result: dict, facts: Optional[Dict[str, Any]]) -> None:
    """Scrub wrong suited/offsuit labels from all coach text fields."""
    if not facts or not isinstance(result, dict):
        return
    for key in ("summary", "biggest_leak"):
        if isinstance(result.get(key), str):
            result[key] = _scrub_wrong_hand_notation(result[key], facts)
    for bucket in ("streets", "hero_actions"):
        for item in result.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            for field in ("comment", "hero_action", "facing"):
                if isinstance(item.get(field), str):
                    item[field] = _scrub_wrong_hand_notation(item[field], facts)
    tags = result.get("tags")
    if isinstance(tags, list):
        result["tags"] = [
            _scrub_wrong_hand_notation(t, facts) if isinstance(t, str) else t for t in tags
        ]


def _scrub_wrong_hand_strength(text: str, made: Optional[Dict[str, Any]]) -> str:
    """Replace downgraded hand-strength labels (e.g. 'top pair' for a full house)."""
    if not text or not made:
        return text
    label = str(made.get("label") or "")
    short = str(made.get("short") or label.split(",")[0])
    cat = int(made.get("category") or -1)
    out = text
    forbidden = list(made.get("forbidden_terms") or [])
    if cat >= 6:
        forbidden.extend(["top set", "set extraction", "top set extraction"])
    for term in forbidden:
        if not term:
            continue
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, out, flags=re.IGNORECASE):
            out = re.sub(pattern, short, out, flags=re.IGNORECASE)
    if cat >= 6 and re.search(r"\btop\s+set\b", out, re.IGNORECASE):
        out = re.sub(r"\btop\s+set\b", short, out, flags=re.IGNORECASE)
    return out


def _align_hero_actions_to_spots(
    hero_actions: List[dict], spots: List[Dict[str, Any]]
) -> List[int]:
    """Map each hero_actions entry to the best-matching spot index."""
    if not spots:
        return []
    by_street: Dict[str, List[int]] = defaultdict(list)
    for i, s in enumerate(spots):
        by_street[str(s.get("street") or "").lower()].append(i)
    street_cursor: Dict[str, int] = defaultdict(int)
    mapping: List[int] = []
    fallback = 0
    for item in hero_actions:
        if not isinstance(item, dict):
            mapping.append(min(fallback, len(spots) - 1))
            fallback += 1
            continue
        st = str(item.get("street") or "").lower()
        candidates = by_street.get(st) or []
        cursor = street_cursor[st]
        if cursor < len(candidates):
            mapping.append(candidates[cursor])
            street_cursor[st] = cursor + 1
        else:
            mapping.append(min(fallback, len(spots) - 1))
        fallback += 1
    return mapping


def _apply_made_hand_guard(
    result: dict,
    spots: List[Dict[str, Any]],
    final_made: Optional[Dict[str, Any]] = None,
) -> None:
    """Scrub hand-strength hallucinations using per-spot and final made-hand facts."""
    if not isinstance(result, dict):
        return

    hero_actions = result.get("hero_actions") or []
    spot_map = _align_hero_actions_to_spots(hero_actions, spots)
    for i, item in enumerate(hero_actions):
        if not isinstance(item, dict) or i >= len(spot_map):
            continue
        made = spots[spot_map[i]].get("made_hand")
        if not made:
            continue
        for field in ("comment", "hero_action", "facing"):
            if isinstance(item.get(field), str):
                item[field] = _scrub_wrong_hand_strength(item[field], made)

    if final_made:
        for key in ("summary", "biggest_leak"):
            if isinstance(result.get(key), str):
                result[key] = _scrub_wrong_hand_strength(result[key], final_made)
        for item in result.get("streets") or []:
            if not isinstance(item, dict):
                continue
            street_key = str(item.get("street") or "").lower()
            if street_key == "river" or not street_key:
                for field in ("comment", "hero_action", "facing"):
                    if isinstance(item.get(field), str):
                        item[field] = _scrub_wrong_hand_strength(item[field], final_made)
            else:
                spot_made = next(
                    (s.get("made_hand") for s in spots if s.get("street") == street_key and s.get("made_hand")),
                    None,
                )
                if spot_made:
                    for field in ("comment", "hero_action", "facing"):
                        if isinstance(item.get(field), str):
                            item[field] = _scrub_wrong_hand_strength(item[field], spot_made)


def _dedupe_street_summaries(streets: List[dict]) -> List[dict]:
    """Keep one street summary per street name (LLM sometimes repeats flop/turn)."""
    seen: set = set()
    out: List[dict] = []
    for item in streets or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("street") or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _attach_elimination_context_to_spots(
    spots: List[Dict[str, Any]], dynamics: Dict[str, Any]
) -> None:
    """Note pending eliminations at each hero call vs all-in."""
    if not dynamics.get("eliminated"):
        return
    all_in_by = dynamics.get("all_in_by_street") or {}
    seen_all_in: set = set()
    for spot in spots:
        sname = str(spot.get("street") or "").lower()
        for p in all_in_by.get(sname, []):
            seen_all_in.add(p)
        if spot.get("facing_all_in") and seen_all_in:
            pending = [p for p in dynamics["eliminated"] if p in seen_all_in or p in all_in_by.get(sname, [])]
            if pending:
                spot["eliminations_pending"] = (
                    f"eliminations if hero wins: {', '.join(pending)}"
                )


def _inject_knockout_summary(
    result: dict,
    dynamics: Dict[str, Any],
    final_made: Optional[Dict[str, Any]],
    hero_won: float,
) -> None:
    """Ensure summary mentions knockouts and final hand strength when hero won."""
    if hero_won <= 0 or not isinstance(result, dict):
        return
    n = int(dynamics.get("elimination_count") or 0)
    summary = str(result.get("summary") or "")
    low = summary.lower()
    parts: List[str] = []
    if n > 0 and "elimin" not in low and "knocked out" not in low and "knockout" not in low:
        names = ", ".join(dynamics.get("eliminated") or [])
        parts.append(f"Won massive multi-way pot, eliminated {n} player(s) ({names})")
    if final_made:
        fl = str(final_made.get("label") or "")
        if fl and fl.lower() not in low and "full house" not in low and "top set" in low:
            parts.append(f"Made {fl} by river")
        elif fl and n > 0 and fl.lower() not in low:
            parts.append(f"Final hand: {fl}")
    if parts:
        prefix = ". ".join(parts) + ". "
        result["summary"] = prefix + summary if summary else prefix.rstrip()


def _spot_constraint_flags(spots: List[Dict[str, Any]]) -> Dict[str, bool]:
    """Hand-level flags used to scrub illegal/committing-action advice."""
    faced_all_in_pre = any(
        s["street"].startswith("pre") and s["facing_all_in"] for s in spots
    )
    committed_all_in = any(
        s["facing_all_in"] and s["action"] in ("call", "raise", "bet")
        for s in spots
    )
    any_multiway = any(s.get("multiway") for s in spots)
    return {
        "faced_all_in_preflop": faced_all_in_pre,
        "committed_all_in": committed_all_in,
        "multiway": any_multiway,
    }


def _compute_equity_block(
    hero_cards: str, board_cards: List[str], spots: Optional[List[Dict[str, Any]]] = None
) -> tuple[str, List[float]]:
    """Compute ground-truth equities for the hand so the coach can't invent them."""
    allowed: List[float] = []
    lines_extra: List[str] = []
    try:
        block = equity_engine.coach_equity_block(hero_cards, board_cards or "")
    except Exception as exc:  # never let equity math break analysis
        log.warning("[AI] equity grounding failed: %s", exc)
        block = None
    if block:
        allowed.extend(float(x) for x in block.get("allowed_equities", []))
        base_text = block.get("text", "")
    else:
        base_text = ""

    for i, spot in enumerate(spots or [], 1):
        eq = spot.get("spot_equity")
        if eq is not None:
            allowed.append(float(eq))
            board_at = spot.get("board_at_spot") or "(preflop)"
            made = spot.get("made_hand_label") or ""
            made_note = f", made hand: {made}" if made else ""
            lines_extra.append(
                f"    Spot {i} ({spot.get('street')}, board [{board_at}]{made_note}): {eq:.0f}%"
            )

    if not base_text and not lines_extra:
        return "", allowed
    text = base_text
    if lines_extra:
        extra = (
            "PER-DECISION EQUITIES (board + made hand at each hero action — quote these):\n"
            + "\n".join(lines_extra)
        )
        text = f"{text}\n\n{extra}" if text else extra
    return text, allowed


def _build_theory_context_block(meta: dict, spots: List[Dict[str, Any]]) -> str:
    """Inject unified CFR+ chart + neural value block for tournament spots."""
    try:
        from theory.charts import build_coach_theory_block
    except ImportError:
        return ""
    return build_coach_theory_block(meta, spots)


def build_hand_analysis_prompt(
    hero_name: str,
    hand_meta: Optional[dict],
    raw_text: str,
) -> tuple[str, str, float, Dict[str, Any], List[float], List[Dict[str, Any]]]:
    """Return (user_prompt, system_prompt, hero_won, spot_flags, allowed_equities, spots)."""
    meta = hand_meta or {}
    hero_won = float(meta.get("hero_won") or 0)
    is_tournament = bool(meta.get("is_tournament"))
    outcome = _outcome_label(hero_won)
    hero_cards = meta.get("hero_cards") or "unknown"
    board = " ".join(meta.get("board_cards") or []) or "unknown"
    position = meta.get("hero_position") or "unknown"
    pot_display = _format_pot_size(meta.get("pot") or 0, is_tournament)
    net_display = _format_amount(hero_won, is_tournament)
    streets = meta.get("streets") or []
    action_log = _format_action_log(streets, hero_name, is_tournament)
    spots = _hero_spot_facts(streets, meta.get("players") or [], hero_name)
    spots = _enrich_spots_with_made_hands(streets, spots, hero_name, meta.get("hero_cards") or "")
    dynamics = detect_knockouts(
        streets,
        meta.get("players") or [],
        hero_name,
        meta.get("winners") or [],
        hero_won=hero_won,
    )
    _attach_elimination_context_to_spots(spots, dynamics)
    final_made = equity_engine.describe_made_hand(
        meta.get("hero_cards") or "", meta.get("board_cards") or []
    )
    spot_flags = _spot_constraint_flags(spots)
    spot_flags["table_dynamics"] = dynamics
    spot_flags["final_made_hand"] = final_made
    hole_card_facts = equity_engine.describe_hole_cards(meta.get("hero_cards") or "")
    if hole_card_facts:
        spot_flags = {**spot_flags, "hole_card_facts": hole_card_facts}
    hole_card_block = _format_hole_card_facts(meta.get("hero_cards") or "")

    winners = meta.get("winners") or []
    winner_text = ", ".join(
        f"{w.get('name')} ({_format_amount(w.get('amount', 0), is_tournament)})"
        for w in winners
    ) or ("Hero collected pot" if hero_won > 0 else "see hand history")

    outcome_block = (
        f"OUTCOME (authoritative — do not contradict):\n"
        f"- Result: {outcome.upper()} ({net_display} net)\n"
        f"- Pot: {pot_display}\n"
        f"- Winners: {winner_text}\n"
    )
    if hero_won > 0:
        outcome_block += (
            "- Hero WON this hand. Never recommend folding the action that won the pot.\n"
            "- Evaluate each Hero decision for efficiency; acknowledge value when Hero made a hand.\n"
        )
    elif hero_won < 0:
        outcome_block += "- Hero LOST net chips. Identify leaks in Hero's actual decisions.\n"

    hero_decisions = _format_hero_decisions(spots)
    table_dynamics_block = _format_table_dynamics_block(
        dynamics, meta, hero_won, final_made_hand=final_made
    )
    made_hand_rules = (
        "MADE HAND RULES (critical): each HERO DECISION POINT lists MADE HAND=... "
        "(authoritative for that moment). Use ONLY that label — never call a set, "
        "full house, or two pair 'top pair'. On later streets the made hand may upgrade "
        "(e.g. set on flop → full house on river); always use the spot-specific label."
    )

    display_cards = (hole_card_facts or {}).get("exact_cards") or hero_cards
    user_prompt = HAND_PROMPT.format(
        hero=hero_name,
        position=position,
        hero_cards=display_cards,
        hole_card_block=hole_card_block,
        board=board,
        outcome=outcome,
        net=net_display,
        pot=pot_display,
        outcome_block=outcome_block,
        table_dynamics_block=table_dynamics_block,
        made_hand_rules=made_hand_rules,
        hero_decisions=hero_decisions,
        action_log=action_log,
        hand=str(raw_text)[:2200],
    )

    system = _hand_analysis_system(hero_won, hole_card_block, made_hand_rules)
    equity_text, allowed_equities = _compute_equity_block(
        meta.get("hero_cards") or "", meta.get("board_cards") or [], spots=spots
    )
    if equity_text:
        system = f"{system}\n\n{equity_text}"
    theory_block = _build_theory_context_block(meta, spots)
    if theory_block:
        system = f"{system}\n\n{theory_block}"
    return user_prompt, system, hero_won, spot_flags, allowed_equities, spots


def _hand_analysis_system(hero_won: float, hole_card_block: str = "", made_hand_rules: str = "") -> str:
    base = (
        "You are an elite poker coach. Return ONLY valid JSON matching the schema. "
        "Evaluate EVERY Hero action in chronological order with concise comments (max 25 words each). "
        "Include one street summary per street Hero reached (not duplicate flop/turn entries). "
        "Include per-action hero_actions entries for every Hero action. "
        "Grades: A=strong, B=solid, C=marginal, D=leak.\n"
    )
    if hole_card_block:
        base += (
            "HOLE CARDS (critical): the prompt includes authoritative HOLE CARD FACTS. "
            "Name Hero's hand ONLY with the exact cards and canonical notation provided. "
            "Never call an offsuit hand suited (e.g. J9o is NOT J9s) and never invert pair/suit labels.\n"
        )
    if made_hand_rules:
        base += f"{made_hand_rules}\n"
    base += (
        "TABLE DYNAMICS: when knockouts are listed, discuss eliminations, pot size, and "
        "MTT stack gain — not just hand strength. Do not invent bounties.\n"
        "ACTION LEGALITY (critical): each Hero decision point lists its LEGAL ACTIONS. "
        "ONLY ever recommend an action from that list. If a spot is marked FACING ALL-IN or "
        "'raising NOT available', the only options are call or fold — NEVER suggest 3-betting, "
        "raising, re-raising, or 'more/less aggression' there; you cannot raise an all-in.\n"
        "AGGRESSION LABELS: calling an all-in, jamming, or getting all-in is a committing, "
        "aggressive action — never call it 'passive' or 'lack of aggression'. Do not assign a "
        "Passive/Tight-Passive style or a passivity leak based on calling off / getting it in.\n"
        "JUDGE THE DECISION, NOT THE RESULT: grade EV given ranges, position, and pot odds — "
        "never based on the runout or which card hit. Do not let the winning/losing card change a "
        "grade and do not say a call/get-in was wrong because Hero was outdrawn.\n"
        "MULTI-WAY POT ODDS (critical): when 3+ players are in the pot or Hero faces a bet with "
        "callers already in, use the multi-way pot odds from HERO DECISION POINTS (includes all "
        "callers' chips + antes). NEVER quote naive heads-up pot odds that ignore callers. "
        "Equity vs one opponent overstates strength — tighten ranges with more callers."
    )
    if hero_won > 0:
        base += (
            " Hero WON this pot — do NOT say folding was correct on the winning line or final action. "
            "Focus on sizing, earlier streets, and whether alternatives were higher EV."
        )
    return base


_FOLD_ADVICE_RE = re.compile(
    r"\b(should\s+(have\s+)?fold(ed)?|fold\s+(the|on|this|that|here)|"
    r"misplay\s+to\s+call|should\s+not\s+call|bad\s+call)\b",
    re.IGNORECASE,
)


def _scrub_fold_advice(text: str, hero_won: float) -> str:
    if hero_won <= 0 or not text:
        return text
    if _FOLD_ADVICE_RE.search(text):
        return re.sub(
            _FOLD_ADVICE_RE,
            "line was profitable",
            text,
            count=1,
        )
    return text


_ALLIN_RAISE_RE = re.compile(
    r"\b(should(?:'ve|\s+have)?\s+(?:also\s+)?(?:3-?bet|three-?bet|re-?raise\w*|raise\w*|jam\w*|shove\w*)"
    r"|(?:3-?bet|three-?bet)(?:ting|s)?"
    r"|(?:be(?:en)?|play(?:ed|ing)?|get(?:ting)?)\s+more\s+aggressive"
    r"|(?:lack of|more|insufficient|not enough|missing|needs?\s+more)\s+(?:preflop\s+)?aggression"
    r"|to\s+define\s+(?:your\s+|his\s+|the\s+)?range)\b",
    re.IGNORECASE,
)
_PASSIVE_RE = re.compile(
    r"\b(too\s+passive|tight-?passive|passive\s+(?:preflop|play|tendenc\w+)?|"
    r"passively)\b",
    re.IGNORECASE,
)
# Results-oriented reasoning — the runout must not drive a grade.
_RESULTS_RE = re.compile(
    r"\b(outdrawn|out-?drawn|sucked?\s*out|suck-?out|bad\s*beat|cooler|rivered|"
    r"two-?outer|one-?outer|got\s+unlucky|hit(?:s|ting)?\s+(?:his|her|their|a|the)\s+\w+)\b",
    re.IGNORECASE,
)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")

_ALLIN_FALLBACK = (
    "Facing an all-in, the only options were call or fold; "
    "judge the call on EV vs range, not the runout."
)


def _scrub_allin_advice(text: str, fallback: str = _ALLIN_FALLBACK) -> str:
    """Drop illegal-raise, passive, and results-oriented sentences for all-in spots."""
    if not text:
        return text
    kept = [
        p for p in _SENT_SPLIT_RE.split(str(text))
        if p.strip()
        and not _ALLIN_RAISE_RE.search(p)
        and not _PASSIVE_RE.search(p)
        and not _RESULTS_RE.search(p)
    ]
    cleaned = re.sub(r"\s*;\s*$", ".", " ".join(kept).strip())
    return cleaned if cleaned else fallback


def _apply_allin_constraints(result: dict, flags: Dict[str, bool]) -> None:
    """Enforce action-validity + non-passive labeling when Hero faced an all-in."""
    faced_pre = flags.get("faced_all_in_preflop")
    committed = flags.get("committed_all_in")
    if not (faced_pre or committed):
        return

    for item in result.get("streets") or []:
        if not isinstance(item, dict):
            continue
        if faced_pre and str(item.get("street", "")).lower().startswith("pre"):
            for key in ("comment", "hero_action", "facing"):
                if item.get(key):
                    item[key] = _scrub_allin_advice(str(item[key]))
    for item in result.get("hero_actions") or []:
        if not isinstance(item, dict):
            continue
        if faced_pre and str(item.get("street", "")).lower().startswith("pre"):
            if item.get("comment"):
                item["comment"] = _scrub_allin_advice(str(item["comment"]))

    if result.get("summary"):
        result["summary"] = _scrub_allin_advice(str(result["summary"]))
    leak = result.get("biggest_leak")
    if isinstance(leak, str) and (
        _ALLIN_RAISE_RE.search(leak) or _PASSIVE_RE.search(leak)
    ):
        result["biggest_leak"] = None

    tags = result.get("tags")
    if isinstance(tags, list):
        result["tags"] = [
            t for t in tags
            if not (
                isinstance(t, str)
                and (_ALLIN_RAISE_RE.search(t) or _PASSIVE_RE.search(t)
                     or "3bet" in t.lower() or "passive" in t.lower())
            )
        ]

    if committed:
        style = str(result.get("play_style") or "")
        if "passive" in style.lower():
            result["play_style"] = "Unknown"


_EQUITY_CLAIM_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%\s*(equity|pot equity|to win)\b", re.IGNORECASE)


def _scrub_invented_equity(text: str, allowed: List[float]) -> str:
    """Replace equity %s that don't match any computed value with the nearest one.

    Deterministic guard: when we have engine-computed equities, any equity claim
    in the model's text that deviates from all of them by >4 points is corrected
    to the closest computed figure, so hallucinated numbers never reach the user.
    """
    if not text or not allowed:
        return text

    def _fix(match: "re.Match") -> str:
        try:
            claimed = float(match.group(1))
        except ValueError:
            return match.group(0)
        nearest = min(allowed, key=lambda a: abs(a - claimed))
        if abs(nearest - claimed) <= 4.0:
            return match.group(0)
        return f"{nearest:.0f}% {match.group(2)}"

    return _EQUITY_CLAIM_RE.sub(_fix, text)


def _apply_equity_guard(result: dict, allowed: List[float]) -> None:
    """Correct invented equity numbers across all free-text fields."""
    if not allowed or not isinstance(result, dict):
        return
    for key in ("summary", "biggest_leak"):
        if isinstance(result.get(key), str):
            result[key] = _scrub_invented_equity(result[key], allowed)
    for bucket in ("streets", "hero_actions"):
        for item in result.get(bucket) or []:
            if isinstance(item, dict) and isinstance(item.get("comment"), str):
                item["comment"] = _scrub_invented_equity(item["comment"], allowed)


def _enrich_analysis_pot_odds(
    result: dict, spots: List[Dict[str, Any]], hero_name: str
) -> None:
    """Attach spot facts and multi-way pot-odds notes to hero action comments."""
    if not spots:
        return
    result["spot_facts"] = spots
    hero_actions = result.get("hero_actions") or []
    for i, item in enumerate(hero_actions):
        if not isinstance(item, dict) or i >= len(spots):
            continue
        spot = spots[i]
        if float(spot.get("to_call") or 0) <= 0:
            continue
        item["pot_odds"] = spot.get("pot_odds")
        item["multiway"] = bool(spot.get("multiway"))
        item["num_players_in_pot"] = spot.get("num_players_in_pot")
        if not spot.get("multiway"):
            continue
        line = format_pot_odds_line(spot)
        if not line:
            continue
        comment = str(item.get("comment") or "")
        if "pot odds" not in comment.lower() and line not in comment:
            item["comment"] = f"{line}. {comment}" if comment else line


def _normalize_hand_analysis(
    result: dict,
    hero_won: float,
    spot_flags: Optional[Dict[str, Any]] = None,
    spots: Optional[List[Dict[str, Any]]] = None,
    hero_name: str = "Hero",
) -> dict:
    """Ensure schema fields exist and sanitize won-hand advice."""
    outcome = result.get("outcome") or _outcome_label(hero_won)
    result["outcome"] = outcome

    streets = result.get("streets")
    if not isinstance(streets, list):
        legacy = result.get("street_notes") or {}
        if isinstance(legacy, dict) and legacy:
            streets = [
                {"street": k.lower(), "comment": str(v), "grade": "C"}
                for k, v in legacy.items()
                if v and str(v).strip() not in ("..", ".")
            ]
        else:
            streets = []
    result["streets"] = _dedupe_street_summaries(streets)

    hero_actions = result.get("hero_actions")
    if not isinstance(hero_actions, list):
        hero_actions = []
    result["hero_actions"] = hero_actions

    if hero_won > 0:
        for item in streets:
            if isinstance(item, dict):
                for key in ("comment", "hero_action"):
                    if key in item and item[key]:
                        item[key] = _scrub_fold_advice(str(item[key]), hero_won)
        for item in hero_actions:
            if isinstance(item, dict) and item.get("comment"):
                item["comment"] = _scrub_fold_advice(str(item["comment"]), hero_won)
        summary = str(result.get("summary") or "")
        if summary:
            result["summary"] = _scrub_fold_advice(summary, hero_won)
        leak = result.get("biggest_leak")
        if isinstance(leak, str) and _FOLD_ADVICE_RE.search(leak):
            result["biggest_leak"] = None

    if spot_flags:
        _apply_allin_constraints(result, spot_flags)
        _apply_hole_card_guard(result, spot_flags.get("hole_card_facts"))
        dynamics = spot_flags.get("table_dynamics") or {}
        final_made = spot_flags.get("final_made_hand")
        if spots:
            _apply_made_hand_guard(result, spots, final_made=final_made)
        _inject_knockout_summary(result, dynamics, final_made, hero_won)
        if dynamics.get("elimination_count"):
            result["knockouts"] = dynamics

    mistakes = sum(
        1
        for item in hero_actions
        if isinstance(item, dict) and str(item.get("grade", "")).upper() in ("C", "D")
    )
    mistakes += sum(
        1
        for item in streets
        if isinstance(item, dict) and str(item.get("grade", "")).upper() in ("C", "D")
    )
    if mistakes and not result.get("mistakes_found"):
        result["mistakes_found"] = min(mistakes, 10)

    if spots:
        _enrich_analysis_pot_odds(result, spots, hero_name)

    return result


# ── Prompts ───────────────────────────────────────────────────────────────────
HAND_PROMPT = """Analyze Hero's play street-by-street AND action-by-action.

{outcome_block}

{table_dynamics_block}

{made_hand_rules}

HERO PROFILE:
- Name: {hero}
- Position: {position}
- Hole cards: {hero_cards}
{hole_card_block}
- Final board: {board}

HERO DECISION POINTS (evaluate each in order — obey the LEGAL ACTIONS for each):
{hero_decisions}

PARSED ACTION LOG (chronological):
{action_log}

Return ONLY valid JSON — no markdown:
{{
  "outcome": "{outcome}",
  "streets": [
    {{
      "street": "preflop|flop|turn|river",
      "board": "cards on this street or empty preflop",
      "hero_action": "what Hero did",
      "facing": "what Hero faced before acting",
      "grade": "A|B|C|D",
      "comment": "1 sentence street summary"
    }}
  ],
  "hero_actions": [
    {{
      "street": "preflop|flop|turn|river",
      "player": "{hero}",
      "action_type": "fold|check|call|bet|raise|all-in",
      "amount": 0,
      "pot_after": null,
      "grade": "A|B|C|D",
      "comment": "facing situation, alternatives, +EV or not (max 25 words)"
    }}
  ],
  "summary": "2 sentence overall coaching note",
  "biggest_leak": "one specific leak or null if none",
  "play_style": "TAG|LAG|Passive|Aggro|Tight-Passive|Maniac",
  "mistakes_found": 0,
  "tags": ["leak_tag"],
  "ev_estimate": "+EV|-EV|Neutral|Marginal +EV|Marginal -EV",
  "confidence": 0.0
}}

Rules:
- Include ONLY streets Hero reached (skip empty streets). ONE entry per street in streets[] — no duplicates.
- hero_actions must cover EVERY Hero action from the log, in order.
- Use MADE HAND= at each decision — never downgrade (no 'top pair' for a set/full house).
- If knockouts are listed, mention eliminations and pot size in summary and relevant all-in calls.
- Net result: {net} on pot {pot}. Outcome is {outcome}.
- If outcome is won: never recommend folding the winning final action.
- Recommend ONLY actions in each spot's LEGAL ACTIONS list. If a spot is FACING ALL-IN or
  'raising NOT available', advise only call or fold — never 3-bet/raise/"be more aggressive".
- Calling an all-in or getting all-in is aggressive/committing — do NOT label it passive and do
  NOT cite it as missing preflop aggression. Pick play_style from real tendencies, not a call-off.
- Judge each decision on EV vs ranges and pot odds, NOT the runout. Do not reference the outcome
  card or say a get-in was wrong because Hero was outdrawn.
- Multi-way: when callers are listed or players_in_pot >= 3, state multi-way pot odds in comments
  (e.g. "Facing bet with 2 callers — pot odds 18% (not 25% heads-up naive)").

Raw hand history (reference):
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

EQUITY RULE (critical): NEVER state, estimate, or guess an equity/odds percentage
from memory. Whenever you need an equity number, call the calculate_equity tool
(hero cards vs a villain hand, a range, or a position+action) and quote only the
number it returns. If you have not computed it with the tool, do not give a
percentage. Open/defend/3-bet frequencies should come from the reference ranges.

Player context:
{context}"""

DATASET_CONTEXT_HEADER = (
    "You have access to this player's full database summary. "
    "Ground all coaching in these career stats, positional tendencies, and known leak patterns."
)

WEB_ACCESS_NOTE = (
    "LIVE WEB ACCESS: You have live internet access via real-time web search "
    "(and any retrieved snippets below). Use current, up-to-date information and cite "
    "sources/URLs when relevant. Do NOT claim you lack internet access or are limited "
    "to a training cutoff — you can retrieve and reference current data."
)

WEB_AVAILABLE_NOTE = (
    "WEB SEARCH ON REQUEST: Live web search is enabled but was not used for this message. "
    "Use database tools for stats and hand history. If the player asks for research, "
    "sources, or current online strategy, call the web_search tool or they can rephrase "
    "with 'research' / 'look up' / 'online sources'. Do NOT claim you permanently lack "
    "internet access — web is available when needed."
)

WEB_SEARCH_MODES = ("off", "on_demand", "always")

def _friendly_api_error(exc: Exception, provider: str = "AI") -> str:
    """Map provider HTTP errors to user-facing hints (never include secrets)."""
    msg = str(exc).strip()
    low = msg.lower()
    labels = {
        "asi1": "ASI:One",
        "openai": "OpenAI",
        "deepseek": "DeepSeek",
        "gemini": "Gemini",
        "anthropic": "Claude",
    }
    env_vars = {
        "asi1": "ASI_ONE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    label = labels.get(provider, provider)
    env_var = env_vars.get(provider, "API key")
    if any(x in low for x in ("401", "unauthorized", "invalid api key", "authentication")):
        return (
            f"{label} rejected your API key (401). Check {env_var} in .env at repo root "
            "and fully restart LeakSnipe."
        )
    if any(
        x in low
        for x in ("429", "rate limit", "quota", "too many requests", "rate_limit")
    ) or (provider == "asi1" and "400" in low and "rate" in low):
        extra = (
            " Check usage at https://asi1.ai (API dashboard)."
            if provider == "asi1"
            else ""
        )
        return (
            f"{label} rate limit hit. Wait a minute and retry, or switch provider in Settings."
            f"{extra}"
        )
    if any(x in low for x in ("404", "model_not_found", "does not exist", "model not found")):
        hint = (
            " Use asi1, asi1-ultra, or asi1-mini (see docs.asi1.ai/documentation/models)."
            if provider == "asi1"
            else ""
        )
        return f"{label} model not found. Check Settings → ASI:One model.{hint}"
    if any(
        x in low
        for x in ("timeout", "timed out", "connection error", "connect timeout", "failed to connect")
    ):
        return (
            f"{label} request timed out or could not connect — service may be slow or offline. "
            "Retry in a moment."
        )
    if provider == "asi1" and any(x in low for x in ("500", "502", "503", "504", "internal server")):
        return f"{label} server error — retry shortly. Persistent failures: check https://docs.asi1.ai"
    if len(msg) > 240:
        msg = msg[:237] + "..."
    return msg


def _ollama_available():
    global _ollama_available_cache
    if _ollama_available_cache is not None:
        return _ollama_available_cache
    try:
        urllib.request.urlopen(f"{_ollama_base()}/api/tags", timeout=2)
        _ollama_available_cache = True
    except Exception:
        _ollama_available_cache = False
    return _ollama_available_cache

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

def _split_provider_model(used_prov: str) -> tuple:
    """Split a provider ref like asi1:model or ollama:qwen into (id, model)."""
    if not used_prov or used_prov == "none":
        return "none", ""
    if ":" in used_prov:
        prov, model = used_prov.split(":", 1)
        return prov, model
    if used_prov in (OPENAI_CHAT_MODEL, "responses-api") or used_prov.startswith("gpt-"):
        return "openai", used_prov
    if used_prov.startswith("claude"):
        return "anthropic", used_prov
    return used_prov, ""


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

# ── Tool-call parsing (text pseudo tool-calls) ─────────────────────────────────
# Some ASI:One models (and fallbacks) emit tool calls as text in a Hermes/Qwen-style
# format instead of the structured `tool_calls` field, which then leaks to the user
# verbatim, e.g.:
#   <tool_call>get_hand<arg_key>hand_id</arg_key><arg_value>ACR_123</arg_value></tool_call>
#   <tool_call>{"name": "get_hand", "arguments": {"hand_id": "ACR_123"}}</tool_call>
# We parse and execute these as a safety net, and strip any stray tags so raw
# tool-call syntax is never surfaced.
_TEXT_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_ARG_PAIR_RE = re.compile(
    r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
    re.DOTALL | re.IGNORECASE,
)


def _coerce_arg_value(raw: str) -> Any:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _parse_text_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse text pseudo tool-calls a model emits instead of structured tool_calls."""
    calls: List[Dict[str, Any]] = []
    if not content or "<tool_call>" not in content.lower():
        return calls
    for block in _TEXT_TOOL_CALL_RE.findall(content):
        block = block.strip()
        if block.startswith("{"):  # JSON form: {"name": ..., "arguments": {...}}
            try:
                obj = json.loads(block)
                name = obj.get("name") or obj.get("tool") or ""
                args = obj.get("arguments") or obj.get("args") or {}
                if isinstance(args, str):
                    args = _coerce_arg_value(args)
                if name:
                    calls.append(
                        {"name": str(name), "arguments": args if isinstance(args, dict) else {}}
                    )
                continue
            except Exception:
                pass
        # XML form: NAME<arg_key>k</arg_key><arg_value>v</arg_value>...
        name = re.split(r"<", block, 1)[0].strip()
        args = {k.strip(): _coerce_arg_value(v) for k, v in _TOOL_ARG_PAIR_RE.findall(block)}
        if name:
            calls.append({"name": name, "arguments": args})
    return calls


def _strip_tool_call_tags(text: str) -> str:
    """Remove any tool-call markup so raw tags never reach the user."""
    if not text:
        return text
    cleaned = _TEXT_TOOL_CALL_RE.sub("", text)
    cleaned = re.sub(r"</?(tool_call|arg_key|arg_value)>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


# ── Research intent (route to live web search) ─────────────────────────────────
_RESEARCH_INTENT_RE = re.compile(
    r"\b(deep\s+research|research|online\s+sources?|search\s+(the\s+)?web|web\s+search|"
    r"look\s+(it\s+)?up|google|latest|current|up[\s-]?to[\s-]?date|recent\s+(news|study|studies)|"
    r"cite\s+sources?|find\s+sources?|on\s+the\s+(web|internet))\b",
    re.IGNORECASE,
)


def _is_research_intent(text: str) -> bool:
    return bool(_RESEARCH_INTENT_RE.search(text or ""))


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
    Primary:        ASI:One (cloud — default in settings when ASI_ONE_API_KEY is set)
    Cloud fallback: OpenAI, DeepSeek, Gemini, Claude (.env keys)
    Local fallback: Ollama (last resort only)
    """

    def __init__(self, settings: dict = None, db_path: str = None):
        self._settings       = settings or {}
        self._db_path        = db_path or self._settings.get("db_path", _DEFAULT_DB)
        self._asi1_client    = None
        self._asi1_key_fp    = ""
        self._asi1_client_fallback = None
        self._asi1_fallback_key_fp = ""
        self._openai_client  = None
        self._deepseek_client = None
        self._anthropic_client = None
        self._gemini_ready   = False
        self._active_provider = None
        self._chat_history: List[dict] = []
        self._context        = ""
        self._chat_context_ready = False
        self._last_error: Optional[str] = None
        self._last_web_context_used: bool = False
        self._provider_init_errors: Dict[str, str] = {}
        self._memory = None
        self._memory_error: Optional[str] = None
        self._init_memory()
        hero = self._active_hero()
        if self._personalization_on() and HAS_COACH_MEMORY:
            # Stable per-hero session id so ASI:One agentic runs stay continuous.
            self._asi1_session_id = _memory_session_id(hero)
        else:
            self._asi1_session_id = str(uuid.uuid4())
        self._init()
        try:
            _ensure_ai_table(self._db_path)
        except Exception:
            pass

    # ── Personalization / memory ───────────────────────────────────────────
    def _personalization_on(self) -> bool:
        return bool(self._settings.get("ai_personalization", True))

    def _agentic_tools_on(self) -> bool:
        return bool(self._settings.get("ai_agentic_tools", True))

    def _active_hero(self) -> str:
        """Hero name to scope coach memory + ASI:One session id under.

        Prefers an explicit ``coach_memory_hero`` setting (the player's primary
        identity, e.g. an alias used across sites) so memory stays under one key
        even when site hero_names list multiple aliases. Falls back to the first
        BetACR/ACR hero, then any configured hero.
        """
        primary = (self._settings.get("coach_memory_hero") or "").strip()
        if primary:
            return primary
        heroes = self._settings.get("hero_names") or {}
        for site in ("BetACR", "ACR"):
            name = (heroes.get(site) or "").strip()
            if name:
                return name.split(",")[0].strip()
        for name in heroes.values():
            name = (name or "").strip()
            if name:
                return name.split(",")[0].strip()
        return "default"

    def _memory_db_path(self) -> Optional[str]:
        configured = (self._settings.get("coach_memory_db") or "").strip()
        if configured:
            return configured
        return None  # CoachMemory falls back to repo-local coach_memory.db

    def _init_memory(self) -> None:
        if not HAS_COACH_MEMORY:
            self._memory_error = "coach_memory module unavailable"
            return
        try:
            self._memory = CoachMemory(self._memory_db_path())
        except Exception as exc:
            self._memory_error = str(exc)
            log.warning("[memory] init failed: %s", exc)

    def _memory_block(self) -> str:
        if not (self._personalization_on() and self._memory):
            return ""
        try:
            return self._memory.memory_block(self._active_hero())
        except Exception as exc:
            log.warning("[memory] block build failed: %s", exc)
            return ""

    def _remember_turn(self, user_text: str, assistant_text: str, provider: str) -> None:
        if not (self._personalization_on() and self._memory):
            return
        try:
            self._memory.add_turn(
                self._active_hero(), user_text, assistant_text, provider=provider
            )
        except Exception as exc:
            log.warning("[memory] remember turn failed: %s", exc)

    def _remember_note(self, content: str, *, provider: str = "") -> None:
        if not (self._personalization_on() and self._memory):
            return
        try:
            self._memory.add_note(self._active_hero(), content, provider=provider)
        except Exception as exc:
            log.warning("[memory] remember note failed: %s", exc)

    def memory_list(self, limit: int = 50) -> Dict[str, Any]:
        hero = self._active_hero()
        if not (HAS_COACH_MEMORY and self._memory):
            return {"hero": hero, "enabled": False, "entries": [], "count": 0,
                    "error": self._memory_error}
        return {
            "hero": hero,
            "enabled": self._personalization_on(),
            "count": self._memory.count(hero),
            "entries": self._memory.list_entries(hero, limit=limit),
        }

    def memory_clear(self) -> Dict[str, Any]:
        hero = self._active_hero()
        if not (HAS_COACH_MEMORY and self._memory):
            return {"ok": False, "cleared": 0, "error": self._memory_error}
        removed = self._memory.clear(hero)
        return {"ok": True, "hero": hero, "cleared": removed}

    def add_memory_note(self, text: str) -> Dict[str, Any]:
        if not (HAS_COACH_MEMORY and self._memory and self._personalization_on()):
            return {"ok": False, "error": self._memory_error or "Personalization is off"}
        self._remember_note(text, provider="user")
        return {"ok": True, "hero": self._active_hero()}

    def _session_done(self, report: Optional[str]) -> str:
        """Persist a distilled takeaway from a session report into durable memory."""
        text = (report or "").strip()
        if text and not text.startswith(("AI analysis failed", "No AI provider")):
            takeaway = " ".join(text.split())[:280]
            self._remember_note(f"Session review: {takeaway}", provider="session")
        return report

    def _asi1_any_ready(self) -> bool:
        return bool(self._asi1_client or self._asi1_client_fallback)

    def _asi1_split_mode(self) -> bool:
        return bool(self._asi1_client and self._asi1_client_fallback)

    def _asi1_client_for(self, workload: str):
        """Pick ASI:One client by workload — split mode avoids shared rate limits."""
        primary = self._asi1_client
        fallback = self._asi1_client_fallback
        if not primary and not fallback:
            return None
        if not self._asi1_split_mode():
            return primary or fallback
        if workload in ("analysis", "test"):
            return primary or fallback
        return fallback or primary

    def _asi1_retryable(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        exc_name = type(exc).__name__.lower()
        if exc_name in (
            "apiconnectionerror",
            "connecterror",
            "connecttimeout",
            "readtimeout",
            "timeoutexception",
            "connectionerror",
        ):
            return True
        if (
            "429" in msg
            or "rate limit" in msg
            or ("400" in msg and "rate" in msg)
            or "timeout" in msg
            or "timed out" in msg
            or "connection error" in msg
            or "connection reset" in msg
            or "forcibly closed" in msg
            or "failed to connect" in msg
            or "name or service not known" in msg
            or "ssl" in msg
        ):
            return True
        if "500" in msg or "502" in msg or "503" in msg or "504" in msg:
            return True
        # Dual-key mode: fail over when one key is invalid or exhausted.
        if "401" in msg or "unauthorized" in msg or "invalid api key" in msg:
            return True
        status = getattr(exc, "status_code", None)
        if status in (400, 401, 429, 500, 502, 503, 504):
            return True
        return False

    def _asi1_invoke(self, workload: str, call_fn):
        """Run an ASI:One SDK call on the routed client; optional failover to the other key."""
        client = self._asi1_client_for(workload)
        if not client:
            raise RuntimeError("ASI1 client not configured")
        try:
            return call_fn(client)
        except Exception as exc:
            if not self._asi1_split_mode() or not self._asi1_retryable(exc):
                raise
            alt = self._asi1_client_fallback if client is self._asi1_client else self._asi1_client
            if not alt or alt is client:
                raise
            log.warning("[AI] ASI1 %s key failed (%s) — retrying on alternate key", workload, type(exc).__name__)
            return call_fn(alt)

    def _asi1_complete(
        self,
        messages: List[dict],
        *,
        max_tokens: int = 600,
        temperature: float = 0.3,
        web_search: Optional[bool] = None,
        workload: str = "inference",
    ) -> str:
        if not self._asi1_any_ready():
            raise RuntimeError("ASI1 client not configured")
        if web_search is None:
            web_search = False
        # Docs: web_search is a body flag on the selected chat model (not a separate model).
        model = self._asi1_chat_model()
        use_workload = "web" if web_search else workload
        if self._asi1_split_mode() and web_search:
            use_workload = "web"
        resp = self._asi1_invoke(
            use_workload,
            lambda client: client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={"x-session-id": self._asi1_session_id},
                extra_body={"web_search": bool(web_search)},
            ),
        )
        if web_search:
            self._last_web_context_used = True
        return (resp.choices[0].message.content or "").strip()

    # ── Tool / function calling (live DB access) ───────────────────────────
    def _asi1_chat_model(self) -> str:
        """Selected ASI:One chat/tools model (settings override, validated)."""
        pick = _normalize_asi1_model(self._settings.get("asi1_model") or ASI1_MODEL)
        return pick if pick in ASI1_CHAT_MODELS else ASI1_MODEL

    def _coach_tool_specs(self) -> List[dict]:
        """OpenAI-style tool schemas letting the model query the live poker DB."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_player_stats",
                    "description": (
                        "Get the hero's career poker statistics from the local database: "
                        "VPIP, PFR, AF, WTSD, W$SD, C-bet, 3-bet, net results, hand counts, "
                        "positional tendencies and known leak alerts. Call this for any "
                        "question about the player's overall game or stats."
                    ),
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_hands",
                    "description": (
                        "Search the hero's hand history. Filter by position (EP/MP/CO/BTN/SB/BB), "
                        "outcome (won/lost), and limit. Returns matching hands with cards, "
                        "position, net result and hand_id."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "position": {
                                "type": "string",
                                "description": "Table position filter, e.g. BTN, SB, CO",
                            },
                            "outcome": {
                                "type": "string",
                                "enum": ["won", "lost", "any"],
                                "description": "Filter by hero outcome",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max hands to return (default 8, max 25)",
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_hand",
                    "description": (
                        "Fetch one specific hand by its hand_id, including board, hero cards, "
                        "position, pot, net result and the chronological action log."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hand_id": {"type": "string", "description": "The hand_id to fetch"}
                        },
                        "required": ["hand_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate_equity",
                    "description": (
                        "Compute REAL poker equity with this app's Monte Carlo engine. "
                        "Use this for ANY equity/odds claim instead of estimating. Provide "
                        "hero hole cards and either a villain hand, a range string "
                        "(e.g. '22+, A2s+, KQo'), or a villain position+action. NEVER state "
                        "an equity percentage you did not get from this tool."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hero": {"type": "string", "description": "Hero hole cards, e.g. 'Kh2d'"},
                            "villain_hand": {"type": "string", "description": "Specific villain cards, e.g. 'AsKs'"},
                            "villain_range": {"type": "string", "description": "Range notation, e.g. '22+, A2s+, KQo' or 'top 15%'"},
                            "villain_position": {"type": "string", "description": "Villain seat: UTG/MP/CO/BTN/SB/BB"},
                            "action_context": {"type": "string", "description": "open/steal/3bet/defend (default open)"},
                            "board": {"type": "string", "description": "Optional board, e.g. 'As Kd 2c'"},
                        },
                        "required": ["hero"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_cfr_solver",
                    "description": (
                        "Run CFR+ on a toy poker subgame (kuhn, leduc, push_fold, "
                        "tournament_push_fold). For MTT use tournament_push_fold with "
                        "ante_per_player and num_players — antes change push/fold ranges."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "game": {
                                "type": "string",
                                "enum": ["kuhn", "leduc", "push_fold", "tournament_push_fold"],
                            },
                            "iterations": {"type": "integer", "description": "CFR+ iterations (default 8000)"},
                            "ante_per_player": {"type": "number", "description": "MTT ante in chips (default 500)"},
                            "num_players": {"type": "integer", "description": "Table size for dead money (default 9)"},
                        },
                        "required": ["game"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "predict_value",
                    "description": (
                        "Neural value / equity estimate for a poker spot. Include "
                        "ante_per_player and dead_money for MTT spots — pot odds must "
                        "reflect antes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hero": {"type": "string", "description": "Hero cards e.g. AsKh"},
                            "board": {"type": "string", "description": "Board cards (optional)"},
                            "pot_odds": {"type": "number", "description": "Pot odds fraction 0-1"},
                            "ante_per_player": {"type": "number"},
                            "dead_money": {"type": "number"},
                        },
                        "required": ["hero"],
                    },
                },
            },
        ]
        if self._web_search_allowed():
            tools.append({
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "Search the public web for current poker strategy, GTO articles, MTT/ICM "
                        "guidance, or recent theory — only when outside references are needed. "
                        "Do NOT call for career stats or hand history (use database tools)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query, e.g. 'MTT ICM short stack strategy'",
                            },
                        },
                        "required": ["query"],
                    },
                },
            })
        return tools

    def _run_coach_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool call against the live database. Always returns a dict."""
        try:
            if name == "get_player_stats":
                from dataset_context import build_dataset_context

                profile, _ = build_dataset_context(self._db_path, self._settings)
                career = profile.get("career") or {}
                return {
                    "hero": self._active_hero(),
                    "total_hands": profile.get("total_hands", 0),
                    "win_pct": profile.get("win_pct"),
                    "vpip": career.get("vpip"),
                    "pfr": career.get("pfr"),
                    "af": career.get("af"),
                    "wtsd": career.get("wtsd"),
                    "wsd": career.get("wsd"),
                    "cbet": career.get("cbet"),
                    "three_bet": profile.get("three_bet"),
                    "net_cash": career.get("net_cash"),
                    "net_chips": career.get("net_chips"),
                    "by_position": profile.get("by_position"),
                    "leak_alerts": [a.get("message") for a in profile.get("leak_alerts", [])],
                    "recent_100": profile.get("recent_100"),
                }
            if name == "get_hand":
                from models import HandDatabase

                hand_id = str(args.get("hand_id") or "").strip()
                if not hand_id:
                    return {"error": "hand_id is required"}
                hand = HandDatabase(self._db_path).get_hand_by_id(hand_id)
                if not hand:
                    return {"error": f"hand {hand_id} not found"}
                meta = hand_meta_from_hand(hand)
                meta["hand_id"] = hand.hand_id
                meta["action_log"] = _format_action_log(
                    meta.get("streets") or [],
                    hand.hero_name(self._settings) or "",
                    bool(meta.get("is_tournament")),
                )
                meta.pop("streets", None)
                return meta
            if name == "search_hands":
                from models import HandDatabase

                position = str(args.get("position") or "").strip().upper()
                outcome = str(args.get("outcome") or "any").strip().lower()
                try:
                    limit = int(args.get("limit") or 8)
                except (TypeError, ValueError):
                    limit = 8
                limit = max(1, min(limit, 25))
                hands = HandDatabase(self._db_path).get_all_hands()
                out: List[dict] = []
                for h in hands:
                    if position and (h.hero_position or "").upper() != position:
                        continue
                    if outcome == "won" and not (h.hero_won > 0):
                        continue
                    if outcome == "lost" and not (h.hero_won < 0):
                        continue
                    out.append({
                        "hand_id": h.hand_id,
                        "cards": h.hero_cards,
                        "position": h.hero_position,
                        "board": " ".join(h.board_cards),
                        "net": round(float(h.hero_won), 2),
                        "site": h.site,
                    })
                    if len(out) >= limit:
                        break
                return {"count": len(out), "hands": out}
            if name == "calculate_equity":
                hero = str(args.get("hero") or "").strip()
                if not hero:
                    return {"error": "hero cards are required"}
                board = str(args.get("board") or "").strip()
                villain_hand = str(args.get("villain_hand") or "").strip()
                villain_range = str(args.get("villain_range") or "").strip()
                villain_pos = str(args.get("villain_position") or "").strip()
                action = str(args.get("action_context") or "open").strip() or "open"
                if villain_hand:
                    res = equity_engine.equity_hand_vs_hand(hero, villain_hand, board=board or None, iters=15000)
                    return {
                        "hero": hero, "villain": villain_hand, "board": board,
                        "hero_equity_pct": res["hero_equity"], "win_pct": res["hero_win"],
                        "tie_pct": res["hero_tie"], "iterations": res["iterations"],
                    }
                if villain_range:
                    res = equity_engine.equity_hand_vs_range(hero, villain_range, board=board or None, iters=12000)
                    return {
                        "hero": hero, "villain_range": villain_range,
                        "villain_range_pct": equity_engine.range_frequency(villain_range),
                        "board": board, "hero_equity_pct": res["hero_equity"],
                        "iterations": res["iterations"],
                    }
                if villain_pos:
                    res = equity_engine.equity_vs_position_range(hero, villain_pos, action, board=board or None, iters=12000)
                    return {
                        "hero": hero, "villain_position": res["villain_position"],
                        "action_context": action, "villain_range": res["villain_range"],
                        "villain_range_pct": res["villain_range_pct"], "board": board,
                        "hero_equity_pct": res["hero_equity"], "iterations": res["iterations"],
                    }
                # No villain specified: equity vs the standard reference ranges.
                grounding = equity_engine.preflop_equity_grounding(hero, iters=4000)
                if not grounding:
                    return {"error": "could not parse hero cards"}
                return {"hero": grounding["hero_cards"], "equity_vs_reference_ranges": grounding["rows"]}
            if name == "run_cfr_solver":
                from theory.cfr_solver import run_cfr_for_game

                game = str(args.get("game") or "kuhn").strip()
                iterations = int(args.get("iterations") or 8000)
                result = run_cfr_for_game(
                    game,
                    iterations=iterations,
                    ante_per_player=float(args.get("ante_per_player") or 500),
                    num_players=int(args.get("num_players") or 9),
                )
                return {
                    "game": result.get("game_id"),
                    "exploitability": result.get("exploitability"),
                    "ev": result.get("ev"),
                    "config": result.get("config"),
                    "strategy": result.get("strategy"),
                    "note": result.get("note"),
                }
            if name == "predict_value":
                from theory.value_net import predict_value

                hero = str(args.get("hero") or "").strip()
                if not hero:
                    return {"error": "hero cards are required"}
                return predict_value(
                    hero,
                    str(args.get("board") or ""),
                    pot_odds=float(args.get("pot_odds") or 0.33),
                    ante_per_player=float(args.get("ante_per_player") or 0),
                    dead_money=float(args.get("dead_money") or 0),
                )
            if name == "web_search":
                if not self._web_search_allowed():
                    return {"error": "Live web search is disabled in Settings."}
                query = str(args.get("query") or "").strip() or "poker GTO strategy"
                try:
                    from web_context import fetch_web_snippets

                    payload = fetch_web_snippets(query, max_results=5)
                    self._last_web_context_used = True
                    return {
                        "ok": payload.get("ok", False),
                        "query": query,
                        "results": (payload.get("results") or [])[:5],
                    }
                except Exception as exc:
                    return {"error": f"web search failed: {exc}"}
            return {"error": f"unknown tool: {name}"}
        except Exception as exc:
            log.warning("[AI] tool %s failed: %s", name, exc)
            return {"error": f"tool execution failed: {exc}"}

    def _asi1_chat_with_tools(
        self,
        messages: List[dict],
        *,
        max_tokens: int = 700,
        temperature: float = 0.4,
        max_rounds: int = 4,
        workload: str = "inference",
    ) -> str:
        """Run an ASI:One chat completion with live DB tools (function-calling loop)."""
        if not self._asi1_any_ready():
            raise RuntimeError("ASI1 client not configured")
        model = self._asi1_chat_model()
        tools = self._coach_tool_specs()
        convo = list(messages)
        for _ in range(max_rounds):
            resp = self._asi1_invoke(
                workload,
                lambda client: client.chat.completions.create(
                    model=model,
                    messages=convo,
                    tools=tools,
                    tool_choice="auto",
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_headers={"x-session-id": self._asi1_session_id},
                ),
            )
            choice = resp.choices[0]
            msg = choice.message
            content = msg.content or ""
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                # Structured tool_calls — echo the assistant message, then tool results.
                convo.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    result = self._run_coach_tool(tc.function.name, args)
                    convo.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result)[:6000],
                    })
                continue
            # Safety net: the model emitted tool calls as text instead of using the
            # structured field. Parse, execute, and feed results back so the raw
            # <tool_call> tags never reach the user.
            text_calls = _parse_text_tool_calls(content)
            if text_calls:
                convo.append({"role": "assistant", "content": content})
                for call in text_calls:
                    result = self._run_coach_tool(call["name"], call.get("arguments") or {})
                    convo.append({
                        "role": "user",
                        "content": (
                            f"Result of tool {call['name']}: "
                            f"{json.dumps(result)[:6000]}\n\n"
                            "Use this to answer directly. Do NOT output any <tool_call> tags."
                        ),
                    })
                continue
            return _strip_tool_call_tags(content)
        # Ran out of rounds — ask for a final answer without tools.
        final = self._asi1_invoke(
            workload,
            lambda client: client.chat.completions.create(
                model=model,
                messages=convo,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={"x-session-id": self._asi1_session_id},
            ),
        )
        return _strip_tool_call_tags((final.choices[0].message.content or "").strip())

    def _asi1_research_chat(
        self,
        messages: List[dict],
        *,
        max_tokens: int = 900,
        temperature: float = 0.4,
    ) -> str:
        """Web-grounded chat for research / online / plan requests (on-demand only)."""
        if not self._web_search_allowed():
            raise RuntimeError(
                "Live web search is disabled. Enable On-demand or Always in Settings → Web search."
            )
        convo = list(messages)
        user_q = next(
            (m.get("content", "") for m in reversed(convo) if m.get("role") == "user"),
            "",
        )

        def _merge_system(extra: str) -> None:
            if not extra:
                return
            if convo and convo[0].get("role") == "system":
                convo[0] = {**convo[0], "content": (convo[0].get("content") or "") + extra}
            else:
                convo.insert(0, {"role": "system", "content": extra.strip()})

        # 1. Ground in the hero's live DB stats.
        try:
            stats = self._run_coach_tool("get_player_stats", {})
            if isinstance(stats, dict) and not stats.get("error"):
                _merge_system(
                    "\n\nLive player stats from the local database (ground all advice "
                    "in these real numbers):\n" + json.dumps(stats)[:4000]
                )
        except Exception as exc:
            log.warning("[AI] research stats prefetch failed: %s", exc)

        # 2. Retrieve real online sources (DuckDuckGo) and answer with base asi1.
        web_text = ""
        try:
            from web_context import fetch_web_snippets, format_web_context_block

            payload = fetch_web_snippets(self._topic_web_query(user_q), max_results=5)
            web_text = format_web_context_block(payload)
        except Exception as exc:
            log.warning("[AI] research web fetch failed: %s", exc)
        if web_text:
            _merge_system(f"\n\n{WEB_ACCESS_NOTE}\n\n{web_text}")
            self._last_web_context_used = True
            return self._asi1_complete(
                convo, max_tokens=max_tokens, temperature=temperature, web_search=False
            )

        # 3. Fallback: no local web results — use ASI:One's native web model.
        return self._asi1_complete(
            convo, max_tokens=max_tokens, temperature=temperature, web_search=True
        )

    def asi1_image_available(self) -> bool:
        """True when an ASI:One key is present (image gen uses raw HTTP, no SDK needed)."""
        return bool(_asi1_api_key(self._settings) or _asi1_api_key_fallback(self._settings))

    def generate_image(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        size: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Generate an image via the ASI:One image API.
        Returns {ok, url, images:[{url}], model, message, error}.
        The API responds with hosted URLs; base64 payloads are passed through as data URIs.
        """
        self._last_error = None
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "Empty prompt", "images": [], "url": None}
        key = _asi1_api_key_fallback(self._settings) or _asi1_api_key(self._settings)
        if _asi1_api_key(self._settings) and _asi1_api_key_fallback(self._settings):
            key = _asi1_api_key_fallback(self._settings)
        if not key:
            return {
                "ok": False,
                "error": (
                    "ASI_ONE_API_KEY not set — add it to .env to enable image generation"
                ),
                "images": [],
                "url": None,
            }
        use_model = (model or ASI1_IMAGE_MODEL).strip() or ASI1_IMAGE_MODEL
        # ASI:One requires a non-empty size from a fixed allow-list; default to square.
        use_size = (size or "").strip() or ASI1_IMAGE_SIZE
        payload: Dict[str, Any] = {"model": use_model, "prompt": prompt, "size": use_size}
        url = f"{ASI1_BASE_URL.rstrip('/')}/image/generate"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode(errors="replace")
            except Exception:
                raw = ""
            detail = raw
            try:
                parsed = json.loads(raw)
                detail = parsed.get("message") or parsed.get("error") or raw
            except Exception:
                pass
            msg = f"ASI:One image API HTTP {exc.code}: {str(detail)[:300]}"
            self._last_error = msg
            log.warning("[AI] image generation failed: HTTP %s", exc.code)
            return {"ok": False, "error": msg, "images": [], "url": None, "model": use_model}
        except Exception as exc:
            msg = f"ASI:One image request failed: {exc}"
            self._last_error = msg
            log.warning("[AI] image generation error: %s", exc)
            return {"ok": False, "error": msg, "images": [], "url": None, "model": use_model}

        # Real API returns OpenAI-style `data: [{b64_json, url, ...}]`; the published
        # docs describe `images: [{url}]`. Support both so we survive either shape.
        items = body.get("data") or body.get("images") or []
        images: List[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            src = item.get("url") or ""
            b64 = item.get("b64_json") or item.get("b64") or ""
            if (not src or not src.startswith(("http", "data:"))) and b64:
                src = f"data:{_b64_image_mime(b64, body.get('output_format'))};base64,{b64}"
            if src:
                images.append({"url": src})
        if not images:
            msg = body.get("message") or "ASI:One returned no image for this prompt"
            self._last_error = msg
            return {"ok": False, "error": msg, "images": [], "url": None, "model": use_model}
        return {
            "ok": True,
            "url": images[0]["url"],
            "images": images,
            "model": use_model,
            "message": body.get("message") or "",
            "error": None,
        }

    def _deepseek_complete(
        self,
        messages: List[dict],
        *,
        model: str = DEEPSEEK_CHAT_MODEL,
        max_tokens: int = 600,
        temperature: float = 0.3,
    ) -> str:
        if not self._deepseek_client:
            raise RuntimeError("DeepSeek client not configured")
        resp = self._deepseek_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def _key_fingerprint(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:12] if key else ""

    def _init_asi1_clients(self) -> None:
        """Initialize primary and fallback ASI:One OpenAI clients."""
        asi1_key = _asi1_api_key(self._settings)
        asi1_fp = self._key_fingerprint(asi1_key)
        fallback_key = _asi1_api_key_fallback(self._settings)
        fallback_fp = self._key_fingerprint(fallback_key)

        if asi1_key and HAS_OPENAI:
            if not self._asi1_client or self._asi1_key_fp != asi1_fp:
                try:
                    self._asi1_client = _asi1_openai_client(asi1_key)
                    self._asi1_key_fp = asi1_fp
                    self._provider_init_errors.pop("asi1", None)
                except Exception as e:
                    self._asi1_client = None
                    self._asi1_key_fp = ""
                    self._provider_init_errors["asi1"] = str(e)
        else:
            self._asi1_client = None
            self._asi1_key_fp = ""

        if fallback_key and HAS_OPENAI:
            if not self._asi1_client_fallback or self._asi1_fallback_key_fp != fallback_fp:
                try:
                    self._asi1_client_fallback = _asi1_openai_client(fallback_key)
                    self._asi1_fallback_key_fp = fallback_fp
                    self._provider_init_errors.pop("asi1_fallback", None)
                except Exception as e:
                    self._asi1_client_fallback = None
                    self._asi1_fallback_key_fp = ""
                    self._provider_init_errors["asi1_fallback"] = str(e)
        else:
            self._asi1_client_fallback = None
            self._asi1_fallback_key_fp = ""

        if self._asi1_split_mode():
            log.info("[AI] ASI:One dual-key split — analysis on primary, coach on fallback")
        elif asi1_key and HAS_OPENAI and self._asi1_client:
            log.info("[AI] ASI:One ready — model: %s @ %s", ASI1_MODEL, ASI1_BASE_URL)
        elif asi1_key and not HAS_OPENAI:
            self._provider_init_errors.setdefault(
                "asi1",
                "openai package not installed — run: pip install -r sidecar\\requirements.txt",
            )

    def _refresh_cloud_clients(self) -> None:
        """Re-init cloud clients when keys appear or change after startup (e.g. .env edited)."""
        had_primary = bool(self._asi1_client)
        had_fallback = bool(self._asi1_client_fallback)
        self._init_asi1_clients()
        if self._asi1_client and not had_primary:
            log.info("[AI] ASI:One primary connected (refreshed)")
        if self._asi1_client_fallback and not had_fallback:
            log.info("[AI] ASI:One fallback connected (refreshed)")

    def _provider_chain(self, explicit: Optional[str] = None) -> List[str]:
        """Ordered provider names for inference."""
        self._refresh_cloud_clients()
        pref = (explicit or self._settings.get("ai_provider") or "asi1").lower()
        has_asi1 = self._asi1_any_ready()
        has_openai = bool(self._openai_client)
        has_deepseek = bool(self._deepseek_client)
        has_gemini = self._gemini_ready
        has_ollama = _ollama_available() and bool(_ollama_installed_models())
        has_claude = bool(self._anthropic_client)

        availability = {
            "asi1": has_asi1,
            "openai": has_openai,
            "deepseek": has_deepseek,
            "gemini": has_gemini,
            "ollama": has_ollama,
            "anthropic": has_claude,
        }

        if pref == "asi1":
            chain = ["asi1", "openai", "deepseek", "gemini", "anthropic", "ollama"]
        elif pref == "openai":
            chain = ["openai", "deepseek", "asi1", "gemini", "anthropic", "ollama"]
        elif pref == "deepseek":
            chain = ["deepseek", "openai", "asi1", "gemini", "anthropic", "ollama"]
        elif pref == "gemini":
            chain = ["gemini", "deepseek", "openai", "asi1", "anthropic", "ollama"]
        elif pref == "ollama":
            chain = ["ollama"]
        elif pref == "anthropic":
            chain = ["anthropic", "deepseek", "openai", "asi1", "gemini", "ollama"]
        elif pref == "auto":
            chain = _cloud_first_chain(availability)
        else:
            chain = _cloud_first_chain(availability)

        return [p for p in chain if availability.get(p, False)]

    def _init(self):
        self._init_asi1_clients()

        try:
            from config import get_api_key

            openai_key = get_api_key("openai")
        except ImportError:
            openai_key = None
        api_key = _sanitize_api_key(
            self._settings.get("openai_api_key")
            or openai_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if api_key and HAS_OPENAI:
            try:
                self._openai_client = _OpenAI(api_key=api_key)
                log.info("[AI] OpenAI ready")
            except Exception as e:
                self._provider_init_errors["openai"] = str(e)
                log.warning(f"[AI] OpenAI init failed: {e}")

        deepseek_key = _deepseek_api_key(self._settings)
        if deepseek_key and HAS_OPENAI:
            try:
                self._deepseek_client = _OpenAI(
                    api_key=deepseek_key,
                    base_url=DEEPSEEK_BASE_URL,
                )
                log.info(
                    "[AI] DeepSeek ready — model: %s @ %s",
                    DEEPSEEK_CHAT_MODEL,
                    DEEPSEEK_BASE_URL,
                )
            except Exception as e:
                self._provider_init_errors["deepseek"] = str(e)
                log.warning("[AI] DeepSeek init failed: %s", e)
        elif not deepseek_key:
            self._provider_init_errors.setdefault(
                "deepseek", "DEEPSEEK_API_KEY not set in .env"
            )

        gemini_key = _gemini_api_key(self._settings)
        if gemini_key:
            self._gemini_ready = True
            log.info("[AI] Gemini ready — models: %s", ", ".join(GEMINI_MODELS))
        else:
            self._provider_init_errors.setdefault(
                "gemini", "GEMINI_API_KEY or GOOGLE_API_KEY not set in .env"
            )

        try:
            from config import get_api_key

            ant_key = get_api_key("anthropic")
        except ImportError:
            ant_key = None
        ant_key = _sanitize_api_key(
            self._settings.get("anthropic_api_key")
            or ant_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if ant_key and HAS_ANTHROPIC:
            try:
                self._anthropic_client = _anthropic.Anthropic(api_key=ant_key)
                log.info(f"[AI] Claude ready — model: {CLAUDE_MODEL}")
            except Exception as e:
                self._provider_init_errors["anthropic"] = str(e)
                log.warning(f"[AI] Claude init failed: {e}")
        elif not ant_key:
            self._provider_init_errors.setdefault(
                "anthropic", "ANTHROPIC_API_KEY not set in .env"
            )

        if not _ollama_available():
            self._provider_init_errors.setdefault(
                "ollama",
                f"Ollama not reachable at {_ollama_base()} — start Ollama or set OLLAMA_BASE_URL",
            )
        elif not _ollama_installed_models():
            self._provider_init_errors.setdefault(
                "ollama",
                f"Ollama running but no models installed — ollama pull {OLLAMA_RECOMMENDED_PULL}",
            )

        if not self._asi1_any_ready():
            self._provider_init_errors.setdefault(
                "asi1",
                "ASI_ONE_API_KEY not set in .env - get a key at https://asi1.ai and add "
                "ASI_ONE_API_KEY=your-key (no uAgents install required)",
            )
        if not api_key:
            self._provider_init_errors.setdefault(
                "openai", "OPENAI_API_KEY not set in .env"
            )

        chain = self._provider_chain()
        if chain:
            self._active_provider = chain[0]
            if chain[0] == "ollama":
                selected = _best_analysis_model(self._settings)
                self._active_provider = f"ollama:{selected}"
                pref = (self._settings.get("ollama_model") or "").strip()
                log.info(
                    "[AI] Ollama selected model: %s%s",
                    selected or "<none>",
                    f" (settings preference: {pref})" if pref else "",
                )
            elif chain[0] == "asi1":
                self._active_provider = f"asi1:{ASI1_MODEL}"
            elif chain[0] == "deepseek":
                self._active_provider = f"deepseek:{DEEPSEEK_CHAT_MODEL}"
        elif (
            _ollama_available()
            and bool(_ollama_installed_models())
            and (self._settings.get("ai_provider") or "asi1").lower() == "ollama"
        ):
            selected = _best_analysis_model(self._settings)
            self._active_provider = f"ollama:{selected}"
            log.info("[AI] Ollama only — model: %s", selected)
        else:
            log.warning(
                "[AI] No provider configured — start Ollama or set cloud API keys in .env"
            )

    def is_available(self) -> bool:
        if _ollama_available() and _ollama_installed_models():
            return True
        return bool(
            self._asi1_any_ready()
            or self._openai_client
            or self._deepseek_client
            or self._gemini_ready
            or self._anthropic_client
        )

    def _provider_key_configured(self, name: str) -> Optional[bool]:
        """True/false if key-based; None for Ollama (no key)."""
        if name == "ollama":
            return None
        try:
            from config import env_keys_detected

            keys = env_keys_detected()
            return bool(keys.get(name))
        except ImportError:
            env_map = {
                "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "asi1": "ASI_ONE_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
            }
            var = env_map.get(name)
            if not var:
                return False
            return bool(os.environ.get(var, "").strip())

    def _provider_ready(self, name: str) -> bool:
        if name == "ollama":
            return _ollama_available() and bool(_ollama_installed_models())
        if name == "openai":
            return bool(self._openai_client)
        if name == "deepseek":
            return bool(self._deepseek_client)
        if name == "gemini":
            return self._gemini_ready
        if name == "anthropic":
            return bool(self._anthropic_client)
        if name == "asi1":
            return self._asi1_any_ready()
        return False

    def _provider_model(self, name: str) -> str:
        if name == "ollama":
            return _best_analysis_model(self._settings) if _ollama_available() else ""
        if name == "openai":
            return OPENAI_CHAT_MODEL
        if name == "deepseek":
            return DEEPSEEK_CHAT_MODEL
        if name == "gemini":
            return GEMINI_MODELS[0] if GEMINI_MODELS else ""
        if name == "anthropic":
            return CLAUDE_MODEL
        if name == "asi1":
            return self._asi1_chat_model()
        return ""

    def _provider_env_var(self, name: str) -> Optional[str]:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY (or GOOGLE_API_KEY)",
            "anthropic": "ANTHROPIC_API_KEY",
            "asi1": "ASI_ONE_API_KEY (or ASI1_API_KEY)",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        return env_map.get(name)

    def get_provider_status(self) -> Dict[str, dict]:
        """Per-provider readiness without live API calls."""
        self._refresh_cloud_clients()
        providers = {}
        for name in ("ollama", "openai", "deepseek", "gemini", "anthropic", "asi1"):
            ready = self._provider_ready(name)
            err = None if ready else self._provider_init_errors.get(name)
            if not ready and not err:
                env_var = self._provider_env_var(name)
                if env_var and self._provider_key_configured(name) is False:
                    err = f"{env_var} not set in .env"
                elif name == "ollama":
                    err = self._provider_init_errors.get("ollama", "Ollama unavailable")
                elif name == "asi1" and self._provider_key_configured(name):
                    err = (
                        "ASI_ONE_API_KEY detected — click Refresh in Settings "
                        "or restart LeakSnipe"
                    )
                else:
                    err = "Not configured"
            providers[name] = {
                "ready": ready,
                "model": self._provider_model(name) or None,
                "error": err,
                "key_configured": self._provider_key_configured(name),
                "env_var": self._provider_env_var(name),
            }
        return providers

    def test_provider(self, name: str) -> dict:
        """Run a minimal completion ping for one provider."""
        name = (name or "").lower().strip()
        if name not in {"ollama", "openai", "deepseek", "gemini", "anthropic", "asi1"}:
            return {"ok": False, "provider": name, "error": f"Unknown provider: {name}"}

        ping_messages = [
            {"role": "system", "content": "Reply with exactly one word: OK"},
            {"role": "user", "content": "Ping"},
        ]

        if not self._provider_ready(name):
            err = self._provider_init_errors.get(name)
            if not err:
                env_var = self._provider_env_var(name)
                if env_var and self._provider_key_configured(name) is False:
                    err = f"{env_var} not set — add to .env at repo root"
                else:
                    err = "Provider not ready"
            return {
                "ok": False,
                "provider": name,
                "model": self._provider_model(name) or None,
                "error": err,
                "skipped": self._provider_key_configured(name) is False,
            }

        try:
            sample = ""
            model = self._provider_model(name)
            if name == "ollama":
                sample = _ollama_complete(
                    "Reply with exactly: OK",
                    model,
                    max_tokens=16,
                    temperature=0,
                    timeout=120,
                )
            elif name == "openai":
                resp = self._openai_client.chat.completions.create(
                    model=OPENAI_CHAT_MODEL,
                    messages=ping_messages,
                    max_tokens=16,
                    temperature=0,
                )
                sample = (resp.choices[0].message.content or "").strip()
            elif name == "deepseek":
                sample = self._deepseek_complete(
                    ping_messages, max_tokens=16, temperature=0
                )
            elif name == "asi1":
                sample = self._asi1_complete(
                    ping_messages, max_tokens=16, temperature=0, web_search=False, workload="test"
                )
            elif name == "gemini":
                sample, model = _gemini_generate(
                    "Ping — reply with exactly: OK",
                    system="Reply with exactly one word: OK",
                    max_tokens=16,
                    temperature=0,
                )
            elif name == "anthropic":
                resp = self._anthropic_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=16,
                    system="Reply with exactly one word: OK",
                    messages=[{"role": "user", "content": "Ping"}],
                )
                sample = resp.content[0].text.strip()
            return {
                "ok": bool(sample),
                "provider": name,
                "model": model,
                "sample": sample[:80] if sample else None,
                "error": None if sample else "Empty response from provider",
            }
        except Exception as e:
            msg = _friendly_api_error(e, name)
            return {
                "ok": False,
                "provider": name,
                "model": self._provider_model(name) or None,
                "error": msg,
            }

    def test_all_providers(self) -> dict:
        results = {}
        for name in ("ollama", "openai", "deepseek", "gemini", "anthropic", "asi1"):
            results[name] = self.test_provider(name)
        return {"results": results}

    def get_last_error(self) -> Optional[str]:
        return self._last_error

    def get_status(self) -> dict:
        chain = self._provider_chain()
        ollama_running = _ollama_available()
        installed = sorted(_ollama_installed_models()) if ollama_running else []
        selected_pref = (self._settings.get("ollama_model") or "").strip()
        active_model = _best_analysis_model(self._settings) if ollama_running else ""
        llm_available = self.is_available()
        llm_provider = self._active_provider or "none"
        if chain:
            head = chain[0]
            if head == "ollama" and ollama_running and active_model:
                llm_provider = f"ollama:{active_model}"
            elif head == "asi1" and self._asi1_any_ready():
                llm_provider = f"asi1:{self._asi1_chat_model()}"
            elif head == "openai" and self._openai_client:
                llm_provider = OPENAI_CHAT_MODEL
            elif head == "deepseek" and self._deepseek_client:
                llm_provider = f"deepseek:{DEEPSEEK_CHAT_MODEL}"
            elif head == "gemini" and self._gemini_ready:
                llm_provider = f"gemini:{GEMINI_MODELS[0]}"
            elif head == "anthropic" and self._anthropic_client:
                llm_provider = CLAUDE_MODEL
            self._active_provider = llm_provider
        pref_installed = bool(
            selected_pref
            and (
                selected_pref in installed
                or _match_installed_model(set(installed), selected_pref)
            )
        )
        dataset_meta: Dict[str, Any] = {
            "ai_include_dataset_context": self._include_dataset_context(),
            "ai_web_search_mode": self._web_search_mode(),
            "ai_include_web_context": self._web_search_allowed(),
            "dataset_context_ready": False,
            "dataset_context_hands": 0,
            "web_context_enabled": self._web_search_allowed(),
        }
        if self._include_dataset_context():
            try:
                ctx = self.get_dataset_context()
                dataset_meta["dataset_context_ready"] = bool(ctx.get("hand_count"))
                dataset_meta["dataset_context_hands"] = ctx.get("hand_count", 0)
            except Exception:
                pass
        return {
            "llm_available": llm_available,
            "llm_provider": llm_provider,
            "ollama_model_selected": selected_pref or None,
            "ollama_model_pref_installed": pref_installed,
            "provider_chain": chain,
            "ai_provider_pref": self._settings.get("ai_provider", "asi1"),
            "asi1_ready": self._asi1_any_ready(),
            "asi1_primary_ready": bool(self._asi1_client),
            "asi1_fallback_ready": bool(self._asi1_client_fallback),
            "asi1_routing_mode": (
                "split" if self._asi1_split_mode() else "single"
            ),
            "asi1_dual_note": (
                "Dual ASI1 keys: analysis + coach run in parallel"
                if self._asi1_split_mode()
                else None
            ),
            "asi1_model": self._asi1_chat_model(),
            "asi1_base_url": ASI1_BASE_URL,
            "asi1_request_timeout_s": ASI1_REQUEST_TIMEOUT,
            "asi1_image_ready": self.asi1_image_available(),
            "asi1_image_model": ASI1_IMAGE_MODEL,
            "asi1_chat_model": self._asi1_chat_model(),
            "asi1_chat_models": ASI1_CHAT_MODELS,
            "ai_personalization": self._personalization_on(),
            "ai_agentic_tools": self._agentic_tools_on(),
            "coach_memory_available": bool(HAS_COACH_MEMORY and self._memory),
            "coach_memory_hero": self._active_hero(),
            "coach_memory_count": (
                self._memory.count(self._active_hero())
                if (HAS_COACH_MEMORY and self._memory)
                else 0
            ),
            "asi1_session_persisted": self._personalization_on() and HAS_COACH_MEMORY,
            "asi1_rate_note": (
                "OpenAI-compatible API at api.asi1.ai/v1 — rate limits per your plan "
                "(https://asi1.ai dashboard). Docs: https://docs.asi1.ai"
            ),
            "asi1_setup_note": (
                "Add ASI_ONE_API_KEY to .env (from https://asi1.ai). "
                "Sidecar deps: pip install -r sidecar\\requirements.txt — uses openai SDK only."
            ),
            "cloud_recommended": bool(
                self._asi1_any_ready()
                or self._openai_client
                or self._deepseek_client
                or self._gemini_ready
                or self._anthropic_client
            ),
            "recommended_provider": chain[0] if chain else None,
            "openai_ready": bool(self._openai_client),
            "openai_model": OPENAI_CHAT_MODEL,
            "deepseek_ready": bool(self._deepseek_client),
            "deepseek_model": DEEPSEEK_CHAT_MODEL,
            "deepseek_models": [DEEPSEEK_CHAT_MODEL, DEEPSEEK_REASONER_MODEL],
            "deepseek_base_url": DEEPSEEK_BASE_URL,
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
            "providers": self.get_provider_status(),
            **dataset_meta,
        }

    # ── Context / chat ────────────────────────────────────────────────────────
    def _include_dataset_context(self) -> bool:
        return bool(self._settings.get("ai_include_dataset_context", True))

    def _web_search_mode(self) -> str:
        """off | on_demand (default) | always."""
        mode = (self._settings.get("ai_web_search_mode") or "").strip().lower()
        if mode in WEB_SEARCH_MODES:
            return mode
        if self._settings.get("ai_include_web_context") is False:
            return "off"
        return "on_demand"

    def _web_search_allowed(self) -> bool:
        return self._web_search_mode() != "off"

    def _web_search_always(self) -> bool:
        return self._web_search_mode() == "always"

    def _needs_web_for_text(self, text: str) -> bool:
        if not self._web_search_allowed():
            return False
        if self._web_search_always():
            return True
        return _is_research_intent(text)

    def _include_web_context(self, provider: Optional[str] = None) -> bool:
        """Whether live web search is allowed at all (legacy bool compat)."""
        return self._web_search_allowed()

    def _uses_asi1_native_web(self, provider: Optional[str] = None) -> bool:
        """ASI1 native web_search flag — only in 'always' mode (on_demand uses DDG/tools)."""
        if not self._web_search_always():
            return False
        pref = (provider or self._settings.get("ai_provider") or "auto").lower()
        chain = self._provider_chain(provider)
        head = chain[0] if chain else ""
        if pref == "asi1" or (pref == "auto" and head == "asi1"):
            return True
        return False

    def _web_context_block(
        self,
        query: str,
        *,
        provider: Optional[str] = None,
        force: bool = False,
    ) -> str:
        """DuckDuckGo snippets — only when always-on or explicitly requested (research)."""
        if not self._web_search_allowed():
            return ""
        if not force and not self._web_search_always():
            return ""
        if self._uses_asi1_native_web(provider):
            return ""
        try:
            from web_context import fetch_web_snippets, format_web_context_block

            payload = fetch_web_snippets(query)
            block = format_web_context_block(payload)
            if block:
                self._last_web_context_used = True
            return block
        except Exception as e:
            log.warning("[AI] web context build failed: %s", e)
            return ""

    def _web_access_note(self, provider: Optional[str], *, web_active: bool = False) -> str:
        """System note — active web, on-request availability, or nothing."""
        if web_active:
            return WEB_ACCESS_NOTE
        if self._web_search_allowed() and not self._web_search_always():
            return WEB_AVAILABLE_NOTE
        return ""

    def _hand_web_query(self, hand_meta: Optional[dict], hero_name: str) -> str:
        try:
            from web_context import build_hand_web_query

            return build_hand_web_query(hand_meta, hero_name)
        except ImportError:
            return "poker GTO strategy"

    def _topic_web_query(self, text: str) -> str:
        try:
            from web_context import build_topic_web_query

            return build_topic_web_query(text)
        except ImportError:
            return "poker GTO strategy"

    def _dataset_max_chars(self, provider: Optional[str] = None) -> int:
        prov = (provider or self._settings.get("ai_provider") or "").lower()
        if prov == "asi1":
            return 8000
        return 4500

    def get_dataset_context(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        from dataset_context import build_dataset_context

        profile, text = build_dataset_context(
            self._db_path, self._settings, force_refresh=force_refresh
        )
        return {
            "profile": profile,
            "text": text,
            "hand_count": profile.get("total_hands", 0),
            "include_enabled": self._include_dataset_context(),
        }

    def _dataset_context_block(self, provider: Optional[str] = None) -> str:
        if not self._include_dataset_context():
            return ""
        try:
            from dataset_context import build_dataset_context

            _, text = build_dataset_context(self._db_path, self._settings)
            if not text:
                return ""
            cap = self._dataset_max_chars(provider)
            if len(text) > cap:
                text = text[: cap - 20] + "\n… [truncated]"
            return f"{DATASET_CONTEXT_HEADER}\n\n{text}"
        except Exception as e:
            log.warning("[AI] dataset context build failed: %s", e)
            return ""

    def set_context(self, context: str):
        self._context     = context
        self._chat_history = []

    def clear_chat(self):
        self._chat_history = []

    def chat(self, user_message: str, provider: Optional[str] = None) -> str:
        self._last_web_context_used = False
        self._chat_history.append({"role": "user", "content": user_message})
        dataset_block = self._dataset_context_block(provider)
        ui_context = (self._context or "").strip()
        if dataset_block:
            if ui_context and ui_context.lower() != "no stats loaded.":
                context = f"{dataset_block}\n\nFiltered view context:\n{ui_context[:1500]}"
            else:
                context = dataset_block
        else:
            context = ui_context[:2000] or "No stats loaded."
        system = CHAT_SYSTEM.format(context=context)
        memory_block = self._memory_block()
        if memory_block:
            system = f"{system}\n\n{memory_block}"
        web_block = ""
        wants_research = self._needs_web_for_text(user_message)
        if wants_research:
            web_block = self._web_context_block(
                self._topic_web_query(user_message), provider=provider, force=True
            )
        elif self._web_search_always():
            web_block = self._web_context_block(
                self._topic_web_query(user_message), provider=provider
            )
        note = self._web_access_note(
            provider, web_active=bool(web_block) or wants_research
        )
        if note:
            system = f"{system}\n\n{note}"
        if web_block:
            system = f"{system}\n\n{web_block}"
        messages = [{"role": "system", "content": system}] + self._chat_history[-12:]

        reply = ""
        used = None
        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_any_ready():
                    if wants_research and self._web_search_allowed():
                        reply = self._asi1_research_chat(
                            messages, max_tokens=900, temperature=0.4
                        )
                        used = f"asi1:{self._asi1_chat_model()}+research"
                    elif self._agentic_tools_on():
                        reply = self._asi1_chat_with_tools(
                            messages, max_tokens=700, temperature=0.4
                        )
                        used = f"asi1:{self._asi1_chat_model()}+tools"
                    else:
                        use_native_web = self._web_search_always()
                        reply = self._asi1_complete(
                            messages,
                            max_tokens=700,
                            temperature=0.4,
                            web_search=use_native_web,
                        )
                        used = f"asi1:{self._asi1_chat_model()}"
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
                if prov == "deepseek" and self._deepseek_client:
                    reply = self._deepseek_complete(
                        messages, max_tokens=600, temperature=0.4
                    )
                    used = f"deepseek:{DEEPSEEK_CHAT_MODEL}"
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
                    model = _best_analysis_model(self._settings)
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
                self._last_error = _friendly_api_error(e, prov)
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
                    "• Add ASI_ONE_API_KEY to .env for ASI:One (recommended)\n"
                    "• Or set OPENAI_API_KEY / GEMINI_API_KEY in .env at repo root\n"
                    "• Or start Ollama as a local fallback"
                )
            else:
                reply = self._last_error or "AI request failed — check sidecar logs and retry."

        self._chat_history.append({"role": "assistant", "content": reply})
        if reply and not reply.startswith(("No AI provider", "Ollama is running")):
            self._remember_turn(user_message, reply, used or (provider or "auto"))
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
                     hand_meta: Optional[dict] = None,
                     temperature: float = 0.2, max_tokens: int = 1000) -> Optional[dict]:
        self._last_error = None
        self._last_web_context_used = False
        prompt, system_prompt, hero_won, spot_flags, allowed_equities, spots = build_hand_analysis_prompt(
            hero_name, hand_meta, str(raw_text)
        )
        dataset_block = self._dataset_context_block(provider)
        if dataset_block:
            system_prompt = f"{system_prompt}\n\n{dataset_block}"
        # Per-hand grading uses the local DB + hand text only — never live web.
        web_block = ""
        if self._web_search_always():
            web_block = self._web_context_block(
                self._hand_web_query(hand_meta, hero_name), provider=provider
            )
        if web_block:
            system_prompt = f"{system_prompt}\n\n{WEB_ACCESS_NOTE}\n\n{web_block}"
        result = None
        used_prov = "none"

        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_any_ready():
                    asi1_tokens = 1400 if dataset_block else max_tokens
                    # Per-hand grading uses base asi1 without web_search (local DB + hand text).
                    text = self._asi1_complete(
                        [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=asi1_tokens,
                        temperature=temperature,
                        web_search=False,
                        workload="analysis",
                    )
                    result = _parse_json_response(text)
                    used_prov = f"asi1:{self._asi1_chat_model()}"
                    break
                if prov == "openai" and self._openai_client:
                    try:
                        text = self._responses_call(prompt[:3000])
                        result = _parse_json_response(text)
                        used_prov = "responses-api"
                    except Exception as e:
                        log.error(f"[AI] Responses API hand error: {e}")
                        r = self._openai_client.chat.completions.create(
                            model=OPENAI_CHAT_MODEL,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=temperature, max_tokens=max_tokens)
                        result = _parse_json_response(r.choices[0].message.content)
                        used_prov = OPENAI_CHAT_MODEL
                    break
                if prov == "deepseek" and self._deepseek_client:
                    text = self._deepseek_complete(
                        [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    result = _parse_json_response(text)
                    used_prov = f"deepseek:{DEEPSEEK_CHAT_MODEL}"
                    break
                if prov == "gemini" and self._gemini_ready:
                    text, model = _gemini_generate(
                        prompt,
                        system=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    result = _parse_json_response(text)
                    used_prov = f"gemini:{model}"
                    break
                if prov == "anthropic" and self._anthropic_client:
                    resp = self._anthropic_client.messages.create(
                        model=CLAUDE_MODEL, max_tokens=max_tokens,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}])
                    result = _parse_json_response(resp.content[0].text)
                    used_prov = CLAUDE_MODEL
                    break
                if prov == "ollama" and _ollama_available():
                    model = _best_analysis_model(self._settings)
                    if not model:
                        raise RuntimeError(
                            "Ollama is running but no models are installed. "
                            f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
                        )
                    raw = _ollama_complete(
                        prompt,
                        model,
                        system=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=600,
                    )
                    result = _parse_json_response(raw)
                    if isinstance(result, dict) and not str(result.get("summary", "")).strip():
                        fallback = _ollama_complete(
                            (
                                f"Hero outcome: {_outcome_label(hero_won)} ({_format_amount(hero_won, bool((hand_meta or {}).get('is_tournament')))}). "
                                "Reply in exactly 2 sentences. No lists.\n\n"
                                f"{prompt[:900]}"
                            ),
                            model,
                            system="Output only two plain coaching sentences.",
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
                self._last_error = _friendly_api_error(e, prov)
                log.warning("[AI] analyze_hand via %s failed: %s", prov, e)
                continue

        if result:
            if not result.get("summary") and result.get("analysis"):
                result["summary"] = _clean_model_text(str(result.get("analysis", "")))
            result = _normalize_hand_analysis(
                result, hero_won, spot_flags, spots=spots, hero_name=hero_name
            )
            _apply_equity_guard(result, allowed_equities)
            result.setdefault("play_style", "Unknown")
            result.setdefault("mistakes_found", 0)
            result.setdefault("tags", [])
            result.setdefault("summary", "")
            result.setdefault("biggest_leak", None)
            result.setdefault("ev_estimate", "Unknown")
            result.setdefault("confidence", 0.5)
            prov_id, model_name = _split_provider_model(used_prov)
            result["provider"] = prov_id
            result["model"] = model_name
            result["web_context_included"] = self._last_web_context_used
            if hand_id:
                try:
                    save_analysis(self._db_path, hand_id, result, used_prov)
                except Exception:
                    pass
        return result

    # ── Session analysis ──────────────────────────────────────────────────────
    def analyze_session(self, hands_text, hero_name: str = "Hero",
                        stats: dict = None, provider: Optional[str] = None) -> str:
        self._last_web_context_used = False
        stats = stats or {}
        skip = {"biggest_wins", "biggest_losses", "by_position", "by_site", "alerts", "sessions_by_date"}
        stats_str = json.dumps({k: v for k, v in stats.items() if k not in skip}, indent=2)
        prompt = SESSION_PROMPT.format(
            hero=hero_name, stats=stats_str, hands=str(hands_text)[:2000]
        )
        dataset_block = self._dataset_context_block(provider)
        coach_system = "You are an elite poker coach."
        if dataset_block:
            coach_system = f"{coach_system}\n\n{dataset_block}"
        memory_block = self._memory_block()
        if memory_block:
            coach_system = f"{coach_system}\n\n{memory_block}"
        web_block = ""
        if self._web_search_always():
            web_block = self._web_context_block(
                self._topic_web_query(
                    f"poker leaks VPIP {stats.get('vpip')} PFR {stats.get('pfr')}"
                ),
                provider=provider,
            )
        note = self._web_access_note(provider, web_active=bool(web_block))
        if note:
            coach_system = f"{coach_system}\n\n{note}"
        if web_block:
            coach_system = f"{coach_system}\n\n{web_block}"

        for prov in self._provider_chain(provider):
            try:
                if prov == "asi1" and self._asi1_any_ready():
                    return self._session_done(self._asi1_complete(
                        [
                            {"role": "system", "content": coach_system},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=1400 if dataset_block else 1200,
                        temperature=0.4,
                        web_search=self._web_search_always(),
                        workload="inference",
                    ))
                if prov == "openai" and self._openai_client:
                    input_text = (
                        f"Hero: {hero_name}\n\nStats:\n{stats_str}\n\n"
                        f"Hands sample:\n{str(hands_text)[:2000]}"
                    )
                    try:
                        return self._session_done(self._responses_call(input_text))
                    except Exception:
                        r = self._openai_client.chat.completions.create(
                            model=OPENAI_SESSION_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.4, max_tokens=1200)
                        return self._session_done(r.choices[0].message.content)
                if prov == "deepseek" and self._deepseek_client:
                    return self._session_done(self._deepseek_complete(
                        [
                            {"role": "system", "content": coach_system},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=1200,
                        temperature=0.4,
                    ))
                if prov == "gemini" and self._gemini_ready:
                    text, _model = _gemini_generate(
                        prompt,
                        system=coach_system,
                        max_tokens=1200,
                        temperature=0.4,
                    )
                    return self._session_done(text)
                if prov == "anthropic" and self._anthropic_client:
                    resp = self._anthropic_client.messages.create(
                        model=CLAUDE_MODEL, max_tokens=1200,
                        system=coach_system,
                        messages=[{"role": "user", "content": prompt}])
                    return self._session_done(resp.content[0].text)
                if prov == "ollama" and _ollama_available():
                    model = _best_analysis_model(self._settings)
                    if not model:
                        raise RuntimeError(
                            "Ollama is running but no models are installed. "
                            f"Run: ollama pull {OLLAMA_RECOMMENDED_PULL}"
                        )
                    text = _ollama_complete(
                        prompt,
                        model,
                        system=coach_system,
                        max_tokens=1200,
                        temperature=0.4,
                        timeout=600,
                    )
                    if text:
                        return self._session_done(text)
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
            "No AI provider available. Add ASI_ONE_API_KEY to .env (recommended), "
            "other cloud keys, or start Ollama as a local fallback."
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
