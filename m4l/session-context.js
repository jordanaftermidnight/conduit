/**
 * session-context.js
 * 
 * Runs inside a [js] object in the M4L patcher.
 * Queries the Live Object Model (LOM) to gather current session state,
 * then outputs it as a JSON string for the node.script bridge.
 * 
 * Inlet 0: bang to refresh context
 * Outlet 0: JSON string of session context
 * 
 * Wire this outlet → [prepend session] → [node.script conduit-bridge.js]
 */

autowatch = 1;
inlets = 1;
outlets = 1;

function bang() {
    var api = new LiveAPI("live_set");

    var ctx = {};

    // BPM
    try {
        ctx.bpm = parseFloat(api.get("tempo"));
    } catch (e) {}

    // Time signature
    try {
        var num = parseInt(api.get("signature_numerator"));
        var den = parseInt(api.get("signature_denominator"));
        ctx.time_signature = num + "/" + den;
    } catch (e) {}

    // Transport state
    try {
        ctx.playing = parseInt(api.get("is_playing")) === 1;
    } catch (e) {}

    // Song position
    try {
        ctx.song_time = parseFloat(api.get("current_song_time"));
    } catch (e) {}

    // Track names
    try {
        var trackIds = api.get("tracks");
        var names = [];
        for (var i = 0; i < trackIds.length; i++) {
            if (trackIds[i] === "id") continue;
            var trackApi = new LiveAPI("live_set tracks " + Math.floor(i / 2));
            var name = trackApi.get("name").toString();
            if (name && name !== "0") {
                names.push(name);
            }
        }
        if (names.length > 0) {
            ctx.track_names = names;
        }
    } catch (e) {}

    // Selected track
    try {
        var selectedTrack = new LiveAPI("live_set view selected_track");
        ctx.selected_track = selectedTrack.get("name").toString();
    } catch (e) {}

    // Groove amount
    try {
        ctx.groove = parseFloat(api.get("groove_amount"));
    } catch (e) {}

    outlet(0, JSON.stringify(ctx));
}

// Note: Do NOT use loadbang here — the Live API is not ready yet.
// The patcher uses [live.thisdevice] → delay → metro to trigger bang()
// only after the API is initialized.
