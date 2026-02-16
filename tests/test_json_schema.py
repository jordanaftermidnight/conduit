"""
test_json_schema.py -- Tests for JSON block extraction and validation.

Covers:
  - extract_json_blocks() with valid ```json blocks
  - Multiple JSON blocks in one response
  - Malformed JSON handling
  - Empty response
  - MIDI note schema validation (pitch 0-127, velocity 0-127, etc.)
  - Param schema validation ({track, device, param, value})

Tests the extract_json_blocks() function (ported from main.py to avoid
pulling the full FastAPI application graph during import) and applies
schema rules documented in the SYSTEM_PROMPT.
"""

import json
import logging
import pytest

logger = logging.getLogger("conduit")


# ---------------------------------------------------------------------------
# Local copy of extract_json_blocks to avoid importing main.py (which pulls
# FastAPI, autodetect, etc.).  The canonical implementation lives in
# conduit/server/main.py -- keep these in sync.
# ---------------------------------------------------------------------------
def extract_json_blocks(text: str) -> list[dict]:
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


# ═══════════════════════════════════════════════════════════════════════════
# Schema Validators (mirrors the SYSTEM_PROMPT contract)
# ═══════════════════════════════════════════════════════════════════════════

def validate_midi_note(note: dict) -> list[str]:
    """Validate a single MIDI note dict. Returns a list of error strings."""
    errors = []
    required = {"pitch", "velocity", "start_beat", "duration_beats"}
    missing = required - set(note.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
        return errors

    if not isinstance(note["pitch"], (int, float)) or not (0 <= note["pitch"] <= 127):
        errors.append(f"pitch must be 0-127, got {note['pitch']}")
    if not isinstance(note["velocity"], (int, float)) or not (0 <= note["velocity"] <= 127):
        errors.append(f"velocity must be 0-127, got {note['velocity']}")
    if not isinstance(note["start_beat"], (int, float)) or note["start_beat"] < 0:
        errors.append(f"start_beat must be >= 0, got {note['start_beat']}")
    if not isinstance(note["duration_beats"], (int, float)) or note["duration_beats"] <= 0:
        errors.append(f"duration_beats must be > 0, got {note['duration_beats']}")
    return errors


def validate_midi_block(block: dict) -> list[str]:
    """Validate a complete MIDI JSON block."""
    if "midi_notes" not in block:
        return ["Missing 'midi_notes' key"]
    if not isinstance(block["midi_notes"], list):
        return ["'midi_notes' must be a list"]
    errors = []
    for i, note in enumerate(block["midi_notes"]):
        note_errors = validate_midi_note(note)
        for e in note_errors:
            errors.append(f"note[{i}]: {e}")
    return errors


def validate_param(param: dict) -> list[str]:
    """Validate a single param-change dict."""
    errors = []
    required = {"track", "device", "param", "value"}
    missing = required - set(param.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
        return errors
    if not isinstance(param["device"], str):
        errors.append(f"device must be str, got {type(param['device']).__name__}")
    if not isinstance(param["param"], str):
        errors.append(f"param must be str, got {type(param['param']).__name__}")
    if not isinstance(param["value"], (int, float)):
        errors.append(f"value must be numeric, got {type(param['value']).__name__}")
    return errors


def validate_param_block(block: dict) -> list[str]:
    """Validate a complete params JSON block."""
    if "params" not in block:
        return ["Missing 'params' key"]
    if not isinstance(block["params"], list):
        return ["'params' must be a list"]
    errors = []
    for i, p in enumerate(block["params"]):
        param_errors = validate_param(p)
        for e in param_errors:
            errors.append(f"param[{i}]: {e}")
    return errors


# ═══════════════════════════════════════════════════════════════════════════
# extract_json_blocks -- Basic Extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractJsonBlocks:

    def test_single_valid_block(self):
        text = 'Here is some MIDI:\n\n```json\n{"midi_notes": []}\n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"midi_notes": []}

    def test_multiple_blocks(self, sample_response_with_json):
        blocks = extract_json_blocks(sample_response_with_json)
        assert len(blocks) == 2
        assert "midi_notes" in blocks[0]
        assert "params" in blocks[1]

    def test_empty_response(self):
        assert extract_json_blocks("") == []

    def test_no_json_blocks(self):
        text = "Just a plain text response about sound design."
        assert extract_json_blocks(text) == []

    def test_text_before_and_after(self):
        text = (
            "Let me generate that for you.\n\n"
            '```json\n{"key": "value"}\n```\n\n'
            "Hope that helps!"
        )
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"key": "value"}

    def test_malformed_json_skipped(self):
        text = '```json\n{broken json here\n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 0

    def test_one_valid_one_malformed(self):
        text = (
            '```json\n{"good": true}\n```\n'
            '```json\n{bad json\n```\n'
        )
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"good": True}

    def test_nested_json(self):
        nested = {"outer": {"inner": [1, 2, 3]}}
        text = f'```json\n{json.dumps(nested)}\n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == nested

    def test_json_array_block(self):
        arr = [{"a": 1}, {"b": 2}]
        text = f'```json\n{json.dumps(arr)}\n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == arr

    def test_whitespace_inside_block(self):
        text = '```json\n  \n  {"spaced": true}  \n  \n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"spaced": True}

    def test_no_closing_backticks(self):
        """If there's no closing ```, the block is not extracted."""
        text = '```json\n{"unclosed": true}\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 0

    def test_non_json_code_block_ignored(self):
        """A ```python block should not be captured."""
        text = '```python\nprint("hi")\n```\n```json\n{"ok": 1}\n```\n'
        blocks = extract_json_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"ok": 1}

    def test_three_json_blocks(self):
        b1 = '```json\n{"a": 1}\n```'
        b2 = '```json\n{"b": 2}\n```'
        b3 = '```json\n{"c": 3}\n```'
        text = f"First:\n{b1}\nSecond:\n{b2}\nThird:\n{b3}\n"
        blocks = extract_json_blocks(text)
        assert len(blocks) == 3
        assert [b["a"] if "a" in b else b.get("b", b.get("c")) for b in blocks] == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════════════════
# MIDI Note Schema Validation
# ═══════════════════════════════════════════════════════════════════════════

class TestMidiSchemaValidation:

    def test_valid_midi_block(self, sample_midi_json_block):
        errors = validate_midi_block(sample_midi_json_block)
        assert errors == []

    def test_missing_midi_notes_key(self):
        assert "Missing 'midi_notes' key" in validate_midi_block({"notes": []})

    def test_midi_notes_not_list(self):
        assert "'midi_notes' must be a list" in validate_midi_block({"midi_notes": "oops"})

    def test_pitch_too_high(self):
        note = {"pitch": 200, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.5}
        errors = validate_midi_note(note)
        assert any("pitch must be 0-127" in e for e in errors)

    def test_pitch_too_low(self):
        note = {"pitch": -1, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.5}
        errors = validate_midi_note(note)
        assert any("pitch must be 0-127" in e for e in errors)

    def test_pitch_boundary_low(self):
        note = {"pitch": 0, "velocity": 64, "start_beat": 0.0, "duration_beats": 0.25}
        assert validate_midi_note(note) == []

    def test_pitch_boundary_high(self):
        note = {"pitch": 127, "velocity": 64, "start_beat": 0.0, "duration_beats": 0.25}
        assert validate_midi_note(note) == []

    def test_velocity_too_high(self):
        note = {"pitch": 60, "velocity": 128, "start_beat": 0.0, "duration_beats": 0.5}
        errors = validate_midi_note(note)
        assert any("velocity must be 0-127" in e for e in errors)

    def test_velocity_too_low(self):
        note = {"pitch": 60, "velocity": -5, "start_beat": 0.0, "duration_beats": 0.5}
        errors = validate_midi_note(note)
        assert any("velocity must be 0-127" in e for e in errors)

    def test_velocity_boundary_zero(self):
        note = {"pitch": 60, "velocity": 0, "start_beat": 0.0, "duration_beats": 0.25}
        assert validate_midi_note(note) == []

    def test_velocity_boundary_max(self):
        note = {"pitch": 60, "velocity": 127, "start_beat": 0.0, "duration_beats": 0.25}
        assert validate_midi_note(note) == []

    def test_start_beat_negative(self):
        note = {"pitch": 60, "velocity": 100, "start_beat": -0.5, "duration_beats": 0.5}
        errors = validate_midi_note(note)
        assert any("start_beat must be >= 0" in e for e in errors)

    def test_start_beat_zero(self):
        note = {"pitch": 60, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.5}
        assert validate_midi_note(note) == []

    def test_duration_zero(self):
        note = {"pitch": 60, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.0}
        errors = validate_midi_note(note)
        assert any("duration_beats must be > 0" in e for e in errors)

    def test_duration_negative(self):
        note = {"pitch": 60, "velocity": 100, "start_beat": 0.0, "duration_beats": -1.0}
        errors = validate_midi_note(note)
        assert any("duration_beats must be > 0" in e for e in errors)

    def test_missing_key_in_note(self):
        note = {"pitch": 60, "velocity": 100}  # missing start_beat, duration_beats
        errors = validate_midi_note(note)
        assert any("Missing keys" in e for e in errors)

    def test_validate_full_block_with_bad_notes(self):
        block = {
            "midi_notes": [
                {"pitch": 60, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.5},
                {"pitch": 200, "velocity": -1, "start_beat": -1.0, "duration_beats": 0.0},
            ]
        }
        errors = validate_midi_block(block)
        # note[1] should have 4 errors
        assert len(errors) == 4
        assert all(e.startswith("note[1]") for e in errors)

    def test_extracted_midi_block_validates(self, sample_response_with_json):
        """End-to-end: extract from LLM response, then validate."""
        blocks = extract_json_blocks(sample_response_with_json)
        midi_blocks = [b for b in blocks if "midi_notes" in b]
        assert len(midi_blocks) >= 1
        errors = validate_midi_block(midi_blocks[0])
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════════
# Param Schema Validation
# ═══════════════════════════════════════════════════════════════════════════

class TestParamSchemaValidation:

    def test_valid_param_block(self, sample_param_json_block):
        errors = validate_param_block(sample_param_json_block)
        assert errors == []

    def test_missing_params_key(self):
        errors = validate_param_block({"changes": []})
        assert "Missing 'params' key" in errors

    def test_params_not_list(self):
        errors = validate_param_block({"params": "oops"})
        assert "'params' must be a list" in errors

    def test_missing_key_in_param(self):
        block = {"params": [{"track": 1, "device": "EQ"}]}  # missing param, value
        errors = validate_param_block(block)
        assert any("Missing keys" in e for e in errors)

    def test_device_must_be_string(self):
        param = {"track": 1, "device": 42, "param": "Freq", "value": 0.5}
        errors = validate_param(param)
        assert any("device must be str" in e for e in errors)

    def test_param_must_be_string(self):
        param = {"track": 1, "device": "EQ", "param": 123, "value": 0.5}
        errors = validate_param(param)
        assert any("param must be str" in e for e in errors)

    def test_value_must_be_numeric(self):
        param = {"track": 1, "device": "EQ", "param": "Freq", "value": "high"}
        errors = validate_param(param)
        assert any("value must be numeric" in e for e in errors)

    def test_track_can_be_int(self):
        param = {"track": 1, "device": "EQ", "param": "Freq", "value": 0.5}
        errors = validate_param(param)
        assert errors == []

    def test_track_can_be_string(self):
        param = {"track": "2-Bass", "device": "Saturator", "param": "Drive", "value": 0.3}
        errors = validate_param(param)
        assert errors == []

    def test_value_can_be_negative(self):
        param = {"track": 1, "device": "Utility", "param": "Gain", "value": -6.0}
        errors = validate_param(param)
        assert errors == []

    def test_extracted_param_block_validates(self, sample_response_with_json):
        """End-to-end: extract from LLM response, then validate."""
        blocks = extract_json_blocks(sample_response_with_json)
        param_blocks = [b for b in blocks if "params" in b]
        assert len(param_blocks) >= 1
        errors = validate_param_block(param_blocks[0])
        assert errors == []

    def test_multiple_params_in_block(self):
        block = {
            "params": [
                {"track": 1, "device": "Wavetable", "param": "Osc1 Pos", "value": 0.3},
                {"track": 1, "device": "Wavetable", "param": "Filter Freq", "value": 0.7},
                {"track": 2, "device": "Reverb", "param": "Decay", "value": 4.5},
            ]
        }
        errors = validate_param_block(block)
        assert errors == []
