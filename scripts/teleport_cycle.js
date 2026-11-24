// teleport_cycle.js: CS:GO's trigger_teleport for CS2 (every trigger_teleport with no landmark).
//
// What CS:GO did (CS:GO server.dll CTriggerTeleport::Touch, read from the binary 2026-11-21; the Source SDK version differs):
//   - with no landmark: the toucher is put at the destination's origin, keeps its velocity, and keeps its own angles (a player their
//     view) unless UseLandmarkAngles is 1, which gives it the destination's angles
//   - each trigger_teleport keeps the destination it used last. A touch looks for the next entity with the target name after that one
//     in the entity list (map order); past the last one it starts again from the first. Spawn clears it, and map entities are new
//     every round, so a fresh round starts at the first destination: with two, a cycle A, B, A, B ... per trigger
//   - any entity with that name is a destination (mg_harero's "cave": an env_soundscape_triggerable, then a point_teleport)
// What CS2 does (CS2 server.dll): with no landmark it always gives the toucher the destination's angles and turns its velocity with
// them (use_landmark_angles is only read with a landmark), and it always takes the first destination of the name. Hence this script.
//
// SETUP: vmap_asset_slop_import.py (CSGO_TELEPORTS) turns each trigger_teleport with no landmark into a trigger_multiple (same brush,
// flags, filter, outputs; csgo_class trigger_teleport, csgo_target) whose OnStartTouch sends RunScriptInput tpc_<N> to the point_script
// teleport_cycle. The data block holds, per trigger, the target name, UseLandmarkAngles and the destinations' places in CS:GO's
// order (the .vmf's); the script matches the entities it finds by that name to those places at each round start, and teleports to
// where the destination is at that moment (a trigger whose target outputs switch aims at a <name>__dest proxy that they move).
// Teleports with a landmark or CheckDestIfClearForPlayer are left to CS2. Touch outputs reach the script one tick after the touch.
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.
//
// Console (sv_cheats 1): tpc_list (each trigger's destinations in order and the one used last), tpc_log 0|1.

import { Instance } from "cs_script/point_script";

/** map -> [{ k: input number, n: target name, a: UseLandmarkAngles 0/1, d: [[x, y, z] ...] destinations in CS:GO's order }] */
const DATA = /*TPC_DATA_BEGIN*/{}/*TPC_DATA_END*/;

let LOG = 0;
const say = (s) => Instance.Msg(`[teleport_cycle] ${s}`);

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
const state = new Map();    // input number -> { order: [entity], last: index used last (-1: none this round) }

/** the entities named n, in CS:GO's order: each data place takes the nearest one not taken yet; any left over go last */
function orderFor(E) {
    const found = Instance.FindEntitiesByName(E.n).filter((x) => x && x.IsValid());
    const left = new Set(found), order = [];
    for (const p of E.d) {
        let best, bd = Infinity;
        for (const x of left) {
            const o = x.GetAbsOrigin();
            const dd = (o.x - p[0]) ** 2 + (o.y - p[1]) ** 2 + (o.z - p[2]) ** 2;
            if (dd < bd) {
                bd = dd;
                best = x;
            }
        }
        if (best) {
            order.push(best);
            left.delete(best);
        }
    }
    for (const x of left) order.push(x);
    return order;
}

function reset(why) {
    state.clear();
    for (const E of ENTRIES) state.set(E.k, { order: orderFor(E), last: -1 });
    say(`${why}: ${ENTRIES.length} trigger(s)` + (ENTRIES.length ? "" : " (no data for this map: rerun the converter)"));
}

function teleport(E, who) {
    if (!who || !who.IsValid()) return;
    let st = state.get(E.k);
    if (!st) {
        st = { order: orderFor(E), last: -1 };
        state.set(E.k, st);
    }
    const n = st.order.length;
    // the next one after the last used that still exists, else from the first (CS:GO: FindEntityByName from the last, then from the start)
    let pick = -1;
    for (let i = st.last + 1; i < n && pick < 0; i++) if (st.order[i].IsValid()) pick = i;
    for (let i = 0; i < n && pick < 0; i++) if (st.order[i].IsValid()) pick = i;
    if (pick < 0) {
        say(`no destination named '${E.n}' found`);
        return;
    }
    st.last = pick;
    const dest = st.order[pick];
    const pos = dest.GetAbsOrigin();
    // CS:GO kept the toucher's velocity (Teleport with no velocity). Passed on explicitly: without it a physics object (mg_hellz's mg8
    // balls, falling into the teleport) could come out at rest and stop on the edge it should have shot past
    let vel;
    try {
        vel = who.GetAbsVelocity();
    } catch (e) {
        vel = undefined;
    }
    const keep = (o) => (vel ? Object.assign(o, { velocity: vel }) : o);
    if (E.a) {
        const ang = dest.GetAbsAngles();
        if (typeof who.SetEyeAngles === "function") {        // a player: the view turns to the destination's angles
            who.Teleport(keep({ position: pos }));
            who.SetEyeAngles(ang);
        } else {
            who.Teleport(keep({ position: pos, angles: ang }));
        }
    } else {
        who.Teleport(keep({ position: pos }));              // velocity and angles kept
    }
    if (LOG) say(`tpc_${E.k}: ${E.n} #${pick + 1} of ${n} (${dest.GetClassName()}) at ${pos.x.toFixed(0)} ${pos.y.toFixed(0)} ${pos.z.toFixed(0)}`);
}

for (const E of ENTRIES) Instance.OnScriptInput(`tpc_${E.k}`, ({ activator }) => teleport(E, activator));

Instance.OnActivate(() => reset("on"));
Instance.OnRoundStart(() => reset("round start"));     // map entities are new every round: CS:GO's triggers started from the first again
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => reset("reloaded") });

const arg = (a) => String(a === undefined ? "" : a).trim();
if (typeof Instance.RegisterCheatCommand === "function") {
    Instance.RegisterCheatCommand("tpc_list", () => {
        for (const E of ENTRIES) {
            const st = state.get(E.k);
            const names = st ? st.order.map((x, i) => `${i + 1}:${x.IsValid() ? x.GetClassName() : "(gone)"}`).join(" ") : "(not set up)";
            say(`tpc_${E.k} -> ${E.n}${E.a ? " (UseLandmarkAngles)" : ""}: ${names}; last ${st && st.last >= 0 ? st.last + 1 : "none"}`);
        }
    });
    Instance.RegisterCheatCommand("tpc_log", (a) => {
        LOG = Number(arg(a)) ? 1 : 0;
        say(`log ${LOG}`);
    });
}
