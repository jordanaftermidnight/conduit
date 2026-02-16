/**
 * param-applicator.js
 *
 * Runs inside a [js] object in the M4L patcher.
 * Receives param change suggestions (JSON string) from the bridge and
 * applies them to Ableton Live devices via the Live Object Model (LOM).
 *
 * Inlet 0: JSON string of param changes
 *   Format A — object with "params" key:
 *     { "params": [{ "track": "Bass", "device": "Wavetable", "param": "Filter Freq", "value": 0.75 }] }
 *   Format B — raw array:
 *     [{ "track": "Bass", "device": "Wavetable", "param": "Filter Freq", "value": 0.75 }]
 *
 *   track:  string (name) or int (index)
 *   device: string (name match)
 *   param:  string (name match)
 *   value:  float — if 0.0–1.0, scaled to the parameter's min/max range
 *
 * Outlet 0: status message (success/error per param, summary)
 *
 * Usage in patcher:
 *   [route params] -> [js param-applicator.js]
 */

autowatch = 1;
inlets = 1;
outlets = 1;

// ── Track resolution ────────────────────────────────────────────────

/**
 * Resolve a track reference to its index.
 * If `ref` is a number, use it directly.
 * If `ref` is a string, search by name (case-insensitive).
 * Returns { index: int, name: string } or null.
 */
function resolveTrack(ref) {
    var api = new LiveAPI("live_set");
    var trackIds = api.get("tracks");
    var trackCount = Math.floor(trackIds.length / 2);

    if (typeof ref === "number") {
        if (ref < 0 || ref >= trackCount) return null;
        var trackApi = new LiveAPI("live_set tracks " + ref);
        return { index: ref, name: trackApi.get("name").toString() };
    }

    // String — search by name
    var target = ref.toString().toLowerCase();
    for (var i = 0; i < trackCount; i++) {
        var trackApi = new LiveAPI("live_set tracks " + i);
        var name = trackApi.get("name").toString();
        if (name.toLowerCase() === target) {
            return { index: i, name: name };
        }
    }
    return null;
}

// ── Device resolution ───────────────────────────────────────────────

/**
 * Find a device on a track by name (case-insensitive).
 * Returns { index: int, name: string } or null.
 */
function resolveDevice(trackIdx, deviceName) {
    var trackApi = new LiveAPI("live_set tracks " + trackIdx);
    var deviceIds = trackApi.get("devices");
    var deviceCount = Math.floor(deviceIds.length / 2);

    var target = deviceName.toString().toLowerCase();
    for (var i = 0; i < deviceCount; i++) {
        var devApi = new LiveAPI("live_set tracks " + trackIdx + " devices " + i);
        var name = devApi.get("name").toString();
        if (name.toLowerCase() === target) {
            return { index: i, name: name };
        }
    }
    return null;
}

// ── Parameter resolution ────────────────────────────────────────────

/**
 * Find a parameter on a device by name (case-insensitive).
 * Returns { index: int, name: string, min: float, max: float } or null.
 */
function resolveParam(trackIdx, deviceIdx, paramName) {
    var devApi = new LiveAPI("live_set tracks " + trackIdx + " devices " + deviceIdx);
    var paramIds = devApi.get("parameters");
    var paramCount = Math.floor(paramIds.length / 2);

    var target = paramName.toString().toLowerCase();
    for (var i = 0; i < paramCount; i++) {
        var paramApi = new LiveAPI(
            "live_set tracks " + trackIdx +
            " devices " + deviceIdx +
            " parameters " + i
        );
        var name = paramApi.get("name").toString();
        if (name.toLowerCase() === target) {
            return {
                index: i,
                name: name,
                min: parseFloat(paramApi.get("min")),
                max: parseFloat(paramApi.get("max"))
            };
        }
    }
    return null;
}

// ── Value scaling ───────────────────────────────────────────────────

/**
 * If value is in [0.0, 1.0], treat it as a normalized value and
 * scale to the parameter's actual range.  Otherwise use it as-is
 * but clamp to [min, max].
 */
function scaleValue(value, min, max) {
    if (value >= 0.0 && value <= 1.0 && (min !== 0.0 || max !== 1.0)) {
        // Normalized — scale to actual range
        return min + value * (max - min);
    }
    // Absolute — clamp to range
    return Math.max(min, Math.min(max, value));
}

// ── Main handler ────────────────────────────────────────────────────

function anything() {
    // Reconstruct the full message (Max splits on spaces)
    var args = arrayfromargs(messagename, arguments);
    var jsonStr = args.join(" ");

    var changes;
    try {
        var parsed = JSON.parse(jsonStr);
        // Accept both { "params": [...] } and bare [...]
        if (Array.isArray(parsed)) {
            changes = parsed;
        } else if (parsed && Array.isArray(parsed.params)) {
            changes = parsed.params;
        } else {
            outlet(0, "error: expected params array or object with params key");
            return;
        }
    } catch (e) {
        outlet(0, "error: invalid param JSON — " + e.message);
        return;
    }

    if (changes.length === 0) {
        outlet(0, "error: empty params array");
        return;
    }

    var applied = 0;
    var errors = 0;

    for (var i = 0; i < changes.length; i++) {
        var c = changes[i];

        // Validate required fields
        if (c.track === undefined || c.device === undefined ||
            c.param === undefined || c.value === undefined) {
            post("param-applicator: skipping entry " + i + " — missing fields\n");
            outlet(0, "error [" + i + "]: missing track/device/param/value");
            errors++;
            continue;
        }

        try {
            // 1. Resolve track
            var track = resolveTrack(c.track);
            if (!track) {
                outlet(0, "error [" + i + "]: track not found — " + c.track);
                errors++;
                continue;
            }

            // 2. Resolve device
            var device = resolveDevice(track.index, c.device);
            if (!device) {
                outlet(0, "error [" + i + "]: device not found — " + c.device + " on " + track.name);
                errors++;
                continue;
            }

            // 3. Resolve parameter
            var param = resolveParam(track.index, device.index, c.param);
            if (!param) {
                outlet(0, "error [" + i + "]: param not found — " + c.param + " on " + track.name + "/" + device.name);
                errors++;
                continue;
            }

            // 4. Scale and set value
            var finalValue = scaleValue(c.value, param.min, param.max);

            var paramApi = new LiveAPI(
                "live_set tracks " + track.index +
                " devices " + device.index +
                " parameters " + param.index
            );
            paramApi.set("value", finalValue);

            post("param-applicator: set " + track.name + "/" + device.name + "/" + param.name + " = " + finalValue + "\n");
            applied++;

        } catch (e) {
            outlet(0, "error [" + i + "]: " + e.message);
            errors++;
        }
    }

    // Summary
    var summary = "applied " + applied + "/" + changes.length + " param changes";
    if (errors > 0) summary += " (" + errors + " errors)";
    outlet(0, summary);
}
