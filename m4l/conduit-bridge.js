/**
 * conduit-bridge.js — Conduit
 *
 * Runs inside Max for Live via the [node.script] object.
 * Communicates with the local Conduit server to send prompts
 * and receive LLM responses, including parsed MIDI data.
 *
 * node.script has 1 inlet and 1 message outlet (outlet 0).
 * All messages go out outlet 0 with a type prefix:
 *   "text <response>"     — LLM text response
 *   "midi <json>"         — MIDI note data
 *   "params <json>"       — parameter changes
 *   "status <message>"    — status updates
 *
 * In the patcher, use [route text midi params status] to split.
 */

// ── Diagnostic logging (console.error works even if max-api fails) ──
console.error("conduit-bridge: loading...");

var http, path, spawn, Max;

try {
    http = require("http");
    console.error("conduit-bridge: http OK");
} catch (e) {
    console.error("conduit-bridge: FAILED http -", e.message);
}

try {
    path = require("path");
    console.error("conduit-bridge: path OK");
} catch (e) {
    console.error("conduit-bridge: FAILED path -", e.message);
}

try {
    spawn = require("child_process").spawn;
    console.error("conduit-bridge: child_process OK");
} catch (e) {
    console.error("conduit-bridge: FAILED child_process -", e.message);
}

try {
    Max = require("max-api");
    console.error("conduit-bridge: max-api OK");
} catch (e) {
    console.error("conduit-bridge: FAILED max-api -", e.message);
    console.error("conduit-bridge: __dirname =", __dirname);
    console.error("conduit-bridge: Tried paths:", [
        __dirname + "/node_modules/max-api",
    ].join(", "));
    // Cannot continue without max-api
    throw e;
}

console.error("conduit-bridge: all requires passed, setting up handlers...");

// ── Config ──────────────────────────────────────────────────────────
var BRIDGE_HOST = "127.0.0.1";
var BRIDGE_PORT = 9321;
var currentMode = "chat";
var currentGenre = "techno";
var sessionContext = null;
var serverProcess = null;

// ── HTTP Helper ─────────────────────────────────────────────────────
function request(method, urlPath, body) {
    return new Promise(function (resolve, reject) {
        var options = {
            hostname: BRIDGE_HOST,
            port: BRIDGE_PORT,
            path: urlPath,
            method: method,
            headers: { "Content-Type": "application/json" },
        };

        var req = http.request(options, function (res) {
            var data = "";
            res.on("data", function (chunk) { data += chunk; });
            res.on("end", function () {
                try {
                    resolve({ status: res.statusCode, data: JSON.parse(data) });
                } catch (e) {
                    resolve({ status: res.statusCode, data: data });
                }
            });
        });

        req.on("error", function (err) { reject(err); });
        req.setTimeout(180000, function () {
            req.destroy();
            reject(new Error("Request timed out (180s)"));
        });

        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

// ── Server Auto-Launch ──────────────────────────────────────────────

function findServerPath() {
    var fs = require("fs");
    var candidates = [
        path.join(__dirname, "..", "server", "main.py"),
        path.join(__dirname, "server", "main.py"),
        path.join(__dirname, "..", "..", "server", "main.py"),
    ];
    for (var i = 0; i < candidates.length; i++) {
        if (fs.existsSync(candidates[i])) return candidates[i];
    }
    return null;
}

function launchServer() {
    var serverPath = findServerPath();
    if (!serverPath) {
        Max.post("Conduit: server/main.py not found — cannot auto-launch");
        return Promise.resolve(false);
    }

    Max.post("Conduit: launching server from " + serverPath);
    Max.outlet("status", "starting server...");

    try {
        serverProcess = spawn("python3", [serverPath], {
            cwd: path.dirname(serverPath),
            stdio: ["ignore", "pipe", "pipe"],
            detached: true,
        });

        serverProcess.stdout.on("data", function (data) {
            Max.post("[server] " + data.toString().trim());
        });

        serverProcess.stderr.on("data", function (data) {
            Max.post("[server] " + data.toString().trim());
        });

        serverProcess.on("error", function (err) {
            Max.post("Conduit: server process error — " + err.message);
            serverProcess = null;
        });

        serverProcess.on("exit", function (code) {
            Max.post("Conduit: server process exited (code " + code + ")");
            serverProcess = null;
        });

        return Promise.resolve(true);
    } catch (err) {
        Max.post("Conduit: failed to launch server — " + err.message);
        return Promise.resolve(false);
    }
}

function healthCheck() {
    return request("GET", "/health").then(function (res) {
        return res.status === 200 ? res.data : null;
    }).catch(function () {
        return null;
    });
}

// ── Shared Response Handling ──────────────────────────────────────────

function summarizeMidi(notes) {
    if (!notes || notes.length === 0) return "No notes";
    var pitchNames = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
    var minPitch = 127, maxPitch = 0;
    var totalDur = 0;
    for (var i = 0; i < notes.length; i++) {
        var p = notes[i].pitch || 0;
        if (p < minPitch) minPitch = p;
        if (p > maxPitch) maxPitch = p;
        totalDur = Math.max(totalDur, (notes[i].start_beat || 0) + (notes[i].duration_beats || 0));
    }
    var lowName = pitchNames[minPitch % 12] + (Math.floor(minPitch / 12) - 1);
    var hiName = pitchNames[maxPitch % 12] + (Math.floor(maxPitch / 12) - 1);
    var bars = Math.ceil(totalDur / 4);
    return notes.length + " notes | " + lowName + "-" + hiName + " | " + bars + " bar" + (bars > 1 ? "s" : "");
}

function handleAskResponse(res) {
    if (res.status !== 200) {
        Max.outlet("status", "error: " + res.status);
        Max.outlet("text", "Error: " + JSON.stringify(res.data));
        return;
    }

    var responseText = res.data.text;
    var json_blocks = res.data.json_blocks;
    var midiSent = false;

    if (json_blocks && json_blocks.length > 0) {
        for (var i = 0; i < json_blocks.length; i++) {
            var block = json_blocks[i];

            // Collect swing/quantize metadata to attach to MIDI payloads
            var meta = {};
            if (block.swing != null) meta.swing = block.swing;
            if (block.quantize != null) meta.quantize = block.quantize;

            if (block.midi_notes) {
                var midiPayload = { notes: block.midi_notes };
                if (meta.swing != null) midiPayload.swing = meta.swing;
                if (meta.quantize != null) midiPayload.quantize = meta.quantize;
                Max.outlet("midi", JSON.stringify(midiPayload));
                Max.outlet("status", summarizeMidi(block.midi_notes) + " — writing to clip...");
                midiSent = true;
            }
            if (block.drum_notes) {
                var drumPayload = { notes: block.drum_notes, is_drum: true };
                if (meta.swing != null) drumPayload.swing = meta.swing;
                if (meta.quantize != null) drumPayload.quantize = meta.quantize;
                Max.outlet("midi", JSON.stringify(drumPayload));
                Max.outlet("status", summarizeMidi(block.drum_notes) + " (drums) — writing to clip...");
                midiSent = true;
            }
            if (block.cc_messages) {
                Max.outlet("params", JSON.stringify({ cc_messages: block.cc_messages }));
                Max.outlet("status", "sending " + block.cc_messages.length + " CC messages...");
            }
            if (block.params) {
                Max.outlet("params", JSON.stringify(block.params));
                Max.outlet("status", "applying " + block.params.length + " param changes...");
            }
        }
    }

    // For generate mode with MIDI data, show a summary instead of raw JSON
    if (midiSent && json_blocks && json_blocks.length > 0 && json_blocks[0].midi_notes) {
        var summary = summarizeMidi(json_blocks[0].midi_notes);
        var patternTag = res.data.pattern_id ? " [#" + res.data.pattern_id + "]" : "";
        Max.outlet("text", "Generated: " + summary + patternTag + "\n\nModel: " + (res.data.model || "?") + " via " + (res.data.provider || "?"));
    } else {
        Max.outlet("text", responseText || "No response");
    }

    if (!midiSent) {
        Max.outlet("status", "ready");
    }
}

function handleAskError(err) {
    Max.outlet("status", "error: " + err.message);
    Max.outlet("text", "Connection error: " + err.message + ". Is the Conduit server running?");
}

// ── Handlers ────────────────────────────────────────────────────────

Max.addHandler("prompt", function () {
    // Max sends multi-word text as separate arguments — rejoin them
    var args = Array.prototype.slice.call(arguments);
    var text = args.join(" ");
    if (!text || text.trim() === "") {
        Max.outlet("status", "empty prompt — ignoring");
        return;
    }

    Max.post("Conduit: prompt received — " + text.substring(0, 80));
    Max.outlet("status", "thinking...");

    var payload = {
        prompt: text,
        mode: currentMode,
        genre: currentGenre,
    };

    if (sessionContext) {
        payload.session = sessionContext;
    }

    request("POST", "/ask", payload)
        .then(handleAskResponse)
        .catch(handleAskError);
});

Max.addHandler("generate", function () {
    var args = Array.prototype.slice.call(arguments);
    var text = args.join(" ");
    if (!text || text.trim() === "") {
        Max.outlet("status", "empty prompt — ignoring");
        return;
    }

    Max.post("Conduit: generate request — " + text.substring(0, 80));
    Max.outlet("status", "generating MIDI...");

    var payload = {
        prompt: text,
        mode: "generate",
        genre: currentGenre,
    };

    if (sessionContext) {
        payload.session = sessionContext;
    }

    request("POST", "/ask", payload)
        .then(handleAskResponse)
        .catch(handleAskError);
});

Max.addHandler("session", function () {
    // Max splits JSON on spaces — rejoin all arguments
    var args = Array.prototype.slice.call(arguments);
    var jsonString = args.join(" ");
    try {
        sessionContext = JSON.parse(jsonString);
        Max.outlet("status", "session updated (BPM: " + (sessionContext.bpm || "?") + ")");
    } catch (e) {
        Max.outlet("status", "invalid session JSON");
    }
});

Max.addHandler("cmd", function (command) {
    var args = Array.prototype.slice.call(arguments, 1);

    switch (command) {
        case "reset":
            request("POST", "/reset").then(function () {
                Max.outlet("status", "conversation reset");
                Max.outlet("text", "Conversation history cleared.");
            }).catch(function (err) {
                Max.outlet("status", "reset failed: " + err.message);
            });
            break;

        case "health":
            request("GET", "/health").then(function (res) {
                if (res.status === 200) {
                    var d = res.data;
                    Max.outlet("status", d.active_provider + "/" + d.active_model +
                        " [" + d.health_score + "hp]");
                } else {
                    Max.outlet("status", "conduit error");
                }
            }).catch(function () {
                Max.outlet("status", "conduit unreachable");
            });
            break;

        case "mode":
            if (args[0] === "chat" || args[0] === "generate") {
                currentMode = args[0];
                Max.outlet("status", "mode: " + currentMode);
            }
            break;

        case "genre":
            if (args[0]) {
                request("POST", "/genres/set", { genre: args[0] }).then(function (res) {
                    if (res.status === 200) {
                        currentGenre = args[0];
                        Max.outlet("status", "genre: " + currentGenre);
                    } else {
                        Max.outlet("status", "genre failed");
                    }
                }).catch(function (err) {
                    Max.outlet("status", "genre failed: " + err.message);
                });
            }
            break;

        case "system":
            request("GET", "/system").then(function (res) {
                if (res.status === 200) {
                    var s = res.data;
                    Max.outlet("text", "Chip: " + (s.system.chip || "unknown") +
                        "\nRAM: " + s.system.total_ram_gb + "GB" +
                        "\nModel: " + s.recommended_model.model);
                    Max.outlet("status", s.system.total_ram_gb + "GB RAM");
                }
            }).catch(function (err) {
                Max.outlet("status", "system check failed: " + err.message);
            });
            break;

        case "provider":
            if (args[0]) {
                var body = { provider: args[0] };
                if (args[1]) body.model = args[1];
                request("POST", "/providers/switch", body).then(function (res) {
                    if (res.status === 200) {
                        Max.outlet("status", res.data.provider + "/" + res.data.model);
                    }
                }).catch(function (err) {
                    Max.outlet("status", "switch failed: " + err.message);
                });
            }
            break;

        case "model":
            if (args[0]) {
                healthCheck().then(function (health) {
                    if (!health) return;
                    return request("POST", "/providers/switch", {
                        provider: health.active_provider,
                        model: args[0],
                    });
                }).then(function (res) {
                    if (res && res.status === 200) {
                        Max.outlet("status", "model: " + args[0]);
                    }
                }).catch(function (err) {
                    Max.outlet("status", "model change failed: " + err.message);
                });
            }
            break;

        case "paste":
            var pasteUrl = args[0] ? "/patterns/" + args[0] : "/patterns/latest";
            Max.outlet("status", "pasting pattern...");
            request("GET", pasteUrl).then(function (res) {
                if (res.status === 200 && res.data.json_blocks) {
                    var blocks = res.data.json_blocks;
                    for (var pi = 0; pi < blocks.length; pi++) {
                        var blk = blocks[pi];
                        if (blk.midi_notes) {
                            Max.outlet("midi", JSON.stringify({ notes: blk.midi_notes }));
                            Max.outlet("status", "pasted " + blk.midi_notes.length + " notes [#" + res.data.id + "]");
                        }
                        if (blk.drum_notes) {
                            Max.outlet("midi", JSON.stringify({ notes: blk.drum_notes, is_drum: true }));
                            Max.outlet("status", "pasted " + blk.drum_notes.length + " drum hits [#" + res.data.id + "]");
                        }
                    }
                } else if (res.status === 404) {
                    Max.outlet("status", "no patterns saved");
                } else {
                    Max.outlet("status", "paste failed: " + res.status);
                }
            }).catch(function (err) {
                Max.outlet("status", "paste failed: " + err.message);
            });
            break;

        case "patterns":
            request("GET", "/patterns").then(function (res) {
                if (res.status === 200 && res.data.patterns) {
                    var pats = res.data.patterns;
                    if (pats.length === 0) {
                        Max.outlet("text", "No patterns saved yet. Generate something first.");
                        Max.outlet("status", "clipboard empty");
                        return;
                    }
                    var lines = ["Pattern Clipboard (" + pats.length + "):"];
                    for (var li = 0; li < pats.length; li++) {
                        var p = pats[li];
                        lines.push("#" + p.id + " — " + p.note_count + " notes — " + (p.prompt || "").substring(0, 50));
                    }
                    Max.outlet("text", lines.join("\n"));
                    Max.outlet("status", pats.length + " patterns saved");
                }
            }).catch(function (err) {
                Max.outlet("status", "patterns failed: " + err.message);
            });
            break;

        case "clear":
            request("DELETE", "/patterns").then(function (res) {
                if (res.status === 200) {
                    Max.outlet("status", "clipboard cleared");
                    Max.outlet("text", "Pattern clipboard cleared.");
                }
            }).catch(function (err) {
                Max.outlet("status", "clear failed: " + err.message);
            });
            break;

        case "undo":
            Max.outlet("undo", "undo");
            Max.outlet("status", "undoing last write...");
            break;

        default:
            Max.outlet("status", "unknown command: " + command);
    }
});

// ── Startup ─────────────────────────────────────────────────────────
console.error("conduit-bridge: registering handlers done, starting health check...");
Max.post("Conduit node.script loaded");
Max.post("Connecting to Conduit server at " + BRIDGE_HOST + ":" + BRIDGE_PORT);

healthCheck().then(function (health) {
    if (health) {
        var provider = health.active_provider || "no provider";
        var model = health.active_model || "";
        Max.outlet("status", "connected: " + provider + "/" + model);
        Max.post("Conduit: connected to " + provider + "/" + model);
        return;
    }

    Max.outlet("status", "server offline — auto-launching...");
    return launchServer().then(function (launched) {
        if (!launched) {
            Max.outlet("status", "server offline — run Start Conduit.command");
            return;
        }

        var attempt = 0;
        function retry() {
            if (attempt >= 5) {
                Max.outlet("status", "server launch timeout");
                return;
            }
            return sleep(2000).then(function () {
                return healthCheck();
            }).then(function (h) {
                attempt++;
                if (h) {
                    Max.outlet("status", "connected: " + (h.active_provider || "?") + "/" + (h.active_model || "?"));
                    Max.post("Conduit: connected after " + attempt + " retries");
                } else {
                    Max.outlet("status", "waiting for server... (" + attempt + "/5)");
                    return retry();
                }
            });
        }
        return retry();
    });
}).catch(function (err) {
    console.error("conduit-bridge: startup error:", err.message);
    Max.outlet("status", "startup error: " + err.message);
});

Max.addHandler("bang", function () {
    healthCheck().then(function (h) {
        if (h) {
            Max.outlet("status", "connected: " + h.active_provider + "/" + h.active_model);
        } else {
            Max.outlet("status", "conduit offline");
        }
    });
});

process.on("exit", function () {
    if (serverProcess) {
        serverProcess.kill();
    }
});

console.error("conduit-bridge: script fully loaded");
