// player_velocity.js: set, add to or scale a player's velocity from map I/O, the way CS:GO's VScript did with
// self.SetVelocity(Vector(x, y, z)). For launch pads, wind, conveyors, bounce plates, brakes, boosters.
//
// SETUP
// 1. Save as  content/csgo_addons/<addon>/scripts/player_velocity.js  and add a point_script whose cs_script is
//    scripts/player_velocity.vjs  (any targetname, e.g. velocity_script).
// 2. Name the entity that does the work "vel_..." (see below). There are two ways to fire it:
//    A) AUTOMATIC (default): name a trigger_multiple / trigger_once "vel_...". Players who touch it get the velocity,
//       with no output wired at all.
//    B) WIRED: set AUTO_HOOK = false. Then from any entity's output run  RunScriptInput  Velocity  on the point_script;
//       the entity that fires the output is the "caller" and its NAME is the recipe (a button named vel_set_0_0_600 ...).
//       The activator (the player) is whoever pressed it / touched it.
//
// NAME = vel_<mode>_<x>_<y>_<z> followed by optional flags, all separated by "_". Numbers: "n" = minus, "p" = decimal
// point (n2p5 = -2.5), "k" = keep (leave that axis alone).
//    set   the player's velocity becomes (x, y, z)         vel_set_0_0_700        launch pad, straight up
//    add   (x, y, z) is added to it                        vel_add_300_k_200      boost (k counts as 0 here)
//    mul   it is multiplied by (x, y, z)                   vel_mul_0p5_0p5_k      halve the horizontal speed
//    set with k keeps that axis:                           vel_set_k_k_600        only change the vertical speed
// flags (any order, after the numbers):
//    view   x is forward and y is to the left of where the player LOOKS        vel_set_500_0_200_view
//    ent    x is forward and y is left of the ENTITY'S own yaw (point it in Hammer)  vel_set_500_0_200_ent
//    cd0p5  the same player cannot trigger it again for 0.5 s (default 0.05)
//    air / gnd   only works while airborne / only while on the ground
//    m2 / m3     only Ts (2) / only CTs (3)
//    hold   keeps applying every tick while the player stays inside (needs the trigger's touch end, which the automatic
//           mode hooks itself). For "add" the numbers are per SECOND, so vel_add_200_0_0_hold is a 200 u/s^2 wind;
//           for "set" it is a constant velocity; for "mul" a per-second factor (0p5 = halve every second).
//
// INPUTS (RunScriptInput):  Velocity   apply the caller's recipe to the activator once
//                           VelocityOn / VelocityOff   start / stop a "hold" recipe for the activator
//                           StopPlayer   set the activator's velocity to 0       StopHorizontal   zero x and y only
//
// A set with a vertical speed on the ground can be ignored by the player's ground movement; add a little height
// (vel_set_0_0_400 rather than 0_0_1) or use "add". If pads look rubber-banded, set MOVE_STYLE to "teleport".
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.

import { Instance } from "cs_script/point_script";

// ---- Settings ----
const PREFIX = "vel_";
const AUTO_HOOK = true;
const TRIGGER_CLASSES = ["trigger_multiple", "trigger_once"];
// "move" keeps the client's interpolation (the usual choice), "teleport" is the fallback.
const MOVE_STYLE = /** @type {string} */ ("move");
const DEFAULT_COOLDOWN = 0.05;
const DEBUG = false;

// ---- Helpers ----
const NUM = "(?:n?\\d+(?:p\\d+)?|k)";

function num(s) {
    if (s === "k") return null;
    return Number(s.replace(/^n/, "-").replace("p", "."));
}

function toPawn(ent) {
    if (!ent || !ent.IsValid()) return undefined;
    if (typeof ent.GetPlayerPawn === "function") return ent.GetPlayerPawn();
    if (typeof ent.GetPlayerController === "function") return ent;
    return undefined;
}

function slotOf(pawn) {
    const c = pawn && pawn.IsValid() ? pawn.GetPlayerController() : undefined;
    return c ? c.GetPlayerSlot() : undefined;
}

const parsed = new Map(); // entity name -> recipe or null
function recipe(name) {
    if (parsed.has(name)) return parsed.get(name);
    let r = null;
    if (name.startsWith(PREFIX)) {
        const m = name.slice(PREFIX.length).match(new RegExp("^(set|add|mul)_(" + NUM + ")_(" + NUM + ")_(" + NUM + ")((?:_.*)?)$"));
        if (m) {
            r = { mode: m[1], v: [num(m[2]), num(m[3]), num(m[4])], frame: "world", cd: DEFAULT_COOLDOWN, air: false, gnd: false, team: 0, hold: false };
            for (const tok of m[5].split("_")) {
                let t;
                if (tok === "view" || tok === "ent") r.frame = tok;
                else if (tok === "air") r.air = true;
                else if (tok === "gnd") r.gnd = true;
                else if (tok === "hold") r.hold = true;
                else if ((t = tok.match(/^cd(n?\d+(?:p\d+)?)$/))) r.cd = num(t[1]);
                else if ((t = tok.match(/^m([23])$/))) r.team = Number(t[1]);
            }
        }
    }
    parsed.set(name, r);
    return r;
}

function onGround(pawn) {
    return pawn.GetGroundEntity() !== undefined;
}

// New velocity for one application. dt = 1 for a one-shot, the tick length for a "hold".
function compute(r, pawn, caller, dt, hold) {
    const cur = pawn.GetAbsVelocity();
    let yaw = 0;
    if (r.frame === "view") yaw = pawn.GetEyeAngles().yaw;
    else if (r.frame === "ent" && caller && caller.IsValid()) yaw = caller.GetAbsAngles().yaw;
    const rad = (yaw * Math.PI) / 180;
    const fwd = { x: Math.cos(rad), y: Math.sin(rad) };
    const left = { x: -Math.sin(rad), y: Math.cos(rad) };
    const [vx, vy, vz] = r.v;
    if (r.mode === "mul") {
        const f = (v) => (v === null ? 1 : hold ? Math.pow(v, dt) : v);
        return { x: cur.x * f(vx), y: cur.y * f(vy), z: cur.z * f(vz) };
    }
    // world-space vector from the recipe; "k" means untouched
    let wx = null;
    let wy = null;
    if (r.frame === "world") {
        wx = vx;
        wy = vy;
    } else {
        const a = vx === null ? 0 : vx;
        const b = vy === null ? 0 : vy;
        wx = fwd.x * a + left.x * b;
        wy = fwd.y * a + left.y * b;
    }
    const k = r.mode === "add" || hold ? dt : 1;
    if (r.mode === "set") {
        return { x: wx === null ? cur.x : wx, y: wy === null ? cur.y : wy, z: vz === null ? cur.z : vz };
    }
    return { x: cur.x + (wx === null ? 0 : wx) * k, y: cur.y + (wy === null ? 0 : wy) * k, z: cur.z + (vz === null ? 0 : vz) * k };
}

function push(pawn, v) {
    if (![v.x, v.y, v.z].every(Number.isFinite)) return;
    if (MOVE_STYLE === "teleport") pawn.Teleport({ velocity: v });
    else pawn.Move({ velocity: v });
}

// ---- Applying ----
const lastUse = new Map(); // "slot|entity name" -> game time
const holds = new Map(); // slot -> Map(entity name -> { r, caller, pawn })

function allowed(r, pawn, now, key) {
    if (!pawn || !pawn.IsValid() || !pawn.IsAlive()) return false;
    if (r.team && pawn.GetTeamNumber() !== r.team) return false;
    if (r.air && onGround(pawn)) return false;
    if (r.gnd && !onGround(pawn)) return false;
    if (now - (lastUse.get(key) ?? -1e9) < r.cd) return false;
    return true;
}

function fire(caller, pawn) {
    const name = caller && caller.IsValid() ? caller.GetEntityName() : "";
    const r = recipe(name);
    if (!r || !pawn) return;
    const slot = slotOf(pawn);
    const now = Instance.GetGameTime();
    const key = slot + "|" + name;
    if (r.hold) {
        startHold(caller, pawn);
        return;
    }
    if (!allowed(r, pawn, now, key)) return;
    lastUse.set(key, now);
    const v = compute(r, pawn, caller, 1, false);
    if (DEBUG) Instance.Msg(`${name}: slot ${slot} ${JSON.stringify(pawn.GetAbsVelocity())} -> ${JSON.stringify(v)}`);
    push(pawn, v);
}

function startHold(caller, pawn) {
    const slot = slotOf(pawn);
    if (slot === undefined) return;
    if (!holds.has(slot)) holds.set(slot, new Map());
    holds.get(slot).set(caller.GetEntityName(), { caller, pawn });
}

function endHold(caller, pawn) {
    const slot = slotOf(pawn);
    if (slot === undefined || !holds.has(slot)) return;
    holds.get(slot).delete(caller.GetEntityName());
    if (holds.get(slot).size === 0) holds.delete(slot);
}

let lastThink = 0;
function think() {
    const now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastThink, 0.001), 0.1);
    lastThink = now;
    for (const [slot, byName] of [...holds]) {
        for (const [name, h] of [...byName]) {
            const r = recipe(name);
            if (!r || !h.pawn.IsValid() || !h.pawn.IsAlive() || !h.caller.IsValid()) {
                byName.delete(name);
                continue;
            }
            if (r.team && h.pawn.GetTeamNumber() !== r.team) continue;
            if (r.air && onGround(h.pawn)) continue;
            if (r.gnd && !onGround(h.pawn)) continue;
            push(h.pawn, compute(r, h.pawn, h.caller, dt, true));
        }
        if (byName.size === 0) holds.delete(slot);
    }
    Instance.SetNextThink(now); // cheap: an empty loop does nothing
}

// ---- Wiring ----
let conns = [];
function hookAll() {
    for (const id of conns) Instance.DisconnectOutput(id);
    conns = [];
    holds.clear();
    if (!AUTO_HOOK) return;
    for (const cls of TRIGGER_CLASSES) {
        for (const ent of Instance.FindEntitiesByClass(cls)) {
            const r = ent.IsValid() ? recipe(ent.GetEntityName()) : null;
            if (!r) continue;
            const a = Instance.ConnectOutput(ent, "OnStartTouch", (d) => fire(ent, toPawn(d.activator) || toPawn(d.caller)));
            if (a !== undefined) conns.push(a);
            if (r.hold) {
                const b = Instance.ConnectOutput(ent, "OnEndTouch", (d) => endHold(ent, toPawn(d.activator) || toPawn(d.caller)));
                if (b !== undefined) conns.push(b);
            }
        }
    }
}

Instance.OnScriptInput("Velocity", (d) => fire(d.caller, toPawn(d.activator)));
Instance.OnScriptInput("VelocityOn", (d) => {
    const pawn = toPawn(d.activator);
    if (d.caller && pawn) startHold(d.caller, pawn);
});
Instance.OnScriptInput("VelocityOff", (d) => {
    const pawn = toPawn(d.activator);
    if (d.caller && pawn) endHold(d.caller, pawn);
});
Instance.OnScriptInput("StopPlayer", (d) => {
    const pawn = toPawn(d.activator);
    if (pawn && pawn.IsValid()) push(pawn, { x: 0, y: 0, z: 0 });
});
Instance.OnScriptInput("StopHorizontal", (d) => {
    const pawn = toPawn(d.activator);
    if (pawn && pawn.IsValid()) push(pawn, { x: 0, y: 0, z: pawn.GetAbsVelocity().z });
});

Instance.OnActivate(() => {
    lastThink = Instance.GetGameTime();
    hookAll();
    Instance.SetThink(think);
    Instance.SetNextThink(lastThink);
});
Instance.OnRoundStart(hookAll); // map entities are new objects every round
Instance.OnPlayerDisconnect(({ playerSlot }) => holds.delete(playerSlot));
Instance.OnPlayerReset(({ player }) => {
    const slot = slotOf(player);
    if (slot !== undefined) holds.delete(slot);
});
