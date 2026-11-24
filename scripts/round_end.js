// round_end.js: CS:GO's game_round_end for CS2 (the entity is gone there).
//
// What CS:GO did (CS:GO server.dll, the game_round_end inputs, read from the binary 2026-11-22):
//   EndRound_TerroristsWin <delay>        if the round has not ended yet: T round wins + 1, then TerminateRound(delay, Terrorists_Win)
//   EndRound_CounterTerroristsWin <delay> if the round has not ended yet: CT round wins + 1, then TerminateRound(delay, CTs_Win)
//   EndRound_Draw <delay>                 if the round has not ended yet: TerminateRound(delay, Round_Draw) (no team score)
//   <delay> is the input's float parameter, 0 when there is none: the next round starts that many seconds later.
// CS2's GameRules.TerminateRound never adds to a team's score (point_script.d.ts), so the win is added first with AddTeamScore.
//
// SETUP: vmap_asset_slop_import.py (GAME_ROUND_END_TO_SCRIPT) makes each game_round_end a point_script running this file under the
// same name, and turns  <output> <it> EndRound_<X> <delay>  into  <output> <it> RunScriptInput RoundEnd_<T|CT|Draw>_<delay>  (delay
// with "p" for the decimal point: RoundEnd_T_0, RoundEnd_CT_2p5). CS2 only takes script inputs registered when the map loads: the
// converter writes the map's names between the markers below.
//
// Written against Valve's point_script.d.ts. Not run in CS2.

import { Instance, GameRules, CSRoundEndReason } from "cs_script/point_script";

const RE_INPUTS = /*ROUND_END_INPUTS_BEGIN*/[]/*ROUND_END_INPUTS_END*/;
const say = (s) => Instance.Msg(`[round_end] ${s}`);

// CS:GO checked its own "round has ended" flag; here: set when a round ends, cleared when one starts
let ended = false;
Instance.OnRoundEnd(() => {
    ended = true;
});
Instance.OnRoundStart(() => {
    ended = false;
});

const num = (s) => Number(String(s).replace(/^n/, "-").replace("p", "."));
for (const name of RE_INPUTS) {
    const m = /^RoundEnd_(T|CT|Draw)_(n?\d+(?:p\d+)?)$/.exec(name);
    if (!m) continue;
    const who = m[1], delay = Math.max(0, num(m[2]) || 0);
    Instance.OnScriptInput(name, () => {
        if (ended) return;
        if (who === "T") {
            GameRules.AddTeamScore(2, 1);
            GameRules.TerminateRound(CSRoundEndReason.TERRORISTS_WIN, delay);
        } else if (who === "CT") {
            GameRules.AddTeamScore(3, 1);
            GameRules.TerminateRound(CSRoundEndReason.CTS_WIN, delay);
        } else {
            GameRules.TerminateRound(CSRoundEndReason.DRAW, delay);
        }
        ended = true;
        say(`${name}: round ended (${who === "Draw" ? "draw" : who + " win"}), next round in ${delay} s`);
    });
}
