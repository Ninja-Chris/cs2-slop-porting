// push_mass.js (v12): v12 adds CS:GO's 2000 u/s cap on physics objects (capSpeeds, end of file).
// push_mass.js (v11): make player pushes on physics props scale with prop mass, and let players' weight tip
// func_physbox balance plates.
//
// v11: pushes follow CS:GO's player physics box (Source 1 vphysics player controller + CBasePlayer, with
// cs_enable_player_physics_box 1). CSGO_PUSH = false gives v10's pushes. The weight pass is unchanged.
// - Running into a prop: the player's 85 kg box hits it at the player's speed. The impact is inelastic (the "player"
//   surfaceprop's elasticity is 0.001), so the prop gets the impulse of an 85 kg body stopping against it, any mass.
// - Pushing while touching: the box is steered half-way toward where the player wants to go each tick, and the
//   controller caps its speed into anything it touches at 50 u/s (SetPushSpeedLimit(50)), or 0 for anything over 350 kg
//   (SetPushMassLimit(350)). The box keeps pressing the prop at that speed; the prop's own floor friction decides how
//   fast it actually goes. Light props end up near 50 u/s (about 43 walking, 28 crouched), heavy ones slower.
// - Several players: each box pushes in turn. Off-centre pushes spin the prop about its yaw (a real angular velocity,
//   so it keeps turning after the push).
//
// v10: player weight on plates follows CS:GO's player physics box (read from Source 1's vphysics player controller;
// cs_enable_player_physics_box 1). Nothing else changed.
// - Standing still (no movement keys): the controller is off and the 85 kg player box rests on the plate as a normal
//   physics object -> 1 x weight (as v9).
// - Moving (movement keys held) on a plate within ~8 degrees of level: the controller leaves the box's vertical speed
//   alone (gravity still acts) and also adds one tick of gravity itself while touching ground -> 2 x weight.
// - Moving on a plate tilted further than that: the contact is no longer "floor" (normal z above -0.99) and the push
//   limit (350 kg) removes the box's speed into anything heavier -> about 0.
// - Landing: the box hits the plate at its fall speed -> one impulse of 85 x fall speed (not when moving on a tilted plate).
// CSGO_WEIGHT_RULES = false gives v9's 1 x weight for everyone on a plate.
//
// v9 fixes (nothing else changed)
// - Plates / physboxes / props that share a name are kept apart: a player on one same-named plate no longer tips another one,
//   and two same-named physboxes pushed at once both move (v8 merged them by name and moved only the first).
// - The angular-velocity unit guess is gone: when a plate stopped (hit its rest, the floor), the spin read back was ~1/57 of what
//   was written, v8 took that for a unit mismatch and scaled every later read by 57, spinning plates up to MAX_TILT_SPIN.
//   Reads and writes now use the same units (ANGVEL_DEGREES).
// - Standing on a plate is found with the player's footprint (as CS:GO's ground check), not a line from the centre, so a player
//   near a plate's edge counts; and a player riding a plate that tips fast still counts (v8 dropped anyone moving faster than
//   60 u/s vertically, so the weight cut out mid-tilt). Only a player jumping up (rising faster than 140) is off it.
// - A player no longer pushes the prop / physbox they are standing on (CS:GO's player ignores its ground entity when touching
//   physics objects: CBasePlayer::Touch). Standing on a tilted prop could push it along under them.
// - Keeps running after a tools-mode save (OnScriptReload).
//
// v8 changes
// - func_physbox / func_physbox_multiplayer can be pushed too. Their mass comes from the name, which
//   vmap_asset_slop_import.py now writes for physboxes as well (push_<mass>_<name>), or from PHYSBOX_MASS below.
// - Balance plates get a weight tag from the importer: push_<mass>_w<x>x<y>x<z>[a]_<name> (size in units;
//   "a" = only after RunScriptInput arm_<full name>, which the importer adds next to the plate's EnableMotion).
// - NEW weight pass: a player standing on an entry with weight: true in PHYSBOX_MASS presses it down
//   with REF_MASS of weight at their feet, so off-centre players tilt it (torque about its centre).
//   v7 only did horizontal pushes and deliberately ignored players standing on top.
// - The weight pass is UNTESTED in game. See WEIGHT_* below; DEBUG_WEIGHT prints what it applies.
//
// Runs inside the map via a point_script entity, so no server plugin is needed.
// The core push is the v3 push, which was confirmed working in-game. CONTACT_GRACE
// is now on (0.12 s). The other experimental switches (SMOOTH_PUSH, DETECT_BLOCKED,
// SLOW_PLAYER) are off and UNTESTED: turn them on one at a time.
// The damage settings are separate and do not change how props are pushed.
//
// How it works
// - Props stay normal physics props, so collisions with walls, players and other
//   props, and forces from other props, are still handled by the engine.
// - Each tick, for every player, a player-sized box is traced toward the prop
//   they are approaching. On contact the script remembers the player's approach
//   velocity, because the engine zeroes it while the prop blocks them. If the
//   player is pressed flush against the prop, the remembered prop keeps being pushed.
// - The push goes along the contact surface normal (straight into the face),
//   at a mass-based fraction of how fast the player was moving INTO the prop:
//   target = into * pushMass / (pushMass + propMass), with pushMass = 85 per player.
//   Sliding sideways along a prop adds no push. Props heavier than
//   PUSH_MASS_LIMIT are left to the engine.
// - Several players pushing the same prop in roughly the same direction add
//   their mass together.
// - By default the prop's speed is set straight to its target each tick. With
//   SMOOTH_PUSH on, it is ramped toward the target instead, so engine kicks and
//   script pushes fight less (less bouncing, more delay).
// - With DETECT_BLOCKED on, a prop that stops moving when pushed into a wall or
//   other static object is no longer fed speed for a moment, which avoids
//   bounce-back and jitter. Props blocked by other movable props are left to the engine.
// - A player is not hurt by a slow prop they are pushing themselves (e.g. squeezed
//   between it and a wall). All other prop damage is normal, unless
//   CANCEL_SLOW_DAMAGE is turned on.
//
// SETUP
// 1. Save in: <Steam>/steamapps/common/Counter-Strike Global Offensive/
//    content/csgo_addons/<your addon>/scripts/push_mass.js
// 2. In Hammer, add a point_script entity and pick this file in its cs_script field.
// 3. The mass comes from the prop's name: <name>__pm<mass> (written automatically by
//    rebuild_lighting.py, e.g. "crate1__pm120") or push_<mass>_<name> by hand
//    (e.g. "push_5_ball1"). Props without either are left alone.
// 4. Rotation assumes the prop's origin is near its horizontal centre.

import { Instance, CSInputs } from "cs_script/point_script";
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


// ---- Push strength ----
// Prop mass at which the prop moves at half the player's push speed.
// 85 = the player's physics mass in the Source engine (CBasePlayer::SetupVPhysicsShadow,
// solid.params.mass = 85). With target = into * REF/(REF + mass) this is the speed a
// perfectly inelastic collision between the player and the prop would give. The real
// push is solved inside vphysics (closed source), so treat it as the best engine-derived
// starting point and compare in-game. The previous value was 20.
const REF_MASS = 85;
// Props heavier than this cannot be pushed by a player (the engine sets the player
// controller's push mass limit to 350: m_pPhysicsController->SetPushMassLimit(350)).
// 0 = no limit (old behaviour: heavy props creep at MIN_PUSH_SPEED).
const PUSH_MASS_LIMIT = 350;
// Smallest speed (units/s) a pushed prop is given, so heavy props still creep.
const MIN_PUSH_SPEED = 10;
// Speed (units/s) into a prop needed to start a push.
const MIN_INTO_SPEED = 20;
// Props already moving faster than this are assumed hit by an explosion or
// bullet, and are left to the engine.
const MAX_EXTERNAL_SPEED = 400;

// ---- CS:GO push (v11) ----
// true = pushes follow CS:GO's player physics box (see the v11 notes at the top). false = v10's pushes (the
// REF_MASS / MIN_PUSH_SPEED / SMOOTH_PUSH / DETECT_BLOCKED / SLOW_PLAYER / ROT_GAIN settings only apply then).
const CSGO_PUSH = true;
// The player box's top speed into anything it touches while pushing (CBasePlayer: SetPushSpeedLimit(50)).
const PUSH_SPEED_LIMIT = 50;
// The player's wish speed: 250 runs (knife); CS:GO walks at 0.52 of it and crouches at 0.34.
const RUN_SPEED = 250;
const WALK_SPEED_MOD = 0.52;
const DUCK_SPEED_MOD = 0.34;
// Spin safety cap, radians per second (CS:GO has none; only guards against a bad ROT_K2 guess).
const MAX_PUSH_SPIN = 20;

// ---- func_physbox masses (v8) ----
// CS2 doesn't expose a physbox's mass to scripts, so it is listed here by entity name.
//   mass   - kg, compared against REF_MASS (85 = one player)
//   size   - [x, y, z] extent in units in the entity's own axes (for rotational inertia). Optional.
//   weight - true: players standing on it press it down at their feet (balance plates)
//   push   - false: no horizontal player push (balance plates on a pin would be shoved off it)
//   arm    - "auto": weight works whenever the plate exists. "input": weight only works after the
//            point_script gets RunScriptInput arm_<name> (send it at the same time as the plate's
//            EnableMotion); it is disarmed again on round start. Use "input" if the plate tilts while
//            it is still motion-disabled.
// mg_hellz mg4 disks: 1024x1024x8 plates resting on an 8x8 pin, massScale 0.2 in CS:GO. vbsp gives a brush with a hollow
// surfaceprop (plain "metal" is 0.1 in sheet metal) surface area x thickness x density, not volume x density: ~9400 kg for a
// metal 1024x1024x8 box, times 0.2 = ~1900 kg (the converter works it out from the brush since its shell-mass fix).
// Not needed for maps converted by vmap_asset_slop_import.py (the names carry this); use it for hand-made
// maps or to override a converted plate. Example:
//   mg4_disk_t: { mass: 1885, size: [1024, 1024, 8], weight: true, push: false, arm: "auto" },
const PHYSBOX_MASS = {};
const PHYSBOX_CLASSES = ["func_physbox", "func_physbox_multiplayer"];

// ---- Player weight on balance plates (v8, UNTESTED) ----
const WEIGHT_ON = true;
// World gravity in units/s^2 (sv_gravity).
const GRAVITY = 800;
// Multiplier on the player's weight. 1 = one REF_MASS player at full gravity (CS:GO: the player box is 85 kg).
const WEIGHT_GAIN = 1;
// CS:GO's rules for how hard the player box presses (see the v10 notes at the top). false = 1 x weight always (v9).
const CSGO_WEIGHT_RULES = true;
// Weight multiplier while moving on a plate whose top is within FLAT_NORMAL_Z of level, and on a steeper one.
const WALK_WEIGHT = 2;
const WALK_WEIGHT_TILTED = 0;
// vphysics treats a contact as floor (skipped by the push limit) when its normal z is at least 0.99 (about 8.1 degrees).
const FLAT_NORMAL_Z = 0.99;
// One impulse of REF_MASS x fall speed when a player lands on a plate.
const LANDING_IMPULSE = true;
// Also push the plate straight down (on a pin or in water the engine turns this into tilt / sinking).
const WEIGHT_LINEAR = true;
// Tilt speed cap in radians per second (the engine still adds its own gravity tilt on top).
const MAX_TILT_SPIN = 1.5;
// Angular velocity units the engine uses for Teleport/GetAbsAngularVelocity. Source uses degrees per
// second; if plates spin ~57x too fast or barely at all, flip this.
const ANGVEL_DEGREES = true;
// How far below the feet (units) to look for the plate when the engine doesn't report it as the ground entity.
const GROUND_TRACE = 6;
// Fallback k^2 (units^2) for weight entries without a size: I = mass * k^2 on every axis.
const WEIGHT_K2_DEFAULT = 2700;
// Print the weight each plate gets, about twice a second.
const DEBUG_WEIGHT = false;

// ---- Smoothing (EXPERIMENTAL, off) ----
// Ramp the prop's speed toward its target instead of setting it directly.
// Can reduce bouncing but adds delay.
const SMOOTH_PUSH = false;
// How fast (units/s per second) a pushed prop's speed may rise or fall toward
// its target when SMOOTH_PUSH is on. Higher = snappier, lower = smoother.
const PUSH_ACCEL = 2000;
const PUSH_DECEL = 2000;

// ---- Contact detection ----
// How far ahead (units) to look when a player starts approaching a prop.
const CONTACT_DIST = 8;
// How far (units) to look to KEEP a push going.
const HOLD_DIST = 3;
// EXPERIMENTAL: seconds a push is remembered after contact is briefly lost
// (stops flicker). 0 = off. Try 0.05 to 0.12.
const CONTACT_GRACE = 0.12;

// ---- Blocked props (wall, other props) (EXPERIMENTAL, off) ----
// Stop injecting speed into a prop that isn't moving when pushed. Can misfire
// and delay pushes, so leave off unless wall bounce-back/damage is the problem.
const DETECT_BLOCKED = false;
// If a prop we pushed at least this fast ends up moving slower than
// BLOCK_RATIO of that, it is treated as blocked.
const BLOCK_MIN_SPEED = 15;
const BLOCK_RATIO = 0.3;
// Seconds to stop pushing a blocked prop before trying again.
const BLOCK_HOLD = 0.3;
// How far (units) ahead of the prop's origin to look for a wall. Set it to
// roughly the prop's half-length plus a few units. Only static things count:
// if what's in the way is another movable prop, the prop is NOT treated as
// blocked, and the engine handles the prop-vs-prop forces as normal.
const BLOCK_REACH = 60;
// Height above the prop's origin for that look-ahead (keeps it off the floor).
const BLOCK_TRACE_Z = 12;

// ---- Rotation ----
const ROTATE_PROPS = true;
// Higher = props resist turning more. About (width^2 + depth^2) / 12 for a box.
const ROT_K2 = 400;
// Multiplier on the computed spin. 0 disables, 1 is physically proportional.
const ROT_GAIN = 0.5;
// Spin speed cap in radians per second.
const MAX_SPIN = 2.0;

// ---- Player ----
// Cut the part of the player's speed into the prop that exceeds the prop's
// speed, so player and prop move together instead of the player repeatedly
// bumping the prop. May cause rubber-banding (client prediction): try both.
const SLOW_PLAYER = false;

// ---- Damage ----
// Damage from a pushed prop moving slower than this (units/s) is cancelled.
const SAFE_PROP_SPEED = 150;
// false = props deal their normal damage at any speed (slow crushing included). true = cancel it below SAFE_PROP_SPEED.
const CANCEL_SLOW_DAMAGE = false;
// Pushing a prop into a wall makes the engine squeeze the pusher between the two. Cancel that, and only that:
// damage whose cause is a prop this same player was pushing through this script within the last
// SELF_PUSH_WINDOW seconds, while the prop is still slower than SAFE_PROP_SPEED. Everything else keeps its normal
// damage: another player squeezed by the prop, a prop that falls or is thrown, a prop pushed by someone else.
const NO_SELF_PUSH_DAMAGE = true;
const SELF_PUSH_WINDOW = 0.3;

// ---- Debug ----
// Print push events to the console for tuning.
const DEBUG = false;
// Print every player damage event (inflictor, types, amount) for tuning.
const DEBUG_DAMAGE = false;

// Player collision boxes (standing 72 tall, crouched 54), lifted slightly off
// the floor so the trace doesn't start touching the ground.
const HULL_STAND = { mins: { x: -16, y: -16, z: 4 }, maxs: { x: 16, y: 16, z: 72 } };
const HULL_DUCK = { mins: { x: -16, y: -16, z: 4 }, maxs: { x: 16, y: 16, z: 54 } };

// Per-player push memory: slot -> { vx, vy, px, py, t, prop }
//   vx,vy = remembered approach velocity, px,py = push direction (into the prop),
//   t = last time contact was confirmed, prop = the prop being pushed
const state = new Map();
// Per-prop push memory: prop entity -> { lastSet, lastTime, blockedUntil, pushers }
const propState = new Map();
let lastTime = 0;

// Template-spawned props share one name, so each is given a unique suffix.
// How often (seconds) to look for newly spawned props to rename.
const SCAN_INTERVAL = 0.5;
let nextScan = 0;
let uid = 0;
const UNIQUE_RE = /_u\d+$/;

// The mass is read from the prop's name, in either form:
//   <name>__pm<mass>   written by rebuild_lighting.py from the model's .phy (12.5 is written 12p5), e.g. "crate1__pm120"
//   push_<mass>_<name> set by hand, e.g. "push_200_crate1"
// A trailing "&0001" (template name fixup) or "_u3" (added below) is ignored.
function massFromName(name) {
    let m = /__pm(\d+(?:p\d+)?)(?:&\d+)?(?:_u\d+)?$/.exec(name);
    if (m) return Number(m[1].replace("p", "."));
    m = /^push_(\d+(?:\.\d+)?)_/.exec(name);
    return m ? Number(m[1]) : undefined;
}

function isProp(cls) {
    return cls === "prop_physics" || cls === "prop_physics_override";
}
function isPhysbox(cls) {
    return PHYSBOX_CLASSES.includes(cls);
}
// Table entry for a physbox (name with any template "&0001" fixup removed), else what its name tag says, or undefined.
const WEIGHT_TAG_RE = /^push_(\d+(?:\.\d+)?)_w(\d+)x(\d+)x(\d+)(a?)_/;
function physboxInfo(name) {
    const n = name.replace(/&\d+$/, "");
    if (PHYSBOX_MASS[n]) return PHYSBOX_MASS[n];
    const m = WEIGHT_TAG_RE.exec(n);
    if (!m) return undefined;
    return { mass: Number(m[1]), size: [Number(m[2]), Number(m[3]), Number(m[4])], weight: true, push: false, arm: m[5] ? "input" : "auto" };
}
// Mass of a pushable entity, or undefined if the script should leave it alone.
function massOf(ent, cls, name) {
    if (isProp(cls)) return massFromName(name);
    if (!isPhysbox(cls)) return undefined;
    const info = physboxInfo(name);
    if (info) return info.push === false ? undefined : info.mass;
    return massFromName(name);
}

// The same entity? The API has no entity index, and the same entity can come back as a different object, so entities sharing a
// name are told apart by position.
function sameEnt(a, b) {
    if (a === b) return true;
    if (!a || !b || !a.IsValid() || !b.IsValid() || a.GetClassName() !== b.GetClassName() || a.GetEntityName() !== b.GetEntityName()) return false;
    const p = a.GetAbsOrigin(), q = b.GetAbsOrigin();
    return Math.abs(p.x - q.x) + Math.abs(p.y - q.y) + Math.abs(p.z - q.z) < 0.01;
}
// The key a map already uses for this entity (or the entity itself).
function entKey(map, ent) {
    if (map.has(ent)) return ent;
    for (const k of map.keys()) if (sameEnt(k, ent)) return k;
    return ent;
}

// What a player stands on, like CS:GO's ground check (the player's box resting on it, not jumping up): the engine's ground
// entity, or, if that is the world or nothing (CS2 may not report physics objects as ground), a box trace down from the feet.
const FEET = { mins: { x: -16, y: -16, z: 0 }, maxs: { x: 16, y: 16, z: 2 } };
function standingOn(pawn) {
    const g = pawn.GetGroundEntity();
    if (g && g.IsValid() && !g.IsWorld()) return g;
    if (pawn.GetAbsVelocity().z > 140) return undefined; // jumping up off it
    const o = pawn.GetAbsOrigin();
    const tr = Instance.TraceBox({
        mins: FEET.mins,
        maxs: FEET.maxs,
        start: { x: o.x, y: o.y, z: o.z + 2 },
        end: { x: o.x, y: o.y, z: o.z - GROUND_TRACE },
        ignoreEntity: pawn,
        ignorePlayers: true,
    });
    const e = tr.didHit && !tr.startedInSolid ? tr.hitEntity : undefined;
    return e && e.IsValid() && !e.IsWorld() ? e : undefined;
}
// once per player per tick: slot -> entity stood on (or undefined)
let stand = new Map();
function standOf(slot, pawn) {
    if (!stand.has(slot)) stand.set(slot, standingOn(pawn));
    return stand.get(slot);
}

// True only if the prop is being pushed into a wall / static object, not into
// another movable prop and not into open space.
function isWallAhead(prop, px, py, pawn) {
    const po = prop.GetAbsOrigin();
    const tr = Instance.TraceLine({
        start: { x: po.x, y: po.y, z: po.z + BLOCK_TRACE_Z },
        end: { x: po.x + px * BLOCK_REACH, y: po.y + py * BLOCK_REACH, z: po.z + BLOCK_TRACE_Z },
        ignoreEntity: [prop, pawn],
        ignorePlayers: true,
    });
    if (!tr.didHit) return false;
    const e = tr.hitEntity;
    if (!e || e.IsWorld()) return true;
    const c = e.GetClassName();
    return !(c === "prop_physics" || c === "prop_physics_override" || c === "prop_physics_multiplayer" || isPhysbox(c));
}

// Give props that share a name (template-spawned copies) a unique name, e.g. z10props__pm120
// becomes z10props__pm120_u0, z10props__pm120_u1, ... The first prop with a name keeps it, and
// props with a unique name are never renamed. The mass still parses. Already-renamed props are
// skipped, and state for props that no longer exist is dropped.
function renameNewProps() {
    const seen = new Set();
    for (const cls of ["prop_physics", "prop_physics_override"]) {
        for (const ent of Instance.FindEntitiesByClass(cls)) {
            let name = ent.GetEntityName();
            if (massFromName(name) === undefined) continue;
            // Only props that share a name are renamed; the first keeps it, so outputs
            // aimed at a unique prop's name (e.g. "crate1__pm120") still reach it.
            if (!UNIQUE_RE.test(name)) {
                if (seen.has(name)) {
                    name = `${name}_u${uid++}`;
                    ent.SetEntityName(name);
                } else {
                    seen.add(name);
                }
            }
        }
    }
    for (const key of [...propState.keys()]) {
        if (!key.IsValid()) propState.delete(key);
    }
}

// Plates with arm: "input" only take weight after RunScriptInput arm_<name>; disarm_<name> stops it.
// Names come from PHYSBOX_MASS and from the physboxes in the map (importer weight tags).
const armed = new Set();
const armInputs = new Set();
function registerArmInputs() {
    const names = Object.keys(PHYSBOX_MASS);
    for (const cls of PHYSBOX_CLASSES) for (const ent of Instance.FindEntitiesByClass(cls)) names.push(ent.GetEntityName().replace(/&\d+$/, ""));
    for (const pname of names) {
        const info = physboxInfo(pname);
        if (!info || !info.weight || info.arm !== "input" || armInputs.has(pname)) continue;
        armInputs.add(pname);
        Instance.OnScriptInput(`arm_${pname}`, () => armed.add(pname));
        Instance.OnScriptInput(`disarm_${pname}`, () => armed.delete(pname));
    }
}
try {
    registerArmInputs(); // entities may not exist yet at load; OnActivate / OnRoundStart register them again
} catch (e) {}

Instance.OnRoundStart(() => {
    armed.clear();
    registerArmInputs();
    renameNewProps();
});

function start() {
    Instance.Msg("push_mass: script activated");
    registerArmInputs();
    renameNewProps();
    lastTime = Instance.GetGameTime();
    setThinkOnce(tick);
    Instance.SetNextThink(lastTime);
}
Instance.OnActivate(start);
// In tools mode, saving the .js reloads the script and clears every callback (the think too); OnActivate doesn't fire again.
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: start });

Instance.OnBeforePlayerDamage((event) => {
    const inf = event.inflictor;
    const valid = inf && inf.IsValid();
    const name = valid ? inf.GetEntityName() : "";

    if (DEBUG_DAMAGE) {
        Instance.Msg(
            `damage ${event.damage} types ${event.damageTypes} inflictor ${
                valid ? inf.GetClassName() + "/" + name : "none"
            }`,
        );
    }

    if (!valid || massOf(inf, inf.GetClassName(), name) === undefined) return;

    if (NO_SELF_PUSH_DAMAGE) {
        const ps = propState.get(entKey(propState, inf));
        const ctrl = event.player ? event.player.GetPlayerController() : undefined;
        const pushedAt = ps && ctrl && ps.pushers ? ps.pushers.get(ctrl.GetPlayerSlot()) : undefined;
        if (pushedAt !== undefined && Instance.GetGameTime() - pushedAt < SELF_PUSH_WINDOW) {
            const pv = inf.GetAbsVelocity();
            if (Math.hypot(pv.x, pv.y, pv.z) < SAFE_PROP_SPEED) {
                if (DEBUG) Instance.Msg(`${name}: damage to its own pusher cancelled`);
                return { abort: true };
            }
        }
    }

    if (!CANCEL_SLOW_DAMAGE) return;

    const v = inf.GetAbsVelocity();
    const speed = Math.hypot(v.x, v.y, v.z);
    if (speed < SAFE_PROP_SPEED) {
        if (DEBUG) Instance.Msg(`${name}: damage cancelled (prop speed ${speed.toFixed(0)})`);
        return { abort: true };
    }
});

// ---- Weight pass (v8) ----
// Source QAngle -> the entity's local axes in world space (forward = local x, left = local y, up = local z).
function axes(a) {
    const d = Math.PI / 180;
    const sp = Math.sin(a.pitch * d), cp = Math.cos(a.pitch * d);
    const sy = Math.sin(a.yaw * d), cy = Math.cos(a.yaw * d);
    const sr = Math.sin(a.roll * d), cr = Math.cos(a.roll * d);
    const f = { x: cp * cy, y: cp * sy, z: -sp };
    const r = { x: -sr * sp * cy + cr * sy, y: -sr * sp * sy - cr * cy, z: -sr * cp };
    const u = { x: cr * sp * cy + sr * sy, y: cr * sp * sy - sr * cy, z: cr * cp };
    return [f, { x: -r.x, y: -r.y, z: -r.z }, u];
}
const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
let nextWeightMsg = 0;

// CS:GO's controller only runs while the player's wish velocity isn't zero, i.e. while movement keys push them.
function keysHeld(pawn) {
    if (typeof pawn.IsInputPressed !== "function") return false;
    const f = (pawn.IsInputPressed(CSInputs.FORWARD) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.BACK) ? 1 : 0);
    const s = (pawn.IsInputPressed(CSInputs.RIGHT) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.LEFT) ? 1 : 0);
    return f !== 0 || s !== 0;
}
// How level the face a player stands on is: the largest |z| of the plate's own axes (1 = level, 0.99 = ~8.1 degrees).
function faceUpZ(ent) {
    const ax = axes(ent.GetAbsAngles());
    return Math.max(Math.abs(ax[0].z), Math.abs(ax[1].z), Math.abs(ax[2].z));
}
// slot -> { on, vz, o, t } from the previous tick, to spot landings
const airState = new Map();

// Every player standing on a weight plate gives it an impulse of (player weight * dt * k) straight down at
// their feet (k from CS:GO's rules, plus a landing impulse): the plate's velocity changes by J / mass and its spin by
// I^-1 (r x J), r measured from the plate's origin (its centre in a converted map). A centred player only presses down;
// an off-centre one tilts it.
function applyWeight(dt, now) {
    const plates = new Map(); // plate entity -> { ent, name, info, feet: [] } (same-named plates kept apart)
    for (let slot = 0; slot < 64; slot++) {
        const controller = Instance.GetPlayerController(slot);
        const pawn = controller ? controller.GetPlayerPawn() : undefined;
        if (!pawn || !pawn.IsAlive()) continue;
        const ground = standOf(slot, pawn);
        const o = pawn.GetAbsOrigin();
        const prev = airState.get(slot);
        airState.set(slot, { on: !!ground, vz: pawn.GetAbsVelocity().z, o, t: now });
        if (!ground || !isPhysbox(ground.GetClassName())) continue;
        const name = ground.GetEntityName().replace(/&\d+$/, "");
        const info = physboxInfo(name);
        if (!info || !info.weight) continue;
        if (info.arm === "input" && !armed.has(name)) continue;
        const key = entKey(plates, ground);
        let p = plates.get(key);
        if (!p) plates.set(key, (p = { ent: key, name, info, feet: [] }));
        let k = 1, land = 0;
        if (CSGO_WEIGHT_RULES) {
            const moving = keysHeld(pawn);
            const flat = faceUpZ(ground) >= FLAT_NORMAL_Z;
            k = !moving ? 1 : flat ? WALK_WEIGHT : WALK_WEIGHT_TILTED;
            // Landed this tick: in the air last tick, falling, and not a teleport or respawn.
            const fresh = prev && now - prev.t < 0.1 && Math.hypot(o.x - prev.o.x, o.y - prev.o.y, o.z - prev.o.z) < 64 + -prev.vz * dt * 2;
            if (LANDING_IMPULSE && fresh && !prev.on && prev.vz < 0 && !(moving && !flat)) land = -prev.vz;
        }
        p.feet.push({ o, k, land });
    }

    const debug = DEBUG_WEIGHT && now >= nextWeightMsg;
    if (debug) nextWeightMsg = now + 0.5;

    for (const { ent, name, info, feet } of plates.values()) {
        const m = info.mass;
        const c = ent.GetAbsOrigin();
        // Torque impulse (r x J) with J = (0, 0, -j): (-ry*j, rx*j, 0). j = this player's impulse, kg*u/s, pointing down.
        const tq = { x: 0, y: 0, z: 0 };
        let jSum = 0;
        for (const f of feet) {
            const j = REF_MASS * WEIGHT_GAIN * (GRAVITY * dt * f.k + f.land);
            tq.x += -(f.o.y - c.y) * j;
            tq.y += (f.o.x - c.x) * j;
            jSum += j;
        }
        if (jSum <= 0) continue; // only players moving on a tilted plate: CS:GO's box doesn't press it
        // Spin change = I^-1 * torque, with I diagonal in the plate's own axes.
        const ax = axes(ent.GetAbsAngles());
        let I;
        if (info.size) {
            const [sx, sy, sz] = info.size;
            I = [(m * (sy * sy + sz * sz)) / 12, (m * (sx * sx + sz * sz)) / 12, (m * (sx * sx + sy * sy)) / 12];
        } else {
            I = [m * WEIGHT_K2_DEFAULT, m * WEIGHT_K2_DEFAULT, m * WEIGHT_K2_DEFAULT];
        }
        const dw = { x: 0, y: 0, z: 0 }; // rad/s, world axes
        for (let k = 0; k < 3; k++) {
            const wl = dot(tq, ax[k]) / I[k];
            dw.x += ax[k].x * wl;
            dw.y += ax[k].y * wl;
            dw.z += ax[k].z * wl;
        }
        // Read and written in the same units (ANGVEL_DEGREES). DEBUG_WEIGHT prints both, to check them in game.
        const unit = ANGVEL_DEGREES ? 180 / Math.PI : 1;
        const w0 = ent.GetAbsAngularVelocity();
        let w = { x: w0.x / unit + dw.x, y: w0.y / unit + dw.y, z: w0.z / unit + dw.z };
        // Cap the tilt (spin about horizontal axes) without touching any yaw spin.
        const tilt = Math.hypot(w.x, w.y);
        if (tilt > MAX_TILT_SPIN) {
            w.x *= MAX_TILT_SPIN / tilt;
            w.y *= MAX_TILT_SPIN / tilt;
        }
        const angularVelocity = { x: w.x * unit, y: w.y * unit, z: w.z * unit };
        if (WEIGHT_LINEAR) {
            const v = ent.GetAbsVelocity();
            const velocity = { x: v.x, y: v.y, z: v.z - jSum / m };
            ent.Teleport({ velocity, angularVelocity });
        } else {
            ent.Teleport({ angularVelocity });
        }
        if (debug) {
            Instance.Msg(
                `weight ${name}: players ${feet.length} (x${feet.map((f) => f.k).join(" x")}${feet.some((f) => f.land) ? ", landing " + feet.filter((f) => f.land).map((f) => f.land.toFixed(0)).join(" ") + " u/s" : ""}), spin read ${w0.x.toFixed(2)} ${w0.y.toFixed(2)} ${w0.z.toFixed(2)}, ` +
                    `added ${(dw.x * unit).toFixed(3)} ${(dw.y * unit).toFixed(3)} ${(dw.z * unit).toFixed(3)} (${ANGVEL_DEGREES ? "deg" : "rad"}/s)`,
            );
        }
    }
}

// CS:GO's wish direction from the movement keys and view yaw (undefined when no key moves the player)
function wishDir(pawn) {
    if (typeof pawn.IsInputPressed !== "function") return undefined;
    const f = (pawn.IsInputPressed(CSInputs.FORWARD) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.BACK) ? 1 : 0);
    const s = (pawn.IsInputPressed(CSInputs.RIGHT) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.LEFT) ? 1 : 0);
    if (!f && !s) return undefined;
    const y = (pawn.GetEyeAngles().yaw * Math.PI) / 180;
    const x = Math.cos(y) * f + Math.sin(y) * s, yy = Math.sin(y) * f - Math.cos(y) * s, l = Math.hypot(x, yy);
    return { x: x / l, y: yy / l };
}

// One prop, every player's box pushing it this tick (CS:GO model, see the v11 notes). Each box is an 85 kg body with
// no spin of its own moving at u into the prop, and each contact is inelastic: it pushes (never pulls) until the box and
// the contact point move together. One box: J = (u - v_contact) / (1/85 + 1/m + lever^2 / I), applied at the player's
// side offset (lever) so it also spins the prop about its yaw. Several boxes are solved together (a few passes over the
// contacts, as a physics solver does), so players pushing from opposite sides squeeze the prop instead of the last one
// winning. u = the player's speed for a new touch (any mass), or for a held push min(50, (wish speed into the prop -
// contact speed) / 2), and 0 for props over PUSH_MASS_LIMIT.
function pushCsgo(g, now) {
    const { prop, name, mass } = g;
    const pr = prop.GetAbsVelocity();
    if (Math.hypot(pr.x, pr.y) > MAX_EXTERNAL_SPEED) return;
    const po = prop.GetAbsOrigin();
    const info = isPhysbox(prop.GetClassName()) ? physboxInfo(name) : undefined;
    const k2 = info && info.size ? (info.size[0] * info.size[0] + info.size[1] * info.size[1]) / 12 : ROT_K2;
    const I = mass * k2;
    const unit = ANGVEL_DEGREES ? 180 / Math.PI : 1;
    const w0 = prop.GetAbsAngularVelocity();
    let vx = pr.x, vy = pr.y, wz = w0.z / unit;
    const contacts = g.list.map((e) => {
        const lever = ROTATE_PROPS ? (e.o.x - po.x) * e.py - (e.o.y - po.y) * e.px : 0;
        const vc = vx * e.px + vy * e.py + wz * lever;
        const u = e.impact ? e.approach : PUSH_MASS_LIMIT > 0 && mass > PUSH_MASS_LIMIT ? 0 : Math.min(PUSH_SPEED_LIMIT, 0.5 * (e.wishN - vc));
        return { e, lever, u, J: 0, k: 1 / (1 / REF_MASS + 1 / mass + (ROTATE_PROPS ? (lever * lever) / I : 0)) };
    });
    for (let pass = 0; pass < (contacts.length > 1 ? 50 : 1); pass++) {
        for (const c of contacts) {
            const vc = vx * c.e.px + vy * c.e.py + wz * c.lever;
            const J = Math.max(0, c.J + (c.u - c.J / REF_MASS - vc) * c.k); // box speed after its own impulse: u - J/85
            const dj = J - c.J;
            c.J = J;
            vx += (dj / mass) * c.e.px;
            vy += (dj / mass) * c.e.py;
            wz += (dj * c.lever) / I;
        }
    }
    if (!ps(prop).pushers) ps(prop).pushers = new Map();
    for (const e of g.list) ps(prop).pushers.set(e.slot, now); // every player currently pushing this prop
    const used = contacts.filter((c) => c.J > 1e-6);
    if (!used.length) return;
    wz = Math.max(-MAX_PUSH_SPIN, Math.min(MAX_PUSH_SPIN, wz));
    const velocity = { x: vx, y: vy, z: pr.z };
    prop.Teleport(ROTATE_PROPS ? { velocity, angularVelocity: { x: w0.x, y: w0.y, z: wz * unit } } : { velocity });
    if (DEBUG) {
        Instance.Msg(
            `${name} (mass ${mass}): ${used.map((c) => `${c.e.impact ? "impact" : "push"} ${c.u.toFixed(0)} u/s`).join(", ")}; speed ${Math.hypot(pr.x, pr.y).toFixed(0)} -> ${Math.hypot(vx, vy).toFixed(0)} u/s, spin ${(w0.z / unit).toFixed(2)} -> ${wz.toFixed(2)} rad/s`,
        );
    }
}
// The prop's bookkeeping (who pushed it, for the damage filter)
function ps(prop) {
    const pk = entKey(propState, prop);
    let s = propState.get(pk);
    if (!s) propState.set(pk, (s = { lastSet: 0, lastTime: -1, blockedUntil: 0 }));
    return s;
}

function tick() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastTime, 0.001), 0.1);
    lastTime = now;
    stand = new Map();

    if (now >= nextScan) {
        nextScan = now + SCAN_INTERVAL;
        renameNewProps();
    }

    if (WEIGHT_ON) applyWeight(dt, now);

    // Every player pushing the same prop is collected first, then each prop is pushed once with their combined effect.
    const groups = new Map();
    for (let slot = 0; slot < 64; slot++) {
        const controller = Instance.GetPlayerController(slot);
        const pawn = controller ? controller.GetPlayerPawn() : undefined;
        if (!pawn || !pawn.IsAlive()) {
            state.delete(slot);
            continue;
        }

        const pv = pawn.GetAbsVelocity();
        const pSpeed = Math.hypot(pv.x, pv.y);
        const st = state.get(slot);

        // Which way to look for a prop, and how far.
        let tx, ty, dist;
        const wd = CSGO_PUSH ? wishDir(pawn) : undefined;
        if (st) {
            tx = st.px;
            ty = st.py;
            dist = HOLD_DIST;
        } else if (pSpeed >= MIN_INTO_SPEED) {
            tx = pv.x / pSpeed;
            ty = pv.y / pSpeed;
            dist = CONTACT_DIST;
        } else if (wd) {
            // CS:GO: the box pushes from the keys, even when the player is already stopped against the prop
            tx = wd.x;
            ty = wd.y;
            dist = CONTACT_DIST;
        } else {
            continue;
        }

        const o = pawn.GetAbsOrigin();
        const hull = pawn.IsCrouching() || pawn.IsCrouched() ? HULL_DUCK : HULL_STAND;

        const tr = Instance.TraceBox({
            mins: hull.mins,
            maxs: hull.maxs,
            start: o,
            end: { x: o.x + tx * dist, y: o.y + ty * dist, z: o.z },
            ignoreEntity: pawn,
            ignorePlayers: true,
        });
        // Pressed flush against the prop the box can start inside it and report no hit. Keep pushing the prop
        // remembered from the last tick (same direction) instead of forgetting the push, which is what made a
        // prop impossible to push again until the player backed off and rammed it.
        let prop = tr.hitEntity;
        let inside = false;
        if (tr.startedInSolid && !tr.didHit && st && st.prop && st.prop.IsValid()) {
            prop = st.prop;
            inside = true;
        } else if (!tr.didHit || tr.startedInSolid || !tr.hitEntity) {
            // Brief loss of contact: keep the remembered push for a moment.
            if (st) st.touched = false; // CS:GO: the next touch is a new impact
            if (st && now - st.t < CONTACT_GRACE) continue;
            state.delete(slot);
            continue;
        }

        // CS:GO's player doesn't push what it stands on (CBasePlayer::Touch ignores its ground entity).
        if (sameEnt(standOf(slot, pawn), prop)) {
            state.delete(slot);
            continue;
        }

        const cls = prop.GetClassName();
        const name = prop.GetEntityName();
        const mass = massOf(prop, cls, name);
        if (mass === undefined) {
            state.delete(slot);
            continue;
        }
        if (!CSGO_PUSH && PUSH_MASS_LIMIT > 0 && mass > PUSH_MASS_LIMIT) {
            // Too heavy for the player to move; leave it to the engine.
            state.delete(slot);
            continue;
        }

        // Surface normal (horizontal), flipped to face the player; push is the opposite.
        let nx = inside ? -st.px : tr.normal.x;
        let ny = inside ? -st.py : tr.normal.y;
        const nh = Math.hypot(nx, ny);
        if (nh < 0.5) {
            // Top/bottom surface (e.g. standing on it): not a push.
            state.delete(slot);
            continue;
        }
        nx /= nh;
        ny /= nh;
        if (nx * tx + ny * ty > 0) {
            nx = -nx;
            ny = -ny;
        }
        const px = -nx;
        const py = -ny;

        if (CSGO_PUSH) {
            // A new touch is an impact at the player's speed; a continued one is the box pressing from the keys.
            const touchedBefore = !!(st && st.touched && sameEnt(st.prop, prop));
            const ducked = typeof pawn.IsDucked === "function" ? pawn.IsDucked() : pawn.IsCrouched();
            const W = wd ? RUN_SPEED * (ducked ? DUCK_SPEED_MOD : pawn.IsInputPressed(CSInputs.WALK) ? WALK_SPEED_MOD : 1) : 0;
            const wishN = wd ? W * (wd.x * px + wd.y * py) : 0;
            const approach = pv.x * px + pv.y * py;
            state.set(slot, { vx: pv.x, vy: pv.y, px, py, t: now, prop, touched: true });
            if (touchedBefore ? wishN <= 0 : approach <= 0) continue; // touching, not pushing into it
            const gk = entKey(groups, prop);
            let g = groups.get(gk);
            if (!g) groups.set(gk, (g = { prop: gk, name, mass, list: [] }));
            g.list.push({ slot, pawn, pv, o, px, py, into: approach, impact: !touchedBefore, approach, wishN });
            continue;
        }

        // Approach velocity: use the live one if it is pushing harder than the
        // remembered one (player moving freely into the prop), otherwise keep
        // the remembered one (player blocked by the prop, or sliding along it).
        let vx = pv.x;
        let vy = pv.y;
        if (st && st.vx * px + st.vy * py > pv.x * px + pv.y * py) {
            vx = st.vx;
            vy = st.vy;
        }
        const into = vx * px + vy * py;
        if (into < MIN_INTO_SPEED) {
            state.delete(slot);
            continue;
        }
        state.set(slot, { vx, vy, px, py, t: now, prop });

        const gk = entKey(groups, prop); // per entity: same-named physboxes are separate pushes
        let g = groups.get(gk);
        if (!g) {
            g = { prop: gk, name, mass, list: [] };
            groups.set(gk, g);
        }
        g.list.push({ slot, pawn, pv, o, px, py, into });
    }

    for (const g of groups.values()) {
        if (CSGO_PUSH) {
            pushCsgo(g, now);
            continue;
        }
        const { prop, name, mass } = g;
        // The strongest pusher sets the direction. Pushers within ~60 degrees of it add their weight; players pushing
        // from other directions do not help (opposite ones would only cancel).
        let lead = g.list[0];
        for (const e of g.list) if (e.into > lead.into) lead = e;
        const { px, py, into, pawn, pv, o } = lead;
        const team = g.list.filter((e) => e.px * px + e.py * py > 0.5);
        const crowd = team.length;

        const pr = prop.GetAbsVelocity();
        if (Math.hypot(pr.x, pr.y) > MAX_EXTERNAL_SPEED) continue;

        const pk = entKey(propState, prop);
        let ps = propState.get(pk);
        if (!ps) {
            ps = { lastSet: 0, lastTime: -1, blockedUntil: 0 };
            propState.set(pk, ps);
        }
        // Pinned against something: let the engine settle instead of pumping speed in.
        if (DETECT_BLOCKED && now < ps.blockedUntil) continue;

        // How fast the prop is actually moving away from the player right now.
        const along = pr.x * px + pr.y * py;

        // We pushed it last tick but it barely moved: it is blocked. Remove its
        // velocity along the push (including any bounce back toward the player).
        if (
            DETECT_BLOCKED &&
            ps.lastSet >= BLOCK_MIN_SPEED &&
            now - ps.lastTime < 0.15 &&
            along < BLOCK_RATIO * ps.lastSet &&
            isWallAhead(prop, px, py, pawn)
        ) {
            prop.Teleport({
                velocity: { x: pr.x - px * along, y: pr.y - py * along, z: pr.z },
            });
            ps.blockedUntil = now + BLOCK_HOLD;
            ps.lastSet = 0;
            if (DEBUG) Instance.Msg(`${name}: blocked, holding`);
            continue;
        }

        // Set (or, with SMOOTH_PUSH, ramp) the prop's speed toward its mass-based target.
        const pushMass = REF_MASS * crowd; // n players push with n times a player's mass
        const target = Math.max(MIN_PUSH_SPEED, (into * pushMass) / (pushMass + mass));
        let newAlong = !SMOOTH_PUSH
            ? target
            : along < target
              ? Math.min(target, along + PUSH_ACCEL * dt)
              : Math.max(target, along - PUSH_DECEL * dt);
        newAlong = Math.max(newAlong, 0); // never pull the prop toward the player
        ps.lastSet = newAlong;
        ps.lastTime = now;

        const velocity = { x: px * newAlong, y: py * newAlong, z: pr.z };

        let angles;
        let omega = 0;
        if (ROTATE_PROPS) {
            // Sideways offset of the player from the prop's centre line along the
            // push direction (z of r x push). Centre push = 0, corner push = max.
            const po = prop.GetAbsOrigin();
            let lever = 0;
            for (const e of team) lever += (e.o.x - po.x) * e.py - (e.o.y - po.y) * e.px;
            omega = (ROT_GAIN * newAlong * lever) / ROT_K2;
            omega = Math.max(-MAX_SPIN, Math.min(MAX_SPIN, omega));
            const ang = prop.GetAbsAngles();
            angles = {
                pitch: ang.pitch,
                yaw: ang.yaw + ((omega * dt) * 180) / Math.PI,
                roll: ang.roll,
            };
        }

        prop.Teleport(angles ? { velocity, angles } : { velocity });
        if (!ps.pushers) ps.pushers = new Map();
        for (const e of g.list) ps.pushers.set(e.slot, now); // every player currently pushing this prop

        // Cut only the part of each player's speed into the prop that exceeds
        // the prop's speed; sideways movement is left alone.
        if (SLOW_PLAYER) {
            for (const e of team) {
                const intoCur = e.pv.x * px + e.pv.y * py;
                if (intoCur > newAlong) {
                    const cut = intoCur - newAlong;
                    e.pawn.Teleport({
                        velocity: { x: e.pv.x - px * cut, y: e.pv.y - py * cut, z: e.pv.z },
                    });
                }
            }
        }

        if (DEBUG) {
            Instance.Msg(
                `${name} (mass ${mass}): pushers ${crowd} into ${into.toFixed(0)} target ${target.toFixed(0)} set ${newAlong.toFixed(0)} u/s, spin ${omega.toFixed(2)} rad/s, dt ${(dt * 1000).toFixed(1)}ms`,
            );
        }
    }

    capSpeeds();
    Instance.SetNextThink(Instance.GetGameTime());
}

// CS:GO's physics never let an object go faster than 2000 u/s (vphysics' default performance settings, maxVelocity 2000). CS2 has
// no such cap: mg_mario_party's mushroom, driven by a 1000 u/s/s thruster (Ignore Mass) for 1-3 s at a time, went well past it and
// left the spawn room through the walls (a 20-unit cube moving 30+ units a tick). Every physics prop / physbox is held to 2000.
const CSGO_MAX_SPEED = 2000;
const CAP_CLASSES = ["prop_physics", "prop_physics_override", "prop_physics_multiplayer", "func_physbox", "func_physbox_multiplayer"];
function capSpeeds() {
    for (const cls of CAP_CLASSES) {
        for (const ent of Instance.FindEntitiesByClass(cls)) {
            if (!ent.IsValid()) continue;
            const v = ent.GetAbsVelocity();
            const sp = Math.hypot(v.x, v.y, v.z);
            if (sp <= CSGO_MAX_SPEED) continue;
            const f = CSGO_MAX_SPEED / sp;
            ent.Teleport({ velocity: { x: v.x * f, y: v.y * f, z: v.z * f } });
            if (DEBUG) Instance.Msg(`push_mass: ${ent.GetEntityName() || cls} capped ${sp.toFixed(0)} -> ${CSGO_MAX_SPEED} u/s`);
        }
    }
}

