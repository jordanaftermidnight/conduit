"""Pydantic v2 models for structured MIDI/param output.

Used for:
- Validating LLM JSON output
- Generating JSON schemas for grammar-constrained decoding (Ollama's `format` parameter)
- Type safety throughout the server
"""

from __future__ import annotations

from pydantic import BaseModel, Field, TypeAdapter


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class MIDINote(BaseModel):
    """Single MIDI note."""

    pitch: int = Field(ge=0, le=127, description="MIDI pitch 0-127")
    velocity: int = Field(ge=1, le=127, description="MIDI velocity 1-127")
    start_beat: float = Field(ge=0, description="Start position in beats")
    duration_beats: float = Field(gt=0, description="Duration in beats")
    is_drum: bool = Field(default=False, description="True if this note targets a drum/percussion voice (GM drum map pitches)")


class CCMessage(BaseModel):
    """Single MIDI CC (continuous controller) message."""

    cc_number: int = Field(ge=0, le=127, description="MIDI CC number (e.g. 1=mod wheel, 74=filter cutoff)")
    value: int = Field(ge=0, le=127, description="CC value 0-127")
    beat: float = Field(ge=0, description="Position in beats")


class MIDIPattern(BaseModel):
    """A pattern of MIDI notes (what the LLM generates for MIDI requests)."""

    midi_notes: list[MIDINote]
    drum_notes: list[MIDINote] | None = Field(default=None, description="Drum/percussion notes using GM drum map pitches (kick=36, snare=38, etc.)")
    cc_messages: list[CCMessage] | None = Field(default=None, description="MIDI CC automation messages")
    swing: float | None = Field(default=None, ge=0, le=100, description="Swing amount 0-100%")
    quantize: str | None = Field(default=None, description="Quantization grid e.g. '1/4', '1/8', '1/16', '1/32'")


class ParamChange(BaseModel):
    """A single parameter change suggestion."""

    track: int | str = Field(description="Track index or name")
    device: str = Field(description="Device name on the track")
    param: str = Field(description="Parameter name")
    value: float = Field(description="Target value")


class ParamSuggestion(BaseModel):
    """Parameter change suggestions from LLM."""

    params: list[ParamChange]


class MIDIGenerationResponse(BaseModel):
    """Combined response that may contain MIDI and/or params."""

    midi_notes: list[MIDINote] | None = None
    drum_notes: list[MIDINote] | None = None
    cc_messages: list[CCMessage] | None = None
    params: list[ParamChange] | None = None
    swing: float | None = Field(default=None, ge=0, le=100)
    quantize: str | None = None
    explanation: str | None = None


# ---------------------------------------------------------------------------
# Schema helpers — pass the return value to Ollama's `format` parameter
# ---------------------------------------------------------------------------

def get_midi_json_schema() -> dict:
    """Return the JSON Schema for MIDIPattern.

    Pass this directly to Ollama's ``format`` parameter to enable
    grammar-constrained decoding for MIDI generation requests.
    """
    return MIDIPattern.model_json_schema()


def get_param_json_schema() -> dict:
    """Return the JSON Schema for ParamSuggestion.

    Pass this directly to Ollama's ``format`` parameter to enable
    grammar-constrained decoding for parameter-change requests.
    """
    return ParamSuggestion.model_json_schema()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_midi_note_list_adapter = TypeAdapter(list[MIDINote])
_cc_message_list_adapter = TypeAdapter(list[CCMessage])


def validate_midi_notes(data: list[dict]) -> list[MIDINote]:
    """Validate a list of raw dicts and return typed MIDINote objects.

    Raises ``pydantic.ValidationError`` if any note is invalid.
    """
    return _midi_note_list_adapter.validate_python(data)


def validate_cc_messages(data: list[dict]) -> list[CCMessage]:
    """Validate a list of raw dicts and return typed CCMessage objects.

    Raises ``pydantic.ValidationError`` if any message is invalid.
    """
    return _cc_message_list_adapter.validate_python(data)
