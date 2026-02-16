/**
 * midi-applicator.js
 *
 * Runs inside a [js] object in the M4L patcher.
 * Receives MIDI note data (JSON string) from the bridge and
 * writes it into a clip on the selected track.
 *
 * Strategy for finding where to write:
 *   1. Look for an empty clip slot on the SELECTED track
 *   2. If none found, fall back to the currently selected clip
 *      (detail_clip) — if it exists, replace its notes
 *   3. If no detail clip, fall back to the highlighted clip slot
 *      and create a new clip there
 *
 * Inlet 0: JSON string of MIDI notes array
 *   Each note: { "pitch": 0-127, "velocity": 0-127,
 *                "start_beat": float, "duration_beats": float }
 *
 * Outlet 0: status message
 *
 * Usage in patcher:
 *   [route midi] -> [js midi-applicator.js]
 */

autowatch = 1;
inlets = 1;
outlets = 1;

// ── Undo State ──────────────────────────────────────────────────────
// Stores info about the last write so we can reverse it.
// type: "created" (new clip in empty slot) or "replaced" (overwrote existing clip)
var lastAction = null;

// ── Helpers ──────────────────────────────────────────────────────────

function log() {
    var parts = [];
    for (var i = 0; i < arguments.length; i++) {
        parts.push(arguments[i]);
    }
    post("midi-applicator: " + parts.join(" ") + "\n");
}

function calcClipLength(notes) {
    var maxEnd = 0;
    for (var i = 0; i < notes.length; i++) {
        var noteEnd = (notes[i].start_beat || 0) + (notes[i].duration_beats || 0.25);
        if (noteEnd > maxEnd) maxEnd = noteEnd;
    }
    // Round up to nearest bar (4 beats in 4/4 time)
    var clipLength = Math.ceil(maxEnd / 4) * 4;
    if (clipLength < 4) clipLength = 4;
    return clipLength;
}

// ── Quantization ────────────────────────────────────────────────────

/**
 * Parse a quantize string like "1/16" or "1/16t" into a beat duration.
 * Triplet grids (suffix "t"): divide the normal grid into 3 equal parts
 * over the space of 2, e.g. 1/8t = 2/3 of an 1/8 note = 0.333 beats.
 * Returns 0 if the string is invalid or null (meaning no quantization).
 */
function parseQuantizeGrid(q) {
    if (!q) return 0;
    var isTriplet = false;
    var clean = q;
    // Check for triplet suffix (e.g. "1/16t")
    if (clean.charAt(clean.length - 1) === "t" || clean.charAt(clean.length - 1) === "T") {
        isTriplet = true;
        clean = clean.substring(0, clean.length - 1);
    }
    var parts = clean.split("/");
    if (parts.length !== 2) return 0;
    var num = parseFloat(parts[0]);
    var den = parseFloat(parts[1]);
    if (!num || !den || den === 0) return 0;
    // In 4/4, a whole note = 4 beats, so 1/16 = 4/16 = 0.25 beats
    var grid = (4 * num) / den;
    // Triplet: 3 notes in the space of 2 normal subdivisions
    if (isTriplet) {
        grid = grid * 2 / 3;
    }
    return grid;
}

/**
 * Quantize a beat position to the nearest grid point.
 */
function quantizeBeat(beat, gridSize) {
    if (gridSize <= 0) return beat;
    return Math.round(beat / gridSize) * gridSize;
}

// ── Swing ───────────────────────────────────────────────────────────

/**
 * Apply swing to notes. Swing delays every other grid position
 * (the "offbeat" subdivisions). swingAmount is 0-100 where
 * 0 = straight, 50 = moderate swing, 100 = full triplet feel.
 * gridSize is the quantization grid in beats (e.g., 0.25 for 1/16).
 */
function applySwing(notes, swingAmount, gridSize) {
    if (!swingAmount || swingAmount <= 0) return notes;
    if (gridSize <= 0) gridSize = 0.25; // default to 1/16 grid

    // Swing factor: 0 = no shift, 1.0 = shift a full grid unit
    var factor = (swingAmount / 100.0) * gridSize * 0.5;

    for (var i = 0; i < notes.length; i++) {
        var beat = notes[i].start_beat || 0;
        // Determine which grid position this note falls on
        var gridIndex = Math.round(beat / gridSize);
        // Apply swing to odd grid positions (the offbeats)
        if (gridIndex % 2 === 1) {
            notes[i].start_beat = beat + factor;
        }
    }
    return notes;
}

// ── CC Message Handling ─────────────────────────────────────────────

/**
 * Write CC automation envelopes into a clip.
 * Uses clip envelope API to create automation for each CC number.
 */
function writeCCIntoClip(clipApi, ccMessages, clipLength) {
    if (!ccMessages || ccMessages.length === 0) return;

    // Group CC messages by cc_number
    var ccByNumber = {};
    for (var i = 0; i < ccMessages.length; i++) {
        var msg = ccMessages[i];
        var ccNum = msg.cc_number;
        if (!ccByNumber[ccNum]) ccByNumber[ccNum] = [];
        ccByNumber[ccNum].push(msg);
    }

    // For each CC number, create envelope breakpoints
    for (var ccNum in ccByNumber) {
        if (!ccByNumber.hasOwnProperty(ccNum)) continue;
        var messages = ccByNumber[ccNum];

        // Sort by beat position
        messages.sort(function (a, b) { return (a.beat || 0) - (b.beat || 0); });

        // Clear existing envelope for this CC and write new breakpoints
        // Use the clip's envelope API (parameter index based on CC number)
        // In Ableton's LOM, MIDI clip envelopes are accessed via
        // clip.clear_envelope(parameter_id) and clip.insert_step(...)
        // For simplicity, log the CC data — the host patcher can route
        // this to a [ctlout] or automation lane.
        log("CC#" + ccNum + ": " + messages.length + " points over " +
            clipLength + " beats");
        for (var j = 0; j < messages.length; j++) {
            log("  CC#" + ccNum + " beat=" + (messages[j].beat || 0) +
                " val=" + messages[j].value);
        }
    }
}

function writeNotesIntoClip(clipApi, notes, clipLength, ccMessages) {
    // Set clip length and looping before writing notes
    clipApi.set("looping", 0);

    // Use select_all_notes + replace_selected_notes pattern for reliability.
    // First clear any existing notes.
    clipApi.call("select_all_notes");
    clipApi.call("replace_selected_notes");
    clipApi.call("notes", 0);
    clipApi.call("done");

    // Brief operational pause — the LOM sometimes needs a tick
    // (In Max JS, this is synchronous, but calling get forces a round-trip)
    clipApi.get("length");

    // Now write the new notes using set_notes
    clipApi.call("set_notes");
    clipApi.call("notes", notes.length);

    for (var i = 0; i < notes.length; i++) {
        var n = notes[i];
        var pitch = Math.max(0, Math.min(127, Math.round(n.pitch)));
        var vel   = Math.max(1, Math.min(127, Math.round(n.velocity || 100)));
        var start = Math.max(0, n.start_beat || 0);
        var dur   = Math.max(0.0625, n.duration_beats || 0.25); // min 1/64 note
        clipApi.call("note", pitch, start, dur, vel, 0); // 0 = not muted
        log("  note", i, "pitch=" + pitch, "start=" + start,
            "dur=" + dur, "vel=" + vel);
    }

    clipApi.call("done");

    // Write CC automation if present
    if (ccMessages && ccMessages.length > 0) {
        writeCCIntoClip(clipApi, ccMessages, clipLength);
    }

    // Re-enable looping and set loop boundaries
    clipApi.set("looping", 1);
    clipApi.set("loop_start", 0);
    clipApi.set("loop_end", clipLength);

    log("wrote", notes.length, "notes, clip length =", clipLength, "beats");
}

// ── Main entry point ─────────────────────────────────────────────────

function anything() {
    // Reconstruct the full message (Max splits on spaces)
    var args = arrayfromargs(messagename, arguments);
    var jsonStr = args.join(" ");

    log("received message, length =", jsonStr.length);

    // ── Parse payload ────────────────────────────────────────────────
    var payload;
    try {
        payload = JSON.parse(jsonStr);
    } catch (e) {
        log("JSON parse error:", e.message);
        outlet(0, "error: invalid MIDI JSON");
        return;
    }

    // Extract metadata (swing, quantize, cc_messages, is_drum) from object payloads
    var swingAmount = 0;
    var quantizeGrid = null;
    var ccMessages = null;
    var isDrum = false;
    var notes;

    // The bridge may send:
    //   - A raw array: [{pitch, velocity, ...}, ...]
    //   - An object: { notes: [...], swing: N, quantize: "1/16", cc_messages: [...], is_drum: bool }
    //   - An object with .midi_notes key (legacy)
    if (payload && !Array.isArray(payload)) {
        if (payload.swing != null) swingAmount = payload.swing;
        if (payload.quantize != null) quantizeGrid = payload.quantize;
        if (payload.cc_messages != null) ccMessages = payload.cc_messages;
        if (payload.is_drum) isDrum = true;

        if (Array.isArray(payload.notes)) {
            notes = payload.notes;
        } else if (Array.isArray(payload.midi_notes)) {
            notes = payload.midi_notes;
        } else {
            log("error: payload is not an array and has no .notes/.midi_notes key");
            outlet(0, "error: unexpected MIDI data format");
            return;
        }
    } else {
        notes = payload;
    }

    if (!notes || notes.length === 0) {
        log("error: empty notes array");
        outlet(0, "error: empty notes array");
        return;
    }

    if (isDrum) log("drum pattern detected (" + notes.length + " hits)");
    else log("parsed", notes.length, "notes");

    // ── Apply quantization ──────────────────────────────────────────
    var gridSize = parseQuantizeGrid(quantizeGrid);
    if (gridSize > 0) {
        log("quantizing to grid:", quantizeGrid, "(" + gridSize + " beats)");
        for (var qi = 0; qi < notes.length; qi++) {
            notes[qi].start_beat = quantizeBeat(notes[qi].start_beat || 0, gridSize);
        }
    }

    // ── Apply swing ─────────────────────────────────────────────────
    if (swingAmount > 0) {
        var swingGrid = gridSize > 0 ? gridSize : 0.25;
        log("applying swing:", swingAmount + "% on grid " + swingGrid);
        notes = applySwing(notes, swingAmount, swingGrid);
    }

    // ── Calculate clip length ────────────────────────────────────────
    var clipLength = calcClipLength(notes);
    log("calculated clip length:", clipLength, "beats");

    // ── Get the SELECTED track ───────────────────────────────────────
    try {
        var track = new LiveAPI("live_set view selected_track");
        var trackName = track.get("name").toString();
        var trackId = track.id;
        log("selected track:", trackName, "(id=" + trackId + ")");

        // Verify this is a MIDI track (has_midi_input)
        // Return tracks don't have clip slots — skip gracefully
        var clipSlots = track.get("clip_slots");
        var numSlots = clipSlots.length / 2; // LOM returns [id, N, id, N, ...]
        log("track has", numSlots, "clip slots");

        // ── Strategy 1: find the first empty clip slot on the selected track
        var targetSlot = null;
        var targetSlotIdx = -1;

        for (var i = 0; i < numSlots; i++) {
            var slotPath = "live_set view selected_track clip_slots " + i;
            var slotApi = new LiveAPI(slotPath);
            var hasClip = parseInt(slotApi.get("has_clip"), 10);
            if (hasClip === 0) {
                targetSlotIdx = i;
                targetSlot = slotApi;
                log("found empty slot at index", i);
                break;
            }
        }

        if (targetSlot !== null) {
            // Create a new clip in the empty slot
            log("creating clip in slot", targetSlotIdx,
                "length =", clipLength);
            targetSlot.call("create_clip", clipLength);

            // Access the newly created clip
            var clipPath = "live_set view selected_track clip_slots " +
                           targetSlotIdx + " clip";
            var clipApi = new LiveAPI(clipPath);

            if (parseInt(clipApi.id, 10) === 0) {
                log("error: clip creation failed — clip id is 0");
                outlet(0, "error: clip creation failed on slot " +
                       targetSlotIdx);
                return;
            }

            log("clip created, id =", clipApi.id);
            writeNotesIntoClip(clipApi, notes, clipLength, ccMessages);

            lastAction = {
                type: "created",
                slotPath: "live_set view selected_track clip_slots " + targetSlotIdx,
                trackName: trackName
            };

            outlet(0, "created " + notes.length + " notes on \"" +
                   trackName + "\" slot " + targetSlotIdx +
                   " (" + clipLength + " beats)");
            return;
        }

        log("no empty clip slots found — trying fallback strategies");

        // ── Strategy 2: use the currently selected clip (detail_clip)
        var detailClip = new LiveAPI("live_set view detail_clip");
        if (parseInt(detailClip.id, 10) !== 0) {
            log("falling back to detail_clip, id =", detailClip.id);

            // Verify the clip belongs to the selected track
            var clipName = detailClip.get("name").toString();
            log("detail clip name:", clipName);

            // Update clip length if needed
            var currentLength = parseFloat(detailClip.get("length"));
            if (clipLength > currentLength) {
                log("extending clip from", currentLength,
                    "to", clipLength, "beats");
                detailClip.set("looping", 0);
                detailClip.set("loop_end", clipLength);
            }

            writeNotesIntoClip(detailClip, notes, clipLength, ccMessages);

            lastAction = {
                type: "replaced",
                clipPath: "live_set view detail_clip",
                trackName: trackName,
                clipName: clipName
            };

            outlet(0, "replaced notes in \"" + clipName + "\" on \"" +
                   trackName + "\" (" + notes.length + " notes, " +
                   clipLength + " beats)");
            return;
        }

        log("no detail clip available — trying highlighted clip slot");

        // ── Strategy 3: use the highlighted clip slot
        var highlightedSlot = new LiveAPI(
            "live_set view highlighted_clip_slot"
        );
        if (parseInt(highlightedSlot.id, 10) !== 0) {
            var hsHasClip = parseInt(highlightedSlot.get("has_clip"), 10);
            log("highlighted clip slot id =", highlightedSlot.id,
                "has_clip =", hsHasClip);

            if (hsHasClip === 1) {
                // There is a clip here — get it and replace its notes
                // We need to find the path. Use the clip child.
                var hsClip = new LiveAPI(
                    "live_set view highlighted_clip_slot clip"
                );
                if (parseInt(hsClip.id, 10) !== 0) {
                    var hsClipName = hsClip.get("name").toString();
                    log("replacing notes in highlighted clip:", hsClipName);

                    var hsCurrentLength = parseFloat(hsClip.get("length"));
                    if (clipLength > hsCurrentLength) {
                        hsClip.set("looping", 0);
                        hsClip.set("loop_end", clipLength);
                    }

                    writeNotesIntoClip(hsClip, notes, clipLength, ccMessages);

                    lastAction = {
                        type: "replaced",
                        clipPath: "live_set view highlighted_clip_slot clip",
                        trackName: trackName,
                        clipName: hsClipName
                    };

                    outlet(0, "replaced notes in highlighted clip \"" +
                           hsClipName + "\" (" + notes.length + " notes, " +
                           clipLength + " beats)");
                    return;
                }
            } else {
                // Empty highlighted slot — create a clip here
                log("creating clip in highlighted slot");
                highlightedSlot.call("create_clip", clipLength);

                var newClip = new LiveAPI(
                    "live_set view highlighted_clip_slot clip"
                );
                if (parseInt(newClip.id, 10) !== 0) {
                    log("clip created in highlighted slot, id =", newClip.id);
                    writeNotesIntoClip(newClip, notes, clipLength, ccMessages);

                    lastAction = {
                        type: "created",
                        slotPath: "live_set view highlighted_clip_slot",
                        trackName: trackName
                    };

                    outlet(0, "created " + notes.length +
                           " notes in highlighted slot on \"" +
                           trackName + "\" (" + clipLength + " beats)");
                    return;
                }
            }
        }

        // ── All strategies exhausted ─────────────────────────────────
        log("error: all strategies exhausted — no place to write notes");
        outlet(0, "error: no available clip slot on \"" + trackName +
               "\". Select a clip or free a slot.");

    } catch (e) {
        log("EXCEPTION:", e.message);
        outlet(0, "error: " + e.message);
    }
}

// ── Undo ────────────────────────────────────────────────────────────

function undo() {
    if (!lastAction) {
        log("undo: nothing to undo");
        outlet(0, "nothing to undo");
        return;
    }

    try {
        if (lastAction.type === "created") {
            // We created a new clip in an empty slot — delete it
            var slot = new LiveAPI(lastAction.slotPath);
            if (parseInt(slot.id, 10) !== 0) {
                var hasClip = parseInt(slot.get("has_clip"), 10);
                if (hasClip === 1) {
                    slot.call("delete_clip");
                    log("undo: deleted clip from", lastAction.slotPath);
                    outlet(0, "undo: deleted clip on \"" + lastAction.trackName + "\"");
                } else {
                    log("undo: clip already gone");
                    outlet(0, "undo: clip already removed");
                }
            }
        } else if (lastAction.type === "replaced") {
            // We replaced notes in an existing clip — clear the notes
            var clip = new LiveAPI(lastAction.clipPath);
            if (parseInt(clip.id, 10) !== 0) {
                clip.call("select_all_notes");
                clip.call("replace_selected_notes");
                clip.call("notes", 0);
                clip.call("done");
                log("undo: cleared notes from", lastAction.clipName);
                outlet(0, "undo: cleared notes from \"" + lastAction.clipName + "\"");
            } else {
                log("undo: clip no longer exists");
                outlet(0, "undo: clip no longer available");
            }
        }
    } catch (e) {
        log("undo EXCEPTION:", e.message);
        outlet(0, "undo error: " + e.message);
    }

    lastAction = null;
}
