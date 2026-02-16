<p align="center">
  <img src="assets/logo-dark.svg" alt="Conduit" width="295">
</p>

<h3 align="center">Conduit v1.0 by Jordanaftermidnight</h3>
<p align="center">AI MIDI generation for Ableton Live</p>
<p align="center"><a href="https://maxforlive.com/library/device/14573/conduit-ai-midi-generator">Download on maxforlive.com</a> · <a href="https://ko-fi.com/jordanaftermidnight">Support this project on Ko-fi</a></p>

Conduit is a Max for Live device that connects Ableton Live to a local AI running on your own machine. Type a prompt like *"generate a 4x4 techno drum beat"* and it writes MIDI clips directly into your session. No cloud API keys, no subscriptions, no sending your music anywhere. Everything runs locally and privately through [Ollama](https://ollama.com).

---

## What It Does

Conduit has two modes:

### Chat Mode
Talk to the AI about your session. Ask for ideas, get feedback, discuss arrangements. It can see your BPM, time signature, track names, and more — so the answers are relevant to what you're actually working on.

### Generate Mode
Tell the AI what you want and get MIDI clips written straight into your selected track. Drums, melodies, basslines, chord progressions — whatever you need to get moving.

---

## Features

**Genre-aware** — 8 built-in genre modules (techno, house, dnb, dubstep, hip-hop, ambient, IDM, trance) that shape how the AI thinks about rhythm, velocity, and note choice. Pick one from the dropdown.

**Session-aware** — The device polls Ableton every 5 seconds for BPM, time signature, track names, selected track, and transport state. The AI uses all of this as context when generating.

**Pattern clipboard** — Every pattern you generate is auto-saved. Hit Paste to re-insert the last one into a new clip slot. Up to 20 patterns stored per session.

**Undo** — Changed your mind? Revert the last generated clip instantly.

**100% local** — Runs on Ollama with llama3.2 (3B parameter model). No API keys. No cloud. Works offline once you've pulled the model.

---

## Requirements

- macOS (Apple Silicon or Intel) or Windows
- Ableton Live 11 or 12 with Max for Live
- Python 3.9+
- [Ollama](https://ollama.com) (free, open source)
- ~4GB free RAM for the model

---

## Setup

This takes about 5 minutes. ☕

### 1. Install Ollama

Download from [ollama.com](https://ollama.com), drag it to Applications, and open it once so it finishes installing.

### 2. Pull the model

Open Terminal and run:

```bash
ollama pull llama3.2
```

This downloads the AI model (~2GB). Only needs to happen once.

### 3. Install the device

Open Terminal in the project folder and run:

```bash
./package-device.sh --install
```

This builds the device and copies it to Ableton's User Library.

### 4. Launch the server

Double-click `Start Conduit.command`. It checks your dependencies, starts the server, and shows a status message when everything's ready. Keep this window open.

### 5. Load in Ableton

Open Ableton's Browser, go to **User Library > MIDI Effects > Conduit**, and drag it onto a MIDI track.

### 6. Wait a few seconds

The device needs about 5 seconds to connect to the server. You'll see the status bar update when it's ready.

### 7. Type a prompt and press Enter

That's it. You're making music with AI now.

---

## Using the Device

### The Interface

```
┌─────────────────────────────────────────────┐
│  CONDUIT       [chat/generate ▾]  [genre ▾] │  <- mode + genre dropdowns
│                                              │
│  [ type your prompt here...              ]   │  <- prompt input
│                                              │
│  ┌────────────────────────────────────┐      │
│  │  response display area             │      │  <- AI response
│  │                                    │      │
│  └────────────────────────────────────┘      │
│                                              │
│  [Reset] [Generate] [Paste] [Undo] [Clear]   │  <- buttons
│  ● Connected                                 │  <- status bar
└─────────────────────────────────────────────┘
```

### Generating MIDI

1. Select a MIDI track in Ableton (or create a new one)
2. Set mode to **generate** in the dropdown
3. Pick a genre
4. Type what you want — *"4-bar techno kick pattern"*, *"8-note ambient melody in C minor"*, whatever comes to mind
5. Press Enter or click Generate
6. The clip appears in the first empty slot on your selected track

### Chatting

1. Set mode to **chat**
2. Ask anything — *"what scale would work over these chords?"*, *"suggest a drum fill for the bridge"*, *"how should I arrange this into a full track?"*
3. The AI sees your session context so it can give answers that actually make sense for your project

---

## Tips for Better Prompts

The more specific you are, the better the results.

**Be specific about what you want:**
> "16-note hi-hat pattern with offbeat accents" beats "make some hats" every time.

**Mention note counts and length:**
> "8 notes", "16 steps", "4-bar phrase", "2 bars of..."

**Reference genre conventions:**
> "303-style acid bassline", "amen break variation", "four-on-the-floor with ghost snares"

**Use music theory if you know it:**
> The AI understands scales, intervals, chord names, and rhythmic subdivisions. Don't be afraid to get technical.

**Keep it conversational if you don't:**
> "something dark and minimal" or "funky bassline that grooves" works too. The genre module fills in the rest.

---

## Buttons

| Button | What it does |
|--------|-------------|
| **Reset** | Clears conversation history. Fresh start. |
| **Generate** | Sends your prompt as a generate request (same as pressing Enter in generate mode). |
| **Paste** | Re-inserts the last generated pattern into a new clip slot. |
| **Undo** | Removes the last clip that was written. |
| **Clear** | Clears the prompt field and response display. |

---

## Project Structure

```
conduit/
├── server/                  # Python server (FastAPI + Ollama)
│   └── genres/              # Genre module YAML files
├── m4l/                     # Max for Live device source
├── docs/                    # Technical documentation
├── tests/                   # Test suite
├── assets/                  # Logo and branding
├── Start Conduit.command    # Double-click launcher
└── package-device.sh        # Build + install script
```

For the full technical breakdown — architecture, API reference, AMPF format spec, signal flow, and how to extend Conduit with custom providers and genres — see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Troubleshooting

**"Server not responding"**
Make sure `Start Conduit.command` is running in the background. Check the terminal window — it should show a green status.

**"No model found"**
You need to pull the model first. Open Terminal and run `ollama pull llama3.2`.

**Device shows "initializing..."**
Give it about 5 seconds. The device needs a moment to handshake with the server.

**No MIDI output**
Make sure you have a MIDI track selected in Ableton and the device is set to **generate** mode, not chat.

**Clips sound wrong or unexpected**
Try being more specific in your prompt. Mention the key, scale, number of notes, and rhythmic feel you're going for. Switching genres can also make a big difference.

---

## License

MIT License. Copyright (c) 2026 Jordanaftermidnight.

Free to use, modify, and distribute. See [LICENSE](LICENSE) for details.

---

*Conduit v1.0 — your session, your AI, your machine.* 🎹
