"""
Conduit — AI MIDI Server

Local bridge server: receives requests from the M4L device,
forwards to Ollama (llama3.2), returns structured MIDI JSON.
Also supports Claude, GPT-4o, LM Studio, llama.cpp, and vLLM.
"""

import json
import re
import logging
from enum import Enum
from typing import Optional, Literal
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from providers import (
    ProviderRegistry,
    ProviderResponse,
    AnthropicProvider,
    OpenAIProvider,
    OpenAICompatibleProvider,
    OllamaProvider,
    build_default_registry,
)
from prompts import build_system_prompt, list_genres, get_genre_info
from autodetect import system_report, is_ollama_running, get_ollama_models
from schemas import get_midi_json_schema

# Ollama JSON schema for format-constrained MIDI generation.
# Bounds prevent invalid values (negative pitches, zero velocity, etc.)
MIDI_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "midi_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                    "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                    "start_beat": {"type": "number", "minimum": 0},
                    "duration_beats": {"type": "number", "minimum": 0.125},
                },
                "required": ["pitch", "velocity", "start_beat", "duration_beats"],
            },
        }
    },
    "required": ["midi_notes"],
}

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("conduit")

# ── Server State ─────────────────────────────────────────────────────
conversation_history: list[dict] = []
MAX_HISTORY = 40
current_genre: Optional[str] = None

# Pattern clipboard — stores recent generated patterns for recall/paste
pattern_bank: list[dict] = []
MAX_PATTERNS = 20
_pattern_id_counter = 0


# ── Lifespan ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # System detection
    report = system_report()
    sys_info = report["system"]
    logger.info(
        f"System: {sys_info['chip'] or 'unknown'} | "
        f"RAM: {sys_info['total_ram_gb']}GB total, "
        f"~{sys_info['estimated_available_gb']}GB available for LLM"
    )

    rec = report["recommended_model"]
    logger.info(f"Recommended model: {rec['model']} ({rec['quality']})")

    if report["pull_command"]:
        logger.info(f"To pull recommended model: {report['pull_command']}")

    # Build provider registry (auto-detects Ollama model)
    registry = build_default_registry()
    app.state.registry = registry
    app.state.system_report = report

    available = registry.list_available()
    for p in available:
        status = "✓ ready" if p["available"] else "✗ not configured"
        active = " (ACTIVE)" if p["active"] else ""
        logger.info(f"  {p['name']}: {p['model']} — {status}{active}")

    if not registry.active_name:
        logger.warning("No providers available! Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or start Ollama.")
    else:
        logger.info(f"✓ Active provider: {registry.active}")

    # Genre modules
    genres = list_genres()
    if genres:
        logger.info(f"Genre modules loaded: {', '.join(genres)}")

    # Warm up the active model — first inference is always slow (model loading)
    if registry.active_name:
        logger.info("Warming up model...")
        try:
            registry.active.chat(
                messages=[{"role": "user", "content": "hi"}],
                system="Reply with one word.",
                max_tokens=4,
                temperature=0.0,
            )
            logger.info("✓ Model warm — ready for requests")
        except Exception as e:
            logger.warning(f"Warmup failed (non-critical): {e}")

    logger.info("✓ Conduit server ready — listening for M4L requests")
    yield
    logger.info("Conduit server shutting down")


app = FastAPI(
    title="Conduit",
    description="AI MIDI generation for Ableton Live",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Models ────────────────────────────────────────
class SessionContext(BaseModel):
    bpm: Optional[float] = None
    time_signature: Optional[str] = None
    key: Optional[str] = None
    selected_track: Optional[str] = None
    track_names: Optional[list[str]] = None
    playing: Optional[bool] = None
    song_time: Optional[float] = None
    groove: Optional[float] = None
    extra: Optional[dict] = None


class BridgeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="User's message or command")
    session: Optional[SessionContext] = None
    mode: Literal["chat", "generate"] = Field(default="chat", description="'chat' for mixed text+JSON, 'generate' for pure MIDI JSON")
    genre: Optional[str] = Field(None, description="Genre override for this request")


class BridgeResponse(BaseModel):
    text: str
    json_blocks: list[dict] = Field(default_factory=list)
    timestamp: str
    model: str
    provider: str
    pattern_id: Optional[int] = Field(None, description="ID in pattern clipboard (generate mode only)")


class SwitchProviderRequest(BaseModel):
    provider: str = Field(..., description="Provider name to switch to")
    model: Optional[str] = Field(None, description="Optionally change the model too")


class AddProviderRequest(BaseModel):
    name: str = Field(..., description="Unique name for this provider")
    type: str = Field(..., description="'ollama', 'openai', 'openai_compatible', 'anthropic'")
    model: str = Field(..., description="Model name/identifier")
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class SetGenreRequest(BaseModel):
    genre: Optional[str] = Field(None, description="Genre name or null for genre-agnostic")


# ── Helpers ──────────────────────────────────────────────────────────
def build_user_message(req: BridgeRequest) -> str:
    parts = []
    if req.session:
        ctx_lines = ["[SESSION CONTEXT]"]
        if req.session.bpm is not None:
            ctx_lines.append(f"  BPM: {req.session.bpm}")
        if req.session.time_signature:
            ctx_lines.append(f"  Time Sig: {req.session.time_signature}")
        if req.session.key:
            ctx_lines.append(f"  Key: {req.session.key}")
        if req.session.selected_track:
            ctx_lines.append(f"  Selected Track: {req.session.selected_track}")
        if req.session.track_names:
            ctx_lines.append(f"  Tracks: {', '.join(req.session.track_names)}")
        if req.session.playing is not None:
            ctx_lines.append(f"  Playing: {req.session.playing}")
        if req.session.song_time is not None:
            ctx_lines.append(f"  Position: {req.session.song_time:.2f}s")
        if req.session.groove is not None:
            ctx_lines.append(f"  Groove: {req.session.groove}")
        if req.session.extra:
            for k, v in req.session.extra.items():
                ctx_lines.append(f"  {k}: {v}")
        parts.append("\n".join(ctx_lines))
    parts.append(req.prompt)

    # For generate mode, reinforce the note count so the model reliably
    # produces the requested quantity.  llama3.2 follows "Generate exactly
    # N notes" far more consistently than implicit counts like "8-note".
    if req.mode == "generate":
        count = _count_requested_notes(req.prompt)
        if count > 0:
            parts.append(f"Generate exactly {count} notes.")
        else:
            # Default note count when user doesn't specify
            is_drum = bool(re.search(r'drum|kick|snare|hi.?hat|perc|beat', req.prompt, re.IGNORECASE))
            default = 16 if is_drum else 8
            parts.append(f"Generate at least {default} notes.")

    return "\n\n".join(parts)


def estimate_generate_tokens(prompt: str) -> int:
    """Estimate how many tokens a generate-mode response needs.

    Heuristic: each MIDI note in JSON is ~55 tokens.  Scan the prompt for
    patterns like "32 notes", "64 steps", "16 beats", "8 bars" etc. and
    scale the budget accordingly.  Clamp to [1000, 3200].
    """
    # Direct counts: "16 notes", "32 steps", "8 hits"
    note_pattern = r'(\d+)\s*[-]?\s*(?:notes?|steps?|hits?|events?)'
    note_matches = re.findall(note_pattern, prompt, re.IGNORECASE)
    if note_matches:
        note_count = max(int(m) for m in note_matches)
        estimated = 300 + note_count * 55
        return max(1000, min(estimated, 3200))

    # Bar counts: "4-bar" → ~4 notes/bar, "8-bar drum" → ~8 notes/bar for drums
    bar_pattern = r'(\d+)\s*[-]?\s*bars?'
    bar_matches = re.findall(bar_pattern, prompt, re.IGNORECASE)
    if bar_matches:
        bars = max(int(m) for m in bar_matches)
        is_drum = bool(re.search(r'drum|kick|snare|hi.?hat|perc', prompt, re.IGNORECASE))
        notes_per_bar = 8 if is_drum else 4
        estimated = 300 + (bars * notes_per_bar) * 55
        return max(1000, min(estimated, 3200))

    # Beat counts: "16 beats" → ~1 note per beat
    beat_pattern = r'(\d+)\s*[-]?\s*beats?'
    beat_matches = re.findall(beat_pattern, prompt, re.IGNORECASE)
    if beat_matches:
        beats = max(int(m) for m in beat_matches)
        estimated = 300 + beats * 55
        return max(1000, min(estimated, 3200))

    return 1000


def extract_json_blocks(text: str) -> list[dict]:
    """Extract JSON from ```json fenced blocks in LLM response."""
    blocks = []
    segments = text.split("```json")
    for segment in segments[1:]:
        end = segment.find("```")
        if end != -1:
            raw = segment[:end].strip()
            try:
                blocks.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON block: {raw[:100]}...")
    return blocks


def _normalize_json_text(text: str) -> str:
    """Apply common fixes to almost-valid JSON produced by LLMs."""
    # Replace single quotes with double quotes (but not inside strings —
    # good-enough heuristic: only replace when adjacent to structural chars)
    text = re.sub(r"(?<=[\[{,:\s])'|'(?=[\]},:\s])", '"', text)
    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix common wrong key names → canonical keys
    key_aliases = {
        '"drumbeats"': '"drum_notes"',
        '"notes"': '"midi_notes"',
        '"note"': '"midi_notes"',
        '"drums"': '"drum_notes"',
        '"melody"': '"midi_notes"',
    }
    for wrong, right in key_aliases.items():
        # Only replace top-level occurrences (immediately after { or start)
        text = re.sub(r'(?<=\{)\s*' + re.escape(wrong), ' ' + right, text)
        # Also handle if it's the very first key
        if text.lstrip().startswith(wrong):
            text = text.replace(wrong, right, 1)
    return text


def parse_generate_response(text: str) -> list[dict]:
    """Parse response from generate mode.

    Handles:
      - Raw JSON and markdown-fenced JSON blocks
      - Text preamble before JSON (e.g. "Here is the pattern: {...}")
      - Single quotes instead of double quotes
      - Trailing commas
      - Wrong key names (drumbeats -> drum_notes, notes -> midi_notes)
      - Truncated output (model ran out of tokens mid-array)
    """
    original_text = text
    text = text.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner.strip()

    # If the response starts with non-JSON text, find the first { or [
    if text and text[0] not in ('{', '['):
        first_brace = text.find('{')
        first_bracket = text.find('[')
        candidates = [i for i in (first_brace, first_bracket) if i >= 0]
        if candidates:
            json_start = min(candidates)
            logger.debug(f"Skipping {json_start} chars of preamble text")
            text = text[json_start:]

    # Normalize common LLM quirks
    text = _normalize_json_text(text)

    # Try direct JSON parse
    try:
        data = json.loads(text)
        return [data] if isinstance(data, dict) else [{"midi_notes": data}]
    except json.JSONDecodeError:
        pass

    # Try to repair truncated JSON (model ran out of tokens mid-array)
    repaired = text.rstrip()
    # Find last complete note object
    last_brace = repaired.rfind("}")
    if last_brace > 0:
        repaired = repaired[:last_brace + 1]
        # Close any open arrays/objects
        open_brackets = repaired.count("[") - repaired.count("]")
        open_braces = repaired.count("{") - repaired.count("}")
        repaired += "]" * open_brackets + "}" * open_braces
        try:
            data = json.loads(repaired)
            logger.info(f"Repaired truncated JSON ({len(text)} -> {len(repaired)} chars)")
            return [data] if isinstance(data, dict) else [{"midi_notes": data}]
        except json.JSONDecodeError:
            pass

    # Fall back to fenced block extraction from original text
    return extract_json_blocks(original_text)


def validate_and_fix_notes(json_blocks: list[dict]) -> list[dict]:
    """Validate and clamp MIDI note data in parsed JSON blocks.

    Fixes:
      - Clamps pitch to 0-127 (catches negative pitches)
      - Clamps velocity to 1-127
      - Ensures start_beat >= 0 and duration_beats >= 0.0625
      - Removes completely invalid notes (missing required fields)
    Returns the fixed blocks.
    """
    for block in json_blocks:
        for key in ("midi_notes", "drum_notes"):
            notes = block.get(key)
            if not isinstance(notes, list):
                continue
            cleaned = []
            for n in notes:
                if not isinstance(n, dict):
                    continue
                # Must have at minimum pitch and start_beat
                if "pitch" not in n:
                    continue
                n["pitch"] = max(0, min(127, int(n["pitch"])))
                n["velocity"] = max(1, min(127, int(n.get("velocity", 100))))
                n["start_beat"] = max(0.0, float(n.get("start_beat", 0)))
                n["duration_beats"] = max(0.125, float(n.get("duration_beats", 0.25)))
                cleaned.append(n)
            block[key] = cleaned

        # Validate CC messages too
        cc = block.get("cc_messages")
        if isinstance(cc, list):
            valid_cc = []
            for msg in cc:
                if not isinstance(msg, dict) or "cc_number" not in msg:
                    continue
                msg["cc_number"] = max(0, min(127, int(msg["cc_number"])))
                msg["value"] = max(0, min(127, int(msg.get("value", 64))))
                msg["beat"] = max(0.0, float(msg.get("beat", 0)))
                valid_cc.append(msg)
            block["cc_messages"] = valid_cc

    return json_blocks


def _extend_pattern(json_blocks: list[dict], target_notes: int) -> list[dict]:
    """If the model produced a valid but short pattern, loop it to reach
    the target note count.  E.g. a 4-note 1-bar pattern → tiled across
    4 bars to produce 16 notes.  Only extends if we have >0 notes and
    fewer than the target."""
    if target_notes <= 0:
        return json_blocks

    for block in json_blocks:
        for key in ("midi_notes", "drum_notes"):
            notes = block.get(key)
            if not isinstance(notes, list) or len(notes) == 0:
                continue
            if len(notes) >= target_notes:
                continue

            # Find the pattern length (last note end → round up to bar)
            max_end = 0.0
            for n in notes:
                end = n["start_beat"] + n["duration_beats"]
                if end > max_end:
                    max_end = end
            pattern_len = max(4.0, float(int((max_end + 3.99) // 4) * 4))

            # Tile the pattern until we reach the target
            original = list(notes)
            offset = pattern_len
            while len(notes) < target_notes:
                for n in original:
                    if len(notes) >= target_notes:
                        break
                    copy = dict(n)
                    copy["start_beat"] = n["start_beat"] + offset
                    notes.append(copy)
                offset += pattern_len

            block[key] = notes
            logger.info(
                f"Extended {key}: {len(original)} → {len(notes)} notes "
                f"(pattern_len={pattern_len}, target={target_notes})"
            )

    return json_blocks


def _count_requested_notes(prompt: str) -> int:
    """Extract the number of notes/steps requested from a prompt. Returns 0 if unclear."""
    # Direct note/step/hit counts: "8 notes", "16-note", "32 hits"
    note_pattern = r'(\d+)\s*[-]?\s*(?:notes?|steps?|hits?)'
    matches = re.findall(note_pattern, prompt, re.IGNORECASE)
    if matches:
        return max(int(m) for m in matches)
    # Bar counts: "4-bar" → 4 notes per beat × 4 beats per bar in 4/4
    bar_pattern = r'(\d+)\s*[-]?\s*bars?'
    bar_matches = re.findall(bar_pattern, prompt, re.IGNORECASE)
    if bar_matches:
        bars = max(int(m) for m in bar_matches)
        return bars * 4  # ~4 notes per bar as baseline
    return 0


def _get_note_count(json_blocks: list[dict]) -> int:
    """Count total notes across all blocks."""
    total = 0
    for block in json_blocks:
        for key in ("midi_notes", "drum_notes"):
            notes = block.get(key)
            if isinstance(notes, list):
                total += len(notes)
    return total


def _save_pattern(prompt: str, genre: Optional[str], model: str, json_blocks: list[dict]) -> Optional[int]:
    """Save a generated pattern to the clipboard bank. Returns the pattern ID."""
    global pattern_bank, _pattern_id_counter
    note_count = _get_note_count(json_blocks)
    if note_count == 0:
        return None

    _pattern_id_counter += 1
    entry = {
        "id": _pattern_id_counter,
        "timestamp": datetime.utcnow().isoformat(),
        "prompt": prompt[:200],
        "genre": genre,
        "model": model,
        "note_count": note_count,
        "json_blocks": json_blocks,
    }
    pattern_bank.append(entry)
    if len(pattern_bank) > MAX_PATTERNS:
        pattern_bank = pattern_bank[-MAX_PATTERNS:]
    logger.info(f"Pattern #{_pattern_id_counter} saved ({note_count} notes)")
    return _pattern_id_counter


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    registry: ProviderRegistry = app.state.registry
    active_health = registry.breaker.get_health(registry.active_name) if registry.active_name else {}
    return {
        "status": "ok",
        "active_provider": registry.active_name,
        "active_model": getattr(registry.active, "model", "?") if registry.active_name else None,
        "genre": current_genre,
        "circuit_state": active_health.get("state", "unknown"),
        "health_score": active_health.get("health_score", 0),
        "avg_response_ms": active_health.get("avg_response_ms", 0),
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/ask", response_model=BridgeResponse)
async def ask(req: BridgeRequest):
    """Main endpoint: send prompt to the active LLM provider."""
    global conversation_history
    registry: ProviderRegistry = app.state.registry

    if not registry.active_name:
        raise HTTPException(status_code=503, detail="No LLM provider configured")

    # Build system prompt with genre context
    genre = req.genre or current_genre
    system_prompt = build_system_prompt(genre=genre, mode=req.mode)

    user_message = build_user_message(req)
    conversation_history.append({"role": "user", "content": user_message})

    if len(conversation_history) > MAX_HISTORY:
        conversation_history = conversation_history[-MAX_HISTORY:]

    # Build kwargs for provider
    if req.mode == "generate":
        gen_tokens = estimate_generate_tokens(req.prompt)
        chat_kwargs = {
            "max_tokens": gen_tokens,
            "temperature": 0.4,
            "json_schema": MIDI_FORMAT_SCHEMA,
            "repeat_penalty": 1.18,
            "top_p": 0.9,
        }
        messages_to_send = [{"role": "user", "content": user_message}]
        logger.debug(f"Generate mode: {gen_tokens} tokens, schema+sampling active")
    else:
        chat_kwargs = {"max_tokens": 4096}
        messages_to_send = conversation_history

    # Use dedicated generate provider if available (with validation + retry)
    if req.mode == "generate" and "ollama_generate" in registry.providers:
        gen_provider = registry.providers["ollama_generate"]
        if gen_provider.is_available():
            import time as _time
            requested_notes = _count_requested_notes(req.prompt)
            if requested_notes == 0:
                # Default: ensure short patterns get tiled to something usable
                is_drum = bool(re.search(r'drum|kick|snare|hi.?hat|perc|beat', req.prompt, re.IGNORECASE))
                requested_notes = 16 if is_drum else 8
            max_attempts = 2  # original + 1 retry

            for attempt in range(max_attempts):
                start = _time.time()
                try:
                    response = gen_provider.chat(
                        system=system_prompt,
                        messages=messages_to_send,
                        **chat_kwargs,
                    )
                    elapsed_ms = (_time.time() - start) * 1000
                    registry.breaker.record_success("ollama_generate", elapsed_ms)
                    logger.info(f"Generate via {gen_provider.model} (attempt {attempt+1}): {elapsed_ms:.0f}ms")
                except Exception as e:
                    logger.warning(f"Generate provider failed: {e}, falling back")
                    response = None
                    break

                if response is None:
                    break

                json_blocks = parse_generate_response(response.text)
                json_blocks = validate_and_fix_notes(json_blocks)
                actual_notes = _get_note_count(json_blocks)

                # Check if result is good enough
                needs_retry = False
                if not json_blocks or actual_notes == 0:
                    logger.warning(f"Attempt {attempt+1}: no valid notes parsed")
                    needs_retry = True
                elif requested_notes > 0 and actual_notes < requested_notes * 0.5:
                    logger.warning(
                        f"Attempt {attempt+1}: got {actual_notes} notes, "
                        f"wanted {requested_notes} (< 50%)"
                    )
                    needs_retry = True

                if not needs_retry or attempt == max_attempts - 1:
                    # Good enough or last attempt — extend short patterns by tiling
                    if requested_notes > 0:
                        json_blocks = _extend_pattern(json_blocks, requested_notes)
                        actual_notes = _get_note_count(json_blocks)
                    conversation_history.append({"role": "assistant", "content": response.text})
                    pid = _save_pattern(req.prompt, genre, response.model, json_blocks)
                    logger.info(
                        f"[{response.provider}/{response.model}] "
                        f"mode: generate | genre: {genre or 'none'} | "
                        f"json_blocks: {len(json_blocks)} | notes: {actual_notes} | "
                        f"pattern: #{pid or '-'} | "
                        f"tokens: {response.input_tokens or '?'}in/{response.output_tokens or '?'}out"
                    )
                    return BridgeResponse(
                        text=response.text,
                        json_blocks=json_blocks,
                        timestamp=datetime.utcnow().isoformat(),
                        model=response.model,
                        provider=response.provider,
                        pattern_id=pid,
                    )

                # Retry with boosted token budget and lower temperature
                logger.info(f"Retrying generation (attempt {attempt+2})")
                chat_kwargs["max_tokens"] = min(chat_kwargs.get("max_tokens", 1000) + 600, 3200)
                chat_kwargs["temperature"] = 0.2

    try:
        response: ProviderResponse = registry.chat_with_failover(
            system=system_prompt,
            messages=messages_to_send,
            **chat_kwargs,
        )
    except RuntimeError as e:
        logger.error(f"All providers failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"Provider error: {e}")
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    conversation_history.append({"role": "assistant", "content": response.text})

    # Parse JSON blocks based on mode
    pid = None
    if req.mode == "generate":
        json_blocks = parse_generate_response(response.text)
        json_blocks = validate_and_fix_notes(json_blocks)
        requested_notes = _count_requested_notes(req.prompt)
        if requested_notes > 0:
            json_blocks = _extend_pattern(json_blocks, requested_notes)
        pid = _save_pattern(req.prompt, genre, response.model, json_blocks)
    else:
        json_blocks = extract_json_blocks(response.text)

    logger.info(
        f"[{response.provider}/{response.model}] "
        f"mode: {req.mode} | genre: {genre or 'none'} | "
        f"prompt: {req.prompt[:60]}... | "
        f"json_blocks: {len(json_blocks)} | "
        f"tokens: {response.input_tokens or '?'}in/{response.output_tokens or '?'}out"
    )

    return BridgeResponse(
        text=response.text,
        json_blocks=json_blocks,
        timestamp=datetime.utcnow().isoformat(),
        model=response.model,
        provider=response.provider,
        pattern_id=pid,
    )


# ── Genre Management ─────────────────────────────────────────────────

@app.get("/genres")
async def get_genres():
    """List available genre modules."""
    genres = list_genres()
    return {
        "genres": genres,
        "active": current_genre,
    }


@app.get("/genres/{genre_name}")
async def get_genre(genre_name: str):
    """Get full genre module data."""
    info = get_genre_info(genre_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Genre '{genre_name}' not found")
    return info


@app.post("/genres/set")
async def set_genre(req: SetGenreRequest):
    """Set the active genre (affects system prompt for all subsequent requests)."""
    global current_genre
    if req.genre and req.genre not in list_genres():
        raise HTTPException(status_code=404, detail=f"Genre '{req.genre}' not found. Available: {list_genres()}")
    current_genre = req.genre
    logger.info(f"Genre set to: {current_genre or 'none (genre-agnostic)'}")
    return {"status": "set", "genre": current_genre}


# ── System Detection ─────────────────────────────────────────────────

@app.get("/system")
async def get_system_info():
    """System detection report: RAM, chip, recommended model, Ollama status."""
    return app.state.system_report


@app.get("/system/ollama/status")
async def ollama_status():
    """Check Ollama status and available models."""
    running = is_ollama_running()
    models = get_ollama_models() if running else []
    return {
        "running": running,
        "models": models,
    }


# ── Provider Management ──────────────────────────────────────────────

@app.get("/providers")
async def list_providers():
    """List all registered providers and their status."""
    registry: ProviderRegistry = app.state.registry
    return {"providers": registry.list_available()}


@app.post("/providers/switch")
async def switch_provider(req: SwitchProviderRequest):
    """Switch the active provider (and optionally model)."""
    registry: ProviderRegistry = app.state.registry
    try:
        provider = registry.switch(req.provider)
        if req.model:
            provider.model = req.model
        return {
            "status": "switched",
            "provider": req.provider,
            "model": provider.model,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/providers/add")
async def add_provider(req: AddProviderRequest):
    """Register a new provider at runtime."""
    registry: ProviderRegistry = app.state.registry

    if req.name in registry.providers:
        raise HTTPException(status_code=409, detail=f"Provider '{req.name}' already exists")

    if req.type == "ollama":
        provider = OllamaProvider(
            model=req.model,
            base_url=req.base_url or "http://localhost:11434",
        )
        provider.name = req.name
    elif req.type == "openai":
        provider = OpenAIProvider(
            model=req.model,
            api_key=req.api_key,
        )
        provider.name = req.name
    elif req.type == "openai_compatible":
        if not req.base_url:
            raise HTTPException(status_code=400, detail="base_url required for openai_compatible")
        provider = OpenAICompatibleProvider(
            base_url=req.base_url,
            model=req.model,
            api_key=req.api_key or "not-needed",
            name_override=req.name,
        )
    elif req.type == "anthropic":
        provider = AnthropicProvider(
            model=req.model,
            api_key=req.api_key,
        )
        provider.name = req.name
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown type '{req.type}'. Use: ollama, openai, openai_compatible, anthropic",
        )

    registry.register(provider)
    return {"status": "added", "name": req.name, "model": req.model}


@app.get("/providers/ollama/models")
async def list_ollama_models():
    """List models available in Ollama."""
    registry: ProviderRegistry = app.state.registry
    for name, provider in registry.providers.items():
        if isinstance(provider, OllamaProvider):
            models = provider.list_models()
            return {"models": models}
    raise HTTPException(status_code=404, detail="No Ollama provider registered")


@app.get("/providers/health")
async def provider_health():
    """Circuit breaker state and health scores for all providers."""
    registry: ProviderRegistry = app.state.registry
    return {"health": registry.breaker.get_all_health()}


@app.post("/providers/reset-circuit/{provider_name}")
async def reset_circuit(provider_name: str):
    """Manually reset a tripped circuit breaker."""
    registry: ProviderRegistry = app.state.registry
    if provider_name not in registry.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")
    registry.breaker.reset(provider_name)
    return {"status": "reset", "provider": provider_name}


# ── Model Warmup ──────────────────────────────────────────────────────

@app.post("/warmup")
async def warmup():
    """Send a tiny request to the active provider to load the model into memory.

    Ollama (and similar local servers) lazy-load models on first request,
    which adds significant cold-start latency.  Call this endpoint once
    after startup or before a session to eliminate that delay.
    """
    import time as _time
    registry: ProviderRegistry = app.state.registry

    if not registry.active_name:
        raise HTTPException(status_code=503, detail="No LLM provider configured")

    # Build a minimal warmup request — just enough to force model loading
    warmup_system = "Respond with OK."
    warmup_messages = [{"role": "user", "content": "hi"}]
    warmup_kwargs = {"max_tokens": 4, "temperature": 0.0}

    providers_warmed = []
    # Warm the active provider and any dedicated generate provider
    targets = [registry.active_name]
    if "ollama_generate" in registry.providers:
        targets.append("ollama_generate")

    for name in targets:
        provider = registry.providers.get(name)
        if provider is None or not provider.is_available():
            continue
        start = _time.time()
        try:
            provider.chat(warmup_system, warmup_messages, **warmup_kwargs)
            elapsed_ms = (_time.time() - start) * 1000
            registry.breaker.record_success(name, elapsed_ms)
            providers_warmed.append({"name": name, "model": getattr(provider, "model", "?"), "warmup_ms": round(elapsed_ms)})
            logger.info(f"Warmed up {name} ({getattr(provider, 'model', '?')}): {elapsed_ms:.0f}ms")
        except Exception as e:
            elapsed_ms = (_time.time() - start) * 1000
            logger.warning(f"Warmup failed for {name}: {e} ({elapsed_ms:.0f}ms)")
            providers_warmed.append({"name": name, "model": getattr(provider, "model", "?"), "error": str(e)})

    return {"status": "warmed", "providers": providers_warmed}


# ── Conversation Management ──────────────────────────────────────────

@app.post("/reset")
async def reset_conversation():
    global conversation_history
    conversation_history = []
    logger.info("Conversation history cleared")
    return {"status": "cleared"}


@app.get("/history")
async def get_history():
    return {"messages": conversation_history, "count": len(conversation_history)}


# ── Pattern Clipboard ────────────────────────────────────────────────

@app.get("/patterns")
async def list_patterns():
    """List saved patterns (most recent first)."""
    summaries = []
    for p in reversed(pattern_bank):
        summaries.append({
            "id": p["id"],
            "timestamp": p["timestamp"],
            "prompt": p["prompt"],
            "genre": p["genre"],
            "note_count": p["note_count"],
        })
    return {"patterns": summaries, "count": len(pattern_bank)}


@app.get("/patterns/latest")
async def get_latest_pattern():
    """Get the most recently saved pattern (full data for paste)."""
    if not pattern_bank:
        raise HTTPException(status_code=404, detail="No patterns saved yet")
    return pattern_bank[-1]


@app.get("/patterns/{pattern_id}")
async def get_pattern(pattern_id: int):
    """Get a specific pattern by ID."""
    for p in pattern_bank:
        if p["id"] == pattern_id:
            return p
    raise HTTPException(status_code=404, detail=f"Pattern #{pattern_id} not found")


@app.delete("/patterns")
async def clear_patterns():
    """Clear all saved patterns."""
    global pattern_bank, _pattern_id_counter
    pattern_bank = []
    _pattern_id_counter = 0
    return {"status": "cleared"}


# ── Run ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=9321,
        reload=True,
        log_level="info",
    )
