// maker_spawn.js: ForceSpawn of a parented env_entity_maker, done by script.
//
// What CS:GO did (Source SDK CEnvEntityMaker::SpawnEntity, CPointTemplate::CreateInstance): the template's entities were spawned at
// the maker's place and turn at that moment, each at its offset from the point_template turned with the maker. On a maker riding a
// moving parent (mg_mario_party's rotating ka cannon) that is wherever the parent has carried it.
// CS2: its own ForceSpawn put the ka cannon's Bullet Bill about a bill's length in front of the barrel, while the cannon's trigger
// (same parent) was where it should be.
//
// SETUP: vmap_asset_slop_import.py (MAKER_TEMPLATE_AT_ENTITY + MAKER_SPAWN_SCRIPT) moves the point_template onto its top entity (no
// offset left), moves the maker by that offset, and turns  <output> <maker> ForceSpawn  into  <output> maker_spawn RunScriptInput
// ms_<maker>. Only makers with no outputs of their own (their OnEntitySpawned would not fire from here).
// This script spawns the template at the maker's place and turn (PointTemplate.ForceSpawn(origin, angles)) and puts each spawned
// entity without a parent at the maker's place (the template's top entity, which had no offset).
//
// Written against Valve's point_script.d.ts. Not run in CS2.
// Console (sv_cheats 1): ms_log 0|1.

import { Instance } from "cs_script/point_script";

/** "maker name|template name" */
const PAIRS = /*MS_PAIRS_BEGIN*/[]/*MS_PAIRS_END*/;

let LOG = 0;
const say = (s) => Instance.Msg(`[maker_spawn] ${s}`);
const f0 = (v) => `${v.x.toFixed(0)} ${v.y.toFixed(0)} ${v.z.toFixed(0)}`;

function spawn(maker, template) {
    const makers = Instance.FindEntitiesByName(maker).filter((e) => e && e.IsValid());
    const templates = Instance.FindEntitiesByName(template).filter((e) => e && e.IsValid() && typeof e.ForceSpawn === "function");
    if (!makers.length || !templates.length) {
        say(`ms_${maker}: ${makers.length ? "" : "no maker"}${!makers.length && !templates.length ? ", " : ""}${templates.length ? "" : "no point_template " + template}`);
        return;
    }
    for (const mk of makers) {
        const o = mk.GetAbsOrigin(), a = mk.GetAbsAngles();
        for (const t of templates) {
            let got;
            try {
                got = t.ForceSpawn(o, a);
            } catch (e) {
                say(`ms_${maker}: ForceSpawn failed: ${e}`);
                continue;
            }
            for (const x of got || []) {
                if (x && x.IsValid() && !x.GetParent()) x.Teleport({ position: o });
            }
            if (LOG) {
                say(`ms_${maker}: ${template} -> ${(got || []).length} entit(ies) at ${f0(o)}, turned ${a.pitch.toFixed(0)} ${a.yaw.toFixed(0)} ${a.roll.toFixed(0)}`);
                for (const x of got || []) {
                    if (x && x.IsValid()) say(`   ${x.GetClassName()} ${x.GetEntityName()} at ${f0(x.GetAbsOrigin())}${x.GetParent() ? " (parented)" : ""}`);
                }
            }
        }
    }
}

for (const p of PAIRS) {
    const [mk, tp] = p.split("|");
    if (mk && tp) Instance.OnScriptInput(`ms_${mk}`, () => spawn(mk, tp));
}
Instance.OnActivate(() => say(`on: ${PAIRS.length} maker(s)`));

if (typeof Instance.RegisterCheatCommand === "function") {
    Instance.RegisterCheatCommand("ms_log", (a) => {
        LOG = Number(String(a === undefined ? "" : a).trim()) ? 1 : 0;
        say(`log ${LOG}`);
    });
}
