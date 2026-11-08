// push_mass.js (v8): make player pushes on physics props scale with prop mass, and (new in v8)
// let players' weight tip func_physbox balance plates.
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

import { Instance } from "cs_script/point_script";

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
// mg_hellz mg4 disks: 1024x1024x8 plates resting on an 8x8 pin, massScale 0.2 in CS:GO. Source 1
// capped a brush's physics mass at 50000, times 0.2 = 10000 (estimate: tune with WEIGHT_GAIN).
// Not needed for maps converted by vmap_asset_slop_import.py (the names carry this); use it for hand-made
// maps or to override a converted plate. Example:
//   mg4_disk_t: { mass: 10000, size: [1024, 1024, 8], weight: true, push: false, arm: "auto" },
const PHYSBOX_MASS = {};
const PHYSBOX_CLASSES = ["func_physbox", "func_physbox_multiplayer"];

// ---- Player weight on balance plates (v8, UNTESTED) ----
const WEIGHT_ON = true;
// World gravity in units/s^2 (sv_gravity).
const GRAVITY = 800;
// Multiplier on the player's weight. 1 = one REF_MASS player at full gravity. Raise if plates tip too
// slowly compared with CS:GO, lower if too fast.
const WEIGHT_GAIN = 1;
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
// Per-prop push memory: name -> { lastSet, lastTime, blockedUntil, pushers }
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
    const live = new Set();
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
            live.add(name);
        }
    }
    for (const key of propState.keys()) {
        if (!live.has(key)) propState.delete(key);
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

Instance.OnActivate(() => {
    Instance.Msg("push_mass: script activated");
    registerArmInputs();
    renameNewProps();
    lastTime = Instance.GetGameTime();
    Instance.SetThink(tick);
    Instance.SetNextThink(lastTime);
});

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
        const ps = propState.get(name);
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
const weightState = new Map(); // plate name -> { w: angular velocity last written, t: time }
let readScale = 1;

// Every player standing on a weight plate gives it an impulse of (player weight * dt) straight down at
// their feet: the plate's velocity changes by J / mass and its spin by I^-1 (r x J), r measured from the
// plate's origin (its centre in a converted map). A centred player only presses down; an off-centre one tilts it.
function applyWeight(dt, now) {
    const plates = new Map(); // name -> { ent, info, feet: [] }
    for (let slot = 0; slot < 64; slot++) {
        const controller = Instance.GetPlayerController(slot);
        const pawn = controller ? controller.GetPlayerPawn() : undefined;
        if (!pawn || !pawn.IsAlive()) continue;
        // What the player stands on: the engine's ground entity, or (if that is not a physbox, e.g. CS2 does not report physics
        // objects as ground) a short trace down from the feet.
        let ground = pawn.GetGroundEntity();
        if (!ground || !ground.IsValid() || !isPhysbox(ground.GetClassName())) {
            const vz = pawn.GetAbsVelocity().z;
            if (Math.abs(vz) > 60) continue; // jumping / falling
            const o = pawn.GetAbsOrigin();
            const tr = Instance.TraceLine({ start: { x: o.x, y: o.y, z: o.z + 4 }, end: { x: o.x, y: o.y, z: o.z - GROUND_TRACE }, ignoreEntity: pawn, ignorePlayers: true });
            ground = tr.didHit && !tr.startedInSolid ? tr.hitEntity : undefined;
            if (!ground || !ground.IsValid() || ground.IsWorld() || !isPhysbox(ground.GetClassName())) continue;
        }
        const name = ground.GetEntityName().replace(/&\d+$/, "");
        const info = physboxInfo(name);
        if (!info || !info.weight) continue;
        if (info.arm === "input" && !armed.has(name)) continue;
        let p = plates.get(name);
        if (!p) plates.set(name, (p = { ent: ground, info, feet: [] }));
        p.feet.push(pawn.GetAbsOrigin());
    }

    const debug = DEBUG_WEIGHT && now >= nextWeightMsg;
    if (debug) nextWeightMsg = now + 0.5;

    for (const [name, { ent, info, feet }] of plates) {
        const m = info.mass;
        const j = REF_MASS * WEIGHT_GAIN * GRAVITY * dt; // impulse per player, kg*u/s, pointing down
        const c = ent.GetAbsOrigin();
        // Torque impulse (r x J) with J = (0, 0, -j): (-ry*j, rx*j, 0)
        const tq = { x: 0, y: 0, z: 0 };
        for (const f of feet) {
            tq.x += -(f.y - c.y) * j;
            tq.y += (f.x - c.x) * j;
        }
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
        const unit = ANGVEL_DEGREES ? 180 / Math.PI : 1;
        const w0 = ent.GetAbsAngularVelocity();
        // The spin read back is compared with what was written last tick: if the engine reports it in other units than it takes
        // (degrees vs radians, a factor of ~57), the read is rescaled, otherwise every tick would throw most of the tilt away.
        const st = weightState.get(name);
        if (st && now - st.t < 0.1 && readScale === 1) {
            const wr = Math.hypot(st.w.x, st.w.y), rr = Math.hypot(w0.x, w0.y);
            if (wr > 1e-3 && rr > 0) {
                const q = rr / wr;
                if (q > 30 && q < 120) readScale = 1 / 57.2958;
                else if (q > 1 / 120 && q < 1 / 30) readScale = 57.2958;
                if (readScale !== 1) Instance.Msg(`push_mass: angular velocity is read back in other units than it is set; rescaling reads by ${readScale.toFixed(4)}`);
            }
        }
        let w = { x: (w0.x * readScale) / unit + dw.x, y: (w0.y * readScale) / unit + dw.y, z: (w0.z * readScale) / unit + dw.z };
        // Cap the tilt (spin about horizontal axes) without touching any yaw spin.
        const tilt = Math.hypot(w.x, w.y);
        if (tilt > MAX_TILT_SPIN) {
            w.x *= MAX_TILT_SPIN / tilt;
            w.y *= MAX_TILT_SPIN / tilt;
        }
        const angularVelocity = { x: w.x * unit, y: w.y * unit, z: w.z * unit };
        weightState.set(name, { w: angularVelocity, t: now });
        if (WEIGHT_LINEAR) {
            const v = ent.GetAbsVelocity();
            const velocity = { x: v.x, y: v.y, z: v.z - (j * feet.length) / m };
            ent.Teleport({ velocity, angularVelocity });
        } else {
            ent.Teleport({ angularVelocity });
        }
        if (debug) {
            Instance.Msg(
                `weight ${name}: players ${feet.length}, spin read ${w0.x.toFixed(2)} ${w0.y.toFixed(2)} ${w0.z.toFixed(2)}, ` +
                    `added ${(dw.x * unit).toFixed(3)} ${(dw.y * unit).toFixed(3)} ${(dw.z * unit).toFixed(3)} (${ANGVEL_DEGREES ? "deg" : "rad"}/s)`,
            );
        }
    }
}

function tick() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastTime, 0.001), 0.1);
    lastTime = now;

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
        if (st) {
            tx = st.px;
            ty = st.py;
            dist = HOLD_DIST;
        } else if (pSpeed >= MIN_INTO_SPEED) {
            tx = pv.x / pSpeed;
            ty = pv.y / pSpeed;
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
            if (st && now - st.t < CONTACT_GRACE) continue;
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
        if (PUSH_MASS_LIMIT > 0 && mass > PUSH_MASS_LIMIT) {
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

        let g = groups.get(name);
        if (!g) {
            g = { prop, name, mass, list: [] };
            groups.set(name, g);
        }
        g.list.push({ slot, pawn, pv, o, px, py, into });
    }

    for (const g of groups.values()) {
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

        let ps = propState.get(name);
        if (!ps) {
            ps = { lastSet: 0, lastTime: -1, blockedUntil: 0 };
            propState.set(name, ps);
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

    Instance.SetNextThink(Instance.GetGameTime());
}
