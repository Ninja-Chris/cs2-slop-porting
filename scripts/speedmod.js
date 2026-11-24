// speedmod.js: player_speedmod for CS2 (the entity is gone there).
//
// What CS:GO did (Source SDK player.cpp CMovementSpeedMod::InputSpeedMod, gamemovement.cpp ProcessMovement):
//   ModifySpeed <k> on the activator (a player) ran that player's movement with the frame time multiplied by k, so the player ran at the
//   held weapon's speed x k (x0.52 walking, x0.34 ducked). It lasts until another ModifySpeed or the player respawns (CBasePlayer::Spawn
//   resets it to 1). ModifySpeed 0 freezes the player. With a value other than 1 the entity's flags also disabled buttons; of those
//   cs_script can only copy "Suppress jump" (4): an input ending in _nojump cancels jumps. Weapons / HUD / duck / use / walk / attack /
//   zoom suppression can't be done from a script.
//
// What this script does (each tick, for a player with a speed other than 1):
//   - on the ground with a movement key held: sets the velocity to the keys' direction x the held weapon's run speed x k, less the
//     friction and acceleration the engine's own move adds before moving, so the player covers run speed x k. (The way skink52's
//     mg_mario_party port does it, which runs in game; it measured the friction step as the ~8% that was missing without the correction.)
//     Measured too: the distance the player really covered each tick against the speed set, and a gain (0.75-1.5) learned on clear runs
//     closes any gap left (in game the run was still slow on flat floors). The velocity into a wall is taken off first (a player trace a
//     step up), as the engine's own move does; without it the player bounced off walls and slid away when the keys were let go.
//   - no key held, or in the air: left to the engine (normal friction stops the player; a jump carries the boosted speed)
//   - k = 0: no horizontal movement and no jumping
// Not copied: CS:GO's k-times faster acceleration, friction, air control and gravity (the player reaches the speed at once instead).
// (2026-11-22w followed CS:GO's movement with the frame time x k and inverted the engine's move each tick; any difference between that model
// and CS2's movement made it fall back to normal speed, and in game the player kept normal speed.)
//
// INPUTS: RunScriptInput Speed_<k> on this point_script, the player as the activator. <k> with "p" for the decimal point and "n"
//   for a minus: Speed_1p5 = 1.5, Speed_0p75, Speed_2, Speed_1 = back to normal. Add _nojump for an entity with "Suppress jump":
//   Speed_0p5_nojump. CS2 only accepts script inputs registered when the map loads, so every name the map uses must be in
//   SPEED_INPUTS below; the converter writes the map's ones between the markers, and COMMON_SPEEDS are always there.
//   The converter turns  <output> <player_speedmod> ModifySpeed <k>  into  <output> <this point_script> RunScriptInput Speed_<k>.
//
// Console (sv_cheats 1): spd_set <k> [nojump] (every player), spd_log 0|1 (a line per tick for slot 0 while it has a speed).

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


const SPEED_INPUTS = /*SPEEDMOD_INPUTS_BEGIN*/[]/*SPEEDMOD_INPUTS_END*/;
const COMMON_SPEEDS = ["0", "0p25", "0p5", "0p75", "1", "1p25", "1p5", "1p75", "2", "2p5", "3"];
let LOG = 0;
const VERSION = "2026-11-23b (wall clip only past the wall, measured gain)";

const V = (x, y, z) => ({ x, y, z });
const f1 = (x) => x.toFixed(1);
const say = (s) => Instance.Msg("speedmod: " + s);
const FRICTION = 5.2, STOP = 80, ACCEL = 5.5;   // sv_friction, sv_stopspeed, sv_accelerate (CS:GO and CS2 defaults)
const WALK_FACTOR = 0.52, DUCK_FACTOR = 0.34;
// run speed of the weapon in hand (CS:GO's; the knife and anything not listed: 250)
const WEAPON_SPEED = {
    weapon_glock: 240, weapon_hkp2000: 240, weapon_usp_silencer: 240, weapon_p250: 240, weapon_elite: 240, weapon_fiveseven: 240,
    weapon_tec9: 240, weapon_cz75a: 240, weapon_deagle: 230, weapon_revolver: 220, weapon_taser: 220,
    weapon_mac10: 240, weapon_mp9: 240, weapon_bizon: 240, weapon_mp5sd: 235, weapon_ump45: 230, weapon_p90: 230, weapon_mp7: 220,
    weapon_ak47: 215, weapon_galilar: 215, weapon_m4a1: 225, weapon_m4a1_silencer: 225, weapon_famas: 220, weapon_aug: 220, weapon_sg556: 210,
    weapon_awp: 200, weapon_ssg08: 230, weapon_g3sg1: 215, weapon_scar20: 215,
    weapon_nova: 220, weapon_xm1014: 215, weapon_mag7: 225, weapon_sawedoff: 210, weapon_m249: 195, weapon_negev: 150,
    weapon_hegrenade: 245, weapon_flashbang: 245, weapon_smokegrenade: 245, weapon_molotov: 245, weapon_incgrenade: 245, weapon_decoy: 245,
};
function wishSpeed(pawn) {
    const w = pawn.GetActiveWeapon();
    let sp = (w && w.IsValid() && WEAPON_SPEED[w.GetClassName()]) || 250;
    if (pawn.IsInputPressed(CSInputs.WALK)) sp *= WALK_FACTOR;
    if (typeof pawn.IsDucking === "function" && pawn.IsDucking()) sp *= DUCK_FACTOR;
    return sp;
}
// the velocity to set so that the engine's own friction + acceleration (frame time dt) leave the player at T
function groundSet(T, w, wish, dt) {
    // its acceleration: all of it while T is below the run speed along the keys, none at or above it
    const a = T.x * w.x + T.y * w.y < wish ? ACCEL * wish * dt : 0;
    const u = V(T.x - a * w.x, T.y - a * w.y, 0);
    const sp = Math.hypot(u.x, u.y);
    if (sp < 0.1) return V(0, 0, 0);
    // friction: speed s -> s - max(s, STOP) * FRICTION * dt; the s before it
    const f = FRICTION * dt;
    const before = sp / (1 - f) >= STOP ? sp / (1 - f) : sp + STOP * f;
    return V((u.x * before) / sp, (u.y * before) / sp, 0);
}

// the direction the movement keys push (CS:GO's wishdir), or undefined
function wishDir(pawn) {
    const f = (pawn.IsInputPressed(CSInputs.FORWARD) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.BACK) ? 1 : 0);
    const s = (pawn.IsInputPressed(CSInputs.RIGHT) ? 1 : 0) - (pawn.IsInputPressed(CSInputs.LEFT) ? 1 : 0);
    if (!f && !s) return undefined;
    const y = (pawn.GetEyeAngles().yaw * Math.PI) / 180;
    const x = Math.cos(y) * f + Math.sin(y) * s, yy = Math.sin(y) * f - Math.cos(y) * s, l = Math.hypot(x, yy);
    return V(x / l, yy / l, 0);
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

// slot -> { k, nojump, ground: on the ground last tick }
const st = new Map();
function setSpeed(pawn, k, nojump, why) {
    const slot = slotOf(pawn);
    if (slot === undefined || !Number.isFinite(k)) return;
    if (k === 1) st.delete(slot);
    else st.set(slot, { k: Math.max(0, k), nojump, ground: pawn.GetGroundEntity() !== undefined, gain: st.has(slot) ? st.get(slot).gain : undefined });
    say(`s${slot} speed ${k}${nojump ? " (no jumping)" : ""} (${why})`);
    if (k !== 1) ensureThink(why);
}

// the set velocity, less what would run into a wall this tick (the engine's ClipVelocity). Without it the push into the wall came back
// out of the wall as speed away from it, and letting go of the keys left the player sliding off the wall at that speed.
// Traced a step (18 units) up, so steps and slopes still take the full speed and the engine steps up them.
let traceSaid = false;
function playerTrace(pawn, start, end) {
    if (!traceSaid) {
        traceSaid = true;
        say(`wall clip with ${typeof Instance.TracePlayer === "function" ? "TracePlayer" : "TraceBox"}`);
    }
    try {
        if (typeof Instance.TracePlayer === "function") return Instance.TracePlayer({ start, end, player: pawn });
        return Instance.TraceBox({ start, end, mins: V(-16, -16, 0), maxs: V(16, 16, 54), ignoreEntity: pawn, ignorePlayers: true });
    } catch (e) {
        return undefined;
    }
}
function clipWalls(pawn, s, dt) {
    const o = pawn.GetAbsOrigin();
    for (let i = 0; i < 3; i++) {
        const dx = s.x * dt, dy = s.y * dt, l = Math.hypot(dx, dy);
        if (l < 0.01) break;
        // from 4 units back along the move: a player pressed against the wall touches it already, and a trace starting in that
        // contact reports "started in solid" with no wall normal (in game the clip never happened and the push-off stayed)
        const bx = (-dx / l) * 4, by = (-dy / l) * 4;
        let r, back;
        for (back of [1, 0.25, 0]) {
            const start = V(o.x + bx * back, o.y + by * back, o.z + 18), end = V(o.x + dx, o.y + dy, o.z + 18);
            r = playerTrace(pawn, start, end);
            if (r && !r.startedInSolid) break;
        }
        if (!r || !r.didHit || r.startedInSolid) break;
        const n = r.normal;
        if (!n || Math.abs(n.z) > 0.7) break;                     // a floor or ceiling, not a wall
        const into = s.x * n.x + s.y * n.y;
        if (into >= 0) break;
        // only what would go past the wall this tick is taken off (the engine moves up to the wall, then clips the rest). Taking all of
        // it off as soon as the wall was within a tick's run stopped the player short of the wall, as if pushed back (2026-11-23 version)
        const used = back === undefined ? 1 : back;
        let d;                                                     // how far the player can still move along the move before the wall
        if (r.end) d = (r.end.x - o.x) * (dx / l) + (r.end.y - o.y) * (dy / l);
        else if (typeof r.fraction === "number") d = r.fraction * (l + 4 * used) - 4 * used;
        else d = 0;
        const gap = Math.max(0, d - 0.03125) * (-into / Math.hypot(s.x, s.y));   // that distance across to the wall (along its normal)
        const keep = Math.min(-into, gap / dt);                   // speed into the wall that still fits this tick
        if (keep >= -into - 0.01) break;
        s = V(s.x - n.x * (into + keep), s.y - n.y * (into + keep), s.z);
    }
    return s;
}

let last = 0;
function work() {
    const now = Instance.GetGameTime();
    // the tick length (1/64 s; the first tick after a pause has nothing to measure it against)
    const dt = last > 0 && now - last > 0 && now - last <= 0.1 ? Math.max(now - last, 1 / 128) : 1 / 64;
    last = now;
    for (const [slot, S] of [...st]) {
        const c = Instance.GetPlayerController(slot);
        const pawn = c ? c.GetPlayerPawn() : undefined;
        if (!pawn || !pawn.IsValid() || !pawn.IsAlive()) {
            st.delete(slot);
            continue;
        }
        if (pawn.IsNoclipping()) continue;
        S.meas = undefined;
        const v = pawn.GetAbsVelocity();
        const g = pawn.GetGroundEntity() !== undefined;
        const jumped = S.ground && !g && v.z > 100;
        const k = S.k;
        let set, note = "";
        if (k === 0) {
            set = V(0, 0, Math.min(v.z, 0));                     // frozen: no running, no jumping
        } else if (jumped && S.nojump) {
            set = V(v.x, v.y, 0);                                 // "Suppress jump": the jump's upward speed taken back
            note = " nojump";
        } else if (g) {
            const w = wishDir(pawn);
            if (w) {
                const wish = wishSpeed(pawn);
                // measured: how fast the last tick's set velocity really moved the player along it. CS2's movement takes off more than
                // the friction worked out in groundSet (running ran slow); the gain closes that gap, learning only on clear ground runs
                const o = pawn.GetAbsOrigin();
                if (S.prev && S.prev.ground && !S.prev.clipped && S.prev.T > 50) {
                    const along = ((o.x - S.prev.o.x) * S.prev.w.x + (o.y - S.prev.o.y) * S.prev.w.y) / dt;
                    S.meas = along;
                    if (along > 0.6 * S.prev.T && along < 1.4 * S.prev.T) {
                        S.gain = Math.min(1.5, Math.max(0.75, (S.gain || 1) * (1 + 0.5 * (S.prev.T / along - 1))));
                    }
                }
                const T = wish * k;
                const raw = groundSet(V(w.x * T * (S.gain || 1), w.y * T * (S.gain || 1), 0), w, wish, dt);
                const gs = clipWalls(pawn, raw, dt);
                const clipped = Math.abs(gs.x - raw.x) + Math.abs(gs.y - raw.y) > 0.5;
                set = V(gs.x, gs.y, v.z);
                S.next = { o, w, T, ground: true, clipped };
                if (clipped) note += " clipped";
            }
        }
        S.prev = S.next;
        S.next = undefined;
        if (set) pawn.Move({ velocity: set });
        if (LOG && slot === 0) {
            say(`t=${now.toFixed(3)} k=${k} ${g ? "G" : "A"} v=${f1(v.x)} ${f1(v.y)} ${f1(v.z)} |v|=${f1(Math.hypot(v.x, v.y))}` +
                (set ? ` set=${f1(set.x)} ${f1(set.y)} ${f1(set.z)} |set|=${f1(Math.hypot(set.x, set.y))}` : " (left to the engine)") +
                (S.meas !== undefined ? ` moved=${f1(S.meas)} gain=${(S.gain || 1).toFixed(3)}` : "") + note);
        }
        S.ground = g || (jumped && S.nojump);
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
let lastThink = -1;
function think() {
    lastThink = Instance.GetGameTime();
    Instance.SetNextThink(lastThink);
    if (st.size) safeWork();
    else last = 0;
}
// The think loop can stop without a word (a round restart, a script reload): a speed change restarts it if it hasn't run lately.
function ensureThink(why) {
    const now = Instance.GetGameTime();
    if (lastThink >= 0 && now - lastThink <= 0.25) return;
    say(`think ${lastThink < 0 ? "never ran" : "stopped at t=" + lastThink.toFixed(2)}: restarted (${why})`);
    last = 0;
    setThinkOnce(think);
    Instance.SetNextThink(now);
}

// ---- inputs (registered now: CS2 ignores script inputs registered after the map has loaded)
const num = (s) => Number(s.replace(/^n/, "-").replace("p", "."));
const names = new Set();
for (const s of COMMON_SPEEDS) {
    names.add("Speed_" + s);
    names.add("Speed_" + s + "_nojump");
}
for (const n of SPEED_INPUTS) names.add(n);
for (const name of names) {
    const m = /^Speed_(n?\d+(?:p\d+)?)(_nojump)?$/.exec(name);
    if (!m) continue;
    const k = num(m[1]), nojump = !!m[2];
    Instance.OnScriptInput(name, ({ activator }) => {
        const pawn = toPawn(activator);
        if (pawn && pawn.IsAlive()) setSpeed(pawn, k, nojump, name);
    });
}
Instance.RegisterCheatCommand("spd_set", (a) => {
    const w = String(a === undefined ? "" : a).trim().split(/\s+/);
    const k = Number(w[0]);
    if (!Number.isFinite(k)) return say("spd_set <speed> [nojump]");
    for (let slot = 0; slot < 64; slot++) {
        const c = Instance.GetPlayerController(slot);
        const pawn = c ? c.GetPlayerPawn() : undefined;
        if (pawn && pawn.IsValid() && pawn.IsAlive()) setSpeed(pawn, k, w[1] === "nojump", "spd_set");
    }
});
Instance.RegisterCheatCommand("spd_log", (a) => {
    LOG = String(a).trim() === "1" ? 1 : 0;
    const now = Instance.GetGameTime();
    say(`${VERSION}: log ${LOG}; ${st.size} player(s) with a speed; think last ran ${lastThink < 0 ? "never" : (now - lastThink).toFixed(2) + " s ago"}`);
});

function start() {
    last = 0;
    setThinkOnce(think);
    Instance.SetNextThink(Instance.GetGameTime());
}
Instance.OnActivate(start);
// every round start too (the other map scripts do the same): a think that was stopped is started again
Instance.OnRoundStart(() => {
    st.clear();
    start();
});
// In tools mode, saving the .js reloads the script and clears every callback (the think too); OnActivate doesn't fire again.
if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: start });
// A respawn puts the speed back to 1 (CBasePlayer::Spawn), as does a round restart (everyone respawns).
Instance.OnPlayerReset(({ player }) => {
    const slot = slotOf(player);
    if (slot !== undefined && st.delete(slot)) say(`s${slot} speed 1 (respawned)`);
});
Instance.OnPlayerDisconnect(({ playerSlot }) => st.delete(playerSlot));
