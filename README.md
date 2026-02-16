# Conduit

**LLM ↔ Ableton Live bridge with auto-failover.**

Route any LLM — Claude, GPT-4o, Ollama, LM Studio, llama.cpp — into your Ableton session via a Max for Live device. Everything runs locally.

## Architecture

```
┌──────────────────────┐      HTTP (localhost:9321)      ┌────────────────────┐
│   Ableton Live       │ ◄──────────────────────────────►│  Conduit Server    │
│                      │                                  │  (FastAPI/Python)  │
│  ┌────────────────┐  │    POST /ask  {prompt, session}  │                    │
│  │  M4L Device    │──┼─────────────────────────────────►│  ┌──────────────┐  │
│  │  (node.script) │  │                                  │  │ Provider     │  │
│  │                │◄─┼──────────────────────────────────┤  │ Registry +   │  │
│  └────────────────┘  │    {text, json_blocks, provider} │  │ Circuit      │  │
│                      │                                  │  │ Breaker      │  │
│  Session Context ────┤    Auto-sent every 5s            │  └──────────────┘  │
│  (BPM, key, tracks)  │                                  │                    │
└──────────────────────┘                                  └────────────────────┘
         MacBook Air                                            MacBook Air
```

## Quick Start

### 1. Start the Conduit server

```bash
cd server
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python main.py
```

Server starts at `http://127.0.0.1:9321`. Verify with:
```bash
curl http://127.0.0.1:9321/health
```

### 2. Build the M4L device

See [`m4l/PATCHER_GUIDE.md`](m4l/PATCHER_GUIDE.md) for step-by-step instructions.

Copy the three JS files into your M4L device's project folder:
- `conduit-bridge.js` — node.script HTTP client
- `session-context.js` — LOM session state gatherer
- `midi-applicator.js` — clip creator from LLM MIDI output

### 3. Use it

Load the M4L device on any MIDI track and start prompting:

| Prompt | What happens |
|--------|-------------|
| "Generate a 4-bar acid bassline in A minor" | Creates a MIDI clip on selected track |
| "What effects chain would work for industrial kicks?" | Text response in device |
| "Make it more syncopated" | Regenerates with conversation context |
| "Suggest parameter tweaks for my Wavetable synth" | Returns param change suggestions |

## Providers

Conduit auto-detects available providers on startup:

| Provider | Config | Best for |
|----------|--------|----------|
| Claude | `ANTHROPIC_API_KEY` env var | Creative suggestions, complex tasks |
| GPT-4o | `OPENAI_API_KEY` env var | General purpose |
| Ollama | Running on `localhost:11434` | Zero-latency local inference, no API cost |
| LM Studio | Running on `localhost:1234` | Local models with GUI |
| Any OpenAI-compatible | Register via API | llama.cpp, vLLM, text-gen-webui |

### Switching from M4L

```
cmd providers              → list all providers with health
cmd provider ollama        → switch to Ollama
cmd model qwen2.5:7b       → change model on current provider
cmd health                 → show active provider + circuit state
cmd circuit                → full circuit breaker breakdown
cmd reset-circuit ollama   → manually reset a tripped breaker
```

## Circuit Breaker

Ported from IRIS orchestrator — minimal version. If a provider fails 3 times consecutively, Conduit:

1. **Trips the circuit** — stops sending to that provider
2. **Auto-failover** — routes to the next healthy provider in the chain
3. **Cooldown** (60s) — then sends one test request
4. **Recovery** — if the test succeeds, circuit closes and traffic resumes

Status is visible in the M4L device: `● closed` `◐ half-open` `○ open`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server status + circuit breaker state |
| `/ask` | POST | Send prompt + session context, get response (with auto-failover) |
| `/providers` | GET | List all providers with health scores |
| `/providers/switch` | POST | Switch active provider + optional model |
| `/providers/add` | POST | Register new provider at runtime |
| `/providers/health` | GET | Circuit breaker state for all providers |
| `/providers/reset-circuit/{name}` | POST | Manual circuit reset |
| `/providers/ollama/models` | GET | List Ollama models |
| `/reset` | POST | Clear conversation history |
| `/history` | GET | View conversation history (debug) |

## Modes

- **chat**: Conversational — ask questions, get advice, discuss production
- **generate**: Structured output — LLM prioritises returning MIDI/param JSON blocks

## Push 3 Standalone Limitation

Push 3 standalone cannot run the Conduit server (no general-purpose OS access). This setup requires Ableton running on the MacBook Air with the M4L device. When in standalone mode, Conduit won't be available.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "conduit offline" in status | Start the Conduit server first |
| No MIDI clip created | Make sure a MIDI track is selected (not return/master) |
| Timeout errors | LLM generating a long response — increase timeout in `conduit-bridge.js` |
| "No providers available" | Set an API key or start Ollama |
| Circuit stays open | `cmd reset-circuit <name>` or wait 60s for auto-recovery |

## File Structure

```
conduit/
├── server/
│   ├── main.py              # FastAPI server with auto-failover
│   ├── providers.py         # Provider abstraction + circuit breaker
│   └── requirements.txt     # Python dependencies
├── m4l/
│   ├── conduit-bridge.js    # node.script HTTP client
│   ├── session-context.js   # LOM session context gatherer
│   ├── midi-applicator.js   # MIDI clip creator
│   └── PATCHER_GUIDE.md     # How to build the M4L device
├── HANDOFF.md               # CCLI handoff document
└── README.md
```
