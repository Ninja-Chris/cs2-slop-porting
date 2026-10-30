// push_mass.js (v7): make player pushes on physics props scale with prop mass.
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

function tick() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastTime, 0.001), 0.1);
    lastTime = now;

    if (now >= nextScan) {
        nextScan = now + SCAN_INTERVAL;
        renameNewProps();
    }

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
        const mass = massFromName(name);
        if ((cls !== "prop_physics" && cls !== "prop_physics_override") || mass === undefined) {
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
