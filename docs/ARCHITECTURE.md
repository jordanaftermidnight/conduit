# Conduit -- Architecture & Developer Guide

This document covers the internals of Conduit: how the pieces fit together, what each file does, and how to extend or modify the system. If you want to hack on the codebase, add a new LLM provider, create a genre module, or just understand why a particular design decision was made, this is the place.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Server Components](#server-components)
  - [main.py -- FastAPI Application](#mainpy----fastapi-application)
  - [providers.py -- LLM Provider Abstraction](#providerspy----llm-provider-abstraction)
  - [autodetect.py -- System Detection](#autodetectpy----system-detection)
  - [prompts.py -- System Prompt Builder](#promptspy----system-prompt-builder)
  - [schemas.py -- Pydantic Models & JSON Schema](#schemaspy----pydantic-models--json-schema)
  - [genres/ -- YAML Genre Modules](#genres----yaml-genre-modules)
- [Max for Live Device](#max-for-live-device)
  - [build-device.py -- Patcher Generator](#build-devicepy----patcher-generator)
  - [conduit-bridge.js -- Node.js Bridge](#conduit-bridgejs----nodejs-bridge)
  - [session-context.js -- LOM Poller](#session-contextjs----lom-poller)
  - [midi-applicator.js -- Clip Writer](#midi-applicatorjs----clip-writer)
  - [param-applicator.js -- Parameter Writer](#param-applicatorjs----parameter-writer)
- [Patcher Signal Flow](#patcher-signal-flow)
- [AMPF Binary Format](#ampf-binary-format)
- [API Reference](#api-reference)
- [Installation Layout](#installation-layout)
- [Adding Custom Providers](#adding-custom-providers)
- [Adding Custom Genres](#adding-custom-genres)
- [Development](#development)

---

## System Architecture

```
+-----------------------+     HTTP localhost:9321      +---------------------+
|   Ableton Live        | <---------------------------> |  Conduit Server     |
|                       |                               |  (FastAPI/Python)   |
|  +----------------+   |   POST /ask {prompt,session}  |                     |
|  |  M4L Device    |---|------------------------------>|  +--------------+   |
|  |  (node.script) |   |                               |  | Provider     |   |
|  |                |<--|-------------------------------|  | Registry +   |   |
|  +----------------+   |   {text, json_blocks}         |  | Circuit      |   |
|                       |                               |  | Breaker      |   |
|  Session Context ----|   BPM, key, tracks (5s poll)   |  +--------------+   |
|  (LOM poller)         |                               |        |            |
+-----------------------+                               |  +--------------+   |
                                                        |  | Ollama       |   |
                                                        |  | (llama3.2)   |   |
                                                        |  +--------------+   |
                                                        +---------------------+
```

The system is two halves connected by HTTP over localhost:

**Ableton side.** A Max for Live MIDI effect device containing a `[node.script]` object (Node.js runtime), three `[js]` objects (Max's built-in JavaScript), and standard Max patcher logic. The device captures user prompts, polls Live session state via the Live Object Model (LOM), and writes generated MIDI data back into clips.

**Server side.** A FastAPI application (`server/main.py`) on port 9321. Receives prompts from the M4L device, enriches them with session context and genre knowledge, routes them through an LLM provider (local Ollama by default, or Anthropic/OpenAI/any OpenAI-compatible endpoint), parses the response, validates MIDI data, and returns structured JSON.

The bridge is intentionally HTTP-on-localhost. No WebSockets, no gRPC, no message queues. The M4L `[node.script]` object has Node's `http` module available. HTTP keeps the protocol dead simple, debuggable with curl, and eliminates connection state issues when Ableton reloads the device.

---

## Server Components

All server code lives in `server/`.

### main.py -- FastAPI Application

**File:** `/server/main.py`

The main application module. Starts a FastAPI server on `127.0.0.1:9321` with CORS wide open (localhost only, so this is fine).

**Lifespan.** On startup, the app runs system detection (`autodetect.system_report()`), builds the provider registry (`build_default_registry()`), loads genre modules, and sends a warmup request to the active model. Ollama lazy-loads models on first inference, which adds significant cold-start latency -- the warmup call absorbs that delay at startup rather than on the user's first prompt.

**Two operating modes:**

| Mode | Purpose | History | Output | Token Budget |
|------|---------|---------|--------|--------------|
| `chat` | Conversational -- advice, explanations, mixed text+JSON | Full conversation history (max 40 messages) | Free-form text, optionally with ` ```json ` fenced blocks | 4096 tokens |
| `generate` | Single-shot MIDI generation | Single message (no history) | Raw JSON, schema-constrained via Ollama's `format` parameter | 1000-3200 tokens (scaled by prompt analysis) |

**Token estimation** (`estimate_generate_tokens`). Scans the user's prompt for patterns like "16 notes", "4-bar drum", "32 steps" and scales the token budget accordingly. Each MIDI note in JSON costs roughly 55 tokens. The budget is clamped to `[1000, 3200]`. This matters because Ollama's `num_predict` directly limits output length, and under-budgeting truncates the JSON mid-array.

**JSON repair pipeline** (`parse_generate_response`). LLMs, especially small local models, produce almost-valid JSON with predictable failure modes. The repair pipeline handles them in order:

1. Strip markdown fences (` ```json ... ``` `)
2. Skip text preamble before the first `{` or `[`
3. Normalize common quirks: single quotes to double quotes, trailing commas, wrong key names (`"notes"` -> `"midi_notes"`, `"drumbeats"` -> `"drum_notes"`)
4. Try `json.loads()`
5. If that fails, attempt truncation repair: find the last complete `}`, close any open brackets/braces, and retry parse
6. Fall back to fenced-block extraction from the original text

**Note validation** (`validate_and_fix_notes`). Clamps values to valid MIDI ranges:
- `pitch`: 0-127 (catches negative pitches from confused models)
- `velocity`: 1-127 (zero velocity is note-off in MIDI)
- `start_beat`: >= 0
- `duration_beats`: >= 0.0625 (1/64 note minimum)
- CC messages: `cc_number` 0-127, `value` 0-127

**Pattern extension** (`_extend_pattern`). If the model produces a valid but short pattern (e.g., 4 notes when 16 were requested), the extension logic tiles the pattern by shifting `start_beat` values forward by the pattern length (rounded up to the nearest bar). This handles the common case where a small model generates one bar correctly but runs out of tokens before completing the requested length.

**Pattern bank.** Generated patterns are auto-saved to an in-memory clipboard (max 20 entries, FIFO eviction). Each entry stores the prompt, genre, model, timestamp, and full JSON blocks. Patterns can be recalled by ID or "latest" for re-pasting into different clips.

**Conversation history.** Chat mode maintains a rolling window of 40 messages. Generate mode sends only the current message (no history) because MIDI JSON generation doesn't benefit from conversational context and the token budget is tight.

### providers.py -- LLM Provider Abstraction

**File:** `/server/providers.py`

Implements the provider abstraction layer with four concrete providers and a registry with circuit breaker integration.

**BaseProvider interface:**

```python
class BaseProvider(ABC):
    name: str = "base"

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse: ...
    def is_available(self) -> bool: ...
```

Every provider takes a system prompt, a message list, and keyword arguments, and returns a `ProviderResponse` (text, model name, token counts, provider name). This uniformity lets the registry swap providers transparently.

**Concrete providers:**

| Provider | Class | Auth | Notes |
|----------|-------|------|-------|
| Ollama | `OllamaProvider` | None (local) | Native REST API, supports `format` (JSON schema constraint), `repeat_penalty`, `top_p`, `top_k`, `temperature`. Disables Qwen3 thinking mode (`think: false`). Strips `<think>` leakage. Default `num_ctx=1024`. |
| Anthropic | `AnthropicProvider` | `ANTHROPIC_API_KEY` env var | Uses the `anthropic` SDK. Default model: `claude-sonnet-4-20250514`. |
| OpenAI | `OpenAIProvider` | `OPENAI_API_KEY` env var | Uses the `openai` SDK. Default model: `gpt-4o`. |
| OpenAI-compatible | `OpenAICompatibleProvider` | Optional API key | Works with any `/v1/chat/completions` endpoint: llama.cpp, LM Studio, vLLM, text-generation-webui. Availability check hits `/v1/models`. |

**Ollama-specific: JSON schema constraint.** When `json_schema` is passed in kwargs, the `OllamaProvider` sets it as Ollama's `format` parameter. This enables grammar-constrained decoding at the inference level -- the model literally cannot produce tokens that violate the schema. The schema used for MIDI generation is defined in `main.py` as `MIDI_FORMAT_SCHEMA`, which constrains `pitch` to 0-127, `velocity` to 1-127, `start_beat` >= 0, and `duration_beats` >= 0.0625.

**Dedicated generate provider.** The registry creates a separate `ollama_generate` provider instance optimized for raw JSON output. It may use a different model than the chat provider (selected by `find_best_generate_model()` in autodetect). This provider gets its own circuit breaker state and can use a different `num_ctx` (2048 vs 1024).

**Circuit breaker** (`CircuitBreaker`). Minimal implementation ported from an IRIS failover pattern. Per-provider health tracking with three states:

```
closed (healthy) --[N consecutive failures]--> open (tripped)
open (tripped) --[recovery_seconds elapsed]--> half_open (testing)
half_open --[success]--> closed
half_open --[failure]--> open
```

Defaults: `failure_threshold=3`, `recovery_seconds=60`. Health scoring is 0-100, penalizing error rate (60% weight) and slow responses (every 1s average = -10 points, capped at -40). Response times are tracked in a sliding window of 20 samples.

**ProviderRegistry.** Manages the set of registered providers, the active one, and the fallback order. `chat_with_failover()` tries the active provider first, then iterates the fallback list, skipping any provider whose circuit is open. If a failover succeeds, the response's `provider` field is tagged `"(failover)"`.

**Default registry construction** (`build_default_registry`):
1. Register Anthropic if `ANTHROPIC_API_KEY` is set (set as active)
2. Register OpenAI if `OPENAI_API_KEY` is set
3. Auto-detect the best Ollama model for the system's RAM
4. Register a dedicated `ollama_generate` instance if a different generate-optimized model is available
5. Register LM Studio at `localhost:1234` (available only if LM Studio is running)
6. If no API keys are set, fall back to Ollama as the active provider

### autodetect.py -- System Detection

**File:** `/server/autodetect.py`

Detects the host system's hardware and recommends an appropriate model tier.

**System detection:**
- Reads total RAM via `sysctl -n hw.memsize`
- Detects Apple Silicon chip via `sysctl -n machdep.cpu.brand_string`
- Estimates available LLM memory: `total_ram - 3GB (OS) - 3GB (Ableton)`

**Model selection.** The `MODEL_TIERS` list ranks models by RAM requirement. Currently simplified to a single tier: `llama3.2:latest` (Q4_K_M quantization, ~2.5GB RAM). This model was selected through comprehensive benchmarking on Apple M4 16GB: 100% pass rate for MIDI generation, ~10s response times, clean output with no thinking-mode leakage. Other models tested (Qwen3, Gemma, Phi4) had issues with thinking mode leaks, excessive latency, or musically unusable output.

**Model auto-detection.** `find_best_available_model()` queries Ollama's `/api/tags` endpoint, cross-references pulled models against the tier list, and returns the best match. `find_best_generate_model()` does the same for the dedicated MIDI generation provider, using a separate ranking list (`GENERATE_MODELS_RANKED`).

**System report.** `system_report()` returns a dict with chip info, RAM breakdown, recommended model, Ollama status, pulled models, and a `pull_command` if the recommended model isn't available yet.

### prompts.py -- System Prompt Builder

**File:** `/server/prompts.py`

Constructs system prompts from composable parts: base prompt, genre module, and response format rules.

**Chat mode prompt structure:**
```
BASE_PROMPT (role definition, capabilities)
+ GENRE_CONTEXT (if genre is set -- full detail)
+ RESPONSE_FORMAT_CHAT (JSON schema rules, velocity dynamics, scale reference)
```

**Generate mode prompt structure:**
```
GENERATE_SYSTEM_PROMPT (compact, fast -- raw JSON only rules)
+ genre brief (BPM + scales only, if genre is set)
```

The generate prompt is deliberately minimal. Local models have limited context windows, and every token of system prompt is a token not available for output. The compact prompt packs the essential rules -- pitch ranges, velocity dynamics, note count enforcement, drum map pitches, scale intervals -- into a dense block.

**Genre loading.** Genre YAML files are loaded from `server/genres/`. The loader tries PyYAML first, and falls back to a minimal built-in parser that handles the subset of YAML used by genre modules (key-value pairs, lists, multi-line strings). Results are cached in `_genre_cache`.

**Two genre injection modes:**
- **Chat mode:** Full genre section with BPM range, scales, key tendencies, rhythm style, bass style, drum patterns, structure, dynamics, effects, reference artists, subgenres.
- **Generate mode:** Brief genre hint with only name, BPM range, scales, and key tendencies. Keeps the system prompt small for fast local inference.

### schemas.py -- Pydantic Models & JSON Schema

**File:** `/server/schemas.py`

Defines Pydantic v2 models for structured MIDI and parameter output. Serves three purposes:

1. **Validation.** `validate_midi_notes()` and `validate_cc_messages()` take raw dicts from LLM output and return typed, validated objects (or raise `ValidationError`).

2. **JSON schema generation.** `get_midi_json_schema()` returns the JSON schema for `MIDIPattern`, suitable for passing directly to Ollama's `format` parameter for grammar-constrained decoding.

3. **Type safety.** Models are used throughout the server for type hints and documentation.

**Core models:**

```python
MIDINote:     pitch (0-127), velocity (1-127), start_beat (>=0), duration_beats (>0), is_drum (bool)
CCMessage:    cc_number (0-127), value (0-127), beat (>=0)
MIDIPattern:  midi_notes, drum_notes?, cc_messages?, swing? (0-100), quantize?
ParamChange:  track (int|str), device (str), param (str), value (float)
```

Note: `main.py` also defines `MIDI_FORMAT_SCHEMA` as a plain dict for Ollama's format parameter. This is a simpler schema than the full Pydantic-generated one, and is used directly for generate-mode requests because Ollama's grammar constraint engine works best with flat, minimal schemas.

### genres/ -- YAML Genre Modules

**Directory:** `/server/genres/`

Eight genre modules: `techno`, `house`, `dnb`, `dubstep`, `hiphop`, `ambient`, `idm`, `trance`.

Each YAML file defines a comprehensive genre knowledge base that gets injected into the LLM's system prompt. This gives the model genre-specific context without fine-tuning.

**Fields in a genre module:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `bpm_range` | [int, int] | Typical tempo range |
| `time_signatures` | list | Common time signatures |
| `scales` | list | Characteristic scales |
| `key_tendencies` | list | Common keys |
| `rhythm_style` | string | Prose description of rhythmic approach |
| `swing` | float | Typical swing amount (0.0 = straight) |
| `instrument_conventions` | dict | Nested dict of instrument categories and their conventions |
| `bass_style` | string | Prose description of bass approach |
| `drum_patterns` | dict | Named drum pattern descriptions (kick, hihat, clap_snare, percussion) |
| `structure` | dict | Phrase/section lengths, arrangement structure, arrangement notes |
| `dynamics` | dict | Dynamic range, velocity range, compression style |
| `effects` | list | Common effects and processing |
| `reference_artists` | list | Artist references for style calibration |
| `subgenres` | list | Related subgenres |

Example (truncated, from `techno.yaml`):

```yaml
name: Techno
bpm_range: [125, 140]
scales:
  - natural minor
  - phrygian
  - locrian
swing: 0.0
drum_patterns:
  kick: >
    Four-on-the-floor (hits on every quarter note). Variations include
    removing the kick on beat 1 of breakdown sections...
reference_artists:
  - Surgeon
  - Blawan
  - Paula Temple
```

---

## Max for Live Device

All M4L code lives in `m4l/`.

### build-device.py -- Patcher Generator

**File:** `/m4l/build-device.py`

Programmatically generates the Max for Live device. Outputs two files:

- **`Conduit.maxpat`** -- Plain JSON patcher file, editable in Max's patcher editor.
- **`Conduit.amxd`** -- AMPF binary container wrapping the JSON patcher plus embedded `[js]` files, loadable by Ableton Live.

The patcher is built entirely in Python rather than edited in Max's GUI. This makes the device reproducible, diffable, and scriptable. The `build_patcher()` function creates box dicts, assigns IDs, sets patching rectangles, defines presentation-mode layout, and wires everything together with patchlines.

**Layout.** The device view is 600x170px in presentation mode:
- Title bar with "CONDUIT" label, mode menu (`chat`/`generate`), genre menu
- Text input field (full width)
- Response display area (read-only, multi-line)
- Button bar: Reset, Generate, Paste, Undo, Clear + status text

**Presentation objects** use `live.menu` for mode/genre selection and `live.text` for buttons. These are M4L-specific objects that survive Ableton's parameter save/recall system.

**AMPF wrapping.** The `wrap_ampf()` function creates the binary container:
1. Builds the 32-byte AMPF header (magic, version, device type, meta)
2. Creates the `mx@c` context block (16 bytes) with content size pointer
3. Embeds the JSON patcher data
4. Embeds `[js]` file resources (session-context.js, midi-applicator.js, param-applicator.js) -- these get extracted by Max at load time
5. Builds the `dlst` resource directory with `dire` entries for each file
6. Assembles the final binary: header + mx@c + content + dlst remainder

Note: `conduit-bridge.js` is NOT embedded in the AMPF. `[node.script]` requires its JavaScript file to be on disk (not extracted from an AMPF container). It's installed to the Max Packages search path by `package-device.sh`.

### conduit-bridge.js -- Node.js Bridge

**File:** `/m4l/conduit-bridge.js`

Runs inside Max's `[node.script]` object, which provides a Node.js runtime. This is the communication hub between the Max patcher and the Conduit server.

**HTTP client.** Uses Node's built-in `http` module. All requests go to `127.0.0.1:9321`. Timeout is 180 seconds (LLM inference can be slow on constrained hardware).

**Message protocol.** All outbound messages go through `Max.outlet()` on outlet 0 with a type prefix:

| Prefix | Content | Destination in patcher |
|--------|---------|----------------------|
| `text` | LLM response text or summary | Response textedit display |
| `midi` | JSON string of notes + metadata | `midi-applicator.js` |
| `params` | JSON string of param changes or CC messages | `param-applicator.js` |
| `status` | Status string | Status bar textedit |
| `undo` | Undo command | `midi-applicator.js` |

**Inbound handlers:**

| Handler | Trigger | Action |
|---------|---------|--------|
| `prompt` | User presses Enter in textedit (chat mode) | POST `/ask` with mode=chat |
| `generate` | Generate button or Enter in generate mode | POST `/ask` with mode=generate |
| `session` | 5-second metro tick | Parses JSON, stores as `sessionContext` |
| `cmd` | Button presses and menu changes | Routes to appropriate endpoint |
| `bang` | Manual reconnect | Health check |

**Command routing** (`cmd` handler):

| Command | Action |
|---------|--------|
| `reset` | POST `/reset` -- clears conversation history |
| `health` | GET `/health` -- shows provider/model/health score |
| `mode` | Switches `currentMode` between "chat" and "generate" |
| `genre` | POST `/genres/set` -- sets active genre |
| `system` | GET `/system` -- shows chip, RAM, model info |
| `provider` | POST `/providers/switch` -- switches active provider |
| `model` | POST `/providers/switch` with model override |
| `paste` | GET `/patterns/latest` or `/patterns/{id}` -- re-pastes a saved pattern |
| `patterns` | GET `/patterns` -- lists saved patterns |
| `clear` | DELETE `/patterns` -- clears pattern clipboard |
| `undo` | Emits `undo` message to midi-applicator |

**Server auto-launch.** On startup, the bridge does a health check. If the server is unreachable, it attempts to auto-launch `server/main.py` via `child_process.spawn("python3", ...)`, searching several relative paths for the script. It then polls health every 2 seconds for up to 5 attempts.

**Response handling** (`handleAskResponse`). Processes the server's `BridgeResponse`:
1. Iterates `json_blocks`, extracting `midi_notes`, `drum_notes`, `cc_messages`, and `params`
2. Attaches swing/quantize metadata to MIDI payloads
3. Emits `midi` messages for note data (one per block, separate for drum vs melodic)
4. Emits `params` messages for CC and parameter changes
5. For generate mode with MIDI data, shows a human-readable summary (note count, pitch range, bar count, pattern ID) instead of raw JSON

### session-context.js -- LOM Poller

**File:** `/m4l/session-context.js`

Runs inside a `[js]` object (Max's built-in JavaScript, not Node.js). Queries Ableton Live's session state via the Live Object Model (LOM) and outputs a JSON string.

**Polled every 5 seconds** by a `[metro 5000]` connected through the startup gate.

**Queries:**

| Property | LOM Path | Description |
|----------|----------|-------------|
| `bpm` | `live_set.tempo` | Current tempo |
| `time_signature` | `live_set.signature_numerator/denominator` | Time signature |
| `playing` | `live_set.is_playing` | Transport state |
| `song_time` | `live_set.current_song_time` | Playhead position in seconds |
| `track_names` | `live_set.tracks[*].name` | All track names |
| `selected_track` | `live_set view selected_track.name` | Currently selected track |
| `groove` | `live_set.groove_amount` | Global groove amount |

Each query is wrapped in try/catch -- if a property isn't available (e.g., no tracks exist), it's silently omitted. The output is a single JSON string sent to outlet 0.

Important: the script does NOT use `loadbang`. The Live API isn't ready when `[js]` objects first load. Instead, the patcher uses `[live.thisdevice]` -> `[delay 2000]` -> `[metro 5000]` to start polling only after the API is initialized.

### midi-applicator.js -- Clip Writer

**File:** `/m4l/midi-applicator.js`

Runs inside a `[js]` object. Receives MIDI note data from the bridge and writes it into Ableton Live clips via the LOM.

**Input format.** Accepts JSON in several shapes:
- Object with `notes` array: `{ notes: [...], swing: N, quantize: "1/16", cc_messages: [...], is_drum: bool }`
- Object with `midi_notes` array (legacy)
- Raw array of note objects

**Clip slot resolution strategy** (in priority order):

1. **First empty clip slot** on the selected track. Creates a new clip with the calculated length.
2. **Currently selected clip** (`detail_clip`). Replaces existing notes. Extends clip length if needed.
3. **Highlighted clip slot**. If it has a clip, replaces notes. If empty, creates a new clip.
4. If all strategies fail, reports an error.

**Quantization.** If the payload includes a `quantize` field (e.g., `"1/16"`, `"1/8t"`), notes are snapped to the nearest grid point. Triplet grids are supported: the `"t"` suffix divides the normal grid by 3/2 (e.g., `1/8t` = 0.333 beats).

**Swing.** If the payload includes a `swing` field (0-100), every other grid position (odd grid indices) is delayed. The delay amount is `(swingAmount / 100) * gridSize * 0.5`. If no quantize grid is specified, defaults to 1/16 (0.25 beats).

**Note writing.** Uses the LOM clip API:
1. Disables looping
2. Clears existing notes via `select_all_notes` + `replace_selected_notes` + `notes 0` + `done`
3. Forces a LOM round-trip (`clipApi.get("length")`) as an operational pause
4. Writes new notes via `set_notes` + `notes N` + `note pitch start dur vel 0` for each note + `done`
5. Re-enables looping, sets loop boundaries

**Undo.** Stores the last write action (`lastAction`) with type (`"created"` or `"replaced"`), slot/clip path, and track name. The `undo()` function either deletes the created clip or clears notes from the replaced clip.

### param-applicator.js -- Parameter Writer

**File:** `/m4l/param-applicator.js`

Runs inside a `[js]` object. Receives parameter change suggestions from the bridge and applies them to Ableton devices via the LOM.

**Input format:**
- Object with `params` array: `{ params: [{ track, device, param, value }, ...] }`
- Raw array of param change objects

**Resolution chain** (all case-insensitive name matching):
1. **Track** -- by index (integer) or name (string search across all tracks)
2. **Device** -- by name (search devices on the resolved track)
3. **Parameter** -- by name (search parameters on the resolved device)

**Value scaling.** If the value is in [0.0, 1.0] and the parameter's actual range is NOT [0.0, 1.0], the value is treated as normalized and scaled: `actual = min + value * (max - min)`. Otherwise, the value is used as-is but clamped to the parameter's range. This lets the LLM suggest "0.75 filter cutoff" without knowing the actual range of every parameter.

---

## Patcher Signal Flow

The M4L patcher has two main signal paths: the prompt path and the session context path.

### Prompt Path

```
[textedit] ---> [gate 2 1] --outlet 0--> [prepend prompt] ---> [node.script]
                    |
                    +--outlet 1--> [prepend generate] ---> [node.script]

[Generate button] --> [t 1 b 2]
                        |  |  |
                        |  |  +--> int 2 --> gate control (switch to generate)
                        |  +-----> bang  --> textedit (triggers content output)
                        +--------> int 1 --> gate control (reset to prompt)
```

The `[gate 2 1]` routes textedit output between the prompt path (outlet 0, default) and the generate path (outlet 1). When the Generate button is pressed, the `[t 1 b 2]` trigger fires right-to-left:
1. `int 2` switches the gate to outlet 1 (generate path)
2. `bang` triggers the textedit to output its contents
3. `int 1` resets the gate back to outlet 0 (prompt path)

This ensures the textedit content goes through `[prepend generate]` exactly once, then the gate resets for normal prompt operation.

### Response Path

```
[node.script] outlet 0
       |
       v
[route text midi params status undo]
   |      |       |        |       |
   v      v       v        v       v
 text   midi    params   status   undo
   |      |       |        |       |
   v      v       v        v       v
[prepend  [js     [js    [prepend  [js
  set]   midi-   param-   set]    midi-
   |     appli-  appli-    |      appli-
   v     cator]  cator]    v      cator]
[response                [status
 textedit]               textedit]
```

### Session Context Path

```
[live.thisdevice] --> [delay 2000] --> [t 1 1]
                                         |   |
                                         |   +--> [metro 5000]
                                         |             |
                                         |             v
                                         |        [js session-context.js]
                                         |             |
                                         |             v
                                         |        [tosymbol]
                                         |             |
                                         |             v
                                         |        [prepend session]
                                         |             |
                                         v             v
                                       [gate 1] <---- data
                                         |
                                         v
                                    [node.script]
```

The startup gate (`[gate 1]`) blocks all control messages (cmd, session, mode, genre) for 2 seconds while `[node.script]` initializes. After the delay:
- `[t 1 1]` outlet 0 opens the gate (left inlet = control)
- `[t 1 1]` outlet 1 starts the metro (begins session polling)

Prompt messages bypass the gate entirely -- they go straight to `[node.script]` through the prompt/generate gate. The user won't realistically type a prompt in under 2 seconds.

Mode menu changes, genre menu changes, button commands (reset, paste, undo, clear), and session context updates all flow through the startup gate to the node.script.

---

## AMPF Binary Format

The `.amxd` file uses Ableton's AMPF (Ableton Max Patcher Format) binary container. This wraps the JSON patcher with a binary header and an embedded resource directory.

### Header (32 bytes)

```
Offset  Size  Description
------  ----  -----------
0x00    4     Magic: "ampf" (ASCII)
0x04    4     Version: 4 (little-endian uint32)
0x08    4     Device type chunk: "mmmm" (MIDI), "iiii" (instrument), or "aaaa" (audio)
0x0C    4     "meta" (ASCII)
0x10    4     Meta payload size: 4 (little-endian uint32)
0x14    4     Meta value: 7 (little-endian uint32)
0x18    4     "ptch" (ASCII)
0x1C    4     Patch payload size (little-endian uint32)
```

### Patch Payload

```
Offset       Size    Description
------       ----    -----------
0x20         16      mx@c context block:
                       "mx@c" (4B) + header size 0x10 (BE uint32)
                       + reserved 0 (BE uint32) + content size (BE uint32)
0x30         var     JSON patcher data
0x30+json    var     Embedded resource data ([js] file contents, concatenated)
             16      dlst header + first dire header (inside content boundary)
             var     dire payloads (outside content boundary)
```

Note the split: the first 16 bytes of the `dlst` + first `dire` sit inside the content boundary (counted in the `mx@c` content size), while the rest of the directory entries sit after. This matches the byte layout of real .amxd files produced by Ableton.

### dlst Directory

The `dlst` (directory list) section uses `dire` (directory entry) chunks, each containing sub-chunks:

| Sub-chunk | Size | Description |
|-----------|------|-------------|
| `type` | 12B | File type: "JSON" for patcher, "TEXT" for scripts |
| `fnam` | 8+N | Filename (null-terminated, padded to 4-byte alignment) |
| `sz32` | 12B | File size (big-endian uint32) |
| `of32` | 12B | Offset from patch payload start (big-endian uint32) |
| `vers` | 12B | Version (always 0) |
| `flag` | 12B | Flags (0x11 for main patcher, 0x00 for resources) |
| `mdat` | 12B | HFS+ timestamp (seconds since January 1, 1904, big-endian uint32) |

The main patcher JSON entry has `of32 = 0x10` (after the mx@c block). Embedded resources have offsets calculated as `0x10 + json_size + sum_of_preceding_resources`.

---

## API Reference

All endpoints are on `http://127.0.0.1:9321`.

### POST /ask

Main endpoint. Sends a prompt to the active LLM provider.

**Request body (`BridgeRequest`):**

```json
{
  "prompt": "Give me a 4-bar techno kick pattern",
  "mode": "generate",
  "genre": "techno",
  "session": {
    "bpm": 138.0,
    "time_signature": "4/4",
    "key": "C minor",
    "selected_track": "1-Drums",
    "track_names": ["1-Drums", "2-Bass", "3-Pad"],
    "playing": false,
    "song_time": 0.0,
    "groove": 0.0
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | User's message (min 1 char) |
| `mode` | `"chat"` or `"generate"` | no | Default: `"chat"` |
| `genre` | string or null | no | Genre override for this request |
| `session` | object or null | no | Live session context |

**Response body (`BridgeResponse`):**

```json
{
  "text": "{\"midi_notes\": [{\"pitch\": 36, \"velocity\": 110, ...}]}",
  "json_blocks": [
    {
      "midi_notes": [
        {"pitch": 36, "velocity": 110, "start_beat": 0.0, "duration_beats": 0.25},
        {"pitch": 36, "velocity": 110, "start_beat": 1.0, "duration_beats": 0.25}
      ]
    }
  ],
  "timestamp": "2025-12-15T10:30:00.000000",
  "model": "llama3.2:latest",
  "provider": "ollama",
  "pattern_id": 1
}
```

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Raw LLM response text |
| `json_blocks` | list[dict] | Parsed and validated JSON blocks |
| `timestamp` | string | ISO 8601 timestamp |
| `model` | string | Model that generated the response |
| `provider` | string | Provider name (may include "(failover)") |
| `pattern_id` | int or null | Pattern clipboard ID (generate mode only) |

**Error responses:** 502 (all providers failed), 503 (no provider configured).

### GET /health

Server health and active provider status.

```json
{
  "status": "ok",
  "active_provider": "ollama",
  "active_model": "llama3.2:latest",
  "genre": "techno",
  "circuit_state": "closed",
  "health_score": 95,
  "avg_response_ms": 850.3,
  "timestamp": "2025-12-15T10:30:00.000000"
}
```

### GET /patterns

List saved patterns (most recent first).

```json
{
  "patterns": [
    {"id": 3, "timestamp": "...", "prompt": "8-note bass...", "genre": "techno", "note_count": 8},
    {"id": 2, "timestamp": "...", "prompt": "kick pattern...", "genre": null, "note_count": 16}
  ],
  "count": 2
}
```

### GET /patterns/latest

Get the most recently saved pattern with full JSON block data.

```json
{
  "id": 3,
  "timestamp": "...",
  "prompt": "8-note bass line in C minor",
  "genre": "techno",
  "model": "llama3.2:latest",
  "note_count": 8,
  "json_blocks": [{"midi_notes": [...]}]
}
```

### GET /patterns/{pattern_id}

Get a specific pattern by ID. Returns 404 if not found.

### DELETE /patterns

Clear all saved patterns and reset the ID counter.

```json
{"status": "cleared"}
```

### GET /genres

List available genre modules and the currently active genre.

```json
{
  "genres": ["ambient", "dnb", "dubstep", "hiphop", "house", "idm", "techno", "trance"],
  "active": "techno"
}
```

### GET /genres/{genre_name}

Get full genre module data. Returns the parsed YAML as JSON.

### POST /genres/set

Set the active genre for all subsequent requests.

**Request:**
```json
{"genre": "dnb"}
```

Set to `null` to clear the genre (genre-agnostic mode):
```json
{"genre": null}
```

### GET /providers

List all registered providers with health status.

```json
{
  "providers": [
    {
      "name": "ollama",
      "model": "llama3.2:latest",
      "active": true,
      "available": true,
      "state": "closed",
      "health_score": 95,
      "consecutive_failures": 0,
      "avg_response_ms": 850.3,
      "total_success": 12,
      "total_errors": 0
    },
    {
      "name": "lm_studio",
      "model": "local-model",
      "active": false,
      "available": false,
      "state": "closed",
      "health_score": 100,
      "consecutive_failures": 0,
      "avg_response_ms": 0,
      "total_success": 0,
      "total_errors": 0
    }
  ]
}
```

### POST /providers/switch

Switch the active provider and optionally change its model.

**Request:**
```json
{"provider": "anthropic", "model": "claude-haiku-4.5-20250514"}
```

### POST /providers/add

Register a new provider at runtime. See [Adding Custom Providers](#adding-custom-providers).

**Request:**
```json
{
  "name": "my_vllm",
  "type": "openai_compatible",
  "model": "meta-llama/Llama-3.2-8B",
  "base_url": "http://localhost:8000/v1",
  "api_key": "not-needed"
}
```

### GET /providers/health

Circuit breaker state and health scores for all providers.

### POST /providers/reset-circuit/{provider_name}

Manually reset a tripped circuit breaker. Useful if a provider was temporarily down and you want to force a retry without waiting for the recovery timeout.

### GET /providers/ollama/models

List models currently pulled in Ollama.

### POST /warmup

Send a tiny request to the active provider (and `ollama_generate` if registered) to load models into memory. Returns per-provider warmup timing.

```json
{
  "status": "warmed",
  "providers": [
    {"name": "ollama", "model": "llama3.2:latest", "warmup_ms": 3200},
    {"name": "ollama_generate", "model": "llama3.2:latest", "warmup_ms": 150}
  ]
}
```

### POST /reset

Clear conversation history.

```json
{"status": "cleared"}
```

### GET /history

Get the current conversation history.

```json
{"messages": [...], "count": 12}
```

### GET /system

Full system detection report.

### GET /system/ollama/status

Ollama server status and available models.

---

## Installation Layout

### M4L Device

The device is installed to Ableton's User Library for MIDI Effects:

```
~/Documents/User Library/Presets/MIDI Effects/Max MIDI Effect/Conduit/
    Conduit.amxd          # AMPF binary (device file)
    conduit-bridge.js     # node.script source (must be on disk)
```

Or on some systems:

```
~/Music/Ableton/User Library/Presets/MIDI Effects/Max MIDI Effect/Conduit/
```

### node.script Search Path

Max's `[node.script]` object looks for JavaScript files in the Max Packages search path. `conduit-bridge.js` is installed to:

```
~/Documents/Max 8/Packages/Conduit/javascript/conduit-bridge.js
```

And/or for Max 9:

```
~/Documents/Max 9/Packages/Conduit/javascript/conduit-bridge.js
```

### Server

The server runs from the repository checkout. No installation to a system path is required -- just run `python3 main.py` from `server/`.

---

## Adding Custom Providers

You can add any OpenAI-compatible provider at runtime via the API. No server restart needed.

### LM Studio

LM Studio exposes an OpenAI-compatible API on port 1234 by default.

```bash
curl -X POST http://localhost:9321/providers/add \
  -H "Content-Type: application/json" \
  -d '{
    "name": "lm_studio",
    "type": "openai_compatible",
    "model": "your-model-name",
    "base_url": "http://localhost:1234/v1"
  }'
```

Note: LM Studio is already pre-registered in the default registry, but it only becomes `available` when LM Studio is actually running.

### vLLM

```bash
curl -X POST http://localhost:9321/providers/add \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vllm_local",
    "type": "openai_compatible",
    "model": "meta-llama/Llama-3.2-8B-Instruct",
    "base_url": "http://localhost:8000/v1",
    "api_key": "not-needed"
  }'
```

### llama.cpp server

```bash
curl -X POST http://localhost:9321/providers/add \
  -H "Content-Type: application/json" \
  -d '{
    "name": "llamacpp",
    "type": "openai_compatible",
    "model": "local-model",
    "base_url": "http://localhost:8080/v1"
  }'
```

### text-generation-webui

```bash
curl -X POST http://localhost:9321/providers/add \
  -H "Content-Type: application/json" \
  -d '{
    "name": "textgen",
    "type": "openai_compatible",
    "model": "local-model",
    "base_url": "http://localhost:5000/v1"
  }'
```

### Switching to a custom provider

After adding:

```bash
curl -X POST http://localhost:9321/providers/switch \
  -H "Content-Type: application/json" \
  -d '{"provider": "vllm_local"}'
```

### Provider types

| `type` value | Class | Requirements |
|--------------|-------|-------------|
| `ollama` | `OllamaProvider` | Ollama running at `base_url` |
| `openai` | `OpenAIProvider` | `api_key` (or `OPENAI_API_KEY` env) |
| `openai_compatible` | `OpenAICompatibleProvider` | `base_url` required, `api_key` optional |
| `anthropic` | `AnthropicProvider` | `api_key` (or `ANTHROPIC_API_KEY` env) |

---

## Adding Custom Genres

Create a new YAML file in `server/genres/`. The filename (minus `.yaml`) becomes the genre name.

### Minimal example

Create `server/genres/synthwave.yaml`:

```yaml
name: Synthwave

bpm_range: [100, 130]

scales:
  - natural minor
  - dorian
  - mixolydian

key_tendencies:
  - A minor
  - D minor
  - E minor

rhythm_style: >
  Steady, driving 4/4 beats with emphasis on downbeats.
  Gated reverb snares on beats 2 and 4. Arpeggiated
  synth patterns in 16th notes provide rhythmic momentum.

swing: 0.0

reference_artists:
  - Perturbator
  - Carpenter Brut
  - Kavinsky
  - Com Truise

subgenres:
  - outrun
  - darksynth
  - dreamwave
```

### Full template

For a complete genre module, include all fields from the [genre module format](#genres----yaml-genre-modules) section. Look at `server/genres/techno.yaml` for a comprehensive example.

### Activation

The new genre is available immediately -- no server restart required. The genre file is loaded on first access and cached.

Set it via API:

```bash
curl -X POST http://localhost:9321/genres/set \
  -H "Content-Type: application/json" \
  -d '{"genre": "synthwave"}'
```

Or from the M4L device: the genre will appear in the dropdown after rebuilding the device (the genre list in `build-device.py` is hardcoded; for runtime-only genres, use the `cmd genre synthwave` message directly).

### How genres affect prompts

In **chat mode**, the full genre module is injected into the system prompt with detailed sections for rhythm style, bass approach, drum patterns, structure, dynamics, effects, and reference artists.

In **generate mode**, only a brief hint is injected: genre name, BPM range, scales, and key tendencies. This keeps the prompt compact for fast local inference.

---

## Development

### Project Structure

```
conduit/
  server/
    main.py              # FastAPI app
    providers.py         # LLM provider abstraction + circuit breaker
    autodetect.py        # System detection, model selection
    prompts.py           # System prompt builder
    schemas.py           # Pydantic models, JSON schema
    requirements.txt     # Python dependencies
    genres/              # YAML genre modules
      techno.yaml
      house.yaml
      ...
  m4l/
    build-device.py      # Patcher generator
    conduit-bridge.js    # Node.js bridge (node.script)
    session-context.js   # LOM poller (js)
    midi-applicator.js   # Clip writer (js)
    param-applicator.js  # Parameter writer (js)
    Conduit.maxpat       # Generated JSON patcher
    Conduit.amxd         # Generated AMPF binary
  tests/
    conftest.py          # Test fixtures and mock providers
    test_circuit_breaker.py
    test_json_schema.py
    test_providers.py
  installer/
    install.sh           # Automated installer script
  package-device.sh      # Build + package + install script
  Start Conduit.command  # Double-click launcher
  dist/                  # Packaged output
```

### Modifying the Patcher

The patcher is generated by `m4l/build-device.py`, not edited in Max's GUI. To make changes:

1. Edit `build-device.py` (add/remove/rewire objects)
2. Run the packager:

```bash
cd /path/to/conduit
./package-device.sh --install
```

This rebuilds both `Conduit.maxpat` and `Conduit.amxd`, copies them to `dist/Conduit/`, and installs to the Ableton User Library and Max Packages directories.

To inspect the generated patcher in Max's editor, open `m4l/Conduit.maxpat` in Max.

### Running Tests

Tests are in `tests/` and use pytest. All external calls are mocked -- no real API traffic.

```bash
cd /path/to/conduit
pytest tests/ -v
```

The test suite covers:
- Circuit breaker state transitions and recovery
- JSON schema validation for MIDI patterns
- Provider registry failover behavior
- Mock providers for isolated unit testing

Test fixtures are defined in `tests/conftest.py` and include mock success/fail providers, sample conversation history, session context, MIDI JSON blocks, and param JSON blocks.

### Server Hot-Reload

For development, run the server with uvicorn's reload mode:

```bash
cd /path/to/conduit/server
python3 main.py
```

The `main.py` `__main__` block already passes `reload=True` to uvicorn:

```python
uvicorn.run("main:app", host="127.0.0.1", port=9321, reload=True, log_level="info")
```

Any changes to `.py` files in `server/` will trigger an automatic restart. Genre YAML files are cached but loaded lazily, so new genres are picked up without a restart. Modified genres require a cache clear (restart the server or call `/reset`).

### Debugging Tips

**Server logs.** The server logs every request with provider, model, mode, genre, JSON block count, and token usage. Set `LOG_LEVEL=DEBUG` for verbose output including JSON repair steps.

**Max console.** In Max, open the Max Console window (Window -> Max Console or Cmd+Shift+M in Ableton). The bridge logs with `Max.post()`, and the applicators log with `post()`. Look for prefixes:
- `[server]` -- stdout/stderr from auto-launched server process
- `conduit-bridge:` -- bridge lifecycle events
- `midi-applicator:` -- note writing details (pitch, start, dur, vel for each note)
- `param-applicator:` -- parameter resolution and value scaling

**curl testing.** Test the server independently of the M4L device:

```bash
# Health check
curl http://localhost:9321/health

# Chat mode
curl -X POST http://localhost:9321/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Suggest a chord progression in A minor", "mode": "chat"}'

# Generate mode
curl -X POST http://localhost:9321/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "8-note techno kick pattern at 140 BPM", "mode": "generate", "genre": "techno"}'
```

**node.script debugging.** `conduit-bridge.js` uses `console.error()` for diagnostic logging during module loading (visible in Max Console even if `max-api` fails to load). The startup sequence logs each `require()` call individually to identify which dependency is missing.
