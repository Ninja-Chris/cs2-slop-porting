// tpush_test.js: a small, standalone test of CS:GO trigger_push behaviour in CS2. No converter, no map data.
// Map setup and test plan: see the end of this file.
//
// What CS:GO did (Source SDK triggers.cpp CTriggerPush::Touch, player_command.cpp CheckMovingGround, gamemovement.cpp):
//   - each tick a player touches a push: base velocity = sum of speed x direction of every push touched that tick
//   - next move: the vertical part is added to the velocity as an acceleration (vz += bz x dt); the horizontal part is added to
//     the velocity for the move only, so friction, acceleration and air strafing only ever act on the player's own velocity
//   - the first tick with no touch: velocity += (1 + dt / 2) x horizontal base velocity (momentum), base velocity cleared
//   - an upward push on a grounded player clears the ground and lifts them 1 unit. The next move puts them back on the ground
//     (within 2 units, not rising fast), and a moving player is snapped back down. A player standing still creeps up a unit a tick:
//     after 3 touches they are 3 units up, off the ground, and the push takes them up from there (and again each time they land)
// Scripts can't set base velocity in CS2, so this script copies it:
//   in the air:    the push rides in the velocity and is taken out again next tick (smooth on the client)
//   on the ground: the player is moved by it (velocity untouched), so friction and ground acceleration only see their own speed
// Two CS2 differences, both seen in the in-game log (2026-11-08):
//   - CS2 snaps a grounded player back onto the floor every tick, so the 1-unit lifts never add up: the script puts a player who
//     has stood still in an up push for 3 touches 3 units up itself (tp_lift)
//   - the touch outputs reach the script one tick late, which pumped the bobbing at the top of an up push until the player fell to
//     the floor. The script makes up the push the move just made should have had (tp_lag)
//
// Everything else the push touches (Source SDK CTriggerPush::Touch, by move type):
//   - physics objects (props, physbox, dropped weapons): ApplyForceCenter(speed x dir x 100 x dt) every tick, i.e. velocity +=
//     speed x dir x dt x 100 / mass (the push assumes 100 kg). Flag 4096 ("account for object mass", "_mass" below): velocity +=
//     speed x dir x dt whatever the mass (that code isn't in the public SDK; this is what the flag's description says)
//   - anything else that moves by itself (grenades in flight): base velocity like players, but it moves them during the move on
//     all three axes instead of accelerating them; leaving: velocity += (1 + dt/2) x horizontal base velocity
//   - Once Only (flag 128, "_once"): velocity += speed x dir for whatever touches first (players and props alike), trigger removed
//   - parented entities, and anything not moving by itself (NONE) or in noclip, are never pushed
//   cs_script can't read a prop's mass: it comes from the prop's name (<name>__pm<kg>, e.g. crate__pm20, or push_<kg>_<name>),
//   else tp_mass (default 100)
//
// Push zones: any trigger_multiple named  tpush_<speed>_<pitch>_<yaw>[_<option>...]
//   e.g. tpush_300_0_0 (300 along +X), tpush_700_-90_0 (700 straight up). "m" works as a minus sign too: tpush_700_m90_0.
//   Speed 0 means 100, as in CS:GO. The script can't read keyvalues or flags, so the name carries what it needs:
//     _once  Once Only (flag 128)     _mass  flag 4096     _fall<vz>  OnlyFallingPlayers with FallingSpeedThreshold <vz>
//   (_fall: players only, pushed while falling with vz <= <vz>, not on the ground, not holding jump.) Anything else after the
//   numbers is a label (tpush_300_0_0_props). Tick the trigger's Clients / Physics Objects / Everything flags as on the CS:GO
//   trigger_push; the trigger's own flags / filter decide who touches it. The zone's direction does not turn with the trigger
//   (keep angles 0). Only trigger_multiple: a trigger_push would push on its own as well (CS2 turns speed 0 into 100, tested in
//   game). A point_template that spawns zones: OnEntitySpawned -> <this point_script> RunScriptInput tpush_rescan, so the script
//   hooks them at once.
//
// Console commands (need sv_cheats 1):
//   tp_mode hybrid|position|velocity   horizontal push: hybrid = in the velocity in the air, moves the player on the ground (default)
//                                      position = always moves the player; velocity = always in the velocity
//   tp_lift <units> how far up a player standing still in an up push is put after 3 touches (default 3, as CS:GO; 0 = off).
//                   CS2 pulls a lifted player back down, so the lift also kicks them up for a tick (see KICKS): "lift works" in the
//                   console says which kick it took
//   tp_lag 0|1      make up for touch outputs arriving a tick late (default 1)
//   tp_on 0|1       the script's pushing on / off (touch lines still print)
//   tp_log 0|1|2    1 = a console line per tick for each pushed player (default); 2 = also slot 0 every tick, pushed or not
//   tp_set <zone name> <speed> [<pitch> <yaw>]   change a zone without rebuilding the map
//   tp_mass <kg>    mass for props whose name has none (default 100)
//
// Uses Instance.ConnectOutput / FindEntitiesByClass / Msg / RegisterCheatCommand: marked deprecated in point_script.d.ts but they
// are what the in-game-tested scripts use. Swap for Entities.* / Console.* once those are confirmed in your build.

import { Instance, CSPlayerPawn, CSPlayerController, CSInputs, CSMoveType } from "cs_script/point_script";
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


let MODE = "hybrid";
let LIFT = 3;
let LAG = 1;
// CS2 keeps a player it thinks is grounded on the ground, so a lift alone gets pulled back down (in-game log, 2026-11-08). The lift
// also gives a one-tick upward kick, taken back as soon as they are airborne. It starts at none (CS:GO) and steps up each time a
// lift is pulled back; the console says which one worked.
const KICKS = [0, 140, 250, 300];
let kickLevel = 0, liftWorked = false;
let ON = true;
let LOG = 1;
let MASS = 100;
const STEP = 18; // sv_stepsize

const V = (x, y, z) => ({ x, y, z });
const add = (a, b) => V(a.x + b.x, a.y + b.y, a.z + b.z);
const sub = (a, b) => V(a.x - b.x, a.y - b.y, a.z - b.z);
const mul = (a, k) => V(a.x * k, a.y * k, a.z * k);
const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
const len = (a) => Math.hypot(a.x, a.y, a.z);
const f1 = (x) => x.toFixed(1);
const say = (s) => Instance.Msg("tpush: " + s);
let tick = 0;

// ---- zones: trigger -> { ent, name, speed, dir, once, realMass, fall, used, inside: Set<player slot>, things: Set<entity>,
//      fresh / tFresh: started since the last tick, brief / tBrief: started and ended since the last tick (still pushed once) }
let zones = new Map();
let conns = [];

function dirOf(pitch, yaw) {
    const d = Math.PI / 180, r = (x) => (Math.abs(x) < 1e-9 ? 0 : x); // cos(90) is 6e-17, not 0
    return V(r(Math.cos(pitch * d) * Math.cos(yaw * d)), r(Math.cos(pitch * d) * Math.sin(yaw * d)), r(-Math.sin(pitch * d)));
}
function parseZone(name) {
    const m = /^tpush_(m?-?[\d.]+)_(m?-?[\d.]+)_(m?-?[\d.]+)/i.exec(name);
    if (!m) return undefined;
    const [s, p, y] = m.slice(1, 4).map((x) => Number(x.replace(/^m/i, "-")));
    if (![s, p, y].every(Number.isFinite)) return undefined;
    const opt = name.slice(m[0].length).toLowerCase().split("_");
    const f = opt.map((o) => /^fall(m?-?[\d.]+)$/.exec(o)).find((x) => x);
    const once = opt.includes("once"), fall = f ? Number(f[1].replace(/^m/, "-")) : undefined;
    return { speed: s === 0 ? 100 : s, dir: dirOf(p, y), once, realMass: opt.includes("mass"), fall };
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
function hook(ent) {
    const z = parseZone(ent.GetEntityName());
    if (!z) return undefined;
    const Z = { ent, name: ent.GetEntityName(), ...z, used: false,
                inside: new Set(), fresh: new Set(), brief: new Set(), things: new Set(), tFresh: new Set(), tBrief: new Set() };
    zones.set(ent, Z);
    const a = Instance.ConnectOutput(ent, "OnStartTouch", ({ activator }) => touch(Z, activator, true));
    const b = Instance.ConnectOutput(ent, "OnEndTouch", ({ activator }) => touch(Z, activator, false));
    for (const id of [a, b]) if (id !== undefined) conns.push(id);
    return Z;
}
// hook the zones not hooked yet (all of them after hookAll; the new ones after a point_template spawned some)
function scan() {
    for (const ent of [...zones.keys()]) if (!ent.IsValid()) zones.delete(ent);
    const added = [];
    for (const ent of Instance.FindEntitiesByClass("trigger_multiple")) {
        if (!ent.IsValid() || zones.has(ent) || findSame(zones.keys(), ent)) continue;
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
    scan();
    say(`${zones.size} zone(s): ` + [...zones.values()].map(describe).join(", "));
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
const onceUsed = new Set(); // by trigger entity, so a second hook on the same trigger can't fire it again
function once(Z, who) {
    if (Z.used || onceUsed.has(Z.ent)) return;
    Z.used = true;
    onceUsed.add(Z.ent);
    const pawn = toPawn(who);
    const ent = pawn || who;
    if (ent && ent.IsValid() && !ent.GetParent()) {
        const imp = mul(Z.dir, Z.speed);
        const nv = add(ent.GetAbsVelocity(), imp);
        if (pawn) pawn.Move({ velocity: nv });
        else ent.Teleport({ velocity: nv });
        say(`t=${tick} once-only ${Z.name}: ${ent.GetClassName()} given ${f1(imp.x)} ${f1(imp.y)} ${f1(imp.z)}, trigger removed`);
    }
    zones.delete(Z.ent);
    if (Z.ent.IsValid()) Z.ent.Remove();
}

// ---- players: slot -> { carry: push put in the velocity last tick, bv: horizontal base velocity of the last touched tick,
//                         applied: push applied for the move just made, creep: ticks stood still in an up push,
//                         kicked: the kick given by a lift last tick (undefined if no lift) }
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

// ---- everything that isn't a player. thing -> { carry: push put in its velocity last tick (non-physics), bv: horizontal base
//      velocity of the last touched tick (non-physics), applied: velocity change given last tick (physics) }
const things = new Map();
function massOf(ent) {
    const n = ent.GetEntityName();
    let m = /__pm(\d+(?:p\d+)?)/.exec(n);
    if (m) return Number(m[1].replace("p", "."));
    m = /^push_(\d+(?:\.\d+)?)_/.exec(n);
    if (m) return Number(m[1]);
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
            for (const t0 of list) {
                if (!t0.IsValid()) {
                    Z.things.delete(t0);
                    continue;
                }
                const t = keyOf(t0);
                let e = now.get(t);
                if (!e) now.set(t, (e = { B: V(0, 0, 0), dv: V(0, 0, 0) }));
                const B = mul(Z.dir, Z.speed);
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
            // a force: this tick's velocity change, plus (touches arriving a tick late) the one of the tick they missed, or minus
            // the one given after it had already left
            nv = add(v, LAG ? add(e.dv, sub(e.dv, S.applied)) : e.dv);
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
    // the zones each player is in
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
        const inZ = bySlot.get(slot) || [];
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
        // the pushes touched this tick. CS:GO: none in noclip; an OnlyFallingPlayers zone only while falling (vz <= its threshold),
        // in the air and not holding jump
        let B = V(0, 0, 0), n = 0;
        if (!pawn.IsNoclipping()) {
            for (const Z of inZ) {
                if (Z.fall !== undefined && (ground || v.z > Z.fall || pawn.IsInputPressed(CSInputs.JUMP))) continue;
                B = add(B, mul(Z.dir, Z.speed));
                n++;
            }
        }
        if (!S) {
            S = { carry: { x: 0, y: 0 }, bv: null, applied: V(0, 0, 0), creep: 0, kicked: undefined };
            if (n) st.set(slot, S);
        }
        // the player's own velocity: what the engine has now, minus the push this script put into it last tick
        const own = V(v.x - S.carry.x, v.y - S.carry.y, v.z);
        let note = "";
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
        const d = V(fix.x + (inVel ? 0 : push.x * dt), fix.y + (inVel ? 0 : push.y * dt), fix.z);
        if (Math.abs(d.x) + Math.abs(d.y) + Math.abs(d.z) > 1e-6) {
            pos = inVel ? slide(pawn, pos || o, d) : walk(pawn, pos || o, d, ground && !lifted);
            if (!inVel && (push.x || push.y)) note += " moved";
        }
        const nv = V(own.x + S.carry.x, own.y + S.carry.y, own.z);
        if (pos) pawn.Move({ position: pos, velocity: nv });
        else if (Math.abs(nv.x - v.x) + Math.abs(nv.y - v.y) + Math.abs(nv.z - v.z) > 1e-3) pawn.Move({ velocity: nv });
        if (LOG && (n || note || watch)) {
            say(`t=${tick} s${slot} ${ground ? "G" : "A"} z=${f1(o.z)} v=${f1(v.x)} ${f1(v.y)} ${f1(v.z)} |v|=${f1(Math.hypot(v.x, v.y))} ` +
                `own=${f1(own.x)} ${f1(own.y)} B=${f1(B.x)} ${f1(B.y)} ${f1(B.z)} ${n ? (inVel ? "vel" : "pos") : "-"}${note}`);
        }
        if (!n && !S.bv && !S.carry.x && !S.carry.y) st.delete(slot); // (S.applied is 0 here)
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
function think() {
    Instance.SetNextThink(Instance.GetGameTime()); // first, so an error can't stop the loop
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
Instance.RegisterCheatCommand("tp_on", (a) => {
    if (arg(a)) ON = arg(a) !== "0";
    say("script pushing " + (ON ? "on" : "off"));
});
Instance.RegisterCheatCommand("tp_log", (a) => {
    if (arg(a)) LOG = Number(arg(a)) || 0;
    say("log " + LOG);
});
Instance.RegisterCheatCommand("tp_mass", (a) => {
    if (Number(arg(a)) > 0) MASS = Number(arg(a));
    say("mass for props with none in their name: " + MASS);
});
Instance.RegisterCheatCommand("tp_set", (a) => {
    const w = arg(a).split(/\s+/);
    const Zs = [...zones.values()].filter((Z) => Z.name.toLowerCase() === (w[0] || "").toLowerCase());
    if (!Zs.length || !Number.isFinite(Number(w[1]))) return say("tp_set <zone name> <speed> [<pitch> <yaw>]");
    for (const Z of Zs) {
        Z.speed = Number(w[1]);
        if (w.length >= 4 && Number.isFinite(Number(w[2])) && Number.isFinite(Number(w[3]))) Z.dir = dirOf(Number(w[2]), Number(w[3]));
    }
    say(`${w[0]}: speed ${Zs[0].speed}, dir ${f1(Zs[0].dir.x)} ${f1(Zs[0].dir.y)} ${f1(Zs[0].dir.z)}`);
});
// (Re)start: hook the zones and run the tick loop. In tools mode, saving the .js reloads the script and clears every callback,
// including the think, and OnActivate doesn't fire again: so this also runs after a reload and at every round start.
function start(why) {
    last = Instance.GetGameTime();
    st.clear();
    things.clear();
    hookAll();
    setThinkOnce(think);
    Instance.SetNextThink(last);
    say(`${why}: mode ${MODE}, lift ${LIFT} (kick ${KICKS[kickLevel]}), lag ${LAG}, runs after thinks: ${typeof Instance.QueueAfterThinks === "function"}`);
}
Instance.OnActivate(() => start("on"));
Instance.OnRoundStart(() => start("round start")); // map entities are new every round
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => start("reloaded") });
Instance.OnScriptInput("tpush_rescan", () => {
    const added = scan();
    say(`t=${tick} rescan: ${added.length} new zone(s)` + (added.length ? ": " + added.map(describe).join(", ") : ""));
});
Instance.OnPlayerDisconnect(({ playerSlot }) => {
    st.delete(playerSlot);
    for (const Z of zones.values()) {
        Z.inside.delete(playerSlot);
        Z.brief.delete(playerSlot);
    }
});
// Respawned (or put back at spawn): no push is in its velocity any more and there is no momentum to hand over. Its old zones go
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

/* ---------------------------------------------------------------- MAP SETUP
 Put this file in <addon>/scripts/tpush_test.js. Add a point_script named tpush, cs_script = scripts/tpush_test.vjs.
 A flat floor with room to run, one wall, and three 8-unit steps. Then these brushes, about 256 x 256 x 128, standing on the floor
 (trigger_multiple, Clients flag on):
   tpush_300_0_0        horizontal, 300 along +X, with open floor past its +X side
   tpush_300_0_0_wall   the same with the wall just past its +X side
   tpush_300_0_90_steps the same across the steps (pushing up them)
   tpush_700_-90_0      up 700
   tpush_850_-90_0      up 850
   tpush_1500_-90_0     up 1500
   tpush_700_-90_0_k + tpush_150_0_0_k   the same box twice: up 700 over forward 150 (the ba_jail_keimusho slide)
 For things other than players (trigger_multiple, flags as noted):
   tpush_300_0_0_props    Clients + Physics Objects; a few prop_physics standing in it (a crate, a barrel), one of them named
                          crate__pm20 (20 kg: pushed 5 x harder than the others)
   tpush_1000_-90_0_props Clients + Physics Objects, up 1000; a prop_physics (or two) in it
   tpush_300_0_0_props_mass  as tpush_300_0_0_props with the _mass option (flag 4096): every prop pushed the same
   tpush_400_-45_0_once   Clients + Physics Objects: Once Only (up and along +X)
   tpush_500_-90_0_all    Everything flag: up 500, for thrown grenades
   tpush_900_-90_0_fallm150  Clients: OnlyFallingPlayers, threshold -150; something to jump down into it from (a ledge 200 above)
   tmpl_push: a point_template, Template01 = tpush_400_-90_0_tmpl (a trigger_multiple somewhere free, Clients flag, up 400),
              output OnEntitySpawned -> tpush RunScriptInput tpush_rescan
 And CS2 trigger_pushes, not named tpush_ (the script leaves them alone), Clients flag on:
   native_speed0   speed 0,   pushdir 0 0 0
   native_300      speed 300, pushdir 0 0 0
   native_props    speed 300, pushdir 0 0 0, Physics Objects flag, a prop_physics in it (CS2's own push on props, for comparison)

 ---------------------------------------------------------------- TEST PLAN  (sv_cheats 1, then con_logfile tpush.log to keep the lines)
 At start the console lists the zones (tpush_400_-90_0_tmpl is not there until the template spawns it).
 T1 native_speed0: tp_log 2, stand in it 2 s. Does v move at all? (Done: it pushes at 100.)
 T3 native_300: stand, then walk with and against it. How CS2's own push behaves, for comparison.
 T4 tpush_300_0_0, in each tp_mode: stand still (CS:GO: you travel 300 u/s while |v| stays near 0), hold W along it (CS:GO:
    550 u/s), hold S against it (CS:GO: 50 u/s with the push), walk out of its +X side ("handover": |v| jumps to about 300, then
    friction). Note any jitter: position and hybrid move you on the ground, and the client corrects for it.
 T5 jump through tpush_300_0_0 from the side, and jump while standing in it.
 T6 Disable / Enable with you inside. tp_on 0 first (nothing pushes, the start / end lines still print). Stand against the wall
    in tpush_300_0_0_wall: ent_fire tpush_300_0_0_wall Disable -> an "end ... tpush_300_0_0_wall" line? Then ent_fire
    tpush_300_0_0_wall Enable -> a "start" line, while you stand still? Then tp_on 1.
    No "end" on Disable: CS2 doesn't fire OnEndTouch then and the script would keep pushing. No "start" on Enable: a push enabled
    with you already in it would not push you until you walk out and in. Either one means the converter must tell the script.
 T7 tpush_700_-90_0, tpush_850_-90_0 and tpush_1500_-90_0: stand still in each, walk through, jump in.
    Standing still, no noclip, no jump: "creep" then "lift (kick N)"; "pulled back" means CS2 put you down again and the next lift
    kicks harder, until the console says "lift works". Then A and rising (CS:GO, by the SDK: 700 hovers a few units up; 850 holds
    you; 1500 bobs up past the top and back down to a few units above the floor, and lifts again if you land).
    Walking through: stays G (CS:GO kept walking players on the ground). The start/end lines should be followed by a "late" line.
 T8 the _k pair: stand, hold W, jump. (CS:GO, reading the SDK: walking keeps you on the ground; standing still may creep up;
    jumping in floats you with 100 u/s^2 net down.)
 T9 tpush_300_0_90_steps: does it carry you up the steps?
 T10 tpush_300_0_0_props: do the props slide out along +X (CS:GO: a 100 kg prop gets 300 u/s of push a second, so friction holds
     heavy ones back), crate__pm20 faster? The start lines show the mass used. When a prop leaves, an "end" line, and it stops being
     pushed (if it keeps speeding up outside the zone, tell me). Compare native_props. Drop a weapon (G) into it.
 T11 tpush_1000_-90_0_props: do the props rise? tpush_300_0_0_props_mass: do light and heavy props now move the same?
 T12 tpush_400_-45_0_once: walk in: "once-only ... given ..., trigger removed", you are thrown once. Rebuild / restart the round and
     push a prop into it instead.
 T13 tpush_500_-90_0_all: throw a grenade through it: does it rise through the zone (CS:GO: moved up 500 u/s while inside)?
 T14 tpush_900_-90_0_fallm150: walk in (no push), jump in holding space (no push), drop in from the ledge (pushed once falling
     faster than 150).
 T15 ent_fire tmpl_push ForceSpawn: "rescan: 1 new zone(s): tpush_400_-90_0_tmpl...". Walk into it: pushed up (log lines).
     Then restart the round and ForceSpawn again (the rescan should still work).
 T16 with a friend connected or on a server with some ping, cl_showerror 1 in your console: stand and walk in tpush_300_0_0 and
     tpush_850_-90_0. How many prediction-error lines, and can you see the corrections? (How much the script's ground pushes jitter.)
 Send the log (or the lines around each test) and what it felt like.
*/
