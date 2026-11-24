// softbarrier.js: CS:GO's trigger_softbarrier for CS2 (CS2 has no trigger_softbarrier).
//
// What CS:GO did (game/server/triggers.cpp CTriggerSoftBarrier): a trigger that gently stops players moving against its push direction,
// so they can't go back through it, without feeling an invisible wall. Moving with the push direction they pass freely.
//   - at spawn: the push direction is pushdir (angles) in world space. Of the trigger's brush faces, the "front" is the one whose outward
//     normal points most along the push direction, the "back" the one pointing most against it
//   - every tick a live player (not in noclip, not parented) touches it: r = how far from the front face toward the back face the
//     player's origin is (0 at the front face, 1 at the back face), bent by Bias(r, 0.2) = r^2.32 so it starts gently.
//     The push normal n is blended from the front face's outward normal to the back face's inward normal by r (both close to the push
//     direction). If the player moves against n, the part of their velocity against n is taken out by the fraction r:
//     velocity -= r x (velocity . n) x n. So a player entering through the front face is slowed more and more, and stopped by the back face.
//   Only players (the trigger's own filter and flags still apply). The planes are fixed at spawn: they don't follow a moving parent.
//
// SETUP: vmap_asset_slop_import.py (SOFT_BARRIERS) does it all: each trigger_softbarrier becomes a trigger_multiple (same brush, flags,
// filter, outputs; csgo_class trigger_softbarrier) and its front and back planes, worked out from the .vmf brush as above, go into the
// data block below. The converter names unnamed ones (trigger_softbarrier__sb<N>); barriers sharing a name are told apart by where they
// are. CS2 has no SetPushDirection for it: the converter logs outputs that sent one.
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.
//
// Console (sv_cheats 1): sb_list (the barriers hooked), sb_log 0|1 (a line per touch and per slowed tick), sb_on 0|1.

import { Instance, CSMoveType } from "cs_script/point_script";
// CS2 now refuses a second SetThink ("Callback \"Think\" already registered"): the think is set once (a script reload re-runs
// this file, so it is set again then)
let thinkSetTo = null;
function setThinkOnce(fn) {
    if (thinkSetTo === fn) return;
    try {
        Instance.SetThink(fn);
    } catch (e) {
        if (!String(e).includes("already registered")) throw e;
    }
    thinkSetTo = fn;
}


/** map -> [{ n: name, o: [x, y, z] origin, f: [nx, ny, nz, d] front face, b: [nx, ny, nz, d] back face }]; outward normal, n . p = d */
const DATA = /*SB_DATA_BEGIN*/{}/*SB_DATA_END*/;
const RESCAN_EVERY = 1.0;                       // seconds: barriers spawned by point_templates get hooked
const BIAS_EXP = Math.log(0.2) * -1.4427;       // Source mathlib Bias(x, 0.2) = x ^ (log(0.2) / log(0.5))

let LOG = 0, ON = 1;

function mapEntries() {
    let name = "";
    try {
        name = String(Instance.GetMapName() || "");
    } catch (e) {
        name = "";
    }
    name = name.replace(/\\/g, "/").split("/").pop().replace(/\.vpk$|\.vmap$/i, "").toLowerCase();
    if (DATA[name]) return DATA[name];
    const keys = Object.keys(DATA);
    return keys.length === 1 ? DATA[keys[0]] : [];
}
const ENTRIES = mapEntries();
const baseName = (n) => String(n || "").replace(/&\d+$/, "").toLowerCase();   // a point_template's name fixup taken off
const NAMES = new Set(ENTRIES.map((d) => d.n.toLowerCase()));

const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
const say = (s) => Instance.Msg(`[softbarrier] ${s}`);

let zones = new Map();      // trigger entity -> { ent, d (entry), inside: Set of pawns }
let conns = [];

function toPawn(ent) {
    if (!ent || !ent.IsValid()) return undefined;
    if (typeof ent.GetPlayerController === "function") return ent;                  // a player pawn
    if (typeof ent.GetPlayerPawn === "function") return ent.GetPlayerPawn();       // a controller
    return undefined;
}

/** the entry for a trigger: its name, then the nearest origin (several barriers can share a name) */
function entryFor(ent) {
    const nm = baseName(ent.GetEntityName());
    const o = ent.GetAbsOrigin();
    let best, bd = Infinity;
    for (const d of ENTRIES) {
        if (d.n.toLowerCase() !== nm) continue;
        const dd = (d.o[0] - o.x) ** 2 + (d.o[1] - o.y) ** 2 + (d.o[2] - o.z) ** 2;
        if (dd < bd) {
            bd = dd;
            best = d;
        }
    }
    return best;
}

function hook(ent) {
    const d = entryFor(ent);
    if (!d) return undefined;
    const Z = { ent, d, inside: new Set() };
    zones.set(ent, Z);
    const a = Instance.ConnectOutput(ent, "OnStartTouch", ({ activator }) => touch(Z, activator, true));
    const b = Instance.ConnectOutput(ent, "OnEndTouch", ({ activator }) => touch(Z, activator, false));
    for (const id of [a, b]) if (id !== undefined) conns.push(id);
    return Z;
}

function scan() {
    for (const ent of [...zones.keys()]) if (!ent.IsValid()) zones.delete(ent);
    if (!NAMES.size) return 0;
    let added = 0;
    for (const ent of Instance.FindEntitiesByClass("trigger_multiple")) {
        if (!ent.IsValid() || zones.has(ent) || !NAMES.has(baseName(ent.GetEntityName()))) continue;
        if (hook(ent)) added++;
    }
    return added;
}

function touch(Z, who, start) {
    const pawn = toPawn(who);
    if (!pawn) return;
    if (start) Z.inside.add(pawn);
    else Z.inside.delete(pawn);
    if (LOG) say(`${start ? "start" : "end"} touch ${Z.ent.GetEntityName()} by ${pawn.GetPlayerController() ? pawn.GetPlayerController().GetPlayerName() : "?"}`);
}

/** CTriggerSoftBarrier::Touch for one player; true if the velocity was changed */
function slow(Z, pawn) {
    if (!pawn.IsValid() || !pawn.IsAlive() || pawn.GetParent()) return false;
    const mt = pawn.GetMoveType();
    if (mt === CSMoveType.NONE || mt === CSMoveType.NOCLIP || mt === CSMoveType.OBSERVER) return false;   // CS:GO: NONE, PUSH, NOCLIP (no PUSH in CS2)
    const [fx, fy, fz, fd] = Z.d.f, [bx, by, bz, bd] = Z.d.b;
    const p = pawn.GetAbsOrigin();
    const toFront = fd - (fx * p.x + fy * p.y + fz * p.z);        // distance inside the front face (inward normal = -outward)
    const toBack = bd - (bx * p.x + by * p.y + bz * p.z);
    const x = Math.min(Math.max(toFront / Math.max(toFront + toBack, 0.0001), 0), 1);
    const r = Math.pow(x, BIAS_EXP);
    // Lerp(r, front outward normal, back inward normal), normalised
    let n = { x: fx + (-bx - fx) * r, y: fy + (-by - fy) * r, z: fz + (-bz - fz) * r };
    const ln = Math.sqrt(dot(n, n));
    if (ln < 1e-9) return false;
    n = { x: n.x / ln, y: n.y / ln, z: n.z / ln };
    const v = pawn.GetAbsVelocity();
    const vn = dot(v, n);
    if (!(vn < 0)) return false;                                // only when moving against the push normal
    const nv = { x: v.x - r * vn * n.x, y: v.y - r * vn * n.y, z: v.z - r * vn * n.z };
    pawn.Teleport({ velocity: nv });
    if (LOG) say(`${Z.ent.GetEntityName()}: r ${r.toFixed(3)} speed into it ${(-vn).toFixed(1)} -> ${(-(1 - r) * vn).toFixed(1)}`);
    return true;
}

let nextScan = 0;
function think() {
    const now = Instance.GetGameTime();
    Instance.SetNextThink(now);             // first, so an error can't stop the loop
    if (now >= nextScan) {
        nextScan = now + RESCAN_EVERY;
        scan();
    }
    if (!ON) return;
    for (const Z of zones.values()) {
        if (!Z.ent.IsValid()) continue;
        for (const pawn of [...Z.inside]) {
            if (!pawn.IsValid()) {
                Z.inside.delete(pawn);
                continue;
            }
            slow(Z, pawn);
        }
    }
}

function start(why) {
    for (const id of conns) Instance.DisconnectOutput(id);
    conns = [];
    zones = new Map();
    scan();
    nextScan = Instance.GetGameTime() + RESCAN_EVERY;
    setThinkOnce(think);
    Instance.SetNextThink(Instance.GetGameTime());
    say(`${why}: ${zones.size} barrier(s) hooked, ${ENTRIES.length} in the data` + (ENTRIES.length ? "" : " (no data for this map: rerun the converter)"));
}
Instance.OnActivate(() => start("on"));
Instance.OnRoundStart(() => start("round start"));     // map entities are new every round
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => start("reloaded") });
Instance.OnScriptInput("sb_rescan", () => scan());

const arg = (a) => String(a === undefined ? "" : a).trim();
const cmd = (name, fn) => (typeof Instance.RegisterCheatCommand === "function" ? Instance.RegisterCheatCommand(name, fn) : undefined);
cmd("sb_list", () => {
    for (const Z of zones.values()) say(`${Z.ent.GetEntityName()}: front ${Z.d.f.join(" ")}, back ${Z.d.b.join(" ")}, ${Z.inside.size} inside`);
    say(`${zones.size} hooked`);
});
cmd("sb_log", (a) => {
    LOG = Number(arg(a)) ? 1 : 0;
    say(`log ${LOG}`);
});
cmd("sb_on", (a) => {
    ON = Number(arg(a)) ? 1 : 0;
    say(`on ${ON}`);
});
