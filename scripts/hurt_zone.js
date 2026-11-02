// hurt_zone.js: scripted hurt / heal zones for cs_script (point_script). A replacement for trigger_hurt that can also
// HEAL (negative damage), change its timing while the map runs, and grow / shrink like an env_fire.
//
// SETUP
// 1. vmap_convert_merged.py does this step (copies the file, adds the point_script). By hand: save as  content/csgo_addons/<addon>/scripts/hurt_zone.js  and add a point_script whose cs_script is
//    scripts/hurt_zone.vjs  (any targetname, e.g. hurt_script).
// 2. Make a zone out of any entity whose targetname starts with "hurt_". Two kinds:
//    - TRIGGER zone: a trigger_multiple (a brush). The zone is exactly the brush; the script hooks its OnStartTouch /
//      OnEndTouch itself, so no I/O is needed. Its size cannot change at runtime (cs_script cannot scale a trigger).
//    - VOLUME zone: any point entity (info_target, a prop, a moving entity...) plus a size in the name: r<units> is a
//      sphere around the entity, b<x>x<y>x<z> is a box with those half-sizes. The size CAN change at runtime, the zone
//      follows the entity if it moves, and it is tested every tick.
//
// NAME = hurt_ followed by options separated by "_" (numbers: "n" = minus, "p" = decimal point, 0p5 = 0.5):
//    d10      damage per tick of the zone              dn5  = -5 = HEAL 5        h5  = heal 5 (same thing)
//    i0p5     seconds between ticks (default 0.5)      w1   = seconds in the zone before the first tick (default 0)
//    t8       burn for 8 s, then fade out (0 / absent = forever)
//    g2       grow in over 2 s (volume zones)          s0p2 = size at the start of the growth (default 0.1 when g is set)
//    o3       fade out over 3 s at the end             e0p1 = size at the end of the fade (default 0, then the zone is off)
//    z        damage also scales with the zone's size (like env_fire's damage scale)
//    f        damage falls off linearly from the centre to the edge (volume zones)
//    p0p5     damage grows by 50% per second spent inside (cap with pm3 = at most x3)
//    c100     heal never goes above this health (default: the player's max health)
//    tr       CS:GO trigger_hurt timing: one clock for the whole zone (first tick the moment someone enters, then every i),
//             every player inside gets the amount with the fraction dropped (no carry-over: dn0p5 heals nothing), and the
//             clock stops when a tick touches nobody. What the converter writes for a trigger_hurt with negative damage.
//    dbl      CS:GO's "doubling w/ forgiveness" damage model (with tr): after a tick that touched anyone the amount doubles,
//    dc10     up to dc (per tick; CS:GO never capped a negative amount, so a doubling heal has no cap), and goes back to d
//             after a tick that touched nobody once 3 s have passed since the last one that did
//    ofs0x0x8 the volume's centre is this far from the entity's origin (world axes). The converter gives a trigger_hurt
//             parented to a weapon b<half sizes> (+ ofs): CS2 never lets a trigger touch the player holding its parent, so
//             the zone is tested as a box that follows the weapon instead (in CS:GO the holder was healed).
//    m2 / m3  only hurts / heals Ts (2) or CTs (3)     ia = ignores armor       off = starts switched off
//    kburn kpoison kshock kblast kdrown kfall kcrush kbullet kgen   damage type (default generic)
//   The first word that is not an option is the zone's LABEL (used by the inputs below), e.g.
//    hurt_d10_i0p5_lava                  trigger_multiple: 10 damage every half second
//    hurt_dn5_i1_r192_healpad            info_target: heals 5 per second inside a 192 unit sphere
//    hurt_d8_i0p25_b96x96x64_g2_t10_o4_fire1     env_fire-like: grows in 2 s, burns 10 s, shrinks away over 4 s
//
//
// FIRE zones (an env_fire, damage side, as CS:GO's fire.cpp did it): any point entity named hurt_fire<size>_... with
//    fire64   the env_fire's firesize              a10    fireattack (s to full heat, default 4)
//    ds1      damagescale (default 1)              fu13   health = burn time in s (absent / 0 = infinite)
//    ip32     ignitionpoint (default 32)           inf    Infinite flag    full  Start Full flag    on  Start On flag
//    dis      StartDisabled                        plasma Plasma fire type (CS2 has no plasma damage: burns)
//   The entity sits where the env_fire was (its origin = the bottom of the flames). Heat grows from 0 to full over the attack
//   time (Start On and Start Full fires start full); the damage box grows with it (half-width max(16, firesize/2 x heat
//   fraction), firesize x heat fraction tall). Damage once a second from the moment it lights: floor(1 + heat^2/64 x 0.1 x
//   damagescale), only to players the fire's centre can see. A fire with a burn time goes out for good after it.
//   CONTROL a fire by sending inputs to the zone entity itself (CS2 ignores script inputs registered after the map loads,
//   so per-label RunScriptInput names do not work):  FireUser1 = StartFire (ignored while disabled)
//   FireUser2 = Extinguish (also clears Infinite; ExtinguishTemporary does the same here)   FireUser3 = Disable (puts a
//   burning fire out)   FireUser4 = Enable.   Extinguish's seconds don't matter: the damage stopped at once in CS:GO too.
//
// LASER zones (an env_laser / env_beam that did damage, as CS:GO's CBeam::BeamDamage): a point entity named hurt_laser_d<dps>_..._<label>
//   at the laser's start (parented like the laser), and a second point entity named hurt_laserend_<label> at the laser's end (parented
//   to what it aimed at, so the beam follows a moving door). Every tick a line is traced from start to end; the first thing it meets
//   takes the damage if it is a player: d is damage PER SECOND (env_laser's "damage"), dealt each tick for the time passed, fractions
//   carried over. Anything else in the way (a wall, a prop) shields what is behind it, as in CS:GO. What the laser is mounted on (its
//   parent chain, its target) does not block it. Switch it with FireUser1-3 = On / Off / Toggle like the other zones; "off" = starts off.
//
// CONTROL any other zone the same way, with inputs sent to the zone entity (a trigger zone: to the trigger):
//    FireUser1 = On   FireUser2 = Off   FireUser3 = Toggle   FireUser4 = Stop (fade out)
//
// INPUTS (RunScriptInput <name>), <label> is the zone's label (every zone with that label obeys). NOTE: CS2 ignored these in
// testing (they are registered when the map starts, too late); use FireUser1-4 above. Kept for the no-label forms below:
//    Hurt_On_<label>  Hurt_Off_<label>  Hurt_Toggle_<label>  Hurt_Stop_<label> (start the fade-out)  Hurt_Reset_<label>
//    Hurt_Heal_<label> (make it heal)  Hurt_Hurt_<label> (make it hurt)  Hurt_Flip_<label>
//    Hurt_Faster_<label> (interval / 2)  Hurt_Slower_<label> (interval x 2)  Hurt_NormalSpeed_<label>
//    Hurt_Bigger_<label> (size x 1.25)  Hurt_Smaller_<label> (size / 1.25)  Hurt_NormalSize_<label>
//    Hurt_On / Hurt_Off / Hurt_Toggle / Hurt_Stop   with no label: act on the zone entity that fired the input
// Damage is dealt as the world (no attacker), so there is no kill credit and no -1 for a teamkill.
// Damage and healing below 1 per tick carry over: d0p5 = 1 point every second tick (health is whole numbers).
//
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.

import { Instance, CSDamageTypes, CSDamageFlags } from "cs_script/point_script";

// ---- Settings ----
const PREFIX = "hurt_";
const TRIGGER_CLASSES = ["trigger_multiple", "trigger_once", "trigger_hurt"];
// Entity classes searched for volume zones (a "hurt_" name on any other class is ignored: add its class here).
const VOLUME_CLASSES = ["info_target", "info_particle_system", "prop_dynamic", "prop_physics", "prop_physics_override", "func_brush", "func_movelinear", "func_rotating", "point_template"];
const DEFAULT_INTERVAL = 0.5;
// Draw the volume zones in game (dev builds only).
const DEBUG_DRAW = false;
const DEBUG = false;
// A few one-off lines in the console (zones found, the first touches and hits, any error) so a zone that does nothing can be diagnosed.
const LOG = true;
// Each zone gets its own few lines (so one busy zone can't use up the lines of the others); LOG_TOTAL caps the whole round.
const LOG_PER_ZONE = 6;
const LOG_TOTAL = 200;
const logLeftOf = new Map();
let logTotal = 0;
function log(msg, key = "") {
    if (!LOG || logTotal >= LOG_TOTAL) return;
    const left = logLeftOf.has(key) ? logLeftOf.get(key) : LOG_PER_ZONE;
    if (left <= 0) return;
    logLeftOf.set(key, left - 1);
    logTotal++;
    Instance.Msg("hurt_zone: " + msg + (left === 1 && key ? "  (no more lines for this zone)" : ""));
}

// ---- Helpers ----
const HULL = 16; // player half-width
const HEIGHT_STAND = 72;
const HEIGHT_DUCK = 54;
const TYPES = {
    gen: CSDamageTypes.GENERIC, burn: CSDamageTypes.BURN, poison: CSDamageTypes.POISON, shock: CSDamageTypes.SHOCK,
    blast: CSDamageTypes.BLAST, drown: CSDamageTypes.DROWN, fall: CSDamageTypes.FALL, crush: CSDamageTypes.CRUSH,
    bullet: CSDamageTypes.BULLET, sonic: CSDamageTypes.SONIC,
};
const NUM = "n?\\d+(?:p\\d+)?";
const clamp01 = (x) => Math.max(0, Math.min(1, x));

function num(s) {
    return Number(s.replace(/^n/, "-").replace("p", "."));
}

function slotOf(pawn) {
    const c = pawn && pawn.IsValid() ? pawn.GetPlayerController() : undefined;
    return c ? c.GetPlayerSlot() : undefined;
}

function toPawn(ent) {
    if (!ent || !ent.IsValid()) return undefined;
    if (typeof ent.GetPlayerPawn === "function") return ent.GetPlayerPawn(); // a controller
    if (typeof ent.GetPlayerController === "function") return ent; // a pawn
    return undefined;
}

// ---- Zone parsing ----
function parseZone(ent) {
    const name = ent.GetEntityName();
    if (!name.startsWith(PREFIX)) return undefined;
    if (name.startsWith(PREFIX + "laserend_")) return undefined; // the far end of a laser zone, not a zone
    const z = {
        ent, name, label: undefined, kind: "trigger", r: 0, half: [0, 0, 0],
        dmg: 0, interval: DEFAULT_INTERVAL, warm: 0, burn: 0, grow: 0, fade: 0, s0: undefined, e0: 0,
        scaleDmg: false, falloff: false, ramp: 0, rampMax: Infinity, cap: undefined, team: 0, ignoreArmor: false,
        types: CSDamageTypes.GENERIC, startOff: false, src: false, dbl: false, dcap: Infinity,
        fire: undefined, // { size, attack, ds, fuelKey, ip, inf, full, on, dis, ... } for fire zones
        // runtime
        phase: "off", t0: 0, sizeMul: 1, intervalMul: 1, flip: 1, inside: new Map(), per: new Map(), acc: new Map(), conns: [],
    };
    let m;
    for (const tok of name.slice(PREFIX.length).split("_")) {
        if (tok === "laser") z.kind = "laser";
        else if ((m = tok.match(new RegExp("^fire(" + NUM + ")$")))) { z.kind = "fire"; z.fire = Object.assign(z.fire || {}, { size: num(m[1]) }); }
        else if ((m = tok.match(new RegExp("^a(" + NUM + ")$")))) (z.fire = z.fire || {}).attack = num(m[1]);
        else if ((m = tok.match(new RegExp("^ds(" + NUM + ")$")))) (z.fire = z.fire || {}).ds = num(m[1]);
        else if ((m = tok.match(new RegExp("^fu(" + NUM + ")$")))) (z.fire = z.fire || {}).health = num(m[1]);
        else if ((m = tok.match(new RegExp("^ip(" + NUM + ")$")))) (z.fire = z.fire || {}).ip = num(m[1]);
        else if (tok === "inf" || tok === "full" || tok === "on" || tok === "dis" || tok === "plasma") (z.fire = z.fire || {})[tok] = true;
        else if ((m = tok.match(new RegExp("^d(" + NUM + ")$")))) z.dmg = num(m[1]);
        else if ((m = tok.match(new RegExp("^h(" + NUM + ")$")))) z.dmg = -Math.abs(num(m[1]));
        else if ((m = tok.match(new RegExp("^i(" + NUM + ")$")))) z.interval = Math.max(0.015, num(m[1]));
        else if ((m = tok.match(new RegExp("^w(" + NUM + ")$")))) z.warm = num(m[1]);
        else if ((m = tok.match(new RegExp("^t(" + NUM + ")$")))) z.burn = num(m[1]);
        else if ((m = tok.match(new RegExp("^g(" + NUM + ")$")))) z.grow = num(m[1]);
        else if ((m = tok.match(new RegExp("^o(" + NUM + ")$")))) z.fade = num(m[1]);
        else if ((m = tok.match(new RegExp("^s(" + NUM + ")$")))) z.s0 = num(m[1]);
        else if ((m = tok.match(new RegExp("^e(" + NUM + ")$")))) z.e0 = num(m[1]);
        else if ((m = tok.match(new RegExp("^pm(" + NUM + ")$")))) z.rampMax = num(m[1]);
        else if ((m = tok.match(new RegExp("^p(" + NUM + ")$")))) z.ramp = num(m[1]);
        else if ((m = tok.match(new RegExp("^c(" + NUM + ")$")))) z.cap = num(m[1]);
        else if ((m = tok.match(new RegExp("^r(" + NUM + ")$")))) { z.kind = "sphere"; z.r = num(m[1]); }
        else if ((m = tok.match(new RegExp("^b(" + NUM + ")x(" + NUM + ")x(" + NUM + ")$")))) { z.kind = "box"; z.half = [num(m[1]), num(m[2]), num(m[3])]; }
        else if ((m = tok.match(new RegExp("^ofs(" + NUM + ")x(" + NUM + ")x(" + NUM + ")$")))) z.ofs = [num(m[1]), num(m[2]), num(m[3])];
        else if ((m = tok.match(/^m([23])$/))) z.team = Number(m[1]);
        else if ((m = tok.match(/^k([a-z]+)$/)) && TYPES[m[1]] !== undefined) z.types = TYPES[m[1]];
        else if (tok === "z") z.scaleDmg = true;
        else if (tok === "f") z.falloff = true;
        else if (tok === "ia") z.ignoreArmor = true;
        else if (tok === "tr") z.src = true;
        else if (tok === "dbl") z.dbl = true;
        else if ((m = tok.match(new RegExp("^dc(" + NUM + ")$")))) z.dcap = num(m[1]);
        else if (tok === "off") z.startOff = true;
        else if (tok && z.label === undefined) z.label = tok;
    }
    if (z.label === undefined) z.label = name;
    if (z.kind === "fire") {
        const f = z.fire;
        f.attack = f.attack === undefined ? 4 : f.attack;
        f.ds = f.ds === undefined ? 1 : f.ds;
        f.health = f.health || 0;
        f.ip = f.ip === undefined ? 32 : f.ip;
        f.maxHeat = FIRE_MAX_HEAT * f.size / FIRE_HEIGHT;
        z.types = CSDamageTypes.BURN;
        return z;
    }
    if (z.s0 === undefined) z.s0 = z.grow > 0 ? 0.1 : 1;
    if (z.kind === "laser") {
        if (z.types === CSDamageTypes.GENERIC) z.types = CSDamageTypes.SHOCK; // CS:GO: DMG_ENERGYBEAM
        return z;
    }
    // a volume needs a size; a trigger brush is its own size
    if (TRIGGER_CLASSES.includes(ent.GetClassName()) && z.kind !== "box" && z.kind !== "sphere") z.kind = "trigger";
    else if (z.kind === "trigger") z.kind = "sphere", z.r = 128; // a point entity with no size in the name
    return z;
}

// ---- Fire zones (Source SDK fire.cpp) ----
const FIRE_HEIGHT = 256;
const FIRE_MAX_HEAT = 64;
const FIRE_DMG_INTERVAL = 1.0; // fire_dmginterval
const FIRE_DMG_BASE = 1; // fire_dmgbase
const FIRE_DMG_SCALE = 0.1; // fire_dmgscale
const FIRE_ABSORB_RATE = 3; // fire_absorbrate

function fireSpawn(z) {
    // CFire::Spawn + Activate
    const f = z.fire;
    Object.assign(f, { state: "out", heat: 0, absorb: f.ip * 0.05, enabled: !f.dis, fuel: 0, permanent: false, dead: false, dmgTime: 0, outAt: 0, infNow: !!f.inf });
    if (f.full) f.heat = f.maxHeat;
    if (f.on) {
        f.heat = f.maxHeat;
        fireStart(z);
    }
}

function fireStart(z) {
    // CFire::StartFire (no enabled check) -> Init -> Start
    const f = z.fire;
    if (f.dead || f.state !== "out") return; // the effect still exists (burning or going out)
    f.fuel = f.infNow ? 0 : f.health;
    f.permanent = f.fuel > 0;
    if (f.full) f.heat = f.maxHeat;
    f.state = "burning";
    f.dmgTime = 0;
    log(`${z.name} lit (heat ${f.heat.toFixed(1)} / ${f.maxHeat}, ${firePawnCount()} live player(s))`, z.name + ":fire");
    gatherFirePawns();
    fireUpdate(z, 0); // Start() thinks right away: the first hit is immediate
}

function fireAddHeat(f, heat) {
    if (!f.enabled) return;
    if (f.absorb > 0) {
        const absorbDamage = heat * FIRE_ABSORB_RATE;
        if (absorbDamage > f.absorb) {
            heat -= f.absorb / FIRE_ABSORB_RATE;
            f.absorb = 0;
        } else {
            f.absorb -= absorbDamage;
            heat = 0;
        }
    }
    f.heat = Math.min(f.maxHeat, f.heat + heat);
}

function fireGoOutIn(z, seconds) {
    const f = z.fire;
    if (f.state !== "burning") return;
    f.state = "goingout"; // no more damage from here (CFire::GoOutInSeconds stops the burn think)
    f.outAt = now + seconds;
}

function fireOut(z) {
    // CFire::GoOut
    const f = z.fire;
    f.heat = Math.min(0, f.heat - 20);
    f.state = "out";
    if (f.permanent) f.dead = true; // a fire with a burn time is removed when it goes out
}

let firePawns = new Map();
function gatherFirePawns() {
    firePawns = new Map();
    for (let slot = 0; slot < 64; slot++) {
        const c = Instance.GetPlayerController(slot);
        const p = c ? c.GetPlayerPawn() : undefined;
        if (p && p.IsValid() && p.IsAlive()) firePawns.set(slot, p);
    }
}

function fireUpdate(z, dt) {
    const f = z.fire;
    if (f.fuel !== 0) {
        f.fuel -= dt;
        if (f.fuel <= 0) {
            fireGoOutIn(z, 1);
            return;
        }
    }
    const strength = f.heat / FIRE_MAX_HEAT;
    const added = (f.attack > 0 ? f.maxHeat / f.attack : f.maxHeat) * dt;
    fireAddHeat(f, added);
    const outputHeat = strength * f.heat;
    if (f.dmgTime > now) return;
    f.dmgTime = now + FIRE_DMG_INTERVAL;
    const dmg = Math.trunc((FIRE_DMG_BASE + outputHeat * FIRE_DMG_SCALE * f.ds) * FIRE_DMG_INTERVAL);
    if (dmg <= 0 || !z.ent.IsValid()) return;
    const o = z.ent.GetAbsOrigin();
    let r = 0, h = 0;
    if (f.heat > 0) {
        const scale = f.heat / f.maxHeat;
        r = Math.max(16, scale * f.size * 0.5);
        h = f.size * scale;
    }
    const centre = { x: o.x, y: o.y, z: o.z + f.size / 2 };
    if (LOG) {
        let near = Infinity;
        for (const [, pawn] of firePawns) {
            if (!pawn.IsValid()) continue;
            const q = pawn.GetAbsOrigin();
            near = Math.min(near, Math.hypot(q.x - o.x, q.y - o.y));
        }
        log(`${z.name} burning at ${o.x.toFixed(0)} ${o.y.toFixed(0)} ${o.z.toFixed(0)}: damage ${dmg}, reach ${r.toFixed(0)} wide / ${h.toFixed(0)} high, nearest player ${near === Infinity ? "none" : near.toFixed(0) + " units"} away`, z.name + ":tick");
    }
    if (DEBUG_DRAW) Instance.DebugBox({ mins: { x: o.x - r, y: o.y - r, z: o.z }, maxs: { x: o.x + r, y: o.y + r, z: o.z + h }, duration: FIRE_DMG_INTERVAL, color: { r: 255, g: 60, b: 0 } });
    for (const [, pawn] of firePawns) {
        const p = pawn.GetAbsOrigin();
        const ph = pawn.IsDucked() ? HEIGHT_DUCK : HEIGHT_STAND;
        if (p.x + HULL < o.x - r || p.x - HULL > o.x + r || p.y + HULL < o.y - r || p.y - HULL > o.y + r || p.z + ph < o.z || p.z > o.z + h) continue;
        const blocker = fireBlocker(z, centre, { x: p.x, y: p.y, z: p.z + ph / 2 }, pawn);
        if (blocker) {
            log(`${z.name} blocked by ${blocker} (no damage through walls)`, z.name + ":block");
            continue;
        }
        hit(z, pawn, dmg);
    }
}

function firePawnCount() {
    return firePawns.size;
}

// The top of a zone's parent chain (the boat a fire is parented to), or undefined.
function rootOf(ent) {
    let r = ent;
    try {
        for (let i = 0; i < 16; i++) {
            const p = r.GetParent();
            if (!p || !p.IsValid()) break;
            r = p;
        }
    } catch (e) {
        return undefined;
    }
    return r === ent ? undefined : r;
}
function sameEnt(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    const n = a.GetEntityName();
    return n !== "" && n === b.GetEntityName() && a.GetClassName() === b.GetClassName();
}
function ownPart(z, ent) {
    // Something the fire is mounted on (its parent chain, or another child of the same root, e.g. the motor prop on the boat).
    if (!ent || !ent.IsValid()) return false;
    if (z.rig === undefined) z.rig = rootOf(z.ent) || null;
    if (!z.rig) return false;
    return sameEnt(ent, z.rig) || sameEnt(rootOf(ent), z.rig);
}
// CFire traces from the flames to the player; the prop/brush the fire sits on (CS:GO dropped the fire onto its top) must not block it.
// Returns a description of what blocks the line, or "" when the player can be hit.
function fireBlocker(z, start, end, pawn) {
    let s = start;
    for (let step = 0; step < 6; step++) {
        const tr = Instance.TraceLine({ start: s, end, ignoreEntity: pawn });
        if (!tr.didHit && !tr.startedInSolid) return "";
        if (!ownPart(z, tr.hitEntity)) return tr.hitEntity ? `${tr.hitEntity.GetClassName()} ${tr.hitEntity.GetEntityName()}` : "the world";
        const dx = end.x - s.x, dy = end.y - s.y, dz = end.z - s.z;
        const len = Math.hypot(dx, dy, dz);
        if (len < 2) return "";
        const at = tr.startedInSolid ? 8 : Math.max(1, tr.fraction * len + 1);
        if (at >= len) return "";
        s = { x: s.x + dx / len * at, y: s.y + dy / len * at, z: s.z + dz / len * at };
    }
    return "";
}

function fireTick(z, dt) {
    const f = z.fire;
    if (f.dead) return;
    if (f.state === "goingout" && now >= f.outAt) fireOut(z);
    else if (f.state === "burning") fireUpdate(z, dt);
}

const FIRE_ACTIONS = {
    StartFire: (z) => { if (z.fire.enabled) fireStart(z); },
    Extinguish: (z) => { z.fire.infNow = false; fireGoOutIn(z, 0); },
    ExtinguishTemporary: (z) => fireGoOutIn(z, 0),
    Enable: (z) => { z.fire.enabled = true; },
    Disable: (z) => { z.fire.enabled = false; fireGoOutIn(z, 0); },
};

// ---- State ----
let zones = [];
const labels = new Set(); // input names already registered
let now = 0;

function scaleOf(z) {
    if (z.phase === "grow") return (z.s0 + (1 - z.s0) * clamp01(z.grow > 0 ? (now - z.t0) / z.grow : 1)) * z.sizeMul;
    if (z.phase === "burn") return z.sizeMul;
    if (z.phase === "fade") return (1 + (z.e0 - 1) * clamp01(z.fade > 0 ? (now - z.t0) / z.fade : 1)) * z.sizeMul;
    return 0;
}

function setPhase(z, phase) {
    z.phase = phase;
    z.t0 = now;
    if (phase === "off") {
        z.inside.clear();
        z.per.clear();
        z.acc.clear();
        z.clockOn = false;
        z.cur = undefined;
    }
}

function turnOn(z) {
    if (z.phase === "off" || z.phase === "fade") setPhase(z, z.grow > 0 ? "grow" : "burn");
}

function turnOff(z) {
    setPhase(z, "off");
}

function stop(z) {
    if (z.phase === "off") return;
    if (z.fade > 0) setPhase(z, "fade");
    else turnOff(z);
}

function resetZone(z) {
    if (z.kind === "fire") {
        z.inside.clear();
        z.per.clear();
        z.acc.clear();
        fireSpawn(z);
        return;
    }
    z.sizeMul = 1;
    z.intervalMul = 1;
    z.flip = 1;
    z.inside.clear();
    z.per.clear();
    setPhase(z, "off");
    if (!z.startOff) turnOn(z);
}

// ---- Touch tracking (trigger zones) ----
function hook(z) {
    const add = (output, fn) => {
        const id = Instance.ConnectOutput(z.ent, output, fn);
        if (id !== undefined) z.conns.push(id);
    };
    add("OnStartTouch", (d) => {
        const pawn = toPawn(d.activator) || toPawn(d.caller);
        const slot = slotOf(pawn);
        log(`${z.name} OnStartTouch, player slot ${slot}`, z.name);
        if (slot !== undefined) z.inside.set(slot, pawn);
    });
    add("OnEndTouch", (d) => {
        const pawn = toPawn(d.activator) || toPawn(d.caller);
        const slot = slotOf(pawn);
        if (LOG && slot !== undefined && z.ent.IsValid()) {
            const o = z.ent.GetAbsOrigin(), p = pawn.GetAbsOrigin();
            log(`${z.name} OnEndTouch, player slot ${slot} (player ${Math.hypot(p.x - o.x, p.y - o.y).toFixed(0)} units from the zone's origin, ${(p.z - o.z).toFixed(0)} above it)`, z.name);
        }
        if (slot !== undefined) z.inside.delete(slot);
    });
}

const USER_ACTIONS = {
    fire: ["StartFire", "Extinguish", "Disable", "Enable"],
    other: ["On", "Off", "Toggle", "Stop"],
};
function hookUser(z) {
    const names = z.kind === "fire" ? USER_ACTIONS.fire : USER_ACTIONS.other;
    names.forEach((action, i) => {
        const id = Instance.ConnectOutput(z.ent, `OnUser${i + 1}`, () => {
            log(`${z.name}: ${action} (FireUser${i + 1})`, z.name + ":io");
            if (z.kind === "fire") FIRE_ACTIONS[action](z);
            else ACTIONS[action](z);
        });
        if (id !== undefined) z.conns.push(id);
    });
}

function parentName(ent) {
    try {
        const p = ent.GetParent();
        return p && p.IsValid() ? `${p.GetClassName()} ${p.GetEntityName()}` : "none";
    } catch (e) {
        return "?";
    }
}

function scan() {
    for (const z of zones) for (const id of z.conns) Instance.DisconnectOutput(id);
    zones = [];
    logLeftOf.clear(); // every round gets its own lines
    logTotal = 0;
    const seen = new Set();
    const ents = [];
    for (const cls of TRIGGER_CLASSES) ents.push(...Instance.FindEntitiesByClass(cls));
    for (const cls of VOLUME_CLASSES) ents.push(...Instance.FindEntitiesByClass(cls));
    // and by name, whatever the class (CS2 entity names take a trailing * wildcard)
    let byName = 0;
    try {
        const named = Instance.FindEntitiesByName(PREFIX + "*");
        byName = named.length;
        ents.push(...named);
    } catch (e) {
        Instance.Msg(`hurt_zone: search by name failed (${e})`);
    }
    for (const e of ents) {
        if (!e.IsValid()) continue;
        const o = e.GetAbsOrigin();
        const key = `${e.GetClassName()}|${e.GetEntityName()}|${o.x.toFixed(1)}|${o.y.toFixed(1)}|${o.z.toFixed(1)}`;
        if (seen.has(e) || seen.has(key)) continue;
        seen.add(e);
        seen.add(key);
        const z = parseZone(e);
        if (!z) continue;
        zones.push(z);
        if (z.kind === "trigger") hook(z);
        hookUser(z);
        resetZone(z);
    }
    registerInputs();
    if (LOG) {
        Instance.Msg(`hurt_zone: ${zones.length} zone(s) found (${byName} entit(ies) named ${PREFIX}*, info_target: ${Instance.FindEntitiesByClass("info_target").length})`);
        for (const z of zones.slice(0, 30)) {
            if (z.kind === "fire") Instance.Msg(`hurt_zone:   ${z.name}  ${z.conns.length} output hook(s)  parent ${parentName(z.ent)}  fire size ${z.fire.size}  attack ${z.fire.attack}s  full damage ${Math.trunc(1 + (z.fire.maxHeat * z.fire.maxHeat / 64) * 0.1 * z.fire.ds)}/s  ${z.fire.state}${z.fire.enabled ? "" : " disabled"}  label ${z.label}`);
            else if (z.kind === "laser") Instance.Msg(`hurt_zone:   ${z.name}  ${z.conns.length} output hook(s)  parent ${parentName(z.ent)}  laser  ${z.dmg}/s  end ${laserEnd(z) ? "found" : "MISSING"}  phase ${z.phase}`);
            else Instance.Msg(`hurt_zone:   ${z.name}  ${z.conns.length} output hook(s)  parent ${parentName(z.ent)}  ${z.kind}  dmg ${z.dmg}  every ${z.interval}s  phase ${z.phase}`);
        }
    }
    if (DEBUG) Instance.Msg(`hurt_zone: ${zones.length} zones`);
}

// ---- Inputs ----
const ACTIONS = {
    On: turnOn,
    Off: turnOff,
    Toggle: (z) => (z.phase === "off" ? turnOn(z) : turnOff(z)),
    Stop: stop,
    Reset: resetZone,
    Heal: (z) => (z.flip = z.dmg > 0 ? -1 : 1),
    Hurt: (z) => (z.flip = z.dmg < 0 ? -1 : 1),
    Flip: (z) => (z.flip = -z.flip),
    Faster: (z) => (z.intervalMul = Math.max(0.03, z.intervalMul / 2)),
    Slower: (z) => (z.intervalMul *= 2),
    NormalSpeed: (z) => (z.intervalMul = 1),
    Bigger: (z) => (z.sizeMul *= 1.25),
    Smaller: (z) => (z.sizeMul /= 1.25),
    NormalSize: (z) => (z.sizeMul = 1),
};

function registerInputs() {
    for (const z of zones) {
        if (z.kind === "fire") {
            for (const action of Object.keys(FIRE_ACTIONS)) {
                const key = `Hurt_${action}_${z.label}`;
                if (labels.has(key)) continue;
                labels.add(key);
                const label = z.label;
                Instance.OnScriptInput(key, () => {
                    for (const q of zones) if (q.label === label && q.kind === "fire") FIRE_ACTIONS[action](q);
                });
            }
            continue;
        }
        for (const action of Object.keys(ACTIONS)) {
            const key = `Hurt_${action}_${z.label}`;
            if (labels.has(key)) continue;
            labels.add(key);
            const label = z.label;
            Instance.OnScriptInput(key, () => {
                for (const q of zones) if (q.label === label) ACTIONS[action](q);
            });
        }
    }
}

for (const action of ["On", "Off", "Toggle", "Stop"]) {
    Instance.OnScriptInput(`Hurt_${action}`, (d) => {
        for (const z of zones) if (z.ent === d.caller) ACTIONS[action](z);
    });
}

// ---- Damage ----
function hit(z, pawn, dmg) {
    try {
        hit2(z, pawn, dmg);
    } catch (e) {
        Instance.Msg(`hurt_zone: ${z.name} damage/heal call failed: ${e}`);
    }
}

// Fractions carry over between ticks (per zone and player), so 0.5 per tick is 1 every second tick, not 1 every tick.
function whole(z, pawn, dmg) {
    const slot = slotOf(pawn);
    const total = (z.acc.get(slot) || 0) + dmg;
    const n = Math.trunc(total + (total > 0 ? 1e-6 : -1e-6));
    z.acc.set(slot, total - n);
    return n;
}

function hit2(z, pawn, raw) {
    const dmg = z.src ? Math.trunc(raw) : whole(z, pawn, raw);
    if (dmg > 0) {
        log(`${z.name} hurts slot ${slotOf(pawn)} by ${dmg} (health ${pawn.GetHealth()})`, z.name + ":hit");
        pawn.TakeDamage({
            damage: dmg,
            damageTypes: z.types,
            damageFlags: z.ignoreArmor ? CSDamageFlags.IGNORE_ARMOR : CSDamageFlags.NONE,
            inflictor: z.ent,
        });
    } else if (dmg < 0) {
        const cur = pawn.GetHealth();
        const top = z.cap !== undefined ? z.cap : pawn.GetMaxHealth();
        const next = Math.min(top, cur - dmg);
        if (next > cur) {
            pawn.SetHealth(next);
            log(`${z.name} heals slot ${slotOf(pawn)} by ${next - cur} (health ${cur} -> ${next})`, z.name + ":hit");
        }
    }
}

// Distance-based test of a player box against a volume zone -> 0..1 (0 = edge) or -1 = outside.
function volumeReach(z, pawn, scale) {
    const c0 = z.ent.GetAbsOrigin();
    const c = z.ofs ? { x: c0.x + z.ofs[0], y: c0.y + z.ofs[1], z: c0.z + z.ofs[2] } : c0;
    const o = pawn.GetAbsOrigin();
    const h = pawn.IsDucked() ? HEIGHT_DUCK : HEIGHT_STAND;
    const lo = [o.x - HULL, o.y - HULL, o.z];
    const hi = [o.x + HULL, o.y + HULL, o.z + h];
    const cc = [c.x, c.y, c.z];
    if (z.kind === "box") {
        const half = z.half.map((v) => v * scale);
        for (let a = 0; a < 3; a++) if (hi[a] < cc[a] - half[a] || lo[a] > cc[a] + half[a]) return -1;
        const mid = [o.x, o.y, o.z + h / 2];
        let far = 0;
        for (let a = 0; a < 3; a++) far = Math.max(far, half[a] > 0 ? Math.abs(mid[a] - cc[a]) / half[a] : 1);
        return clamp01(1 - far);
    }
    const r = z.r * scale;
    let d2 = 0;
    for (let a = 0; a < 3; a++) {
        const p = Math.max(lo[a], Math.min(cc[a], hi[a]));
        d2 += (p - cc[a]) * (p - cc[a]);
    }
    if (d2 > r * r) return -1;
    const mid = [o.x, o.y, o.z + h / 2];
    const dc = Math.hypot(mid[0] - cc[0], mid[1] - cc[1], mid[2] - cc[2]);
    return r > 0 ? clamp01(1 - dc / r) : 0;
}

// ---- Laser zones (Source SDK beam.cpp CBeam::BeamDamage / env_laser StrikeThink) ----
function laserEnd(z) {
    if (z.end && z.end.IsValid()) return z.end;
    try {
        const found = Instance.FindEntitiesByName(PREFIX + "laserend_" + z.label);
        z.end = found.length ? found[0] : undefined;
    } catch (e) {
        z.end = undefined;
    }
    if (!z.end) log(`${z.name}: no ${PREFIX}laserend_${z.label} entity, so this laser has no end and hurts nobody`, z.name + ":end");
    return z.end;
}
function laserTick(z, pawns) {
    const endEnt = laserEnd(z);
    if (!endEnt || !endEnt.IsValid()) return;
    const dt = tickDt;
    if (dt <= 0) return;
    const end = endEnt.GetAbsOrigin();
    if (z.endRig === undefined) z.endRig = rootOf(endEnt) || null;
    let s = z.ent.GetAbsOrigin();
    for (let step = 0; step < 8; step++) {
        const tr = Instance.TraceLine({ start: s, end, ignoreEntity: z.ent });
        if (!tr.didHit && !tr.startedInSolid) return; // reached the end: nobody in the beam
        const he = tr.hitEntity;
        const pawn = toPawn(he);
        if (pawn && pawn.IsValid() && pawn.IsAlive()) {
            if (z.team && pawn.GetTeamNumber() !== z.team) return;
            const slot = slotOf(pawn);
            if (slot === undefined || !pawns.has(slot)) return;
            if (DEBUG_DRAW) Instance.DebugLine({ start: z.ent.GetAbsOrigin(), end: pawn.GetAbsOrigin(), duration: 0.05, color: { r: 255, g: 0, b: 0 } });
            hit(z, pawn, z.dmg * z.flip * dt);
            return; // the first thing the beam meets takes it all
        }
        // the laser's own mount (its parent chain) and what it aims at do not block it; anything else does
        const mine = ownPart(z, he) || (he && (sameEnt(he, endEnt) || (z.endRig && (sameEnt(he, z.endRig) || sameEnt(rootOf(he), z.endRig)))));
        if (!mine) return;
        const dx = end.x - s.x, dy = end.y - s.y, dz = end.z - s.z;
        const len = Math.hypot(dx, dy, dz);
        if (len < 2) return;
        const at = tr.startedInSolid ? 8 : Math.max(1, tr.fraction * len + 1);
        if (at >= len) return;
        s = { x: s.x + dx / len * at, y: s.y + dy / len * at, z: s.z + dz / len * at };
    }
}

function tickZone(z, pawns) {
    if (z.phase === "off") return;
    if (!z.ent.IsValid()) return;
    if (z.kind === "laser") {
        laserTick(z, pawns);
        return;
    }
    // lifecycle
    if (z.phase === "grow" && now - z.t0 >= z.grow) setPhase(z, "burn");
    if (z.phase === "burn" && z.burn > 0 && now - z.t0 >= z.burn) stop(z);
    if (z.phase === "fade" && now - z.t0 >= z.fade) {
        turnOff(z);
        return;
    }
    if (LOG && z.kind === "trigger") {
        const o = z.ent.GetAbsOrigin();
        if (!z.seenAt) z.seenAt = o;
        else if (Math.hypot(o.x - z.seenAt.x, o.y - z.seenAt.y, o.z - z.seenAt.z) > 16) {
            log(`${z.name} moved to ${o.x.toFixed(0)} ${o.y.toFixed(0)} ${o.z.toFixed(0)} (with its parent)`, z.name + ":move");
            z.seenAt = o;
        }
    }
    const scale = scaleOf(z);
    if (scale <= 0) return;
    if (DEBUG_DRAW && z.kind !== "trigger") {
        const c = z.ent.GetAbsOrigin();
        if (z.kind === "box") {
            const h = z.half.map((v) => v * scale);
            Instance.DebugBox({ mins: { x: c.x - h[0], y: c.y - h[1], z: c.z - h[2] }, maxs: { x: c.x + h[0], y: c.y + h[1], z: c.z + h[2] }, duration: 0.05, color: { r: 255, g: 120, b: 0 } });
        } else {
            Instance.DebugSphere({ center: c, radius: z.r * scale, duration: 0.05, color: { r: 255, g: 120, b: 0 } });
        }
    }
    if (z.src) {
        srcTick(z, pawns, scale);
        return;
    }
    const present = new Set();
    for (const [slot, pawn] of pawns) {
        if (z.team && pawn.GetTeamNumber() !== z.team) continue;
        let reach = 1;
        if (z.kind === "trigger") {
            if (!z.inside.has(slot)) continue;
        } else {
            reach = volumeReach(z, pawn, scale);
            if (reach < 0) continue;
        }
        present.add(slot);
        let st = z.per.get(slot);
        if (!st) {
            st = { t0: now, next: now + z.warm };
            z.per.set(slot, st);
        }
        if (now < st.next) continue;
        let dmg = z.dmg * z.flip;
        if (z.ramp) dmg *= Math.min(z.rampMax, 1 + z.ramp * Math.max(0, now - st.t0 - z.warm));
        if (z.scaleDmg) dmg *= scale / z.sizeMul;
        if (z.falloff && z.kind !== "trigger") dmg *= reach;
        if (dmg) hit(z, pawn, dmg);
        const step = z.interval * z.intervalMul;
        st.next = Math.max(st.next + step, now + step * 0.25);
    }
    for (const slot of [...z.per.keys()]) if (!present.has(slot)) z.per.delete(slot);
    // trigger zones: drop players that died, left or changed slot without an OnEndTouch
    if (z.kind === "trigger") {
        for (const [slot, pawn] of [...z.inside]) if (!pawn.IsValid() || !pawn.IsAlive()) z.inside.delete(slot);
    }
}

// CS:GO trigger_hurt (triggers.cpp CTriggerHurt::HurtThink / HurtAllTouchers): one clock per zone, every 0.5 s while anyone is inside
function srcTick(z, pawns, scale) {
    const inside = [];
    for (const [slot, pawn] of pawns) {
        if (z.team && pawn.GetTeamNumber() !== z.team) continue;
        if (z.kind === "trigger" ? z.inside.has(slot) : volumeReach(z, pawn, scale) >= 0) inside.push(pawn);
    }
    if (z.cur === undefined) z.cur = z.dmg;
    if (!z.clockOn) {
        if (!inside.length) return;
        z.clockOn = true; // Touch starts the think right away
        z.nextTick = now;
    }
    if (now < z.nextTick) return;
    const amount = z.cur * z.flip;
    for (const pawn of inside) hit(z, pawn, amount);
    if (z.dbl) {
        if (!inside.length) {
            if (now > (z.resetAt || 0)) z.cur = z.dmg;
        } else {
            z.cur *= 2;
            if (z.cur > z.dcap) z.cur = z.dcap; // only a positive amount is ever capped, as in CS:GO
            z.resetAt = now + 3;
        }
    }
    if (!inside.length) z.clockOn = false;
    else z.nextTick = now + z.interval * z.intervalMul;
    if (z.kind === "trigger") {
        for (const [slot, pawn] of [...z.inside]) if (!pawn.IsValid() || !pawn.IsAlive()) z.inside.delete(slot);
    }
}

let lastNow = 0;
let tickDt = 0; // seconds since the last think (laser zones deal damage per second)
function think() {
    now = Instance.GetGameTime();
    const dt = Math.min(Math.max(now - lastNow, 0), 0.25);
    tickDt = dt;
    lastNow = now;
    if (zones.some((z) => z.kind === "fire" && !z.fire.dead && z.fire.state !== "out")) {
        gatherFirePawns();
        for (const z of zones) if (z.kind === "fire") fireTick(z, dt);
    }
    if (zones.some((z) => z.kind !== "fire" && z.phase !== "off")) {
        const pawns = new Map();
        for (let slot = 0; slot < 64; slot++) {
            const c = Instance.GetPlayerController(slot);
            const p = c ? c.GetPlayerPawn() : undefined;
            if (p && p.IsValid() && p.IsAlive()) pawns.set(slot, p);
        }
        for (const z of zones) if (z.kind !== "fire") tickZone(z, pawns);
    }
    Instance.SetNextThink(now);
}

Instance.OnActivate(() => {
    now = Instance.GetGameTime();
    lastNow = now;
    scan();
    Instance.SetThink(think);
    Instance.SetNextThink(now);
});
// map entities are new objects every round: hook and parse them again
Instance.OnRoundStart(() => {
    now = Instance.GetGameTime();
    lastNow = now;
    scan();
});
Instance.OnPlayerDisconnect(({ playerSlot }) => {
    for (const z of zones) {
        z.inside.delete(playerSlot);
        z.per.delete(playerSlot);
    }
});
Instance.OnPlayerReset(({ player }) => {
    const slot = slotOf(player);
    if (slot === undefined) return;
    for (const z of zones) z.per.delete(slot); // a respawn restarts the "time in the zone" clock
});
