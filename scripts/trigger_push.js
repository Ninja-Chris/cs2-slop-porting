// trigger_push.js: CS:GO trigger_push behaviour in CS2 (cs_script / point_script). The pushing is the in-game-tested tpush_test.js
// (2026-11-09); the pushes come from the data the converter writes below instead of zone names.
//
// What CS:GO did (Source SDK triggers.cpp CTriggerPush::Touch, player_command.cpp CheckMovingGround, gamemovement.cpp):
//   - each tick a player touches a push: base velocity = sum of speed x direction of every push touched that tick
//   - next move: the vertical part is added to the velocity as an acceleration (vz += bz x dt); the horizontal part is added to
//     the velocity for the move only, so friction, acceleration and air strafing only ever act on the player's own velocity
//   - the first tick with no touch: velocity += (1 + dt / 2) x horizontal base velocity (momentum), base velocity cleared
//   - an upward push on a grounded player clears the ground and lifts them 1 unit. The next move puts them back on the ground
//     (within 2 units, not rising fast), and a moving player is snapped back down. A player standing still creeps up a unit a tick:
//     after 3 touches they are 3 units up, off the ground, and the push takes them up from there (and again each time they land)
//   - speed 0 means 100 (CTriggerPush::Spawn)
// Scripts can't set base velocity in CS2, so this script copies it:
//   in the air:    the push rides in the velocity and is taken out again next tick (smooth on the client)
//   on the ground: the player is moved by it (velocity untouched), so friction and ground acceleration only see their own speed
// Riding in the velocity, the push would also be seen by CS2's air acceleration: strafing against a push would hold the player back far
// more than in CS:GO, where only the own velocity was accelerated (a 300 push, holding S in the air: CS:GO still drifts at 270 u/s, the
// player would nearly stop). AIR_ACCEL_FIX takes that out: what CS2's air acceleration added is measured each tick and replaced by what
// CS:GO's AirAccelerate would have added to the own velocity, and the next move is pre-corrected so the player also travels as in CS:GO.
// The formula needs sv_air_max_wishspeed and sv_airaccelerate (read from the console when this CS2 build has the Console API, else 30
// and 12) and the player's acceleration per tick, which depends on the weapon, walking and crouching: it is measured from what CS2 did.
// Two CS2 differences, both seen in the in-game log (2026-11-08):
//   - CS2 snaps a grounded player back onto the floor every tick, so the 1-unit lifts never add up: the script puts a player who
//     has stood still in an up push for 3 touches 3 units up itself (LIFT)
//   - the touch outputs reach the script one tick late, which pumped the bobbing at the top of an up push until the player fell to
//     the floor. The script makes up the push the move just made should have had (LAG)
//
// Everything else the push touches (Source SDK CTriggerPush::Touch, by move type):
//   - physics objects (props, physbox, dropped weapons): ApplyForceCenter(speed x dir x 100 x dt) every tick, i.e. velocity +=
//     speed x dir x dt x 100 / mass (the push assumes 100 kg). Flag 4096 ("account for object mass"): velocity += speed x dir x dt
//     whatever the mass. CS:GO's physics never let an object move faster than 2000 units/s (vphysics maxVelocity), so a very hard
//     push (a "cannon": speed 200000) left it at that speed; the script stops its own pushing there too (PROP_MAX_SPEED)
//   - anything else that moves by itself (grenades in flight): base velocity like players, but it moves them during the move on
//     all three axes instead of accelerating them; leaving: velocity += (1 + dt/2) x horizontal base velocity
//   - Once Only (flag 128): velocity += speed x dir for whatever touches first (players and props alike), trigger removed
//   - parented entities, and anything not moving by itself (NONE) or in noclip, are never pushed
//   cs_script can't read a prop's mass: it comes from the prop's name (push_<kg>_<name> or <name>__pm<kg>, as push_mass.js), else
//   the converter's tables below (func_physbox by name, props by model), else MASS (100)
//   - OnlyFallingPlayers: players only, pushed while falling with vz <= FallingSpeedThreshold, not on the ground, not holding jump
// The push direction is kept in the trigger's own space, as CS:GO did, so it turns with a rotating parent.
//
// SETUP: vmap_asset_slop_import.py (TRIGGER_PUSH_SCRIPT) does it all. CS2's own trigger_push pushes even at speed 0 (it uses 100, tested
// in game), so the converter turns each trigger_push into a trigger_multiple: the same brush, flags (Once Only and Ladders taken off:
// they are in the data), filter, outputs and Enable / Disable, so the trigger itself still decides who touches it. It writes each
// push's speed, direction, flags and place into the data block below, copies this file to scripts/trigger_push.js and adds a
// point_script named trigger_push. The trigger_multiple keeps the push's keys (speed, pushdir, OnlyFallingPlayers,
// FallingSpeedThreshold, csgo_spawnflags): change those in Hammer and rerun the converter.
// The script finds the pushes by name: the converter names unnamed pushes (trigger_push__tp<N>) and renames a push that shares its
// name with another trigger_multiple (<name>__tp<N>, outputs to the old name also sent to the new one). Pushes sharing a name are
// told apart by where they are. SetPushDirection / AddOutput pushdir sent to a push also come here (RunScriptInput tpush_dir_<N>);
// point_templates and env_entity_makers that spawn pushes send RunScriptInput tpush_rescan, so new pushes are hooked at once (the
// script also looks for new ones every second).
//
// Console commands (need sv_cheats 1):
//   tp_mode hybrid|position|velocity   horizontal push: hybrid = in the velocity in the air, moves the player on the ground (default)
//                                      position = always moves the player; velocity = always in the velocity
//   tp_lift <units> how far up a player standing still in an up push is put after 3 touches (default 3, as CS:GO; 0 = off).
//                   CS2 pulls a lifted player back down, so the lift also kicks them up for a tick (see KICKS): "lift works" in the
//                   console says which kick it took
//   tp_lag 0|1      make up for touch outputs arriving a tick late (default 1)
//   tp_air 0|1      AIR_ACCEL_FIX (default 1)
//   tp_on 0|1       the script's pushing on / off (touch lines still print)
//   tp_log 0|1|2    1 = a console line per touch and per tick for each pushed player; 2 = also slot 0 every tick (default 0)
//   tp_list         the pushes hooked, with their speed and options
//   tp_set <trigger name> <speed> [<pitch> <yaw>]   change a push until the round restarts
//   tp_mass <kg>    mass for props with none in their name or the converter's tables (default 100)
//   tp_cap <speed>  the fastest a push makes a physics object go (default 2000, CS:GO's physics limit; 0 = no limit)
//
// Uses Instance.ConnectOutput / FindEntitiesByClass / Msg / RegisterCheatCommand: marked deprecated in point_script.d.ts but they
// are what the in-game-tested scripts use. Console (for the two air convars) is reached through the module object and checked before
// use, so a build without it still runs the script (with 30 / 12).

import { Instance, CSPlayerPawn, CSPlayerController, CSInputs, CSMoveType } from "cs_script/point_script";
import * as PS from "cs_script/point_script";
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


// ---- settings (the console commands above change them until the map reloads) ----
let MODE = "hybrid";
let LIFT = 3;
let LAG = 1;
// CS2 keeps a player it thinks is grounded on the ground, so a lift alone gets pulled back down (in-game log, 2026-11-08). The lift
// also gives a one-tick upward kick, taken back as soon as they are airborne. It starts at none (CS:GO) and steps up each time a
// lift is pulled back; the console says which one worked.
const KICKS = [0, 140, 250, 300];
let kickLevel = 0, liftWorked = false;
let AIR_ACCEL_FIX = true;
// the speed the first guess at a player's air acceleration assumes (knife: 250), until it is measured
const WISH_SPEED = 250;
let ON = true;
let LOG = 0;
let MASS = 100;
let PROP_MAX_SPEED = 2000; // CS:GO's vphysics maxVelocity (units/s): a push never takes a physics object past it
const STEP = 18; // sv_stepsize
const RESCAN_EVERY = 1; // seconds between looks for pushes spawned since (point_template / env_entity_maker)

// ---- per-map data, written by the converter. Do not edit: change the map and rerun the converter. ----
// { "<map name>": { t: [ { n: targetname, o: origin, a: angles, d: pushdir, s: speed, f: spawnflags (of the trigger_push), of:
//   OnlyFallingPlayers, ft: FallingSpeedThreshold, b: [mins, maxs] world bounds or null, p: parented }, ... ],
//   m: { "<model>": mass }, pm: { "<func_physbox name>": mass }, i: { "<input>": [[entry, "p y r"], ...] } } }
const DATA = /*TPUSH_DATA_BEGIN*/{}/*TPUSH_DATA_END*/;

const SF_ONCE = 128;
const SF_REAL_MASS = 4096;

const V = (x, y, z) => ({ x, y, z });
const add = (a, b) => V(a.x + b.x, a.y + b.y, a.z + b.z);
const sub = (a, b) => V(a.x - b.x, a.y - b.y, a.z - b.z);
const mul = (a, k) => V(a.x * k, a.y * k, a.z * k);
const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
const len = (a) => Math.hypot(a.x, a.y, a.z);
const f1 = (x) => x.toFixed(1);
const say = (s) => Instance.Msg("trigger_push: " + s);
let tick = 0;

// Source AngleMatrix: columns = forward, left, up of (pitch, yaw, roll)
function angleMatrix(p, y, r) {
    const d = Math.PI / 180;
    const sp = Math.sin(p * d), cp = Math.cos(p * d), sy = Math.sin(y * d), cy = Math.cos(y * d), sr = Math.sin(r * d), cr = Math.cos(r * d);
    return [
        [cp * cy, sp * sr * cy - cr * sy, sp * cr * cy + sr * sy],
        [cp * sy, sp * sr * sy + cr * cy, sp * cr * sy - sr * cy],
        [-sp, sr * cp, cr * cp],
    ];
}
const rotate = (M, v) => V(M[0][0] * v.x + M[0][1] * v.y + M[0][2] * v.z, M[1][0] * v.x + M[1][1] * v.y + M[1][2] * v.z, M[2][0] * v.x + M[2][1] * v.y + M[2][2] * v.z);
const irotate = (M, v) => V(M[0][0] * v.x + M[1][0] * v.y + M[2][0] * v.z, M[0][1] * v.x + M[1][1] * v.y + M[2][1] * v.z, M[0][2] * v.x + M[1][2] * v.y + M[2][2] * v.z);
// the push direction of pushdir angles (cos(90) is 6e-17, not 0)
function forward(a) {
    const d = Math.PI / 180, r = (x) => (Math.abs(x) < 1e-9 ? 0 : x);
    return V(r(Math.cos(a[0] * d) * Math.cos(a[1] * d)), r(Math.cos(a[0] * d) * Math.sin(a[1] * d)), r(-Math.sin(a[0] * d)));
}

// ---- map data ----
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
    if (keys.length === 1) return DATA[keys[0]];
    return undefined;
}
const MD = mapData();
const ENTRIES = MD ? MD.t : [];
const MODEL_MASS = MD && MD.m ? MD.m : {};
const NAME_MASS = MD && MD.pm ? MD.pm : {}; // func_physbox name -> mass (a physbox has no model to look it up by)
const baseName = (n) => String(n || "").replace(/&\d+$/, "").toLowerCase(); // a point_template's name fixup taken off
const BY_NAME = new Map(); // name -> entry indexes (every push has a name: the converter names the unnamed ones)
ENTRIES.forEach((e, i) => {
    const k = baseName(e.n);
    if (k) (BY_NAME.get(k) || BY_NAME.set(k, []).get(k)).push(i);
});

// sv_air_max_wishspeed: CS2 refuses it to a workshop map's script ("DISALLOWED WORKSHOP CONVAR", a warning every read): CS2's default
const AIR_MAX_WISHSPEED = 30;
// a console variable (Console API), or dflt when this build has none / the variable is missing
function convar(name, dflt) {
    try {
        const C = PS.Console;
        const v = C && typeof C.GetConvarNumber === "function" ? C.GetConvarNumber(name) : undefined;
        return typeof v === "number" && Number.isFinite(v) ? v : dflt;
    } catch (e) {
        return dflt;
    }
}
const hasConsole = () => {
    try {
        return !!(PS.Console && typeof PS.Console.GetConvarNumber === "function");
    } catch (e) {
        return false;
    }
};
// the horizontal direction the player's movement keys and view ask for (undefined: no key)
function wishDir(pawn) {
    const f = (pawn.IsInputPressed(CSInputs.FORWARD) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.BACK) ? 1 : 0);
    const s = (pawn.IsInputPressed(CSInputs.RIGHT) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.LEFT) ? 1 : 0);
    if (!f && !s) return undefined;
    const y = (pawn.GetEyeAngles().yaw * Math.PI) / 180;
    const wx = Math.cos(y) * f + Math.sin(y) * s, wy = Math.sin(y) * f - Math.cos(y) * s; // forward x f + right x s
    const l = Math.hypot(wx, wy);
    return { x: wx / l, y: wy / l };
}
// air acceleration for this tick: cap = sv_air_max_wishspeed, A = most a tick adds (sv_airaccelerate x wish speed x dt): the value
// measured for this player, measured again when the weapon, walking or crouching changes; until then a guess from the convars
function airParams(pawn, S, dt) {
    let wpn = "";
    try {
        const g = typeof pawn.GetActiveWeapon === "function" ? pawn.GetActiveWeapon() : undefined;
        wpn = g && g.IsValid() ? g.GetData().GetName() : "";
    } catch (e) {
        wpn = "";
    }
    const key = `${wpn}|${pawn.IsInputPressed(CSInputs.WALK) ? 1 : 0}|${pawn.IsDucked() ? 1 : 0}`;
    if (key !== S.aKey) {
        S.aKey = key;
        S.A = undefined;
    }
    return { cap: AIR_MAX_WISHSPEED, A: S.A !== undefined ? S.A : convar("sv_airaccelerate", 12) * WISH_SPEED * dt };
}
// what CS:GO's AirAccelerate adds along w to velocity u
function airAdd(u, w, air) {
    const room = air.cap - (u.x * w.x + u.y * w.y);
    return room <= 0 ? 0 : Math.min(air.A, room);
}

function toPawn(e) {
    if (e instanceof CSPlayerPawn) return e;
    if (e instanceof CSPlayerController) return e.GetPlayerPawn();
    return undefined;
}
function slotOf(pawn) {
    const c = pawn && pawn.IsValid() ? pawn.GetPlayerController() : undefined;
    return c ? c.GetPlayerSlot() : -1;
}
// The same entity? The API has no entity index, and nothing says it hands out one JS object per entity: if it doesn't, the
// activator of an OnEndTouch would not be the object stored at OnStartTouch. Same class, name and position counts as the same.
function sameEnt(a, b) {
    if (a === b) return true;
    if (!a.IsValid() || !b.IsValid() || a.GetClassName() !== b.GetClassName() || a.GetEntityName() !== b.GetEntityName()) return false;
    const p = a.GetAbsOrigin(), q = b.GetAbsOrigin();
    return Math.abs(p.x - q.x) + Math.abs(p.y - q.y) + Math.abs(p.z - q.z) < 0.01;
}
function findSame(list, e) {
    for (const t of list) if (sameEnt(t, e)) return t;
    return undefined;
}

// ---- pushes: trigger -> { ent, idx: data entry, name, speed, local: direction in the trigger's space, once, realMass, fall, box,
//      used, inside: Set<player slot>, things: Set<entity>, fresh / tFresh: started since the last tick, brief / tBrief: started
//      and ended since the last tick (still pushed once) }
let zones = new Map();
let conns = [];
const usedEntries = new Set(); // entries hooked this round: pushes sharing a name each get their own
let onceUsed = new Set(); // Once Only triggers already fired, so a second hook on the same trigger can't fire it again

// the data entry of a trigger: its name, then the nearest place (preferring entries not hooked yet; a point_template can spawn the
// same push many times, anywhere)
function findEntry(ent) {
    const list = BY_NAME.get(baseName(ent.GetEntityName()));
    if (!list) return -1;
    const o = ent.GetAbsOrigin();
    let best = -1, bestD = Infinity;
    for (const i of list) {
        const e = ENTRIES[i];
        const d = Math.hypot(e.o[0] - o.x, e.o[1] - o.y, e.o[2] - o.z) + (usedEntries.has(i) ? 1e6 : 0);
        if (d < bestD) {
            bestD = d;
            best = i;
        }
    }
    usedEntries.add(best);
    return best;
}
function dirOf(Z) {
    const a = Z.ent.GetAbsAngles();
    return rotate(angleMatrix(a.pitch, a.yaw, a.roll), Z.local);
}
function hook(ent) {
    const idx = findEntry(ent);
    if (idx < 0) return undefined;
    const d = ENTRIES[idx];
    const a0 = ent.GetAbsAngles();
    // CS:GO stores the push direction in the trigger's own space at spawn (so it turns with a rotating parent). pushdir is the map's
    // direction at the map's angles: taken into the trigger's space with those (d.a), not with the angles it has when hooked - a push
    // on a rotating parent hooked at round start, already turned, pushed off its barrel by that turn
    const ma = d.a && d.a.length === 3 ? { pitch: d.a[0], yaw: d.a[1], roll: d.a[2] } : a0;
    const local = irotate(angleMatrix(ma.pitch, ma.yaw, ma.roll), forward(d.d));
    // its box, to drop a player whose end touch never came (a trigger moved or removed): only for an unparented push at its own angles
    const turned = [a0.pitch, a0.yaw, a0.roll].some((x, k) => Math.abs((((x - (d.a ? d.a[k] : 0)) % 360) + 540) % 360 - 180) > 0.5);
    const Z = { ent, idx, name: ent.GetEntityName(), speed: d.s === 0 ? 100 : d.s, local, once: !!(d.f & SF_ONCE), realMass: !!(d.f & SF_REAL_MASS),
                fall: d.of ? d.ft : undefined, box: d.b && !d.p && !turned ? d.b : null, used: false,
                inside: new Set(), fresh: new Set(), brief: new Set(), things: new Set(), tFresh: new Set(), tBrief: new Set() };
    zones.set(ent, Z);
    const a = Instance.ConnectOutput(ent, "OnStartTouch", ({ activator }) => touch(Z, activator, true));
    const b = Instance.ConnectOutput(ent, "OnEndTouch", ({ activator }) => touch(Z, activator, false));
    for (const id of [a, b]) if (id !== undefined) conns.push(id);
    return Z;
}
// hook the pushes not hooked yet (all of them after hookAll; the new ones after a point_template spawned some). A hooked push is the
// same object, else one with the same class, name and place, matched one to one (two pushes can share a name and a place).
function scan() {
    for (const ent of [...zones.keys()]) if (!ent.IsValid()) zones.delete(ent);
    if (!BY_NAME.size) return [];
    const free = new Set(zones.keys()), todo = [], added = [];
    for (const ent of Instance.FindEntitiesByClass("trigger_multiple")) {
        if (!ent.IsValid() || !BY_NAME.has(baseName(ent.GetEntityName()))) continue;
        if (free.has(ent)) free.delete(ent);
        else todo.push(ent);
    }
    for (const ent of todo) {
        const same = findSame(free, ent);
        if (same) {
            free.delete(same);
            continue;
        }
        if (onceUsed.has(ent) || findSame(onceUsed, ent)) continue; // fired and being removed
        const Z = hook(ent);
        if (Z) added.push(Z);
    }
    return added;
}
const describe = (Z) => `${Z.name} (${Z.speed}${Z.once ? " once" : ""}${Z.realMass ? " mass" : ""}${Z.fall !== undefined ? ` fall<=${Z.fall}` : ""})`;
function hookAll() {
    for (const id of conns) Instance.DisconnectOutput(id);
    conns = [];
    zones = new Map();
    usedEntries.clear();
    onceUsed = new Set();
    scan();
    return `${zones.size} push(es) hooked, ${ENTRIES.length} in the data` + (MD ? "" : " (no data for this map: rerun the converter)");
}
function touch(Z, who, start) {
    if (!who || !who.IsValid()) return;
    if (Z.once) {
        if (start) once(Z, who);
        return;
    }
    const pawn = toPawn(who);
    if (pawn) {
        const slot = slotOf(pawn);
        if (slot < 0) return;
        if (start) {
            Z.inside.add(slot);
            Z.fresh.add(slot);
        } else {
            Z.inside.delete(slot);
            if (Z.fresh.has(slot)) Z.brief.add(slot); // in and out between two ticks: CS:GO still pushed it for the tick it touched
        }
        if (LOG) say(`t=${tick} ${start ? "start" : "end"} ${Z.name} s${slot}`);
    } else {
        const t = findSame(Z.things, who);
        if (start && !t) {
            Z.things.add(who);
            Z.tFresh.add(who);
        } else if (!start && t) {
            Z.things.delete(t);
            if (Z.tFresh.has(t)) Z.tBrief.add(t);
        }
        if (LOG) say(`t=${tick} ${start ? "start" : "end"} ${Z.name} ${who.GetClassName()} ${who.GetEntityName()}` +
            (start && !Z.realMass && who.GetMoveType() === CSMoveType.VPHYSICS ? ` (mass ${massOf(who)})` : ""));
    }
}
// Once Only: whatever touches first gets velocity += speed x dir (CS:GO ApplyAbsVelocityImpulse: a physics object gets it whatever
// its mass), then the trigger is removed
function once(Z, who) {
    if (Z.used || onceUsed.has(Z.ent)) return;
    Z.used = true;
    onceUsed.add(Z.ent);
    const pawn = toPawn(who);
    const ent = pawn || who;
    if (ent && ent.IsValid() && !ent.GetParent()) {
        const imp = mul(dirOf(Z), Z.speed);
        const v0 = ent.GetAbsVelocity();
        const nv = !pawn && ent.GetMoveType() === CSMoveType.VPHYSICS ? capSpeed(add(v0, imp), v0, true) : add(v0, imp);
        if (pawn) pawn.Move({ velocity: nv });
        else ent.Teleport({ velocity: nv });
        if (LOG) say(`t=${tick} once-only ${Z.name}: ${ent.GetClassName()} given ${f1(imp.x)} ${f1(imp.y)} ${f1(imp.z)}, trigger removed`);
    }
    zones.delete(Z.ent);
    if (Z.ent.IsValid()) Z.ent.Remove();
}
// The converter sends SetPushDirection / AddOutput pushdir given to a push here as RunScriptInput tpush_dir_<N>: registered at load
for (const [input, list] of Object.entries((MD && MD.i) || {})) {
    Instance.OnScriptInput(input, () => {
        for (const [idx, dirStr] of list) {
            const pyr = String(dirStr).trim().split(/\s+/).map(Number);
            if (pyr.length !== 3 || !pyr.every(Number.isFinite)) continue;
            for (const Z of zones.values()) {
                if (Z.idx !== idx || !Z.ent.IsValid()) continue;
                const a = Z.ent.GetAbsAngles();
                Z.local = irotate(angleMatrix(a.pitch, a.yaw, a.roll), forward(pyr));
            }
        }
    });
}

// ---- players: slot -> { carry: push put in the velocity last tick, pre: air acceleration pre-correction put in it last tick,
//                         air: { set: velocity set, own: own velocity } last tick when the push rode in it in the air,
//                         A / aKey: the player's measured air acceleration per tick and the weapon / walk / crouch it is for,
//                         bv: horizontal base velocity of the last touched tick, applied: push applied for the move just made,
//                         creep: ticks stood still in an up push, kicked: the kick given by a lift last tick (undefined if no lift) }
const st = new Map();

// move the player by d, sliding along what it hits; on the ground also step up (StepMove) and stay on the ground (StayOnGround)
function slide(pawn, p, d) {
    for (let i = 0; i < 3 && Math.abs(d.x) + Math.abs(d.y) + Math.abs(d.z) > 0.01; i++) {
        const tr = Instance.TracePlayer({ start: p, end: add(p, d), player: pawn });
        if (tr.startedInSolid) break;
        p = tr.end;
        if (!tr.didHit) break;
        const left = mul(d, 1 - tr.fraction);
        d = sub(left, mul(tr.normal, dot(left, tr.normal)));
    }
    return p;
}
function walk(pawn, start, d, ground) {
    let p = slide(pawn, start, d);
    if (!ground) return p;
    const got = Math.hypot(p.x - start.x, p.y - start.y);
    if (got < Math.hypot(d.x, d.y) - 0.01) {
        const up = Instance.TracePlayer({ start, end: add(start, V(0, 0, STEP)), player: pawn });
        if (!up.startedInSolid) {
            const q = slide(pawn, up.end, d);
            if (Math.hypot(q.x - start.x, q.y - start.y) > got + 0.01) p = q;
        }
    }
    const dn = Instance.TracePlayer({ start: p, end: sub(p, V(0, 0, STEP)), player: pawn });
    if (dn.didHit && !dn.startedInSolid && dn.normal.z >= 0.7) p = dn.end;
    return p;
}
// a player clearly outside a push's box (48 units to spare) whose end touch never came
function outside(Z, o) {
    if (!Z.box || Z.ent.GetParent()) return false;
    const c = Z.ent.GetAbsOrigin(), e = ENTRIES[Z.idx];
    const ox = c.x - e.o[0], oy = c.y - e.o[1], oz = c.z - e.o[2];
    const [lo, hi] = Z.box;
    return o.x < lo[0] + ox - 48 || o.x > hi[0] + ox + 48 || o.y < lo[1] + oy - 48 || o.y > hi[1] + oy + 48 ||
        o.z < lo[2] + oz - 96 || o.z > hi[2] + oz + 48;
}

// ---- everything that isn't a player. thing -> { carry: push put in its velocity last tick (non-physics), bv: horizontal base
//      velocity of the last touched tick (non-physics), applied: velocity change given last tick (physics) }
const things = new Map();
// CS:GO's physics limit: a push never leaves a physics object faster than PROP_MAX_SPEED (or than it already was)
function capSpeed(nv, v, pushed) {
    if (!pushed || !(PROP_MAX_SPEED > 0)) return nv;
    const sp = len(nv), lim = Math.max(PROP_MAX_SPEED, len(v));
    return sp > lim ? mul(nv, lim / sp) : nv;
}
function massOf(ent) {
    const n = ent.GetEntityName();
    let m = /__pm(\d+(?:p\d+)?)/.exec(n);
    if (m) return Number(m[1].replace("p", "."));
    m = /^push_(\d+(?:\.\d+)?)_/i.exec(n);
    if (m) return Number(m[1]);
    if (NAME_MASS[baseName(n)]) return NAME_MASS[baseName(n)];
    try {
        const model = typeof ent.GetModelName === "function" ? String(ent.GetModelName()).replace(/\\/g, "/").toLowerCase() : "";
        if (MODEL_MASS[model]) return MODEL_MASS[model];
    } catch (e) {
        /* not a model entity */
    }
    return MASS;
}
function pushThings(dt) {
    const now = new Map(); // thing -> { B: base velocity (sum of speed x dir), dv: velocity change for a physics object }
    const keyOf = (t) => (now.has(t) || things.has(t) ? t : findSame(now.keys(), t) || findSame(things.keys(), t) || t);
    if (ON) {
        for (const [ent, Z] of zones) {
            if (!ent.IsValid()) continue;
            const list = [...Z.things];
            for (const t of Z.tBrief) if (!findSame(list, t)) list.push(t);
            if (!list.length) continue;
            const B = mul(dirOf(Z), Z.speed);
            for (const t0 of list) {
                if (!t0.IsValid()) {
                    Z.things.delete(t0);
                    continue;
                }
                const t = keyOf(t0);
                let e = now.get(t);
                if (!e) now.set(t, (e = { B: V(0, 0, 0), dv: V(0, 0, 0) }));
                e.B = add(e.B, B);
                e.dv = add(e.dv, mul(B, dt * (Z.realMass ? 1 : 100 / Math.max(massOf(t), 0.01))));
            }
        }
    }
    for (const Z of zones.values()) {
        Z.tFresh.clear();
        Z.tBrief.clear();
    }
    for (const t of things.keys()) if (!now.has(t)) now.set(t, { B: V(0, 0, 0), dv: V(0, 0, 0) }); // left: undo / hand over
    for (const [t, e] of now) {
        if (!t.IsValid() || t.GetParent()) {
            things.delete(t); // CS:GO never pushed parented entities
            continue;
        }
        const mt = t.GetMoveType();
        if (mt === CSMoveType.NONE || mt === CSMoveType.NOCLIP) {
            things.delete(t);
            continue;
        }
        const S = things.get(t) || { carry: V(0, 0, 0), bv: null, applied: V(0, 0, 0) };
        const v = t.GetAbsVelocity();
        let nv;
        if (mt === CSMoveType.VPHYSICS) {
            // a force: this tick's velocity change, for every tick the object is seen inside. The touches arrive a tick late at both
            // ends, so it gets as many ticks as in CS:GO, one tick later. (Until 2026-11-13 the late tick was made up for: twice the
            // change on the first tick, one taken back after leaving. With a hard push the engine capped the doubled speed and the
            // full change was still taken back: mg_hellz's cannon ball got +6250, was capped, lost 3125 and crawled out.)
            nv = capSpeed(add(v, e.dv), v, len(e.dv) > 0);
            S.applied = e.dv;
        } else {
            // moves by itself: the base velocity rides in the velocity for the move (all three axes), momentum on leaving
            const own = sub(v, S.carry);
            if (!len(e.B) && S.bv) {
                own.x += S.bv.x * (1 + dt / 2);
                own.y += S.bv.y * (1 + dt / 2);
                S.bv = null;
            }
            S.carry = e.B;
            if (len(e.B)) S.bv = V(e.B.x, e.B.y, 0);
            nv = add(own, S.carry);
        }
        if (len(sub(nv, v)) > 1e-3) t.Teleport({ velocity: nv });
        if (len(e.B) || len(S.applied) || len(S.carry) || S.bv) things.set(t, S);
        else things.delete(t);
    }
}

let last = 0;
function work() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - last, 1 / 128), 0.1);
    last = now;
    tick++;
    pushThings(dt);
    // the pushes each player is in
    const bySlot = new Map();
    const put = (s, Z) => (bySlot.get(s) || bySlot.set(s, []).get(s)).push(Z);
    if (ON) {
        for (const [ent, Z] of zones) {
            if (!ent.IsValid()) {
                zones.delete(ent); // killed: like leaving it
                continue;
            }
            for (const s of Z.inside) put(s, Z);
            for (const s of Z.brief) if (!Z.inside.has(s)) put(s, Z);
        }
    }
    const slots = new Set([...bySlot.keys(), ...st.keys()]);
    if (LOG === 2) slots.add(0);
    for (const slot of [...slots].sort((a, b) => a - b)) {
        let inZ = bySlot.get(slot) || [];
        let S = st.get(slot);
        const watch = LOG === 2 && slot === 0;
        if (!inZ.length && !S && !watch) continue;
        const c = Instance.GetPlayerController(slot);
        const pawn = c ? c.GetPlayerPawn() : undefined;
        if (!pawn || !pawn.IsValid() || !pawn.IsAlive()) {
            st.delete(slot);
            for (const Z of zones.values()) {
                Z.inside.delete(slot);
                Z.brief.delete(slot);
            }
            continue;
        }
        const v = pawn.GetAbsVelocity(), o = pawn.GetAbsOrigin();
        const ground = pawn.GetGroundEntity() !== undefined;
        if (inZ.length && inZ.some((Z) => outside(Z, o))) {
            inZ = inZ.filter((Z) => {
                if (!outside(Z, o)) return true;
                Z.inside.delete(slot);
                if (LOG) say(`t=${tick} s${slot} far outside ${Z.name} with no end touch: dropped`);
                return false;
            });
        }
        // the pushes touched this tick. CS:GO: none in noclip; an OnlyFallingPlayers push only while falling (vz <= its threshold),
        // in the air and not holding jump
        let B = V(0, 0, 0), n = 0;
        if (!pawn.IsNoclipping()) {
            for (const Z of inZ) {
                if (Z.fall !== undefined && (ground || v.z > Z.fall || pawn.IsInputPressed(CSInputs.JUMP))) continue;
                B = add(B, mul(dirOf(Z), Z.speed));
                n++;
            }
        }
        if (!S) {
            S = { carry: { x: 0, y: 0 }, pre: { x: 0, y: 0 }, air: null, A: undefined, aKey: "", bv: null, applied: V(0, 0, 0), creep: 0, kicked: undefined };
            if (n) st.set(slot, S);
        }
        // the player's own velocity: what the engine has now, minus what this script put into it last tick
        const own = V(v.x - S.carry.x - S.pre.x, v.y - S.carry.y - S.pre.y, v.z);
        let note = "";
        // CS2's air acceleration acted on what was set (own + push + pre-correction); CS:GO's on the own velocity only. Swap the one for
        // the other (the keys now are the keys of the move just made; it started in the air, even if it landed)
        const w = AIR_ACCEL_FIX ? wishDir(pawn) : undefined;
        let air;
        if (w && S.air) {
            air = airParams(pawn, S, dt);
            const dx = v.x - S.air.set.x, dy = v.y - S.air.set.y;
            const m = dx * w.x + dy * w.y; // what CS2's air acceleration added
            const room = Math.max(air.cap - (S.air.set.x * w.x + S.air.set.y * w.y), 0);
            // changed along the wish direction only and by no more than air acceleration can: no wall hit in between
            if (Math.abs(dx * w.y - dy * w.x) < 0.5 && m > -0.01 && m < room + 0.01) {
                // held back by the acceleration, not the cap: that is the player's A. Stopped by the cap: A is at least that much
                if (m > 0.01 && m < room - 0.01) S.A = air.A = m;
                else if (m > air.A) S.A = air.A = m;
                const cs = airAdd(S.air.own, w, air);
                own.x += w.x * (cs - m);
                own.y += w.y * (cs - m);
                if (Math.abs(cs - m) > 0.01) note += ` air ${f1(m)}->${f1(cs)}`;
            }
        }
        // touches arrive a tick late: the move just made should already have had the pushes touched now. Make up the difference
        // (horizontally only while still in a push: leaving them all is covered by the hand-over below, as in CS:GO)
        const fix = V(0, 0, 0);
        if (LAG) {
            const d = sub(B, S.applied);
            if (!ground && Math.abs(d.z) > 1e-6) {
                own.z += d.z * dt;
                fix.z = d.z * dt * dt;
            }
            if (n && Math.abs(d.x) + Math.abs(d.y) > 1e-6) {
                fix.x = d.x * dt;
                fix.y = d.y * dt;
            }
            if (fix.x || fix.y || fix.z) note += " late";
        }
        if (S.kicked !== undefined) {
            // lifted last tick: did it hold?
            const k = S.kicked;
            S.kicked = undefined;
            if (ground) {
                note += ` lift pulled back (kick ${k})`;
                if (kickLevel < KICKS.length - 1) {
                    kickLevel++;
                    say(`lift with kick ${k} was pulled back down: next lift kicks ${KICKS[kickLevel]}`);
                } else if (!liftWorked) say(`every kick was pulled back down: try a higher lift (tp_lift 19)`);
            } else {
                own.z -= k; // take the kick back: CS:GO's creep left the ground with no extra speed
                if (!liftWorked) say(`lift works: ${LIFT} units up with kick ${k}`);
                liftWorked = true;
            }
        }
        if (!n && S.bv) {
            // left every push: the base velocity becomes momentum (CheckMovingGround)
            own.x += S.bv.x * (1 + dt / 2);
            own.y += S.bv.y * (1 + dt / 2);
            S.bv = null;
            note += " handover";
        }
        let push = { x: 0, y: 0 }, pos, lifted = false;
        if (n) {
            own.z += B.z * dt; // vertical push: an acceleration
            S.bv = push = { x: B.x, y: B.y }; // horizontal push: moves the player during the next move
            // up push on a grounded player standing still: CS:GO crept them up a unit a tick, off the ground after 3 touches
            if (LIFT > 0 && B.z > 0 && ground && Math.hypot(own.x + B.x, own.y + B.y) < 1) {
                S.creep++;
                if (S.creep + LAG >= 3) {
                    pos = add(o, V(0, 0, LIFT));
                    lifted = true;
                    S.creep = 0;
                    S.kicked = KICKS[kickLevel];
                    own.z += S.kicked;
                    note += ` lift (kick ${S.kicked})`;
                } else note += " creep";
            } else S.creep = 0;
        } else S.creep = 0;
        S.applied = n ? V(B.x, B.y, B.z) : V(0, 0, 0);
        // in the velocity only while airborne now (hybrid): on the ground CS2 would put friction and ground acceleration on it
        const inVel = MODE === "velocity" || (MODE === "hybrid" && !ground);
        S.carry = inVel ? push : { x: 0, y: 0 };
        // the next move in the air: p along the wish direction so that p + what CS2's air acceleration adds to (own + push + p) is what
        // CS:GO's adds to own; then the player travels own + push + CS:GO's acceleration, as in CS:GO
        S.pre = { x: 0, y: 0 };
        const airPush = w && inVel && !ground && !lifted && (push.x || push.y);
        if (airPush) {
            air = air || airParams(pawn, S, dt);
            const ac = airAdd(own, w, air);
            const s0 = (own.x + push.x) * w.x + (own.y + push.y) * w.y;
            const p = ac >= air.cap - s0 ? ac : ac - air.A;
            S.pre = { x: w.x * p, y: w.y * p };
        }
        const d = V(fix.x + (inVel ? 0 : push.x * dt), fix.y + (inVel ? 0 : push.y * dt), fix.z);
        if (Math.abs(d.x) + Math.abs(d.y) + Math.abs(d.z) > 1e-6) {
            pos = inVel ? slide(pawn, pos || o, d) : walk(pawn, pos || o, d, ground && !lifted);
            if (!inVel && (push.x || push.y)) note += " moved";
        }
        const nv = V(own.x + S.carry.x + S.pre.x, own.y + S.carry.y + S.pre.y, own.z);
        S.air = airPush ? { set: nv, own: V(own.x, own.y, 0) } : null;
        if (pos) pawn.Move({ position: pos, velocity: nv });
        else if (Math.abs(nv.x - v.x) + Math.abs(nv.y - v.y) + Math.abs(nv.z - v.z) > 1e-3) pawn.Move({ velocity: nv });
        if (LOG && (n || note || watch)) {
            say(`t=${tick} s${slot} ${ground ? "G" : "A"} z=${f1(o.z)} v=${f1(v.x)} ${f1(v.y)} ${f1(v.z)} |v|=${f1(Math.hypot(v.x, v.y))} ` +
                `own=${f1(own.x)} ${f1(own.y)} B=${f1(B.x)} ${f1(B.y)} ${f1(B.z)} ${n ? (inVel ? "vel" : "pos") : "-"}${note}`);
        }
        if (!n && !S.bv && !S.carry.x && !S.carry.y && !S.pre.x && !S.pre.y) st.delete(slot); // (S.applied is 0 here)
    }
    for (const Z of zones.values()) {
        Z.fresh.clear();
        Z.brief.clear();
    }
}

let errors = 0;
function safeWork() {
    try {
        work();
    } catch (e) {
        if (errors++ < 5) say("error: " + ((e && e.stack) || e));
    }
}
let nextScan = 0;
function think() {
    const now = Instance.GetGameTime();
    Instance.SetNextThink(now); // first, so an error can't stop the loop
    if (now >= nextScan) {
        nextScan = now + RESCAN_EVERY;
        try {
            const added = scan();
            if (added.length && LOG) say(`t=${tick} ${added.length} new push(es): ` + added.map(describe).join(", "));
        } catch (e) {
            if (errors++ < 5) say("error: " + ((e && e.stack) || e));
        }
    }
    if (typeof Instance.QueueAfterThinks === "function") Instance.QueueAfterThinks(safeWork); // after the players have moved
    else safeWork();
}

const arg = (a) => String(a === undefined ? "" : a).trim();
Instance.RegisterCheatCommand("tp_mode", (a) => {
    if (["hybrid", "position", "velocity"].includes(arg(a))) MODE = arg(a);
    say("mode " + MODE);
});
Instance.RegisterCheatCommand("tp_lift", (a) => {
    if (arg(a) && Number.isFinite(Number(arg(a)))) LIFT = Math.max(0, Number(arg(a)));
    say("lift " + LIFT);
});
Instance.RegisterCheatCommand("tp_lag", (a) => {
    if (arg(a)) LAG = arg(a) === "0" ? 0 : 1;
    say("lag " + LAG);
});
Instance.RegisterCheatCommand("tp_air", (a) => {
    if (arg(a)) AIR_ACCEL_FIX = arg(a) !== "0";
    say(`air acceleration fix ${AIR_ACCEL_FIX ? "on" : "off"}` + airInfo());
});
Instance.RegisterCheatCommand("tp_on", (a) => {
    if (arg(a)) ON = arg(a) !== "0";
    say("script pushing " + (ON ? "on" : "off"));
});
Instance.RegisterCheatCommand("tp_log", (a) => {
    if (arg(a)) LOG = Number(arg(a)) || 0;
    say("log " + LOG);
});
Instance.RegisterCheatCommand("tp_list", () => {
    say(`${zones.size} push(es) hooked, ${ENTRIES.length} in the data: ` + [...zones.values()].map(describe).join(", "));
});
Instance.RegisterCheatCommand("tp_mass", (a) => {
    if (Number(arg(a)) > 0) MASS = Number(arg(a));
    say("mass for props with none in their name or the converter's tables: " + MASS);
});
Instance.RegisterCheatCommand("tp_cap", (a) => {
    if (arg(a) && Number.isFinite(Number(arg(a)))) PROP_MAX_SPEED = Math.max(0, Number(arg(a)));
    say("fastest a push makes a physics object go: " + (PROP_MAX_SPEED > 0 ? PROP_MAX_SPEED : "no limit"));
});
Instance.RegisterCheatCommand("tp_set", (a) => {
    const w = arg(a).split(/\s+/);
    const Zs = [...zones.values()].filter((Z) => Z.ent.IsValid() && Z.name.toLowerCase() === (w[0] || "").toLowerCase());
    if (!Zs.length || !Number.isFinite(Number(w[1]))) return say("tp_set <trigger name> <speed> [<pitch> <yaw>]");
    for (const Z of Zs) {
        Z.speed = Number(w[1]) === 0 ? 100 : Number(w[1]);
        if (w.length >= 4 && Number.isFinite(Number(w[2])) && Number.isFinite(Number(w[3]))) {
            const an = Z.ent.GetAbsAngles();
            Z.local = irotate(angleMatrix(an.pitch, an.yaw, an.roll), forward([Number(w[2]), Number(w[3]), 0]));
        }
    }
    const dr = dirOf(Zs[0]);
    say(`${w[0]}: speed ${Zs[0].speed}, dir ${f1(dr.x)} ${f1(dr.y)} ${f1(dr.z)}`);
});
const airInfo = () => `: sv_air_max_wishspeed ${AIR_MAX_WISHSPEED} (fixed), sv_airaccelerate ${convar("sv_airaccelerate", 12)}` +
    (hasConsole() ? " (from the console)" : " (no Console API in this build: CS2's defaults)");
// (Re)start: hook the pushes and run the tick loop. In tools mode, saving the .js reloads the script and clears every callback,
// including the think, and OnActivate doesn't fire again: so this also runs after a reload and at every round start.
function start(why) {
    last = Instance.GetGameTime();
    nextScan = last + RESCAN_EVERY;
    st.clear();
    things.clear();
    const what = hookAll();
    setThinkOnce(think);
    Instance.SetNextThink(last);
    say(`${why}: ${what}; mode ${MODE}, lift ${LIFT} (kick ${KICKS[kickLevel]}), lag ${LAG}, air fix ${AIR_ACCEL_FIX ? "on" + airInfo() : "off"}`);
}
Instance.OnActivate(() => start("on"));
Instance.OnRoundStart(() => start("round start")); // map entities are new every round
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => start("reloaded") });
Instance.OnScriptInput("tpush_rescan", () => {
    const added = scan();
    if (LOG) say(`t=${tick} rescan: ${added.length} new push(es)` + (added.length ? ": " + added.map(describe).join(", ") : ""));
});
Instance.OnPlayerDisconnect(({ playerSlot }) => {
    st.delete(playerSlot);
    for (const Z of zones.values()) {
        Z.inside.delete(playerSlot);
        Z.brief.delete(playerSlot);
    }
});
// Respawned (or put back at spawn): no push is in its velocity any more and there is no momentum to hand over. Its old pushes go
// too: their OnEndTouch would only arrive a tick later and push it once at the spawn.
if (typeof Instance.OnPlayerReset === "function") Instance.OnPlayerReset(({ player }) => {
    const slot = slotOf(player);
    if (slot < 0) return;
    st.delete(slot);
    for (const Z of zones.values()) {
        Z.inside.delete(slot);
        Z.fresh.delete(slot);
        Z.brief.delete(slot);
    }
});
