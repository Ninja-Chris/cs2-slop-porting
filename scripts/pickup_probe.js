// pickup_probe.js: diagnostic for "this weapon can't be picked up here" (mg_divine_multigames: molotovs that can't be picked up in some
// parts of the map). Not part of a converted map: add a point_script with this script to the map by hand, test, then remove it.
//
// Console (sv_cheats 1), as the first player on the server (slot 0):
//   pk_probe [radius]   every weapon lying within radius (default 250) of you: where it is, who owns it, and what a straight line from
//                       your eyes to it (and back) runs into. The use / pickup checks need a clear line; a hit names what is in the way.
//   pk_here             what is around you: whether you are inside something solid, and what is right under you
//
// Written against Valve's point_script.d.ts. Not run in CS2.

import { Instance } from "cs_script/point_script";

const say = (s) => Instance.Msg(`[pickup_probe] ${s}`);
const f = (v) => `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.z.toFixed(1)}`;
const who = (e) => (e && e.IsValid() ? `${e.GetClassName()} "${e.GetEntityName()}"` : "the world");
const CLASSES = ["weapon_molotov", "weapon_incgrenade", "weapon_hegrenade", "weapon_flashbang", "weapon_smokegrenade", "weapon_decoy",
    "weapon_knife", "weapon_glock", "weapon_hkp2000", "weapon_usp_silencer", "weapon_p250", "weapon_deagle", "weapon_ak47", "weapon_m4a1",
    "weapon_m4a1_silencer", "weapon_awp", "weapon_ssg08", "weapon_nova", "weapon_mp9", "weapon_mac10", "weapon_p90", "weapon_negev",
    "weapon_taser", "weapon_healthshot", "weapon_tec9", "weapon_fiveseven", "weapon_elite", "weapon_cz75a", "weapon_revolver"];

function me() {
    const c = Instance.GetPlayerController(0);
    const p = c ? c.GetPlayerPawn() : undefined;
    return p && p.IsValid() && p.IsAlive() ? p : undefined;
}

function line(a, b, ignore) {
    const t = Instance.TraceLine({ start: a, end: b, ignoreEntity: ignore });
    if (t.startedInSolid) return `starts inside something solid (${who(t.hitEntity)})`;
    if (!t.didHit) return "clear";
    return `hits ${who(t.hitEntity)} at ${f(t.end)} (${(t.fraction * 100).toFixed(0)}% of the way)`;
}

Instance.RegisterCheatCommand("pk_probe", (a) => {
    const pawn = me();
    if (!pawn) return say("no live player in slot 0");
    const r = Number(String(a === undefined ? "" : a).trim()) || 250;
    const eye = pawn.GetEyePosition();
    say(`you: eyes at ${f(eye)}, feet at ${f(pawn.GetAbsOrigin())}`);
    let n = 0;
    for (const cls of CLASSES) {
        for (const w of Instance.FindEntitiesByClass(cls)) {
            if (!w.IsValid()) continue;
            const o = w.GetAbsOrigin();
            const d = Math.hypot(o.x - eye.x, o.y - eye.y, o.z - eye.z);
            if (d > r) continue;
            n++;
            const owner = w.GetOwner();
            say(`${who(w)} at ${f(o)}, ${d.toFixed(0)} units away, owner ${owner ? who(owner) : "none"}`);
            say(`   eyes -> weapon: ${line(eye, o, [pawn, w])}`);
            say(`   weapon -> eyes: ${line(o, eye, [pawn, w])}`);
            const up = { x: o.x, y: o.y, z: o.z + 8 };
            say(`   8 units above it -> eyes: ${line(up, eye, [pawn, w])}`);
            const box = Instance.TraceBox({ start: o, end: o, mins: { x: -2, y: -2, z: -2 }, maxs: { x: 2, y: 2, z: 2 }, ignoreEntity: w });
            say(`   inside something solid: ${box.startedInSolid ? "YES (" + who(box.hitEntity) + ")" : "no"}`);
        }
    }
    if (!n) say(`no weapons within ${r} units`);
});

Instance.RegisterCheatCommand("pk_here", () => {
    const pawn = me();
    if (!pawn) return say("no live player in slot 0");
    const o = pawn.GetAbsOrigin();
    const t = Instance.TracePlayer({ start: o, end: o, player: pawn });
    say(`feet at ${f(o)}; player box inside something solid: ${t.startedInSolid ? "YES" : "no"}`);
    const dn = Instance.TraceLine({ start: { x: o.x, y: o.y, z: o.z + 4 }, end: { x: o.x, y: o.y, z: o.z - 64 }, ignoreEntity: pawn });
    say(`under you: ${dn.didHit ? who(dn.hitEntity) + " at " + f(dn.end) : "nothing within 64 units"}; ground entity: ${who(pawn.GetGroundEntity())}`);
    const eye = pawn.GetEyePosition();
    for (const [name, d] of [["forward 64", { x: 64, y: 0, z: 0 }], ["down 64", { x: 0, y: 0, z: -64 }], ["up 64", { x: 0, y: 0, z: 64 }]]) {
        say(`eyes ${name}: ${line(eye, { x: eye.x + d.x, y: eye.y + d.y, z: eye.z + d.z }, pawn)}`);
    }
});

say("loaded: pk_probe [radius], pk_here (sv_cheats 1)");
