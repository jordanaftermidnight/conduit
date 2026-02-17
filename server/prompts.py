"""
prompts.py — Modular system prompt builder for Conduit

Constructs system prompts from: BASE_PROMPT + GENRE_MODULE + RESPONSE_FORMAT
Genre modules are loaded from YAML files in server/genres/.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("conduit")

# ── Base Prompt (always included) ────────────────────────────────────

BASE_PROMPT = """You are Conduit, an AI music production assistant embedded inside Ableton Live via a Max for Live device. You help the user with:

- Generating MIDI patterns (as JSON arrays of note objects)
- Suggesting sound design parameters, effects chains, and mixing decisions
- Analysing session context (BPM, key, track layout) and making creative suggestions
- Arranging and structuring tracks
- Explaining synthesis techniques and audio concepts

SESSION CONTEXT will be provided with each message showing current BPM, time signature, tracks, and other Live session state. Use this to inform your suggestions.

When the session has Ableton grooves loaded, factor the groove state into generation. Suggest when to apply Ableton's built-in groove templates rather than generating swing manually."""

# ── Response Format Rules ────────────────────────────────────────────

RESPONSE_FORMAT_CHAT = """
RESPONSE FORMAT RULES:
- When the user asks for MIDI data, return a JSON block with key "midi_notes" containing an array of note objects.
- Each note: {"pitch": 0-127, "velocity": 1-127, "start_beat": float, "duration_beats": float, "is_drum": bool}
- For drum patterns, you may also use a separate "drum_notes" array. Use GM drum map pitches:
  Kick=36, Snare=38, Rimshot=37, Clap=39, Closed HH=42, Open HH=46,
  Pedal HH=44, Low Tom=41, Mid Tom=47, High Tom=50, Crash=49, Ride=51,
  Ride Bell=53, Cowbell=56, Tambourine=54, Shaker=69, Clave=75.
  Mark all drum notes with "is_drum": true.
- For MIDI CC automation, include a "cc_messages" array: {"cc_number": 0-127, "value": 0-127, "beat": float}
  Common CC numbers: 1=Mod Wheel, 7=Volume, 10=Pan, 11=Expression, 64=Sustain, 71=Resonance, 74=Filter Cutoff.
- Include "swing" (0-100) to apply swing feel. 0=straight, 50=moderate, 100=extreme triplet feel.
- Include "quantize" to set the grid: "1/4", "1/8", "1/16", "1/32", or triplets: "1/4t", "1/8t", "1/16t", "1/32t".
- When the user asks for parameter changes, return a JSON block with key "params" containing an array of {"track": int|str, "device": str, "param": str, "value": float}.
- For text responses (advice, explanations), just respond naturally.
- You can mix text and JSON blocks in the same response.
- Wrap any JSON data in ```json blocks so the client can parse them.

VELOCITY DYNAMICS:
- Use velocity to create musical dynamics. Don't make every note the same velocity.
- Ghost notes: vel 20-50 (soft, subtle). Normal: vel 70-100. Accents: vel 110-127.
- Common patterns: accent beat 1 & 3 (or 2 & 4 for backbeat), ghost notes on offbeats.
- Crescendo: gradually increase velocity. Decrescendo: gradually decrease.
- Hi-hats often alternate accent/ghost for a natural feel.

SCALE REFERENCE (for melodic content — choose notes from the appropriate scale):
- Chromatic: all 12 semitones
- Major (Ionian): W-W-H-W-W-W-H (e.g. C D E F G A B)
- Natural Minor (Aeolian): W-H-W-W-H-W-W (e.g. C D Eb F G Ab Bb)
- Harmonic Minor: W-H-W-W-H-WH-H (e.g. C D Eb F G Ab B)
- Melodic Minor (asc): W-H-W-W-W-W-H (e.g. C D Eb F G A B)
- Dorian: W-H-W-W-W-H-W (e.g. C D Eb F G A Bb) — jazzy minor
- Phrygian: H-W-W-W-H-W-W (e.g. C Db Eb F G Ab Bb) — flamenco, dark
- Lydian: W-W-W-H-W-W-H (e.g. C D E F# G A B) — bright, dreamy
- Mixolydian: W-W-H-W-W-H-W (e.g. C D E F G A Bb) — dominant, blues-rock
- Locrian: H-W-W-H-W-W-W (e.g. C Db Eb F Gb Ab Bb) — diminished feel
- Pentatonic Major: W-W-WH-W-WH (e.g. C D E G A)
- Pentatonic Minor: WH-W-W-WH-W (e.g. C Eb F G Bb)
- Blues: WH-W-H-H-WH-W (e.g. C Eb F Gb G Bb)
- Whole Tone: W-W-W-W-W-W (e.g. C D E F# G# A#)
When the session key is provided, use it. Otherwise infer from context or ask."""

RESPONSE_FORMAT_GENERATE = """
RESPONSE FORMAT:
You MUST respond with ONLY a JSON object. No text before or after.
The JSON object must have a "midi_notes" key containing an array of note objects.
Each note: {"pitch": 0-127, "velocity": 1-127, "start_beat": float >= 0, "duration_beats": float > 0, "is_drum": bool}
For drum patterns, use "drum_notes" array with GM drum pitches (kick=36, snare=38, closed_hh=42, open_hh=46, clap=39, ride=51, crash=49).
Optionally include: "cc_messages": [{"cc_number": int, "value": 0-127, "beat": float}], "swing": 0-100, "quantize": "1/16" (or triplets: "1/8t","1/16t").
Use varied velocities for musical dynamics: ghost notes 20-50, normal 70-100, accents 110-127.
Do not include any explanation, markdown, or code fences. Output raw JSON only."""

# ── Compact Generate Prompt (for fast local inference) ────────────────

GENERATE_SYSTEM_PROMPT = (
    'Output ONLY a raw JSON object. No text. No markdown. No code fences. No explanation.\n'
    'Format: {"midi_notes":[{"pitch":60,"velocity":100,"start_beat":0.0,"duration_beats":0.5}, ...]}\n'
    "RULES:\n"
    "- pitch: integer 0-127. NEVER negative. C3=48, C4=60, C5=72. Bass: 36-60. Melody: 48-84.\n"
    "- velocity: integer 1-127. MUST vary for dynamics — never all the same value.\n"
    "  Accents: 110-127. Normal: 70-100. Ghost notes: 20-50.\n"
    "  Accent beats 1 & 3, softer on offbeats. Hi-hats alternate accent/ghost.\n"
    "- start_beat: float >= 0. Space notes across beats, not all at 0.\n"
    "- duration_beats: float > 0. Minimum 0.125 (1/32 note). Use 0.25-1.0 for melodies, 0.125-0.5 for drums/percussion.\n"
    "- Generate EXACTLY the number of notes requested. If asked for 8 notes, output 8.\n"
    "- For drums: kick=36 snare=38 closed_hh=42 open_hh=46 clap=39 ride=51 crash=49.\n"
    '- Optional keys: "drum_notes":[...], "cc_messages":[{"cc_number":74,"value":64,"beat":0.0}], "swing":0-100, "quantize":"1/16" (or triplets: "1/8t","1/16t").\n'
    "Scales: major=W-W-H-W-W-W-H, minor=W-H-W-W-H-W-W, dorian=W-H-W-W-W-H-W, phrygian=H-W-W-W-H-W-W, pentatonic_min=WH-W-W-WH-W."
)

# ── Genre Loading ────────────────────────────────────────────────────

GENRES_DIR = Path(__file__).parent / "genres"

_genre_cache: dict[str, dict] = {}


def _load_genre(name: str) -> Optional[dict]:
    """Load a genre module from YAML file. Caches results."""
    if name in _genre_cache:
        return _genre_cache[name]

    yaml_path = GENRES_DIR / f"{name}.yaml"
    if not yaml_path.exists():
        return None

    try:
        # Use a simple YAML parser that doesn't require PyYAML
        genre_data = _parse_simple_yaml(yaml_path)
        _genre_cache[name] = genre_data
        return genre_data
    except Exception as e:
        logger.warning(f"Failed to load genre '{name}': {e}")
        return None


def _parse_simple_yaml(path: Path) -> dict:
    """
    Parse a simple YAML file without requiring PyYAML.
    Handles: key: value, key: [list], multi-line strings.
    Falls back to PyYAML if available.
    """
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        pass

    # Minimal parser for our genre YAML format
    # Handles: key: value, key: [list], nested dicts, folded scalars (>), literal scalars (|)
    data = {}
    current_key = None
    current_list = None
    block_key = None       # key being filled by a folded/literal block
    block_lines = []       # accumulated block lines
    block_sep = " "        # " " for folded (>), "\n" for literal (|)
    parent_key = None      # for nested dicts (one level deep)

    def _flush_block():
        nonlocal block_key, block_lines
        if block_key and block_lines:
            text = block_sep.join(block_lines).strip()
            if parent_key and isinstance(data.get(parent_key), dict):
                data[parent_key][block_key] = text
            else:
                data[block_key] = text
        block_key = None
        block_lines = []

    def _parse_value(v):
        """Parse a scalar value string into the appropriate Python type."""
        if not v:
            return v
        # Inline list: [a, b, c]
        if v.startswith("[") and v.endswith("]"):
            items = v[1:-1].split(",")
            return [i.strip().strip('"').strip("'") for i in items if i.strip()]
        # Boolean
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        # Number
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        # String
        return v.strip('"').strip("'")

    with open(path) as f:
        for line in f:
            raw = line.rstrip("\n")
            stripped = raw.strip()
            indent = len(raw) - len(raw.lstrip())

            # Skip comments and empty lines
            if not stripped or stripped.startswith("#"):
                if block_key and block_lines:
                    block_lines.append("")  # preserve paragraph breaks
                continue

            # Accumulate block scalar lines (indented continuation)
            # A line is a block continuation if it's indented and doesn't look like a key: value pair
            is_kv = ":" in stripped and not stripped.startswith("-") and not stripped.startswith("[")
            if block_key and indent > 0 and not is_kv:
                block_lines.append(stripped)
                continue
            elif block_key:
                _flush_block()

            # List item under current key
            if stripped.startswith("- ") and current_key:
                if current_list is None:
                    current_list = []
                    data[current_key] = current_list
                current_list.append(stripped[2:].strip().strip('"').strip("'"))
                continue

            # Key: value pair
            if ":" in stripped and not stripped.startswith("-"):
                current_list = None
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                # Nested dict detection: indented key-value under a bare parent key
                if indent > 0 and parent_key and parent_key != key:
                    # Initialize parent as dict if not already
                    if not isinstance(data.get(parent_key), dict):
                        data[parent_key] = {}
                    current_key = key
                    if value in (">", "|"):
                        block_key = key
                        block_lines = []
                        block_sep = " " if value == ">" else "\n"
                    elif value:
                        data[parent_key][key] = _parse_value(value)
                    continue

                # Top-level key
                if indent == 0:
                    parent_key = None

                current_key = key

                if value in (">", "|"):
                    # Folded or literal block scalar
                    block_key = key
                    block_lines = []
                    block_sep = " " if value == ">" else "\n"
                elif not value:
                    # Empty value — next lines will be list items or nested dict
                    # Don't pre-create; let the first continuation line decide the type
                    parent_key = key
                else:
                    parent_key = None
                    data[key] = _parse_value(value)

    _flush_block()
    return data


def list_genres() -> list[str]:
    """List all available genre modules."""
    if not GENRES_DIR.exists():
        return []
    return sorted(
        p.stem for p in GENRES_DIR.glob("*.yaml")
    )


def get_genre_info(name: str) -> Optional[dict]:
    """Get full genre data for API response."""
    return _load_genre(name)


# ── Genre Prompt Construction ────────────────────────────────────────

def _build_genre_section(genre_name: str) -> str:
    """Build the genre context section for the system prompt."""
    genre = _load_genre(genre_name)
    if not genre:
        return ""

    lines = [f"\nGENRE CONTEXT: {genre.get('name', genre_name).upper()}"]

    if "bpm_range" in genre:
        bpm = genre["bpm_range"]
        if isinstance(bpm, list) and len(bpm) == 2:
            lines.append(f"BPM: {bpm[0]}-{bpm[1]}")
        else:
            lines.append(f"BPM: {bpm}")

    field_map = {
        "time_signatures": "Time Signatures",
        "scales": "Scales",
        "key_tendencies": "Key Tendencies",
        "rhythm_style": "Rhythm",
        "swing": "Swing",
        "bass_style": "Bass",
        "drum_patterns": "Drums",
        "structure": "Structure",
        "dynamics": "Dynamics",
        "effects": "Effects",
        "instrument_conventions": "Instruments",
        "reference_artists": "Reference Artists",
    }

    for key, label in field_map.items():
        if key in genre:
            value = genre[key]
            if isinstance(value, list):
                lines.append(f"{label}: {', '.join(str(v) for v in value)}")
            else:
                lines.append(f"{label}: {value}")

    if "subgenres" in genre:
        subs = genre["subgenres"]
        if isinstance(subs, list):
            lines.append(f"Related Subgenres: {', '.join(subs)}")

    return "\n".join(lines)


# ── Brief Genre Info (for generate mode — BPM + scales only) ─────────

def _build_genre_brief(genre_name: str) -> str:
    """Build a minimal genre hint with only BPM and scale info for fast generation."""
    genre = _load_genre(genre_name)
    if not genre:
        return ""

    parts = []
    name = genre.get("name", genre_name)
    parts.append(f"Genre: {name}.")

    if "bpm_range" in genre:
        bpm = genre["bpm_range"]
        if isinstance(bpm, list) and len(bpm) == 2:
            parts.append(f"BPM: {bpm[0]}-{bpm[1]}.")
        else:
            parts.append(f"BPM: {bpm}.")

    if "scales" in genre:
        scales = genre["scales"]
        if isinstance(scales, list):
            parts.append(f"Scales: {', '.join(str(s) for s in scales)}.")
        else:
            parts.append(f"Scales: {scales}.")

    if "key_tendencies" in genre:
        kt = genre["key_tendencies"]
        if isinstance(kt, list):
            parts.append(f"Keys: {', '.join(str(k) for k in kt)}.")
        else:
            parts.append(f"Keys: {kt}.")

    # Velocity range from genre
    dynamics = genre.get("dynamics", {})
    if isinstance(dynamics, dict) and "velocity_range" in dynamics:
        vr = dynamics["velocity_range"]
        if isinstance(vr, list) and len(vr) == 2:
            parts.append(f"Velocity: {vr[0]}-{vr[1]}.")

    # First sentence of rhythm style for musical context
    rhythm = genre.get("rhythm_style", "")
    if isinstance(rhythm, str) and rhythm.strip():
        first_sentence = rhythm.strip().split(". ")[0].strip()
        if first_sentence:
            parts.append(f"Rhythm: {first_sentence}.")

    return " ".join(parts)


# ── Main Prompt Builder ──────────────────────────────────────────────

def build_system_prompt(
    genre: Optional[str] = None,
    mode: str = "chat",
) -> str:
    """
    Build the full system prompt.

    Args:
        genre: Genre name (e.g. "techno", "dnb"). None for genre-agnostic.
        mode: "chat" for mixed text+JSON, "generate" for pure JSON output.

    Returns:
        Complete system prompt string.
    """
    # Generate mode: use the compact prompt for faster local inference
    if mode == "generate":
        parts = [GENERATE_SYSTEM_PROMPT]
        if genre:
            brief = _build_genre_brief(genre)
            if brief:
                parts.append(brief)
            else:
                logger.warning(f"Genre '{genre}' not found, using generic prompt")
        return " ".join(parts)

    # Chat mode: full prompt with detailed genre module
    parts = [BASE_PROMPT]

    if genre:
        genre_section = _build_genre_section(genre)
        if genre_section:
            parts.append(genre_section)
        else:
            logger.warning(f"Genre '{genre}' not found, using generic prompt")

    parts.append(RESPONSE_FORMAT_CHAT)

    return "\n".join(parts)


# ── Default (backwards compatible) ───────────────────────────────────

# Default genre-agnostic prompt for backward compatibility
DEFAULT_SYSTEM_PROMPT = build_system_prompt(genre=None, mode="chat")
