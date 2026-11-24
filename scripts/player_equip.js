// player_equip.js: CS:GO's game_player_equip and player_weaponstrip for CS2, and map weapons keeping their names when picked up.
//
// What CS:GO did (CS:GO server.dll, read from the binary 2026-11-21):
//   CGamePlayerEquip::EquipPlayer(player, weapon = all of its own):
//     - "Strip All Weapons First" (2): the player loses everything first
//     - for each weapon (its keys: "weapon_x N"; N, from 0, is reserve ammo given with it, not a count of copies):
//         grenades and the healthshot: given only if the player may take one more (CS:GO's carry limits)
//         anything else: with "Only Strip Same Weapon Type" (4) the player's primary / secondary in that slot goes first; then it is given
//         and N rounds of its ammo are added
//     - TriggerForActivatedPlayer <weapon> gives only that weapon; TriggerForAllPlayers gives every player
//   Player spawn: every game_player_equip without "Use Only" (1) equips each player on a team, in map order (and the default loadout is
//   not given). Without "Strip First" players kept what they had, so survivors carried the last round's guns: the server plugin
//   game_player_equip.sp (Mitch) gave every equip without "Use Only" "Strip First", and that fixed behaviour is what this does (at spawn
//   always; when fired too unless the converter's EQUIP_PLUGIN_STRIP_ON_USE is off).
//   player_weaponstrip: Strip / StripWeaponsAndSuit take every weapon from the activator.
// What CS2 does (CS2 server.dll): at spawn it still strips with "Strip First" and skips the default loadout, but it never gives the
// spawn equips' weapons; a fired equip gives a gun the player already has again (they stack); player_weaponstrip is gone.
// Not read from CS:GO's binary: whether it refused a second copy of a gun the player already had (skink52's CS:GO tests: it did); this
// script gives none. Grenade limits are kept as "not one the player already has" (CS:GO allowed 2 flashbangs: a second is not given).
//
// Weapon names: CS2 drops a map weapon's targetname when it is picked up (skink52); CS:GO kept it. The spawn role remembers the named
// map weapons and puts the name back on pickup (also when CS2 hands over a new entity: the weapon of that class that disappeared nearest
// to the player takes it).
//
// SETUP: vmap_asset_slop_import.py (EQUIP_SCRIPT) makes each named game_player_equip / player_weaponstrip a point_script with this file
// under the same name (so Kill still removes it); outputs that fired it send RunScriptInput eq_<N> (activator), eq_<N>_all,
// eq_<N>_w<i> (TriggerForActivatedPlayer <weapon>) or eq_<N>_strip (a weaponstrip). Several of one name: the first is the point_script,
// the rest info_targets of that name, and one input runs them all in map order. Spawn equips keep a stand-in game_player_equip with no weapons (CS2 then still skips
// the default loadout), and a point_script player_equip_spawn runs this file in the spawn role. CS2 still gave the default knife, so the
// spawn role also empties CS2's default loadout cvars (mp_t_default_melee ...) while the map has spawn equips.
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.
//
// Console (sv_cheats 1): eq_log 0|1.

import { Instance, CSGearSlot } from "cs_script/point_script";
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


const ROLE = /*EQ_ROLE*/"equip"/*EQ_ROLE_END*/;          // "equip": one equip's inputs; "spawn": spawn equips and weapon names
/** map -> { e: [{ k, g: group (shared name), i: map order, n, sf, kind: "equip"|"strip", w: [[weapon, ammo]], p: [weapon] }], wn: [{ n, c, o }] } */
const DATA = /*EQ_DATA_BEGIN*/{}/*EQ_DATA_END*/;

let LOG = 0;
const say = (s) => Instance.Msg(`[player_equip] ${s}`);
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
    return keys.length === 1 ? DATA[keys[0]] : { e: [], wn: [] };
}
const MD = mapData();
const EQUIPS = MD.e || [];
const NAMED = MD.wn || [];

const GRENADES = new Set(["weapon_hegrenade", "weapon_flashbang", "weapon_smokegrenade", "weapon_molotov", "weapon_incgrenade", "weapon_decoy",
    "weapon_tagrenade", "weapon_healthshot"]);
const PISTOLS = new Set(["weapon_glock", "weapon_hkp2000", "weapon_usp_silencer", "weapon_p250", "weapon_fiveseven", "weapon_tec9",
    "weapon_cz75a", "weapon_deagle", "weapon_revolver", "weapon_elite"]);
const isKnife = (w) => w.startsWith("weapon_knife") || w === "weapon_bayonet";

function toPawn(ent) {
    if (!ent || !ent.IsValid()) return undefined;
    if (typeof ent.GetPlayerController === "function") return ent;
    if (typeof ent.GetPlayerPawn === "function") return ent.GetPlayerPawn();
    return undefined;
}
const alivePawn = (p) => p && p.IsValid() && p.IsAlive();

function addAmmo(pawn, name, ammo) {
    if (!(ammo > 0)) return;
    const w = pawn.FindWeapon(name);
    if (!w) return;
    const max = w.GetData().GetMaxReserveAmmo();
    w.SetReserveAmmo(Math.min(w.GetReserveAmmo() + ammo, max > 0 ? max : w.GetReserveAmmo() + ammo));
}

/** CGamePlayerEquip::EquipPlayer, with the plugin's strip for equips CS:GO ran at spawn (the converter set flag 2 for them) */
function equip(E, pawn, only, atSpawn) {
    if (!alivePawn(pawn)) return;
    if (E.kind === "strip") {
        pawn.DestroyWeapons();
        if (LOG) say(`${E.n || "strip"}: stripped`);
        return;
    }
    // at spawn always (the game_player_equip.sp plugin). The weapons DestroyWeapons removes still count as held until the next tick,
    // so after a strip nothing is "already held" (a strip-first knife equip gave no knife: FindWeaponBySlot still found the old one)
    const stripped = !!(E.sf & 2 || atSpawn);
    if (stripped) pawn.DestroyWeapons();
    const list = only ? [[only, E.w.length ? E.w[0][1] : 0]] : E.w;
    const got = [];
    for (const [name, ammo] of list) {
        if (name === "item_kevlar" || name === "item_assaultsuit" || name === "item_heavyassaultsuit") {
            if (pawn.GetArmor() < 100) pawn.SetArmor(100);
            if (name !== "item_kevlar") pawn.SetHasHelmet(true);
            got.push(name);
            continue;
        }
        if (name.startsWith("item_")) continue;                     // item_defuser / item_cutters: no script API for them
        if (GRENADES.has(name)) {
            if (stripped || !pawn.FindWeapon(name)) {
                pawn.GiveNamedItem(name);
                got.push(name);
            }
            continue;
        }
        if (isKnife(name)) {
            if (stripped || !pawn.FindWeaponBySlot(CSGearSlot.KNIFE)) {
                pawn.GiveNamedItem(name);
                got.push(name);
            }
            continue;
        }
        if (E.sf & 4 && !stripped) {                                             // Only Strip Same Weapon Type: the primary / secondary in its slot goes
            const slot = PISTOLS.has(name) ? CSGearSlot.PISTOL : name === "weapon_taser" ? CSGearSlot.INVALID : CSGearSlot.RIFLE;
            const cur = slot === CSGearSlot.INVALID ? undefined : pawn.FindWeaponBySlot(slot);
            if (cur && cur.GetData().GetName() !== name) pawn.DestroyWeapon(cur);
        }
        if (stripped || !pawn.FindWeapon(name)) {
            pawn.GiveNamedItem(name);
            got.push(name);
        }
        addAmmo(pawn, name, ammo);
    }
    if (LOG) say(`${E.n || "equip"} -> ${got.join(", ") || "nothing new"}`);
}

/** the spawn equips one after another, worked out first and applied once. Each strips first at spawn (the plugin), so CS:GO ended
 *  with the last one's weapons (plus any armour). Applied one by one in one tick it went wrong: DestroyWeapons doesn't remove a weapon
 *  given in the same tick, and the weapons it removes still count as held until the next tick, so mg_mario_party's thwomp_knifes knife
 *  survived the strip-all equip that runs after it (in game: "thwomp_knifes -> weapon_knife", then the strip, and a knife at spawn). */
function spawnEquip(pawn, list) {
    if (!alivePawn(pawn) || !list.length) return;
    let plan = [];
    let armor = 0, helmet = false;
    for (const E of list) {
        plan = [];                                                  // every spawn equip strips first (E.kind "strip" too)
        if (E.kind === "strip") continue;
        for (const [name, ammo] of E.w) {
            if (name === "item_kevlar" || name === "item_assaultsuit" || name === "item_heavyassaultsuit") {
                armor = 100;
                if (name !== "item_kevlar") helmet = true;
                continue;
            }
            if (name.startsWith("item_")) continue;
            if (isKnife(name) ? plan.some(([n]) => isKnife(n)) : plan.some(([n]) => n === name)) continue;
            plan.push([name, ammo]);
        }
    }
    pawn.DestroyWeapons();
    if (armor && pawn.GetArmor() < armor) pawn.SetArmor(armor);
    if (helmet) pawn.SetHasHelmet(true);
    for (const [name, ammo] of plan) {
        pawn.GiveNamedItem(name);
        if (!GRENADES.has(name) && !isKnife(name)) addAmmo(pawn, name, ammo);
    }
    if (LOG) say(`spawn (${list.map((E) => E.n || E.kind).join(", ")}) -> ${plan.map(([n]) => n).join(", ") || "nothing"}${armor ? " + armour" : ""}`);
}

function allPawns() {
    return Instance.FindEntitiesByClass("player").map(toPawn).filter(alivePawn);
}

if (ROLE === "equip") {
    // an input reaches every equip / weaponstrip of the name (its group, in map order), as CS:GO's outputs did
    const groups = new Map();
    for (const E of EQUIPS) {
        const g = E.g || E.k;
        if (!groups.has(g)) groups.set(g, []);
        groups.get(g).push(E);
    }
    for (const [g, list] of groups) {
        const eq = list.filter((E) => E.kind === "equip"), st = list.filter((E) => E.kind === "strip");
        const params = list.reduce((acc, E) => acc.concat(E.p || []), []);
        Instance.OnScriptInput(`eq_${g}`, ({ activator }) => {
            const p = toPawn(activator);
            for (const E of eq) equip(E, p);
        });
        Instance.OnScriptInput(`eq_${g}_strip`, ({ activator }) => {
            const p = toPawn(activator);
            for (const E of st) equip(E, p);
        });
        Instance.OnScriptInput(`eq_${g}_all`, () => {
            for (const p of allPawns()) for (const E of eq) equip(E, p);
        });
        params.forEach((w, i) => Instance.OnScriptInput(`eq_${g}_w${i}`, ({ activator }) => {
            const p = toPawn(activator);
            for (const E of eq) equip(E, p, w);
        }));
    }
} else {
    // ---- spawn equips: one tick after a player's reset (CS2's own spawn strip and loadout come first), in map order
    const SPAWN = EQUIPS.filter((E) => E.kind === "equip" && E.sp);
    const due = new Map();               // pawn -> game time to equip at
    // ---- named map weapons
    let named = [];                       // { n, c, ent, pos }
    const dist2 = (a, b) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
    function scanNamed() {
        const have = new Set(named.map((t) => t.ent));
        for (const d of NAMED) {
            for (const ent of Instance.FindEntitiesByName(d.n)) {
                if (!ent || !ent.IsValid() || have.has(ent) || ent.GetClassName() !== d.c) continue;
                named.push({ n: d.n, c: d.c, ent, pos: ent.GetAbsOrigin() });
                have.add(ent);
            }
        }
    }
    function track() {
        for (const t of named) if (t.ent && t.ent.IsValid()) t.pos = t.ent.GetAbsOrigin();
    }
    Instance.OnWeaponPickup(({ weapon }) => {
        if (!weapon || !weapon.IsValid()) return;
        let t = named.find((x) => x.ent === weapon);
        if (!t) {                         // a new entity for a named weapon that just went: the nearest one of that class
            const owner = weapon.GetOwner();
            const at = owner && owner.IsValid() ? owner.GetAbsOrigin() : weapon.GetAbsOrigin();
            let best, bd = 200 * 200;
            for (const x of named) {
                if (x.c !== weapon.GetClassName() || (x.ent && x.ent.IsValid())) continue;
                const d = dist2(x.pos, at);
                if (d < bd) {
                    bd = d;
                    best = x;
                }
            }
            t = best;
            if (t) t.ent = weapon;
        }
        if (t && weapon.GetEntityName() !== t.n) {
            weapon.SetEntityName(t.n);
            if (LOG) say(`picked up ${t.c}: name ${t.n} kept`);
        }
    });
    Instance.OnPlayerReset(({ player }) => {
        if (SPAWN.length && player && player.IsValid()) due.set(player, Instance.GetGameTime());
    });
    let nextScan = 0;
    function think() {
        const now = Instance.GetGameTime();
        Instance.SetNextThink(now);
        for (const [pawn, t] of [...due]) {
            if (now <= t) continue;
            due.delete(pawn);
            if (!alivePawn(pawn) || pawn.GetTeamNumber() <= 1) continue;
            // a named spawn equip that was killed no longer equips (CS:GO looked them up each spawn)
            spawnEquip(pawn, SPAWN.filter((E) => !E.n || Instance.FindEntitiesByName(E.n).some((x) => x && x.IsValid())));
        }
        if (now >= nextScan) {
            nextScan = now + 1.0;
            if (NAMED.length) scanNamed();
        }
        if (NAMED.length) track();
    }
    // CS:GO gave no default loadout to a map with spawn equips, knife included. CS2 still gives the default knife with the stand-in
    // equip in place (reported in game on mg_mario_party; skink52's port clears these too), so the map's spawn equips clear CS2's
    // default loadout cvars. They are server settings: they stay so until the next map sets them (a server config, gamemode cfg).
    const NO_DEFAULT_LOADOUT = ["mp_t_default_melee", "mp_ct_default_melee", "mp_t_default_secondary", "mp_ct_default_secondary",
                                "mp_t_default_primary", "mp_ct_default_primary", "mp_t_default_grenades", "mp_ct_default_grenades"];
    function noDefaultLoadout() {
        if (!SPAWN.length || typeof Instance.ServerCommand !== "function") return;
        for (const cvar of NO_DEFAULT_LOADOUT) Instance.ServerCommand(`${cvar} ""`);
    }
    function start(why) {
        noDefaultLoadout();
        named = [];
        scanNamed();
        setThinkOnce(think);
        Instance.SetNextThink(Instance.GetGameTime());
        say(`${why}: ${SPAWN.length} spawn equip(s), ${named.length} named weapon(s) watched`);
    }
    Instance.OnActivate(() => start("on"));
    Instance.OnRoundStart(() => start("round start"));
    if (typeof Instance.OnScriptReload === "function") Instance.OnScriptReload({ after: () => start("reloaded") });
    if (typeof Instance.RegisterCheatCommand === "function") {
        Instance.RegisterCheatCommand("eq_log", (a) => {
            LOG = Number(String(a === undefined ? "" : a).trim()) ? 1 : 0;
            say(`log ${LOG}`);
        });
    }
}
