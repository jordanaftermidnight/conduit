#!/usr/bin/env python3
"""
build-device.py — Generate Conduit.maxpat for Max for Live

Architecture:
    node.script has 1 message outlet (outlet 0). All messages use a
    type prefix: "text ...", "midi ...", "params ...", "status ...".
    A single [route text midi params status] splits them.

    A [gate] blocks all control messages (cmd, session) for 2 seconds
    on startup, giving node.script time to initialize. The gate opens
    after delay, and the metro starts simultaneously.

Usage:
    python build-device.py
"""

import json
import struct
import sys
import os
import time

_box_counter = 0


def next_id(prefix="obj"):
    global _box_counter
    _box_counter += 1
    return f"{prefix}-{_box_counter:04d}"


PASSTHROUGH_KEYS = (
    "fontsize", "fontname", "fontface", "bgcolor", "textcolor", "frgb",
    "lines", "autoscroll", "readonly", "mode", "parameter_enable",
    "varname", "saved_attribute_attributes", "keymode", "tabmode",
    "texton",
)


def make_box(maxclass, text="", rect=(0, 0, 100, 22), **kwargs):
    box_id = next_id()
    box = {
        "box": {
            "maxclass": maxclass,
            "id": box_id,
            "patching_rect": list(rect),
            "numinlets": kwargs.get("numinlets", 1),
            "numoutlets": kwargs.get("numoutlets", 1),
            "outlettype": kwargs.get("outlettype", [""]),
        }
    }
    if text:
        box["box"]["text"] = text
    if kwargs.get("presentation"):
        box["box"]["presentation"] = 1
        if kwargs.get("presentation_rect"):
            box["box"]["presentation_rect"] = list(kwargs["presentation_rect"])
    for key in PASSTHROUGH_KEYS:
        if key in kwargs:
            box["box"][key] = kwargs[key]
    return box_id, box


def make_newobj(text, rect=(0, 0, 100, 22), **kwargs):
    return make_box("newobj", text=text, rect=rect, **kwargs)


def make_message(text, rect=(0, 0, 100, 22), **kwargs):
    return make_box("message", text=text, rect=rect, **kwargs)


def make_comment(text, rect=(0, 0, 100, 22), **kwargs):
    return make_box("comment", text=text, rect=rect,
                    numoutlets=0, outlettype=[], numinlets=1, **kwargs)


def pl(src_id, src_outlet, dst_id, dst_inlet):
    return {
        "patchline": {
            "source": [src_id, src_outlet],
            "destination": [dst_id, dst_inlet],
        }
    }


# ── Layout ───────────────────────────────────────────────────────────
# Standard M4L device view is ~170px tall. openrect constrains visible area.
DEVICE_W = 600
DEVICE_H = 170
MARGIN = 4
GAP = 2
TITLE_H = 18
PROMPT_H = 22
STATUS_H = 20

TITLE_Y = MARGIN
PROMPT_Y = TITLE_Y + TITLE_H + GAP
RESPONSE_Y = PROMPT_Y + PROMPT_H + GAP
BOTTOM_Y = DEVICE_H - MARGIN - STATUS_H
RESPONSE_H = BOTTOM_Y - GAP - RESPONSE_Y

# Button bar: 5 buttons + status
BTN_W = 50
BTN_GAP = 3


def build_patcher():
    boxes = []
    lines = []

    LX, CX, RX = 30, 230, 480
    Y0 = 30

    # ── PRESENTATION OBJECTS ─────────────────────────────────────

    title_id, b = make_comment(
        "CONDUIT", rect=(LX, Y0, 200, 24),
        fontsize=11, fontface=1,
        presentation=True,
        presentation_rect=(MARGIN, TITLE_Y, 80, TITLE_H))
    boxes.append(b)

    mode_id, b = make_box(
        "live.menu", rect=(RX, Y0, 100, 20),
        numinlets=1, numoutlets=3,
        outlettype=["", "", "float"],
        parameter_enable=1, varname="mode_menu",
        presentation=True,
        presentation_rect=(DEVICE_W - MARGIN - 190, TITLE_Y, 90, TITLE_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Mode",
                "parameter_shortname": "Mode",
                "parameter_type": 2,
                "parameter_enum": ["chat", "generate"],
                "parameter_initial_enable": 1,
                "parameter_initial": [0],
            }
        })
    boxes.append(b)

    genres = ["techno", "house", "dnb", "dubstep",
              "hiphop", "ambient", "idm", "trance"]
    genre_id, b = make_box(
        "live.menu", rect=(RX + 110, Y0, 100, 20),
        numinlets=1, numoutlets=3,
        outlettype=["", "", "float"],
        parameter_enable=1, varname="genre_menu",
        presentation=True,
        presentation_rect=(DEVICE_W - MARGIN - 90, TITLE_Y, 90, TITLE_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Genre",
                "parameter_shortname": "Genre",
                "parameter_type": 2,
                "parameter_enum": genres,
                "parameter_initial_enable": 1,
                "parameter_initial": [0],
            }
        })
    boxes.append(b)

    prompt_id, b = make_box(
        "textedit", rect=(LX, Y0 + 50, 180, 22),
        numinlets=1, numoutlets=4,
        outlettype=["", "int", "", ""],
        lines=1, autoscroll=1, keymode=1, tabmode=0,
        presentation=True,
        presentation_rect=(MARGIN, PROMPT_Y, DEVICE_W - 2 * MARGIN, PROMPT_H))
    boxes.append(b)

    resp_id, b = make_box(
        "textedit", rect=(LX, Y0 + 270, 150, 22),
        numinlets=1, numoutlets=4,
        outlettype=["", "int", "", ""],
        lines=5, autoscroll=1, readonly=1,
        presentation=True,
        presentation_rect=(MARGIN, RESPONSE_Y, DEVICE_W - 2 * MARGIN, RESPONSE_H))
    boxes.append(b)

    btn_x = MARGIN  # running x position for buttons

    reset_id, b = make_box(
        "live.text", text="Reset", rect=(LX, Y0 + 430, 80, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""],
        mode=0, parameter_enable=1, varname="reset_button",
        texton="Reset",
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, BTN_W, STATUS_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Reset",
                "parameter_shortname": "Reset",
                "parameter_type": 2,
                "parameter_enum": ["Reset", "Reset"],
            }
        })
    boxes.append(b)
    btn_x += BTN_W + BTN_GAP

    gen_btn_id, b = make_box(
        "live.text", text="Generate", rect=(LX + 100, Y0 + 430, 80, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""],
        mode=0, parameter_enable=1, varname="generate_button",
        texton="Gen",
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, BTN_W, STATUS_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Generate",
                "parameter_shortname": "Generate",
                "parameter_type": 2,
                "parameter_enum": ["Generate", "Generate"],
            }
        })
    boxes.append(b)
    btn_x += BTN_W + BTN_GAP

    paste_btn_id, b = make_box(
        "live.text", text="Paste", rect=(LX + 200, Y0 + 430, 80, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""],
        mode=0, parameter_enable=1, varname="paste_button",
        texton="Paste",
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, BTN_W, STATUS_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Paste",
                "parameter_shortname": "Paste",
                "parameter_type": 2,
                "parameter_enum": ["Paste", "Paste"],
            }
        })
    boxes.append(b)
    btn_x += BTN_W + BTN_GAP

    undo_btn_id, b = make_box(
        "live.text", text="Undo", rect=(LX + 300, Y0 + 430, 80, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""],
        mode=0, parameter_enable=1, varname="undo_button",
        texton="Undo",
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, BTN_W, STATUS_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Undo",
                "parameter_shortname": "Undo",
                "parameter_type": 2,
                "parameter_enum": ["Undo", "Undo"],
            }
        })
    boxes.append(b)
    btn_x += BTN_W + BTN_GAP

    clear_btn_id, b = make_box(
        "live.text", text="Clear", rect=(LX + 400, Y0 + 430, 80, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""],
        mode=0, parameter_enable=1, varname="clear_button",
        texton="Clear",
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, BTN_W, STATUS_H),
        saved_attribute_attributes={
            "valueof": {
                "parameter_longname": "Conduit Clear",
                "parameter_shortname": "Clear",
                "parameter_type": 2,
                "parameter_enum": ["Clear", "Clear"],
            }
        })
    boxes.append(b)
    btn_x += BTN_W + BTN_GAP

    status_id, b = make_box(
        "textedit", rect=(RX + 60, Y0 + 280, 200, 22),
        numinlets=1, numoutlets=4,
        outlettype=["", "int", "", ""],
        lines=1, autoscroll=0, readonly=1,
        presentation=True,
        presentation_rect=(btn_x, BOTTOM_Y, DEVICE_W - MARGIN - btn_x, STATUS_H))
    boxes.append(b)

    # ── CORE OBJECTS ─────────────────────────────────────────────

    pp_id, b = make_newobj("prepend prompt", rect=(LX, Y0 + 110, 120, 22))
    boxes.append(b)

    pg_id, b = make_newobj("prepend generate", rect=(LX + 140, Y0 + 110, 130, 22))
    boxes.append(b)

    # gate 2 1: routes textedit output between prompt (outlet 0) and generate (outlet 1)
    # default output is 1 (prompt path)
    prompt_gate_id, b = make_newobj(
        "gate 2 1", rect=(LX + 60, Y0 + 85, 60, 22),
        numinlets=2, numoutlets=2, outlettype=["", ""])
    boxes.append(b)

    # t 1 b 2: Generate button trigger
    # outlet 2 (fires first):  int 2 → prompt gate control (switch to generate)
    # outlet 1 (fires second): bang → textedit (output content)
    # outlet 0 (fires third):  int 1 → prompt gate control (reset to prompt)
    gen_trig_id, b = make_newobj(
        "t 1 b 2", rect=(LX + 100, Y0 + 460, 60, 22),
        numoutlets=3, outlettype=["int", "bang", "int"])
    boxes.append(b)

    node_id, b = make_newobj(
        "node.script conduit-bridge.js @autostart 1",
        rect=(CX, Y0 + 150, 260, 22),
        numinlets=1, numoutlets=2,
        outlettype=["", ""])
    boxes.append(b)

    router_id, b = make_newobj(
        "route text midi params status undo",
        rect=(CX, Y0 + 210, 250, 22),
        numoutlets=6,
        outlettype=["", "", "", "", "", ""])
    boxes.append(b)

    # ── APPLICATORS + STATUS ─────────────────────────────────────

    mjs_id, b = make_newobj("js midi-applicator.js", rect=(LX + 180, Y0 + 270, 150, 22))
    boxes.append(b)

    pjs_id, b = make_newobj("js param-applicator.js", rect=(LX + 350, Y0 + 270, 150, 22))
    boxes.append(b)

    # prepend set for response textedit (textedit needs "set <text>" to display)
    ps_resp_id, b = make_newobj("prepend set", rect=(LX, Y0 + 250, 100, 22))
    boxes.append(b)

    ps_id, b = make_newobj("prepend set", rect=(RX + 60, Y0 + 250, 100, 22))
    boxes.append(b)

    pm_id, b = make_newobj("print conduit-midi", rect=(LX + 180, Y0 + 330, 140, 22),
                           numoutlets=0, outlettype=[])
    boxes.append(b)

    pp2_id, b = make_newobj("print conduit-param", rect=(LX + 350, Y0 + 330, 140, 22),
                            numoutlets=0, outlettype=[])
    boxes.append(b)

    # prepend set for midi-applicator status → status textedit
    ps_midi_status_id, b = make_newobj("prepend set", rect=(LX + 180, Y0 + 310, 100, 22))
    boxes.append(b)

    # ── STARTUP GATE ─────────────────────────────────────────────
    # gate blocks all control messages for 2 seconds while node.script loads
    #
    # loadbang → delay 2000 → t 1 1
    #                          |   |
    #                          |   → metro 5000 (starts session polling)
    #                          → gate (opens, allowing cmd/session through)
    #
    # All cmd/session messages go through gate → node.script
    # Prompt goes directly to node.script (user won't type in 5s)

    gate_id, b = make_newobj(
        "gate 1", rect=(CX + 60, Y0 + 120, 50, 22),
        numinlets=2, numoutlets=1)
    boxes.append(b)

    lb_id, b = make_newobj("live.thisdevice", rect=(RX, Y0 + 50, 110, 22),
                           numoutlets=2, outlettype=["", ""])
    boxes.append(b)

    delay_id, b = make_newobj("delay 2000", rect=(RX, Y0 + 75, 80, 22),
                              numinlets=2)
    boxes.append(b)

    # t 1 1: right-to-left output. outlet 1 fires first (→ metro), outlet 0 fires second (→ gate)
    t11_id, b = make_newobj("t 1 1", rect=(RX, Y0 + 100, 45, 22),
                            numoutlets=2, outlettype=["int", "int"])
    boxes.append(b)

    metro_id, b = make_newobj("metro 5000", rect=(RX, Y0 + 130, 80, 22),
                              numinlets=2)
    boxes.append(b)

    sjs_id, b = make_newobj("js session-context.js", rect=(RX, Y0 + 157, 160, 22))
    boxes.append(b)

    tosym_id, b = make_newobj("tosymbol", rect=(RX, Y0 + 170, 80, 22))
    boxes.append(b)

    psess_id, b = make_newobj("prepend session", rect=(RX, Y0 + 184, 120, 22))
    boxes.append(b)

    # ── COMMAND ROUTING ──────────────────────────────────────────

    pm_mode_id, b = make_newobj("prepend cmd mode", rect=(RX, Y0 + 25, 130, 22))
    boxes.append(b)

    pm_genre_id, b = make_newobj("prepend cmd genre", rect=(RX + 140, Y0 + 25, 130, 22))
    boxes.append(b)

    mr_id, b = make_message("cmd reset", rect=(LX, Y0 + 460, 100, 22))
    boxes.append(b)

    mp_id, b = make_message("cmd paste", rect=(LX + 200, Y0 + 460, 100, 22))
    boxes.append(b)

    mu_id, b = make_message("cmd undo", rect=(LX + 300, Y0 + 460, 100, 22))
    boxes.append(b)

    mc_id, b = make_message("cmd clear", rect=(LX + 400, Y0 + 460, 100, 22))
    boxes.append(b)

    # Initial status text (so the status bar isn't a mysterious empty box)
    init_status_id, b = make_message("set initializing...", rect=(RX + 60, Y0 + 230, 140, 22))
    boxes.append(b)

    # ══════════════════════════════════════════════════════════════
    # PATCHLINES
    # ══════════════════════════════════════════════════════════════

    # -- Prompt / Generate: textedit → gate → prepend prompt|generate → node.script --
    # textedit output → gate data inlet (right inlet)
    lines.append(pl(prompt_id, 0, prompt_gate_id, 1))
    # gate outlet 0 (default) → prepend prompt → node.script
    lines.append(pl(prompt_gate_id, 0, pp_id, 0))
    lines.append(pl(pp_id, 0, node_id, 0))
    # gate outlet 1 → prepend generate → node.script
    lines.append(pl(prompt_gate_id, 1, pg_id, 0))
    lines.append(pl(pg_id, 0, node_id, 0))

    # -- Generate button → trigger → gate control + textedit bang --
    lines.append(pl(gen_btn_id, 0, gen_trig_id, 0))
    # outlet 2 (fires first): int 2 → gate control (switch to generate path)
    lines.append(pl(gen_trig_id, 2, prompt_gate_id, 0))
    # outlet 1 (fires second): bang → textedit (causes content output)
    lines.append(pl(gen_trig_id, 1, prompt_id, 0))
    # outlet 0 (fires third): int 1 → gate control (reset to prompt path)
    lines.append(pl(gen_trig_id, 0, prompt_gate_id, 0))

    # -- node.script outlet 0 → router --
    lines.append(pl(node_id, 0, router_id, 0))

    # -- Router outlets → destinations --
    lines.append(pl(router_id, 0, ps_resp_id, 0))  # text → prepend set
    lines.append(pl(ps_resp_id, 0, resp_id, 0))    # → response textedit
    lines.append(pl(router_id, 1, mjs_id, 0))      # midi → applicator
    lines.append(pl(router_id, 2, pjs_id, 0))      # params → applicator
    lines.append(pl(router_id, 3, ps_id, 0))       # status → prepend set
    lines.append(pl(ps_id, 0, status_id, 0))        # → status display
    lines.append(pl(router_id, 4, mjs_id, 0))      # undo → midi-applicator

    # -- Applicator status → status display --
    lines.append(pl(mjs_id, 0, ps_midi_status_id, 0))
    lines.append(pl(ps_midi_status_id, 0, status_id, 0))
    # -- Applicator debug --
    lines.append(pl(mjs_id, 0, pm_id, 0))
    lines.append(pl(pjs_id, 0, pp2_id, 0))

    # -- Initial status text: loadbang → "set initializing..." → status textedit --
    lines.append(pl(lb_id, 0, init_status_id, 0))
    lines.append(pl(init_status_id, 0, status_id, 0))

    # -- Startup timing: loadbang → delay 2000 → t 1 1 --
    lines.append(pl(lb_id, 0, delay_id, 0))
    lines.append(pl(delay_id, 0, t11_id, 0))

    # -- t 1 1 outlet 0 → gate (open it) --
    lines.append(pl(t11_id, 0, gate_id, 0))         # left inlet = control

    # -- t 1 1 outlet 1 → metro (start it) --
    lines.append(pl(t11_id, 1, metro_id, 0))

    # -- Gate outlet → node.script --
    lines.append(pl(gate_id, 0, node_id, 0))

    # -- Session context: metro → js → tosymbol → prepend → gate right inlet --
    lines.append(pl(metro_id, 0, sjs_id, 0))
    lines.append(pl(sjs_id, 0, tosym_id, 0))
    lines.append(pl(tosym_id, 0, psess_id, 0))
    lines.append(pl(psess_id, 0, gate_id, 1))       # → gate data inlet

    # -- Mode: menu → prepend → gate right inlet --
    lines.append(pl(mode_id, 1, pm_mode_id, 0))
    lines.append(pl(pm_mode_id, 0, gate_id, 1))     # → gate data inlet

    # -- Genre: menu → prepend → gate right inlet --
    lines.append(pl(genre_id, 1, pm_genre_id, 0))
    lines.append(pl(pm_genre_id, 0, gate_id, 1))    # → gate data inlet

    # -- Reset: button → message → gate right inlet --
    lines.append(pl(reset_id, 0, mr_id, 0))
    lines.append(pl(mr_id, 0, gate_id, 1))          # → gate data inlet

    # -- Paste: button → message → gate right inlet --
    lines.append(pl(paste_btn_id, 0, mp_id, 0))
    lines.append(pl(mp_id, 0, gate_id, 1))          # → gate data inlet

    # -- Undo: button → message → gate right inlet --
    lines.append(pl(undo_btn_id, 0, mu_id, 0))
    lines.append(pl(mu_id, 0, gate_id, 1))          # → gate data inlet

    # -- Clear: button → message → gate right inlet --
    lines.append(pl(clear_btn_id, 0, mc_id, 0))
    lines.append(pl(mc_id, 0, gate_id, 1))          # → gate data inlet

    # ══════════════════════════════════════════════════════════════
    return {
        "patcher": {
            "fileversion": 1,
            "appversion": {
                "major": 8, "minor": 6, "revision": 0,
                "architecture": "x64", "modernui": 1,
            },
            "classnamespace": "box",
            "rect": [100, 100, 900, 700],
            "openrect": [0.0, 0.0, float(DEVICE_W), float(DEVICE_H)],
            "openinpresentation": 1,
            "default_fontsize": 12.0,
            "default_fontface": 0,
            "default_fontname": "Arial",
            "gridonopen": 1,
            "gridsize": [15.0, 15.0],
            "gridsnaponopen": 1,
            "objectsnaponopen": 1,
            "statusbarvisible": 2,
            "toolbarvisible": 1,
            "lefttoolbarpinned": 0,
            "toptoolbarpinned": 0,
            "righttoolbarpinned": 0,
            "bottomtoolbarpinned": 0,
            "toolbars_unpinned_last_save": 0,
            "tallnewobj": 0,
            "boxanimatetime": 200,
            "enablehscroll": 1,
            "enablevscroll": 1,
            "devicewidth": float(DEVICE_W),
            "description": "Conduit — AI MIDI generation for Ableton Live",
            "digest": "LLM-powered MIDI generation via local or cloud models",
            "tags": "MIDI AI LLM generation",
            "style": "",
            "subpatcher_template": "",
            "assistshowspatchername": 0,
            "boxes": boxes,
            "lines": lines,
            "parameters": {
                "parameterbanks": {
                    "0": {
                        "index": 0,
                        "name": "Conduit",
                        "parameters": [
                            "Conduit Mode",
                            "Conduit Genre",
                            "Conduit Reset",
                            "Conduit Generate",
                            "Conduit Paste",
                            "Conduit Undo",
                            "Conduit Clear",
                        ],
                    }
                },
                "inherited_shortname": 1,
            },
            "dependency_cache": [
                {"name": "conduit-bridge.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
                {"name": "session-context.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
                {"name": "midi-applicator.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
                {"name": "param-applicator.js", "bootpath": ".", "type": "TEXT", "implicit": 1},
            ],
            "autosave": 0,
        }
    }


def wrap_ampf(json_bytes: bytes, device_type: str = "midi",
              filename: str = "Conduit.amxd",
              resources: list = None) -> bytes:
    """Wrap JSON patcher data in the AMPF binary container that Ableton expects.

    The AMPF (Ableton Max Patcher Format) container wraps Max JSON patchers
    with binary header and a dlst resource directory.  The dlst section
    straddles the content boundary: its headers (dlst tag/size + first dire
    tag/size = 16 bytes) sit at the END of the content section, while the
    dire payload follows AFTER the content boundary.

    Structure (matching real .amxd files byte-for-byte):
      [header 32B]   ampf magic + version + device-type chunk + meta
      [mx@c 16B]     Max context: content-size pointer
      [JSON]         patcher JSON data
      [dlst+dire hdr 16B]  directory list header (inside content boundary)
      [dire payload]       resource entry fields (outside content boundary)
    """
    # Device-type chunk ID: mmmm=MIDI, iiii=instrument, aaaa=audio
    chunk_ids = {"midi": b"mmmm", "instrument": b"iiii", "audio": b"aaaa"}
    chunk_id = chunk_ids.get(device_type, b"mmmm")

    if resources is None:
        resources = []

    json_size = len(json_bytes)

    # HFS+ timestamp (seconds since January 1, 1904)
    HFS_EPOCH_OFFSET = 2082844800
    mdat_ts = (int(time.time()) + HFS_EPOCH_OFFSET) & 0xFFFFFFFF

    def _pad4(b: bytes) -> bytes:
        while len(b) % 4 != 0:
            b += b"\x00"
        return b

    def _make_dire(ftype: bytes, fname: str, sz: int, offset: int,
                   flags: int = 0) -> bytes:
        """Build a dire entry: tag(4)+size(4)+payload of sub-chunks."""
        fn = _pad4(fname.encode("ascii") + b"\x00")
        payload = b""
        payload += b"type" + struct.pack(">I", 12) + ftype
        payload += b"fnam" + struct.pack(">I", 8 + len(fn)) + fn
        payload += b"sz32" + struct.pack(">I", 12) + struct.pack(">I", sz)
        payload += b"of32" + struct.pack(">I", 12) + struct.pack(">I", offset)
        payload += b"vers" + struct.pack(">I", 12) + struct.pack(">I", 0)
        payload += b"flag" + struct.pack(">I", 12) + struct.pack(">I", flags)
        payload += b"mdat" + struct.pack(">I", 12) + struct.pack(">I", mdat_ts)
        return payload

    # ── Build content: main JSON + embedded resources ──
    embedded_data = json_bytes
    # Track offsets: of32 is measured from patch payload start (after header)
    # mx@c context is 16 bytes, so main JSON starts at offset 16
    resource_entries = []  # (dire_payload_bytes,) for each resource

    # Main patcher entry
    main_dire = _make_dire(b"JSON", filename, json_size, 0x10, flags=0x11)
    resource_entries.append(main_dire)

    # Embedded JS/TEXT resources
    current_offset = 0x10 + json_size  # after mx@c + main JSON
    for res_name, res_data in resources:
        embedded_data += res_data
        res_dire = _make_dire(b"TEXT", res_name, len(res_data), current_offset)
        resource_entries.append(res_dire)
        current_offset += len(res_data)

    # ── Build dlst directory ──
    # All dire entries (each wrapped with dire tag+size)
    all_dires = b""
    for dp in resource_entries:
        dire_chunk = b"dire" + struct.pack(">I", 8 + len(dp)) + dp
        all_dires += dire_chunk

    dlst_total = 8 + len(all_dires)  # dlst tag(4) + size(4) + all dires

    # The dlst header + first dire header (16 bytes) go INSIDE the content
    # boundary; the rest of the dire payloads go AFTER the boundary.
    # Split: first 16 bytes of dlst+first_dire into content, rest after.
    dlst_bytes = b"dlst" + struct.pack(">I", dlst_total) + all_dires
    dlst_in_content = dlst_bytes[:16]
    dlst_after_content = dlst_bytes[16:]

    content = embedded_data + dlst_in_content
    content_size = len(content)

    # ── mx@c context (16 bytes) ──
    mx_context = b"mx@c"
    mx_context += struct.pack(">I", 0x10)          # context header = 16
    mx_context += struct.pack(">I", 0)             # reserved
    mx_context += struct.pack(">I", content_size)  # content size

    # ── Patch payload: mx@c + content + dlst remainder ──
    patch_payload = mx_context + content + dlst_after_content

    # ── AMPF header (32 bytes) ──
    header = b"ampf"
    header += struct.pack("<I", 4)              # version 4 (LE)
    header += chunk_id                          # device type chunk
    header += b"meta"
    header += struct.pack("<I", 4)              # meta payload size (LE)
    header += struct.pack("<I", 7)              # meta value
    header += b"ptch"
    header += struct.pack("<I", len(patch_payload))  # patch payload size (LE)

    return header + patch_payload


def main():
    output = "Conduit.amxd"
    if len(sys.argv) > 1 and sys.argv[1] == "--output" and len(sys.argv) > 2:
        output = sys.argv[2]

    patcher = build_patcher()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Always save .maxpat (plain JSON) for editing in Max
    maxpat_path = os.path.join(script_dir, "Conduit.maxpat")
    json_str = json.dumps(patcher, indent=2)
    with open(maxpat_path, "w") as f:
        f.write(json_str)

    # Save .amxd with AMPF binary wrapper for Ableton
    # [js] objects need files embedded in the AMPF container.
    # node.script needs files on disk (installed to Max Packages by package-device.sh).
    amxd_path = os.path.join(script_dir, output)
    json_bytes = json_str.encode("utf-8")

    # Embed [js] dependencies in AMPF (session-context, midi-applicator, param-applicator)
    js_embed = []
    for js_name in ["session-context.js", "midi-applicator.js", "param-applicator.js"]:
        js_path = os.path.join(script_dir, js_name)
        if os.path.exists(js_path):
            with open(js_path, "rb") as jf:
                js_embed.append((js_name, jf.read()))
            print(f"  Embedded: {js_name}")
        else:
            print(f"  WARNING: {js_name} not found, skipping embed")

    amxd_data = wrap_ampf(json_bytes, device_type="midi", resources=js_embed)
    with open(amxd_path, "wb") as f:
        f.write(amxd_data)

    nb = len(patcher["patcher"]["boxes"])
    nl = len(patcher["patcher"]["lines"])
    print(f"Generated {amxd_path}  ({len(amxd_data)} bytes, AMPF container)")
    print(f"  {nb} objects, {nl} connections")
    print(f"  Device: {DEVICE_W}x{DEVICE_H}")
    print(f"  Also saved: {maxpat_path}  (plain JSON for Max editor)")
    print()
    print("  node.script → outlet 0 → [route text midi params status undo]")
    print("  [gate] blocks cmd/session for 2s while node.script loads")
    print("  prompt goes direct (not gated)")


if __name__ == "__main__":
    main()
