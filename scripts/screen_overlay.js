// screen_overlay.js: CS:GO's env_screenoverlay for CS2 (the entity is gone there), shown through a custom_hud_layout.
//
// What CS:GO did (CS:GO server.dll CEnvScreenOverlay inputs and client.dll C_EnvScreenOverlay, read from the binaries 2026-11-22):
//   - every player sees it (it is drawn over each client's view, dead players and spectators too)
//   - StartOverlays: nothing if OverlayName1 is empty. Otherwise this one becomes active and every other active env_screenoverlay is
//     switched off (one at a time), and it shows its overlays from the current one (the first, unless SwitchOverlay chose another):
//     each for its OverlayTime (default 1), then the next; after an empty name, or the 10th, the last one shown stays on the screen
//     until StopOverlays (the timing stops, the material is not cleared; corrected 2026-11-23 after a check in CS:GO showed it).
//     An OverlayTime of -1 shows that one until stopped.
//   - StopOverlays: it stops showing
//   - SwitchOverlay <n>: overlay n (1-10) is the current one from now on (nothing if that name is empty); an active one starts it now
//   - a name whose material does not exist stops it showing
//   - map entities are new every round: a new round starts with none showing
// CS2: the converter writes one Panorama layout per map with a panel per overlay material (its texture as a PNG, its $alpha as the
// panel's opacity) and a custom_hud_layout for it; this script shows and hides the panels. A material Panorama can't draw (a
// refract / screen-warp shader) has no panel and behaves like a missing one.
//
// SETUP: vmap_asset_slop_import.py (SCREEN_OVERLAYS) gives each env_screenoverlay a group number (so_g) per name and turns
//   StartOverlays / StopOverlays / SwitchOverlay <n>  into  RunScriptInput so_<g>_start / so_<g>_stop / so_<g>_sw<n>  on this
// point_script. The data block holds, per map, the custom_hud_layout's name and each entity's overlays.
//
// Written against Valve's point_script.d.ts. Not run in CS2.
//
// Console (sv_cheats 1): so_list, so_log 0|1.

import { Instance, Entities, CustomHudLayout } from "cs_script/point_script";

/** map -> { hud: custom_hud_layout name, e: [{ g: group, n: name, s: [[panel id ("" = none / can't draw), time, named 0|1] x up to 10]] }] } */
const DATA = /*SO_DATA_BEGIN*/{}/*SO_DATA_END*/;

let LOG = 0;
const say = (s) => Instance.Msg(`[screen_overlay] ${s}`);

function mapData() {
    let name = "";
    try {
        name = String(Instance.GetMapName() || "");
    } catch (e) {
        name = "";
    }
    name = name.replace(/\\/g, "/").split("/").pop().replace(/\.vpk$|\.vmap$/i, "").toLowerCase();
    if (DATA[name]) return DATA[name];
    const keys = Object.keys(DATA);
    return keys.length === 1 ? DATA[keys[0]] : { hud: "", e: [] };
}
const MD = mapData();
const ENTS = MD.e || [];

function hud() {
    const x = Entities.FindByName(MD.hud)[0];
    return x instanceof CustomHudLayout ? x : undefined;
}

let active = undefined;      // the entry showing (CS:GO's one active env_screenoverlay)
let slot = -1;               // its current overlay (index into s)
let until = -1;              // when it moves on (-1: never)
let shown = "";              // the panel showing
const desired = new Map();   // entry -> the overlay StartOverlays starts from (SwitchOverlay)

function show(pid) {
    if (pid === shown) return;
    const h = hud();
    if (shown && h) h.SetHasClass(shown, "Hidden", true);
    if (pid && h) h.SetHasClass(pid, "Hidden", false);
    if (!h && (pid || shown)) say(`custom_hud_layout "${MD.hud}" not found`);
    shown = pid;
}

/** C_EnvScreenOverlay::StartCurrentOverlay */
function startSlot(E, i) {
    slot = i;
    const o = i < 10 ? E.s[i] : undefined;
    if (!o || !o[2]) {
        // past the last name: CS:GO's StartCurrentOverlay only stops the timing ("Hit the end of our overlays, so stop") and leaves the
        // overlay it was showing on the screen until StopOverlays (mg_mario_party's portraits: OverlayTime 1, StopOverlays at 3.5 s, shown
        // the whole 3.5 s in CS:GO)
        until = -1;
        if (LOG) say(`${E.n}: no overlay ${i + 1}: the last one stays until StopOverlays`);
        return;
    }
    if (!o[0]) {
        if (LOG) say(`${E.n}: overlay ${i + 1} can't be drawn: stopped`);
        show("");
        until = -1;
        slot = -1;
        return;
    }
    show(o[0]);
    until = o[1] === -1 ? -1 : Instance.GetGameTime() + o[1];
    if (LOG) say(`${E.n}: overlay ${i + 1} (${o[0]}) for ${o[1] === -1 ? "ever" : o[1] + " s"}`);
}

function start(E) {
    if (!E.s[0] || !E.s[0][2]) {
        say(`${E.n} has no overlays to display`);
        return;
    }
    active = E;
    startSlot(E, desired.get(E) || 0);
}

function stop(E) {
    if (active !== E) return;
    active = undefined;
    show("");
    until = -1;
    slot = -1;
}

function switchTo(E, n) {
    const i = Math.abs(n - 1);
    if (i > 9 || !E.s[i] || !E.s[i][2]) {
        say(`${E.n}: SwitchOverlay ${n}: no overlay there`);
        return;
    }
    desired.set(E, i);
    if (active === E) startSlot(E, i);
}

function reset(why) {
    active = undefined;
    desired.clear();
    until = -1;
    slot = -1;
    shown = "";
    const h = hud();
    if (h) for (const E of ENTS) for (const o of E.s) if (o && o[0]) h.SetHasClass(o[0], "Hidden", true);
    if (LOG) say(`${why}: none showing`);
}

Instance.SetThink(() => {
    const now = Instance.GetGameTime();
    if (active && slot >= 0 && until >= 0 && now >= until) startSlot(active, slot + 1);
    Instance.SetNextThink(now);
});

const groups = new Map();
for (const E of ENTS) {
    if (!groups.has(E.g)) groups.set(E.g, []);
    groups.get(E.g).push(E);
}
for (const [g, list] of groups) {
    // an input reaches every env_screenoverlay of the name, in map order (the last one started stays active, as in CS:GO)
    Instance.OnScriptInput(`so_${g}_start`, () => list.forEach(start));
    Instance.OnScriptInput(`so_${g}_stop`, () => list.forEach(stop));
    for (let n = 1; n <= 10; n++) Instance.OnScriptInput(`so_${g}_sw${n}`, () => list.forEach((E) => switchTo(E, n)));
}

Instance.OnActivate(() => {
    reset("on");
    Instance.SetNextThink(Instance.GetGameTime());
});
Instance.OnRoundStart(() => reset("round start"));
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => { reset("reloaded"); Instance.SetNextThink(Instance.GetGameTime()); } });

if (typeof Instance.RegisterCheatCommand === "function") {
    Instance.RegisterCheatCommand("so_list", () => {
        for (const E of ENTS) say(`so_${E.g} ${E.n}: ${E.s.map((o, i) => o && o[2] ? `${i + 1}:${o[0] || "(none)"}/${o[1]}` : "").filter(Boolean).join(" ")}`
            + (active === E ? ` <- active, overlay ${slot + 1}` : ""));
    });
    Instance.RegisterCheatCommand("so_log", (a) => {
        LOG = Number(String(a === undefined ? "" : a).trim()) ? 1 : 0;
        say(`log ${LOG}`);
    });
}
