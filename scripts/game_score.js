// game_score.js: CS:GO's game_score for CS2 (CS2 has no game_score).
//
// The converter turns a game_score with "Team Points", or one sent AddScoreTerrorist / AddScoreCT, into a point_script with
// this script, keeping its targetname, so outputs and Kill / KillHierarchy still find it. Its inputs become RunScriptInput <name>
// at it, with the points in the name ("n" = minus):
//   ApplyScore, "Team Points" set   -> TeamScore_<points>   (TeamScore_25, TeamScore_n5)
//   AddScoreTerrorist / AddScoreCT  -> TeamT_<points> / TeamCT_<points>
// A plain game_score (ApplyScore added kills) is not converted: nothing in CS2 adds kills without a real kill.
// CS2 only takes script inputs registered when the map loads, so the names the maps use are listed in MAP_INPUTS below.
//
// What CS:GO did (CS:GO server.dll, read from the binary 2026-11-22):
//   ApplyScore: only a player activator counts (and only when its master, if any, is on). With "Team Points" (spawnflag 2) the points
//     go to the activator's team's total score (CCSTeam m_scoreTotal, the score shown for the team); otherwise to the activator's
//     KILLS (CBasePlayer m_iFrags), which without "Allow Negative" (spawnflag 1) never drop below 0.
//   AddScoreTerrorist / AddScoreCT: the points are added to that team's score (the match's round-win totals), whoever fired it.
// CS2: a team's score is GameRules.AddTeamScore.
// Written against Valve's point_script.d.ts and checked with a stand-in engine only. Not run in CS2.

import { Instance, GameRules } from "cs_script/point_script";

const MAP_INPUTS = /*SCORE_INPUTS_BEGIN*/[]/*SCORE_INPUTS_END*/;

/** TeamScore_25 -> ["TeamScore", 25], TeamCT_n5 -> ["TeamCT", -5]; anything else -> undefined */
function parse(name) {
    const m = /^(TeamScore|TeamT|TeamCT)_(n?)(\d+)$/.exec(name);
    return m ? [m[1], (m[2] ? -1 : 1) * Number(m[3])] : undefined;
}

/** the activator's player controller: a player pawn, or a controller; anything else (a prop, nothing) -> undefined */
function controllerOf(ent) {
    if (!ent || !ent.IsValid()) return undefined;
    if (typeof ent.GetPlayerPawn === "function") return ent;
    if (typeof ent.GetPlayerController === "function") return ent.GetPlayerController();
    return undefined;
}

for (const name of MAP_INPUTS) {
    const p = parse(name);
    if (!p) continue;
    const [kind, pts] = p;
    Instance.OnScriptInput(name, (d) => {
        if (kind === "TeamT") return GameRules.AddTeamScore(2, pts);
        if (kind === "TeamCT") return GameRules.AddTeamScore(3, pts);
        const c = controllerOf(d.activator);
        if (!c || !c.IsValid()) return;
        const team = c.GetTeamNumber();
        if (team === 2 || team === 3) GameRules.AddTeamScore(team, pts);
    });
}
