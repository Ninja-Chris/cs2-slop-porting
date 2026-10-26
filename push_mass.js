// push_mass.js (v5): make player pushes on physics props scale with prop mass.
//
// Runs inside the map via a point_script entity, so no server plugin is needed.
// With every experimental switch below OFF, the push behaves like v3, which was
// confirmed working in-game. The switches (SMOOTH_PUSH, DETECT_BLOCKED,
// CONTACT_GRACE, SLOW_PLAYER) are UNTESTED: turn them on one at a time.
// The damage filter is separate and does not change how props are pushed.
//
// How it works
// - Props stay normal physics props, so collisions with walls, players and other
//   props, and forces from other props, are still handled by the engine.
// - Each tick, for every player, a player-sized box is traced toward the prop
//   they are approaching. On contact the script remembers the player's approach
//   velocity, because the engine zeroes it while the prop blocks them.
// - The push goes along the contact surface normal (straight into the face),
//   at a mass-based fraction of how fast the player was moving INTO the prop.
//   Sliding sideways along a prop adds no push.
// - The prop's speed is ramped toward its target instead of snapped, so engine
//   kicks and script pushes don't fight each other (less bouncing).
// - If a prop stops moving when pushed (wall, another prop), the script stops
//   injecting speed into it, which avoids bounce-back and jitter.
// - Damage to players from pushed props is cancelled when the prop is moving
//   slowly, but real fast impacts still hurt.
//
// SETUP
// 1. Save in: <Steam>/steamapps/common/Counter-Strike Global Offensive/
//    content/csgo_addons/<your addon>/scripts/push_mass.js
// 2. In Hammer, add a point_script entity and pick this file in its cs_script field.
// 3. Name each movable prop push_<mass>_<unique name>, e.g. "push_5_ball1",
//    "push_200_crate1". Other props are left alone.
// 4. Rotation assumes the prop's origin is near its horizontal centre.

import { Instance } from "cs_script/point_script";

// ---- Push strength ----
// Prop mass at which the prop moves at half the player's push speed.
const REF_MASS = 20;
// Smallest speed (units/s) a pushed prop is given, so heavy props still creep.
const MIN_PUSH_SPEED = 10;
// Speed (units/s) into a prop needed to start a push.
const MIN_INTO_SPEED = 20;
// Props already moving faster than this are assumed hit by an explosion or
// bullet, and are left to the engine.
const MAX_EXTERNAL_SPEED = 400;

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
const CONTACT_GRACE = 0;

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

// ---- Debug ----
// Print push events to the console for tuning.
const DEBUG = true;
// Print every player damage event (inflictor, types, amount) for tuning.
const DEBUG_DAMAGE = true;

// Player collision boxes (standing 72 tall, crouched 54), lifted slightly off
// the floor so the trace doesn't start touching the ground.
const HULL_STAND = { mins: { x: -16, y: -16, z: 4 }, maxs: { x: 16, y: 16, z: 72 } };
const HULL_DUCK = { mins: { x: -16, y: -16, z: 4 }, maxs: { x: 16, y: 16, z: 54 } };

// Per-player push memory: slot -> { vx, vy, px, py, t }
//   vx,vy = remembered approach velocity, px,py = push direction (into the prop),
//   t = last time contact was confirmed
const state = new Map();
// Per-prop push memory: name -> { lastSet, lastTime, blockedUntil }
const propState = new Map();
let lastTime = 0;

// Template-spawned props share one name, so each is given a unique suffix.
// How often (seconds) to look for newly spawned props to rename.
const SCAN_INTERVAL = 0.5;
let nextScan = 0;
let uid = 0;
const UNIQUE_RE = /_u\d+$/;

function massFromName(name) {
    const m = /^push_(\d+(?:\.\d+)?)_/.exec(name);
    return m ? Number(m[1]) : undefined;
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
    return !(c === "prop_physics" || c === "prop_physics_override" || c === "prop_physics_multiplayer");
}

// Give every template-spawned prop a unique name, e.g. push_5_z10props becomes
// push_5_z10props_u0, push_5_z10props_u1, ... The push_<mass> prefix is kept, so
// mass still parses. Already-renamed props are skipped, and state for props that
// no longer exist is dropped.
function renameNewProps() {
    const live = new Set();
    for (const cls of ["prop_physics", "prop_physics_override"]) {
        for (const ent of Instance.FindEntitiesByClass(cls)) {
            let name = ent.GetEntityName();
            if (massFromName(name) === undefined) continue;
            if (!UNIQUE_RE.test(name)) {
                name = `${name}_u${uid++}`;
                ent.SetEntityName(name);
            }
            live.add(name);
        }
    }
    for (const key of propState.keys()) {
        if (!live.has(key)) propState.delete(key);
    }
}

Instance.OnRoundStart(() => renameNewProps());

Instance.OnActivate(() => {
    Instance.Msg("push_mass: script activated");
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

    if (!valid || massFromName(name) === undefined) return;

    const v = inf.GetAbsVelocity();
    const speed = Math.hypot(v.x, v.y, v.z);
    if (speed < SAFE_PROP_SPEED) {
        if (DEBUG) Instance.Msg(`${name}: damage cancelled (prop speed ${speed.toFixed(0)})`);
        return { abort: true };
    }
});

function tick() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastTime, 0.001), 0.1);
    lastTime = now;

    if (now >= nextScan) {
        nextScan = now + SCAN_INTERVAL;
        renameNewProps();
    }

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
        if (!tr.didHit || tr.startedInSolid || !tr.hitEntity) {
            // Brief loss of contact: keep the remembered push for a moment.
            if (st && now - st.t < CONTACT_GRACE) continue;
            state.delete(slot);
            continue;
        }

        const prop = tr.hitEntity;
        const cls = prop.GetClassName();
        const name = prop.GetEntityName();
        const mass = massFromName(name);
        if ((cls !== "prop_physics" && cls !== "prop_physics_override") || mass === undefined) {
            state.delete(slot);
            continue;
        }

        // Surface normal (horizontal), flipped to face the player; push is the opposite.
        let nx = tr.normal.x;
        let ny = tr.normal.y;
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
        state.set(slot, { vx, vy, px, py, t: now });

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

        // Ramp the prop's speed toward its mass-based target.
        const target = Math.max(MIN_PUSH_SPEED, (into * REF_MASS) / (REF_MASS + mass));
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
            const lever = (o.x - po.x) * py - (o.y - po.y) * px;
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

        // Cut only the part of the player's speed into the prop that exceeds
        // the prop's speed; sideways movement is left alone.
        if (SLOW_PLAYER) {
            const intoCur = pv.x * px + pv.y * py;
            if (intoCur > newAlong) {
                const cut = intoCur - newAlong;
                pawn.Teleport({
                    velocity: { x: pv.x - px * cut, y: pv.y - py * cut, z: pv.z },
                });
            }
        }

        if (DEBUG) {
            Instance.Msg(
                `${name} (mass ${mass}): into ${into.toFixed(0)} target ${target.toFixed(0)} set ${newAlong.toFixed(0)} u/s, spin ${omega.toFixed(2)} rad/s, dt ${(dt * 1000).toFixed(1)}ms`,
            );
        }
    }

    Instance.SetNextThink(Instance.GetGameTime());
}
