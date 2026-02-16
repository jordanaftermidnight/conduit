# CCLI HANDOFF — Conduit

## What This Is

Conduit is a middleware bridge that routes any LLM (Claude, GPT-4o, Ollama, LM Studio, llama.cpp, vLLM) into Ableton Live via a Max for Live device. Includes auto-failover via circuit breaker (ported from IRIS orchestrator). Everything runs locally on MacBook Air.

## Architecture

```
┌──────────────────────┐     HTTP localhost:9321      ┌────────────────────┐
│   Ableton Live       │ ◄──────────────────────────► │  Conduit Server    │
│                      │                               │  (FastAPI/Python)  │
│  ┌────────────────┐  │   POST /ask {prompt,session}  │                    │
│  │  M4L Device    │──┼──────────────────────────────►│  ┌──────────────┐  │
│  │  (node.script) │  │                               │  │ Provider     │  │
│  │                │◄─┼───────────────────────────────┤  │ Registry +   │  │
│  └────────────────┘  │   {text, json_blocks}         │  │ Circuit      │  │
│                      │                               │  │ Breaker      │  │
│  Session Context ────┤   BPM, key, tracks (auto 5s)  │  └──────────────┘  │
│  (LOM poller)        │                               │                    │
└──────────────────────┘                               └────────────────────┘
        MacBook Air                                           MacBook Air
```

## File Structure

```
conduit/
├── server/
│   ├── main.py              # FastAPI server — endpoints, conversation history, auto-failover
│   ├── providers.py         # Provider abstraction + circuit breaker + health tracking
│   └── requirements.txt     # Python deps
├── m4l/
│   ├── conduit-bridge.js    # node.script — HTTP client, provider/circuit commands
│   ├── session-context.js   # LOM poller — gathers Ableton session state
│   ├── midi-applicator.js   # Creates MIDI clips from LLM JSON output
│   └── PATCHER_GUIDE.md     # How to wire the M4L device in Max
├── HANDOFF.md
└── README.md
```

## What's Built and Tested

### Server (`server/`)

**main.py** (~363 lines)
- FastAPI app on localhost:9321
- `POST /ask` — main endpoint, uses `chat_with_failover` for auto-failover across providers
- `GET /health` — includes circuit breaker state, health score, avg response ms
- `GET /providers` — lists all providers with health data
- `POST /providers/switch` — switch active provider + optional model change
- `POST /providers/add` — register new provider at runtime
- `GET /providers/health` — circuit breaker status for all providers
- `POST /providers/reset-circuit/{name}` — manual circuit reset
- `GET /providers/ollama/models` — list Ollama models
- `POST /reset` — clear conversation history
- `GET /history` — debug endpoint
- Conversation history with 40-message sliding window
- JSON block extraction from LLM responses (```json fenced blocks)
- System prompt tuned for industrial/techno/IDM production

**providers.py** (~551 lines)
- `BaseProvider` ABC — unified interface: `chat()`, `is_available()`
- `AnthropicProvider` — Claude API via `anthropic` SDK
- `OpenAIProvider` — GPT-4o etc via `openai` SDK
- `OllamaProvider` — native REST, zero dependencies, includes `list_models()`
- `OpenAICompatibleProvider` — llama.cpp, LM Studio, vLLM, text-gen-webui
- `CircuitBreaker` — ported from IRIS orchestrator, minimal version:
  - 3 states: closed / open / half_open
  - Trips after 3 consecutive failures, 60s cooldown
  - Auto-recovery via half_open test
  - Per-provider health score (0-100) from sliding window of 20 response times
- `ProviderRegistry` — manages providers + integrates circuit breaker
  - `chat_with_failover()` — tries active, falls back through chain, skips tripped circuits
  - `list_available()` — includes health data per provider
- `build_default_registry()` — auto-detects available providers from env vars + local services

### M4L (`m4l/`)

**conduit-bridge.js** (~288 lines) — node.script for Max for Live
- HTTP client to Conduit server
- 4 outlets: text response, MIDI data, param changes, status
- Commands: prompt, session, reset, health, mode, provider, providers, model, circuit, reset-circuit
- Status shows circuit state icons: ● closed, ◐ half-open, ○ open
- 60s request timeout

**session-context.js** (81 lines) — Max [js] object
- Queries LOM for: BPM, time sig, transport state, song position, track names, selected track, groove
- Outputs JSON string, wired to node.script via [prepend session]
- Auto-runs on loadbang with 2s delay

**midi-applicator.js** (114 lines) — Max [js] object
- Parses MIDI JSON from LLM: {pitch, velocity, start_beat, duration_beats}
- Finds first empty clip slot on selected track
- Creates clip, writes notes via LOM clip API
- Auto-calculates clip length rounded to nearest bar (4/4)

**PATCHER_GUIDE.md** — ASCII wiring diagram + step-by-step instructions for building the M4L device in Max (can't be generated programmatically)

## What's NOT Built Yet (potential next steps)

1. **No .amxd file** — the M4L patcher must be wired manually in Max following PATCHER_GUIDE.md
2. **No audio analysis** — could add spectral analysis / onset detection to enrich session context
3. **No param applicator JS** — `midi-applicator.js` handles MIDI, but param changes from outlet 2 just go to [print]. Could add `param-applicator.js` that maps LLM param suggestions to `live.object` calls
4. **No preset/snapshot system** — could save/load provider configs and system prompts
5. **No streaming** — responses are full round-trip. Could add SSE for long responses
6. **System prompt is hardcoded** — could be loaded from a file or configurable via API
7. **No tests directory** — all testing was done inline. Could extract to pytest suite

## Key Design Decisions

- **Everything on MacBook Air** — Push 3 standalone has no WiFi/BT MIDI and can't run servers
- **HTTP not OSC** — node.script in M4L can do HTTP natively, OSC would need extra Max objects
- **Ollama uses raw urllib** — no `openai` SDK dependency needed for local models
- **Circuit breaker from IRIS** — ported the core pattern (3 failures → 60s cooldown → half-open test) but dropped the IRIS ML layer (semantic cache, RL routing, threat detection) as overkill for a local music tool
- **JSON in ```json fences** — works across all LLM providers, easy to parse
- **Conversation history on server** — not in M4L, avoids node.script memory limits
- **Name: Conduit** — signal path between LLMs and Live, industrial connotation

## Running It

```bash
cd server
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."  # optional
# export OPENAI_API_KEY="sk-..."       # optional
# Start Ollama if using local models:  ollama serve
python main.py
```

Then build + load the M4L device per PATCHER_GUIDE.md.

## Dependencies

- Python 3.10+
- fastapi, uvicorn, pydantic (core)
- anthropic SDK (if using Claude)
- openai SDK (if using GPT-4o / LM Studio / llama.cpp)
- Ollama needs no Python SDK (raw HTTP)
- Ableton Live 11/12 with Max for Live
- Node.js available in Max's node.script
