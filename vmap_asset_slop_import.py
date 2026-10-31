#!/usr/bin/env python3
# vmap_convert_merged.py
#
# Source1 -> CS2 map port helper. Run it on the .vmap(s) Valve's importer produced (VMAP_PATH). One pass does three things:
#
#   A. ASSETS: harvest every asset the map references and bring it into the CS2 addon folder
#   B. REPAIRS: fix what the importer got wrong inside the .vmap itself (in place; Hammer must be closed)
#   C. EXTRAS: generate the pieces the importer cannot (soundevents, steam particles, stub materials)
#   ... and write logs (<script dir>\logs\<prefix>_*.txt) saying what it did and what it could not do.
# It is meant to be re-run: results are rebuilt from the vmap + Source1 files each time, with no edit tracking in the script.
#
# A. ASSETS
#   1. Already in the CS2 install -> skipped. Checked against the *_dir.vpk indexes under CS2_GAME_DIR
#      (USE_CS2_VPK; read directly, cached in the logs folder) and any extracted CS2_VERIFY_FOLDER.
#      CS2 vmats that ship as stubs (error shader, no textures) are re-ported from Source1 (<prefix>_cs2_stub_materials.txt).
#   2. Otherwise converted / copied from the Source1 roots (map folder first, then the stock roots):
#        .vmat/.vmt   -> VMT parsed (patch/include/insert/replace) and written as VMAT, textures decoded from VTF to
#                        .tga / .png atlas. Additive: moved to csgo_unlitgeneric (F_BLEND_MODE 4);
#                        LIT_ADDITIVE_VIA_TRANSLUCENT keeps lightmapped ones lit (F_TRANSLUCENT + F_ADDITIVE_BLEND, not reliable). Alpha test and translucent are exclusive on lit shaders.
#                        $ssbump bump maps are converted to real normal maps;
#                        $nocull -> F_RENDER_BACKFACES; translucent / alpha-tested materials get a _trans.tga alpha mask
#        .tga/.png/.jpg raw images -> copied
#        .vsnd / .wav / .mp3 -> the Source1 sound is copied to sounds/ (CS2 compiles it); non-PCM wavs and looping mp3
#                        are converted to wav (pip install miniaudio for mp3)
#        .vmdl        -> the Source1 .mdl is imported with cs_mdl_import.exe (IMPORT_MODELS); its materials are converted too.
#                        Models that cannot be imported: <prefix>_models_to_convert.txt / _models_skipped.txt
#        .vpcf        -> Source1 .pcf particles cannot be converted (env_steam is handled in C)
#   3. Anything left: <prefix>_missing.txt; <prefix>_missing_reasons.txt says why, what referenced it, and whether a file
#      with the same name exists elsewhere in the Source1 folders / CS2.
#
# Every pass that writes a vmap first copies it to the backups folder (BACKUP_DIR): <map>.original.vmap (the first copy, always kept)
# and <map>.<time>.before_<pass>.vmap (newest BACKUP_KEEP kept). The records of what was done once (water volumes, lighting, radar files)
# live there too: delete one to have that step redone.
#
# B. REPAIRS INSIDE THE VMAP (patched in place;
#    both need the decompiled Source1 .vmf, auto-found under <Source1 root>/maps or set via STATIC_OVERLAY_VMF)
#   - static overlays (decals): the importer writes a tiny placeholder quad; rebuilt to the Source1 texture size x $decalscale,
#     laid on the surface found in the .vmf (origin, angles, mirrored UVs). <prefix>_static_overlay_fix.txt
#   - static overlays that sit on brush ENTITIES (CS2 overlays do not project onto entities): replaced by a translucent quad parented to the
#     entity (<prefix>_overlay_entity_quads.txt; OVERLAY_ENTITY_QUADS)
#   - prop_physics / prop_physics_override are renamed push_<mass>_<old name> for push_mass.js (mass = Source1 .phy mass x massScale; PROP_MASS_RENAME),
#     and every output, input parameter, keyvalue and point_template slot that named the old prop is updated (
#     <prefix>_prop_mass_rename.txt). Outputs are duplicated when the old name is also used by other entities or several masses now share it.
#   - <prefix>_missing_entities.txt (REPORT_MISSING_ENTITIES): .vmf entities that are not in the .vmap (deleted, dropped by the importer, or moved). Report only.
#   - <prefix>_dropped_io.txt (REPORT_DROPPED_IO): entity outputs in the .vmf that are not in the .vmap, with whether their target still exists. Report only.
#   - brush face texture mapping: the importer reduces shifts to 0..16 and (without the textures) mangles scales; the original
#     axes / scale / shift are copied back from the matching .vmf brush side and the face texture coordinates are rewritten
#     from the Source1 texture size. Faces on a displacement take the displacement's base-face mapping; geometry that is a copy of a .vmf brush
#     moved elsewhere in Hammer is recognised by its size and takes that brush's mapping, shift corrected for the move (texture lock).
#     <prefix>_absurd_scales.txt lists every face whose scale is still out of range and what the .vmf says about its material.
#     <prefix>_face_scale_fix.txt (faces it could not match are left as imported)
#
# C. EXTRAS
#   - soundevents/soundevents_addon.vsndevts: one event per converted sound (named like the path, e.g. mysounds.mh_blade_loop1;
#     set that name on the entity in Hammer, see <prefix>_sound_report.txt / _soundevent_map.txt), distance curves from the
#     ambient_generic radius, looping base event, plus ported Source1 soundscript names
#   - env_steam -> particles/steam/*.vpcf named after the Source1 keyvalues (see <prefix>_generated_particles.txt for the
#     effect_name to use). Type 1 (heat wave) is a refracting sprite; STEAM_HEATWAVE_TEXTURE = "source1vmt:sprites/heatwave"
#     uses the original material's normal map and refract amount
#   - trigger materials (TRIGGER_MATERIAL_FIX): trigger volumes storing a non-trigger material (Hammer hides it) are set to toolstrigger.
#   - faces culled under water brushes (CULLED_FACE_FIX): rebuilt from the .vmf.
#   - importer duplicates at the origin (ORIGIN_DUPLICATE_FIX): world copies of brush entities at their local coordinates are deleted.
#   - "(null)" output parameters (NULL_PARAM_FIX) -> empty, as in the Source1 map.
#   - game_player_equip (EQUIP_CHECK / EQUIP_FIX_INPUTS): CS:GO -> CS2 differences reported; "Use" inputs on equips -> TriggerForActivatedPlayer.
#   - brush entity render settings (BRUSH_RENDER_FIX): Cast Shadows / Render Mode / Disable Receiving Shadows / Disable Mesh Merging on
#     BRUSH_RENDER_CLASSES (every run, or once per entity with BRUSH_RENDER_ONCE), and every brush entity's meshes given the entity's Cast Shadows value.
#   - water (WATER_VOLUMES): Source1 water the importer left as world geometry -> func_water; func_water_analog -> func_water; deep water gets
#     an underwater post_processing_volume (lighting/postprocessing/postprocess_filmic_underwater.vpost, settings copied from the master volume). <prefix>_water.txt.
#     Water materials themselves are not converted (the log says which ones CS2 ships). Underwater lights: the lighting rebuild below.
#   - lighting (LIGHTING_REBUILD, from rebuild_lighting.py): every light / light probe volume / vis hint is rebuilt from the .vmf with the
#     real brightness and colour, then light probes, Static -> Stationary lights, vis hints and large-face splitting. Every run by default
#     (LIGHTING_REBUILD "once" keeps lights you tune in Hammer). <prefix>_lighting.txt.
#   - entity seams (ENTITY_SEAM_REPORT): brush-entity faces that continue or overlap a world surface (they light as a separate patch). Report only.
#   - radar (RADAR): drawn from the map geometry (method from make_radar.py) -> panorama/images/overheadmaps/<map>_radar_psd.png/.vtex and
#     resource/overviews/<map>.txt in the addon's game folder; <prefix>_radar.txt, preview in the logs folder. Files you edit are never redrawn.
# Not done: water materials, Source1 .pcf particles, editing entity keyvalues in the vmap (sound event names are set by hand in Hammer).
#
# Stock Source 2 copies (the old COPYABLE_ROOTS) are no longer copied from, so a stale
# pre-converted .vmat can no longer win over the Source1 VMT.

import collections
import math
import pickle
import os
import random
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from PIL import Image

# ======================================================================== CONFIG
VMAP_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\content\csgo_addons\mg_tomgreens_allinonev2\maps"
EXPORT_DIR = None                      # None -> addon folder = parent of the "maps" folder
CS2_VERIFY_FOLDER = r"D:\Counter-Strike\Assets\CS2\cs2"
USE_CS2_VPK = True                     # read the CS2 install's *_dir.vpk indexes directly (always up to date, nothing to extract)
CS2_GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game"
CS2_VPK_FOLDERS = ["csgo", "csgo_core", "core"]      # folders under CS2_GAME_DIR whose *_dir.vpk files are indexed
CS2_VPK_SKIP_PREFIXES = ("shaders",)   # *_dir.vpk files starting with these are ignored (shader caches, not content)
CS2_EXTRA_VERIFY_FOLDERS = []              # more folders laid out like CS2_VERIFY_FOLDER (e.g. extracted particles\ or sounds\). Extracted folders are used
                                       # in addition to the VPK index (and alone if USE_CS2_VPK is False or no VPK is found)

# Source1 roots, searched in this order. Each root holds materials\, models\, sound\ ...
SOURCE1_ROOT = r"C:\map_sources\mg_tomgreens_allinone_v2_nc1_d"            # the map's own content
SOURCE1_STOCK_ROOTS = [
    rf"D:\Counter-Strike\Assets\Source1\{name}" for name in ("csgo", "css", "hl2", "hl2e2", "custom")
]
SOURCE1_ROOTS = [SOURCE1_ROOT] + SOURCE1_STOCK_ROOTS

WRITE_SOUNDEVENTS = True               # write soundevents/soundevents_addon.vsndevts for converted sounds
LOOP_EVENT_BASE = "amb.looping.stereo.base"   # base (from Valve's template soundevents file) for looping events; looping events then carry only base+volume+files+curves, no type/mixgroup; "" = none
SOUND_RANGE_CURVES = True              # write distance_volume_mapping_curve from each ambient_generic's radius (Source1 default 1250); "Play everywhere" = flat curve
SOUND_DEFAULT_RADIUS = 1250.0
SOUND_RANGE_SCALE = 1.0                # multiply every radius (not "play everywhere") when writing curves; raise if CS2 fades out sooner than Source did
STATIC_OVERLAY_VMF = ""                # "" = auto-find the closest-named .vmf in <Source1 root>/maps. Otherwise the path to the decompiled Source1 .vmf: lets the script rebuild the importer's placeholder overlay quads (size from the texture, orientation from the brush face under each infodecal). Writes a patched COPY of the vmap into the logs folder; "" = off
STATIC_OVERLAY_PATCH_IN_PLACE = True   # True: patch the overlays inside the vmap itself (Hammer must be closed); the untouched file is first copied to the backups folder as <name>.<time>.before_overlayfix. False: only list, per overlay, the origin/angles/size it should have (static_overlay_fix log) and change nothing
STATIC_OVERLAY_ONLY_PLACEHOLDERS = True   # patching only touches overlays that are still the importer's untouched 2x2 quad (angles 0, scale 1). Any overlay you changed by hand (even just the scale) is skipped. False: patch every overlay that differs from the .vmf target
STATIC_OVERLAY_PROJECTION_FAR = None   # e.g. 8.0: every static overlay's projection depth is set to this (also on overlays that are already patched / hand-edited; the importer sometimes writes 64, which shows as a 2x2x64 box and the overlay is not visible). The overlay origin sits NORMAL_OFFSET above the surface, so only far - offset reaches into it. None = leave the depth alone
FACE_SCALE_FIX = True   # repair brush face texture scale/shift the importer got wrong (it does not read the Source1 textures: scale comes out as 0.25/256 etc.). Needs the .vmf (same auto-find as the overlays); patched in place, the vmap is backed up to the backups folder first
LIT_ADDITIVE_VIA_TRANSLUCENT = False  # True: additive materials stay on csgo_lightmappedgeneric with F_TRANSLUCENT 1 + F_ADDITIVE_BLEND 1 (the flags exist in a saved lightmapped vmat, but a black additive material such as Goldeneye glass04 then shows as opaque black in Hammer). False (default): move them to csgo_unlitgeneric (F_BLEND_MODE 4, confirmed additive and see-through). Vertexlit materials are always moved to unlit (no confirmed additive flag there). Alpha test and translucent are exclusive on the lit shaders: alpha test wins unless the material is additive
MISSING_SUGGESTIONS = True   # missing_reasons log: also say what referenced each missing asset and whether a file with the same name exists elsewhere in the Source1 folders / CS2 (one extra scan of the folders when something is missing)
MATCH_MOVED_LEVELS = False      # True: faces with no .vmf geometry are re-tested at offsets that several materials agree on (a floor/level moved as a whole in Hammer)
FACE_SCALE_BAD_RATIO = (0.75, 1.5)   # a face counts as broken when vmap scale / .vmf scale is outside this range on either axis (axis normalisation alone gives up to ~1.3)
STATIC_OVERLAY_TEX_FLIP = "v"          # how the texture is laid on the quad: "" (importer's UVs), "u", "v" or "uv" (mirror the image left-right / up-down / both). Absolute values are written, so reruns do not flip twice
STATIC_OVERLAY_NORMAL_OFFSET = 4.0     # the importer puts each overlay origin this far above the surface (+4 in Z on the floor decals)
OVERLAY_ENTITY_QUADS = True   # CS2 static overlays do not project onto brush entities (func_breakable, doors, ...). True: an overlay whose projection reaches a face of exactly one entity (and no world brush face) is replaced by a translucent quad parented to that entity (CMapMesh + <material>_quad.vmat written to the addon), so it shows and moves with it. Patched in place (Hammer must be closed); the vmap is backed up to the backups folder first. <prefix>_overlay_entity_quads.txt lists every overlay it converted or left, and why. Overlays that also touch world brushes, touch two entities or lie on slanted faces stay overlays
OVERLAY_QUAD_OFFSET = 0.25    # the quad is laid this far in front of the entity face (toward the viewer), to avoid z-fighting
PROP_MASS_RENAME = True       # rename every prop_physics / prop_physics_override to push_<mass>_<old name> (the format push_mass.js reads), mass = the Source1 model's .phy mass x massScale, and update every reference (outputs, keyvalues, point_template slots). Needs the models' .phy files in the Source1 folders. Patched in place (Hammer must be closed); the vmap is backed up to the backups folder first. <prefix>_prop_mass_rename.txt lists every change
PROP_MASS_PREFIX = "push_"    # must stay "push_": push_mass.js reads ^push_<number>_
PROP_MASS_CLASSES = ("prop_physics", "prop_physics_override")
PROP_MASS_KEEP_EXISTING = True   # a prop whose name already starts with push_<number>_ keeps it (hand-set mass; reruns do not rename twice)
PROP_MASS_SKIP_IMMOVABLE = True        # do not rename props that can never move: Debris flag (4), or Motion Disabled (8) when nothing in the map/scripts can EnableMotion them. Anything uncertain IS renamed and logged UNCERTAIN
PROP_MASS_SCRIPT_DIRS = None           # folders with the map's .js/.ts scripts, searched for EnableMotion calls. None = <addon>/scripts found above the .vmap
PROP_MASS_IN_PLACE = True        # False: only write the log
PROP_MASS_NAME_PARAM_INPUTS = {"setparent", "settarget", "setfilter", "setmeasuretarget", "setowner", "forcespawnatentityorigin", "setattachment", "setparentattachment", "setparentattachmentmaintainoffset", "teleportentity"}   # inputs whose parameter is an entity name
TRIGGER_MATERIAL_FIX = True   # trigger volumes (trigger_*, func_buyzone, func_bomb_target, func_hostage_rescue) whose faces store another material are set to toolstrigger. Hammer always DRAWS these entities with toolstrigger, so a trigger made from a copied floor brush still carries the floor material unseen (it only shows if the entity is turned into a func_brush). Logged in <prefix>_trigger_materials.txt; the vmap is backed up first
TRIGGER_CLASSES = ("trigger_", "func_buyzone", "func_bomb_target", "func_hostage_rescue")   # class names, or prefixes ending in _
TRIGGER_MATERIAL = "materials/tools/toolstrigger.vmat"
HEAL_TRIGGER_REPORT = True    # list trigger_hurt with negative damage: CS:GO healed players gradually with them, CS2 does not. Report only (the map is not changed): a gradual heal needs a cs_script. <prefix>_heal_triggers.txt
TOOL_MATERIAL_PATH_FIX = True # a tool material referenced outside materials/tools/ (e.g. materials/toolslightmapres.vmat), which neither CS2 nor the addon has, is pointed at CS2's materials/tools/<same name> when that exists. <prefix>_tool_material_paths.txt
CULLED_FACE_FIX = True        # world brush faces Valve's importer dropped because a WATER brush lies on them (a sea floor under a water brush whose every side is water, so the importer treats the water as solid and culls the floor) are rebuilt from the .vmf: the brush comes back as a box with its own material and texture mapping on that side (axis-aligned box brushes only; others are listed). <prefix>_culled_faces.txt, vmap backed up first
ORIGIN_DUPLICATE_FIX = True   # delete the copies of brush-entity geometry Valve's importer leaves in the WORLD around the map origin (each brush entity's brushes again, at its own local coordinates). A world mesh is removed only when every vertex lies on the surface of the .vmf's brush entities moved to the origin and it is not world geometry of the .vmf; logged in <prefix>_origin_duplicates.txt, vmap backed up first. Needs the .vmf + numpy
DISPLAY_BRUSH_TO_WORLD = True # a func_brush / func_illusionary that only ever shows its textures (never solid, no name, parent, outputs or render changes, starts enabled) and lies flush in a world surface of the same material (a ceiling / wall panel) becomes world meshes with Physics Type None: still non-solid, but baked into the same lightmap as the surface around it instead of probe-lit (a probe-lit brush in a wall or ceiling hole picks up the darkness behind it). Brushes not flush with the world (a non-solid floor) are left alone. <prefix>_display_brushes.txt, vmap backed up first
MIRRORED_TEXTURE_FIX = True   # a small patch of faces whose texture runs turned 180 degrees against the larger surface of the same material it lies in shades differently in CS2 when the material has a normal map (lit from the wrong side). The patch is turned to the surrounding surface's texture direction and alignment. Only normal-mapped materials; mirrored patches and materials that can't be checked are only listed; 90-degree turns are left alone. <prefix>_mirrored_textures.txt, vmap backed up first
NULL_PARAM_FIX = True         # outputs whose parameter is "(null)" (the importer writes it where the Source1 output had no parameter) get an empty parameter, which is what they had in CS:GO. Logged in <prefix>_null_params.txt; the vmap is backed up first
EQUIP_CHECK = True            # check every game_player_equip for what changed from CS:GO to CS2 (item_* in a slot crashes the server, old weapon names, legacy weapon_x keys, equips CS:GO ran on spawn, nothing firing them, guns stacking) -> <prefix>_player_equips.txt
EQUIP_SPAWN_STRIP = True      # a game_player_equip without "Use Only" replaced the spawn loadout in CS:GO (players got only what it lists; an empty one stripped everything, knife included). CS2 only strips when "Strip First" (2) is set (tested: knives and grenades go at spawn and round restart), so such equips get flag 2 to behave as in CS:GO. Logged in <prefix>_player_equips.txt, vmap backed up first
EQUIP_FIX_INPUTS = True       # outputs that fire "Use" at a game_player_equip -> TriggerForActivatedPlayer (CS2's equip input; gives the same player the weapons), and their target spelled exactly like the equip's name (CS:GO matched names ignoring case). Same result either way if Use still works, so safe to leave on. The vmap is backed up first
BRUSH_RENDER_FIX = True       # CS2 lights most brush entities per object from light probes, not lightmaps, so with the imported settings they come out darker / blotchier than the world around them. The settings below are applied on every run (see BRUSH_RENDER_ONCE). <prefix>_brush_render.txt; the vmap is backed up first
BRUSH_RENDER_ONCE = False     # True: each entity is only set once (remembered by Hammer reference ID in backups\<map>.vmap.brush_render_done.txt), so hand edits to single entities are kept
BRUSH_RENDER_CLASSES = ("func_breakable",)   # entity classes that get the render settings below (add e.g. "func_door", "func_brush" once tested)
BRUSH_CAST_SHADOWS = "only realtime"   # Cast Shadows, on the entity and its meshes: "enabled", "no shadows", "only realtime", "only baked"; None = leave
BRUSH_RENDER_MODE = "texture"          # Render Mode: "normal", "texture" (or a raw kRender... value); None = leave
BRUSH_DISABLE_RECEIVING_SHADOWS = True # Disable Receiving Shadows (otherwise a shadow over part of the entity darkens all of it); None = leave
BRUSH_DISABLE_MESH_MERGING = True      # Disable Mesh Merging on the entity's meshes; None = leave
BRUSH_SYNC_MESH_SHADOWS = True         # every brush entity (any class): its meshes get the entity's Cast Shadows value. Hammer writes both when you edit it; the importer left the meshes at 0 (Enabled) on entities set to No Shadows
WATER_VOLUMES = True          # Source1 water -> CS2: world geometry with a Source1 "Water" material is moved into a func_water (swimmable), func_water_analog -> func_water, and deep water gets an underwater post_processing_volume. Patched in place (Hammer must be closed); <prefix>_water.txt. Reruns never touch water already in a func_water or a volume that already exists, and a volume you delete stays deleted. Underwater lights come from the lighting rebuild (LIGHTING_UNDERWATER)
WATER_PP = True               # add the underwater post_processing_volume (False: only the func_water part)
WATER_PP_VPOST = "lighting/postprocessing/port_underwater.vpost"   # the underwater volumes' vpost, written to the addon from WATER_PP_VPOST_TEXT (a cyan blur-bloom tint). Its own name, not CS2's postprocess_filmic_underwater.vpost, so the map always loads this copy (with WATER_PP_BRIGHTNESS) and not the stock one; volumes from earlier runs are switched over
WATER_PP_OLD_VPOSTS = ("lighting/postprocessing/postprocess_filmic_underwater.vpost",)   # vposts earlier runs gave the underwater volumes: those volumes are treated as the script's own
WATER_PP_BRIGHTNESS = 0.0     # exposure bias of the underwater vpost in stops (+1 = twice as bright). 1.0 tested as a flashbang in game; brightness is now handled by WATER_PP_TINT
WATER_PP_TINT = 0.5           # how strong the cyan underwater tint is: 1 = skink52's vpost tint (very dark: it multiplies the picture by about 0.5 cyan), 0 = no tint. In between keeps the hue but brightens towards white
WATER_PP_FADE_TIME = 0.0      # blend time into / out of the underwater effect in seconds. 0 = instant, like CS:GO. None = the master volume's fadetime (1.0 on the test map: in game that was far too slow)
WATER_PP_MASTER_FADE = True   # also give the map's master post_processing_volume WATER_PP_FADE_TIME: leaving the water blends back to the master over ITS fadetime (1 s gave a flash / dark frame on the way out). Only when the map has underwater volumes
WATER_PP_VPOST_OVERWRITE = True   # rewrite the vpost every run so it always matches the script. Set False once you tune it in the Post Processing editor, or your changes are replaced
WATER_PP_TEST = False         # True: write a test vpost instead (exposure 3 stops down: the screen goes very dark the moment you are inside an underwater volume), to check the volumes switch on at all. Rebuild the map after changing it
WATER_PP_MIN_DEPTH = 64.0     # water this shallow or less cannot put a standing player's eyes under: no volume
WATER_PP_BELOW_SURFACE = 64.0 # the volume's top sits this far under the surface: the volume tests the player's origin (feet), and a standing player's eyes are 64 above the feet, so the effect starts as the eyes go under (was 80, skink52's figure: in game that came in late). Volumes from earlier runs are resized to it
WATER_PP_SKIP_DEATH_WATER = True   # no volume where trigger_teleport / trigger_hurt boxes fill the water (players are sent away before they see it); listed in the log
WATER_PP_MATCH_MASTER = True  # the underwater volumes copy every setting of the map's master post_processing_volume (fade time, exposure, flags) except the vpost: blending between volumes with different settings can stop the underwater one showing. Also applied on reruns to volumes that use WATER_PP_VPOST
WATER_PP_PROPS = {"StartDisabled": "0", "spawnflags": "4097", "minexposure": "0.5", "maxexposure": "1.25", "enableexposure": "1",
                  "exposurespeedup": "1", "exposurespeeddown": "1", "fadetime": "1.0"}   # used when the map has no master volume (or WATER_PP_MATCH_MASTER is False)
WATER_PP_MATERIAL = "materials/tools_postprocess_volume.vmat"   # the volume's mesh material when the map has no post_processing_volume box to copy (the importer's own)
WATER_MATERIAL = "auto"       # Source1 water materials with no CS2 vmat (not shipped with CS2, not in the addon) are swapped on the water meshes for: "auto" = the stock CS2 water material in materials/liquids/ whose name is closest to the Source1 one (the log lists every one your CS2 has); or a path, e.g. "materials/liquids/<name>.vmat", for all of them; "" = leave them (they show as the error material)
WATER_MATERIAL_MAP = {}       # per material, overrides WATER_MATERIAL, e.g. {"materials/liquids/nuke_water.vmat": "materials/liquids/<name>.vmat"}. Applied even when the left-hand material exists, so it also replaces a stock water an earlier run picked
WATER_IGNORE_MATERIALS = ()   # material paths (or parts of them) never treated as water, e.g. ("materials/nature/waterfall",)
LIGHTING_REBUILD = "always"   # rebuild the lights from the .vmf (the importer gets brightness and colour wrong): every light, light probe volume and visibility_hint is deleted and rebuilt, then the probe, stationary-light and vis passes run (rebuild_lighting.py; needs numpy + scipy and the .vmf). "always" = every run, so lights you add or tune in Hammer are replaced (change the Source1 values or LIGHT_SCALE instead); "once" = only the first time per map (remembered in backups\<map>.vmap.lighting_done.txt), so later hand edits are kept; "off". Report: <prefix>_lighting.txt
LIGHTING_KEEP_PREFIX = "keep_"  # lights whose name starts with this survive the rebuild (lights you add in Hammer), unless a light of that name is in the .vmf (then it is the Source1 map's own light and is rebuilt like the rest). The stationary-light pass may still switch kept lights to Stationary; "" = keep none
LIGHTING_PROBE_WALL_CLASSES = ("func_breakable",)   # brush entities the light probe volumes treat as walls: compartments split at them and volume sides stop at their faces instead of reaching into them (lit per object from probes, a breakable inside a volume edge comes out dark)
LIGHT_SCALE = 1.0             # brightness of every rebuilt light_omni2 / light_barn (0.8 = 20% dimmer). Not checked against a real CS2 bake: bake, compare a few rooms to CS:GO, tune, then rebuild with LIGHTING_REBUILD = "always"
SUN_SCALE = 1.0               # light_environment brightness
LIGHTING_UNDERWATER = True    # a few dim lights inside each func_water that has no light in it (Source1 water was lit by the lightmap)
LIGHTING_EXPOSURE = None      # e.g. (0.8, 1.0): min / max exposure of the master post_processing_volume
LIGHTING_PROBES = True        # env_combined_light_probe_volume fitted to each playable compartment
LIGHTING_STATIONARY = True    # switch a minimal set of lights per compartment Static -> Stationary (so players / weapons are lit)
LIGHTING_VIS = True           # visibility_hint boxes fitted to the play space (faster vis compile)
LIGHTING_VIS_ALL_LOWER = False   # vis: Lower hints instead of Low (for a vis compile that stalls)
LIGHTING_LARGE_FACE_AREA = 262144.0   # lit faces larger than this (sq units) are one lightmap chart each and can bake blotchy: split / reported
LIGHTING_SPLIT_LARGE_FACES = "faces"   # "faces" = cut large lit faces on a LIGHTING_FACE_TILE grid, kept in one mesh; "meshes" = also one mesh per tile (each gets its own cubemap / probe lighting: visible seams); "off" = only report
LIGHTING_FACE_TILE = 512.0
ENTITY_SEAM_REPORT = True     # list brush-entity faces that fill a gap in, or lie on, a world surface in the same plane (they light differently: e.g. a black square in a ceiling). Report only: <prefix>_entity_seams.txt
RADAR = True                  # draw the radar (minimap) from the map geometry: panorama/images/overheadmaps/<map>_radar_psd.png + .vtex in the addon, resource/overviews/<map>.txt in its game\ twin, a preview in the logs folder. Needs numpy (+ scipy for the room filter). Redrawn on reruns only while its files are still exactly what the script wrote: edit one and none is touched again (delete them to redraw)
RADAR_SIZE = 1024             # image size in pixels
RADAR_TRIM = 0.5              # percent of the floor extent trimmed off each edge (0 keeps every floor)
RADAR_MIN_WALL = 40.0         # outline wall faces at least this tall
RADAR_ROOM_FILTER = True      # draw only floors that open onto a room with lights / spawns (drops roofs and outside faces); needs scipy
RADAR_CLIP_AREA = 262144.0    # floor faces bigger than this (sq units seen from above; 512 x 512) are clipped to the rooms pixel by pixel, so a square block of water or terrain that reaches outside the map is not drawn there (needs the room filter)
RADAR_TEXTURE_COLOURS = True  # colour floors from their converted textures in the addon where they exist; otherwise guessed from the material name
REPORT_MISSING_ENTITIES = True  # list the .vmf entities that are not in the .vmap (named ones by name, unnamed ones by position / brush bounds; ones that only moved are listed apart) -> <prefix>_missing_entities.txt. Read-only. Needs the .vmf
REPORT_DROPPED_IO = True      # list the entity outputs (IO) that are in the .vmf but not in the .vmap -> <prefix>_dropped_io.txt. Read-only: nothing is restored or changed. Needs the .vmf
SOUND_RANGE_MIN = 0.0                  # floor for the curve end distance after scaling (0 = none)
SOUND_SOURCE1_FALLOFF = True           # True: the volume curve follows Source1's own distance model for ambient_generic (sound level from the radius, then gain = 1/(distance x dist_mult), see _source1_gain). False: the old straight line to silence at the radius
SOUND_SOURCE1_GAIN_MIN = 0.01          # snd_gain_min: below this gain Source1 fades the sound to 0 over the same distance again (default 0.01 in the Source engine code)
SOUND_ENTITY_VOLUME = True             # multiply each ambient_generic's Volume keyvalue (stored as health, 0-10, default 10) into its event volume. Several entities on one event: the loudest wins
SOUND_EVERYWHERE_RADIUS = 100000.0
SOUNDEVENT_PREFIX = ""                 # e.g. 'mymap.' -> 'mymap.mysounds.mh_blade_loop1'
LOOP_NAME_HINTS = ("loop",)            # a sound whose file name contains one of these is treated as looping
CONVERT_LOOP_MP3_TO_WAV = True         # looping .mp3 -> .wav (mp3 has no loop cue points; needs: pip install miniaudio)
CONVERT_ALL_MP3_TO_WAV = False         # False: non-looping .mp3 are copied unchanged (smaller, no re-encode loss)
CONVERT_NONPCM_WAV = True              # re-encode wavs that are not plain 16-bit PCM (ADPCM, 8/24-bit, float, extensible) to 16-bit PCM; cue/smpl loop chunks are kept
SOUND_PREFER_CS2_FILES = False         # True: soundscript waves listed in CS2's VPK index are referenced, not copied. Default False because some CS2 index entries are stubs whose audio is missing/unplayable (e.g. physics/metal/metal_grate_impact_*); the Source1 file is copied into the addon instead
ADD_LOOP_CUE_TO_CONVERTED = True       # write a cue point at sample 0 into converted looping .wav (UNVERIFIED in CS2)
SPRITE_DEFAULT_ADDITIVE = True         # 'Sprite' VMTs with no $additive/$translucent/$alphatest: assume additive (glow sprites)
GENERATE_SPRITE_PARTICLES = False      # OFF by default: a generated particles/<dir>/<name>.vpcf in the addon overrides a stock CS2 particle of the same path
SPRITE_PARTICLE_RADIUS = 18.0          # world units; tune in Hammer/Particle Editor
CONVERT_FIRE_SPARKS = True             # env_fire keeps burning / hurting, and gets an info_particle_system <name>_fx with the particle CS:GO's own code played for it (particles/burning_fx/env_fire_<tiny|small|medium|large>[_smoke], which CS2 ships under the same names), started / stopped by the same outputs. env_spark keeps its sound and gets an info_particle_system <name>_fx with a particle generated from CS:GO's spark code (particles/spark/), driven by the same outputs. <prefix>_fire_sparks.txt, vmap backed up first
FIRE_HURT = True                       # CS2's env_fire does no damage (tested): each gets a trigger_hurt <name>_hurt (burn damage, players) over the flames, switched by the same outputs
FIRE_HURT_DAMAGE = 2.0                 # trigger_hurt damage per second. CS:GO's fire did 1/s (measured), but CS2's trigger_hurt applies half its damage every 0.5 s in whole points, so 1 does nothing and 2 (1 every half second) is the lowest that hurts
FIRE_HURT_WIDTH = 1.0                  # trigger width as a fraction of firesize. Source (fire.cpp GetFireDimensions): players are hurt inside +-firesize x 128/256 x 0.5 x 2 = +-firesize/2 (at least +-16) around the fire, from its origin up to firesize
FIRE_HURT_MODE = "trigger"             # "trigger" (default: point_hurt did no damage in CS2 and cannot be parented, tested). "point_hurt": a point_hurt <name>_hurt doing FIRE_POINT_HURT_DAMAGE every FIRE_POINT_HURT_DELAY s within a sphere covering the fire's damage box (like CS:GO: one hit per interval). "trigger": the trigger_hurt (CS2 applies half its damage every 0.5 s, so 2/s is its minimum). Switching mode converts the _hurt entities made earlier
FIRE_POINT_HURT_DAMAGE = 1.0           # point_hurt damage per hit (CS:GO fire: 1, measured)
FIRE_POINT_HURT_DELAY = 1.0            # seconds between hits (CS:GO: fire_dmginterval 1.0)
FIRE_SIZE_STEP = 36.0                  # CS:GO picked the fire particle by floor(firesize / 36): 0 tiny, 1 small, 2 medium, 3+ large (Source SDK c_fire_smoke.cpp: the client scale is the firesize)
CONVERT_STEAM_TRAILS = True            # env_steam -> info_particle_system with its generated particles/steam particle (start_active from InitialState; TurnOn/TurnOff inputs -> Start/Stop), and env_spritetrail -> info_particle_system with a generated trail particle (particles/spritetrail/: a ribbon behind the parent, Source1 sprite texture, width, lifetime, colour). In the vmap, backed up first; <prefix>_steam_trails.txt
GENERATE_STEAM_PARTICLES = True        # write a particle for each STEAM_EFFECTS entry (env_steam replacement)
STEAM_FROM_VMAP = True                 # read env_steam entities from the (text) .vmap files and build a particle for each distinct set of keyvalues
STEAM_SIZE_TO_RADIUS = 0.5             # Source1 StartSize/EndSize -> CS2 radius (0.5 = Source1 size is the full quad width; 1.0 looked far too wide)
STEAM_EMISSIVE_ADD = (0, 0, 0)         # added to an Emissive jet's rendercolor (0 = use rendercolor as-is; it made no visible difference)
STEAM_HEATWAVE_MODE = "refract"        # env_steam type 1 (heat wave) is a distortion, not visible steam: "refract" = refracting sprites (no colour, just the shimmer), "sprite" = soft white steam instead, "skip" = build nothing
STEAM_HEATWAVE_TEXTURE = ""                # "" = generate a soft noisy normal map (default; raise STEAM_HEATWAVE_NORMAL_STRENGTH for a stronger ripple). Alternatives: "source1vmt:sprites/heatwave" = the real Source1 heat-wave material: converts its $normalmap (effects/fisheyelens_normal) as a proper normal map (needs that vtf + the vmt under SOURCE1_ROOTS); "source1:particle/particle_smokegrenade" = convert that Source1 texture and refract with it; "materials/particle/warp5_explosion.vtex" = stock CS2 shockwave texture (draws a dark ring); or any stock CS2 .vtex path (see the heat-wave candidates log)
STEAM_HEATWAVE_SPIN = 120.0              # heat-wave sprites: spin speed range (+- degrees/second). Source1 gives each heat-wave quad a randomly tilted normal every frame (a flutter) instead of rolling it; spinning the lens normal map is the closest CS2 equivalent. 0 = no flutter
STEAM_HEATWAVE_NORMAL_STRENGTH = 3.0      # strength of the generated normal map (bigger = stronger ripple)
STEAM_HEATWAVE_REFRACT = None          # refract amount. None = match Source1: the $refractamount of sprites/heatwave.vmt (-0.007 in the heatwave.vmt I was given; unverified against a stock CSGO install), read whatever STEAM_HEATWAVE_TEXTURE is; if that .vmt is not found under SOURCE1_ROOTS nothing is guessed: 0.25 is used and the log says which file to add. A number overrides it (0.25 = what the stock CS2 reference particle uses)
STEAM_FORWARD_AXIS = "x"               # which local axis of the info_particle_system the jet fires along: x (Source1 forward), -x, y, -y, z, -z. Change it if the steam points the wrong way
STEAM_EMIT_AXIS = "x"                  # local axis the jet travels along in the particle: "x" (forward, as in Source1), "y" or "z" (up). Change it if the steam leaves in the wrong direction even though the entity angles match
STEAM_ALPHA_SCALE = 1.0                # multiplies renderamt (clamped to 255); raise it (e.g. 1.5-2) if the steam is still less white/dense than Source1
STEAM_SELF_ILLUM = 1.0                 # Emissive jets (spawnflags 1): renderer "self illumination" so map lighting does not darken the steam (CS2 sprites are lit per particle by default, which is why they look grey in the map but white in the particle editor). None = do not write it
STEAM_DIFFUSE_AMOUNT = None            # how much map light affects the steam (None = leave at the CS2 default; 0 = ignore map light entirely)
STEAM_FOG = "default"                  # "default" (leave alone), "disabled" (steam ignores map fog) or "enabled"
STEAM_OVERBRIGHT = 1.0                 # renderer overbright factor (1 = default; raise to brighten)
STEAM_UNLIT_TINT = (200, 200, 200)     # colour for non-Emissive env_steam (Source1 uses world lighting there)
STEAM_TEXTURE_SOURCE1 = "particle/particle_smokegrenade"   # Source1 texture tried first; a soft radial puff is generated if missing
STEAM_EFFECTS = {}                     # optional extras, name -> env_steam keyvalues, for jets whose entity is not in a text .vmap. Normally empty: particles are named after their values (env_steam_s120_p45_...) and rebuilt from any effect_name in the vmap
REDERIVE_EVENTS_FROM_NAMES = True      # entity already uses an event name like 'mysounds.mh_blade_loop1' -> find sound/mysounds/mh_blade_loop1.wav|mp3 again
KEEP_PREVIOUS_SOUNDEVENTS = False      # also keep events from earlier runs that nothing in the vmap or Source1 can be matched to (stale)
CONVERT_SOUNDSCRIPTS = True            # port Source1 soundscript names found in the vmaps (e.g. MetalGrate.ImpactHard) as same-named soundevents
COPY_SOUNDS = True                     # copy .wav/.mp3 from Source1 (same relative path)
OVERWRITE_EXISTING_VMATS = True        # regenerate VMATs that already exist in the addon folder

# ---- Source1 model import (cs_mdl_import.exe) -----------------------------------------------
IMPORT_MODELS = True                   # False: only list the models in <prefix>_models_to_convert.txt
CS_MDL_IMPORT_EXE = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs_mdl_import.exe"
IMPORT_GAME = "csgo"
OVERWRITE_EXISTING_MODELS = False      # True: re-import models whose .vmdl is already in the addon folder
PRECHECK_SIDECARS = True               # skip models lacking .vvd/.vtx (logged, not retried)
MDL_MAX_RETRIES = 3
MDL_POLL_SECONDS = 2
MDL_MAX_IDLE_SECONDS = 60              # no importer output and no output-file change -> hung
MDL_MAX_RUNTIME_SECONDS = 900          # hard cap per attempt
MDL_MTIME_TOLERANCE = 2.5              # seconds; exFAT/FAT drives store 2 s timestamps
MDL_VTX_SUFFIXES = (".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx")
ANIM_EXT = ".png"
STATIC_EXT = ".tga"
DEBUG_EXTRACT = False
REQUIRE_NULL_DELIMITED = True
ALLOWED_ROOTS = {
    "materials", "models", "sound", "sounds", "particles", "panorama", "resource", "scripts", "maps",
}
ENABLE_STRATEGY_B_MODEL_SCAN = True    # scan .vmdl files already in the addon folder for deps
VMAP_SCAN_WORKERS = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
BACKUP_DIR = os.path.join(SCRIPT_DIR, "backups")   # map backups + the records of what was done once (water volumes, lighting, radar files)
BACKUP_KEEP = 5               # timestamped backups kept per map (newest); <map>.original.vmap, the first copy ever made, is always kept

SHADER_MAP = {
    "vertexlitgeneric": "csgo_vertexlitgeneric.vfx",
    "lightmappedgeneric": "csgo_lightmappedgeneric.vfx",
    "worldvertextransition": "csgo_lightmappedgeneric.vfx",
    "unlitgeneric": "csgo_unlitgeneric.vfx",
    "unlittwotexture": "csgo_unlitgeneric.vfx",
    "sprite": "csgo_unlitgeneric.vfx",          # Source1 sprites are billboarded, unlit quads
}
DEFAULT_AO = "materials/default/default_ao.tga"

IMAGE_FORMAT_RGBA8888 = 0
IMAGE_FORMAT_ABGR8888 = 1
IMAGE_FORMAT_RGB888 = 2
IMAGE_FORMAT_BGR888 = 3
IMAGE_FORMAT_ARGB8888 = 11
IMAGE_FORMAT_BGRA8888 = 12
IMAGE_FORMAT_DXT1 = 13
IMAGE_FORMAT_DXT3 = 14
IMAGE_FORMAT_DXT5 = 15
IMAGE_FORMAT_BGRX8888 = 16
IMAGE_FORMAT_DXT1_ONEBITALPHA = 20
TEXTUREFLAGS_ENVMAP = 0x00004000
SUPPORTED = {
    IMAGE_FORMAT_DXT1, IMAGE_FORMAT_DXT1_ONEBITALPHA, IMAGE_FORMAT_DXT3, IMAGE_FORMAT_DXT5,
    IMAGE_FORMAT_BGRA8888, IMAGE_FORMAT_RGBA8888, IMAGE_FORMAT_ABGR8888, IMAGE_FORMAT_ARGB8888,
    IMAGE_FORMAT_BGR888, IMAGE_FORMAT_RGB888, IMAGE_FORMAT_BGRX8888,
}
VTF_RSRC_IMAGE = 0x30

VALID_EXTENSIONS = {
    ".vmat", ".vmdl", ".vmdl_c", ".dmx", ".tga", ".vsnd", ".vpcf",
    ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".vmt", ".vtf", ".vtex", ".vtex_c",
}
MATERIALISH_BARE_EXTS = {".vmt", ".vtf", ".vmat", ".tga", ".png", ".jpg", ".jpeg", ".vtex", ".vtex_c"}
ROOT_TOKENS = ["materials/", "models/", "particles/", "panorama/", "sound/", "sounds/", "resource/", "scripts/", "maps/"]
COMPILED_EXTS = {".vmat", ".vmdl", ".vtex", ".vsnd", ".vpcf"}   # stored as "<name>_c" in CS2
IMAGE_EXTS = (".tga", ".png", ".jpg", ".jpeg")
# Source1 shaders with no $basetexture and no direct VMAT equivalent here: listed in
# <prefix>_unsupported_shaders.txt instead of being reported as failures.
UNSUPPORTED_SHADERS = {"water", "refract", "eyerefract", "eyes", "teeth"}
# Underwater .vpost written when WATER_PP_VPOST is in neither the addon nor CS2 (the one from the conversation: tone mapping + a cyan-tinted bloom). Never overwritten.
WATER_PP_VPOST_TEXT = '<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n{\n\t_class = "CPostProcessData"\n\tm_layers = \n\t[\n\t\t{\n\t\t\t_class = "CToneMappingLayer"\n\t\t\tm_name = "Tone Mapping"\n\t\t\tm_nOpacityPercent = 100\n\t\t\tm_bVisible = true\n\t\t\tm_pLayerMask = null\n\t\t\tm_params = \n\t\t\t{\n\t\t\t\tm_flExposureBias = 0.0\n\t\t\t\tm_flShoulderStrength = 0.1507\n\t\t\t\tm_flLinearStrength = 0.4968\n\t\t\t\tm_flLinearAngle = 0.1004\n\t\t\t\tm_flToeStrength = 0.2022\n\t\t\t\tm_flToeNum = 0.0184\n\t\t\t\tm_flToeDenom = 0.2983\n\t\t\t\tm_flWhitePoint = 2.0\n\t\t\t\tm_flLuminanceSource = 0.0\n\t\t\t\tm_flExposureBiasShadows = 0.0\n\t\t\t\tm_flExposureBiasHighlights = 0.0\n\t\t\t\tm_flMinShadowLum = 0.0\n\t\t\t\tm_flMaxShadowLum = 0.5\n\t\t\t\tm_flMinHighlightLum = 2.0\n\t\t\t\tm_flMaxHighlightLum = 8.0\n\t\t\t}\n\t\t},\n\t\t{\n\t\t\t_class = "CBloomLayer"\n\t\t\tm_name = "Bloom"\n\t\t\tm_nOpacityPercent = 100\n\t\t\tm_bVisible = true\n\t\t\tm_pLayerMask = null\n\t\t\tm_params = \n\t\t\t{\n\t\t\t\tm_blendMode = "BLOOM_BLEND_BLUR"\n\t\t\t\tm_flBloomStrength = 0.0\n\t\t\t\tm_flScreenBloomStrength = 0.0\n\t\t\t\tm_flBlurBloomStrength = 0.9507\n\t\t\t\tm_flBloomThreshold = 0.0\n\t\t\t\tm_flBloomThresholdWidth = 1.849\n\t\t\t\tm_flSkyboxBloomStrength = 1.0\n\t\t\t\tm_flBloomStartValue = 1.0\n\t\t\t\tm_flBlurWeight = \n\t\t\t\t[\n\t\t\t\t\t0.2, 0.2, 0.2, 0.2,\n\t\t\t\t\t0.2,\n\t\t\t\t]\n\t\t\t\tm_vBlurTint = \n\t\t\t\t[\n\t\t\t\t\t[ 0.007499, 0.53948, 0.62396 ],\n\t\t\t\t\t[ 0.093059, 0.283149, 0.439657 ],\n\t\t\t\t\t[ 0.072272, 0.144128, 0.219526 ],\n\t\t\t\t\t[ 1.0, 1.0, 1.0 ],\n\t\t\t\t\t[ 1.0, 1.0, 1.0 ],\n\t\t\t\t]\n\t\t\t}\n\t\t},\n\t]\n}\n'
WATER_PP_TEST_VPOST_TEXT = WATER_PP_VPOST_TEXT.replace("m_flExposureBias = 0.0", "m_flExposureBias = -3.0", 1)


def _water_vpost_text():
    """the underwater vpost: WATER_PP_TEST's very dark test one, else WATER_PP_VPOST_TEXT with WATER_PP_BRIGHTNESS as its exposure bias"""
    if WATER_PP_TEST:
        return WATER_PP_TEST_VPOST_TEXT
    text = WATER_PP_VPOST_TEXT.replace("m_flExposureBias = 0.0", "m_flExposureBias = %s" % float(WATER_PP_BRIGHTNESS or 0.0), 1)
    k = max(0.0, min(1.0, float(WATER_PP_TINT)))

    def soften(mt):                              # one tint row: hue kept (scaled so its brightest channel is 1), then mixed towards white
        v = [float(x) for x in mt.group(1).split(",")]
        top = max(v) or 1.0
        if k >= 1.0:
            return mt.group(0)
        return "[ " + ", ".join("%.6f" % ((1.0 - k) + k * x / top) for x in v) + " ]"
    head, _, tail = text.partition("m_vBlurTint")
    return head + "m_vBlurTint" + re.sub(r"\[\s*([0-9.eE+-]+\s*,\s*[0-9.eE+-]+\s*,\s*[0-9.eE+-]+)\s*\]", soften, tail)

# F_BLEND_MODE values, assumed from the Material Editor dropdown order
# (Opaque 0, Translucent 1, Alpha Test 2, Mod2x 3, Additive 4, Multiply 5, ModThenAdd 6).
# Only Additive has been confirmed to work; verify the others in a saved .vmat.
# $nocull on vertexlit/lightmapped: also write F_DONT_FLIP_BACKFACE_NORMALS (the editor offers it
# alongside F_RENDER_BACKFACES; Source1 never flipped normals on back faces). Set False to omit.
NOCULL_DONT_FLIP_NORMALS = True
BLEND_MODE_TRANSLUCENT = 1
BLEND_MODE_ALPHATEST = 2
BLEND_MODE_ADDITIVE = 4

# ======================================================================== PATH HELPERS

@lru_cache(maxsize=None)
def file_exists(path: str) -> bool:
    return os.path.isfile(path)

@lru_cache(maxsize=None)
def listdir_cached(path: str) -> tuple:
    try:
        return tuple(os.listdir(path))
    except Exception:
        return tuple()

@lru_cache(maxsize=500_000)
def rel_norm(p: str) -> str:
    return str(p).strip().replace("\\", "/").lstrip("/")

@lru_cache(maxsize=500_000)
def normalize_candidate(p: str) -> str:
    p = rel_norm(p)
    if not p:
        return ""
    low = p.lower()
    if any(low.startswith(tok) for tok in ROOT_TOKENS):
        return low
    _name, ext = os.path.splitext(p)
    if ext.lower() in MATERIALISH_BARE_EXTS:
        return f"materials/{p}"
    return p

@lru_cache(maxsize=500_000)
def normalize_dep_must_be_rooted(p: str) -> str:
    p = rel_norm(str(p)).strip().strip('"').strip("'")
    if not p:
        return ""
    low = p.lower().lstrip("/")
    if any(low.startswith(tok) for tok in ROOT_TOKENS):
        return normalize_candidate(low)
    return ""

@lru_cache(maxsize=500_000)
def top_root(p: str) -> str:
    p = normalize_candidate(p)
    if not p or "/" not in p:
        return ""
    return p.split("/", 1)[0].lower()

@lru_cache(maxsize=500_000)
def passes_root_filter(p: str) -> bool:
    r = top_root(p)
    return bool(r) and (r in ALLOWED_ROOTS)

def strip_materials_prefix(ref: str) -> str:
    ref = rel_norm(str(ref).strip('"'))
    return ref[len("materials/"):] if ref.lower().startswith("materials/") else ref

def find_ci(root: Path, rel: str):
    """Case-insensitive lookup of rel below root. Returns Path or None."""
    cur = root
    for part in rel_norm(rel).split("/"):
        if not part:
            continue
        cand = cur / part
        if cand.exists():
            cur = cand
            continue
        match = next((n for n in listdir_cached(str(cur)) if n.lower() == part.lower()), None)
        if match is None:
            return None
        cur = cur / match
    return cur

@lru_cache(maxsize=500_000)
def find_in_source1(rel: str):
    """Search SOURCE1_ROOTS in order for rel (e.g. 'materials/x/y.vmt'). -> (abs path, root) or (None, None)."""
    rel = rel_norm(rel)
    variants = [rel]
    if "_" in rel:
        variants.append(rel.replace("_", " "))
    for root in SOURCE1_ROOTS:
        r = Path(root)
        for v in variants:
            p = find_ci(r, v)
            if p is not None and p.is_file():
                return str(p), root
    return None, None

def resolve_vtf(ref: str):
    rel = strip_materials_prefix(ref)
    if rel.lower().endswith(".vtf"):
        rel = rel[:-4]
    p, _ = find_in_source1("materials/" + rel + ".vtf")
    return Path(p) if p else None

_EXT_END_RE = re.compile(r'\.[a-z0-9_]{1,8}$', re.IGNORECASE)

def split_glued_paths(s: str):
    s = normalize_candidate(s)
    hits = []
    for tok in ROOT_TOKENS:
        start = 0
        while True:
            i = s.find(tok, start)
            if i == -1:
                break
            hits.append(i)
            start = i + 1
    hits = sorted(set(hits))
    # only a root token preceded by a file extension starts a NEW (glued) path
    hits = [i for i in hits if i == 0 or _EXT_END_RE.search(s[:i])]
    if len(hits) <= 1:
        return [s]
    out = []
    for idx, pos in enumerate(hits):
        end = hits[idx + 1] if idx + 1 < len(hits) else len(s)
        chunk = normalize_candidate(s[pos:end].strip("/"))
        if "/" in chunk and "." in chunk.rsplit("/", 1)[-1]:
            out.append(chunk)
    return out or [s]

VALID_PATH_CHARS_RE = re.compile(r'^[a-zA-Z0-9_\-./&()+ ]+$')

@lru_cache(maxsize=500_000)
def is_valid_asset_path(path: str) -> bool:
    path = normalize_candidate(path)
    if not path or "/" not in path or ".." in path.split("/"):
        return False
    folder, filename = path.rsplit("/", 1)
    if not folder or not filename or "." not in filename:
        return False
    if os.path.splitext(filename)[1].lower() not in VALID_EXTENSIONS:
        return False
    if filename.startswith(".") and "." not in filename[1:]:
        return False
    return bool(VALID_PATH_CHARS_RE.match(path))

def derive_export_dir(vmap_path_or_dir: Path) -> Path:
    p = vmap_path_or_dir
    if p.is_file():
        return p.parent.parent
    if p.is_dir():
        if p.name.lower() == "maps":
            return p.parent
        cur = p
        for _ in range(8):
            if cur.name.lower() == "maps":
                return cur.parent
            cur = cur.parent
    return p

def enumerate_vmaps(vmap_path_or_dir: str):
    p = Path(vmap_path_or_dir)
    if p.is_file() and p.suffix.lower() == ".vmap":
        return sorted(p.parent.rglob("*.vmap")), p
    if p.is_dir():
        return sorted(p.rglob("*.vmap")), None
    raise FileNotFoundError(f"VMAP_PATH does not exist or is not a .vmap/dir: {p}")

def copy_asset_to_export(export_dir: Path, source_path: str, relative_path: str) -> bool:
    dest = export_dir / Path(rel_norm(relative_path.replace("+", "_")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(source_path, str(dest))
        return True
    return False

# ======================================================================== VMAP HARVEST

PAT_BYTES = re.compile(rb'[A-Za-z0-9_\-./\\&()+ ]{2,}\.[A-Za-z0-9_]{1,8}')
PAT_ASCII_IN_STR = re.compile(r'[A-Za-z0-9_\-./\\&()+ ]{2,}\.[A-Za-z0-9_]{1,8}')

def _extract_from_ascii_tokens(data: bytes):
    out = set()
    for seg in data.split(b"\x00"):
        if 4 <= len(seg) <= 16384:
            out.update(PAT_ASCII_IN_STR.findall(seg.decode("ascii", errors="ignore")))
    return out

def _extract_from_utf16le_tokens(data: bytes):
    out = set()
    try:
        t16 = data.decode("utf-16le", errors="ignore")
    except Exception:
        return out
    for tok in t16.split("\x00"):
        if 4 <= len(tok) <= 16384:
            out.update(PAT_ASCII_IN_STR.findall(tok))
    return out

def _fallback_regex_scan(data: bytes):
    out = set()
    for m in PAT_BYTES.finditer(data):
        s = m.group(0).decode("ascii", errors="ignore")
        if s:
            out.add(s)
    return out

_PLUS_REF_RE = re.compile(rb'(materials/[A-Za-z0-9_\-./ ]*/)\+([0-9][A-Za-z0-9_\-. ]*\.(?:vmat|vmt|vtf|tga|png))')

def rename_plus_refs(vmap_paths, backup_dir):
    """CS:GO animated-texture frames (materials/x/+0name.vmat): resourcecompiler stops with "Non-fused-child compiles cannot contain fused delimiter '+'".
    The converted files are written with '_' (see _tex_base); the vmap's references are rewritten the same way. Same length, so patched in place
    (backed up to BACKUP_DIR first). -> list of the renamed references"""
    done = []
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
            hits = sorted({m_.group(0).decode("ascii", "ignore") for m_ in _PLUS_REF_RE.finditer(data)})
            if not hits:
                continue
            bak = _backup_vmap(vp, "plusfix")
            Path(vp).write_bytes(_PLUS_REF_RE.sub(lambda m_: m_.group(1) + b"_" + m_.group(2), data))
            done += hits
            print(f"  [PLUS] {os.path.basename(str(vp))}: {len(hits)} reference(s) to '+' names renamed to '_' ({'; '.join(hits[:4])}); original saved as {os.path.basename(bak)}")
        except Exception as ex:
            print(f"  [WARN] could not rename '+' references in {vp}: {ex}")
    return done

# ======================================================================== PROP MASS NAMES (for push_mass.js)

# Modules for reading/writing binary .vmap files, bundled from skink52's CS2 port tools (github.com/skink52/cs2portdocs).
# Changed here only for speed (same results): vmapedit.next_node_id scans just the elements added since its last call; visfit.merge_boxes
# computes box volumes per row in one call. probes.run also takes wall_classes (LIGHTING_PROBE_WALL_CLASSES).
_BUNDLED_VMAP_MODULES = {'dmx': '"""Read/write Source 2 binary DMX (encoding binary 9) - the .vmap format.\n\ndoc = dmx.load(path) -> Doc with .header, .prefix (list of attr lists), .elements\nElement = dict(type=bytes, name=bytes, guid=bytes(16), attrs=list[[name, type, value]])\nElement references are ints (index into doc.elements), -1 = null.\n"""\nimport struct\n\nT_ELEM, T_INT, T_FLOAT, T_BOOL, T_STR, T_BIN, T_TIME, T_COLOR, T_V2, T_V3, T_V4, T_QANG, T_QUAT, T_MAT, T_U64, T_U8 = range(1, 17)\nARR = 32\n_fix = {T_INT: (\'<i\', 4), T_FLOAT: (\'<f\', 4), T_BOOL: (\'<?\', 1), T_TIME: (\'<i\', 4), T_COLOR: (\'<4B\', 4), T_V2: (\'<2f\', 8),\n        T_V3: (\'<3f\', 12), T_V4: (\'<4f\', 16), T_QANG: (\'<3f\', 12), T_QUAT: (\'<4f\', 16), T_MAT: (\'<16f\', 64), T_U64: (\'<Q\', 8), T_U8: (\'<B\', 1)}\n\nclass R:\n    def __init__(s, d, p=0): s.d = d; s.p = p\n    def i(s): v = struct.unpack_from(\'<i\', s.d, s.p)[0]; s.p += 4; return v\n    def b(s): v = s.d[s.p]; s.p += 1; return v\n    def cstr(s): e = s.d.index(b\'\\0\', s.p); v = s.d[s.p:e]; s.p = e + 1; return v\n    def raw(s, n): v = s.d[s.p:s.p + n]; s.p += n; return v\n    def fmt(s, f, n):\n        v = struct.unpack_from(f, s.d, s.p); s.p += n\n        return v[0] if len(v) == 1 else list(v)\n\nclass Doc:\n    def __init__(s): s.header = b\'\'; s.prefix = []; s.elements = []\n\ndef _read_val(r, t, strtab, inline_str):\n    if t == T_ELEM:\n        v = r.i()\n        if v == -2: return (\'guid\', r.cstr())\n        return v\n    if t == T_STR: return r.cstr() if inline_str else strtab[r.i()]\n    if t == T_BIN: n = r.i(); return r.raw(n)\n    f, n = _fix[t]; return r.fmt(f, n)\n\ndef _read_attr(r, strtab, inline_str):\n    t = r.b()\n    if t > ARR:\n        n = r.i(); bt = t - ARR\n        if bt == T_STR:  # string arrays are always inline\n            return t, [r.cstr() for _ in range(n)]\n        return t, [_read_val(r, bt, strtab, inline_str) for _ in range(n)]\n    return t, _read_val(r, t, strtab, inline_str)\n\ndef loads(d):\n    doc = Doc(); e = d.index(b\'\\n\\x00\'); doc.header = d[:e + 1]\n    r = R(d, e + 2)\n    for _ in range(r.i()):\n        attrs = []\n        for _ in range(r.i()):\n            name = r.cstr(); t, v = _read_attr(r, None, True); attrs.append([name, t, v])\n        doc.prefix.append(attrs)\n    strtab = [r.cstr() for _ in range(r.i())]\n    n = r.i()\n    for _ in range(n):\n        doc.elements.append({\'type\': strtab[r.i()], \'name\': strtab[r.i()], \'guid\': r.raw(16), \'attrs\': []})\n    for el in doc.elements:\n        for _ in range(r.i()):\n            name = strtab[r.i()]; t, v = _read_attr(r, strtab, False); el[\'attrs\'].append([name, t, v])\n    if r.p != len(d): raise ValueError(\'trailing data %d/%d\' % (r.p, len(d)))\n    return doc\n\ndef load(path): return loads(open(path, \'rb\').read())\n\ndef _w_val(out, t, v, sidx, inline):\n    if t == T_ELEM:\n        if isinstance(v, tuple): out += struct.pack(\'<i\', -2) + v[1] + b\'\\0\'\n        else: out += struct.pack(\'<i\', v)\n    elif t == T_STR: out += (v + b\'\\0\') if inline else struct.pack(\'<i\', sidx[v])\n    elif t == T_BIN: out += struct.pack(\'<i\', len(v)) + v\n    else:\n        f, n = _fix[t]; out += struct.pack(f, *(v if isinstance(v, (list, tuple)) else [v]))\n\ndef _w_attr(out, t, v, sidx, inline):\n    out.append(t)\n    if t > ARR:\n        out += struct.pack(\'<i\', len(v)); bt = t - ARR\n        for x in v:\n            if bt == T_STR: out += x + b\'\\0\'\n            else: _w_val(out, bt, x, sidx, inline)\n    else: _w_val(out, t, v, sidx, inline)\n\ndef dumps(doc):\n    strs = []; sidx = {}\n    def add(s):\n        if s not in sidx: sidx[s] = len(strs); strs.append(s)\n    for el in doc.elements:\n        add(el[\'type\']); add(el[\'name\'])\n        for name, t, v in el[\'attrs\']:\n            add(name)\n            if t == T_STR: add(v)\n    out = bytearray(doc.header + b\'\\0\')\n    out += struct.pack(\'<i\', len(doc.prefix))\n    for attrs in doc.prefix:\n        out += struct.pack(\'<i\', len(attrs))\n        for name, t, v in attrs: out += name + b\'\\0\'; _w_attr(out, t, v, None, True)\n    out += struct.pack(\'<i\', len(strs))\n    for s in strs: out += s + b\'\\0\'\n    out += struct.pack(\'<i\', len(doc.elements))\n    for el in doc.elements: out += struct.pack(\'<ii\', sidx[el[\'type\']], sidx[el[\'name\']]) + el[\'guid\']\n    for el in doc.elements:\n        out += struct.pack(\'<i\', len(el[\'attrs\']))\n        for name, t, v in el[\'attrs\']: out += struct.pack(\'<i\', sidx[name]); _w_attr(out, t, v, sidx, False)\n    return bytes(out)\n\n# ---- helpers ----\ndef get(el, name, default=None):\n    for a in el[\'attrs\']:\n        if a[0] == name: return a[2]\n    return default\ndef geta(el, name):\n    for a in el[\'attrs\']:\n        if a[0] == name: return a\n    return None\ndef set_(el, name, t, v):\n    a = geta(el, name)\n    if a: a[1] = t; a[2] = v\n    else: el[\'attrs\'].append([name, t, v])\ndef delete(el, name): el[\'attrs\'] = [a for a in el[\'attrs\'] if a[0] != name]\n', 'kv2': '"""Parse DMX keyvalues2 text (what Valve\'s importer writes for the main .vmap) into a dmx.Doc,\nso it can be edited and saved as binary 9 like Hammer does."""\nimport re, uuid\nimport dmx\n\nTYPES = {\'element\': 1, \'int\': 2, \'float\': 3, \'bool\': 4, \'string\': 5, \'binary\': 6, \'time\': 7, \'color\': 8, \'vector2\': 9,\n         \'vector3\': 10, \'vector4\': 11, \'qangle\': 12, \'quaternion\': 13, \'matrix\': 14, \'uint64\': 15, \'uint8\': 16}\n_tok = re.compile(r\'"((?:[^"\\\\]|\\\\.)*)"|([{}\\[\\],])|//[^\\n]*|(\\S+)\')\n\ndef _unesc(s): return s.replace(\'\\\\"\', \'"\').replace(\'\\\\n\', \'\\n\').replace(\'\\\\t\', \'\\t\').replace(\'\\\\\\\\\', \'\\\\\')\n\ndef _scalar(t, s):\n    if t == 2: return int(s)\n    if t == 3: return float(s)\n    if t == 4: return s.strip() not in (\'0\', \'false\', \'\')\n    if t == 5: return _unesc(s).encode(\'utf-8\')\n    if t == 6: return bytes.fromhex(\'\'.join(s.split()))\n    if t == 7: return int(round(float(s) * 10000))\n    if t == 8: return [int(x) for x in s.split()]\n    if t in (9, 10, 11, 12, 13, 14): return [float(x) for x in s.split()]\n    if t == 15: return int(s, 16) if s.lower().startswith(\'0x\') else int(s)\n    if t == 16: return int(s)\n    raise ValueError(t)\n\ndef loads(text):\n    if isinstance(text, bytes): text = text.decode(\'utf-8\')\n    nl = text.index(\'\\n\')\n    toks = [(m.group(1) is not None, m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3))) for m in _tok.finditer(text, nl + 1)\n            if m.group(1) is not None or m.group(2) or m.group(3)]\n    pos = [0]\n    def nxt(): t = toks[pos[0]]; pos[0] += 1; return t\n    def peek(): return toks[pos[0]] if pos[0] < len(toks) else (False, None)\n    elements = []; by_id = {}; refs = []   # refs: (element index, attr index, list index or None, guid)\n    def parse_element(etype):\n        q, b = nxt(); assert b == \'{\', b\n        idx = len(elements); el = {\'type\': etype.encode(), \'name\': b\'\', \'guid\': b\'\', \'attrs\': []}; elements.append(el)\n        while True:\n            q, name = nxt()\n            if not q and name == \'}\': break\n            q2, typ = nxt()\n            if typ == \'elementid\':\n                _, v = nxt(); g = uuid.UUID(v).bytes; el[\'guid\'] = g; by_id[g] = idx; continue\n            if typ.endswith(\'_array\'):\n                bt = TYPES[typ[:-6]]; nxt()  # [\n                vals = []\n                while True:\n                    qv, v = nxt()\n                    if not qv and v == \']\': break\n                    if not qv and v == \',\': continue\n                    if bt == 1:\n                        if v == \'element\':\n                            _, g = nxt()\n                            if g: refs.append((idx, len(el[\'attrs\']), len(vals), uuid.UUID(g).bytes)); vals.append(-1)\n                            else: vals.append(-1)\n                        else: vals.append(parse_element(v))\n                    else: vals.append(_scalar(bt, v))\n                el[\'attrs\'].append([name.encode(), bt + 32, vals]); continue\n            if typ in TYPES:\n                t = TYPES[typ]; _, v = nxt()\n                if t == 1:\n                    if v: refs.append((idx, len(el[\'attrs\']), None, uuid.UUID(v).bytes))\n                    el[\'attrs\'].append([name.encode(), 1, -1]); continue\n                if name == \'name\' and t == 5: el[\'name\'] = _unesc(v).encode(\'utf-8\'); continue\n                el[\'attrs\'].append([name.encode(), t, _scalar(t, v)]); continue\n            # inline element: "attr" "ElementType" { ... }\n            el[\'attrs\'].append([name.encode(), 1, parse_element(typ)])\n        return idx\n    tops = []\n    while pos[0] < len(toks):\n        q, etype = nxt(); tops.append(parse_element(etype))\n    for ei, ai, li, g in refs:\n        j = by_id.get(g, -1)\n        if li is None: elements[ei][\'attrs\'][ai][2] = j\n        else: elements[ei][\'attrs\'][ai][2][li] = j\n    # binary layout: root element first, $prefix_element$ second\n    root = next(i for i in tops if elements[i][\'type\'] == b\'CMapRootElement\')\n    pre = [i for i in tops if elements[i][\'type\'] == b\'$prefix_element$\']\n    order = [root] + pre + [i for i in range(len(elements)) if i != root and i not in pre]\n    remap = {o: n for n, o in enumerate(order)}\n    doc = dmx.Doc(); doc.header = b\'<!-- dmx encoding binary 9 format vmap 40 -->\\n\'\n    for o in order:\n        e = elements[o]; attrs = []\n        for n, t, v in e[\'attrs\']:\n            if t == 1: v = remap[v] if v >= 0 else -1\n            elif t == 33: v = [remap[x] if x >= 0 else -1 for x in v]\n            attrs.append([n, t, v])\n        doc.elements.append({**e, \'attrs\': attrs})\n    doc.prefix = [[list(a) for a in doc.elements[1][\'attrs\']]] if pre else []\n    return doc\n', 'vmapedit': '"""Edit Hammer 5 .vmap files (binary DMX) without Hammer."""\nimport os, random, math, copy\nimport dmx\nfrom dmx import T_ELEM, T_INT, T_FLOAT, T_BOOL, T_STR, T_V3, T_QANG, T_U64, ARR\n\ndef guid(): return bytes(random.getrandbits(8) for _ in range(16))\n\nclass Map:\n    def __init__(s, path=None, data=None):\n        s.path = path\n        raw = data if data is not None else open(path, \'rb\').read()\n        if b\'encoding keyvalues2\' in raw[:80]:\n            import kv2; s.doc = kv2.loads(raw)\n        else:\n            s.doc = dmx.loads(raw)\n        s.E = s.doc.elements\n        s.root = next(i for i, e in enumerate(s.E) if e[\'type\'] == b\'CMapRootElement\')\n        s.world = dmx.get(s.E[s.root], b\'world\')\n\n    # ---------- basic element access ----------\n    def g(s, i, k, d=None): return dmx.get(s.E[i], k, d)\n    def type(s, i): return s.E[i][\'type\']\n    def children(s, i): return s.g(i, b\'children\') or []\n    def walk(s, i=None, parent=None):\n        """yield (node, parent) for every node under i (default world), depth first"""\n        i = s.world if i is None else i\n        for c in s.children(i):\n            yield c, i\n            yield from s.walk(c, i)\n    def entities(s):\n        return [n for n, _ in s.walk() if s.type(n) == b\'CMapEntity\']\n    def parent_of(s):\n        return {n: p for n, p in s.walk()}\n\n    # ---------- entity properties ----------\n    def props(s, i): return s.E[s.g(i, b\'entity_properties\')]\n    def P(s, i, k, d=None):\n        v = dmx.get(s.props(i), k.encode() if isinstance(k, str) else k)\n        return d if v is None else v.decode(\'utf-8\', \'replace\')\n    def setp(s, i, k, v):\n        k = k.encode() if isinstance(k, str) else k\n        v = v.encode() if isinstance(v, str) else v\n        dmx.set_(s.props(i), k, T_STR, v)\n    def delp(s, i, k): dmx.delete(s.props(i), k.encode() if isinstance(k, str) else k)\n    def cls(s, i): return s.P(i, \'classname\', \'\')\n    def name(s, i): return s.P(i, \'targetname\', \'\')\n    def origin(s, i): return list(s.g(i, b\'origin\', [0, 0, 0]))\n    def by_class(s, *c): return [e for e in s.entities() if s.cls(e) in c]\n\n    # ---------- connections ----------\n    def conns(s, i): return [s.E[c] for c in s.g(i, b\'connectionsData\') or []]\n    def conn_tuple(s, c):\n        return (dmx.get(c, b\'outputName\').decode(), dmx.get(c, b\'targetName\').decode(), dmx.get(c, b\'inputName\').decode(),\n                dmx.get(c, b\'overrideParam\').decode(), round(dmx.get(c, b\'delay\'), 3), dmx.get(c, b\'timesToFire\'))\n    def add_conn(s, i, out, target, inp, param=\'\', delay=0.0, times=-1):\n        c = s.new_el(b\'DmeConnectionData\', [\n            [b\'outputName\', T_STR, out.encode()], [b\'targetType\', T_INT, 7], [b\'targetName\', T_STR, target.encode()],\n            [b\'inputName\', T_STR, inp.encode()], [b\'overrideParam\', T_STR, param.encode()],\n            [b\'delay\', T_FLOAT, float(delay)], [b\'timesToFire\', T_INT, int(times)]])\n        dmx.geta(s.E[i], b\'connectionsData\')[2].append(c)\n        return c\n    def all_conns(s):\n        for e in s.entities():\n            for c in s.conns(e): yield e, c\n\n    # ---------- creation ----------\n    def new_el(s, typ, attrs, name=b\'\'):\n        s.E.append({\'type\': typ, \'name\': name, \'guid\': guid(), \'attrs\': attrs}); return len(s.E) - 1\n    def next_node_id(s):\n        """1 + the highest nodeID in the map. Remembers the highest found so far and scans only elements added since\n        (nodeIDs are only written when an element is created), not the whole map on every new entity"""\n        seen, m = getattr(s, \'_nid\', (0, 0))\n        if seen > len(s.E): seen, m = 0, 0           # element list replaced (prune): scan it all again\n        for e in s.E[seen:]:\n            v = dmx.get(e, b\'nodeID\')\n            if isinstance(v, int) and v > m: m = v\n        s._nid = (len(s.E), m)\n        return m + 1\n    def new_entity(s, classname, origin, props, angles=(0, 0, 0), parent=None):\n        pin = s.new_el(b\'DmElement\', [[b\'referenceName\', T_STR, b\'\'], [b\'targetReferenceID\', T_U64, 0], [b\'offsetOrigin\', T_V3, [0.0, 0.0, 0.0]],\n                                     [b\'offsetAngles\', T_QANG, [0.0, 0.0, 0.0]], [b\'pinAngles\', T_BOOL, True], [b\'twoWay\', T_BOOL, False]])\n        plug = s.new_el(b\'DmePlugList\', [[b\'names\', 37, []], [b\'dataTypes\', 34, []], [b\'plugTypes\', 34, []], [b\'descriptions\', 37, []]])\n        pr = [[b\'classname\', T_STR, classname.encode()]] + [[k.encode(), T_STR, str(v).encode()] for k, v in props.items()]\n        pe = s.new_el(b\'EditGameClassProps\', pr)\n        e = s.new_el(b\'CMapEntity\', [\n            [b\'nodeID\', T_INT, s.next_node_id()], [b\'referenceID\', T_U64, random.getrandbits(63)], [b\'children\', ARR + T_ELEM, []],\n            [b\'variableTargetKeys\', ARR + T_STR, []], [b\'variableNames\', ARR + T_STR, []], [b\'relayPlugData\', T_ELEM, plug],\n            [b\'connectionsData\', ARR + T_ELEM, []], [b\'entity_properties\', T_ELEM, pe], [b\'hitNormal\', T_V3, [0.0, 0.0, 1.0]],\n            [b\'isProceduralEntity\', T_BOOL, False], [b\'origin\', T_V3, [float(x) for x in origin]], [b\'angles\', T_QANG, [float(x) for x in angles]],\n            [b\'scales\', T_V3, [1.0, 1.0, 1.0]], [b\'transformLocked\', T_BOOL, False], [b\'transformPin\', T_ELEM, pin],\n            [b\'force_hidden\', T_BOOL, False], [b\'editorOnly\', T_BOOL, False], [b\'customVisGroup\', T_STR, b\'\'],\n            [b\'randomSeed\', T_INT, random.randint(1, 2**31 - 1)]])\n        dmx.geta(s.E[parent if parent is not None else s.world], b\'children\')[2].append(e)\n        return e\n    def remove_node(s, i):\n        for n, p in list(s.walk()):\n            if n == i:\n                a = dmx.geta(s.E[p], b\'children\'); a[2] = [x for x in a[2] if x != i]; return True\n        return False\n\n    # ---------- copying between maps ----------\n    def import_subtree(s, other, i, memo, node_id):\n        """deep-copy element i (and everything it references) from Map `other` into this map"""\n        if i in memo: return memo[i]\n        src = other.E[i]\n        if src[\'type\'] in (b\'CMapRootElement\', b\'CMapWorld\'):\n            raise ValueError(\'refusing to copy root/world\')\n        j = len(s.E); memo[i] = j\n        s.E.append({\'type\': src[\'type\'], \'name\': src[\'name\'], \'guid\': guid(), \'attrs\': []})\n        attrs = []\n        for name, t, v in src[\'attrs\']:\n            if t == T_ELEM and isinstance(v, int) and v >= 0: v = s.import_subtree(other, v, memo, node_id)\n            elif t == ARR + T_ELEM: v = [s.import_subtree(other, x, memo, node_id) if isinstance(x, int) and x >= 0 else x for x in v]\n            elif name == b\'nodeID\' and t == T_INT: v = node_id[0]; node_id[0] += 1\n            elif isinstance(v, list): v = list(v)\n            attrs.append([name, t, v])\n        s.E[j][\'attrs\'] = attrs\n        return j\n\n    # ---------- bounds ----------\n    def mesh_points(s, mesh_node):\n        """world-space vertex positions of a CMapMesh (mesh nodes keep local verts + node origin)"""\n        md = s.g(mesh_node, b\'meshData\')\n        if md is None: return []\n        vdata = s.g(md, b\'vertexData\')\n        o = s.g(mesh_node, b\'origin\', [0, 0, 0]); ang = s.g(mesh_node, b\'angles\', [0, 0, 0]); sc = s.g(mesh_node, b\'scales\', [1, 1, 1])\n        pts = []\n        for st in s.g(vdata, b\'streams\') or []:\n            if s.E[st][\'name\'].startswith(b\'position\'):\n                for p in s.g(st, b\'data\'):\n                    pts.append(xform([p[0] * sc[0], p[1] * sc[1], p[2] * sc[2]], o, ang))\n        return pts\n    def bounds(s, node):\n        pts = []\n        stack = [node]\n        while stack:\n            n = stack.pop()\n            if s.type(n) == b\'CMapMesh\': pts += s.mesh_points(n)\n            stack += s.children(n)\n        if not pts: return None\n        return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]\n\n    # ---------- save ----------\n    def prune(s):\n        """drop elements no longer reachable from the root / prefix; returns how many were removed"""\n        keep = set(); stack = [s.root]\n        stack += [i for i, e in enumerate(s.E) if e[\'type\'] == b\'$prefix_element$\']\n        while stack:\n            i = stack.pop()\n            if i in keep: continue\n            keep.add(i)\n            for _, t, v in s.E[i][\'attrs\']:\n                if t == T_ELEM and isinstance(v, int) and v >= 0: stack.append(v)\n                elif t == ARR + T_ELEM: stack += [x for x in v if isinstance(x, int) and x >= 0]\n        order = [i for i in range(len(s.E)) if i in keep]\n        remap = {o: n for n, o in enumerate(order)}\n        newE = []\n        for i in order:\n            e = s.E[i]; attrs = []\n            for name, t, v in e[\'attrs\']:\n                if t == T_ELEM and isinstance(v, int) and v >= 0: v = remap[v]\n                elif t == ARR + T_ELEM: v = [remap[x] if isinstance(x, int) and x >= 0 else x for x in v]\n                attrs.append([name, t, v])\n            newE.append({**e, \'attrs\': attrs})\n        removed = len(s.E) - len(newE)\n        s.doc.elements = s.E = newE; s.root = remap[s.root]; s.world = remap[s.world]\n        s._nid = (0, 0)\n        return removed\n    def dumps(s):\n        out = dmx.dumps(s.doc); dmx.loads(out); return out\n    def save(s, path):\n        out = s.dumps(); os.makedirs(os.path.dirname(path), exist_ok=True); open(path, \'wb\').write(out); return len(out)\n\n# ---------- math ----------\ndef rot_matrix(ang):\n    p, y, r = [math.radians(a) for a in ang]\n    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)\n    # Source QAngle (pitch, yaw, roll) -> forward/left/up columns\n    return [[cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy],\n            [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy],\n            [-sp, sr * cp, cr * cp]]\ndef xform(p, o, ang):\n    if not any(ang): return [p[0] + o[0], p[1] + o[1], p[2] + o[2]]\n    m = rot_matrix(ang)\n    return [m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + o[r] for r in range(3)]\ndef angle_fwd(ang):\n    m = rot_matrix(ang); return [m[0][0], m[1][0], m[2][0]]\n'}
# The lighting passes (stationary lights, light probes, vis hints, large-face splitting), bundled from rebuild_lighting.py /
# skink52's cs2portdocs tools. They need numpy + scipy and are only loaded by the lighting rebuild and the radar.
_BUNDLED_VMAP_MODULES['lighting'] = '"""Stationary (dynamic) light pass.\n\nCS:GO lights import as Static (directlight 1, baked only), so weapons / players render black. This picks a minimal set of lights\nper sealed compartment and switches them to directlight 3 (Stationary: dynamic direct light, baked shadows; max 4 may overlap), with ranges trimmed so no point in the map is\ninside more than MAX_OVERLAP stationary lights. Compartments come from voxelising the world geometry and flood-filling\nthe empty space; compartments that see sky (moondome / skybox) count as lit by light_environment when the map has one.\n"""\nimport collections, math\nimport numpy as np\nimport dmx\n\nTOOL_PASS = (\'materials/tools/toolstrigger\', \'materials/tools/toolsclip\', \'materials/tools/toolsplayerclip\', \'materials/tools/toolsnpcclip\',\n             \'materials/tools/toolsinvisible\', \'materials/tools/toolsinvisibleladder\', \'materials/tools/toolsareaportal\', \'materials/tools/toolsoccluder\',\n             \'materials/tools/toolshint\', \'materials/tools/toolsskip\', \'materials/tools/toolsfog\', \'materials/tools/toolslight\', \'materials/tools/toolsblocklight\',\n             \'materials/tools/toolsblock_los\', \'materials/tools/toolsbuyzone\', \'materials/tools/toolsbombsite\')\nMAX_OVERLAP = 4\nLIGHT_CLASSES = (\'light_omni2\', \'light_spot\', \'light_barn\', \'light_rect\', \'light_capsule\', \'light_omni\', \'light_ortho\')\n\ndef mesh_polys(m, node):\n    """[(material, [p0, p1, ...])] for a CMapMesh, world space"""\n    md = m.g(node, b\'meshData\')\n    if md is None: return []\n    E = m.E\n    get = lambda k: dmx.get(E[md], k)\n    vdi, evi, eni, fei, fdi = get(b\'vertexDataIndices\'), get(b\'edgeVertexIndices\'), get(b\'edgeNextIndices\'), get(b\'faceEdgeIndices\'), get(b\'faceDataIndices\')\n    mats = [x.decode().lower() for x in get(b\'materials\') or []]\n    pos = None; matidx = None\n    for st in dmx.get(E[get(b\'vertexData\')], b\'streams\'):\n        if E[st][\'name\'].startswith(b\'position\'): pos = dmx.get(E[st], b\'data\')\n    for st in dmx.get(E[get(b\'faceData\')], b\'streams\'):\n        if E[st][\'name\'].startswith(b\'materialindex\'): matidx = dmx.get(E[st], b\'data\')\n    if pos is None or not fei: return []\n    o = m.g(node, b\'origin\', [0, 0, 0])\n    out = []\n    for f, e0 in enumerate(fei):\n        loop = []; e = e0\n        for _ in range(64):\n            v = evi[e]; p = pos[vdi[v]]; loop.append((p[0] + o[0], p[1] + o[1], p[2] + o[2]))\n            e = eni[e]\n            if e == e0: break\n        mi = matidx[fdi[f]] if matidx else 0\n        out.append((mats[mi] if mi < len(mats) else \'\', loop))\n    return out\n\ndef world_polys(m):\n    """polygons of world geometry (meshes not inside entities)"""\n    polys = []\n    def walk(n):\n        for c in m.children(n):\n            t = m.type(c)\n            if t == b\'CMapMesh\': polys.extend(mesh_polys(m, c))\n            elif t == b\'CMapGroup\': walk(c)\n    walk(m.world)\n    return polys\n\ndef voxelise(polys, vs):\n    pts_all = np.array([p for _, l in polys for p in l], np.float64)\n    lo = pts_all.min(0) - 2 * vs; hi = pts_all.max(0) + 2 * vs\n    shape = tuple(int(x) for x in np.ceil((hi - lo) / vs))\n    solid = np.zeros(shape, np.uint8)   # 1 wall, 2 sky\n    for mat, loop in polys:\n        if mat.startswith(TOOL_PASS): continue\n        kind = 2 if (\'moondome\' in mat or \'toolsskybox\' in mat) else 1\n        P = np.array(loop, np.float64)\n        for i in range(1, len(P) - 1):\n            a, b, c = P[0], P[i], P[i + 1]\n            n = int(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / (vs * 0.4)) + 2\n            u, w = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))\n            k = (u + w) <= 1\n            q = a + np.outer(u[k], b - a) + np.outer(w[k], c - a)\n            idx = np.floor((q - lo) / vs).astype(int)\n            idx = np.clip(idx, 0, np.array(shape) - 1)\n            cur = solid[idx[:, 0], idx[:, 1], idx[:, 2]]\n            solid[idx[:, 0], idx[:, 1], idx[:, 2]] = np.maximum(cur, kind)\n    return solid, lo\n\ndef label_empty(solid):\n    from scipy import ndimage\n    lab, n = ndimage.label(solid == 0)\n    return lab, n\n\ndef run(m, c, vs=32.0):\n    lights = m.by_class(*LIGHT_CLASSES)\n    if not lights: return\n    polys = world_polys(m)\n    if not polys: return\n    solid, lo = voxelise(polys, vs)\n    lab, n = label_empty(solid)\n    shape = np.array(solid.shape)\n    exterior = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(), lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])))\n    from scipy import ndimage\n    sky = ndimage.binary_dilation(solid == 2)\n    sky_labels = set(np.unique(lab[sky & (lab > 0)]))\n    has_env = bool(m.by_class(\'light_environment\'))\n    def vox(p): return tuple(np.clip(np.floor((np.array(p) - lo) / vs).astype(int), 0, shape - 1))\n    def region_of(p):\n        i = vox(p)\n        if lab[i]: return lab[i]\n        # light sits in a wall voxel: nearest empty voxel within 2 cells\n        best = None\n        for d in [(dx, dy, dz) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2)]:\n            j = tuple(np.clip(np.array(i) + d, 0, shape - 1))\n            if lab[j] and (best is None or sum(x * x for x in d) < best[0]): best = (sum(x * x for x in d), lab[j])\n        return best[1] if best else 0\n    by_region = collections.defaultdict(list)\n    for e in lights:\n        by_region[region_of(m.origin(e))].append(e)\n    # region voxel centres (subsampled) for coverage / overlap checks\n    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(n + 1))\n    centres = {}\n    for r in by_region:\n        if r == 0 or r in exterior: continue\n        idx = np.argwhere(lab == r)\n        if len(idx) > 4000: idx = idx[np.random.RandomState(r).choice(len(idx), 4000, replace=False)]\n        centres[r] = lo + (idx + 0.5) * vs\n    chosen = []            # (entity, pos, range)\n    all_samples = np.concatenate(list(centres.values())) if centres else np.zeros((0, 3))\n    overlap = np.zeros(len(all_samples), np.int32)\n    offs = {}; k = 0\n    for r, pts in centres.items(): offs[r] = (k, k + len(pts)); k += len(pts)\n    already = [e for e in lights if m.P(e, \'directlight\') in (\'2\', \'3\')]\n    for e in already:\n        p = np.array(m.origin(e)); rg = float(m.P(e, \'range\', \'512\') or 512)\n        overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n    # rooms players can reach (spawns / teleport targets) with no light at all get a new stationary light first\n    play = collections.Counter(region_of(m.origin(e)) for e in m.by_class(\'info_player_terrorist\', \'info_player_counterterrorist\', \'info_teleport_destination\'))\n    unlit = [r for r in play if r and r not in exterior and r not in by_region and sizes[r] >= 8]\n    tmpl = next(iter(m.by_class(\'light_omni2\')), None)\n    added = 0\n    for r in unlit:\n        idx = np.argwhere(lab == r)\n        want = np.array([idx[:, 0].mean(), idx[:, 1].mean(), idx[:, 2].max() - 1])\n        v = idx[np.argmin(((idx - want) ** 2).sum(1))]\n        p = lo + (v + 0.5) * vs\n        rg = float(np.linalg.norm((idx - v) * vs, axis=1).max()) + 64\n        while rg > 128 and (overlap[np.linalg.norm(all_samples - p, axis=1) < rg] >= MAX_OVERLAP).any(): rg *= 0.85\n        props = {a[0].decode(): a[2].decode() for a in m.props(tmpl)[\'attrs\'] if a[0] not in (b\'classname\', b\'targetname\')} if tmpl else {}\n        props.update({\'directlight\': \'3\', \'range\': \'%.0f\' % rg, \'enabled\': \'1\', \'color\': \'255 255 255\'})\n        m.new_entity(\'light_omni2\', p, props); added += 1\n        overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n    if added: c.log.append(\'added %d stationary light_omni2 to sealed rooms with spawns / teleport destinations that had no light at all\' % added)\n    report = {}; skipped_sky = 0\n    MIN_VOX = 64                                          # smaller pockets (lamp housings, vents) don\'t count as rooms\n    regions = [r for r in by_region if r and r not in exterior and r in centres and sizes[r] >= MIN_VOX]\n    if has_env: skipped_sky = sum(1 for r in regions if r in sky_labels); regions = [r for r in regions if r not in sky_labels]\n    regions = [r for r in regions if not any(e in already for e in by_region[r])]\n    covered = {r: np.zeros(len(centres[r]), bool) for r in regions}\n    def fit(p, rg):\n        while rg >= 128:\n            if not (overlap[np.linalg.norm(all_samples - p, axis=1) < rg] >= MAX_OVERLAP).any(): return rg\n            rg *= 0.85\n        return None\n    def take(r, e, p, rg):\n        nonlocal overlap\n        chosen.append((e, p, rg)); overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n        covered[r] |= np.linalg.norm(centres[r] - p, axis=1) < rg\n    # pass 1: every room gets one light, smallest rooms first so the big ones can\'t crowd them out\n    for r in sorted(regions, key=lambda r: sizes[r]):\n        pts = centres[r]; best = None\n        for e in by_region[r]:\n            p = np.array(m.origin(e)); rg0 = float(m.P(e, \'range\', \'512\') or 512)\n            d = np.linalg.norm(pts - p, axis=1)\n            rg = fit(p, max(min(rg0, float(np.percentile(d, 95)) + 64), 192.0))\n            if rg is None: continue\n            gain = int((d < rg).sum())\n            if best is None or gain > best[0]: best = (gain, e, p, rg)\n        if best: take(r, best[1], best[2], best[3])\n        report[r] = 1 if best else 0\n    # pass 2: top up rooms that are still mostly uncovered\n    for r in sorted(regions, key=lambda r: covered[r].mean()):\n        pts = centres[r]\n        while report[r] and report[r] < 6 and covered[r].mean() < 0.85:\n            best = None\n            for e in by_region[r]:\n                if any(e is x[0] for x in chosen): continue\n                p = np.array(m.origin(e)); rg0 = float(m.P(e, \'range\', \'512\') or 512)\n                d = np.linalg.norm(pts - p, axis=1); left = d[~covered[r]]\n                if not len(left): continue\n                rg = fit(p, max(min(rg0, float(np.percentile(left, 90)) + 64), 192.0))\n                if rg is None: continue\n                gain = int(((d < rg) & ~covered[r]).sum())\n                if best is None or gain > best[0]: best = (gain, e, p, rg)\n            if best is None or best[0] < 0.08 * len(pts): break\n            take(r, best[1], best[2], best[3]); report[r] += 1\n    report = [(r, len(by_region[r]), report[r], round(float(covered[r].mean()), 2)) for r in regions]\n    for e, p, rg in chosen:\n        m.setp(e, \'directlight\', \'3\')\n        old = float(m.P(e, \'range\', \'0\') or 0)\n        if rg < old - 1: m.setp(e, \'range\', \'%.0f\' % rg)\n    none = [x for x in report if x[2] == 0]\n    if none: c.todo.append(\'%d compartments could not get a stationary light without going over %d overlapping - check them\' % (len(none), MAX_OVERLAP))\n    c.log.append(\'stationary lights: %d of %d lights switched Static -> Stationary (directlight 3) across %d sealed compartments, ranges trimmed so no spot is inside more than %d%s\'\n                 % (len(chosen), len(lights), len(report), MAX_OVERLAP, \'; %d compartments open to sky left to light_environment\' % skipped_sky if skipped_sky else \'\'))\n    weak = [x for x in report if x[3] < 0.6]\n    if weak: c.todo.append(\'%d compartments only partly covered by stationary light (overlap cap) - check weapons are lit there\' % len(weak))\n    return report\n'
_BUNDLED_VMAP_MODULES['visfit'] = '"""Vis compile time: hint boxes fitted to the play space, and sky shells out of vis where nothing needs them.\n\nThe CS2 vis compile (VIS3) spends most of its time clustering, sampling line of sight and merging clusters down to the 4,096 cap.\nHow many clusters it starts with depends on the visibility_hint that governs each part of the map (where hints overlap, the\nhighest resolution wins). So hints should only be fine where players can actually stand.\n\n1. Playable air: empty space at VS-unit voxels (world meshes that block players, incl. clips, detail and sky shells) connected\n   to a spawn or teleport destination, plus sealed compartments that hold gameplay entities (buttons, doors, triggers, trains,\n   weapons). The open void around the map never counts.\n2. Walkable band: playable air up to BAND units above something to stand on (world or entity meshes).\n3. Hints, on a 256-unit grid so they line up with the Lowest regions, Low and Lower never overlapping:\n     Low    (hintType 3, 64-unit grid)                         cells holding walkable band\n     Lower  (hintType 8, 64-unit grid, fewer initial clusters)  other playable air next to geometry (high ceilings, shafts)\n     Lowest (hintType 9, 256-unit grid)                         one box over the whole map: void, sealed-off rooms, open sky\n   Open sky far above an outdoor area gets no box of its own, so it falls to Lowest. all_lower=True makes the Low cells Lower too\n   (for maps whose vis merge stalls, e.g. mg_egypt_course). Boxes are merged while at least 75% (then 60%, 50%) of a box is its\n   own cells, aiming for at most 100 boxes.\n4. Sky shells (all-moondome / all-toolsskybox meshes) get Exclude from Vis unless they border playable air that is open once\n   every sky shell is gone. That test uses only vis-relevant geometry: Valve\'s docs say toolsnodraw is ignored by vis and by\n   its inside/outside test, and clips, triggers and meshes already excluded from vis don\'t count either.\n5. Optional extra_visex_box (world box): every world mesh entirely inside it is excluded from vis too (an unreachable\n   ex-3D-skybox, for example).\nExisting visibility_hint entities are replaced.\n"""\nimport collections\nimport numpy as np\nfrom scipy import ndimage\nimport dmx\nfrom lighting import mesh_polys\n\nPLAY = (\'info_player_counterterrorist\', \'info_player_terrorist\', \'info_player_start\', \'info_teleport_destination\')\nACTIVE = (\'func_button\', \'func_rot_button\', \'func_door\', \'func_door_rotating\', \'func_movelinear\', \'trigger_multiple\', \'trigger_once\',\n          \'trigger_teleport\', \'game_player_equip\', \'point_teleport\', \'func_tracktrain\', \'func_rotating\', \'func_physbox\')\nPHYS_PASS = (\'materials/tools/toolstrigger\', \'materials/tools/toolshint\', \'materials/tools/toolsskip\', \'materials/tools/toolsfog\',\n             \'materials/tools/toolslight\', \'materials/tools/toolsblocklight\', \'materials/tools/toolsblock_los\', \'materials/tools/toolsbuyzone\',\n             \'materials/tools/toolsbombsite\', \'materials/tools/toolsareaportal\', \'materials/tools/toolsoccluder\', \'materials/tools/toolsvisblocker\')\nCELL = 256.0\nBAND = 160.0\nCODE = {\'Low\': \'3\', \'Lower\': \'8\', \'Lowest\': \'9\'}\n\ndef is_shell(mats):\n    return bool(mats) and all(\'moondome\' in x or x == \'materials/tools/toolsskybox.vmat\' for x in mats)\n\ndef vis_relevant(mat):\n    if \'moondome\' in mat: return True\n    if mat.startswith(\'materials/tools/\'):\n        return mat.startswith((\'materials/tools/toolsskybox\', \'materials/tools/toolssolidblocklight\', \'materials/tools/toolsvisblocker\'))\n    return True\n\ndef meshes(m):\n    par = {n: p for n, p in m.walk()}\n    def in_ent(n):\n        p = par.get(n)\n        while p is not None:\n            if m.type(p) == b\'CMapEntity\': return p\n            p = par.get(p)\n        return None\n    out = []\n    for n, _ in m.walk():\n        if m.type(n) != b\'CMapMesh\': continue\n        pl = mesh_polys(m, n); mats = {x for x, _ in pl}\n        a = dmx.geta(m.E[n], b\'visexclude\')\n        out.append(dict(n=n, ent=in_ent(n), polys=pl, mats=mats, shell=is_shell(mats), visex=bool(a and a[2])))\n    return out\n\ndef raster(polys, lo, shape, vs, grid):\n    for L in polys:\n        L = np.asarray(L, np.float64)\n        if (np.floor((L.max(0) - lo) / vs) < 0).any() or (np.floor((L.min(0) - lo) / vs) >= shape).any(): continue\n        for i in range(1, len(L) - 1):\n            a, b, c = L[0], L[i], L[i + 1]\n            k = int(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / (vs * 0.4)) + 2\n            u, w = np.meshgrid(np.linspace(0, 1, k), np.linspace(0, 1, k)); s = (u + w) <= 1\n            q = a + np.outer(u[s], b - a) + np.outer(w[s], c - a)\n            idx = np.floor((q - lo) / vs).astype(int)\n            idx = idx[((idx >= 0) & (idx < shape)).all(1)]\n            grid[idx[:, 0], idx[:, 1], idx[:, 2]] = True\n\ndef _border(lab):\n    return set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),\n                                         lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))) - {0}\n\ndef boxes_from_cells(cells):\n    left = set(cells); out = []\n    while left:\n        i, j, k = min(left)\n        x1 = i\n        while (x1 + 1, j, k) in left: x1 += 1\n        y1 = j\n        while all((x, y1 + 1, k) in left for x in range(i, x1 + 1)): y1 += 1\n        z1 = k\n        while all((x, y, z1 + 1) in left for x in range(i, x1 + 1) for y in range(j, y1 + 1)): z1 += 1\n        for x in range(i, x1 + 1):\n            for y in range(j, y1 + 1):\n                for z in range(k, z1 + 1): left.discard((x, y, z))\n        out.append(((i, j, k), (x1 + 1, y1 + 1, z1 + 1)))\n    return out\n\ndef merge_boxes(boxes, own, other, fill=0.75):\n    """join boxes whose union is mostly own cells and holds none of the other hint type\'s cells (boxes stay disjoint)"""\n    boxes = [(tuple(a), tuple(b)) for a, b in boxes]\n    if len(boxes) < 2: return boxes\n    allc = np.array(list(own | other))\n    o = allc.min(0); sz = allc.max(0) - o + 1\n    def cum(cs):\n        g = np.zeros(sz + 1, np.int32)\n        for c in cs: g[tuple(np.array(c) - o + 1)] = 1\n        return g.cumsum(0).cumsum(1).cumsum(2)\n    O, T = cum(own), cum(other)\n    def cnt(S, a, b):\n        x0, y0, z0 = np.array(a) - o; x1, y1, z1 = np.array(b) - o\n        return int(S[x1, y1, z1] - S[x0, y1, z1] - S[x1, y0, z1] - S[x1, y1, z0] + S[x0, y0, z1] + S[x0, y1, z0] + S[x1, y0, z0] - S[x0, y0, z0])\n    while len(boxes) > 1:\n        A = np.array([bx[0] for bx in boxes]); B = np.array([bx[1] for bx in boxes])\n        best = None\n        for i in range(len(boxes)):\n            ua = np.minimum(A[i], A[i + 1:]); ub = np.maximum(B[i], B[i + 1:])\n            vols = np.prod(ub - ua, axis=1)          # every candidate\'s volume in one call\n            for a, b, v in zip(ua, ub, vols):\n                v = int(v)\n                if v > 4096: continue\n                n = cnt(O, a, b)\n                if n / v < fill or (best and n / v <= best[0]) or cnt(T, a, b): continue\n                ins = np.all((A >= a) & (B <= b), 1); ovl = np.all((A < b) & (B > a), 1)\n                if (ovl & ~ins).any(): continue\n                best = (n / v, tuple(a), tuple(b), ins)\n        if not best: break\n        _, a, b, ins = best\n        boxes = [bx for k, bx in enumerate(boxes) if not ins[k]] + [(a, b)]\n    return boxes\n\ndef plan(m, all_lower=False, extra_visex_box=None, vs=None, margin=256.0):\n    ms = meshes(m)\n    world = [x for x in ms if x[\'ent\'] is None]\n    allp = np.array([p for x in world for _, l in x[\'polys\'] for p in l])\n    lo = allp.min(0) - margin; hi = allp.max(0) + margin\n    if vs is None:     # keep the grids within the cloud workspace\'s memory\n        vs = next(v for v in (16.0, 24.0, 32.0, 48.0) if np.prod(np.ceil((hi - lo) / v)) < 1.2e8)\n    shape = np.array([int(v) for v in np.ceil((hi - lo) / vs)])\n    phys = np.zeros(shape, bool); visg = np.zeros(shape, bool); ent = np.zeros(shape, bool); sh = np.zeros(shape, np.int32)\n    raster([l for x in world for mt, l in x[\'polys\'] if not mt.startswith(PHYS_PASS)], lo, shape, vs, phys)\n    raster([l for x in world if not x[\'visex\'] and not x[\'shell\'] for mt, l in x[\'polys\'] if vis_relevant(mt)], lo, shape, vs, visg)\n    raster([l for x in ms if x[\'ent\'] is not None for mt, l in x[\'polys\'] if not mt.startswith(PHYS_PASS)], lo, shape, vs, ent)\n    shells = [x for x in world if x[\'shell\']]\n    for si, x in enumerate(shells, 1):\n        g = np.zeros(shape, bool); raster([l for _, l in x[\'polys\']], lo, shape, vs, g); sh[g] = si\n    lab, _ = ndimage.label(~(phys | (sh > 0)))\n    def comp_at(p, r=3):\n        i = np.floor((np.array(p, float) - lo) / vs).astype(int)\n        for rr in range(r + 1):\n            v = lab[tuple(slice(max(0, i[k] - rr), i[k] + rr + 1) for k in range(3))]; v = v[v > 0]\n            if v.size: return int(np.bincount(v).argmax())\n        return 0\n    seeds = {comp_at(np.array(m.origin(e), float) + [0, 0, 36]) for e in m.by_class(*PLAY)} - {0}\n    act = {comp_at(m.origin(e), 2) for e in m.entities() if m.cls(e) in ACTIVE or m.cls(e).startswith(\'weapon_\')} - {0}\n    border = _border(lab)\n    comps = (seeds | act) - border\n    P = np.isin(lab, list(comps))\n    del lab\n    solid = phys | (sh > 0) | ent\n    band = np.zeros(shape, bool)\n    for k in range(1, int(BAND / vs) + 1):\n        band[:, :, k:] |= solid[:, :, :-k]\n    band &= P\n    del solid\n    def cells_of(mask):\n        idx = np.argwhere(mask)\n        return set(map(tuple, np.floor((lo + (idx + 0.5) * vs) / CELL).astype(int)))\n    pc, bc = cells_of(P), cells_of(band)\n    geo = cells_of((phys & (sh == 0)) | visg | ent)\n    low = set(bc); lower = (pc - low) & geo\n    if all_lower: lower |= low; low = set()\n    for fill in (0.75, 0.6, 0.5):\n        hints = [(\'Low\', a, b) for a, b in merge_boxes(boxes_from_cells(low), low, lower, fill)] + \\\n                [(\'Lower\', a, b) for a, b in merge_boxes(boxes_from_cells(lower), lower, low, fill)]\n        if len(hints) <= 100: break\n    hints = [(t, np.array(a) * CELL, np.array(b) * CELL) for t, a, b in hints]\n    hints.append((\'Lowest\', np.floor(allp.min(0) / 256) * 256 - 256, np.ceil(allp.max(0) / 256) * 256 + 256))\n    # sky shells: keep the ones bordering playable air that is open once all sky shells are gone\n    vl, _ = ndimage.label(~visg)\n    leaky = P & np.isin(vl, list(_border(vl)))\n    del vl\n    near_leak = ndimage.binary_dilation(leaky, iterations=2)\n    keep_ids = set(np.unique(sh[near_leak & (sh > 0)])) - {0}\n    exclude = [x for si, x in enumerate(shells, 1) if si not in keep_ids and not x[\'visex\']]\n    extra = []\n    if extra_visex_box is not None:\n        a, b = np.array(extra_visex_box[0], float), np.array(extra_visex_box[1], float)\n        for x in world:\n            if x[\'visex\'] or x[\'shell\']: continue\n            bb = m.bounds(x[\'n\'])\n            if bb and (np.array(bb[0]) >= a).all() and (np.array(bb[1]) <= b).all(): extra.append(x)\n    stats = dict(vs=vs, play=P.sum() * vs ** 3 / 1e9, band=band.sum() * vs ** 3 / 1e9, shells=len(shells), kept=len(shells) - len(exclude),\n                 hints=dict(collections.Counter(h[0] for h in hints)))\n    return dict(hints=hints, exclude=exclude, extra=extra, stats=stats)\n\ndef apply(m, pl):\n    for e in m.by_class(\'visibility_hint\'): m.remove_node(e)\n    for t, mn, mx in pl[\'hints\']:\n        o = (mn + mx) / 2\n        m.new_entity(\'visibility_hint\', o, {\'box_mins\': \' \'.join(\'%g\' % v for v in mn - o), \'box_maxs\': \' \'.join(\'%g\' % v for v in mx - o),\n                                            \'hintType\': CODE[t]})\n    for x in pl[\'exclude\'] + pl[\'extra\']:\n        a = dmx.geta(m.E[x[\'n\']], b\'visexclude\')\n        if a: a[2] = True\n        else: dmx.set_(m.E[x[\'n\']], b\'visexclude\', dmx.T_BOOL, True)\n\ndef run(m, c, all_lower=False, extra_visex_box=None):\n    old = collections.Counter({\'3\': \'Low\', \'8\': \'Lower\', \'9\': \'Lowest\', \'2\': \'Medium\', \'0\': \'High\', \'1\': \'Normal\', \'10\': \'Reduced\'}\n                              .get(m.P(e, \'hintType\', \'\'), \'?\') for e in m.by_class(\'visibility_hint\'))\n    pl = plan(m, all_lower, extra_visex_box)\n    apply(m, pl)\n    s = pl[\'stats\']; h = s[\'hints\']\n    c.log.append(\'visfit (%g-unit voxels): %.1f billion cubic units of playable air, %.1f of it walkable band; hints %d Low, %d Lower, %d Lowest%s\'\n                 % (s[\'vs\'], s[\'play\'], s[\'band\'], h.get(\'Low\', 0), h.get(\'Lower\', 0), h.get(\'Lowest\', 0),\n                    (\' (replacing \' + \', \'.join(\'%d %s\' % (v, k) for k, v in old.items()) + \')\') if old else \'\'))\n    if s[\'shells\']:\n        c.log.append(\'visfit: %d of %d sky shell meshes set to Exclude from Vis, %d kept because they close playable air\'\n                     % (len(pl[\'exclude\']), s[\'shells\'], s[\'kept\']))\n    if pl[\'extra\']: c.log.append(\'visfit: %d meshes inside %s set to Exclude from Vis\' % (len(pl[\'extra\']), extra_visex_box))\n    c.todo.append(\'vis: if anything pops in or out of view where players stand, change the nearest Lower hint to Low (or Low to Medium)\')\n    return pl\n'
_BUNDLED_VMAP_MODULES['probes'] = '"""Light probe pass: env_combined_light_probe_volume fitted to each playable compartment.\n\nPlayers, weapons and props get their indirect light from light probe volumes; without them they render dark.\nUnlike daylight.py (one slab per 2048 units along the map\'s longest axis), this fits volumes to the actual\nempty space of each compartment a player can reach, split into tiles of at most TILE units so no single\nvolume covers a huge empty bounding box.\n\nPlayable compartments: empty-space regions (world geometry voxelised and flood-filled, as in lighting.py)\nholding a spawn or teleport destination. The open void outside the map never counts.\nExisting probe volumes are left alone unless replace=True.\n"""\nimport numpy as np\nfrom lighting import world_polys, mesh_polys, voxelise, label_empty\n\nSEEDS = (\'info_player_counterterrorist\', \'info_player_terrorist\', \'info_player_start\', \'info_teleport_destination\')\nPROBE_CLASSES = (\'env_combined_light_probe_volume\', \'env_light_probe_volume\')\nTILE = 2048.0      # max volume edge length\nPAD = 48.0         # extra space around the compartment so probes cover the walls\nMIN_VOX = 8        # ignore tiny pockets\n\nPROPS = dict(StartDisabled=0, cubemaptexture=\'\', bakenearz=\'2.0\', bakefarz=\'4096.0\', lightgroup=\'\', moveable=0,\n             lightprobetexture=\'\', voxel_size=\'48.0\', flood_fill=1, voxelize=1, light_probe_volume_from_cubemap=0,\n             storage=-1, indoor_outdoor_level=0, edge_fade_dists=\'0 32 0\', clientSideEntity=1)\n\n\ndef run(m, c, vs=32.0, replace=False, wall_classes=()):\n    """wall_classes: brush entities (e.g. func_breakable) that count as walls: compartments are split at them (a room reached only\n    through one still gets its volume) and a volume side that meets one stops at its face instead of padding into it."""\n    existing = m.by_class(*PROBE_CLASSES)\n    if existing and not replace:\n        c.log.append(\'probes: map already has %d light probe volumes - left alone (run the probes_replace pass to refit them)\' % len(existing))\n        return\n    polys = world_polys(m)\n    if not polys:\n        c.todo.append(\'probes: no world geometry found - no probe volumes added\')\n        return\n    walls = [e for e in m.entities() if m.cls(e) in wall_classes]\n    wpolys = [p for e in walls for ch in m.children(e) if m.type(ch) == b\'CMapMesh\' for p in mesh_polys(m, ch)]\n    wboxes = [b for b in (m.bounds(e) for e in walls) if b]\n    solid, lo = voxelise(polys + [(\'materials/tools/toolstrigger\', l) for _, l in wpolys], vs)   # same grid as below, walls left out\n    lab, _ = label_empty(solid)\n    shape = np.array(lab.shape)\n\n    def border_of(lab):\n        return set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),\n                                             lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))) - {0}\n    border = border_of(lab)\n\n    def region_at(p, r=2):\n        i = np.clip(np.floor((np.asarray(p, float) - lo) / vs).astype(int), 0, shape - 1)\n        for rr in range(r + 1):\n            v = lab[tuple(slice(max(0, i[k] - rr), i[k] + rr + 1) for k in range(3))]\n            v = v[v > 0]\n            if v.size: return int(np.bincount(v).argmax())\n        return 0\n\n    regions, leaked = set(), 0\n    for e in m.by_class(*SEEDS):\n        r = region_at(np.array(m.origin(e), float) + [0, 0, 36])\n        if r in border: leaked += 1\n        elif r: regions.add(r)\n    if leaked:\n        c.todo.append(\'probes: %d spawns / teleport destinations sit in space connected to the outside of the map (a leak?) - no probes there\' % leaked)\n    if not regions:\n        c.todo.append(\'probes: no sealed playable compartments found - no probe volumes added\')\n        return\n    if wpolys:                                       # split the playable space again with the walls in\n        playable = np.isin(lab, list(regions))\n        del lab\n        solid, lo2 = voxelise(polys + wpolys, vs)\n        lab, _ = label_empty(solid)\n        border = border_of(lab)\n        regions = set(int(x) for x in np.unique(lab[playable & (lab > 0)])) - border\n        del playable\n\n    for e in existing: m.remove_node(e)\n    n_vol = n_snap = 0\n    for r in sorted(regions):\n        idx = np.argwhere(lab == r)\n        if len(idx) < MIN_VOX: continue\n        P = lo + (idx + 0.5) * vs\n        tile = np.floor((P - P.min(0)) / TILE).astype(int)\n        for t in np.unique(tile, axis=0):\n            sel = P[(tile == t).all(1)]\n            vmn, vmx = sel.min(0) - vs / 2, sel.max(0) + vs / 2\n            mn, mx = vmn - PAD, vmx + PAD\n            for k in range(3):                       # a side meeting a wall entity stops at the wall\'s face\n                o1, o2 = [a for a in range(3) if a != k]\n                for b0, b1 in wboxes:\n                    ov = (max(0.0, min(mx[o1], b1[o1]) - max(mn[o1], b0[o1])) * max(0.0, min(mx[o2], b1[o2]) - max(mn[o2], b0[o2])))\n                    if ov <= 0: continue\n                    if vmx[k] - vs <= b0[k] < mx[k]:\n                        mx[k] = max(b0[k], vmx[k] - vs); n_snap += 1\n                    if mn[k] < b1[k] <= vmn[k] + vs:\n                        mn[k] = min(b1[k], vmn[k] + vs); n_snap += 1\n            o = (mn + mx) / 2\n            props = dict(PROPS, box_mins=\'%f %f %f\' % tuple(mn - o), box_maxs=\'%f %f %f\' % tuple(mx - o))\n            m.new_entity(\'env_combined_light_probe_volume\', o, props)\n            n_vol += 1\n    c.log.append(\'probes: %s%d env_combined_light_probe_volume fitted to %d playable compartments (tiles up to %g units)%s\'\n                 % (\'replaced %d existing volumes with \' % len(existing) if existing else \'\', n_vol, len(regions), TILE,\n                    \'; %s count as walls: %d volume side(s) stop at their faces\' % (\'/\'.join(wall_classes), n_snap) if wall_classes else \'\'))\n    c.todo.append(\'probes: after a full bake, check players / weapons are lit evenly; if a room looks off, check its probe volume covers it\')\n'
_BUNDLED_VMAP_MODULES['splitfaces'] = '"""Split very large lit faces of a .vmap into tiles (development copy; pasted into rebuild_lighting.py).\n\nWhy: in CS2 a face is one lightmap chart, and a mesh (CMapMesh) gets one probe / cubemap for the whole mesh. A huge floor\ntherefore bakes blotchy and reflects the wrong place.  This cuts faces over `limit` square units on a world-aligned grid\n(`cell` units) and, optionally, gives every grid cell its own copy of the mesh.\n\nAll geometry is rebuilt from polygons: vertices are welded by position, every undirected edge gets two half-edges (the twin of\na boundary edge has face -1), and the corner data (texcoord / normal / tangent / vertex paint) is interpolated along cut edges.\nMeshes with subdivision data (importer displacements) and non-convex faces are left alone.\n"""\nimport numpy as np\n\nEPS = 0.01          # a point this close to a cut plane counts as on it\nWELD = 3            # vertex welding, decimals\n\n\n# ----------------------------------------------------------------------------------------------------------- reading\ndef _streams(m, node, part):\n    """-> {stream name: (element index, data list)} for meshData.<part>"""\n    md = m.g(node, b\'meshData\')\n    pd = m.g(md, part)\n    return {m.E[st][\'name\'].decode(): (st, m.g(st, b\'data\')) for st in (m.g(pd, b\'streams\') or [])}\n\n\ndef mesh_has_subdivision(m, node):\n    sd = m.g(m.g(node, b\'meshData\'), b\'subdivisionData\')\n    return bool(sd is not None and m.g(sd, b\'streams\'))\n\n\ndef mesh_read(m, node):\n    """-> (polys, materials).  poly = {\'attrs\': {faceData stream: value}, \'corners\': [{\'pos\': ndarray(3), \'fvd\': {stream: ndarray}}]}"""\n    md = m.g(node, b\'meshData\')\n    dest, nxt = m.g(md, b\'edgeVertexIndices\'), m.g(md, b\'edgeNextIndices\')\n    fe, fd = m.g(md, b\'faceEdgeIndices\'), m.g(md, b\'faceDataIndices\')\n    evd, vdi = m.g(md, b\'edgeVertexDataIndices\'), m.g(md, b\'vertexDataIndices\')\n    pos = _streams(m, node, b\'vertexData\')[\'position:0\'][1]\n    fvd = {k: v[1] for k, v in _streams(m, node, b\'faceVertexData\').items()}\n    fdat = {k: v[1] for k, v in _streams(m, node, b\'faceData\').items()}\n    polys = []\n    for f, h0 in enumerate(fe):\n        corners, h = [], h0\n        for _ in range(len(dest) + 1):\n            corners.append({\'pos\': np.array(pos[vdi[dest[h]]], float),\n                            \'fvd\': {k: np.array(v[evd[h]], float) for k, v in fvd.items()}})\n            h = nxt[h]\n            if h == h0:\n                break\n        polys.append({\'attrs\': {k: v[fd[f]] for k, v in fdat.items()}, \'corners\': corners})\n    return polys, [x.decode(\'utf-8\', \'replace\') if isinstance(x, (bytes, bytearray)) else x for x in m.g(md, b\'materials\')]\n\n\n# ------------------------------------------------------------------------------------------------------------ geometry\ndef poly_area(corners):\n    P = np.array([c[\'pos\'] for c in corners])\n    n = np.zeros(3)\n    for i in range(1, len(P) - 1):\n        n += np.cross(P[i] - P[0], P[i + 1] - P[0])\n    return float(np.linalg.norm(n) / 2)\n\n\ndef is_convex(corners, tol=1e-6):\n    P = np.array([c[\'pos\'] for c in corners])\n    n = np.zeros(3)\n    for i in range(1, len(P) - 1):\n        n += np.cross(P[i] - P[0], P[i + 1] - P[0])\n    ln = np.linalg.norm(n)\n    if ln < 1e-9:\n        return False\n    n /= ln\n    for i in range(len(P)):\n        c = np.dot(np.cross(P[(i + 1) % len(P)] - P[i], P[(i + 2) % len(P)] - P[(i + 1) % len(P)]), n)\n        if c < -tol * max(1.0, ln):\n            return False\n    return True\n\n\ndef _lerp(a, b, t):\n    out = {\'pos\': a[\'pos\'] + (b[\'pos\'] - a[\'pos\']) * t, \'fvd\': {}}\n    for k in a[\'fvd\']:\n        v = a[\'fvd\'][k] + (b[\'fvd\'][k] - a[\'fvd\'][k]) * t\n        if k.startswith(\'normal\'):\n            ln = np.linalg.norm(v)\n            if ln > 1e-9:\n                v = v / ln\n        out[\'fvd\'][k] = v\n    return out\n\n\ndef _edge_key(p, q):\n    a, b = tuple(np.round(p, WELD)), tuple(np.round(q, WELD))\n    return (a, b) if a <= b else (b, a)\n\n\ndef clip_poly(corners, axis, c):\n    """Cut a convex polygon with the plane  pos[axis] = c  -> (below, above) corner lists (either may be empty)."""\n    lo, hi = [], []\n    n = len(corners)\n    for i in range(n):\n        a, b = corners[i], corners[(i + 1) % n]\n        sa, sb = a[\'pos\'][axis] - c, b[\'pos\'][axis] - c\n        sa0, sb0 = abs(sa) < EPS, abs(sb) < EPS\n        if sa0:\n            lo.append(a)\n            hi.append(a)\n        elif sa < 0:\n            lo.append(a)\n        else:\n            hi.append(a)\n        if not sa0 and not sb0 and (sa < 0) != (sb < 0):\n            if tuple(np.round(a[\'pos\'], WELD)) <= tuple(np.round(b[\'pos\'], WELD)):\n                p = _lerp(a, b, sa / (sa - sb))\n            else:                                   # always interpolate from the same end, so both faces sharing the edge agree\n                p = _lerp(b, a, sb / (sb - sa))\n            p[\'pos\'][axis] = c                      # exactly on the plane, so both sides weld\n            lo.append(p)\n            hi.append(p)\n    return lo, hi\n\n\ndef _usable(corners):\n    return len(corners) >= 3 and poly_area(corners) > 1e-4\n\n\n# ----------------------------------------------------------------------------------------------------------- cutting\ndef cut_polys(polys, limit, cell, candidate, min_piece=None):\n    """Cut every candidate poly with area >= limit on the grid.  -> (new polys, cut count, skipped [(reason, area)])"""\n    min_piece = cell / 64.0 if min_piece is None else min_piece\n    plan, skipped = {}, []\n    for i, p in enumerate(polys):\n        if not candidate(p):\n            continue\n        area = poly_area(p[\'corners\'])\n        if area <= limit * 1.0001:                  # a tile of exactly cell x cell is fine\n            continue\n        if not is_convex(p[\'corners\']):\n            skipped.append((\'not convex\', area))\n            continue\n        P = np.array([c[\'pos\'] for c in p[\'corners\']])\n        planes = []\n        for axis in range(3):\n            lo, hi = P[:, axis].min(), P[:, axis].max()\n            if hi - lo <= cell:\n                continue\n            k = int(np.ceil((lo + EPS) / cell))\n            while k * cell < hi - EPS:\n                c = k * cell\n                if c - lo >= min_piece and hi - c >= min_piece:\n                    planes.append((axis, float(c)))\n                k += 1\n        if planes:\n            plan[i] = planes\n    # points where the planes cross the edges of cut faces: the neighbours across those edges need the same points\n    edge_pts = {}\n    for i, planes in plan.items():\n        cs = polys[i][\'corners\']\n        for j in range(len(cs)):\n            a, b = cs[j], cs[(j + 1) % len(cs)]\n            for axis, c in planes:\n                sa, sb = a[\'pos\'][axis] - c, b[\'pos\'][axis] - c\n                if abs(sa) >= EPS and abs(sb) >= EPS and (sa < 0) != (sb < 0):\n                    if tuple(np.round(a[\'pos\'], WELD)) <= tuple(np.round(b[\'pos\'], WELD)):\n                        p = a[\'pos\'] + (b[\'pos\'] - a[\'pos\']) * (sa / (sa - sb))\n                    else:\n                        p = b[\'pos\'] + (a[\'pos\'] - b[\'pos\']) * (sb / (sb - sa))\n                    p[axis] = c\n                    edge_pts.setdefault(_edge_key(a[\'pos\'], b[\'pos\']), {})[tuple(np.round(p, WELD))] = p\n    out, cut = [], 0\n    for i, p in enumerate(polys):\n        cs = p[\'corners\']\n        if edge_pts:\n            ncs = []\n            for j in range(len(cs)):\n                a, b = cs[j], cs[(j + 1) % len(cs)]\n                pts = edge_pts.get(_edge_key(a[\'pos\'], b[\'pos\']))\n                if pts:\n                    L = np.linalg.norm(b[\'pos\'] - a[\'pos\'])\n                    ts = sorted(((float(np.dot(q - a[\'pos\'], b[\'pos\'] - a[\'pos\']) / (L * L)), q) for q in pts.values()), key=lambda x: x[0])\n                    mids = [_lerp(a, b, t) for t, _ in ts]\n                    for mid, (_, q) in zip(mids, ts):\n                        mid[\'pos\'] = q.copy()\n                    ncs.extend(mids)\n                ncs.append(b)                       # corner j+1 (so the list starts at corner 1; rotated back below)\n            cs = ncs[-1:] + ncs[:-1]                # restore the original starting corner\n        if i in plan:\n            pieces = [cs]\n            for axis, c in plan[i]:\n                nxt = []\n                for pc in pieces:\n                    lo, hi = clip_poly(pc, axis, c)\n                    nxt += [x for x in (lo, hi) if _usable(x)]\n                pieces = nxt\n            for pc in pieces:\n                out.append({\'attrs\': p[\'attrs\'], \'corners\': pc, \'cut\': True})\n            cut += 1\n        else:\n            out.append({\'attrs\': p[\'attrs\'], \'corners\': cs})\n    return out, cut, skipped\n\n\n# ----------------------------------------------------------------------------------------------------------- writing\ndef mesh_write(m, node, polys):\n    """Rebuild the half-edge arrays and data streams of `node` from polygons.  Returns an error string or None (mesh untouched on error)."""\n    import dmx\n    md = m.g(node, b\'meshData\')\n    verts, vidx = [], {}\n\n    def vid(p):\n        k = tuple(np.round(p, WELD))\n        if k not in vidx:\n            vidx[k] = len(verts)\n            verts.append(p)\n        return vidx[k]\n\n    faces = []\n    for p in polys:\n        pairs = []\n        for c in p[\'corners\']:\n            k = vid(c[\'pos\'])\n            if pairs and pairs[-1][0] == k:\n                continue                            # two corners welded together: drop the repeat\n            pairs.append((k, c))\n        if len(pairs) > 1 and pairs[0][0] == pairs[-1][0]:\n            pairs.pop()\n        ids = [k for k, _ in pairs]\n        if len(ids) < 3 or len(set(ids)) != len(ids):\n            return \'degenerate or pinched face after welding\'\n        faces.append(({\'attrs\': p[\'attrs\'], \'corners\': [c for _, c in pairs]}, ids))\n    half = {}                                       # directed (origin, destination) -> half-edge index\n    dest, face_of, corner_of, edge_of, opp, nxt, fedge = [], [], [], [], [], [], []\n    for f, (p, ids) in enumerate(faces):\n        n = len(ids)\n        hs = []\n        for j in range(n):\n            u, v = ids[j - 1], ids[j]               # the half-edge into corner j runs from the previous corner to this one\n            h = half.get((u, v))\n            if h is None:                           # new edge: face side 2k, boundary twin 2k+1\n                k = len(dest) // 2\n                h = 2 * k\n                dest += [v, u]\n                face_of += [-1, -1]\n                corner_of += [None, None]\n                edge_of += [k, k]\n                opp += [h + 1, h]\n                nxt += [-1, -1]\n                half[(u, v)] = h\n                half[(v, u)] = h + 1\n            elif face_of[h] != -1:\n                return \'non-manifold edge (two faces use the same direction)\'\n            face_of[h] = f\n            corner_of[h] = (f, j)\n            hs.append(h)\n        for j in range(n):\n            nxt[hs[j]] = hs[(j + 1) % n]\n        fedge.append(hs[0])\n    H = len(dest)\n    # boundary loops: next boundary half-edge starts where this one ends\n    out_b = {}\n    for h in range(H):\n        if face_of[h] == -1:\n            origin = dest[opp[h]]\n            out_b.setdefault(origin, []).append(h)\n    for h in range(H):\n        if face_of[h] == -1:\n            lst = out_b.get(dest[h], [])\n            nxt[h] = lst.pop(0) if lst else opp[h]\n    nv = len(verts)\n    vedge = [-1] * nv\n    for h in range(H):\n        o = dest[opp[h]]\n        if vedge[o] == -1 or (face_of[h] != -1 and face_of[vedge[o]] == -1):\n            vedge[o] = h\n    if -1 in vedge:\n        return \'unused vertex\'\n    nf = len(faces)\n    # ---- write\n    def setv(el, name, val):\n        a = dmx.geta(m.E[el], name)\n        a[2] = val\n\n    setv(md, b\'vertexEdgeIndices\', vedge)\n    setv(md, b\'vertexDataIndices\', list(range(nv)))\n    setv(md, b\'edgeVertexIndices\', dest)\n    setv(md, b\'edgeOppositeIndices\', opp)\n    setv(md, b\'edgeNextIndices\', nxt)\n    setv(md, b\'edgeFaceIndices\', face_of)\n    setv(md, b\'edgeDataIndices\', edge_of)\n    setv(md, b\'edgeVertexDataIndices\', list(range(H)))\n    setv(md, b\'faceEdgeIndices\', fedge)\n    setv(md, b\'faceDataIndices\', list(range(nf)))\n    ne = H // 2\n\n    def resize(part, size, fill):\n        pd = m.g(md, part)\n        setv(pd, b\'size\', size)\n        for st in m.g(pd, b\'streams\') or []:\n            name = m.E[st][\'name\'].decode()\n            setv(st, b\'data\', fill(name))\n\n    resize(b\'vertexData\', nv, lambda name: [list(map(float, v)) for v in verts])\n    zero = {}\n    for k, (st, data) in _streams(m, node, b\'faceVertexData\').items():\n        zero[k] = [0.0] * len(data[0]) if data else [0.0]\n\n    def fvd_fill(name):\n        arr = [list(zero[name]) for _ in range(H)]\n        for h in range(H):\n            if corner_of[h] is not None:\n                f, j = corner_of[h]\n                arr[h] = [float(x) for x in faces[f][0][\'corners\'][j][\'fvd\'][name]]\n        return arr\n\n    # (zero template is taken from the old data before it is overwritten)\n    resize(b\'faceVertexData\', H, fvd_fill)\n    resize(b\'edgeData\', ne, lambda name: [0] * ne)\n    resize(b\'faceData\', nf, lambda name: [faces[f][0][\'attrs\'][name] for f in range(nf)])\n    sd = m.g(md, b\'subdivisionData\')\n    if sd is not None:\n        setv(sd, b\'subdivisionLevels\', [0] * H)\n    return None\n\n\n# ----------------------------------------------------------------------------------------------------------- checking\ndef mesh_check(m, node):\n    """Structural check of the half-edge arrays.  -> list of problems (empty = fine)"""\n    md = m.g(node, b\'meshData\')\n    g = lambda k: m.g(md, k)\n    dest, opp, nxt, face = g(b\'edgeVertexIndices\'), g(b\'edgeOppositeIndices\'), g(b\'edgeNextIndices\'), g(b\'edgeFaceIndices\')\n    fe, vedge = g(b\'faceEdgeIndices\'), g(b\'vertexEdgeIndices\')\n    evd, edi = g(b\'edgeVertexDataIndices\'), g(b\'edgeDataIndices\')\n    bad = []\n    H = len(dest)\n    if not (len(opp) == len(nxt) == len(face) == len(evd) == len(edi) == H):\n        return [\'array lengths differ\']\n    if sorted(evd) != list(range(len(evd))):\n        bad.append(\'edgeVertexDataIndices is not a permutation\')\n    for h in range(H):\n        if opp[opp[h]] != h:\n            bad.append(\'opposite of opposite of %d\' % h)\n        if dest[opp[h]] == dest[h]:\n            bad.append(\'half-edge %d is a loop\' % h)\n        if dest[h] != dest[opp[nxt[h]]] and False:\n            pass\n        if dest[opp[nxt[h]]] != dest[h]:\n            bad.append(\'next of %d does not start where it ends\' % h)\n        if face[h] != -1 and face[nxt[h]] != face[h]:\n            bad.append(\'next of %d leaves the face\' % h)\n    for f, h0 in enumerate(fe):\n        h, seen = h0, 0\n        while True:\n            if face[h] != f:\n                bad.append(\'face %d loop has a foreign half-edge\' % f)\n                break\n            h = nxt[h]\n            seen += 1\n            if h == h0 or seen > H:\n                break\n        if seen > H:\n            bad.append(\'face %d loop does not close\' % f)\n    for v, h in enumerate(vedge):\n        if dest[opp[h]] != v:\n            bad.append(\'vertexEdge of %d does not start at it\' % v)\n    dm = m.g(m.g(node, b\'meshData\'), b\'vertexData\')\n    for st in m.g(dm, b\'streams\'):\n        if len(m.g(st, b\'data\')) != m.g(dm, b\'size\'):\n            bad.append(\'vertexData stream length\')\n    return bad[:10]\n\n\n# ----------------------------------------------------------------------------------------------------------- whole-map pass\ndef _identity_xform(m, node):\n    o = m.g(node, b\'origin\', [0, 0, 0]); a = m.g(node, b\'angles\', [0, 0, 0]); sc = m.g(node, b\'scales\', [1, 1, 1])\n    ok = all(abs(x) < 1e-4 for x in a) and all(abs(x - 1) < 1e-4 for x in sc)\n    return ok, np.array(o, dtype=float)\n\n\ndef split_large(m, limit, cell, mode, skip_mat, log):\n    """Cut every lit face over `limit` sq units on a world grid of `cell`.  mode \'faces\' keeps one mesh; \'meshes\' also moves the\n    pieces into one mesh per grid cell (a cubemap / probe is assigned per mesh).  -> dict of counts + list of skipped notes"""\n    res = dict(meshes=0, faces=0, pieces=0, new_meshes=0, skipped=[])\n    owner = {}\n\n    def walk(n, label, cls):\n        for c in m.children(n):\n            t = m.type(c)\n            if t == b\'CMapMesh\':\n                owner[c] = (n, label, cls)\n            elif t == b\'CMapEntity\':\n                walk(c, \'%s %s\' % (m.cls(c), m.name(c) or \'-\'), m.cls(c))\n            else:\n                walk(c, label, cls)\n    walk(m.world, \'world\', \'world\')\n    for node, (parent, label, cls) in list(owner.items()):\n        if m.g(node, b\'meshData\') is None:\n            continue\n        if cls.startswith(\'func_water\') or cls.startswith(\'trigger_\'):\n            continue\n        ent_nodes = [e for e in m.entities() if m.cls(e) == cls and \'%s %s\' % (cls, m.name(e) or \'-\') == label] if cls != \'world\' else []\n        if ent_nodes and m.P(ent_nodes[0], \'lightmapstatic\', \'1\') == \'0\':\n            continue\n        try:\n            polys, mats = mesh_read(m, node)\n        except Exception:\n            continue\n        cand = lambda p: not any(x in mats[p[\'attrs\'][\'materialindex:0\']] for x in skip_mat)\n        big = [p for p in polys if cand(p) and poly_area(p[\'corners\']) > limit * 1.0001]\n        if not big:\n            continue\n        if mesh_has_subdivision(m, node):\n            res[\'skipped\'].append(\'%s (mesh %d): %d large face(s) in a displacement / subdivision mesh, not touched\' % (label, node, len(big)))\n            continue\n        ok, org = _identity_xform(m, node)\n        if not ok:\n            res[\'skipped\'].append(\'%s (mesh %d): %d large face(s), mesh is rotated or scaled, not touched\' % (label, node, len(big)))\n            continue\n        for p in polys:\n            p[\'corners\'] = [{\'pos\': c[\'pos\'] + org, \'fvd\': c[\'fvd\']} for c in p[\'corners\']]\n        newp, cut, sk = cut_polys(polys, limit, cell, cand)\n        for kind, area in sk:\n            res[\'skipped\'].append(\'%s (mesh %d): face of %d sq units %s, not cut\' % (label, node, area, kind))\n        if not cut:\n            continue\n        for p in newp:                              # corner dicts can be shared between pieces, so copy rather than shift in place\n            p[\'corners\'] = [{\'pos\': c[\'pos\'] - org, \'fvd\': c[\'fvd\']} for c in p[\'corners\']]\n        groups = {}\n        rest = []\n        if mode == \'meshes\':\n            for p in newp:\n                if p.get(\'cut\'):\n                    ctr = np.mean([c[\'pos\'] for c in p[\'corners\']], axis=0) + org\n                    groups.setdefault(tuple(int(x) for x in np.floor(ctr / cell)), []).append(p)\n                else:\n                    rest.append(p)\n        else:\n            rest = newp\n        if len(groups) <= 1 and not rest:\n            groups = {}\n            rest = newp\n        if len(groups) > 0 and rest:\n            pass\n        # write the original mesh first; if it has nothing left, it takes the first group\n        keys = sorted(groups)\n        first = rest if rest else groups[keys.pop(0)] if groups else newp\n        err = mesh_write(m, node, first)\n        if err:\n            res[\'skipped\'].append(\'%s (mesh %d): %s, not touched\' % (label, node, err))\n            continue\n        made = []\n        failed = False\n        for k in keys:\n            nid = [m.next_node_id()]\n            new = m.import_subtree(m, node, {}, nid)\n            e = mesh_write(m, new, groups[k])\n            if e:\n                failed = True\n                res[\'skipped\'].append(\'%s (mesh %d): tile %s: %s\' % (label, node, k, e))\n                break\n            made.append(new)\n        if failed:                                  # put every piece back in the original mesh\n            mesh_write(m, node, newp)\n            continue\n        if made:\n            from dmx import geta\n            arr = geta(m.E[parent], b\'children\')\n            arr[2].extend(made)\n        res[\'meshes\'] += 1\n        res[\'faces\'] += cut\n        res[\'pieces\'] += sum(1 for p in newp if p.get(\'cut\'))\n        res[\'new_meshes\'] += len(made)\n        log.append(\'%s (mesh %d): cut %d large face(s) into %d pieces%s\' % (label, node, cut, sum(1 for p in newp if p.get(\'cut\')), \', %d new mesh(es)\' % len(made) if made else \'\'))\n    return res\n'

def _bundled_vmapedit():
    """register the bundled dmx / kv2 / vmapedit modules (in import order, so they can import each other) -> (vmapedit, dmx)"""
    for name in ("dmx", "kv2", "vmapedit"):
        _bundled_install(name)
    return sys.modules["vmapedit"], sys.modules["dmx"]


def _bundled_lighting_passes(names=("lighting", "visfit", "probes", "splitfaces")):
    """register the bundled lighting-pass modules (lighting needs numpy; visfit / probes also scipy) -> the lighting module"""
    _bundled_vmapedit()
    for name in names:
        _bundled_install(name)
    return sys.modules["lighting"]


def _bundled_install(name):
    """exec one bundled module's source into sys.modules[name] (once)"""
    import types
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    mod = sys.modules[name]
    if not getattr(mod, "_bundled", False):
        mod.__file__ = __file__
        mod._bundled = True
        exec(compile(_BUNDLED_VMAP_MODULES[name], "<bundled %s.py>" % name, "exec"), mod.__dict__)


_PROP_NAME_HAS_MASS_RE = re.compile(r"^push_(\d+(?:\.\d+)?)_", re.I)
_PHY_SOLID_RE = re.compile(r"solid\s*\{(.*?)\}", re.S | re.I)
_PHY_MASS_RE = re.compile(r'"mass"\s+"([0-9.eE+-]+)"', re.I)
_PHY_TOTALMASS_RE = re.compile(r'"totalmass"\s+"([0-9.eE+-]+)"', re.I)

def parse_phy_mass(path):
    """Mass in kg of a Source1 model from its .phy: the sum of the "mass" of every solid in the text section at the end of the file
    (what the engine gives the prop), else the editparams "totalmass". None if the file has neither."""
    try:
        text = Path(path).read_bytes().decode("latin-1")
    except Exception:
        return None
    total = 0.0
    n = 0
    for blk in _PHY_SOLID_RE.findall(text):
        mm = _PHY_MASS_RE.search(blk)
        if mm:
            try:
                total += float(mm.group(1))
                n += 1
            except ValueError:
                pass
    if n and total > 0:
        return total
    mt = _PHY_TOTALMASS_RE.search(text)
    if mt:
        try:
            v = float(mt.group(1))
            return v if v > 0 else None
        except ValueError:
            return None
    return None

_MODEL_MASS_MEMO = {}

def model_mass(model: str):
    """vmap model path (models/x/y.vmdl) -> mass of the Source1 model (models/x/y.phy found in SOURCE1_ROOTS) or None."""
    key = (model or "").replace("\\", "/").lower()
    if key not in _MODEL_MASS_MEMO:
        base = key[:-5] if key.endswith(".vmdl") else (key[:-4] if key.endswith(".mdl") else key)
        p, _r = find_in_source1(base + ".phy") if base else (None, None)
        _MODEL_MASS_MEMO[key] = parse_phy_mass(p) if p else None
    return _MODEL_MASS_MEMO[key]

def _fmt_mass(m: float) -> str:
    return f"{m:.2f}".rstrip("0").rstrip(".")

def _ci_prop(m, e, key: str, default=""):
    """entity property by case-insensitive key (the importer writes massScale, Hammer may write massscale)"""
    kl = key.lower()
    for k, t, v in m.props(e)["attrs"]:
        if k.decode("utf-8", "ignore").lower() == kl and isinstance(v, (bytes, bytearray)):
            return v.decode("utf-8", "replace")
    return default

_PROP_REF_SKIP_KEYS = {"classname", "targetname", "model", "message", "vscripts", "globalname", "parentattachmentname", "soundname",
                       "effect_name", "addon", "bodygroups", "propdata_override", "customvisgroup"}

_ENABLE_MOTION_RE = re.compile(r"enable\s*_?motion")
_DISABLE_MOTION_RE = re.compile(r"disable\s*_?motion")

def _prop_script_texts(vp):
    """lower-cased text of the map's scripts (.js/.ts/.vjs/.vts) that mention EnableMotion, for the static-prop check"""
    dirs = []
    if PROP_MASS_SCRIPT_DIRS:
        dirs = [Path(d) for d in PROP_MASS_SCRIPT_DIRS]
    else:
        for par in list(Path(vp).resolve().parents)[:3]:
            if (par / "scripts").is_dir():
                dirs.append(par / "scripts")
                break
    texts = []
    for d in dirs:
        for f in d.rglob("*"):
            if f.suffix.lower() in (".js", ".ts", ".vjs", ".vts", ".mjs") and f.is_file():
                try:
                    t = f.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    continue
                if _ENABLE_MOTION_RE.search(t):
                    texts.append((f.name, t))
    return texts

def _motion_index(m, ents, dmx, vp):
    """Everything in the map that can turn motion on/off, for _prop_motion_notes()"""
    import fnmatch
    ix = {"en_exact": {}, "en_wild": [], "en_class": {}, "en_special": [], "text": [], "dis_exact": set(), "dis_wild": [], "scripts": _prop_script_texts(vp), "fn": fnmatch}
    for e in ents:
        who = f"{m.cls(e)} {m.name(e) or '(unnamed)'}"
        for c in m.conns(e):
            tgt = (dmx.get(c, b"targetName") or b"").decode("utf-8", "replace").lower()
            inp = (dmx.get(c, b"inputName") or b"").decode("utf-8", "replace").lower()
            prm = (dmx.get(c, b"overrideParam") or b"").decode("utf-8", "replace").lower()
            outn = (dmx.get(c, b"outputName") or b"").decode("utf-8", "replace")
            tgt = re.sub(r"&\d+$", "", tgt)
            if _ENABLE_MOTION_RE.fullmatch(inp):
                lab = f"{who} {outn} -> EnableMotion"
                if tgt.startswith("!"):
                    ix["en_special"].append(f"{lab} on {tgt} (target only known at runtime)")
                elif "*" in tgt:
                    ix["en_wild"].append((tgt, lab))
                elif tgt:
                    ix["en_exact"].setdefault(tgt, lab)
                    ix["en_class"].setdefault(tgt, lab)
            elif _DISABLE_MOTION_RE.fullmatch(inp):
                if "*" in tgt:
                    ix["dis_wild"].append(tgt)
                elif tgt:
                    ix["dis_exact"].add(tgt)
            if _ENABLE_MOTION_RE.search(prm):
                ix["text"].append((prm, f"{who} {outn} -> {inp} \"{prm}\""))
        for k, t, v in m.props(e)["attrs"]:
            if t == dmx.T_STR and isinstance(v, (bytes, bytearray)) and _ENABLE_MOTION_RE.search(v.decode("utf-8", "replace").lower()):
                ix["text"].append((v.decode("utf-8", "replace").lower(), f"{who} keyvalue {k.decode('utf-8', 'ignore')}"))
    return ix

def _prop_motion_notes(ix, name, cls):
    """-> (reasons it could be enabled later [list of str], can be frozen later [bool])"""
    n = (name or "").lower()
    why = []
    if n and n in ix["en_exact"]:
        why.append(ix["en_exact"][n])
    for pat, lab in ix["en_wild"]:
        if n and ix["fn"].fnmatchcase(n, pat):
            why.append(lab + f" (wildcard {pat})")
    if cls.lower() in ix["en_class"] and cls.lower() not in ("",):
        why.append(ix["en_class"][cls.lower()] + " (targets the whole class)")
    why += ix["en_special"]
    for prm, lab in ix["text"]:
        if (n and n in prm) or "*" in prm or "!" in prm or cls.lower() in prm:
            why.append(lab)
    for fname, t in ix["scripts"]:
        if (n and n in t) or cls.lower() in t:
            why.append(f"script {fname} mentions EnableMotion and this prop/class")
    froze = bool(n) and (n in ix["dis_exact"] or any(ix["fn"].fnmatchcase(n, p_) for p_ in ix["dis_wild"]))
    return why, froze

def rename_props_by_mass(vmap_paths, backup_dir, in_place=True):
    """Give every prop_physics / prop_physics_override the name  push_<mass>_<old name>  (the format push_mass.js reads), mass = Source1 model mass
    (.phy) x massScale. Everything that pointed at the old name is updated:
      - entity outputs (connectionsData) targeting the name (exact, or a wildcard pattern the new name no longer matches); if other entities
        still use the old name, or several masses now share it, the output is DUPLICATED for each new name instead of moved
      - input parameters that are entity names (PROP_MASS_NAME_PARAM_INPUTS, e.g. SetParent)
      - any entity keyvalue that equals the old name (parentname, target, filtername, ...)
      - point_template Template01..16: the slot is replaced; extra slots are used when one name became several (different masses)
    Props that already start with push_<number>_ are left alone (PROP_MASS_KEEP_EXISTING). The vmap is backed up to BACKUP_DIR first.
    -> list of log lines"""
    import fnmatch
    dmx = _bundled_vmapedit()[1]
    out = []
    summary = {"props": 0, "renamed": 0, "kept": 0, "static": 0, "uncertain": 0, "nomass": 0, "conns": 0, "dups": 0, "keys": 0, "slots": 0}
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        ents = m.entities()
        props = [e for e in ents if m.cls(e) in PROP_MASS_CLASSES]
        if not props:
            _keep_map(m, vp)
            continue
        summary["props"] += len(props)
        renamed = {}                 # entity -> (old, new)
        mapping = {}                 # old name (lower) -> [new names]
        rows = []
        kept_n = {}
        mx = None
        static_names = []
        for e in props:
            old, model, cls = m.name(e), m.P(e, "model", ""), m.cls(e)
            mass = model_mass(model)
            if mass is not None:
                try:
                    sc = float(_ci_prop(m, e, "massscale", "0") or 0)
                except ValueError:
                    sc = 0.0
                if sc > 0:
                    mass *= sc
            tag = _fmt_mass(mass) if mass else ""
            note = ""
            if PROP_MASS_SKIP_IMMOVABLE:
                try:
                    fl = int(float(_ci_prop(m, e, "spawnflags", "0") or 0))
                except ValueError:
                    fl = 0
                if fl & 4:
                    summary["static"] += 1
                    rows.append(f"STATIC\t{cls}\t{old or '(unnamed)'}\tDebris flag (no player collision), not renamed\t{model}")
                    static_names.append(old)
                    continue
                if fl & 8:
                    if mx is None:
                        mx = _motion_index(m, ents, dmx, vp)
                    why, _fz = _prop_motion_notes(mx, old, cls)
                    if not why:
                        summary["static"] += 1
                        rows.append(f"STATIC\t{cls}\t{old or '(unnamed)'}\tMotion Disabled and nothing in the map or scripts can EnableMotion it, not renamed\t{model}")
                        static_names.append(old)
                        continue
                    note = "\tUNCERTAIN: Motion Disabled, but could be enabled by: " + " | ".join(dict.fromkeys(why))[:400]
                    summary["uncertain"] += 1
                else:
                    if mx is None:
                        mx = _motion_index(m, ents, dmx, vp)
                    if _prop_motion_notes(mx, old, cls)[1]:
                        note = "\t(a DisableMotion output can freeze it later)"
            if PROP_MASS_KEEP_EXISTING and _PROP_NAME_HAS_MASS_RE.match(old):
                summary["kept"] += 1
                kept_n[(cls, old, tag or "unknown", model)] = kept_n.get((cls, old, tag or "unknown", model), 0) + 1
                continue
            if not tag or tag == "0":
                summary["nomass"] += 1
                rows.append(f"NO MASS\t{cls}\t{old or '(unnamed)'}\t{('model has no .phy in the Source1 folders' if mass is None else 'mass rounds to 0')}\t{model}")
                continue
            new = f"{PROP_MASS_PREFIX}{tag}_{old or 'prop'}"
            renamed[e] = (old, new)
            if old:
                lst = mapping.setdefault(old.lower(), [])
                if new not in lst:
                    lst.append(new)
            rows.append(f"RENAME\t{cls}\t{old or '(unnamed)'}\t-> {new}\t{model}\tmass {tag}{note}")
        rows += [f"KEPT\t{cls}\t{old}\tx{n_}\t(name already carries a mass; model mass {tg})\t{model}" for (cls, old, tg, model), n_ in sorted(kept_n.items())]
        if not renamed:
            out.append(f"# {vn}: {len(props)} prop_physics, none renamed (kept {sum(kept_n.values())}, no mass {sum(1 for r in rows if r.startswith('NO MASS'))})")
            out += rows
            _keep_map(m, vp)                     # nothing changed
            continue
        # names still in use by entities that are NOT renamed (old name stays valid for them)
        still = {}
        for e in ents:
            if e not in renamed and m.name(e):
                still[m.name(e).lower()] = still.get(m.name(e).lower(), 0) + 1
        ref_rows = []
        # ---- outputs (connections) ----
        for e in ents:
            for c in list(m.conns(e)):
                tgt = (dmx.get(c, b"targetName") or b"").decode("utf-8", "replace")
                inp = (dmx.get(c, b"inputName") or b"").decode("utf-8", "replace")
                prm = (dmx.get(c, b"overrideParam") or b"").decode("utf-8", "replace")
                outn = (dmx.get(c, b"outputName") or b"").decode("utf-8", "replace")
                if inp.lower() in PROP_MASS_NAME_PARAM_INPUTS and prm.lower() in mapping:
                    nn = mapping[prm.lower()]
                    dmx.set_(c, b"overrideParam", dmx.T_STR, nn[0].encode())
                    ref_rows.append(f"INPUT PARAM\t{m.cls(e)} {m.name(e) or '(unnamed)'}\t{outn} -> {tgt}.{inp}\t{prm} -> {nn[0]}" + (f"\t(WARNING: {len(nn)} masses share this name, first used)" if len(nn) > 1 else ""))
                    summary["conns"] += 1
                if not tgt or tgt.startswith("!"):
                    continue
                tl = tgt.lower()
                if "*" in tl:
                    extra = [nw for ol, nws in mapping.items() if fnmatch.fnmatchcase(ol, tl) for nw in nws if not fnmatch.fnmatchcase(nw.lower(), tl)]
                    keep_orig = True
                elif tl in mapping:
                    extra = list(mapping[tl])
                    keep_orig = still.get(tl, 0) > 0
                else:
                    continue
                if not extra:
                    continue
                tt = dmx.get(c, b"targetType")
                if not keep_orig:
                    dmx.set_(c, b"targetName", dmx.T_STR, extra[0].encode())
                    ref_rows.append(f"OUTPUT MOVED\t{m.cls(e)} {m.name(e) or '(unnamed)'}\t{outn} -> {inp}\t{tgt} -> {extra[0]}")
                    extra = extra[1:]
                    summary["conns"] += 1
                for nw in extra:
                    c2 = m.add_conn(e, outn, nw, inp, prm, dmx.get(c, b"delay") or 0.0, dmx.get(c, b"timesToFire") if dmx.get(c, b"timesToFire") is not None else -1)
                    if tt is not None:
                        dmx.set_(m.E[c2], b"targetType", dmx.T_INT, tt)
                    ref_rows.append(f"OUTPUT ADDED\t{m.cls(e)} {m.name(e) or '(unnamed)'}\t{outn} -> {inp}\t{tgt} also -> {nw}" + ("\t(old name still used by other entities)" if keep_orig and "*" not in tl else ""))
                    summary["dups"] += 1
        # ---- keyvalues that name a renamed prop ----
        for e in ents:
            cls = m.cls(e)
            for k, t, v in list(m.props(e)["attrs"]):
                if t != dmx.T_STR or not isinstance(v, (bytes, bytearray)) or not v:
                    continue
                key = k.decode("utf-8", "ignore")
                kl = key.lower()
                if kl in _PROP_REF_SKIP_KEYS:
                    continue
                val = v.decode("utf-8", "replace")
                if cls == "point_template" and re.fullmatch(r"template\d+", kl):
                    continue             # handled below
                if val.lower() in mapping:
                    nn = mapping[val.lower()]
                    m.setp(e, key, nn[0])
                    ref_rows.append(f"KEYVALUE\t{cls} {m.name(e) or '(unnamed)'}\t{key}\t{val} -> {nn[0]}" + (f"\t(WARNING: {len(nn)} masses share this name, first used)" if len(nn) > 1 else ""))
                    summary["keys"] += 1
        # ---- point_template slots ----
        for e in ents:
            if m.cls(e) != "point_template":
                continue
            slots = {}
            for k, t, v in m.props(e)["attrs"]:
                mm = re.fullmatch(r"template(\d+)", k.decode("utf-8", "ignore").lower())
                if mm and isinstance(v, (bytes, bytearray)):
                    slots[int(mm.group(1))] = (k.decode(), v.decode("utf-8", "replace"))
            todo = []              # (slot, new names to place)
            for n_, (key, val) in sorted(slots.items()):
                if val.lower() in mapping:
                    nn = list(mapping[val.lower()])
                    if still.get(val.lower(), 0) == 0:
                        m.setp(e, key, nn[0])
                        ref_rows.append(f"TEMPLATE\t{m.name(e) or '(unnamed)'}\t{key}\t{val} -> {nn[0]}")
                        summary["slots"] += 1
                        nn = nn[1:]
                    present = {x.lower() for _, x in slots.values()}
                    todo += [nw for nw in nn if nw.lower() not in present]
            free = [n_ for n_ in range(1, 17) if not slots.get(n_, ("", ""))[1]]
            for nw in todo:
                if not free:
                    ref_rows.append(f"TEMPLATE\t{m.name(e) or '(unnamed)'}\t(no free slot)\tWARNING: {nw} could not be added to this point_template (16 slots used); add it by hand")
                    continue
                n_ = free.pop(0)
                m.setp(e, f"Template{n_:02d}", nw)
                ref_rows.append(f"TEMPLATE\t{m.name(e) or '(unnamed)'}\tTemplate{n_:02d}\t(empty) -> {nw}")
                summary["slots"] += 1
        # ---- the renames themselves ----
        for e, (old, new) in renamed.items():
            m.setp(e, "targetname", new)
        summary["renamed"] += len(renamed)
        out.append(f"# {vn}: {len(renamed)} of {len(props)} prop_physics renamed; {len(ref_rows)} reference(s) updated")
        out += rows + ref_rows
        if in_place:
            bak = _save_map(m, vp, "propmass")
            print(f"  [PROPS] {vn}: {len(renamed)} prop(s) renamed to {PROP_MASS_PREFIX}<mass>_<name>, {len(ref_rows)} reference(s) updated; original saved as {os.path.basename(bak)}")
    out.insert(0, "# prop_physics/_override renamed to " + PROP_MASS_PREFIX + "<mass>_<name> for push_mass.js. Mass = Source1 .phy mass x massScale. "
               + ", ".join(f"{k} {v}" for k, v in summary.items()))
    if not in_place:
        out.insert(1, "# PROP_MASS_IN_PLACE is False: nothing was written to the vmap")
    return out


# ======================================================================== WATER (func_water + underwater post-processing)

_WATER_MAT_CACHE = {}


def _water_material(mat: str):
    """-> (is_water, how) for a material path used in the vmap. The Source1 .vmt's shader decides ("Water");
    with no .vmt found, a "water" in the file name is taken as a guess (logged as such)."""
    key = rel_norm(mat).lower()
    if key in _WATER_MAT_CACHE:
        return _WATER_MAT_CACHE[key]
    res = (False, "")
    if not key.startswith("materials/tools") and not any(x.lower() in key for x in WATER_IGNORE_MATERIALS):
        vmt = (key[:-5] if key.endswith(".vmat") else key) + ".vmt"
        shader = ""
        p, _ = find_in_source1(vmt)
        if p:
            try:
                shader = (parse_vmt(Path(p)).get("shader") or "").lower()
            except Exception:
                shader = ""
        if shader:
            res = (shader == "water", "Source1 shader " + shader)
        else:
            res = ("water" in os.path.basename(key), "GUESSED from the name (no .vmt found in SOURCE1_ROOTS)")
    _WATER_MAT_CACHE[key] = res
    return res


def _fmt_box(lo, hi):
    return "(%s)-(%s)" % (" ".join("%g" % round(x) for x in lo), " ".join("%g" % round(x) for x in hi))


def _box_overlap(a, b, pad=0.0):
    return all(a[0][k] <= b[1][k] + pad and b[0][k] <= a[1][k] + pad for k in range(3))




def _sky_region(m):
    """-> (lo, hi) of the 3D skybox still inside this map (the cluster of toolsskybox faces around the sky_camera), or None"""
    cams = m.by_class("sky_camera")
    if not cams:
        return None
    boxes = []
    for n, _p in m.walk():
        if m.type(n) != b"CMapMesh":
            continue
        md = m.g(n, b"meshData")
        if md is None or not any(b"skybox" in x.lower() for x in m.g(md, b"materials") or []):
            continue
        for _f, P, _k, mt in _mesh_faces_world(m, n):
            if "skybox" in mt.lower() and P:
                boxes.append(([min(q[i] for q in P) for i in range(3)], [max(q[i] for q in P) for i in range(3)]))
    idx = list(range(len(boxes)))

    def find(i):
        while idx[i] != i:
            idx[i] = idx[idx[i]]
            i = idx[i]
        return i
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _box_overlap(boxes[i], boxes[j], 32.0):
                idx[find(i)] = find(j)
    groups = {}
    for i, b in enumerate(boxes):
        groups.setdefault(find(i), []).append(b)
    c = m.origin(cams[0])
    for g in groups.values():
        lo = [min(b[0][k] for b in g) for k in range(3)]
        hi = [max(b[1][k] for b in g) for k in range(3)]
        if all(lo[k] <= c[k] <= hi[k] for k in range(3)):
            return lo, hi
    return None


def _split_water_faces(m, node, keep=None):
    """move the water-material faces of a world mesh into a new mesh (a copy of the node, so it keeps the same transform) and leave the
    rest in the original -> the new mesh element (not attached); `node` itself when every face is water; None when the mesh cannot be
    rebuilt (displacement, error). keep(material) -> True: the face counts as water's (stays with it) although it is not water"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return None
    _bundled_vmapedit()
    _bundled_install("splitfaces")
    sf = sys.modules["splitfaces"]
    if sf.mesh_has_subdivision(m, node):
        return None
    try:
        polys, mats = sf.mesh_read(m, node)
    except Exception:
        return None
    def is_wet(p):
        mt = mats[p["attrs"]["materialindex:0"]]
        return _water_material(mt)[0] or bool(keep and keep(mt))
    wet = [p for p in polys if is_wet(p)]
    dry = [p for p in polys if not is_wet(p)]
    if not dry:
        return node
    if not wet:
        return None
    new = m.import_subtree(m, node, {}, [m.next_node_id()])
    if sf.mesh_write(m, new, wet) or sf.mesh_write(m, node, dry):
        return None
    return new


def _water_box_mesh(m, dmx, mn, mx, cache):
    """A new axis-aligned box CMapMesh from mn to mx (world space), copied from a box mesh already in the map (the importer's
    post_processing_volume box first) with its 8 corners moved. -> mesh element, or None when the map has no box mesh to copy."""
    if "tmpl" not in cache:
        cache["tmpl"] = None
        def is_box(n):
            md = m.g(n, b"meshData")
            if md is None or len(m.g(md, b"faceEdgeIndices") or []) != 6:
                return False
            if any(m.g(n, b"angles", [0, 0, 0])) or list(m.g(n, b"scales", [1, 1, 1])) != [1.0, 1.0, 1.0]:
                return False
            for st in m.g(m.g(md, b"vertexData"), b"streams") or []:
                if m.E[st]["name"].startswith(b"position"):
                    pts = m.g(st, b"data")
                    return len(pts) == 8 and all(len({round(p[k], 3) for p in pts}) == 2 for k in range(3))
            return False
        pp_boxes = [c for e in m.by_class("post_processing_volume") for c in m.children(e) if m.type(c) == b"CMapMesh"]
        for n in pp_boxes + [n for n, _ in m.walk() if m.type(n) == b"CMapMesh"]:
            if is_box(n):
                cache["tmpl"] = (n, n in pp_boxes)
                break
    if cache["tmpl"] is None:
        return None
    tnode, from_pp = cache["tmpl"]
    j = m.import_subtree(m, tnode, {}, [m.next_node_id()])
    md = m.g(j, b"meshData")
    if not from_pp:
        dmx.set_(m.E[md], b"materials", dmx.ARR + dmx.T_STR, [WATER_PP_MATERIAL.encode()])
    for st in m.g(m.g(md, b"faceData"), b"streams") or []:
        if m.E[st]["name"].startswith(b"materialindex"):
            a = dmx.geta(m.E[st], b"data")
            a[2] = [0] * len(a[2])
    dmx.set_(m.E[j], b"origin", dmx.T_V3, [0.0, 0.0, 0.0])
    for st in m.g(m.g(md, b"vertexData"), b"streams") or []:
        if m.E[st]["name"].startswith(b"position"):
            a = dmx.geta(m.E[st], b"data")
            pts = a[2]
            ctr = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
            a[2] = [[float(mx[k]) if p[k] > ctr[k] else float(mn[k]) for k in range(3)] for p in pts]
    return j


def _state_read(path):
    try:
        return set(x.strip() for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#"))
    except OSError:
        return set()


def _water_vmat_missing(mat, export_dir: Path):
    """True when neither the addon nor CS2 (as a real, non-stub vmat) has this material"""
    return not (export_dir / mat).exists() and not (cs2_file_exists(mat + "_c") and not cs2_vmat_is_stub(mat))


@lru_cache(maxsize=1)
def _cs2_water_materials():
    """stock CS2 water materials (materials/liquids/*.vmat in the VPKs / extracted folders), real ones only"""
    names = sorted(n[:-2] for n in cs2_listdir("materials/liquids") if n.lower().endswith(".vmat_c"))
    mats = ["materials/liquids/" + n for n in names if not cs2_vmat_is_stub("materials/liquids/" + n)]
    return [x for x in mats if "water" in os.path.basename(x)] or mats


def _water_replacement(mat):
    """-> (CS2 material to use instead of a missing Source1 water material, why) or (None, why)"""
    chosen = {k.lower(): v for k, v in WATER_MATERIAL_MAP.items()}.get(mat.lower())
    if chosen:
        return chosen, "WATER_MATERIAL_MAP"
    if WATER_MATERIAL != "auto":
        return (WATER_MATERIAL, "WATER_MATERIAL") if WATER_MATERIAL else (None, 'WATER_MATERIAL is ""')
    cands = _cs2_water_materials()
    if not cands:
        return None, "no CS2 water material found under materials/liquids/ (CS2 install not read?)"
    import difflib
    base = os.path.splitext(os.path.basename(mat))[0].lower()
    special = ("chemical", "spill", "toxic", "sewer", "slime", "acid", "blood", "oil", "lava", "puddle", "mud")
    pool = [c for c in cands if not any(w in os.path.basename(c).lower() and w not in base for w in special)] or cands   # plain water for plain water
    def ratio(c):
        return difflib.SequenceMatcher(None, base, os.path.splitext(os.path.basename(c))[0]).ratio()
    best = max(pool, key=ratio)
    if ratio(best) < 0.6:                        # no real name match: a general-purpose CS2 water
        general = [c for g in ("nuke_water", "boathouse_water", "aztecwater", "shack_water") for c in pool if os.path.basename(c).lower().startswith(g)]
        if general:
            return general[0], f"no CS2 water with a similar name: general water ({len(cands)} CS2 water materials found)"
    return best, f"closest name of the {len(cands)} CS2 water materials"


def _merge_water_tiles(m, dmx, rows):
    """Undo large-face splitting on water: a water surface that an earlier lighting rebuild cut into 512-unit tiles (one single-face mesh per
    tile) is joined back into one face - one tile is stretched over the whole surface with its texture coordinates continued, the others are
    removed. Only when the tiles cover exactly one rectangle and share one continuous texture mapping. -> number of surfaces joined"""
    par = m.parent_of()
    tiles = []
    for n, p in par.items():
        if m.type(n) != b"CMapMesh" or any(m.g(n, b"angles", [0, 0, 0])) or list(m.g(n, b"scales", [1, 1, 1])) != [1.0, 1.0, 1.0]:
            continue
        md = m.g(n, b"meshData")
        if md is None or len(m.g(md, b"faceEdgeIndices") or []) != 1:
            continue
        g = lambda k: m.g(md, k)  # noqa: E731
        st = {m.E[x]["name"].split(b":")[0]: x for grp in (b"vertexData", b"faceVertexData", b"faceData") for x in m.g(g(grp), b"streams") or []}
        if not all(k in st for k in (b"position", b"texcoord", b"materialindex")):
            continue
        pos, tc = m.g(st[b"position"], b"data"), m.g(st[b"texcoord"], b"data")
        mats = [x.decode("utf-8", "replace") for x in g(b"materials") or []]
        mi = m.g(st[b"materialindex"], b"data")[0]
        if len(pos) != 4 or mi >= len(mats) or not _water_material(mats[mi])[0] or len({round(q[2], 3) for q in pos}) != 1:
            continue
        o = m.g(n, b"origin", [0, 0, 0])
        evx, nxt, vdi, evd, he = g(b"edgeVertexIndices"), g(b"edgeNextIndices"), g(b"vertexDataIndices"), g(b"edgeVertexDataIndices"), g(b"faceEdgeIndices")[0]
        loop = []
        for _ in range(4):
            loop.append((vdi[evx[he]], evd[he]))
            he = nxt[he]
        w = [(pos[v][0] + o[0], pos[v][1] + o[1]) for v, _ in loop]
        uv = [tc[f] for _, f in loop]
        det = (w[1][0] - w[0][0]) * (w[2][1] - w[0][1]) - (w[1][1] - w[0][1]) * (w[2][0] - w[0][0])
        if abs(det) < 1e-6:
            continue
        fit = []                                 # u, v = a x + b y + c (world x, y)
        for k in (0, 1):
            du, dv = uv[1][k] - uv[0][k], uv[2][k] - uv[0][k]
            a = (du * (w[2][1] - w[0][1]) - (w[1][1] - w[0][1]) * dv) / det
            b = ((w[1][0] - w[0][0]) * dv - du * (w[2][0] - w[0][0])) / det
            fit.append((a, b, uv[0][k] - a * w[0][0] - b * w[0][1]))
        if max(abs(fit[k][0] * x + fit[k][1] * y + fit[k][2] - uv[i][k]) for k in (0, 1) for i, (x, y) in enumerate(w)) > 1e-3:
            continue
        tiles.append(dict(n=n, p=p, mat=mats[mi], z=round(pos[0][2] + o[2], 2), w=w, fit=fit, loop=loop, pos=st[b"position"], tc=st[b"texcoord"], o=o))
    groups = {}
    for t in tiles:
        groups.setdefault((t["p"], t["mat"], t["z"]), []).append(t)
    joined = 0
    for (_p, mat, z), ts in groups.items():
        idx = list(range(len(ts)))

        def find(i):
            while idx[i] != i:
                idx[i] = idx[idx[i]]
                i = idx[i]
            return i
        bb = [(min(x for x, _ in t["w"]), min(y for _, y in t["w"]), max(x for x, _ in t["w"]), max(y for _, y in t["w"])) for t in ts]
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                if bb[i][0] <= bb[j][2] + 0.5 and bb[j][0] <= bb[i][2] + 0.5 and bb[i][1] <= bb[j][3] + 0.5 and bb[j][1] <= bb[i][3] + 0.5:
                    idx[find(i)] = find(j)
        sets = {}
        for i in range(len(ts)):
            sets.setdefault(find(i), []).append(i)
        for members in sets.values():
            if len(members) < 2:
                continue
            x0, y0 = min(bb[i][0] for i in members), min(bb[i][1] for i in members)
            x1, y1 = max(bb[i][2] for i in members), max(bb[i][3] for i in members)
            where = "%s z %g (%d tiles, %g x %g)" % (mat, z, len(members), round(x1 - x0), round(y1 - y0))
            area = sum((bb[i][2] - bb[i][0]) * (bb[i][3] - bb[i][1]) for i in members)
            if abs(area - (x1 - x0) * (y1 - y0)) > 0.005 * (x1 - x0) * (y1 - y0):
                rows.append(f"TILES_KEPT\t{where}\tthe tiles do not make one rectangle: left split")
                continue
            f0 = ts[members[0]]["fit"]
            if any(abs(ts[i]["fit"][k][q] - f0[k][q]) > (1e-3 if q < 2 else 0.05) for i in members for k in (0, 1) for q in range(3)):
                rows.append(f"TILES_KEPT\t{where}\ttexture mapping differs between tiles: left split")
                continue
            keep = ts[min(members, key=lambda i: ts[i]["n"])]
            o, pdata, tdata = keep["o"], dmx.geta(m.E[keep["pos"]], b"data")[2], dmx.geta(m.E[keep["tc"]], b"data")[2]
            cx, cy = sum(x for x, _ in keep["w"]) / 4, sum(y for _, y in keep["w"]) / 4
            for (v, f), (x, y) in zip(keep["loop"], keep["w"]):
                wx, wy = (x1 if x > cx else x0), (y1 if y > cy else y0)
                pdata[v] = [wx - o[0], wy - o[1], pdata[v][2]]
                tdata[f] = [f0[k][0] * wx + f0[k][1] * wy + f0[k][2] for k in (0, 1)]
            for i in members:
                if ts[i] is not keep:
                    a = dmx.geta(m.E[ts[i]["p"]], b"children")
                    a[2] = [c for c in a[2] if c != ts[i]["n"]]
            rows.append(f"TILES_JOINED\t{where}\twater surface cut into tiles by an earlier lighting rebuild, joined back into one face")
            joined += 1
    return joined


def fix_trigger_materials(vmap_paths):
    """Trigger volumes store whatever material their faces had when the brush was tied to the entity (e.g. a copied floor), but Hammer shows
    them as toolstrigger, so the stored material is invisible in the editor. Every face of a TRIGGER_CLASSES entity that is not TRIGGER_MATERIAL
    is set to it. Rerun-safe (patched triggers are already right). -> list of log lines"""
    dmx = _bundled_vmapedit()[1]
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        rows = []
        for e in m.entities():
            cls = m.cls(e)
            if not any(cls.startswith(c) if c.endswith("_") else cls == c for c in TRIGGER_CLASSES):
                continue
            for c in m.children(e):
                md = m.g(c, b"meshData") if m.type(c) == b"CMapMesh" else None
                if md is None:
                    continue
                mats = [x.decode("utf-8", "replace") for x in m.g(md, b"materials") or []]
                mi = next((x for x in m.g(m.g(md, b"faceData"), b"streams") or [] if m.E[x]["name"].startswith(b"materialindex")), None)
                if mi is None:
                    continue
                used = [mats[i] if 0 <= i < len(mats) else "?" for i in m.g(mi, b"data")]
                wrong = collections.Counter(x for x in used if x.lower() != TRIGGER_MATERIAL)
                if not wrong:
                    continue
                dmx.set_(m.E[md], b"materials", dmx.ARR + dmx.T_STR, [TRIGGER_MATERIAL.encode()])
                a = dmx.geta(m.E[mi], b"data")
                a[2] = [0] * len(a[2])
                b = m.bounds(c)
                rows.append(f"PATCHED\t{cls} {m.name(e) or '(unnamed)'}\t{_fmt_box(*b) if b else ''}\t"
                            + ", ".join(f"{n} face(s) {x}" for x, n in sorted(wrong.items())) + f" -> {os.path.basename(TRIGGER_MATERIAL)}")
        if not rows:
            _keep_map(m, vp)
        else:
            try:
                bak = _save_map(m, vp, "triggers")
            except OSError as ex:
                out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
                continue
            print(f"  [TRIGGERS] {vn}: {len(rows)} trigger mesh(es) set to {os.path.basename(TRIGGER_MATERIAL)}; original saved as {os.path.basename(bak)}")
            total += len(rows)
            out += [f"# {vn}"] + rows
    out.insert(0, f"# trigger volumes whose faces stored a material other than {os.path.basename(TRIGGER_MATERIAL)} (Hammer draws them as "
                  f"{os.path.basename(TRIGGER_MATERIAL)} anyway, so this was invisible in the editor): {total} mesh(es) patched")
    return out









def make_spark_particle(export_dir: Path, kv: dict):
    """env_spark -> particles/spark/<name>.vpcf, following CS:GO's spark code (FX_ElectricSpark, from memory of the Source SDK): every
    0.1 + random(0, MaxDelay) s a burst of about Magnitude^2 x 3 white streaks (effects/spark, additive) leaves the entity at 100-300 u/s,
    falls with gravity 800 and fades out; TrailLength stretches the streaks. Directional (512) fires along the entity's forward axis,
    otherwise upwards in a spread. The bursts are averaged into a continuous emitter. -> (ok, message, vpcf path)"""
    mag = max(1, int(_steam_num(kv, "Magnitude", 1)))
    trail = max(1, int(_steam_num(kv, "TrailLength", 1)))
    delay = max(0.0, _steam_num(kv, "MaxDelay", 0))
    flags = int(_steam_num(kv, "spawnflags", 0))
    directional = bool(flags & 512)
    name = f"env_spark_m{mag}_t{trail}_d{_sn(delay)}{'_dir' if directional else ''}"
    rel = f"particles/spark/{name}.vpcf"
    vmt, _r = find_in_source1("materials/effects/spark.vmt")
    try:
        data = parse_vmt(Path(vmt)) if vmt else {"basetexture": "effects/spark"}
        color_rel, _a = export_color_texture(export_dir, data.get("basetexture") or "effects/spark", False)
        vtex_rel = os.path.splitext(color_rel)[0] + "_particle.vtex"
        write_simple_vtex(export_dir / vtex_rel, color_rel)
    except Exception as e:
        return False, f"spark texture (CS:GO materials/effects/spark) not exported: {e}", rel
    per_burst = mag * mag * 3.0
    rate = per_burst / (0.1 + delay / 2.0)
    life = 0.35 + 0.15 * mag
    s0, s1 = 100.0, 300.0
    if directional:
        vmin, vmax = [s0, -0.4 * s1, -0.4 * s1], [s1, 0.4 * s1, 0.4 * s1]
    else:
        vmin, vmax = [-0.4 * s1, -0.4 * s1, s0 * 0.5], [0.4 * s1, 0.4 * s1, s1]
    ind = "\t\t"
    inits = [ind + "{", ind + '\t_class = "C_INIT_CreateWithinSphereTransform"', ind + "\tm_LocalCoordinateSystemSpeedMin = "]
    inits += _pvec_literal_lines(ind + "\t", vmin)
    inits.append(ind + "\tm_LocalCoordinateSystemSpeedMax = ")
    inits += _pvec_literal_lines(ind + "\t", vmax)
    inits.append(ind + "},")
    inits += _init_float_lines(ind, life * 0.6, life, 1)
    inits += _init_float_lines(ind, 0.5, 2.0, 3)
    body = VPCF66_HEADER + (
        "{\n\t_class = \"CParticleSystemDefinition\"\n\tm_nBehaviorVersion = 12\n"
        f"\tm_nMaxParticles = {int(rate * life) + 16}\n"
        "\tm_ConstantColor = [ 255, 255, 255, 255 ]\n"
        "\tm_Renderers =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_RenderTrails\"\n"
        "\t\t\tm_nOutputBlendMode = \"PARTICLE_OUTPUT_BLEND_MODE_ADD\"\n"
        f"\t\t\tm_flLengthScale = {0.03 * trail:g}\n"
        "\t\t\tm_vecTexturesInput =\n\t\t\t[\n\t\t\t\t{\n"
        f"\t\t\t\t\tm_hTexture = resource:\"{vtex_rel}\"\n" "\t\t\t\t},\n\t\t\t]\n\t\t},\n\t]\n"
        f"\tm_Emitters =\n\t[\n\t\t{{\n\t\t\t_class = \"C_OP_ContinuousEmitter\"\n\t\t\tm_flEmitRate = {rate:.2f}\n\t\t}},\n\t]\n"
        "\tm_Initializers =\n\t[\n" + "\n".join(inits) + "\n\t]\n"
        "\tm_Operators =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_BasicMovement\"\n\t\t\tm_Gravity = [ 0.0, 0.0, -800.0 ]\n\t\t},\n"
        "\t\t{\n\t\t\t_class = \"C_OP_FadeOutSimple\"\n\t\t\tm_flFadeOutTime = 0.5\n\t\t},\n"
        "\t\t{\n\t\t\t_class = \"C_OP_Decay\"\n\t\t},\n\t]\n}\n")
    out = export_dir / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if not (out.is_file() and out.read_text(encoding="utf-8", errors="replace") == body):
        out.write_text(body, encoding="utf-8")
    return True, f"{per_burst:g} sparks per burst, every 0.1-{0.1 + delay:g} s, texture {vtex_rel}", rel


def convert_fire_and_sparks(vmap_paths, export_dir: Path):
    """env_fire: the entity stays (nothing it did is visible or hurts in CS2, tested; kept so its inputs still resolve); an info_particle_system <name>_fx at the same
    place and parent plays particles/burning_fx/env_fire_<size>[_smoke] - the particle CS:GO's own code played (C_FireSmoke), which CS2
    ships - and every output that starts / extinguishes the fire also starts / stops it (a fire that is not Infinite also stops after its
    Duration, as in CS:GO). env_spark -> info_particle_system with a particle generated from CS:GO's spark code (make_spark_particle);
    StartSpark / StopSpark -> Start / Stop, SparkOnce -> Start then Stop 0.15 s later. Rerun-safe. -> log lines"""
    dmx = _bundled_vmapedit()[1]
    out, total = [], 0
    sizes = ("tiny", "small", "medium", "large")
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        rows, changed = [], False
        names = {m.name(e).lower() for e in m.entities() if m.name(e)}
        fires = {}                               # fire name -> [(new entity name, on input, off input)], duration, infinite
        box_cache = {}
        for e in m.by_class("env_fire"):
            nm = m.name(e)
            try:
                size, flags, dur = float(m.P(e, "firesize", "64") or 64), int(float(m.P(e, "spawnflags", "0") or 0)), float(m.P(e, "health", "30") or 30)
            except ValueError:
                size, flags, dur = 64.0, 0, 30.0
            if not nm and not flags & 4:
                rows.append(f"FIRE_SKIPPED\tenv_fire (unnamed) at {' '.join(str(round(x)) for x in m.origin(e))}\tnot named and not Start On: nothing can light it")
                continue
            made = []
            o = m.origin(e)
            fx, hurt = (nm + "_fx") if nm else "", (nm + "_hurt") if nm else ""
            # the flames
            if not (fx and fx.lower() in names):
                eff = f"particles/burning_fx/env_fire_{sizes[min(3, int(size // FIRE_SIZE_STEP))]}{'' if flags & 2 else '_smoke'}.vpcf"
                if cs2_vpk().loaded and not cs2_file_exists(eff + "_c"):
                    rows.append(f"FIRE_NO_PARTICLE\tenv_fire {nm or '(unnamed)'}\t{eff} is not in this CS2 install; no flames added")
                else:
                    props = {"targetname": fx, "effect_name": eff, "start_active": "1" if flags & 4 else "0"}
                    if m.P(e, "parentname"):
                        props["parentname"] = m.P(e, "parentname")
                    m.new_entity("info_particle_system", o, props, m.g(e, b"angles", [0, 0, 0]))
                    made.append((fx, "Start", "Stop"))
                    rows.append(f"FIRE\tenv_fire {nm or '(unnamed)'}\t+ info_particle_system {fx or '(unnamed)'} {eff} (firesize {size:g}"
                                f"{', smokeless' if flags & 2 else ''}{', starts on' if flags & 4 else ''}{'' if flags & 1 else f', burns {dur:g} s'})")
            # the damage as a point_hurt (FIRE_HURT_MODE): one hit every FIRE_POINT_HURT_DELAY s inside a sphere round the damage box
            if FIRE_HURT and FIRE_HURT_MODE == "point_hurt" and hurt:
                half = max(16.0, size * FIRE_HURT_WIDTH / 2.0)
                centre = [o[0], o[1], o[2] + size / 2.0]
                radius = math.sqrt(2.0) * half
                want = {"Damage": "%g" % FIRE_POINT_HURT_DAMAGE, "DamageDelay": "%g" % FIRE_POINT_HURT_DELAY, "DamageRadius": "%g" % round(radius, 1),
                        "DamageType": "8", "DamageTarget": ""}
                old = [h for h in m.by_class("trigger_hurt", "point_hurt") if m.name(h).lower() == hurt.lower()]
                if old:
                    h = old[0]
                    if m.cls(h) == "trigger_hurt":          # made by an earlier run in "trigger" mode: becomes the point_hurt
                        m.setp(h, "classname", "point_hurt")
                        a_ = dmx.geta(m.E[h], b"children")
                        a_[2] = [c for c in a_[2] if m.type(c) != b"CMapMesh"]
                        dmx.set_(m.E[h], b"origin", dmx.T_V3, [float(x) for x in centre])
                        for k in ("damage", "damagecap", "damagetype", "spawnflags", "StartDisabled"):
                            m.delp(h, k)
                        for src, cc in m.all_conns():
                            if dmx.get(cc, b"targetName").decode("utf-8", "replace").lower() == hurt.lower():
                                il = dmx.get(cc, b"inputName").decode("utf-8", "replace").lower()
                                if il in ("enable", "disable"):
                                    dmx.set_(cc, b"inputName", dmx.T_STR, b"TurnOn" if il == "enable" else b"TurnOff")
                        rows.append(f"FIRE_HURT_CONVERTED\t{hurt}\ttrigger_hurt -> point_hurt (FIRE_HURT_MODE); Enable/Disable outputs -> TurnOn/TurnOff")
                        changed = True
                    for k, v in want.items():
                        if (m.P(h, k) or "") != v:
                            m.setp(h, k, v)
                            changed = True
                elif hurt.lower() not in names:
                    props = dict(want, targetname=hurt)
                    if m.P(e, "parentname"):
                        props["parentname"] = m.P(e, "parentname")
                    m.new_entity("point_hurt", centre, props)
                    made.append((hurt, "TurnOn", "TurnOff"))
                    rows.append(f"FIRE_HURT\tenv_fire {nm}\t+ point_hurt {hurt}: {FIRE_POINT_HURT_DAMAGE:g} burn damage every {FIRE_POINT_HURT_DELAY:g} s within {radius:.0f} u "
                                f"(covers CS:GO's {2 * half:g} x {2 * half:g} x {size:g} damage box; CS2's env_fire does no damage)")
                    if flags & 4:
                        rows.append(f"FIRE_NOTE\t{hurt}\tthe fire starts on, but a point_hurt only starts with TurnOn: fire TurnOn at it on map spawn / round start")
            # the damage as a burn trigger_hurt over the flames (FIRE_HURT_MODE "trigger")
            if FIRE_HURT and FIRE_HURT_MODE != "point_hurt" and hurt:      # a point_hurt from an earlier run -> back to the trigger_hurt
                for h in [h for h in m.by_class("point_hurt") if m.name(h).lower() == hurt.lower()]:
                    half = max(16.0, size * FIRE_HURT_WIDTH / 2.0)
                    mn, mx = [o[0] - half, o[1] - half, o[2]], [o[0] + half, o[1] + half, o[2] + size]
                    box = _water_box_mesh(m, dmx, mn, mx, box_cache)
                    if box is None:
                        continue
                    dmx.set_(m.E[m.g(box, b"meshData")], b"materials", dmx.ARR + dmx.T_STR, [TRIGGER_MATERIAL.encode()])
                    m.setp(h, "classname", "trigger_hurt")
                    for k in ("Damage", "DamageDelay", "DamageRadius", "DamageType", "DamageTarget"):
                        m.delp(h, k)
                    for k, v in (("damage", "%g" % FIRE_HURT_DAMAGE), ("damagecap", "%g" % FIRE_HURT_DAMAGE), ("damagetype", "8"), ("spawnflags", "1"),
                                 ("StartDisabled", "0" if flags & 4 else "1")):
                        m.setp(h, k, v)
                    dmx.set_(m.E[h], b"origin", dmx.T_V3, [(mn[k] + mx[k]) / 2 for k in range(3)])
                    dmx.geta(m.E[h], b"children")[2].append(box)
                    for src, cc in m.all_conns():
                        if dmx.get(cc, b"targetName").decode("utf-8", "replace").lower() == hurt.lower():
                            il = dmx.get(cc, b"inputName").decode("utf-8", "replace").lower()
                            if il in ("turnon", "turnoff"):
                                dmx.set_(cc, b"inputName", dmx.T_STR, b"Enable" if il == "turnon" else b"Disable")
                    rows.append(f"FIRE_HURT_CONVERTED\t{hurt}\tpoint_hurt -> trigger_hurt (FIRE_HURT_MODE); TurnOn/TurnOff outputs -> Enable/Disable")
                    changed = True
            if FIRE_HURT and FIRE_HURT_MODE != "point_hurt" and hurt and hurt.lower() in names:     # made on an earlier run: keep its damage in step with FIRE_HURT_DAMAGE
                want = "%g" % FIRE_HURT_DAMAGE
                half = max(16.0, size * FIRE_HURT_WIDTH / 2.0)
                for h in m.by_class("trigger_hurt"):
                    bb = m.bounds(h) if m.name(h).lower() == hurt.lower() else None
                    if bb and abs((bb[1][0] - bb[0][0]) - 2 * half) > 0.5:
                        mn, mx = [o[0] - half, o[1] - half, o[2]], [o[0] + half, o[1] + half, o[2] + size]
                        box = _water_box_mesh(m, dmx, mn, mx, box_cache)
                        if box is not None:
                            dmx.set_(m.E[m.g(box, b"meshData")], b"materials", dmx.ARR + dmx.T_STR, [TRIGGER_MATERIAL.encode()])
                            a_ = dmx.geta(m.E[h], b"children")
                            a_[2] = [c for c in a_[2] if m.type(c) != b"CMapMesh"] + [box]
                            rows.append(f"FIRE_HURT_RESIZED\t{hurt}\t{bb[1][0] - bb[0][0]:g} -> {2 * half:g} wide")
                            changed = True
                    if m.name(h).lower() == hurt.lower() and (m.P(h, "damage") != want or m.P(h, "damagecap") != want):
                        rows.append(f"FIRE_HURT_UPDATED\t{hurt}\tdamage {m.P(h, 'damage')} -> {want}/s")
                        m.setp(h, "damage", want)
                        m.setp(h, "damagecap", want)
                        changed = True
            if FIRE_HURT and FIRE_HURT_MODE != "point_hurt" and not (hurt and hurt.lower() in names):
                half = max(16.0, size * FIRE_HURT_WIDTH / 2.0)
                mn, mx = [o[0] - half, o[1] - half, o[2]], [o[0] + half, o[1] + half, o[2] + size]
                box = _water_box_mesh(m, dmx, mn, mx, box_cache)
                if box is None:
                    rows.append(f"FIRE_NO_HURT\tenv_fire {nm or '(unnamed)'}\tno box mesh in the map to copy for the trigger; add a trigger_hurt by hand")
                else:
                    dmx.set_(m.E[m.g(box, b"meshData")], b"materials", dmx.ARR + dmx.T_STR, [TRIGGER_MATERIAL.encode()])
                    dmg = FIRE_HURT_DAMAGE
                    props = {"targetname": hurt, "damage": "%g" % dmg, "damagecap": "%g" % dmg, "damagetype": "8", "spawnflags": "1",
                             "StartDisabled": "0" if flags & 4 else "1"}
                    if m.P(e, "parentname"):
                        props["parentname"] = m.P(e, "parentname")
                    h = m.new_entity("trigger_hurt", [(mn[k] + mx[k]) / 2 for k in range(3)], props)
                    dmx.geta(m.E[h], b"children")[2].append(box)
                    made.append((hurt, "Enable", "Disable"))
                    rows.append(f"FIRE_HURT\tenv_fire {nm or '(unnamed)'}\t+ trigger_hurt {hurt or '(unnamed)'} {dmg:g} burn damage/s over "
                                f"{2 * half:g} x {2 * half:g} x {size:g} (FIRE_HURT_DAMAGE; CS:GO did 1/s, the lowest a CS2 trigger_hurt can do is 2/s; CS2's env_fire does no damage)")
            if made:
                changed = True
                if nm:
                    names.update(x[0].lower() for x in made)
                    fires[nm.lower()] = (made, dur, bool(flags & 1))
                if flags & 4 and not flags & 1:
                    rows.append(f"FIRE_NOTE\tenv_fire {nm or '(unnamed)'}\tstarts on and burns {dur:g} s: the flames / hurt start on but are not switched off after it")
        for src, cc in list(m.all_conns()):
            o_, t, i, p, d, n = m.conn_tuple(cc)
            f = fires.get(t.lower())
            if not f:
                continue
            made, dur, infinite = f
            for ent, on, off in made:
                if i.lower() == "startfire":
                    m.add_conn(src, o_, ent, on, "", d, n)
                    if not infinite:
                        m.add_conn(src, o_, ent, off, "", d + dur, n)
                elif i.lower() in ("extinguish", "extinguishtemporary"):
                    m.add_conn(src, o_, ent, off, "", d, n)
        # an earlier version turned env_spark itself into the particle: undo that (env_spark still makes its sound in CS2)
        for e in m.by_class("info_particle_system"):
            if (m.P(e, "effect_name", "") or "").lower().startswith("particles/spark/") and m.P(e, "Magnitude") is not None and not m.name(e).lower().endswith("_fx"):
                m.setp(e, "classname", "env_spark")
                m.delp(e, "effect_name")
                m.delp(e, "start_active")
                for src, cc in m.all_conns():
                    if m.name(e) and dmx.get(cc, b"targetName").decode("utf-8", "replace").lower() == m.name(e).lower():
                        il = dmx.get(cc, b"inputName").decode("utf-8", "replace").lower()
                        if il in ("start", "stop"):
                            dmx.set_(cc, b"inputName", dmx.T_STR, b"StartSpark" if il == "start" else b"StopSpark")
                rows.append(f"SPARK_RESTORED\t{m.name(e) or '(unnamed)'}\tback to env_spark (it keeps its sound); the particle goes on a separate _fx entity")
                changed = True
        sparks = {}
        for e in m.by_class("env_spark"):
            nm = m.name(e)
            fx = (nm + "_fx") if nm else ""
            kv = {a_[0].decode(): a_[2].decode("utf-8", "replace") for a_ in m.props(e)["attrs"] if a_[1] == dmx.T_STR}
            ok, msg, rel = make_spark_particle(export_dir, kv)      # rebuilt every run
            if not ok:
                rows.append(f"SPARK_NOT_PORTED\tenv_spark {nm or '(unnamed)'}\t{msg}")
                continue
            flags = int(_steam_num(kv, "spawnflags", 0))
            if fx and fx.lower() in names:
                continue                         # done on an earlier run
            if not nm and not flags & 64:
                rows.append(f"SPARK_SKIPPED\tenv_spark (unnamed) at {' '.join(str(round(x)) for x in m.origin(e))}\tnot named and not Start ON: nothing can start it")
                continue
            props = {"targetname": fx, "effect_name": rel, "start_active": "1" if flags & 64 else "0"}
            if m.P(e, "parentname"):
                props["parentname"] = m.P(e, "parentname")
            m.new_entity("info_particle_system", m.origin(e), props, m.g(e, b"angles", [0, 0, 0]))
            if fx:
                names.add(fx.lower())
                sparks[nm.lower()] = fx
            rows.append(f"SPARK\tenv_spark {nm or '(unnamed)'}\t+ info_particle_system {fx or '(unnamed)'} {rel} ({msg}); the env_spark stays for its sound")
            changed = True
        lost = collections.Counter()
        for src, cc in list(m.all_conns()):
            o, t, i, p, d, n = m.conn_tuple(cc)
            fx = sparks.get(t.lower())
            if not fx:
                continue
            il = i.lower()
            if il in ("startspark", "stopspark"):
                m.add_conn(src, o, fx, "Start" if il == "startspark" else "Stop", "", d, n)
            elif il == "sparkonce":
                m.add_conn(src, o, fx, "Start", "", d, n)
                m.add_conn(src, o, fx, "Stop", "", d + 0.15, n)
            elif il == "togglespark":
                lost[(fx, i)] += 1
        for (t, i), k in sorted(lost.items()):
            rows.append(f"INPUT_LOST\t{t}\t{i} x{k}: particles have no toggle input; the spark toggles but its particle does not")
        if not changed:
            _keep_map(m, vp)
            out += [f"# {vn}"] + rows if rows else []
            continue
        try:
            bak = _save_map(m, vp, "fire_sparks")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        nf = sum(r.startswith(("FIRE\t", "FIRE_HURT\t")) for r in rows)
        ns = sum(r.startswith("SPARK\t") for r in rows)
        print(f"  [FIRE/SPARKS] {vn}: {nf} flame particle(s) / burn trigger(s) added, {ns} env_spark converted; original saved as {os.path.basename(bak)}")
        total += nf + ns
        out += [f"# {vn}"] + rows
    out.insert(0, f"# env_fire / env_spark visuals ported: {total}")
    return out


def make_trail_particle(export_dir: Path, kv: dict):
    """env_spritetrail -> particles/spritetrail/<name>.vpcf: a ribbon (rope renderer) of particles dropped at the entity every frame, so it
    trails behind whatever the entity is parented to, like Source1. Texture from the sprite's .vmt, width startwidth -> endwidth and alpha
    fading over `lifetime`, rendercolor / renderamt, additive for rendermode 5. -> (ok, message, vpcf path)"""
    sprite = _steam_str(kv, "spritename", "sprites/laserbeam.vmt").replace("\\", "/").lower()
    sprite = sprite[:-4] if sprite.endswith((".vmt", ".spr")) else sprite
    life = max(0.05, _steam_num(kv, "lifetime", 0.5))
    w0, w1 = max(0.1, _steam_num(kv, "startwidth", 8.0)), max(0.0, _steam_num(kv, "endwidth", 1.0))
    col = [int(float(x)) for x in (_steam_str(kv, "rendercolor", "255 255 255").split() + ["255"] * 3)[:3]]
    amt = int(max(0.0, min(255.0, _steam_num(kv, "renderamt", 255))))
    add = _steam_str(kv, "rendermode", "5").strip() in ("5", "9", "kRenderTransAdd", "kRenderGlow")
    name = "trail_%s_l%s_w%s_%s_c%d_%d_%d_a%d%s" % (os.path.basename(sprite), _sn(life), _sn(w0), _sn(w1), col[0], col[1], col[2], amt, "" if add else "_blend")
    rel = f"particles/spritetrail/{name}.vpcf"
    vmt, _r = find_in_source1(f"materials/{sprite}.vmt")
    if not vmt:
        return False, f"no Source1 materials/{sprite}.vmt for the trail texture", rel
    try:
        data = parse_vmt(Path(vmt))
        if not data["basetexture"]:
            return False, f"{vmt} has no $basetexture", rel
        color_rel, _anim = export_color_texture(export_dir, data["basetexture"], False)
        vtex_rel = os.path.splitext(color_rel)[0] + "_particle.vtex"
        write_simple_vtex(export_dir / vtex_rel, color_rel)
    except Exception as e:
        return False, f"trail texture export failed: {e}", rel
    rate = 30.0
    body = VPCF66_HEADER + (
        "{\n\t_class = \"CParticleSystemDefinition\"\n\tm_nBehaviorVersion = 12\n"
        f"\tm_nMaxParticles = {int(life * rate) + 8}\n"
        f"\tm_ConstantColor = [ {col[0]}, {col[1]}, {col[2]}, {amt} ]\n"
        f"\tm_flConstantRadius = {w0 / 2.0:g}\n"
        "\tm_Renderers =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_RenderRopes\"\n"
        f"\t\t\tm_nOutputBlendMode = \"{'PARTICLE_OUTPUT_BLEND_MODE_ADD' if add else 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA'}\"\n"
        "\t\t\tm_vecTexturesInput =\n\t\t\t[\n\t\t\t\t{\n"
        f"\t\t\t\t\tm_hTexture = resource:\"{vtex_rel}\"\n" "\t\t\t\t},\n\t\t\t]\n\t\t},\n\t]\n"
        f"\tm_Emitters =\n\t[\n\t\t{{\n\t\t\t_class = \"C_OP_ContinuousEmitter\"\n\t\t\tm_flEmitRate = {rate:g}\n\t\t}},\n\t]\n"
        + _vpcf_spawn_initializers(life) +
        "\tm_Operators =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_Decay\"\n\t\t},\n"
        f"\t\t{{\n\t\t\t_class = \"C_OP_InterpolateRadius\"\n\t\t\tm_flStartScale = 1.0\n\t\t\tm_flEndScale = {w1 / w0:g}\n\t\t}},\n"
        "\t\t{\n\t\t\t_class = \"C_OP_FadeOutSimple\"\n\t\t\tm_flFadeOutTime = 1.0\n\t\t},\n\t]\n}\n")
    out = export_dir / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if not (out.is_file() and out.read_text(encoding="utf-8", errors="replace") == body):
        out.write_text(body, encoding="utf-8")
    return True, f"trail from {vmt} -> {vtex_rel}", rel


def convert_steam_and_trails(vmap_paths, export_dir: Path):
    """env_steam / env_steamjet -> info_particle_system (particles/steam/<from its keyvalues>.vpcf, built if missing; start_active =
    InitialState, which is Off when the key is absent, as in Source1) and env_spritetrail -> info_particle_system with a generated trail.
    Keyvalues, name, parent, origin and angles stay. Inputs that targeted them: TurnOn -> Start, TurnOff -> Stop; others (Toggle,
    SetScale...) have no info_particle_system equivalent and are listed. Rerun-safe. -> log lines"""
    dmx = _bundled_vmapedit()[1]
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        rows, conv = [], {}
        for e in m.by_class("info_particle_system"):    # converted on an earlier run: rebuild the trail file every run, like other assets
            eff = (m.P(e, "effect_name", "") or "").replace("\\", "/").lower()
            if eff.startswith("particles/spritetrail/") and m.P(e, "spritename"):
                kv = {a[0].decode(): a[2].decode("utf-8", "replace") for a in m.props(e)["attrs"] if a[1] == dmx.T_STR}
                ok, msg, rel = make_trail_particle(export_dir, kv)
                if not ok:
                    rows.append(f"TRAIL_NOT_REBUILT\tinfo_particle_system {m.name(e) or '(unnamed)'}\t{msg}")
                elif rel.lower() != eff:
                    m.setp(e, "effect_name", rel)
                    conv[e] = "info_particle_system"
                    rows.append(f"TRAIL_RENAMED\t{m.name(e) or '(unnamed)'}\t{eff} -> {rel}")
        for e in m.by_class("env_steam", "env_steamjet", "env_spritetrail"):
            cls = m.cls(e)
            kv = {a[0].decode(): a[2].decode("utf-8", "replace") for a in m.props(e)["attrs"] if a[1] == dmx.T_STR}
            if cls == "env_spritetrail":
                ok, msg, rel = make_trail_particle(export_dir, kv)
                start = "1"
            else:
                nm = steam_particle_name(kv)
                rel = f"particles/steam/{nm}.vpcf"
                ok, msg = True, "steam particle"
                if not (export_dir / rel).is_file():
                    ok, msg, _ln = make_steam_particle(export_dir, nm, kv)
                start = "1" if _steam_str(kv, "InitialState", "0").strip() == "1" else "0"
            if not ok:
                rows.append(f"NOT_CONVERTED\t{cls} {m.name(e) or '(unnamed)'}\t{_steam_status(msg)}")
                continue
            m.setp(e, "classname", "info_particle_system")
            m.setp(e, "effect_name", rel)
            m.setp(e, "start_active", start)
            conv[e] = cls
            rows.append(f"CONVERTED\t{cls} {m.name(e) or '(unnamed)'}\t-> info_particle_system {rel}\tstart_active {start}")
        glow_fix = collections.Counter()           # importer's env_sprite conversions keep ShowSprite / HideSprite, which they do not have
        glows = {m.name(e).lower(): m.cls(e) for e in m.by_class("env_particle_glow", "info_particle_system") if m.name(e)}
        for src, cc in m.all_conns():
            t = dmx.get(cc, b"targetName").decode("utf-8", "replace").lower()
            i = dmx.get(cc, b"inputName").decode("utf-8", "replace")
            new = {"showsprite": "Start", "hidesprite": "Stop"}.get(i.lower())
            if t in glows and new:
                dmx.set_(cc, b"inputName", dmx.T_STR, new.encode())
                glow_fix[(glows[t], t, i, new)] += 1
            elif t in glows and i.lower() == "togglesprite":
                glow_fix[(glows[t], t, i, None)] += 1
        for (c, t, i, new), k in sorted(glow_fix.items(), key=lambda kv: (kv[0][1], kv[0][2])):
            rows.append(f"GLOW_INPUT\t{c} {t}\t{i} x{k} -> {new}" if new else f"INPUT_LOST\t{c} {t}\t{i} x{k}: no toggle input on particles; those outputs do nothing now")
        if any(new for (_c, _t, _i, new) in glow_fix):
            conv.setdefault(-1, "glow inputs")
        names = {m.name(e).lower(): c for e, c in conv.items() if e != -1 and m.name(e)}
        bad = collections.Counter()
        for src, cc in m.all_conns():
            t = dmx.get(cc, b"targetName").decode("utf-8", "replace").lower()
            if t not in names:
                continue
            i = dmx.get(cc, b"inputName").decode("utf-8", "replace")
            new = {"turnon": "Start", "turnoff": "Stop"}.get(i.lower())
            if new:
                dmx.set_(cc, b"inputName", dmx.T_STR, new.encode())
            elif i.lower() not in ("start", "stop", "stopplayendcap", "destroyimmediately", "kill", "setparent", "clearparent"):
                bad[(names[t], t, i)] += 1
        for (c, t, i), k in sorted(bad.items()):
            rows.append(f"INPUT_LOST\t{c} {t}\t{i} x{k}: info_particle_system has no such input (Source1-only); those outputs do nothing now")
        if not conv:
            _keep_map(m, vp)
            out += [f"# {vn}"] + rows if rows else []
            continue
        try:
            bak = _save_map(m, vp, "steam_trails")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        nconv = sum(1 for e in conv if e != -1)
        print(f"  [STEAM/TRAILS] {vn}: {nconv} env_steam / env_spritetrail -> info_particle_system, {sum(k for (_c, _t, _i, n), k in glow_fix.items() if n)} ShowSprite/HideSprite -> Start/Stop; original saved as {os.path.basename(bak)}")
        total += nconv
        out += [f"# {vn}"] + rows
    out.insert(0, f"# env_steam / env_spritetrail converted to info_particle_system: {total}")
    return out


def fix_tool_material_paths(vmap_paths, export_dir: Path):
    """materials/tools*.vmat referenced at the wrong path (missing in the addon and CS2, but CS2 has materials/tools/<same file name>):
    the reference is rewritten in every mesh's material list. Rerun-safe. -> log lines"""
    dmx = _bundled_vmapedit()[1]
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        fixed = {}
        for n, _p in m.walk():
            md = m.g(n, b"meshData") if m.type(n) == b"CMapMesh" else None
            a = dmx.geta(m.E[md], b"materials") if md is not None else None
            if not a:
                continue
            new = []
            for x in a[2]:
                mat = x.decode("utf-8", "replace").replace("\\", "/")
                low = mat.lower()
                base = os.path.basename(low)
                if base.startswith("tools") and not low.startswith("materials/tools/"):
                    good = "materials/tools/" + base
                    if not (export_dir / low).is_file() and not cs2_file_exists(low + "_c") and cs2_file_exists(good + "_c"):
                        fixed[mat] = fixed.get(mat, 0) + 1
                        x = good.encode()
                new.append(x)
            a[2] = new
        if not fixed:
            _keep_map(m, vp)
            continue
        try:
            bak = _save_map(m, vp, "tool_paths")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        print(f"  [TOOL PATHS] {vn}: {sum(fixed.values())} mesh material reference(s) moved to materials/tools/; original saved as {os.path.basename(bak)}")
        total += sum(fixed.values())
        out += [f"# {vn}"] + [f"FIXED\t{k}\t-> materials/tools/{os.path.basename(k.lower())}\t{v} mesh(es)" for k, v in sorted(fixed.items())]
    out.insert(0, f"# tool materials referenced outside materials/tools/: {total} reference(s) fixed")
    return out


def _vmf_world_solids(vmf_path):
    """-> [dict(sides=[dict(n (outward unit normal), d, mat, ua, us, va, vs)], lo, hi)] for the WORLD brushes of the .vmf"""
    text = Path(vmf_path).read_text(encoding="utf-8", errors="replace")
    wi, ei = text.find("\nworld"), text.find("\nentity")
    text = text[wi:ei if ei > wi else len(text)] if wi >= 0 else ""
    out = []
    for sd in re.findall(r"\n\s*solid\s*\n\s*\{(.*?)\n\t\}", text, re.S):
        sides, pts = [], []
        for blk in re.findall(r"side\s*\n\s*\{(.*?)\n\s*\}", sd, re.S):
            kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', blk))
            pl = [[float(x) for x in t.split()] for t in re.findall(r"\(([^)]*)\)", kv.get("plane", ""))]
            ua = re.match(r"\[([^\]]*)\]\s*(\S+)", kv.get("uaxis", ""))
            va = re.match(r"\[([^\]]*)\]\s*(\S+)", kv.get("vaxis", ""))
            if len(pl) != 3 or not ua or not va or "dispinfo" in blk:
                continue
            try:
                n = _v_norm(_v_cross(_v_sub(pl[1], pl[0]), _v_sub(pl[2], pl[0])))
            except (ValueError, ZeroDivisionError):
                continue
            pts += pl
            sides.append(dict(n=n, d=_v_dot(n, pl[0]), mat=kv.get("material", "").lower().replace("\\", "/"),
                              ua=[float(x) for x in ua.group(1).split()[:4]], us=float(ua.group(2)),
                              va=[float(x) for x in va.group(1).split()[:4]], vs=float(va.group(2))))
        if len(sides) < 4:
            continue
        cen = [sum(q[j] for q in pts) / len(pts) for j in range(3)]
        for x in sides:                                  # outward
            if _v_dot(x["n"], cen) - x["d"] > 0:
                x["n"] = [-c for c in x["n"]]
                x["d"] = -x["d"]
        out.append(dict(sides=sides, lo=[min(q[j] for q in pts) for j in range(3)], hi=[max(q[j] for q in pts) for j in range(3)]))
    return out


def _mesh_faces_world(m, node):
    """-> [(face index, [(x, y, z)] world positions, [faceVertexData index], material)] for a CMapMesh"""
    _ve = _bundled_vmapedit()[0]
    md = m.g(node, b"meshData")
    if md is None:
        return []
    pos = next((m.g(st, b"data") for st in m.g(m.g(md, b"vertexData"), b"streams") or [] if m.E[st]["name"].startswith(b"position")), None)
    if pos is None:
        return []
    evx, nxt, vdi, evd = m.g(md, b"edgeVertexIndices"), m.g(md, b"edgeNextIndices"), m.g(md, b"vertexDataIndices"), m.g(md, b"edgeVertexDataIndices")
    o, a = m.g(node, b"origin", [0, 0, 0]), m.g(node, b"angles", [0, 0, 0])
    mats = [x.decode("utf-8", "replace") for x in m.g(md, b"materials") or []]
    mi = next((m.g(st, b"data") for st in m.g(m.g(md, b"faceData"), b"streams") or [] if m.E[st]["name"].startswith(b"materialindex")), None)
    fdi = m.g(md, b"faceDataIndices")
    out = []
    for f, he0 in enumerate(m.g(md, b"faceEdgeIndices") or []):
        loop, he = [], he0
        for _ in range(64):
            loop.append((vdi[evx[he]], evd[he]))
            he = nxt[he]
            if he == he0:
                break
        j = mi[fdi[f] if fdi else f] if mi else -1
        out.append((f, [_ve.xform(list(pos[v]), o, a) for v, _ in loop], [k for _, k in loop], mats[j] if 0 <= j < len(mats) else ""))
    return out


def restore_culled_faces(vmap_paths, vmf_path):
    """Valve's importer culls a brush face that another brush covers. A Source1 water brush is not solid, but when every side of it is
    a water material the importer treats it as one, so the face under it (typically the sea floor) disappears. Each such face of an
    axis-aligned world box brush is rebuilt: the brush comes back as a box mesh, nodraw except that side, which gets the .vmf material,
    texture axes, scale and shift (texture coordinates from the Source1 texture size, 512 if unknown). Rerun-safe. -> log lines"""
    if not vmf_path or not Path(vmf_path).is_file():
        return ["# culled faces: skipped - no .vmf"]
    vmapedit, dmx = _bundled_vmapedit()
    solids = _vmf_world_solids(vmf_path)

    def toolish(mt):                             # sides the importer drops anyway (sky faces are kept: they seal the map)
        return mt.startswith("tools/") and "skybox" not in mt
    def axis(n):
        k = max(range(3), key=lambda i: abs(n[i]))
        return k if abs(abs(n[k]) - 1.0) < 1e-4 else None
    water = [s_ for s_ in solids if all("water" in x["mat"] for x in s_["sides"])]
    cands = []                                   # (solid, side) visible, lying on a water brush's face
    for s_ in solids:
        if s_ in water or len(s_["sides"]) != 6 or any(axis(x["n"]) is None for x in s_["sides"]):
            continue
        for x in s_["sides"]:
            if toolish(x["mat"]) or "water" in x["mat"]:
                continue
            k = axis(x["n"])
            plane = s_["hi"][k] if x["n"][k] > 0 else s_["lo"][k]
            o1, o2 = [i for i in range(3) if i != k]
            for w in water:
                wplane = w["lo"][k] if x["n"][k] > 0 else w["hi"][k]
                if abs(wplane - plane) < 1.5 and w["lo"][o1] < s_["hi"][o1] and s_["lo"][o1] < w["hi"][o1] and w["lo"][o2] < s_["hi"][o2] and s_["lo"][o2] < w["hi"][o2]:
                    # only the part the water covers was culled (a sky wall keeps its face above the water): rebuild that slice of the brush
                    lo, hi = list(s_["lo"]), list(s_["hi"])
                    for i in (o1, o2):
                        lo[i], hi[i] = max(lo[i], w["lo"][i]), min(hi[i], w["hi"][i])
                    cands.append((s_, x, k, plane, lo, hi))
    out, total = [], 0
    if not cands:
        return ["# faces culled under water brushes: none in the .vmf"]
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        faces = []                               # (axis, plane, lo2, hi2) of every axis-aligned face in the map
        for n, _p in m.walk():
            if m.type(n) != b"CMapMesh":
                continue
            for _f, P, _k, fmat in _mesh_faces_world(m, n):
                fl = fmat.lower()
                if len(P) < 3 or ("tools" in fl and "skybox" not in fl) or "water" in fl or "liquids/" in fl:
                    continue                     # the water's own faces (and nodraw) do not count
                for k in range(3):
                    if max(q[k] for q in P) - min(q[k] for q in P) < 0.05:
                        faces.append((k, P[0][k], [min(q[i] for q in P) for i in range(3)], [max(q[i] for q in P) for i in range(3)]))
                        break
        rows, cache = [], {}
        for s_, x, k, plane, blo, bhi in cands:
            c = [(blo[i] + bhi[i]) / 2 for i in range(3)]
            c[k] = plane
            if any(fk == k and abs(fp - plane) < 0.5 and all(lo[i] - 1 <= c[i] <= hi[i] + 1 for i in range(3) if i != k) for fk, fp, lo, hi in faces):
                continue                         # the face is there
            box = _water_box_mesh(m, dmx, blo, bhi, cache)
            if box is None:
                rows.append(f"NO_TEMPLATE\t{x['mat']}\t{_fmt_box(blo, bhi)}\tno box mesh in the map to copy; rebuild this brush by hand")
                continue
            mat = "materials/" + x["mat"] + ".vmat"
            ts = _overlay_texture_size(x["mat"]) or (512, 512, 1.0)
            md = m.g(box, b"meshData")
            dmx.set_(m.E[md], b"materials", dmx.ARR + dmx.T_STR, [b"materials/tools/toolsnodraw.vmat", mat.encode()])
            streams = {m.E[st]["name"].split(b":")[0]: st for grp in (b"faceVertexData", b"faceData") for st in m.g(m.g(md, grp), b"streams") or []}
            fdi = m.g(md, b"faceDataIndices")
            for f, P, fv, _m in _mesh_faces_world(m, box):
                nrm = _v_norm(_v_cross(_v_sub(P[1], P[0]), _v_sub(P[2], P[0])))
                fd = fdi[f] if fdi else f
                mine = abs(abs(nrm[k]) - 1.0) < 1e-3 and abs(P[0][k] - plane) < 0.5
                dmx.geta(m.E[streams[b"materialindex"]], b"data")[2][fd] = 1 if mine else 0
                if not mine:
                    continue
                ua, va = x["ua"], x["va"]
                if b"textureAxisU" in streams:
                    dmx.geta(m.E[streams[b"textureAxisU"]], b"data")[2][fd] = [float(v) for v in ua]
                    dmx.geta(m.E[streams[b"textureAxisV"]], b"data")[2][fd] = [float(v) for v in va]
                if b"textureScale" in streams:
                    dmx.geta(m.E[streams[b"textureScale"]], b"data")[2][fd] = [float(x["us"]), float(x["vs"])]
                tc = dmx.geta(m.E[streams[b"texcoord"]], b"data")[2]
                for q, i in zip(P, fv):
                    tc[i] = [(_v_dot(q, ua[:3]) / (x["us"] or 1.0) + ua[3]) / ts[0], (_v_dot(q, va[:3]) / (x["vs"] or 1.0) + va[3]) / ts[1]]
            dmx.geta(m.E[m.world], b"children")[2].append(box)
            rows.append(f"RESTORED\t{x['mat']}\t{_fmt_box(blo, bhi)}\tface at {'xyz'[k]} {plane:g} was culled where a water brush lies against it: rebuilt as a box, nodraw except that face")
        if not any(r.startswith("RESTORED") for r in rows):
            _keep_map(m, vp)
            out += [f"# {vn}"] + rows if rows else []
            continue
        try:
            bak = _save_map(m, vp, "culled_faces")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        n_ = sum(r.startswith("RESTORED") for r in rows)
        print(f"  [CULLED FACES] {vn}: {n_} face(s) culled under water rebuilt; original saved as {os.path.basename(bak)}")
        total += n_
        out += [f"# {vn}"] + rows
    out.insert(0, f"# brush faces the importer culled under water brushes: {total} rebuilt")
    return out


def _vmf_brush_solids(vmf_path):
    """-> (entities, world): entities = [(classname, name, origin, [solid], materials)] for brush entities with an origin key, world = [solid]
    for worldspawn; solid = (outward unit normals N (k,3), offsets D (k,)), in the entity's LOCAL frame (origin subtracted) for entities."""
    import numpy as np
    text = Path(vmf_path).read_text(encoding="utf-8", errors="replace")

    def solids(block, o):
        out = []
        for sd in re.findall(r"\n\s*solid\s*\n\s*\{(.*?)\n\s*\}\s*(?=\n\s*(?:solid|editor|\}|connections|group|$))", block, re.S):
            planes = [[tuple(float(c) for c in p_.split()) for p_ in re.findall(r"\(([^)]*)\)", pl)] for pl in re.findall(r'"plane"\s+"([^"]*)"', sd)]
            planes = [pl for pl in planes if len(pl) == 3]
            if len(planes) < 4:
                continue
            N, D, P = [], [], []
            for a, b, c in planes:
                a, b, c = (np.array(x) - o for x in (a, b, c))
                n = np.cross(b - a, c - a)
                ln = np.linalg.norm(n)
                if ln < 1e-9:
                    continue
                N.append(n / ln)
                D.append(float(n @ a / ln))
                P += [a, b, c]
            N, D = np.array(N), np.array(D)
            cen = np.mean(P, axis=0)
            flip = np.where(N @ cen - D > 0, -1.0, 1.0)      # outward: the brush's centre is behind every plane
            sm = {x.lower() for x in re.findall(r'"material"\s+"([^"]*)"', sd)}
            visual = any(not (x.startswith("tools/") or "water" in x or "skybox" in x) for x in sm)
            out.append((N * flip[:, None], D * flip, np.min(P, axis=0), np.max(P, axis=0), visual))
        return out
    ents, world = [], []
    wi = text.find("\nworld")
    ei = text.find("\nentity")
    if wi >= 0:
        world = solids(text[wi:ei if ei > wi else len(text)], np.zeros(3))
    for mm in re.finditer(r"\nentity\s*\n\{(.*?)\n\}", text, re.S):
        b = mm.group(1)
        kv = dict(re.findall(r'^\t"([^"]+)"\s+"([^"]*)"', b, re.M))
        if "origin" not in kv or "\tsolid" not in b:
            continue
        try:
            o = np.array([float(x) for x in kv["origin"].split()[:3]])
        except ValueError:
            continue
        sol = solids(b, o)
        if sol:
            mats = {os.path.basename(x).lower() for x in re.findall(r'"material"\s+"([^"]*)"', b)}
            ents.append((kv.get("classname", ""), kv.get("targetname", ""), o, sol, mats))
    return ents, world


def remove_origin_duplicates(vmap_paths, vmf_path, backup_dir):
    """Valve's importer leaves a copy of each brush entity's brushes in the world, at the entity's LOCAL coordinates (around 0 0 0): extra
    geometry in the middle of the map (S2ZE porting guide 1.2.3). A world mesh is deleted when (1) every vertex (95%+) lies on the surface of a
    .vmf brush entity's brushes moved to the origin, (2) its materials are ones those entities use, and (3) it is not on the .vmf's own world
    brushes (so real geometry at the origin stays). Rerun-safe. -> log lines"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return ["# origin duplicates: skipped - needs numpy"]
    if not vmf_path or not Path(vmf_path).is_file():
        return ["# origin duplicates: skipped - no .vmf"]
    import numpy as np
    vmapedit, dmx = _bundled_vmapedit()
    ents, world = _vmf_brush_solids(vmf_path)
    world_vis = [sl for sl in world if sl[4]]            # not water / sky / tool-only brushes (a copy can sit on a water surface)

    def on_surface(P, sols, tol=1.0):
        on = np.zeros(len(P), bool)
        lo, hi = P.min(0), P.max(0)
        for N, D, slo, shi, _vis in sols:
            if (hi < slo - tol).any() or (lo > shi + tol).any():
                continue
            mx = (P @ N.T - D).max(1)
            on |= (mx <= tol) & (mx >= -tol)
        return on
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        par = m.parent_of()
        rows, gone = [], []
        for n, p in par.items():
            if m.type(n) != b"CMapMesh":
                continue
            q, inent = p, False
            while q is not None:
                if m.type(q) == b"CMapEntity":
                    inent = True
                    break
                q = par.get(q)
            if inent:
                continue
            pts = m.mesh_points(n)
            q = p
            while q is not None and m.type(q) == b"CMapGroup":     # group transforms
                pts = [vmapedit.xform(x, m.g(q, b"origin", [0, 0, 0]), m.g(q, b"angles", [0, 0, 0])) for x in pts]
                q = par.get(q)
            if len(pts) < 3:
                continue
            P = np.array(pts)
            lo, hi = P.min(0), P.max(0)
            near = [e for e in ents if any(not ((hi < sl[2] - 1).any() or (lo > sl[3] + 1).any()) for sl in e[3])]
            if not near:
                continue
            on = on_surface(P, [sl for e in near for sl in e[3]])
            if on.mean() < 0.95:
                continue
            if world_vis and on_surface(P, world_vis).mean() >= 0.9:
                continue                                 # also lies on real (visible) world brushes: keep
            md = m.g(n, b"meshData")
            mats = {os.path.basename(x.decode("utf-8", "replace")).lower().rsplit(".", 1)[0] for x in m.g(md, b"materials") or []}
            emats = {x.rsplit(".", 1)[0] for e in near for x in e[4]}
            if mats - emats:
                continue
            who = sorted({f"{e[0]} {e[1] or '(unnamed)'}" for e in near if on_surface(P, e[3]).mean() >= 0.5})
            rows.append(f"DELETED\t{_fmt_box(lo.tolist(), hi.tolist())}\t{len(m.g(md, b'faceEdgeIndices') or [])} face(s) "
                        f"{', '.join(sorted(mats))}\tcopy of {', '.join(who[:3]) or 'brush entities'}{' ...' if len(who) > 3 else ''}")
            gone.append((n, p))
        if not gone:
            _keep_map(m, vp)
            continue
        for n, p in gone:
            a = dmx.geta(m.E[p], b"children")
            a[2] = [c for c in a[2] if c != n]
        dead = {n for n, _p in gone}
        for el in m.E:                                   # Hammer's visibility manager lists nodes too
            if el["type"] == b"CVisibilityMgr":
                a = dmx.geta(el, b"nodes")
                if a:
                    a[2] = [c for c in a[2] if c not in dead]
        m.prune()                                        # drop the deleted meshes' data from the file too
        try:
            bak = _save_map(m, vp, "origin_duplicates")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        print(f"  [DUPLICATES] {vn}: {len(gone)} importer copies of brush entities at the origin deleted; original saved as {os.path.basename(bak)}")
        total += len(gone)
        out += [f"# {vn}"] + rows
    out.insert(0, f"# world meshes that were copies of brush entities at the map origin (left by the importer): {total} deleted")
    return out


def _mesh_world_faces(m, par, n, splitfaces, np):
    """faces of a CMapMesh in world space -> (polys, materials, offset) or None when the mesh or a group above it is rotated / scaled
    (offset = what to add to mesh_read positions; mesh nodes keep local verts + their own origin, groups add theirs)"""
    a = m.g(n, b"angles", [0, 0, 0]); sc = m.g(n, b"scales", [1, 1, 1])
    if any(abs(x) > 1e-4 for x in a) or any(abs(x - 1) > 1e-4 for x in sc):
        return None
    off = np.array(m.g(n, b"origin", [0, 0, 0]), float)
    q = par.get(n)
    while q is not None:
        if m.type(q) == b"CMapGroup":
            if any(abs(x) > 1e-4 for x in m.g(q, b"angles", [0, 0, 0])) or any(abs(x - 1) > 1e-4 for x in m.g(q, b"scales", [1, 1, 1])):
                return None
            off = off + np.array(m.g(q, b"origin", [0, 0, 0]), float)
        q = par.get(q)
    try:
        polys, mats = splitfaces.mesh_read(m, n)
    except Exception:
        return None
    return polys, [str(x).lower() for x in mats], off


def _plane_key(P, np):
    """(rounded normal, rounded distance) of a planar polygon, or None for a degenerate one"""
    nrm = np.zeros(3)
    for i in range(1, len(P) - 1):
        nrm += np.cross(P[i] - P[0], P[i + 1] - P[0])
    ln = np.linalg.norm(nrm)
    if ln < 1e-6:
        return None
    nrm /= ln
    return tuple(round(float(x), 3) + 0.0 for x in nrm), round(float(P[0] @ nrm) * 2) / 2


def _in_entity(m, par, n):
    q = par.get(n)
    while q is not None:
        if m.type(q) == b"CMapEntity":
            return q
        q = par.get(q)
    return None


def convert_display_brushes(vmap_paths):
    """func_brush / func_illusionary that only ever display their textures -> world meshes with Physics Type None (non-solid, baked with the
    world around them). Only brushes with a visible face flush in a world surface of the same material. Rerun-safe. -> log lines"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return ["# display brushes: skipped - needs numpy"]
    import numpy as np
    _bundled_vmapedit()
    _bundled_install("splitfaces")
    import splitfaces
    dmx = sys.modules["dmx"]
    out, total = [], 0
    dflt = lambda v, ok: (v or "").strip() in ok
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        par = m.parent_of()
        world = {}                                       # (material, plane) -> [(lo, hi)] of world faces
        for n, p in par.items():
            if m.type(n) != b"CMapMesh" or _in_entity(m, par, n) is not None:
                continue
            r = _mesh_world_faces(m, par, n, splitfaces, np)
            if not r:
                continue
            polys, mats, off = r
            for q in polys:
                mat = mats[q["attrs"].get("materialindex:0", 0)]
                if "materials/tools/" in mat:
                    continue
                P = np.array([c["pos"] for c in q["corners"]]) + off
                k = _plane_key(P, np)
                if k:
                    world.setdefault((mat, k), []).append((P.min(0), P.max(0)))
        rows, done = [], []
        for e in m.by_class("func_brush", "func_illusionary"):
            cls = m.cls(e)
            why = None
            if cls == "func_brush" and m.P(e, "Solidity", "0") != "1":
                why = "can be solid"
            elif m.name(e) or m.P(e, "parentname", ""):
                why = "has a name / parent (may be switched, moved or killed)"
            elif m.conns(e):
                why = "has outputs"
            elif not (dflt(m.P(e, "StartDisabled"), ("", "0")) and dflt(m.P(e, "rendermode"), ("", "0", "kRenderNormal"))
                      and dflt(m.P(e, "renderamt"), ("", "255")) and dflt(m.P(e, "renderfx"), ("", "0", "kRenderFxNone"))
                      and dflt(m.P(e, "rendercolor"), ("", "255 255 255")) and dflt(m.P(e, "vscripts"), ("",))
                      and dflt(m.P(e, "fademindist"), ("", "-1")) and dflt(m.P(e, "fademaxdist"), ("", "0"))):
                why = "starts disabled or has render / fade / script settings"
            meshes = [c for c in m.children(e) if m.type(c) == b"CMapMesh"]
            if not why and (not meshes or len(meshes) != len(m.children(e))):
                why = "no meshes, or other nodes under it"
            flush, faces = False, 0
            if not why:
                for c in meshes:
                    r = _mesh_world_faces(m, par, c, splitfaces, np)
                    if not r:
                        why = "rotated or scaled mesh"
                        break
                    polys, mats, off = r
                    faces += len(polys)
                    for q in polys:
                        mat = mats[q["attrs"].get("materialindex:0", 0)]
                        if "materials/tools/" in mat:
                            continue
                        P = np.array([c2["pos"] for c2 in q["corners"]]) + off
                        k = _plane_key(P, np)
                        lo, hi = P.min(0), P.max(0)
                        if k and any((lo <= whi + 0.1).all() and (wlo <= hi + 0.1).all() for wlo, whi in world.get((mat, k), ())):
                            flush = True
            b = m.bounds(e)
            box = _fmt_box(*b) if b else ""
            if why:
                if cls == "func_illusionary" or m.P(e, "Solidity", "0") == "1":
                    rows.append(f"KEPT\t{cls} {m.name(e) or '(unnamed)'}\t{box}\t{why}")
                continue
            if not flush:
                rows.append(f"KEPT\t{cls} (unnamed)\t{box}\tnot flush with a world surface of the same material (left probe-lit)")
                continue
            done.append((e, meshes))
            rows.append(f"WORLD\t{cls} (unnamed)\t{box}\t{faces} face(s) now world mesh(es) with Physics Type None (still non-solid; baked with the surface around it)")
        if not done:
            _keep_map(m, vp)
            if rows:
                out += [f"# {vn}"] + rows
            continue
        dead = set()
        for e, meshes in done:
            p = par[e]
            a = dmx.geta(m.E[p], b"children")
            i = a[2].index(e)
            for c in meshes:
                dmx.set_(m.E[c], b"physicsType", dmx.T_STR, b"none")
            a[2][i:i + 1] = meshes                       # the meshes take the entity's place (same group / layer)
            dead.add(e)
        for el in m.E:
            if el["type"] == b"CVisibilityMgr":
                a = dmx.geta(el, b"nodes")
                if a:
                    a[2] = [c for c in a[2] if c not in dead]
        m.prune()
        try:
            bak = _save_map(m, vp, "display_brushes")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        print(f"  [DISPLAY BRUSHES] {vn}: {len(done)} non-solid display brush(es) turned into non-solid world meshes; original saved as {os.path.basename(bak)}")
        total += len(done)
        out += [f"# {vn}"] + rows
    out.insert(0, f"# non-solid display-only func_brush / func_illusionary flush in world surfaces, made world meshes with Physics Type None: {total} converted")
    return out


def _material_has_normalmap(mat, export_dir):
    """True / False when it can be told (the addon's .vmat, else the Source1 .vmt), None when not (a CS2 stock material)"""
    vm = Path(export_dir) / mat if export_dir else None
    if vm is not None and vm.is_file():
        t = read_text_safe(str(vm))
        mm = re.search(r'"TextureNormal"\s+"([^"]*)"', t)
        return bool(mm and "default_normal" not in mm.group(1).lower())
    src, _root = find_in_source1(os.path.splitext(mat)[0] + ".vmt")
    if src:
        try:
            return bool(parse_vmt(Path(src)).get("normalmap"))
        except Exception:
            return None
    return None


def fix_mirrored_textures(vmap_paths, export_dir=None, max_share=0.25, max_area=262144.0):
    """Coplanar faces of one material whose texture runs turned 180 degrees against the surrounding surface are given the surrounding
    surface's axes and alignment (texture coordinates fitted from the neighbouring faces): with a normal map they shade as if lit from the
    other side. Only small patches (at most max_share of the surface and max_area sq units) touching the surface, of materials with a normal
    map. Mirrored patches, and materials whose normal map can't be checked, are only listed. Rerun-safe. -> log lines"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return ["# mirrored textures: skipped - needs numpy"]
    import numpy as np
    _bundled_vmapedit()
    _bundled_install("splitfaces")
    import splitfaces
    out, total = [], 0
    unit = lambda v: tuple(round(float(x), 3) + 0.0 for x in np.asarray(v[:3], float) / (np.linalg.norm(v[:3]) or 1))
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        par = m.parent_of()
        meshes, groups = {}, {}
        for n in par:
            if m.type(n) != b"CMapMesh":
                continue
            r = _mesh_world_faces(m, par, n, splitfaces, np)
            if not r:
                continue
            meshes[n] = r
            polys, mats, off = r
            ent = _in_entity(m, par, n)
            for fi, q in enumerate(polys):
                a = q["attrs"]
                mat = mats[a.get("materialindex:0", 0)]
                if "materials/tools/" in mat or "textureAxisU:0" not in a or "texcoord:0" not in q["corners"][0]["fvd"]:
                    continue
                P = np.array([c["pos"] for c in q["corners"]]) + off
                k = _plane_key(P, np)
                if k:
                    groups.setdefault((mat, k, ent), []).append((n, fi, P, unit(a["textureAxisU:0"]), unit(a["textureAxisV:0"])))
        mirrored = 0
        changes, rows, nmap = {}, [], {}              # mesh -> {face index: (axes, fit, tangent)}; material -> has a normal map
        for (mat, (nrm, d), ent), faces in groups.items():
            area = {}
            for f in faces:
                area[(f[3], f[4])] = area.get((f[3], f[4]), 0.0) + splitfaces.poly_area([{"pos": x} for x in f[2]])
            if len(area) < 2:
                continue
            main = max(area, key=area.get)
            tot = sum(area.values())
            for ori in area:
                if ori == main:
                    continue
                pu, pv = np.dot(ori[0], main[0]), np.dot(ori[1], main[1])
                if not (abs(abs(pu) - 1) < 1e-3 and abs(abs(pv) - 1) < 1e-3 and (pu < 0 or pv < 0)):
                    continue                             # not the same lines reversed (a 90-degree turn or skew): leave it
                patch = [f for f in faces if (f[3], f[4]) == ori]
                keys = lambda f: {tuple(np.round(x, 2)) for x in f[2]}
                pk = set().union(*(keys(f) for f in patch))
                nb = [f for f in faces if (f[3], f[4]) == main and len(keys(f) & pk) >= 1]
                parea = area[ori]
                lo = np.min([f[2].min(0) for f in patch], 0); hi = np.max([f[2].max(0) for f in patch], 0)
                where = f"{mat}\t{_fmt_box(lo, hi)}\t{len(patch)} face(s), {parea:.0f} sq u"
                if not nb:
                    continue                             # a separate surface on the same plane (another wall), not a patch in this one
                if (pu < 0) != (pv < 0):
                    mirrored += 1                        # mirrored (not turned): not shown to shade differently, left alone
                    continue
                if parea > max_share * tot or parea > max_area:
                    rows.append(f"KEPT\t{where}\tturned 180 degrees, but too large a share of the surface (may be intentional)")
                    continue
                if mat not in nmap:
                    nmap[mat] = _material_has_normalmap(mat, export_dir)
                if nmap[mat] is False:
                    continue                             # no normal map: the turn does not change the shading
                if nmap[mat] is None:
                    rows.append(f"CHECK\t{where}\tturned 180 degrees against the surface around it; not changed (could not tell whether the material has a normal map)")
                    continue
                # fit texcoord = [x y z 1] @ A on the neighbouring main faces (they share one alignment if the residual is ~0)
                X, T, ref = [], [], None
                for (n, fi, P, _u, _v) in nb:
                    q = meshes[n][0][fi]
                    ref = ref or (n, fi)
                    for c, p in zip(q["corners"], P):
                        X.append([p[0], p[1], p[2], 1.0]); T.append(c["fvd"]["texcoord:0"])
                X, T = np.array(X), np.array(T)
                A = np.linalg.lstsq(X, T, rcond=None)[0]
                if np.abs(X @ A - T).max() > 1e-3:
                    rows.append(f"KEPT\t{where}\tthe surrounding faces do not share one alignment")
                    continue
                rq = meshes[ref[0]][0][ref[1]]
                axes = {k2: rq["attrs"][k2] for k2 in ("textureAxisU:0", "textureAxisV:0", "textureScale:0") if k2 in rq["attrs"]}
                tan = rq["corners"][0]["fvd"].get("tangent:0")
                for (n, fi, P, _u, _v) in patch:
                    changes.setdefault(n, {})[fi] = (axes, A, tan)
                rows.append(f"FIXED\t{where}\ttexture ran turned 180 degrees against the surface around it (normal map lit from the wrong side); now runs with it")
        if mirrored:
            rows.append(f"NOTE\t{mirrored} mirrored patch(es) (texture flipped, not turned) were left alone: a flip has not been seen to change the shading")
        if not changes:
            _keep_map(m, vp)
            if rows:
                out += [f"# {vn}"] + rows
            continue
        bad = 0
        for n, fx in changes.items():
            polys, mats, off = meshes[n]
            for fi, (axes, A, tan) in fx.items():
                q = polys[fi]
                for k2, v in axes.items():
                    q["attrs"][k2] = [float(x) for x in v]
                for c in q["corners"]:
                    p = c["pos"] + off
                    c["fvd"]["texcoord:0"] = np.array([p[0], p[1], p[2], 1.0]) @ A
                    if tan is not None and "tangent:0" in c["fvd"]:
                        c["fvd"]["tangent:0"] = np.array(tan, float)
            err = splitfaces.mesh_write(m, n, polys)
            if err:
                bad += 1
                rows.append(f"ERROR\tmesh node {m.g(n, b'nodeID')}\t{err}; that mesh was left as it was")
        try:
            bak = _save_map(m, vp, "mirrored_textures")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        nfix = sum(1 for r in rows if r.startswith("FIXED"))
        print(f"  [MIRRORED TEXTURES] {vn}: {nfix} texture patch(es) turned 180 degrees now run with their surface; original saved as {os.path.basename(bak)}")
        total += nfix
        out += [f"# {vn}"] + rows
    out.insert(0, f"# small normal-mapped patches whose texture ran turned 180 degrees against the surface around them (shades differently in CS2): {total} fixed")
    return out



def report_heal_triggers(vmap_paths):
    """trigger_hurt with negative damage healed players gradually in CS:GO (damage per second, applied every half second, up to max
    health). CS2 ignores negative damage. Report only: reproducing the gradual heal needs a cs_script. -> log lines"""
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        rows = []
        for e in m.by_class("trigger_hurt"):
            try:
                dmg = float(m.P(e, "damage", "0") or 0)
            except ValueError:
                continue
            if dmg < 0:
                b = m.bounds(e)
                rows.append(f"NEEDS_SCRIPT\t{m.name(e) or '(unnamed)'}{' (parent ' + m.P(e, 'parentname') + ')' if m.P(e, 'parentname') else ''}\t"
                            f"{_fmt_box(*b) if b else ''}\theals {-dmg:g} health/s in CS:GO; does nothing in CS2 until a cs_script heals players inside it")
        _keep_map(m, vp)
        total += len(rows)
        if rows:
            out += [f"# {vn}"] + rows
    out.insert(0, f"# healing trigger_hurt (negative damage) that need a script in CS2: {total}")
    return out


def fix_null_params(vmap_paths):
    """The importer writes "(null)" as the parameter of outputs that had none in Source1 (S2ZE porting guide 1.2.3). Those are set back to "".
    Rerun-safe. -> log lines"""
    dmx = _bundled_vmapedit()[1]
    out, total = [], 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        rows = []
        for e, cc in m.all_conns():
            o, t, i, p, _d, _n = m.conn_tuple(cc)
            if p.strip().lower() == "(null)":
                dmx.set_(cc, b"overrideParam", dmx.T_STR, b"")
                rows.append(f"FIXED\t{m.cls(e)} {m.name(e) or '(unnamed)'}\t{o} -> {t}.{i}\t'(null)' -> ''")
        if not rows:
            _keep_map(m, vp)
            continue
        try:
            bak = _save_map(m, vp, "null_params")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        print(f"  [NULL PARAMS] {vn}: {len(rows)} output parameter(s) '(null)' cleared; original saved as {os.path.basename(bak)}")
        total += len(rows)
        out += [f"# {vn}"] + rows
    out.insert(0, f"# outputs whose parameter the importer wrote as '(null)': {total} cleared")
    return out


_CS2_WEAPONS = set(("weapon_" + w) for w in (
    "ak47 aug awp bizon c4 cz75a deagle decoy elite famas fiveseven flashbang g3sg1 galilar glock hegrenade hkp2000 incgrenade knife "
    "knife_t knifegg m249 m4a1 m4a1_silencer mac10 mag7 molotov mp5sd mp7 mp9 negev nova p250 p90 revolver sawedoff scar20 sg556 "
    "smokegrenade ssg08 taser tec9 ump45 usp_silencer xm1014 healthshot tagrenade").split())
_CSGO_WEAPON_RENAMES = {"weapon_mp5navy": "weapon_mp5sd", "weapon_scout": "weapon_ssg08", "weapon_m3": "weapon_nova", "weapon_sg550": "weapon_scar20",
                        "weapon_sg552": "weapon_sg556", "weapon_tmp": "weapon_mp9", "weapon_p228": "weapon_p250", "weapon_usp": "weapon_usp_silencer",
                        "weapon_galil": "weapon_galilar", "weapon_p2000": "weapon_hkp2000"}
_EQUIP_SLOTS = ["weapon%d" % i for i in range(22)]


def check_player_equips(vmap_paths):
    """game_player_equip, CS:GO -> CS2 (from skink52's cs2-gotchas). Reports; with EQUIP_FIX_INPUTS also renames "Use" inputs on equips to
    TriggerForActivatedPlayer and spells their target exactly like the equip. Rerun-safe (fixed outputs are already right). -> log lines"""
    dmx = _bundled_vmapedit()[1]
    out, nfix, nwarn = [], 0, 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        eqs = m.by_class("game_player_equip")
        if not eqs:
            _keep_map(m, vp)
            continue
        by_name = {}
        for e in eqs:
            if m.name(e):
                by_name.setdefault(m.name(e).lower(), e)
        fired = collections.defaultdict(list)        # equip -> [(source class, source name, output, input, times)]
        fixes = []
        for src, cc in m.all_conns():
            o, t, i, _p, _d, n = m.conn_tuple(cc)
            e = by_name.get(t.lower())
            if e is None:
                continue
            fired[e].append((m.cls(src), m.name(src), o, i, n))
            if not EQUIP_FIX_INPUTS:
                continue
            what = []
            if i.lower() == "use":
                dmx.set_(cc, b"inputName", dmx.T_STR, b"TriggerForActivatedPlayer")
                what.append("Use -> TriggerForActivatedPlayer")
            if t != m.name(e):
                dmx.set_(cc, b"targetName", dmx.T_STR, m.name(e).encode())
                what.append(f"target {t} -> {m.name(e)}")
            if what:
                fixes.append(f"FIXED\t{m.cls(src)} {m.name(src) or '(unnamed)'}\t{o} -> {m.name(e)}\t" + ", ".join(what))
        rows = []
        for e in eqs:
            who = f"game_player_equip {m.name(e) or '(unnamed)'} at {' '.join(str(round(x)) for x in m.origin(e))}"
            pe = m.props(e)
            try:
                sf = int(m.P(e, "spawnflags", "0") or 0)
            except ValueError:
                sf = 0
            slots = [(k, (m.P(e, k, "") or "").strip()) for k in _EQUIP_SLOTS]
            gives = [v.lower() for _k, v in slots if v]
            legacy = [a[0].decode("utf-8", "replace") for a in pe["attrs"] if re.match(rb"(?i)^(weapon_|item_|ammo_)", a[0])]
            items = [v for v in gives if v.startswith("item_")]
            if items:
                rows.append(f"CRASH\t{who}\t{', '.join(items)} in a weapon slot: the CS2 server crashes while loading the map. Keep only weapon_* in the "
                            "slots and give armour another way (cs_script GiveNamedItem)")
            for v in gives:
                if v.startswith("item_"):
                    continue
                if v in _CSGO_WEAPON_RENAMES:
                    rows.append(f"OLD_WEAPON\t{who}\t{v} is the CS:GO name: use {_CSGO_WEAPON_RENAMES[v]}")
                elif v not in _CS2_WEAPONS and not v.startswith("weapon_knife"):
                    rows.append(f"UNKNOWN_WEAPON\t{who}\t{v} is not a CS2 weapon class name")
            if legacy:
                rows.append(f"LEGACY_KEYS\t{who}\tkeys {', '.join(legacy)} (CS:GO's 'weapon_x 1' form) do nothing in CS2: put the names in weapon0..weapon21")
            if EQUIP_SPAWN_STRIP and not sf & 1 and not sf & 2:
                sf |= 2
                m.setp(e, "spawnflags", str(sf))
                fixes.append(f"FIXED\t{who}\tno 'Use Only': CS:GO replaced the spawn loadout with it ({', '.join(gives) or 'nothing: a full strip'}); "
                             "'Strip First' (2) added so CS2 strips at spawn too")
            if not gives and not (sf & 2):
                rows.append(f"EMPTY\t{who}\tgives nothing and does not strip: does nothing")
            src = fired.get(e, [])
            if not sf & 1:
                rows.append(f"SPAWN_EQUIP\t{who}\tno 'Use Only': runs at spawn ({'strip, then ' if sf & 2 else ''}{', '.join(gives) or 'nothing'}). "
                            f"Stripping at spawn / round restart is confirmed in CS2 with 'Strip First'; check in game that {', '.join(gives) if gives else 'players spawn with nothing'}"
                            f"{' arrive' if gives else ''}")
            elif not src:
                rows.append(f"NEVER_FIRED\t{who}\t'Use Only' but no output targets it{'' if m.name(e) else ' (it has no name)'}: it never gives anything")
            for c, nm, o, i, _n in src:
                if c in ("logic_auto", "logic_timer") and i.lower() in ("use", "triggerforactivatedplayer"):
                    rows.append(f"NO_PLAYER\t{who}\t{c} {nm or '(unnamed)'} {o} -> {i}: there is no player activator there, so nobody gets anything (use TriggerForAllPlayers)")
            if not EQUIP_FIX_INPUTS and any(i.lower() == "use" for _c, _nm, _o, i, _n in src):
                rows.append(f"USE_INPUT\t{who}\tfired with 'Use': CS2's equip input is TriggerForActivatedPlayer (EQUIP_FIX_INPUTS changes it)")
            rep = collections.Counter(f"{c} {o}" for c, _nm, o, i, n in src if n < 0 and i.lower() in ("use", "triggerforactivatedplayer")
                                      and c in ("trigger_multiple", "trigger_hurt") and o.lower() in ("onstarttouch", "ontrigger", "onstarttouchall", "ontouching"))
            if rep and any(v for v in gives if not v.startswith("weapon_knife")):
                rows.append(f"STACKING\t{who}\tfired every time by {', '.join(f'{k} x{v}' for k, v in rep.items())}: CS:GO never gave a second copy of a gun the "
                            "player already had, CS2 does, so going back in stacks them (fine if the player loses them in between, e.g. strip or death)")
        nwarn += sum(1 for r in rows if r.split("\t")[0] in ("CRASH", "OLD_WEAPON", "UNKNOWN_WEAPON", "LEGACY_KEYS", "SPAWN_EQUIP", "NEVER_FIRED", "NO_PLAYER", "USE_INPUT"))
        if fixes:
            try:
                bak = _save_map(m, vp, "equips")
            except OSError as ex:
                out.append(f"# {vn}: could not write the map ({ex}); the outputs were not changed. Close Hammer and rerun.")
                fixes = []
            else:
                print(f"  [EQUIPS] {vn}: {len(fixes)} output(s) on game_player_equips fixed; original saved as {os.path.basename(bak)}")
                nfix += len(fixes)
        else:
            _keep_map(m, vp)
        order = {"CRASH": 0, "OLD_WEAPON": 1, "UNKNOWN_WEAPON": 2, "LEGACY_KEYS": 3, "NO_PLAYER": 4, "SPAWN_EQUIP": 5, "NEVER_FIRED": 6, "USE_INPUT": 7, "EMPTY": 8, "STACKING": 9}
        out += [f"# {vn}: {len(eqs)} game_player_equip(s)"] + sorted(rows, key=lambda r: order.get(r.split("\t")[0], 99)) + fixes
    out.insert(0, f"# game_player_equip: {nwarn} thing(s) to check, {nfix} output(s) fixed. CRASH / OLD_WEAPON / LEGACY_KEYS / NO_PLAYER need changing; "
                  "SPAWN_EQUIP and STACKING are behaviour that changed from CS:GO; EMPTY / NEVER_FIRED are probably leftovers")
    return out


_CAST_SHADOWS = {"enabled": 0, "no shadows": 1, "only realtime": 2, "only baked": 3}
_RENDER_MODES = {"normal": "kRenderNormal", "texture": "kRenderTransTexture"}


def fix_brush_render(vmap_paths, backup_dir):
    """Brush-entity render settings (BRUSH_*) on BRUSH_RENDER_CLASSES, and every brush entity's meshes given the entity's Cast Shadows value.
    Every run by default; BRUSH_RENDER_ONCE remembers entities by reference ID (<map>.vmap.brush_render_done.txt) so hand edits are kept. -> log lines"""
    dmx = _bundled_vmapedit()[1]
    cast = _CAST_SHADOWS[BRUSH_CAST_SHADOWS.lower()] if BRUSH_CAST_SHADOWS else None
    mode = (_RENDER_MODES.get(BRUSH_RENDER_MODE.lower(), BRUSH_RENDER_MODE) if BRUSH_RENDER_MODE else None)
    out, n_set, n_sync = [], 0, 0
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        state = Path(backup_dir) / (vn + ".brush_render_done.txt")
        done = _state_read(state)
        new_done, rows = set(), []
        for e in m.entities():
            meshes = [c for c in m.children(e) if m.type(c) == b"CMapMesh"]
            rid = str(m.g(e, b"referenceID", ""))
            if not meshes or not rid or (BRUSH_RENDER_ONCE and rid in done):
                continue
            cls, who = m.cls(e), f"{m.cls(e)} {m.name(e) or '(unnamed)'} @ {' '.join('%g' % round(x) for x in m.origin(e))}"
            ch = []
            if cls in BRUSH_RENDER_CLASSES:
                if cast is not None and m.P(e, "disableshadows") != str(cast):
                    m.setp(e, "disableshadows", str(cast)); ch.append(f"Cast Shadows {BRUSH_CAST_SHADOWS}")
                if mode and m.P(e, "rendermode") != mode:
                    m.setp(e, "rendermode", mode); ch.append(f"Render Mode {BRUSH_RENDER_MODE}")
                if BRUSH_DISABLE_RECEIVING_SHADOWS is not None and m.P(e, "disablereceiveshadows") != str(int(BRUSH_DISABLE_RECEIVING_SHADOWS)):
                    m.setp(e, "disablereceiveshadows", str(int(BRUSH_DISABLE_RECEIVING_SHADOWS))); ch.append("Disable Receiving Shadows %s" % ("on" if BRUSH_DISABLE_RECEIVING_SHADOWS else "off"))
                if BRUSH_DISABLE_MESH_MERGING is not None:
                    k = [c for c in meshes if bool(m.g(c, b"disablemerging", False)) != BRUSH_DISABLE_MESH_MERGING]
                    for c in k:
                        dmx.set_(m.E[c], b"disablemerging", dmx.T_BOOL, bool(BRUSH_DISABLE_MESH_MERGING))
                    if k:
                        ch.append(f"Disable Mesh Merging {'on' if BRUSH_DISABLE_MESH_MERGING else 'off'} ({len(k)} mesh(es))")
                n_set += bool(ch)
            want = m.P(e, "disableshadows")
            if BRUSH_SYNC_MESH_SHADOWS and want is not None and want.strip().isdigit():
                k = [c for c in meshes if m.g(c, b"disableShadows", None) is not None and m.g(c, b"disableShadows") != int(want)]
                for c in k:
                    dmx.set_(m.E[c], b"disableShadows", dmx.T_INT, int(want))
                if k:
                    ch.append(f"{len(k)} mesh(es) Cast Shadows set to the entity's ({want})")
                    n_sync += 1
            if cls in BRUSH_RENDER_CLASSES or ch:
                new_done.add(rid)
            if ch:
                rows.append(f"SET\t{who}\t" + "; ".join(ch))
        if not rows:
            _keep_map(m, vp)
        else:
            try:
                bak = _save_map(m, vp, "brushrender")
            except OSError as ex:
                out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
                continue
            print(f"  [BRUSHES] {vn}: {len(rows)} brush entit(ies) changed; original saved as {os.path.basename(bak)}")
        if BRUSH_RENDER_ONCE and new_done - done:
            try:
                state.write_text("# brush entities already given the BRUSH_* settings (Hammer reference IDs). Delete a line to have it redone.\n"
                                 + "\n".join(sorted(done | new_done)) + "\n", encoding="utf-8")
            except OSError:
                pass
        out += [f"# {vn}"] + rows
    out.insert(0, f"# brush entity render settings: {n_set} entit(ies) of {', '.join(BRUSH_RENDER_CLASSES)} set; {n_sync} entit(ies) had meshes whose Cast Shadows "
                  "differed from the entity's (synced)." + (" Each entity is only processed once (BRUSH_RENDER_ONCE)." if BRUSH_RENDER_ONCE else " Applied every run."))
    return out


def add_water_volumes(vmap_paths, export_dir: Path, backup_dir, vmf_path=None):
    """Source1 water -> CS2 (CS2 only treats water as water - swimming, splashes, sounds - inside a func_water):
      - world meshes using a Source1 "Water" material (the importer sometimes leaves water as plain world geometry: a nodraw shell
        plus the surface cut into 512 tiles) are grouped by touching bounds and each group is moved into a new func_water.
        A group with no depth (surface only) is left alone and listed: it needs a box built in Hammer.
      - func_water_analog (Source1 moving water; no such entity in CS2) -> func_water, movement keys dropped.
      - a water surface an earlier lighting rebuild cut into 512-unit tiles is joined back into one face (_merge_water_tiles).
      - each func_water deeper than WATER_PP_MIN_DEPTH gets an underwater post_processing_volume (WATER_PP_VPOST) over its footprint,
        from 16 u under the bottom to WATER_PP_BELOW_SURFACE under the surface, unless a post_processing_volume already overlaps it or a
        trigger_teleport / trigger_hurt fills it (WATER_PP_SKIP_DEATH_WATER).
    Rerun-safe: water already in a func_water is never touched again, an existing post_processing_volume is never changed, and water that was
    given a volume once is remembered (<name>.vmap.water_done.txt in the backups folder), so a volume you delete stays deleted.
    Underwater LIGHTS are not added here: the lighting rebuild (run after this pass) lights every func_water.
    The vmap is backed up to BACKUP_DIR first. -> list of log lines"""
    dmx = _bundled_vmapedit()[1]
    out, todo = [], []
    tot = {"tiles_joined": 0, "new_func_water": 0, "analog_converted": 0, "volumes_added": 0, "volumes_kept": 0, "death_water": 0, "surface_only": 0}
    water_mats = {}
    swaps = {}                   # (old material, new material, why) -> meshes
    vpost_used = False
    for vp in vmap_paths:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        par = m.parent_of()
        rows, changed = [], False
        state_path = os.path.join(backup_dir, vn + ".water_done.txt")
        done = _state_read(state_path)
        done_new = set()

        def owner(n):
            p = par.get(n)
            while p is not None and p != m.world:
                if m.type(p) == b"CMapEntity":
                    return p
                p = par.get(p)
            return None

        def mesh_mats(n):                        # materials the mesh's faces actually use
            md = m.g(n, b"meshData")
            if md is None:
                return []
            mats_ = [x.decode("utf-8", "replace") for x in (m.g(md, b"materials") or [])]
            mi = next((m.g(st, b"data") for st in m.g(m.g(md, b"faceData"), b"streams") or [] if m.E[st]["name"].startswith(b"materialindex")), None)
            return [mats_[i] for i in sorted(set(mi))if 0 <= i < len(mats_)] if mi else mats_

        vmf_water = []                           # (lo, hi) of the .vmf's world water brushes: the depth a flat imported surface had
        if vmf_path and Path(vmf_path).is_file():
            vmf_water = [(s_["lo"], s_["hi"]) for s_ in _vmf_world_solids(vmf_path) if any("water" in x["mat"] for x in s_["sides"])
                         and not any(x["mat"].startswith("tools/") and "nodraw" not in x["mat"] for x in s_["sides"])]

        def vmf_depth(lo, hi):                   # bottom of the .vmf water brushes under a flat surface (lo, hi), or None
            zs = [wl[2] for wl, wh in vmf_water if abs(wh[2] - hi[2]) < 1.0 and wl[0] < hi[0] and lo[0] < wh[0] and wl[1] < hi[1] and lo[1] < wh[1]]
            return min(zs) if zs and min(zs) < hi[2] - 2 else None

        box_cache_w = {}

        def give_depth(ent, lo, hi):             # a flat func_water gets a nodraw box down to the .vmf water's bottom, so it can be swum in
            bot = vmf_depth(lo, hi)
            if bot is None:
                return False
            box = _water_box_mesh(m, dmx, [lo[0], lo[1], bot], [hi[0], hi[1], hi[2]], box_cache_w)
            if box is None:
                return False
            dmx.set_(m.E[m.g(box, b"meshData")], b"materials", dmx.ARR + dmx.T_STR, [b"materials/tools/toolsnodraw.vmat"])
            dmx.geta(m.E[ent], b"children")[2].append(box)
            rows.append(f"DEPTH\t{m.name(ent) or '(unnamed)'}\t{_fmt_box(lo, hi)}\tflat water surface (the importer dropped the water brush's sides): "
                        f"nodraw box added down to z {bot:g} from the .vmf, so players can swim in it")
            return True

        # ---- water surfaces split into tiles by an earlier lighting rebuild -> one face again
        nj = _merge_water_tiles(m, dmx, rows)
        if nj:
            tot["tiles_joined"] += nj
            changed = True
            par = m.parent_of()

        # ---- func_water_analog -> func_water
        for e in m.by_class("func_water_analog"):
            m.setp(e, "classname", "func_water")
            for k in ("WaveHeight", "startposition", "speed", "movedistance", "movedir"):
                m.delp(e, k)
            rows.append(f"ANALOG\t{m.name(e) or '(unnamed)'}\t{_fmt_box(*(m.bounds(e) or ([0] * 3, [0] * 3)))}\tfunc_water_analog -> func_water (CS2 has no moving water: it no longer moves)")
            tot["analog_converted"] += 1
            changed = True

        # ---- func_water holding sky / wall faces (an earlier run moved a whole mixed world mesh): those faces go back to the world
        for e in m.by_class("func_water"):
            for c in [c for c in m.children(e) if m.type(c) == b"CMapMesh"]:
                if all(_water_material(x)[0] or "toolsnodraw" in x.lower() for x in mesh_mats(c)):
                    continue
                dn = _split_water_faces(m, c, keep=lambda mt: "toolsnodraw" in mt.lower())
                if dn is None or dn is c:
                    continue
                # dn holds the water (+ nodraw) faces, c the rest: swap so the entity keeps the water
                a = dmx.geta(m.E[e], b"children")
                a[2] = [dn if x == c else x for x in a[2]]
                dmx.geta(m.E[m.world], b"children")[2].append(c)
                par[c], par[dn] = m.world, e
                rows.append(f"UNMIXED\t{m.name(e) or '(unnamed)'}\t{_fmt_box(*(m.bounds(c) or ([0] * 3, [0] * 3)))}\tnon-water faces "
                            f"({', '.join(sorted({os.path.basename(x) for x in mesh_mats(c) if not _water_material(x)[0]}))}) moved out of the func_water back into the world")
                changed = True

        # ---- world water -> func_water
        world = []
        for n, _p in list(m.walk()):
            if m.type(n) != b"CMapMesh" or owner(n) is not None:
                continue
            hits = [x for x in mesh_mats(n) if _water_material(x)[0]]
            b = m.bounds(n) if hits else None
            if not b:
                continue
            for x in hits:
                water_mats.setdefault(x, _water_material(x)[1])
            if len(hits) < len(mesh_mats(n)):           # the importer merged the water with other brushes (sky, walls): split the water faces off
                wn = _split_water_faces(m, n)
                if wn is n:
                    world.append((n, b))
                    continue
                if wn is None:
                    rows.append(f"MIXED_MESH\t{_fmt_box(*b)}\ta world mesh holds water and other faces ({', '.join(os.path.basename(x) for x in mesh_mats(n))}) and could not be split: "
                                "left in the world. In Hammer select its water faces, separate them into their own mesh and tie that to a func_water")
                    continue
                rows.append(f"SPLIT\t{_fmt_box(*b)}\twater faces split off a world mesh that also held {', '.join(sorted({os.path.basename(x) for x in mesh_mats(n) if not _water_material(x)[0]}))}")
                par[wn] = _p
                dmx.geta(m.E[_p], b"children")[2].append(wn)
                n, b = wn, m.bounds(wn)
                changed = True
            world.append((n, b))
        idx = list(range(len(world)))

        def find(i):
            while idx[i] != i:
                idx[i] = idx[idx[i]]
                i = idx[i]
            return i
        for i in range(len(world)):
            for j in range(i + 1, len(world)):
                if _box_overlap(world[i][1], world[j][1], 1.0):
                    idx[find(i)] = find(j)
        groups = {}
        for i in range(len(world)):
            groups.setdefault(find(i), []).append(world[i])
        for g in groups.values():
            lo = [min(b[0][k] for _, b in g) for k in range(3)]
            hi = [max(b[1][k] for _, b in g) for k in range(3)]
            if hi[2] - lo[2] < 2 and vmf_depth(lo, hi) is None:
                rows.append(f"SURFACE_ONLY\t{_fmt_box(lo, hi)}\t{len(g)} mesh(es) of water surface with no depth: left as they are. Build a box under it in Hammer and tie it to a func_water to make it swimmable")
                tot["surface_only"] += 1
                continue
            ent = m.new_entity("func_water", [(lo[k] + hi[k]) / 2 for k in range(3)], {})
            for n, _b in g:
                a = dmx.geta(m.E[par[n]], b"children")
                a[2] = [x for x in a[2] if x != n]
                dmx.geta(m.E[ent], b"children")[2].append(n)
            rows.append(f"FUNC_WATER\t(new)\t{_fmt_box(lo, hi)}\t{len(g)} world mesh(es) moved into a new func_water, depth {hi[2] - lo[2]:g}")
            if hi[2] - lo[2] < 2:
                give_depth(ent, lo, hi)
            tot["new_func_water"] += 1
            changed = True

        for e in m.by_class("func_water"):       # func_water left flat by an earlier run
            b = m.bounds(e)
            if b and b[1][2] - b[0][2] < 2 and give_depth(e, b[0], b[1]):
                changed = True

        # ---- underwater post_processing_volume
        waters = m.by_class("func_water")
        for e in waters:
            for c in m.children(e):
                if m.type(c) == b"CMapMesh":
                    for x in mesh_mats(c):
                        if _water_material(x)[0]:
                            water_mats.setdefault(x, _water_material(x)[1])
        if WATER_PP:
            pps = m.by_class("post_processing_volume")
            masters = [e for e in pps if (m.P(e, "master", "0") or "0").strip() == "1"]
            box_cache = {}
            pp_props = dict(WATER_PP_PROPS)
            own_vposts = {WATER_PP_VPOST.lower()} | {x.lower() for x in WATER_PP_OLD_VPOSTS}
            own = [e for e in pps if e not in masters and (m.P(e, "postprocessing", "") or "").lower() in own_vposts]
            for e in own:
                if (m.P(e, "postprocessing", "") or "") != WATER_PP_VPOST:
                    rows.append(f"VPOST_SWITCHED\t{_fmt_box(*(m.bounds(e) or ([0] * 3, [0] * 3)))}\tunderwater volume: {m.P(e, 'postprocessing', '')} -> {WATER_PP_VPOST}")
                    m.setp(e, "postprocessing", WATER_PP_VPOST)
                    changed = True
            for e in pps:                    # an underwater volume an earlier lighting rebuild cut into pieces is not a closed box any more: one box again
                if m.P(e, "postprocessing", "") != WATER_PP_VPOST:
                    continue
                kids = [c for c in m.children(e) if m.type(c) == b"CMapMesh"]
                nf = sum(len(m.g(m.g(c, b"meshData"), b"faceEdgeIndices") or []) for c in kids if m.g(c, b"meshData") is not None)
                bb = m.bounds(e)
                if bb and (len(kids) != 1 or nf != 6):
                    box = _water_box_mesh(m, dmx, bb[0], bb[1], box_cache)
                    if box is not None:
                        a = dmx.geta(m.E[e], b"children")
                        a[2] = [c for c in a[2] if c not in kids] + [box]
                        rows.append(f"VOLUME_REBUILT\t{_fmt_box(*bb)}\tunderwater volume was cut into {len(kids)} mesh(es) / {nf} faces by an earlier "
                                    "lighting rebuild (fixed since): rebuilt as one box, so the game can tell when you are inside it")
                        changed = True
            if WATER_PP_MATCH_MASTER and masters:
                pp_props = {a[0].decode(): a[2].decode("utf-8", "replace") for a in m.props(masters[0])["attrs"] if a[1] == dmx.T_STR
                            and a[0] not in (b"classname", b"targetname", b"globalname", b"postprocessing", b"master", b"vscripts", b"parentname",
                                             b"parentAttachmentName", b"useLocalOffset") and not a[0].startswith(b"local.")}   # settings only, not position / parenting
            if WATER_PP_FADE_TIME is not None:
                pp_props["fadetime"] = "%g" % float(WATER_PP_FADE_TIME)
                if WATER_PP_MASTER_FADE and own:
                    for e in masters:
                        if (m.P(e, "fadetime") or "") != pp_props["fadetime"]:
                            rows.append(f"MASTER_FADE\tmaster post_processing_volume fadetime {m.P(e, 'fadetime') or '(unset)'} -> {pp_props['fadetime']} (WATER_PP_MASTER_FADE)")
                            m.setp(e, "fadetime", pp_props["fadetime"])
                            changed = True
            if (WATER_PP_MATCH_MASTER and masters) or WATER_PP_FADE_TIME is not None:
                for e in own:                    # volumes made by earlier runs: bring them in line with the master (+ WATER_PP_FADE_TIME)
                    diff = {k: v for k, v in pp_props.items() if (m.P(e, k) or "") != v}
                    for k, v in diff.items():
                        m.setp(e, k, v)
                    if diff:
                        rows.append(f"VOLUME_SYNCED\t{_fmt_box(*m.bounds(e))}\tunderwater volume set to {', '.join(f'{k} {v}' for k, v in sorted(diff.items()))}")
                        changed = True
            others = [(e, m.bounds(e)) for e in pps if e not in masters]
            kill = [(m.cls(e), (m.name(e) or "(unnamed)") + (" (starts disabled)" if (m.P(e, "StartDisabled", "0") or "0").strip() == "1" else ""), m.bounds(e))
                    for e in m.by_class("trigger_teleport", "trigger_hurt")]
            kill = [k for k in kill if k[2] and not k[1].endswith("(starts disabled)")]   # a disabled one lets players swim until something enables it

            def area(bb):
                return max(bb[1][0] - bb[0][0], 1.0) * max(bb[1][1] - bb[0][1], 1.0)
            sky = _sky_region(m)
            used_own = set()
            for w in waters:
                b = m.bounds(w)
                if not b:
                    continue
                lo, hi = b
                depth = hi[2] - lo[2]
                if depth <= WATER_PP_MIN_DEPTH:
                    continue
                if sky and all(sky[0][k] - 1 <= lo[k] and hi[k] <= sky[1][k] + 1 for k in range(3)):
                    rows.append(f"SKYBOX_WATER\t{m.name(w) or '(unnamed)'}\t{_fmt_box(lo, hi)}\tin the 3D skybox (around the sky_camera): nobody swims there, no underwater volume")
                    continue
                key = " ".join("%d" % round(x) for x in lo + hi)
                where = f"{m.name(w) or '(unnamed)'}\t{_fmt_box(lo, hi)}"
                mn = [lo[0], lo[1], lo[2] - 16.0]
                mx = [hi[0], hi[1], max(hi[2] - WATER_PP_BELOW_SURFACE, lo[2] + 8.0)]
                mine = [e for e in own if (m.bounds(e) and _box_overlap(m.bounds(e), b))]
                if mine:                         # the script's own volume: kept in step with WATER_PP_BELOW_SURFACE
                    used_own.update(mine)
                    for e in mine:
                        bb = m.bounds(e)
                        if max(abs(bb[0][k] - mn[k]) for k in range(3)) > 0.5 or max(abs(bb[1][k] - mx[k]) for k in range(3)) > 0.5:
                            box = _water_box_mesh(m, dmx, mn, mx, box_cache)
                            if box is not None:
                                a = dmx.geta(m.E[e], b"children")
                                a[2] = [c for c in a[2] if m.type(c) != b"CMapMesh"] + [box]
                                rows.append(f"VOLUME_RESIZED\t{where}\tunderwater volume {_fmt_box(*bb)} -> {_fmt_box(mn, mx)} (top {WATER_PP_BELOW_SURFACE:g} u under the surface)")
                                changed = True
                    done_new.add(key)
                    tot["volumes_kept"] += 1
                    continue
                if key in done:
                    done_new.add(key)
                    rows.append(f"DONE_EARLIER\t{where}\tgiven an underwater volume on an earlier run; left alone (delete this line from {os.path.basename(state_path)} to have it added again)")
                    tot["volumes_kept"] += 1
                    continue
                ov = [e for e, bb in others if bb and _box_overlap(bb, b) and e not in own]
                if ov:
                    rows.append(f"HAS_VOLUME\t{where}\ta post_processing_volume already overlaps it ({', '.join(m.P(e, 'postprocessing', '') or '(no vpost)' for e in ov)}); left alone")
                    done_new.add(key)
                    tot["volumes_kept"] += 1
                    continue
                fitted = [t for t in kill if area(t[2]) <= 2.0 * area(b)]     # sized to this water, not an arena-wide teleport
                if WATER_PP_SKIP_DEATH_WATER and fitted:
                    pts = [[mn[0] + (i + 0.5) * (mx[0] - mn[0]) / 8, mn[1] + (j + 0.5) * (mx[1] - mn[1]) / 8, mn[2] + (k + 0.5) * (mx[2] - mn[2]) / 3]
                           for i in range(8) for j in range(8) for k in range(3)]
                    by = {}
                    inside = 0
                    for p in pts:
                        hit = [t for t in fitted if all(t[2][0][a] <= p[a] <= t[2][1][a] for a in range(3))]
                        inside += bool(hit)
                        for t in hit:
                            by[(t[0], t[1])] = 1
                    if inside >= 0.95 * len(pts):
                        rows.append(f"DEATH_WATER\t{where}\tfilled by {', '.join(f'{c} {n}' for c, n in sorted(by))}: players never stay under, no volume added (WATER_PP_SKIP_DEATH_WATER)")
                        tot["death_water"] += 1
                        continue
                box = _water_box_mesh(m, dmx, mn, mx, box_cache)
                if box is None:
                    rows.append(f"NO_TEMPLATE\t{where}\tno box mesh in the map to copy for the volume; add a post_processing_volume by hand")
                    continue
                pp = m.new_entity("post_processing_volume", [(mn[k] + mx[k]) / 2 for k in range(3)], dict(pp_props, master="0", postprocessing=WATER_PP_VPOST))
                dmx.geta(m.E[pp], b"children")[2].append(box)
                rows.append(f"VOLUME\t{where}\tunderwater post_processing_volume {_fmt_box(mn, mx)} (top {WATER_PP_BELOW_SURFACE:g} u under the surface)")
                done_new.add(key)
                tot["volumes_added"] += 1
                changed = True
            for e in own:                        # the script's volumes whose water is gone, flat, or in the skybox
                if e not in used_own:
                    pe = dict(m.walk()).get(e)
                    if pe is not None:
                        a = dmx.geta(m.E[pe], b"children")
                        a[2] = [x for x in a[2] if x != e]
                    rows.append(f"VOLUME_REMOVED\t{_fmt_box(*(m.bounds(e) or ([0] * 3, [0] * 3)))}\tunderwater volume from an earlier run no longer matches any water deep enough: removed")
                    changed = True
            if not masters and any(r.startswith("VOLUME") for r in rows):
                todo.append(f"{vn}: no master post_processing_volume; the underwater one will still work, but check the map's look outside the water")
            vpost_used = vpost_used or any(m.P(e, "postprocessing", "") == WATER_PP_VPOST for e in m.by_class("post_processing_volume"))
        # ---- water materials with no CS2 vmat -> a stock CS2 water material (WATER_MATERIAL)
        for w in waters:
            for c in m.children(w):
                md = m.g(c, b"meshData") if m.type(c) == b"CMapMesh" else None
                a = dmx.geta(m.E[md], b"materials") if md is not None else None
                if not a:
                    continue
                new = []
                for x in a[2]:
                    mat = x.decode("utf-8", "replace")
                    repl, why = (None, "")
                    if mat.lower() in {k.lower() for k in WATER_MATERIAL_MAP}:     # an explicit swap applies even to a material that exists
                        repl, why = _water_replacement(mat)
                    elif _water_material(mat)[0] and _water_vmat_missing(mat, export_dir):
                        repl, why = _water_replacement(mat)
                    if repl and repl.lower() != mat.lower():
                        swaps[(mat, repl, why)] = swaps.get((mat, repl, why), 0) + 1
                        x = repl.encode("utf-8")
                    new.append(x)
                if new != a[2]:
                    a[2] = new
                    changed = True
        if not changed:
            _keep_map(m, vp)
        else:
            try:
                bak = _save_map(m, vp, "water")
            except OSError as ex:
                out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
                print(f"  [WARN] water: could not write {vp} ({ex}); close Hammer and rerun")
                continue
            print(f"  [WATER] {vn}: " + ", ".join(r.split("\t")[0] for r in rows if r.split("\t")[0] in ("TILES_JOINED", "FUNC_WATER", "ANALOG", "VOLUME", "VOLUME_SYNCED")).lower() + f"; original saved as {os.path.basename(bak)}")
        if done_new - done or (changed and done_new):
            try:
                Path(state_path).write_text("# water given an underwater post_processing_volume (bounds lo hi). Delete a line to have it redone.\n"
                                            + "\n".join(sorted(done | done_new)) + "\n", encoding="utf-8")
            except OSError:
                pass
        out.append(f"# {vn}: {len(waters)} func_water")
        out += rows
        for e in m.by_class("post_processing_volume"):    # what is in the map now, to check against the game
            if m.P(e, "postprocessing", "") == WATER_PP_VPOST:
                b = m.bounds(e)
                out.append(f"IN_MAP\t{_fmt_box(*b) if b else '(no mesh!)'}\tunderwater volume: the effect shows while your feet are inside this box"
                           + (f" (z {b[0][2]:g} to {b[1][2]:g})" if b else "")
                           + f"; fadetime {m.P(e, 'fadetime', '?')}, exposure {m.P(e, 'minexposure', '?')}-{m.P(e, 'maxexposure', '?')}")
    # ---- the vpost
    if vpost_used:
        if _game_dir(export_dir):
            built = _game_dir(export_dir) / (WATER_PP_VPOST + "_c")
            out.append(f"VPOST_BUILT\t{built}\t" + ("compiled: the game can load it (rebuild the map after the vpost changes)" if built.is_file() else
                       "NOT compiled yet: build the map in Hammer (it compiles the vpost); until then the underwater volumes show nothing"))
        dst = export_dir / WATER_PP_VPOST
        text = _water_vpost_text()
        kind = "TEST vpost (very dark: WATER_PP_TEST = True)" if WATER_PP_TEST else "underwater vpost"
        if dst.exists() and not WATER_PP_VPOST_OVERWRITE:
            out.append(f"VPOST\t{WATER_PP_VPOST}\talready in the addon; left alone (WATER_PP_VPOST_OVERWRITE = False)")
        elif not dst.exists() and cs2_file_exists(WATER_PP_VPOST + "_c") and not WATER_PP_TEST:
            out.append(f"VPOST\t{WATER_PP_VPOST}\tshipped with CS2; nothing written")
        else:
            same = dst.is_file() and dst.read_bytes() == text.encode("utf-8")
            err = None if same else _safe_write(dst, text.encode("utf-8"))
            out.append(f"VPOST\t{WATER_PP_VPOST}\t" + (err or (f"{kind} unchanged" if same else f"{kind} written to the addon")))
    # ---- what each water material will look like
    swapped = {old for old, _n, _w in swaps}
    for (old, new, why), n in sorted(swaps.items()):
        out.append(f"SWAPPED\t{old} -> {new}\t{n} water mesh(es)\t({why}; Source1 water shaders are not converted)")
    for mat, how in sorted(water_mats.items()):
        if (export_dir / mat).exists():
            where = "in the addon"
        elif cs2_file_exists(mat + "_c") and not cs2_vmat_is_stub(mat):
            where = "ships with CS2"
        elif mat in swapped:
            where = "not in CS2: swapped (above)"
        else:
            where = "MISSING"
            todo.append(f"{mat}: no CS2 vmat and not swapped ({_water_replacement(mat)[1]}); in Hammer, give the water a stock CS2 water material")
        out.append(f"MATERIAL\t{mat}\t{where}\tdetected by: {how}")
        if how.startswith("GUESSED") and where not in ("ships with CS2", "in the addon"):
            todo.append(f"{mat}: treated as water from its name only; if it is not water, add it to WATER_IGNORE_MATERIALS and restore the vmap from its .before_water backup in the backups folder")
    if water_mats:
        out.append("# CS2 water materials available (materials/liquids/): " + (", ".join(_cs2_water_materials()) or "none found"))
    out.insert(0, "# water: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in tot.items())
               + ". Underwater lights come from the lighting rebuild (LIGHTING_UNDERWATER).")
    if todo:
        out += ["", "# check by hand:"] + ["TODO\t" + t for t in todo]
    return out



# ======================================================================== LIGHTING (from rebuild_lighting.py)
# Valve's importer gets CS:GO lights wrong (brightness NaN -> Hammer resets it to 1000 lumens, colour turned into a 6600 K white).
# rebuild_map_lighting() deletes every light, light probe volume and visibility hint and rebuilds them from the .vmf:
#   light -> light_omni2, light_spot -> light_barn (brightness = _light x _lightHDR x _lightscaleHDR, real colour, range from _distance /
#   fade, cone from _cone / _inner_cone; switchable lights become Dynamic), light_environment kept (SUN_SCALE), dim underwater lights in each
#   func_water, then skink52's passes: light probe volumes, Static -> Stationary lights (max 4 overlapping), vis hints, large-face splitting.
# The light scale (1000 lumens per 265 intensity) comes from the importer's own output; bake and compare a few rooms to CS:GO, then tune LIGHT_SCALE.
LIGHT_CLASSES = ('light_omni2', 'light_barn', 'light_rect', 'light_capsule', 'light_omni', 'light_ortho', 'light_spot', 'light')
WATER_CLASSES = ('func_water', 'func_water_analog')
DELETE_CLASSES = LIGHT_CLASSES + ('env_combined_light_probe_volume', 'env_light_probe_volume', 'visibility_hint')

INTENSITY_AT_1_LEGACY = 265.0       # CS:GO intensity that the importer calls legacy brightness 1
OMNI_LUMENS_PER_LEGACY = 1000.0     # light_omni2 at legacy 1 is 1000 lumens (Hammer's default, 80 candelas, 9816 nits)
OMNI_RANGE_K = 3.3856e6             # range^2 = legacy * K for a light with no _distance (fits the importer: I=200 -> 1598.49)

# Source 1 light_style presets (a = dark, m = normal, z = double)
LIGHT_STYLES = {
    1: 'mmnmmommommnonmmonqnmmo',
    2: 'abcdefghijklmnopqrstuvwxyzyxwvutsrqponmlkjihgfedcba',
    3: 'mmmmmaaaaammmmmaaaaaabcdefgabcdefg',
    4: 'mamamamamama',
    5: 'jklmnopqrstuvwxyzyxwvutsrqponmlkj',
    6: 'nmonqnmomnmomomno',
    7: 'mmmaaaabcdefgmmmmaaaammmaamm',
    8: 'mmmaaammmaaammmabcdefaaaammmmabcdefmmmaaaa',
    9: 'aaaaaaaazzzzzzzz',
    10: 'mmamammmmammamamaaamammma',
    11: 'abcdefghijklmnopqrrqponmlkjihgfedcba',
}

# properties of a Hammer-saved light_omni2 / light_barn (from Valve's importer), everything that is not set from the vmf
OMNI_DEFAULTS = {
    'vscripts': '', 'parentname': '', 'parentAttachmentName': '', 'useLocalOffset': '0', 'local.origin': '', 'local.angles': '', 'local.scales': '',
    'clientSideEntity': '1', 'brightness_units': '1', 'enabled': '1', 'directlight': '1', 'colormode': '0', 'colortemperature': '6600',
    'skirt': '0', 'bouncelight': '1', 'bouncescale': '1.0', 'shape': '0', 'size_params': '2.0 24.0 0.15', 'outer_angle': '180.0', 'inner_angle': '180.0',
    'lightcookie': '', 'bakespeculartocubemaps': '0', 'minroughness': '0', 'castshadows': '2', 'shadowmapsize': '-1', 'shadowpriority': '-1',
    'pvs_modify_entity': '0', 'shadowfade_size_start': '.10', 'shadowfade_size_end': '.05', 'brightnessscale': '1.0', 'rendertocubemaps': '1',
    'showlight': '0', 'fade_size_start': '.05', 'fade_size_end': '.025', 'light_style': '',
    'light_style_output_event0': '', 'light_style_output_event1': '', 'light_style_output_event2': '', 'light_style_output_event3': '',
    'light_style_target0': '', 'light_style_target1': '', 'light_style_target2': '', 'light_style_target3': '',
    'bakespeculartocubemaps_scale': '1.0', 'transmit_always': '0',
}
BARN_DEFAULTS = {
    'vscripts': '', 'parentname': '', 'parentAttachmentName': '', 'useLocalOffset': '0', 'local.origin': '', 'local.angles': '', 'local.scales': '',
    'clientSideEntity': '1', 'brightness_units': '1', 'enabled': '1', 'directlight': '1', 'colormode': '0', 'colortemperature': '6600',
    'skirt': '0', 'skirt_near': '0', 'bouncelight': '1', 'bouncescale': '1.0', 'luminaire_shape': '1', 'luminaire_size': '4', 'luminaire_anisotropy': '0',
    'shape': '1', 'shear': '0.0 0.0', 'lightcookie': '', 'bakespeculartocubemaps': '0', 'bakespeculartocubemaps_size': '6.0 6.0 0.0', 'minroughness': '0',
    'castshadows': '2', 'shadowmapsize': '-1', 'shadowpriority': '-1', 'pvs_modify_entity': '0', 'shadowfade_size_start': '.10', 'shadowfade_size_end': '.05',
    'rendertocubemaps': '1', 'brightnessscale': '1.0', 'fade_size_start': '.05', 'fade_size_end': '.025', 'light_style': '',
    'light_style_output_event0': '', 'light_style_output_event1': '', 'light_style_output_event2': '', 'light_style_output_event3': '',
    'light_style_target0': '', 'light_style_target1': '', 'light_style_target2': '', 'light_style_target3': '',
    'forceshadowsenabled': '0', 'bakespeculartocubemaps_scale': '1.0', 'transmit_always': '0',
}


# ======================================================================= VMF reading
def parse_vmf(text):
    """-> list of top-level 'entity' blocks as {'kv': {lower key: value}, 'conns': [(output, target, input)]}.  Solids etc. are skipped."""
    toks = []
    for mt in re.finditer(r'"((?:[^"\\]|\\.)*)"|([{}])|([A-Za-z_][A-Za-z0-9_]*)', text):
        if mt.group(1) is not None:
            toks.append(('s', mt.group(1)))
        elif mt.group(2):
            toks.append(('b', mt.group(2)))
        else:
            toks.append(('w', mt.group(3)))
    n = len(toks)

    def block(i):
        """i is just after '{'; -> (kv list, [(child name, kv list)], index after the closing '}')"""
        kv, kids = [], []
        while i < n:
            kind, val = toks[i]
            if kind == 'b' and val == '}':
                return kv, kids, i + 1
            if kind == 'w' and i + 1 < n and toks[i + 1] == ('b', '{'):
                ckv, _ck, i = block(i + 2)
                kids.append((val, ckv))
                continue
            if kind == 's' and i + 1 < n and toks[i + 1][0] == 's':
                kv.append((val, toks[i + 1][1]))
                i += 2
                continue
            i += 1
        return kv, kids, i

    out, i = [], 0
    while i < n:
        if toks[i] == ('w', 'entity') and i + 1 < n and toks[i + 1] == ('b', '{'):
            kv, kids, i = block(i + 2)
            conns = []
            for name, ckv in kids:
                if name == 'connections':
                    for _k, v in ckv:
                        parts = re.split(r'[,\x1b]', v)
                        if len(parts) >= 2:
                            conns.append((_k, parts[0], parts[1]))
            out.append({'kv': {k.lower(): v for k, v in kv}, 'conns': conns})
        else:
            i += 1
    return out


def fnum(s, d=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return d


def fvec(s, n=3, d=0.0):
    p = [fnum(x, d) for x in str(s or '').split()]
    return (p + [d] * n)[:n]


# ======================================================================= light maths
def light_intensity(kv):
    """-> (r, g, b normalised to 255, effective intensity) for a light / light_spot, using the HDR light CS2 renders.
    _lightHDR "-1 -1 -1 1" means 'same as _light, times the 4th value'; otherwise it replaces colour and intensity."""
    lv = [fnum(x) for x in str(kv.get('_light', '255 255 255 200')).split()]
    if len(lv) == 1:
        lv = lv * 3
    r, g, b = (lv + [255.0, 255.0, 255.0])[:3]
    inten = lv[3] if len(lv) > 3 else 255.0
    hdr = [fnum(x, -1.0) for x in str(kv.get('_lighthdr', '-1 -1 -1 1')).split()]
    hdr = (hdr + [-1.0, -1.0, -1.0, 1.0])[:4]
    if hdr[0] >= 0 and hdr[1] >= 0 and hdr[2] >= 0:
        r, g, b = hdr[:3]
        inten = hdr[3] if hdr[3] > 0 else inten
    elif hdr[3] > 0:
        inten *= hdr[3]
    scale = fnum(kv.get('_lightscalehdr'), 1.0)
    inten *= scale if scale > 0 else 1.0
    mx = max(r, g, b)
    if mx <= 0:
        return 255.0, 255.0, 255.0, 0.0
    return r * 255.0 / mx, g * 255.0 / mx, b * 255.0 / mx, inten * (mx / 255.0) ** 2.2


def nondefault_attn(kv):
    """True for a light that does not use plain 1/d^2 (_quadratic_attn only); CS2 lights are inverse square, so these are only approximated"""
    c, l, q = (fnum(kv.get('_constant_attn')), fnum(kv.get('_linear_attn')), fnum(kv.get('_quadratic_attn')))
    return (c != 0 or l != 0) and not (c == 0 and l == 0 and q == 0)


def cone_solid_angle(outer_deg):
    """solid angle of the light_barn pyramid for a cone of outer_deg (matches the importer: 65 -> 3.87 sr, 80 -> 5.31 sr)"""
    t = math.tan(math.radians(min(max(outer_deg, 1.0), 89.0)))
    s2 = t * t / (1.0 + t * t)
    return 4.0 * math.asin(min(s2, 1.0))


def dir_to_ang(d):
    """direction -> Source pitch (positive = down) / yaw"""
    x, y, z = d
    h = math.hypot(x, y)
    return math.degrees(math.atan2(-z, h)), math.degrees(math.atan2(y, x))


def fmt(x):
    return ('%.6f' % x).rstrip('0').rstrip('.') if x != int(x) else '%d' % int(x)


class Rebuild:
    def __init__(self, m, vmf_ents, args):
        self.m, self.v, self.a = m, vmf_ents, args
        self.log, self.todo = [], []
        self.new = []                      # entity nodes this script created
        self.faces_report, self.faces_ignored = [], []
        self.lumens = []

    # -------------------------------------------------- 1. delete
    def delete(self):
        m = self.m
        parent = m.parent_of()
        gone = {k: 0 for k in DELETE_CLASSES}
        self.kept = set()
        vmf_names = {(v['kv'].get('targetname') or '').lower() for v in self.v if (v['kv'].get('classname') or '').lower() in ('light', 'light_spot')}
        from_vmf = []
        for e in m.entities():
            k = m.cls(e)
            if k in LIGHT_CLASSES and LIGHTING_KEEP_PREFIX and m.name(e).lower().startswith(LIGHTING_KEEP_PREFIX.lower()):
                if m.name(e).lower() not in vmf_names:
                    self.kept.add(e)                     # your own light: survives the rebuild untouched
                    continue
                from_vmf.append(m.name(e))               # the Source1 map's own light that happens to be named keep_...: rebuilt like the rest
            if k in DELETE_CLASSES and e in parent:
                from dmx import geta
                a = geta(m.E[parent[e]], b'children')
                a[2] = [x for x in a[2] if x != e]
                gone[k] += 1
        self.log.append('deleted: ' + ', '.join('%d %s' % (n, k) for k, n in gone.items() if n)
                        + ('; kept %d light(s) named %s...' % (len(self.kept), LIGHTING_KEEP_PREFIX) if self.kept else '')
                        + ('; rebuilt %d %s... light(s) that come from the .vmf (%s), not kept' % (len(from_vmf), LIGHTING_KEEP_PREFIX, ', '.join(sorted(set(from_vmf))[:5]))
                           if from_vmf else ''))
        return gone

    # -------------------------------------------------- 2. recreate lights
    def targeted_names(self):
        names = set()
        for ent in self.v:
            for _out, tgt, _inp in ent['conns']:
                names.add(tgt.lower())
        return names

    def recreate(self):
        m, a = self.m, self.a
        targeted = self.targeted_names()
        by_name = {ent['kv'].get('targetname', '').lower(): ent for ent in self.v if ent['kv'].get('targetname')}
        n_omni = n_barn = n_dyn = n_style = n_dark = n_tgt = n_skip = n_attn = 0
        for ent in self.v:
            kv = ent['kv']
            cls = kv.get('classname', '').lower()
            if cls not in ('light', 'light_spot') or not kv.get('origin'):
                continue
            o = fvec(kv['origin'])
            r, g, b, inten = light_intensity(kv)
            if inten <= 0:
                n_skip += 1
                continue
            leg = inten / INTENSITY_AT_1_LEGACY
            dist = fnum(kv.get('_distance'))
            zero = fnum(kv.get('_zero_percent_distance'))
            if zero > 0:
                rng = zero
            elif dist > 0:
                rng = dist
            else:
                rng = math.sqrt(leg * OMNI_RANGE_K)
            n_attn += nondefault_attn(kv)
            leg *= a.light_scale
            leg = max(leg, 1e-4)
            name = kv.get('targetname', '')
            spawn = int(fnum(kv.get('spawnflags')))
            style = int(fnum(kv.get('style')))
            dark = bool(spawn & 1)
            switched = bool(name and name.lower() in targeted)
            props = dict(OMNI_DEFAULTS if cls == 'light' else BARN_DEFAULTS)
            props['targetname'] = name
            if kv.get('parentname'):
                props['parentname'] = kv['parentname']
            props['color'] = '%d %d %d' % (round(r), round(g), round(b))
            props['colormode'] = '0'
            props['range'] = fmt(round(rng, 1))
            props['brightness_legacy'] = fmt(round(leg, 6))
            props['brightness'] = fmt(round(math.log2(leg), 6))
            props['enabled'] = '0' if dark else '1'
            angles = (0.0, 0.0, 0.0)
            if cls == 'light':
                lum = OMNI_LUMENS_PER_LEGACY * leg
                props['brightness_lumens'] = str(max(1, round(lum)))
                props['brightness_candelas'] = str(max(1, round(lum / (4 * math.pi))))
                props['brightness_nits'] = str(max(1, round(9816 * leg)))
                n_omni += 1
            else:
                outer = fnum(kv.get('_cone'), 45.0)
                inner = fnum(kv.get('_inner_cone'), 30.0)
                outer = min(max(outer, 1.0), 89.0)
                lum = leg * OMNI_LUMENS_PER_LEGACY / (4 * math.pi) * cone_solid_angle(outer)
                props['brightness_lumens'] = str(max(1, round(lum)))
                t_out = math.tan(math.radians(outer))
                soft = 1.0 - math.tan(math.radians(min(inner, outer))) / t_out
                props['size_params'] = '1.000000 1.000000 %.6f' % t_out
                props['soft_x'] = props['soft_y'] = '%.6f' % max(0.0, min(1.0, soft))
                ang = fvec(kv.get('angles', '0 0 0'))
                # the light's own `pitch` key is negative-down (importer: pitch -90 -> angles 90); the angles key is positive-down
                pitch = -fnum(kv['pitch']) if kv.get('pitch', '') != '' else ang[0]
                yaw, roll = ang[1], ang[2]
                tgt = kv.get('target', '')
                if tgt and tgt.lower() in by_name and by_name[tgt.lower()]['kv'].get('origin'):
                    to = fvec(by_name[tgt.lower()]['kv']['origin'])
                    d = (to[0] - o[0], to[1] - o[1], to[2] - o[2])
                    if any(d):
                        pitch, yaw = dir_to_ang(d)             # positive = down
                        n_tgt += 1
                angles = (pitch, yaw, roll)
                n_barn += 1
            if style and style in LIGHT_STYLES:
                props['light_style'] = LIGHT_STYLES[style]
                n_style += 1
            dynamic = dark or switched or (style != 0)
            props['directlight'] = '2' if dynamic else '1'
            n_dyn += dynamic
            n_dark += dark
            e = m.new_entity('light_omni2' if cls == 'light' else 'light_barn', o, props, angles=angles)
            self.new.append(e)
            self.lumens.append(float(props['brightness_lumens']) if cls == 'light' else None)
        self.log.append('recreated from the vmf: %d light -> light_omni2, %d light_spot -> light_barn%s (colour kept, colormode 0)'
                        % (n_omni, n_barn, '; %d spotlights aimed at a target entity' % n_tgt if n_tgt else ''))
        if n_dyn:
            self.log.append('%d lights are Dynamic (directlight 2): %d start dark, the rest are switched by an output or have a style (a Static light cannot be switched)' % (n_dyn, n_dark))
        if n_attn:
            self.todo.append('%d lights use _constant_attn / _linear_attn falloff; CS2 lights are inverse square, so they got the same brightness as a normal light - check them' % n_attn)
        if n_style:
            self.todo.append('%d lights have a light style; the pattern was copied to light_style but CS2 behaviour is unverified' % n_style)
        if n_skip:
            self.log.append('%d lights with zero intensity were not recreated' % n_skip)
        if a.light_scale != 1.0:
            self.log.append('all point / spot lights scaled by %g' % a.light_scale)

    # -------------------------------------------------- 3. sun
    def sun(self):
        m, a = self.m, self.a
        envs = m.by_class('light_environment')
        if not envs:
            ve = next((e for e in self.v if e['kv'].get('classname', '').lower() == 'light_environment'), None)
            if ve:
                self.todo.append('the vmf has a light_environment but the vmap has none; add one in Hammer (colour %s, intensity %s)' % (ve['kv'].get('_light'), ve['kv'].get('_light')))
            return
        for e in envs:
            b = fnum(m.P(e, 'brightness', '1'), 1.0)
            if a.sun_scale != 1.0:
                m.setp(e, 'brightness', fmt(round(b * a.sun_scale, 6)))
            ve = next((x for x in self.v if x['kv'].get('classname', '').lower() == 'light_environment'), None)
            if ve:
                sp = fnum(ve['kv'].get('sunspreadangle'))
                if sp > 0 and fnum(m.P(e, 'angulardiameter', '0')) == 0:
                    m.setp(e, 'angulardiameter', fmt(sp))
        self.log.append('light_environment kept (brightness %s, colour %s, sky %s x %s)%s' % (
            m.P(envs[0], 'brightness'), m.P(envs[0], 'color'), m.P(envs[0], 'skycolor'), m.P(envs[0], 'skyintensity'),
            '; sun scaled by %g' % a.sun_scale if a.sun_scale != 1.0 else ''))

    # -------------------------------------------------- 4. underwater
    def underwater(self):
        m = self.m
        waters = [e for e in m.entities() if m.cls(e) in WATER_CLASSES]
        if not waters:
            return
        omni = [x for x in self.lumens if x]
        lum = statistics.median(omni) if omni else 150.0
        lum = max(60.0, min(lum, 250.0))
        lights = [(m.origin(e), fnum(m.P(e, 'range', '512'), 512.0)) for e in self.new]
        step0, cap = 640.0, 16
        added = used = 0
        sky = _sky_region(m)
        for w in waters:
            bb = m.bounds(w)
            if not bb:
                continue
            lo, hi = bb
            if sky and all(sky[0][k] - 1 <= lo[k] and hi[k] <= sky[1][k] + 1 for k in range(3)):
                continue                         # 3D skybox water
            h = hi[2] - lo[2]
            if h < 48 or (hi[0] - lo[0]) < 64 or (hi[1] - lo[1]) < 64:
                continue
            z = lo[2] + min(128.0, h * 0.5)
            step = max(step0, math.sqrt((hi[0] - lo[0]) * (hi[1] - lo[1]) / cap))      # at most about `cap` lights per water volume
            nx = max(1, int(math.ceil((hi[0] - lo[0]) / step)))
            ny = max(1, int(math.ceil((hi[1] - lo[1]) / step)))
            placed = 0
            for ix in range(nx):
                for iy in range(ny):
                    x = lo[0] + (ix + 0.5) * (hi[0] - lo[0]) / nx
                    y = lo[1] + (iy + 0.5) * (hi[1] - lo[1]) / ny
                    near = any(lo[0] - 64 <= p[0] <= hi[0] + 64 and lo[1] - 64 <= p[1] <= hi[1] + 64 and lo[2] - 64 <= p[2] <= hi[2] + 64 and
                               math.dist((x, y, z), p) < min(r, step * 0.9) for p, r in lights)
                    if near or placed >= cap + 4:
                        continue
                    props = dict(OMNI_DEFAULTS)
                    leg = lum / OMNI_LUMENS_PER_LEGACY
                    props.update({'color': '255 255 255', 'range': '512', 'castshadows': '0', 'brightness_legacy': fmt(round(leg, 6)),
                                  'brightness': fmt(round(math.log2(leg), 6)), 'brightness_lumens': str(round(lum)),
                                  'brightness_candelas': str(round(lum / (4 * math.pi))), 'brightness_nits': str(round(9816 * leg)), 'directlight': '1'})
                    e = m.new_entity('light_omni2', (x, y, z), props)
                    self.new.append(e)
                    lights.append(((x, y, z), 512.0))
                    placed += 1
            added += placed
            used += bool(placed)
        if added:
            self.log.append('%d underwater light_omni2 (%d lumens, range 512, no shadows) added in %d of %d water volumes' % (added, round(lum), used, len(waters)))
            self.todo.append('underwater lights are a heuristic (sparse grid, no light within 640 units); check the water areas and tune with LIGHT_SCALE or delete them')

    # -------------------------------------------------- large faces (split)
    def split_faces(self):
        import splitfaces
        a = self.a
        notes = []
        skip_mat = ('materials/tools', 'toolsnodraw', 'sky', 'moondome', 'water', 'materials/liquids/')     # tool volumes (also tools_postprocess_volume) and water are never split
        res = splitfaces.split_large(self.m, a.large_face_area, a.face_tile, a.split_large_faces, skip_mat, notes)
        self.split_notes = notes + ['NOT SPLIT: ' + x for x in res['skipped']]
        if res['faces']:
            self.log.append('split %d large face(s) in %d mesh(es) into %d pieces on a %g grid%s; area preserved, T-junctions repaired' % (
                res['faces'], res['meshes'], res['pieces'], a.face_tile, ', %d new mesh(es)' % res['new_meshes'] if res['new_meshes'] else ''))
            self.todo.append('split faces: check the cut floors / ramps for seams or lightmap-density changes after the bake (new vertices are interpolated, UVs follow the world-aligned texture axes)')
        if res['skipped']:
            self.todo.append('%d large face(s) could not be split (displacement meshes, rotated meshes or non-convex faces): see "large faces" below' % len(res['skipped']))

    # -------------------------------------------------- large faces (report only)
    def large_faces(self):
        """Lists very large lit faces: one face is one lightmap chart, so a huge floor can bake blotchy.  Changes nothing."""
        import numpy as np
        import lighting as skl
        m, limit = self.m, self.a.large_face_area
        owner = {}

        def walk(n, label, cls):
            for c in m.children(n):
                t = m.type(c)
                if t == b'CMapMesh':
                    owner[c] = (label, cls)
                elif t == b'CMapEntity':
                    walk(c, '%s %s' % (m.cls(c), m.name(c) or '-'), m.cls(c))
                else:
                    walk(c, label, cls)
        walk(m.world, 'world', 'world')
        skip_mat = ('materials/tools/', 'toolsnodraw', 'sky', 'moondome')
        groups, ignored = {}, {}
        for node, (label, cls) in owner.items():
            if m.g(node, b'meshData') is None:
                continue
            ent = None
            if cls != 'world':
                ent = next((e for e in m.entities() if '%s %s' % (m.cls(e), m.name(e) or '-') == label), None)
            for mat, loop in skl.mesh_polys(m, node):
                if any(x in mat for x in skip_mat) or len(loop) < 3:
                    continue
                P = np.array(loop)
                nrm = np.zeros(3)
                for i in range(1, len(P) - 1):
                    nrm += np.cross(P[i] - P[0], P[i + 1] - P[0])
                area = float(np.linalg.norm(nrm) / 2)
                if area <= limit * 1.0001:          # a 512 x 512 tile is fine
                    continue
                name = mat.split('/')[-1].rsplit('.', 1)[0]
                why = None
                if cls.startswith('func_water'):
                    why = 'water shader, not baked like a floor'
                elif cls.startswith('trigger_'):
                    why = 'trigger volume, not rendered'
                elif ent is not None and m.P(ent, 'lightmapstatic', '1') == '0':
                    why = 'entity with lightmapstatic 0, not baked'
                tgt = ignored if why else groups
                key = (name, label, why)
                a0, n0, big, at = tgt.get(key, (0.0, 0, 0.0, None))
                tgt[key] = (a0 + area, n0 + 1, max(big, area), [round(x) for x in P.mean(0)] if area > big else at)
        self.faces_report = []
        for key, (_tot, n, big, at) in sorted(groups.items(), key=lambda kv: -kv[1][2]):
            self.faces_report.append('%-22s %-34s %3d face(s), largest %9d sq units, at %s' % (key[0], key[1][:34], n, big, at))
        if groups:
            self.todo.append('%d groups of very large lit faces (over %d sq units, one lightmap chart each): see "large faces" below; split a face only if its bake looks blotchy' % (len(groups), limit))
        self.faces_ignored = ['%-22s %-34s %3d face(s), largest %9d sq units: %s' % (k[0], k[1][:34], v[1], v[2], k[2]) for k, v in sorted(ignored.items(), key=lambda kv: -kv[1][2])]

    # -------------------------------------------------- 5. fix lights the lighting pass added
    def normalise_added(self):
        m = self.m
        mine = set(self.new)
        omni = [x for x in self.lumens if x]
        if not omni:
            return
        med = statistics.median(omni)
        extra = [e for e in m.by_class('light_omni2', 'light_barn') if e not in mine and e not in getattr(self, 'kept', ())]
        for e in extra:
            if m.cls(e) == 'light_omni2':
                leg = med / OMNI_LUMENS_PER_LEGACY
                m.setp(e, 'brightness_lumens', str(round(med)))
                m.setp(e, 'brightness_legacy', fmt(round(leg, 6)))
                m.setp(e, 'brightness', fmt(round(math.log2(leg), 6)))
                m.setp(e, 'brightness_candelas', str(round(med / (4 * math.pi))))
                m.setp(e, 'brightness_nits', str(round(9816 * leg)))
                m.setp(e, 'castshadows', '0')
        if extra:
            self.log.append('%d lights added by the stationary-light pass (rooms with a spawn and no light) set to the map median, %d lumens, no shadows' % (len(extra), round(med)))


def _main_vmaps(vmaps, primary):
    """the map(s) the lighting and radar are made for: VMAP_PATH's file, else the .vmaps directly in the maps folder (prefab sub-maps excluded)"""
    return [Path(primary)] if primary else [Path(v) for v in vmaps if Path(v).parent.name.lower() == "maps"]


def rebuild_map_lighting(targets, vmf, backup_dir):
    """Rebuild each main map's lighting from the .vmf (rebuild_lighting.py, in place). LIGHTING_REBUILD "once": only the first time per map
    (remembered in backups\\<map>.vmap.lighting_done.txt), so later runs keep the lights you tuned in Hammer; "always": every run.
    The vmap is backed up to BACKUP_DIR first. -> list of log lines"""
    import datetime
    import importlib.util
    import traceback
    import types
    if not vmf:
        return ["# lighting: skipped - needs the Source1 .vmf (none found under SOURCE1_ROOTS/maps; set STATIC_OVERLAY_VMF)"]
    if importlib.util.find_spec("numpy") is None or importlib.util.find_spec("scipy") is None:
        return ["# lighting: skipped - needs numpy and scipy:  py -m pip install numpy scipy"]
    lighting = _bundled_lighting_passes()
    probes, visfit = sys.modules["probes"], sys.modules["visfit"]
    a = types.SimpleNamespace(light_scale=LIGHT_SCALE, sun_scale=SUN_SCALE, large_face_area=LIGHTING_LARGE_FACE_AREA,
                              face_tile=LIGHTING_FACE_TILE, split_large_faces=LIGHTING_SPLIT_LARGE_FACES)
    out, ents = [], None
    for vp in targets:
        vn = vp.name
        state = Path(backup_dir) / (vn + ".lighting_done.txt")
        if LIGHTING_REBUILD == "once" and state.is_file():
            out.append(f"# {vn}: lighting was rebuilt on an earlier run ({state.read_text(encoding='utf-8').strip()}); left alone so your light edits stay. "
                       f'LIGHTING_REBUILD = "always" (or deleting {state.name}) rebuilds it, deleting every light first')
            continue
        if ents is None:
            print(f"  lighting: reading {vmf}...", flush=True)
            ents = parse_vmf(open(vmf, encoding="utf-8", errors="replace").read())
        m = _open_map(vp)
        rb = Rebuild(m, ents, a)
        try:
            rb.delete()
            rb.recreate()
            rb.sun()
            if LIGHTING_UNDERWATER:
                rb.underwater()
            if LIGHTING_EXPOSURE:
                for e in m.by_class("post_processing_volume"):
                    if (m.P(e, "master", "0") or "0").strip() == "1":          # the underwater volumes keep their own exposure
                        m.setp(e, "minexposure", fmt(LIGHTING_EXPOSURE[0]))
                        m.setp(e, "maxexposure", fmt(LIGHTING_EXPOSURE[1]))
                rb.log.append("master post_processing_volume exposure set to %g - %g" % tuple(LIGHTING_EXPOSURE))
            passes = [("light probes", lambda: probes.run(m, rb, replace=True, wall_classes=tuple(LIGHTING_PROBE_WALL_CLASSES)))] if LIGHTING_PROBES else []
            passes += [("stationary lights", lambda: lighting.run(m, rb))] if LIGHTING_STATIONARY else []
            passes += [("vis hints", lambda: visfit.run(m, rb, all_lower=LIGHTING_VIS_ALL_LOWER))] if LIGHTING_VIS else []
            for name, fn in passes:
                print(f"  lighting: running {name}...", flush=True)
                fn()
            if passes:
                rb.normalise_added()
            if LIGHTING_SPLIT_LARGE_FACES != "off":
                print("  lighting: splitting large faces...", flush=True)
                rb.split_faces()
            rb.large_faces()
            rb.log.append("removed %d orphaned elements" % m.prune())
        except Exception as ex:
            out += [f"# {vn}: lighting rebuild failed ({type(ex).__name__}: {ex}); nothing was saved, the map is unchanged"] + traceback.format_exc().splitlines()
            continue
        now = datetime.datetime.now()
        try:
            bak = _save_map(m, vp, "lighting")
        except OSError as ex:
            out.append(f"# {vn}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
            continue
        state.write_text(f"{now:%Y-%m-%d %H:%M} from {os.path.basename(vmf)}\n", encoding="utf-8")
        out += [f"# {vn}: lighting rebuilt from {vmf}; the map before it: {bak}", "", "changes:"] + ["  - " + x for x in rb.log]
        out += ["", "check in game:"] + ["  - " + x for x in rb.todo]
        if getattr(rb, "split_notes", None):
            out += ["", "large faces split (%s):" % LIGHTING_SPLIT_LARGE_FACES] + ["  - " + x for x in rb.split_notes]
        out += ["", "large faces remaining (reported only, nothing was changed; over %d sq units, each is one lightmap chart):" % LIGHTING_LARGE_FACE_AREA]
        out += ["  - " + x for x in rb.faces_report] + ["", "large faces that are probably fine:"] + ["  - " + x for x in rb.faces_ignored]
        out += ["", "Reopen the map in Hammer and do a full lighting bake.", ""]
    return out


# ======================================================================== RADAR (minimap)

RADAR_VTEX = '''<!-- dmx encoding keyvalues2_noids 1 format vtex 1 -->
"CDmeVtex"
{
    "m_inputTextureArray" "element_array"
    [
        "CDmeInputTexture"
        {
            "m_name" "string" "SheetTexture"
            "m_fileName" "string" "panorama/images/overheadmaps/%(map)s_radar_psd.png"
            "m_colorSpace" "string" "srgb"
            "m_typeString" "string" "2D"
            "m_imageProcessorArray" "element_array"
            [
            ]
        }
    ]
    "m_outputTypeString" "string" "2D"
    "m_outputFormat" "string" "BC7"
    "m_outputClearColor" "vector4" "0 0 0 0"
    "m_nOutputMinDimension" "int" "0"
    "m_nOutputMaxDimension" "int" "%(size)d"
    "m_textureOutputChannelArray" "element_array"
    [
        "CDmeTextureOutputChannel"
        {
            "m_inputTextureArray" "string_array"
            [
                "SheetTexture"
            ]
            "m_srcChannels" "string" "rgba"
            "m_dstChannels" "string" "rgba"
            "m_mipAlgorithm" "CDmeImageProcessor"
            {
                "m_algorithm" "string" ""
                "m_stringArg" "string" ""
                "m_vFloat4Arg" "vector4" "0 0 0 0"
            }
            "m_outputColorSpace" "string" "srgb"
        }
    ]
    "m_vClamp" "vector3" "0 0 0"
    "m_bNoLod" "bool" "1"
}
'''
RADAR_OVERVIEW = '''// Overview (radar) description for %(map)s, drawn from the map geometry by vmap_asset_slop_import
"%(map)s"
{
	"material"	"overviews/%(map)s_radar"
	"pos_x"		"%(px)d"	// upper left world coordinate
	"pos_y"		"%(py)d"
	"scale"		"%(scale).2f"	// world units per pixel
	"rotate"	"0"
	"zoom"		"1.0"

	"CTSpawn_x"	"%(ctx).4f"
	"CTSpawn_y"	"%(cty).4f"
	"TSpawn_x"	"%(tx).4f"
	"TSpawn_y"	"%(ty).4f"
}
'''
RADAR_SKIP_MATS = ("materials/tools/", "tools_", "toolsnodraw", "moondome", "skybox", "sky")
RADAR_SKIP_ENTS = ("trigger_", "func_buyzone", "func_bomb_target", "visibility", "func_hostage_rescue", "post_processing_volume",
                   "env_combined_light_probe_volume", "env_light_probe_volume", "env_cubemap_box", "func_clip", "func_nav")
RADAR_COLOURS = [
    (("grass", "foliage", "leaf", "moss"), (92, 128, 70)), (("water", "liquid"), (70, 110, 150)),
    (("sand", "beach", "desert"), (196, 178, 128)), (("dirt", "mud", "gravel", "ground", "soil"), (128, 108, 84)),
    (("snow", "ice"), (224, 230, 236)), (("wood", "plank", "floorboard", "parquet"), (148, 112, 76)),
    (("brick", "tile"), (156, 100, 84)), (("metal", "steel", "iron", "grate", "catwalk"), (120, 126, 134)),
    (("concrete", "cement", "stone", "rock", "asphalt", "road", "pavement", "cobble"), (140, 140, 140)),
    (("carpet", "rug"), (130, 96, 96)), (("glass",), (170, 200, 210)),
]
RADAR_DEFAULT_COLOUR = (128, 132, 140)


class _RadarTex:
    """Floor colour per material: the average of its colour texture in the addon (the .tga/.png this script wrote next to the
    vmat) when RADAR_TEXTURE_COLOURS, else a guess from the material name."""

    def __init__(self, export_dir: Path):
        self.root, self.cache = export_dir, {}

    def colour(self, mat):
        key = mat.lower()
        if key not in self.cache:
            col = None
            if RADAR_TEXTURE_COLOURS:
                base = os.path.splitext(key)[0]
                for suf in ("_color", "_c", ""):
                    for ext in (".tga", ".png", ".jpg"):
                        p = self.root / (base + suf + ext)
                        if p.is_file():
                            try:
                                im = Image.open(p).convert("RGB").resize((8, 8))
                                from PIL import ImageStat
                                col = tuple(int(x) for x in ImageStat.Stat(im).mean[:3])
                            except Exception:
                                col = None
                            break
                    if col:
                        break
            if not col:
                col = next((rgb for words, rgb in RADAR_COLOURS if any(w in key for w in words)), RADAR_DEFAULT_COLOUR)
            self.cache[key] = col
        return self.cache[key]


def _poly_area_xy(P):
    """area of a polygon seen from above (as drawn on the radar)"""
    x, y = P[:, 0], P[:, 1]
    return 0.5 * abs(float((x * (y.take(range(1, len(y) + 1), mode="wrap")) - y * (x.take(range(1, len(x) + 1), mode="wrap"))).sum()))


def _radar_draw(m, export_dir: Path, size: int):
    """Draw the radar for an open vmapedit.Map (method from make_radar.py). -> (rgba image, overview values, note lines)"""
    import numpy as np
    from PIL import ImageDraw
    notes = []
    lighting = _bundled_lighting_passes(("lighting",))         # its mesh / voxel helpers; numpy only
    wp = lighting.world_polys(m)
    polys = list(wp)
    for e in m.entities():                              # doors, func_brush, breakables, water...
        if m.cls(e).startswith(RADAR_SKIP_ENTS):
            continue
        for ch in m.children(e):
            if m.type(ch) == b"CMapMesh":
                polys.extend(lighting.mesh_polys(m, ch))
    floors, walls = [], []
    for mat, loop in polys:
        if any(s in mat for s in RADAR_SKIP_MATS) or len(loop) < 3:
            continue
        P = np.array(loop)
        Q = np.roll(P, -1, axis=0)                       # Newell normal
        n = np.array([((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2])).sum(), ((P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0])).sum(),
                      ((P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1])).sum()])
        ln = np.linalg.norm(n)
        if ln < 1e-6:
            continue
        n /= ln
        if n[2] > 0.6:
            floors.append((mat, P))
        elif abs(n[2]) < 0.3:
            walls.append(P)
    if not floors:
        return None, None, ["no floor faces found"]
    notes.append(f"{len(floors)} floor faces, {len(walls)} wall faces")
    note = "room filter off (RADAR_ROOM_FILTER)"
    room_ok = None
    if wp and RADAR_ROOM_FILTER:
        try:
            from scipy import ndimage
        except ImportError:
            ndimage = None
            note = "room filter skipped: needs scipy (py -m pip install scipy); roofs and outside faces are drawn too"
        if ndimage is not None:
            vs = 32.0
            solid, vlo = lighting.voxelise(wp, vs)
            lab, _ = lighting.label_empty(solid)
            shape = np.array(solid.shape)

            def lab_at(p):
                i = np.floor((np.array(p) - vlo) / vs).astype(int)
                return 0 if (i < 0).any() or (i >= shape).any() else lab[tuple(i)]

            def lab_near(p):
                for dz in (0, 32, -32, 64):
                    v = lab_at([p[0], p[1], p[2] + dz])
                    if v:
                        return v
                return 0
            play = set()
            for e in m.by_class(*lighting.LIGHT_CLASSES) + m.by_class("info_player_terrorist", "info_player_counterterrorist", "info_teleport_destination", "info_deathmatch_spawn"):
                v = lab_near(m.origin(e))
                if v:
                    play.add(v)
            ext = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(), lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])))
            skyl = set(np.unique(lab[ndimage.binary_dilation(solid == 2) & (lab > 0)]))
            play -= ext                     # a light / spawn outside the sealed map must not turn the void into a room
            note = "room filter found no rooms with lights or spawns; every upward face drawn"
            if play:
                ids = np.arange(int(lab.max()) + 1)
                okl = np.isin(ids, list(play)) | ((ids > 0) & ~np.isin(ids, list(ext)) & ~np.isin(ids, list(skyl)))

                def _room_ok(pts):
                    """(N, 3) world points -> True where the point is in a room (with lights / spawns, or sealed and not open to sky)"""
                    i = np.floor((np.asarray(pts, float) - vlo) / vs).astype(int)
                    inb = ((i >= 0) & (i < shape)).all(1)
                    res = np.zeros(len(i), bool)
                    j = i[inb]
                    res[inb] = okl[lab[j[:, 0], j[:, 1], j[:, 2]]]
                    return res

                def face_ok(P):
                    c0 = P.mean(0)
                    return bool(_room_ok(np.array([c0] + [(c0 + v) / 2 for v in P]) + [0, 0, 40]).any())
                kept = [f for f in floors if _poly_area_xy(f[1]) > RADAR_CLIP_AREA or face_ok(f[1])]
                if kept:
                    note = "kept %d of %d floor faces that open onto a room; faces over %g sq units clipped to the rooms pixel by pixel" % (len(kept), len(floors), RADAR_CLIP_AREA)
                    floors = kept
                    room_ok = _room_ok
    notes.append(note)
    land = [f for f in floors if "water" not in f[0].lower()] or floors
    allp = np.concatenate([f[1] for f in land])
    lo = np.percentile(allp, RADAR_TRIM, axis=0)
    hi = np.percentile(allp, 100 - RADAR_TRIM, axis=0)
    span = max(hi[0] - lo[0], hi[1] - lo[1]) * 1.04 + 128
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    px, py = cx - span / 2, cy + span / 2
    scale = span / size
    ss = 2
    S = size * ss
    zlo, zhi = np.percentile(allp[:, 2], 2), np.percentile(allp[:, 2], 98)
    tex = _RadarTex(export_dir)

    def to_img(P):
        return [((x - px) / scale * ss, (py - y) / scale * ss) for x, y in P[:, :2]]

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for mat, P in sorted(floors, key=lambda f: f[1][:, 2].mean()):
        t = float(np.clip((P[:, 2].mean() - zlo) / max(zhi - zlo, 1), 0, 1))
        r, g, b = tex.colour(mat)
        k = 0.55 + 0.5 * t
        fill = (int(min(255, r * k)), int(min(255, g * k)), int(min(255, b * k)), 110 if "glass" in mat.lower() else 255)
        if room_ok is None or _poly_area_xy(P) <= RADAR_CLIP_AREA:
            d.polygon(to_img(P), fill=fill)
            continue
        # big face (a square block of water / terrain often reaches outside the map): keep only the pixels over a room
        pts = to_img(P)
        x0, y0 = max(0, int(min(q[0] for q in pts))), max(0, int(min(q[1] for q in pts)))
        x1, y1 = min(S, int(max(q[0] for q in pts)) + 2), min(S, int(max(q[1] for q in pts)) + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
        M = np.asarray(mask) > 0
        yy, xx = np.nonzero(M)
        wx = px + (xx + x0 + 0.5) / ss * scale
        wy = py - (yy + y0 + 0.5) / ss * scale
        Q = np.roll(P, -1, axis=0)
        n = np.array([((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2])).sum(), ((P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0])).sum(), ((P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1])).sum()])
        c0 = P.mean(0)
        wz = c0[2] - (n[0] * (wx - c0[0]) + n[1] * (wy - c0[1])) / n[2]          # on the face's plane
        ok = room_ok(np.stack([wx, wy, wz + 40], 1))
        M[yy[~ok], xx[~ok]] = False
        img.paste(fill, (x0, y0), Image.fromarray(M.astype(np.uint8) * 255))
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    de = ImageDraw.Draw(edge)
    for P in walls:
        if P[:, 2].max() - P[:, 2].min() < RADAR_MIN_WALL:
            continue
        pts = to_img(P)
        de.line(pts + [pts[0]], fill=(20, 22, 28, 255), width=max(1, ss))
    img = Image.alpha_composite(img, edge).resize((size, size), Image.LANCZOS)

    def frac(cls):
        es = m.by_class(cls)
        if not es:
            return 0.5, 0.5
        o = np.mean([m.origin(e)[:2] for e in es], axis=0)
        return float(np.clip((o[0] - px) / span, 0, 1)), float(np.clip((py - o[1]) / span, 0, 1))
    ctx, cty = frac("info_player_counterterrorist")
    tx, ty = frac("info_player_terrorist")
    notes.append("map extent x %.0f..%.0f  y %.0f..%.0f; pos_x %d pos_y %d scale %.2f" % (lo[0], hi[0], lo[1], hi[1], px, py, scale))
    return img, dict(px=px, py=py, scale=scale, ctx=ctx, cty=cty, tx=tx, ty=ty), notes


def _sha1(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


SEAM_SKIP_CLASSES = ("trigger_", "func_water", "post_processing_volume", "env_", "visibility_hint", "func_buyzone", "func_bomb_target",
                     "func_hostage_rescue", "func_clip", "func_nav")


def report_entity_seams(targets):
    """Brush-entity faces that sit in the plane of a world surface: one filling a gap in it (shares edges with the world faces around) is
    lit as a separate surface, so it rarely matches (a black square in a ceiling); one lying ON a world face z-fights. Report only.
    -> list of log lines"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return ["# entity seams: skipped - needs numpy"]
    lighting = _bundled_lighting_passes(("lighting",))

    def plane(loop):
        P = loop
        n = [0.0, 0.0, 0.0]
        for i in range(len(P)):
            a, b = P[i], P[(i + 1) % len(P)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2]); n[1] += (a[2] - b[2]) * (a[0] + b[0]); n[2] += (a[0] - b[0]) * (a[1] + b[1])
        ln = math.sqrt(sum(x * x for x in n))
        if ln < 1e-6:
            return None
        n = [x / ln for x in n]
        c = [sum(p[k] for p in P) / len(P) for k in range(3)]
        return n, sum(n[k] * c[k] for k in range(3)), c, ln / 2

    def flat(n, pts):                            # drop the dominant axis -> 2D
        ax = max(range(3), key=lambda k: abs(n[k]))
        keep = [k for k in range(3) if k != ax]
        return [(p[keep[0]], p[keep[1]]) for p in pts]

    def shared(e1, e2):                          # collinear overlap length of two 2D segments
        (ax, ay), (bx, by) = e1
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return 0.0
        ux, uy = dx / L, dy / L
        ts = []
        for (px, py) in e2:
            if abs((px - ax) * uy - (py - ay) * ux) > 0.25:
                return 0.0
            ts.append((px - ax) * ux + (py - ay) * uy)
        return max(0.0, min(L, max(ts)) - max(0.0, min(ts)))

    def inside(pt, poly):
        x, y = pt
        c = False
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                c = not c
        return c

    out, total = [], 0
    for vp in targets:
        vn = os.path.basename(str(vp))
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {vn}: cannot read ({ex})")
            continue
        index = {}
        for mat, loop in lighting.world_polys(m):
            if mat.startswith("materials/tools") or len(loop) < 3:
                continue
            pl = plane(loop)
            if pl:
                n, d, _c, _a = pl
                index.setdefault((round(n[0], 2), round(n[1], 2), round(n[2], 2), round(d)), []).append((mat, flat(n, loop)))
        rows = []
        for e in m.entities():
            cls = m.cls(e)
            if cls.startswith(SEAM_SKIP_CLASSES):
                continue
            for ch in m.children(e):
                if m.type(ch) != b"CMapMesh":
                    continue
                for mat, loop in lighting.mesh_polys(m, ch):
                    if mat.startswith("materials/tools") or len(loop) < 3:
                        continue
                    pl = plane(loop)
                    if not pl:
                        continue
                    n, d, c, area = pl
                    key = (round(n[0], 2), round(n[1], 2), round(n[2], 2))
                    cands = [w for dd in (-1, 0, 1) for w in index.get(key + (round(d) + dd,), [])]
                    if not cands:
                        continue
                    f2 = flat(n, loop)
                    edges = [(f2[i], f2[(i + 1) % len(f2)]) for i in range(len(f2))]
                    c2 = flat(n, [c])[0]
                    on_top = [wm for wm, w2 in cands if inside(c2, w2)]
                    touch, wmats = 0, set()
                    for ed in edges:
                        hit = False
                        for wm, w2 in cands:
                            if any(shared(ed, (w2[i], w2[(i + 1) % len(w2)])) > 1.0 for i in range(len(w2))):
                                hit = True
                                wmats.add(wm)
                        touch += hit
                    who = f"{cls} {m.name(e) or '(unnamed)'}"
                    where = " ".join("%g" % round(x) for x in c)
                    if on_top:
                        rows.append(f"OVERLAPS\t{who}\tat {where}\t{os.path.basename(mat)} lies on a world face ({os.path.basename(on_top[0])}): the two z-fight")
                    elif touch:
                        rows.append(f"FILLS_GAP\t{who}\tat {where}\t{area:.0f} sq u of {os.path.basename(mat)}, {touch} of {len(edges)} edge(s) meet the world surface "
                                    f"({', '.join(sorted(os.path.basename(x) for x in wmats))}): lit separately, may not match it")
        _keep_map(m, vp)
        total += len(rows)
        out += [f"# {vn}"] + sorted(rows)
    out.insert(0, f"# entity seams: {total} brush-entity face(s) continue or overlap a world surface in the same plane. Such a face is lit on its own "
                  "(a separate lightmap chart, or per object from probes), so it often shows as a lighter or darker patch; nothing was changed. "
                  "If the entity never moves or disappears, making the face part of the world surface (or nodraw on the entity) removes the seam.")
    return out


def make_radars(vmaps, primary, export_dir: Path, backup_dir):
    """Draw a radar for the main map(s) (primary, else the .vmaps directly in the maps folder; prefab sub-maps get none):
         <addon>\\panorama\\images\\overheadmaps\\<map>_radar_psd.png + .vtex      (content)
         <game twin of the addon>\\resource\\overviews\\<map>.txt                  (pos_x / pos_y / scale + spawn icons)
         logs\\<map>_radar_preview.png                                             (a quick look, with the spawn spots)
    Rerun-safe: what was written is remembered (backups\\<map>.radar_written.txt, a hash per file). The radar is redrawn only while all of its files
    are still exactly what this script wrote; once you edit or replace any of them (or they came from make_radar.py / elsewhere), none is
    touched again. Delete the files to have them drawn again. -> list of log lines"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return ["# radar: skipped - needs numpy (and scipy for the room filter):  py -m pip install numpy scipy"]
    out = []
    targets = _main_vmaps(vmaps, primary)
    game_dir = _game_dir(export_dir)
    for vp in targets:
        name = vp.stem
        try:
            m = _open_map(vp)
        except Exception as ex:
            out.append(f"# {name}: cannot read ({ex})")
            continue
        files = {
            "png": export_dir / "panorama" / "images" / "overheadmaps" / f"{name}_radar_psd.png",
            "vtex": export_dir / "panorama" / "images" / "overheadmaps" / f"{name}_radar_psd.vtex",
            "txt": (game_dir / "resource" / "overviews" / f"{name}.txt") if game_dir else Path(LOG_DIR) / "radar_game_files" / "resource" / "overviews" / f"{name}.txt",
        }
        state_path = Path(backup_dir) / f"{name}.radar_written.txt"
        known = {}
        for ln in (state_path.read_text(encoding="utf-8").splitlines() if state_path.is_file() else []):
            if "\t" in ln and not ln.startswith("#"):
                h, p = ln.split("\t", 1)
                known[p] = h
        foreign = [k for k, p in files.items() if p.is_file() and known.get(str(p)) != _sha1(p.read_bytes())]
        try:
            img, ov, notes = _radar_draw(m, export_dir, RADAR_SIZE)
            _keep_map(m, vp)                     # the radar only reads the map
        except Exception as ex:
            out.append(f"# {name}: radar not drawn ({type(ex).__name__}: {ex})")
            continue
        if img is None:
            out.append(f"# {name}: radar not drawn ({notes[0]})")
            continue
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = {"png": buf.getvalue(), "vtex": (RADAR_VTEX % dict(map=name, size=RADAR_SIZE)).encode("utf-8"),
                "txt": (RADAR_OVERVIEW % dict(map=name, **ov)).encode("utf-8")}
        out.append(f"# {name}: " + "; ".join(notes))
        for k, p in files.items():
            if p.is_file() and foreign:
                why = "edited by hand or not made by this script" if k in foreign else f"kept with the {', '.join(f.name for kk, f in files.items() if kk in foreign)} you changed (they must match)"
                out.append(f"KEPT\t{p}\t{why}; delete the radar files to have them drawn again")
                continue
            if foreign and k == "txt":
                out.append(f"NOTE\t{p}\twritten for the new drawing, but your radar image was kept: check pos_x / pos_y / scale still match it")
            same = p.is_file() and p.read_bytes() == data[k]
            err = None if same else _safe_write(p, data[k])
            if err:
                out.append(f"ERROR\t{p}\t{err}")
            else:
                known[str(p)] = _sha1(data[k])
                out.append(f"{'UNCHANGED' if same else 'WROTE'}\t{p}")
        try:
            state_path.write_text("# radar files written by vmap_asset_slop_import (sha1). A file whose hash no longer matches was edited and is left alone.\n"
                                  + "\n".join(f"{h}\t{p}" for p, h in sorted(known.items())) + "\n", encoding="utf-8")
        except OSError:
            pass
        try:
            from PIL import ImageDraw
            prev = Image.new("RGBA", img.size, (24, 26, 32, 255))
            prev.alpha_composite(img)
            pd = ImageDraw.Draw(prev)
            for (fx, fy), col in (((ov["ctx"], ov["cty"]), (80, 140, 255)), ((ov["tx"], ov["ty"]), (255, 190, 60))):
                x, y = fx * RADAR_SIZE, fy * RADAR_SIZE
                pd.ellipse([x - 6, y - 6, x + 6, y + 6], outline=col + (255,), width=3)
            pp = os.path.join(LOG_DIR, f"{name}_radar_preview.png")
            prev.convert("RGB").save(pp)
            out.append(f"PREVIEW\t{pp}\t(blue = CT spawns, orange = T spawns; not compiled)")
        except Exception:
            pass
        if not game_dir:
            out.append(f"NOTE\t{files['txt']}\tthe addon is not under content\\csgo_addons, so the overview .txt was written to the logs folder: copy it to <addon game folder>\\resource\\overviews\\")
    if out:
        out.insert(0, "# radar: drawn from the map geometry (floors lowest to highest, darker lower down, tall walls outlined). Reopen the addon in Hammer / rebuild so the radar texture compiles.")
    return out


def extract_assets_from_vmap(path: str):
    data = Path(path).read_bytes()
    candidates = set()
    candidates.update(_extract_from_ascii_tokens(data))
    candidates.update(_extract_from_utf16le_tokens(data))
    if not REQUIRE_NULL_DELIMITED:
        candidates.update(_fallback_regex_scan(data))
    results = set()
    for c in candidates:
        for piece in split_glued_paths(c):
            piece = normalize_candidate(piece)
            if piece:
                results.add(piece)
    out = sorted(results)
    if DEBUG_EXTRACT:
        print(f"DEBUG: extracted candidates (pre-rootfilter): {len(out)}")
    return out

EXT_RE = r"(?:vmat|vtex|vtex_c|tga|png|jpg|jpeg|dmx|vmdl|vmdl_c|vsnd|vpcf|wav|mp3|vmt|vtf)"
PATH_RE_TEXT = re.compile(rf'([A-Za-z0-9_\-./\\&()+ ]+\.{EXT_RE})', re.IGNORECASE)

@lru_cache(maxsize=200_000)
def parse_vmat_dependencies(vmat_path: str):
    deps = set()
    try:
        text = Path(vmat_path).read_text(encoding="utf-8", errors="ignore")
        deps.update(re.findall(r'"([^"]+\.(?:tga|png|jpg|jpeg|vtex|vtex_c))"', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.vmat)"', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.vmdl)"', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.dmx)"', text, flags=re.IGNORECASE))
    except Exception:
        pass
    return frozenset(d for d in (normalize_dep_must_be_rooted(x) for x in deps) if d)

def scan_addon_models_for_deps(export_dir: Path) -> set:
    deps = set()
    models_root = export_dir / "models"
    if not models_root.exists():
        return deps
    for fp in models_root.rglob("*"):
        if not fp.is_file() or fp.suffix.lower() not in (".vmdl", ".vmdl_c"):
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in PATH_RE_TEXT.findall(text):
            rel = m.strip().strip('"').strip("'").lstrip("/").replace("\\", "/")
            if not rel.lower().endswith("meshinfo.txt"):
                nr = normalize_dep_must_be_rooted(rel)
                if nr:
                    deps.add(nr)
    return deps

# ======================================================================== MDL (Source1 model) materials

def parse_mdl_materials(mdl_path: str):
    """Return candidate 'materials/<cd>/<name>.vmat' paths referenced by a Source1 .mdl
    (studiohdr: numtextures@204, textureindex@208, numcdtextures@212, cdtextureindex@216)."""
    try:
        data = Path(mdl_path).read_bytes()
    except Exception:
        return []
    if len(data) < 220 or data[:4] != b"IDST":
        return []

    def cstr(off):
        if off < 0 or off >= len(data):
            return ""
        end = data.find(b"\0", off)
        return data[off:end].decode("ascii", errors="ignore") if end != -1 else ""

    try:
        numtex, texidx, numcd, cdidx = struct.unpack_from("<4i", data, 204)
    except struct.error:
        return []
    names, cds = [], []
    if 0 <= numtex <= 512 and texidx > 0:
        for i in range(numtex):
            base = texidx + i * 64
            if base + 4 > len(data):
                break
            rel = struct.unpack_from("<i", data, base)[0]
            names.append(cstr(base + rel))
    if 0 <= numcd <= 64 and cdidx > 0:
        for i in range(numcd):
            p = cdidx + i * 4
            if p + 4 > len(data):
                break
            cds.append(cstr(struct.unpack_from("<i", data, p)[0]))
    out = []
    for cd in cds or [""]:
        cd = rel_norm(cd).strip("/")
        for n in names:
            n = rel_norm(n)
            if n:
                out.append(normalize_candidate("materials/" + (cd + "/" if cd else "") + n + ".vmat"))
    return out

# ======================================================================== KEYVALUES / VMT

def _strip_comments_keep_quotes(text: str) -> str:
    out_lines = []
    for line in text.splitlines():
        result, in_quotes, i = [], False, 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                in_quotes = not in_quotes
            elif not in_quotes and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            result.append(ch)
            i += 1
        out_lines.append("".join(result))
    return "\n".join(out_lines)

KV_TOK_RE = re.compile(r'"[^"]*"|\[[^\]]*\]|[^\s{}"]+|{|}')

def _kv_add(d: dict, k: str, v):
    """Repeated block keys (several 'Clamp' proxies, ...) become a list of dicts."""
    if isinstance(v, dict) and k in d:
        old = d[k]
        if isinstance(old, list):
            old.append(v)
            return
        if isinstance(old, dict):
            d[k] = [old, v]
            return
    d[k] = v

def _parse_kv_block(tokens, i):
    d = {}
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "}":
            return d, i + 1
        if tok == "{":
            i += 1
            continue
        key = tok.strip('"').lower()
        if i + 1 >= n:
            break
        nxt = tokens[i + 1]
        if nxt == "{":
            sub, i = _parse_kv_block(tokens, i + 2)
            _kv_add(d, key, sub)
        elif nxt == "}":
            i += 1
        else:
            d[key] = nxt.strip('"')
            i += 2
    return d, i

def parse_kv_tree(text: str):
    tokens = KV_TOK_RE.findall(_strip_comments_keep_quotes(text))
    if not tokens:
        return "lightmappedgeneric", {}
    shader = tokens[0].strip('"').lower()
    if len(tokens) < 2 or tokens[1] != "{":
        return shader, {}
    tree, _ = _parse_kv_block(tokens, 2)
    return shader, tree

def _kv_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _kv_merge(out[k], v)
        else:
            out[k] = v
    return out

def _emit_kv_block(d: dict, level: int):
    pad = "\t" * level
    out = []
    for k, v in d.items():
        for item in (v if isinstance(v, list) else [v]):
            if isinstance(item, dict):
                out.append(f'{pad}"{k}"')
                out.append(f"{pad}{{")
                out.extend(_emit_kv_block(item, level + 1))
                out.append(f"{pad}}}")
            else:
                out.append(f'{pad}"{k}" "{item}"')
    return out

def _kv_tree_to_text(shader: str, tree: dict) -> str:
    return "\n".join([f'"{shader}"', "{", *_emit_kv_block(tree, 1), "}"]) + "\n"

@lru_cache(maxsize=100_000)
def read_text_safe(path_str: str) -> str:
    path = Path(path_str)
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return path.read_text(errors="ignore")

def _resolve_vmt_tree(text: str, depth: int = 0):
    """Resolve patch VMTs (include + insert + replace) recursively through all Source1 roots."""
    shader, tree = parse_kv_tree(text)
    if shader != "patch" or depth > 8:
        return shader, tree, False
    inc = tree.get("include")
    if not isinstance(inc, str):
        return shader, tree, False
    rel = "materials/" + strip_materials_prefix(inc)
    if not rel.lower().endswith(".vmt"):
        rel += ".vmt"
    inc_abs, _ = find_in_source1(rel)
    if not inc_abs:
        return shader, tree, False
    base_shader, base_tree, _ = _resolve_vmt_tree(read_text_safe(inc_abs), depth + 1)
    merged = base_tree
    for name in ("insert", "replace"):
        blk = tree.get(name)
        if isinstance(blk, dict):
            merged = _kv_merge(merged, blk)
    return base_shader, merged, True

def _as_list(v):
    return v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])

def _to_float(s, default=0.0):
    try:
        return float(str(s).strip())
    except Exception:
        return default

def parse_envmaptint_scalar(s):
    if not s:
        return None
    s = str(s).strip().replace("[", "").replace("]", "").replace("{", "").replace("}", "")
    parts = [p for p in s.replace(",", " ").split() if p]
    try:
        return float(parts[0]) if parts else None
    except Exception:
        return None

def parse_vmt(vmt_path: Path):
    text = read_text_safe(str(vmt_path))
    shader, tree, resolved = _resolve_vmt_tree(text)
    shown_text = _kv_tree_to_text(shader, tree) if resolved else text

    flat = {k: v for k, v in tree.items() if isinstance(v, str)}
    for k, v in tree.items():           # >=dx9x blocks only fill in missing keys
        if isinstance(v, dict) and k.startswith(">=dx9"):
            for kk, vv in v.items():
                if isinstance(vv, str):
                    flat.setdefault(kk, vv)

    # "!gameconsole?$key" applies on PC; "gameconsole?$key" is console-only and ignored.
    for k, v in list(flat.items()):
        if k.startswith("!gameconsole?"):
            flat.setdefault(k[len("!gameconsole?"):], v)

    def is_true(key):
        return str(flat.get(key, "")).strip() == "1"

    def ref(key):
        return rel_norm(flat[key]) if flat.get(key) else None

    shader = shader.removeprefix("sdk_").removesuffix("_dx9").removesuffix("_hdr")
    data = {
        "shader": shader,
        "basetexture": ref("$basetexture"),
        "texture2": ref("$texture2"),
        "normalmap": ref("$normalmap") or ref("$bumpmap"),
        "surfaceprop": flat.get("$surfaceprop"),
        "translucent": is_true("$translucent"),
        "alphatest": is_true("$alphatest"),
        "additive": is_true("$additive"),
        "nocull": is_true("$nocull"),
        "ssbump": is_true("$ssbump"),
        "selfillum": is_true("$selfillum"),
        "alphatestreference": None,
        "envmapmask": ref("$envmapmask"),
        "phongmask": ref("$phongmask"),
        "envmap": flat.get("$envmap"),
        "envmaptint": flat.get("$envmaptint"),
        "normalmapalphaenvmapmask": is_true("$normalmapalphaenvmapmask"),
        "basealphaenvmapmask": is_true("$basealphaenvmapmask"),
        "detail": ref("$detail"),
        "detailscale": flat.get("$detailscale"),
        "detailblendfactor": flat.get("$detailblendfactor"),
        "detailblendmode": flat.get("$detailblendmode"),
        "fps": 15,
        "scrolls": [],
        "text": shown_text,
    }
    if flat.get("$alphatestreference"):
        v = _to_float(flat["$alphatestreference"], None)
        data["alphatestreference"] = v

    proxies = tree.get("proxies")
    if isinstance(proxies, dict):
        for blk in _as_list(proxies.get("texturescroll")):
            data["scrolls"].append((
                str(blk.get("texturescrollvar", "")).lower(),
                _to_float(blk.get("texturescrollrate", 0)),
                _to_float(blk.get("texturescrollangle", 0)),
            ))
        for blk in _as_list(proxies.get("animatedtexture")):
            if "animatedtextureframerate" in blk:
                data["fps"] = int(_to_float(blk["animatedtextureframerate"], 15))
    return data

# ======================================================================== VTF DECODING

def vtf_block_bytes(fmt):
    if fmt in (IMAGE_FORMAT_DXT1, IMAGE_FORMAT_DXT1_ONEBITALPHA):
        return 8
    if fmt in (IMAGE_FORMAT_DXT3, IMAGE_FORMAT_DXT5):
        return 16
    return 0

def vtf_bytes_per_pixel(fmt):
    if fmt in (IMAGE_FORMAT_BGRA8888, IMAGE_FORMAT_RGBA8888, IMAGE_FORMAT_ABGR8888,
               IMAGE_FORMAT_ARGB8888, IMAGE_FORMAT_BGRX8888):
        return 4
    if fmt in (IMAGE_FORMAT_RGB888, IMAGE_FORMAT_BGR888):
        return 3
    return 0

def mip_level_size(w, h, fmt):
    bb = vtf_block_bytes(fmt)
    if bb:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bb
    bpp = vtf_bytes_per_pixel(fmt)
    if not bpp:
        raise ValueError(f"Unsupported format for sizing: {fmt}")
    return w * h * bpp

def parse_vtf_header(vtf_path: Path):
    with open(vtf_path, "rb") as f:
        if f.read(4) != b"VTF\0":
            raise ValueError("Not a VTF file (missing signature).")
        ver_major, ver_minor = struct.unpack("<II", f.read(8))
        header_size = struct.unpack("<I", f.read(4))[0]
        width, height = struct.unpack("<HH", f.read(4))
        flags = struct.unpack("<I", f.read(4))[0]
        frames, start_frame = struct.unpack("<HH", f.read(4))
        f.read(4); f.read(12); f.read(4); f.read(4)
        image_format = struct.unpack("<I", f.read(4))[0]
        mip_count = struct.unpack("<B", f.read(1))[0]
        low_res_format = struct.unpack("<I", f.read(4))[0]
        low_res_w = struct.unpack("<B", f.read(1))[0]
        low_res_h = struct.unpack("<B", f.read(1))[0]
        depth = 1
        if ver_major == 7 and ver_minor >= 2:
            depth = struct.unpack("<H", f.read(2))[0]
    return {
        "ver": (ver_major, ver_minor), "header_size": header_size, "width": width, "height": height,
        "depth": max(1, depth), "flags": flags, "frames": max(1, frames), "start_frame": start_frame,
        "format": image_format, "mip_count": max(1, mip_count),
        "low_res_format": low_res_format, "low_res_w": low_res_w, "low_res_h": low_res_h,
    }

def parse_vtf_resources_if_any(vtf_path: Path, hdr):
    """VTF 7.3+: numResources @68, entries (3-byte tag, 1-byte flags, uint32 offset) from @80."""
    ver_major, ver_minor = hdr["ver"]
    if ver_major != 7 or ver_minor < 3:
        return {}
    with open(vtf_path, "rb") as f:
        blob = f.read(max(hdr["header_size"], 80))
    if len(blob) < 80:
        return {}
    num = struct.unpack_from("<I", blob, 68)[0]
    if num > 64 or 80 + num * 8 > len(blob):
        return {}
    file_size = vtf_path.stat().st_size
    out = {}
    for i in range(num):
        raw_tag, off = struct.unpack_from("<II", blob, 80 + i * 8)
        if (raw_tag >> 24) & 0x02:
            continue
        if off < file_size:
            out[raw_tag & 0x00FFFFFF] = off
    return out

def compute_image_data_offset(hdr, resources=None):
    resources = resources or {}
    if VTF_RSRC_IMAGE in resources:
        return resources[VTF_RSRC_IMAGE]
    lrf = hdr["low_res_format"]
    low = 0
    if lrf != 0xFFFFFFFF and hdr["low_res_w"] and hdr["low_res_h"]:
        try:
            low = mip_level_size(hdr["low_res_w"], hdr["low_res_h"], lrf)
        except Exception:
            low = 0
    return hdr["header_size"] + low

def compute_largest_mip_layout(vtf_path: Path, hdr):
    file_size = vtf_path.stat().st_size
    fmt, frames, depth = hdr["format"], hdr["frames"], hdr["depth"]
    start = compute_image_data_offset(hdr, parse_vtf_resources_if_any(vtf_path, hdr))
    w0, h0 = hdr["width"], hdr["height"]
    for faces in ((6 if hdr["flags"] & TEXTUREFLAGS_ENVMAP else 1), 1):
        for mips in range(hdr["mip_count"], 0, -1):
            try:
                cw, ch = max(1, w0 >> (mips - 1)), max(1, h0 >> (mips - 1))
                sizes = []
                for _ in range(mips):
                    sizes.append(mip_level_size(cw, ch, fmt))
                    cw, ch = min(w0, cw * 2), min(h0, ch * 2)
                base = start + sum(sizes[:-1]) * frames * faces * depth
                largest = sizes[-1]
                if base + largest <= file_size:
                    return base, largest, largest * faces * depth
            except Exception:
                continue
    w, h = w0, h0
    for _ in range(16):
        try:
            cand = mip_level_size(w, h, fmt)
        except Exception:
            break
        if start + cand <= file_size:
            return start, cand, cand
        w, h = max(1, w // 2), max(1, h // 2)
    raise ValueError("VTF image data offset/size does not fit file (corrupt or unsupported layout).")

def _rgb565(c):
    return (((c >> 11) & 0x1F) * 255 // 31, ((c >> 5) & 0x3F) * 255 // 63, (c & 0x1F) * 255 // 31)

def _color_palette(c0, c1, four_color_always):
    p0, p1 = _rgb565(c0), _rgb565(c1)
    if four_color_always or c0 > c1:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
        return [p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,)]
    p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
    return [p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0)]

def decode_dxt1(data, w, h, one_bit_alpha=False):
    out = bytearray(w * h * 4)
    pos = 0
    for by in range((h + 3) // 4):
        for bx in range((w + 3) // 4):
            if pos + 8 > len(data):
                return bytes(out)
            c0, c1 = struct.unpack_from("<HH", data, pos)
            bits = struct.unpack_from("<I", data, pos + 4)[0]
            pos += 8
            pal = _color_palette(c0, c1, False)
            if not one_bit_alpha:
                pal[3] = pal[3][:3] + (255,)       # Source treats plain DXT1 as opaque
            for py in range(4):
                for px in range(4):
                    x, y = bx * 4 + px, by * 4 + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        out[o:o + 4] = bytes(pal[(bits >> (2 * (py * 4 + px))) & 3])
    return bytes(out)

def decode_dxt3(data, w, h):
    out = bytearray(w * h * 4)
    pos = 0
    for by in range((h + 3) // 4):
        for bx in range((w + 3) // 4):
            if pos + 16 > len(data):
                return bytes(out)
            alpha_val = int.from_bytes(data[pos:pos + 8], "little")
            c0, c1 = struct.unpack_from("<HH", data, pos + 8)
            bits = struct.unpack_from("<I", data, pos + 12)[0]
            pos += 16
            pal = _color_palette(c0, c1, True)
            for py in range(4):
                for px in range(4):
                    x, y = bx * 4 + px, by * 4 + py
                    if x < w and y < h:
                        p = py * 4 + px
                        r, g, b, _ = pal[(bits >> (2 * p)) & 3]
                        o = (y * w + x) * 4
                        out[o:o + 4] = bytes((r, g, b, ((alpha_val >> (4 * p)) & 0xF) * 255 // 15))
    return bytes(out)

def decode_dxt5(data, w, h):
    out = bytearray(w * h * 4)
    pos = 0
    for by in range((h + 3) // 4):
        for bx in range((w + 3) // 4):
            if pos + 16 > len(data):
                return bytes(out)
            a0, a1 = data[pos], data[pos + 1]
            alpha_idx = int.from_bytes(data[pos + 2:pos + 8], "little")
            c0, c1 = struct.unpack_from("<HH", data, pos + 8)
            bits = struct.unpack_from("<I", data, pos + 12)[0]
            pos += 16
            ap = [a0, a1]
            if a0 > a1:
                ap += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                ap += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            pal = _color_palette(c0, c1, True)
            for py in range(4):
                for px in range(4):
                    x, y = bx * 4 + px, by * 4 + py
                    if x < w and y < h:
                        p = py * 4 + px
                        r, g, b, _ = pal[(bits >> (2 * p)) & 3]
                        o = (y * w + x) * 4
                        out[o:o + 4] = bytes((r, g, b, ap[(alpha_idx >> (3 * p)) & 7]))
    return bytes(out)

def decode_uncompressed_to_rgba(data, w, h, fmt):
    n = w * h
    if fmt == IMAGE_FORMAT_RGBA8888:
        return bytes(data[:n * 4])
    out = bytearray(n * 4)
    for i in range(n):
        if fmt == IMAGE_FORMAT_BGRA8888:
            b, g, r, a = data[i * 4:i * 4 + 4]
        elif fmt == IMAGE_FORMAT_ABGR8888:
            a, b, g, r = data[i * 4:i * 4 + 4]
        elif fmt == IMAGE_FORMAT_ARGB8888:
            a, r, g, b = data[i * 4:i * 4 + 4]
        elif fmt == IMAGE_FORMAT_BGRX8888:
            b, g, r, _x = data[i * 4:i * 4 + 4]
            a = 255
        elif fmt == IMAGE_FORMAT_BGR888:
            b, g, r = data[i * 3:i * 3 + 3]
            a = 255
        elif fmt == IMAGE_FORMAT_RGB888:
            r, g, b = data[i * 3:i * 3 + 3]
            a = 255
        else:
            raise ValueError(f"Unsupported uncompressed format: {fmt}")
        out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return bytes(out)

def vtf_extract_frames_rgba(vtf_path: Path):
    hdr = parse_vtf_header(vtf_path)
    fmt = hdr["format"]
    if fmt not in SUPPORTED:
        raise ValueError(f"VTF format not supported by this script: {fmt}")
    base, largest, stride = compute_largest_mip_layout(vtf_path, hdr)
    w, h = hdr["width"], hdr["height"]
    file_size = vtf_path.stat().st_size
    frames = []
    with open(vtf_path, "rb") as f:
        for i in range(hdr["frames"]):
            off = base + i * stride
            if off + largest > file_size:
                break
            f.seek(off)
            chunk = f.read(largest)
            if len(chunk) != largest:
                break
            if fmt == IMAGE_FORMAT_DXT1:
                rgba = decode_dxt1(chunk, w, h, False)
            elif fmt == IMAGE_FORMAT_DXT1_ONEBITALPHA:
                rgba = decode_dxt1(chunk, w, h, True)
            elif fmt == IMAGE_FORMAT_DXT3:
                rgba = decode_dxt3(chunk, w, h)
            elif fmt == IMAGE_FORMAT_DXT5:
                rgba = decode_dxt5(chunk, w, h)
            else:
                rgba = decode_uncompressed_to_rgba(chunk, w, h, fmt)
            frames.append((w, h, rgba))
    if not frames:
        raise ValueError("Unexpected EOF reading image data.")
    return hdr, frames

@lru_cache(maxsize=16)
def vtf_extract_frames_rgba_cached(path_str: str):
    return vtf_extract_frames_rgba(Path(path_str))

# ======================================================================== IMAGES

def _next_pow2(x):
    p = 1
    while p < x:
        p <<= 1
    return p

def build_atlas(rgba_list, w, h, out_path: Path):
    count = len(rgba_list)
    cols = _next_pow2(math.ceil(math.sqrt(count)))
    rows = _next_pow2(math.ceil(count / cols))
    atlas = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
    for idx, rgba in enumerate(rgba_list):
        atlas.paste(Image.frombytes("RGBA", (w, h), rgba), ((idx % cols) * w, (idx // cols) * h))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(out_path)
    return rows, cols

def save_static(rgba, w, h, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGBA", (w, h), rgba).save(out_path)

def ssbump_to_normal_rgba(rgba, w, h):
    """$ssbump textures are self-shadowing bump maps: R, G, B are the light reaching the surface from the three Source1 radiosity
    basis directions, not a tangent-space normal (exported as one they average ~(122,123,116) instead of ~(128,128,255) and light
    wrongly). Rebuild an approximate normal: n = R*b0 + G*b1 + B*b2, normalised, with the Source1 basis (the same y orientation as
    Source1 normal maps, so legacy_source1_inverted_normal still applies). -> rgba bytes (alpha 255)."""
    r2, r6, r3 = math.sqrt(2.0 / 3.0), 1.0 / math.sqrt(6.0), 1.0 / math.sqrt(3.0)
    h2 = 1.0 / math.sqrt(2.0)
    try:
        import numpy as np
        a = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].astype(np.float32) / 255.0
        bas = np.array([[r2, 0.0, r3], [-r6, h2, r3], [-r6, -h2, r3]], dtype=np.float32)
        n = a @ bas
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
        out = np.empty((h, w, 4), dtype=np.uint8)
        out[:, :, :3] = np.clip((n * 0.5 + 0.5) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        out[:, :, 3] = 255
        return out.tobytes()
    except ImportError:
        pass
    out = bytearray(w * h * 4)
    for i in range(w * h):
        r, g, b = rgba[i * 4] / 255.0, rgba[i * 4 + 1] / 255.0, rgba[i * 4 + 2] / 255.0
        x, y, z = r * r2 - (g + b) * r6, (g - b) * h2, (r + g + b) * r3
        ln = math.sqrt(x * x + y * y + z * z) or 1.0
        o = i * 4
        out[o] = int((x / ln * 0.5 + 0.5) * 255.0 + 0.5)
        out[o + 1] = int((y / ln * 0.5 + 0.5) * 255.0 + 0.5)
        out[o + 2] = int((z / ln * 0.5 + 0.5) * 255.0 + 0.5)
        out[o + 3] = 255
    return bytes(out)

def save_png(rgba, w, h, out_path: Path):
    save_static(rgba, w, h, out_path)

def _extract_mask_from_rgba(rgba, w, h, channel="A", invert=True):
    img = Image.frombytes("RGBA", (w, h), rgba)
    if channel.upper() == "L":
        mask = img.convert("L")
    else:
        mask = img.getchannel({"R": 0, "G": 1, "B": 2, "A": 3}[channel.upper()])
    if invert:
        mask = mask.point(lambda v: 255 - v)
    return Image.merge("RGBA", (mask, mask, mask, Image.new("L", mask.size, 255))).tobytes()

def write_texture_settings_txt(texture_path: Path, settings: dict):
    lines = ['"settings"', '{']
    for k, v in settings.items():
        lines.append(f'\t"{k}"\t\t"{v}"')
    lines.append('}')
    texture_path.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

def _tex_base(ref: str, suffix: str) -> str:
    """'Effects/Foo/bar' -> 'materials/effects/foo/bar<suffix>' (lower-case, suffix not doubled)."""
    t = strip_materials_prefix(ref).lower()
    if t.endswith(".vtf"):
        t = t[:-4]
    # '+' (CS:GO animated-texture frames: +0name) is a "fused delimiter" in resourcecompiler: files are written with '_' instead
    if t.endswith(suffix):
        return ("materials/" + t).replace("+", "_")
    return ("materials/" + t + suffix).replace("+", "_")

def _load_vtf_first_frame(ref: str):
    vtf = resolve_vtf(ref)
    if vtf is None:
        return None
    try:
        return vtf_extract_frames_rgba_cached(str(vtf))[1][0]
    except Exception:
        return None

class TextureMissing(Exception):
    pass

_EXPORTED_TEXTURES = {}
MISSING_TEXTURES = set()
MISSING_TEXTURE_USERS = {}   # texture -> {vmat that needed it}
OVERLAY_VMATS = set()      # lower-case 'materials/x.vmat' used by CMapStaticOverlay elements in the scanned vmaps; written with the static overlay shader

def export_color_texture(export_dir: Path, ref: str, allow_anim: bool):
    """Decode a VTF into <ref>_color.tga (static) or <ref>_color.png (multi-frame atlas).
    Returns (rel_path, flipbook) where flipbook is None or (rows, cols, nframes)."""
    base = _tex_base(ref, "_color")
    key = (str(export_dir), base, allow_anim)
    if key in _EXPORTED_TEXTURES:
        return _EXPORTED_TEXTURES[key]
    vtf = resolve_vtf(ref)
    if vtf is None:
        raise TextureMissing(ref)
    _hdr, frames = vtf_extract_frames_rgba_cached(str(vtf))
    if allow_anim and len(frames) > 1:
        rel = base + ANIM_EXT
        w, h, _ = frames[0]
        rows, cols = build_atlas([r for (_, _, r) in frames], w, h, export_dir / rel)
        result = (rel, (rows, cols, len(frames)))
    else:
        rel = base + STATIC_EXT
        w, h, rgba = frames[0]
        save_static(rgba, w, h, export_dir / rel)
        result = (rel, None)
    _EXPORTED_TEXTURES[key] = result
    return result

# ======================================================================== CS2 VPK INDEX

VPK_SIGNATURE = 0x55AA1234
VPK_READ_PREFIXES = ("soundevents/",)      # only these entries keep their data location (to read them later)

class Cs2Vpk:
    """Directory index of the CS2 *_dir.vpk files: which files exist (no extraction). Read-only."""
    def __init__(self):
        self.files = set()                 # lower-case 'dir/name.ext'
        self.dirs = {}                     # lower-case dir -> set of file names
        self.entries = {}                  # path -> (dir vpk path, archive idx, offset, length, preload bytes) for VPK_READ_PREFIXES
        self.loaded = []                   # [(vpk path, file count)]
        self.errors = []

    def add_dir_vpk(self, path: str):
        try:
            st = os.stat(path)
            cache = os.path.join(LOG_DIR, ".vpkcache3_" + re.sub(r"[^A-Za-z0-9]+", "_", path)[-120:] + ".pkl")
            key = (path, st.st_size, int(st.st_mtime))
            try:
                with open(cache, "rb") as f:
                    ckey, files, entries = pickle.load(f)
                if ckey == key:
                    self._merge(files, entries)
                    self.loaded.append((path, len(files)))
                    return
            except Exception:
                pass
            files, entries = self._parse(path)
            try:
                with open(cache, "wb") as f:
                    pickle.dump((key, files, entries), f, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                pass
            self._merge(files, entries)
            self.loaded.append((path, len(files)))
        except Exception as e:
            self.errors.append(f"{path}: {e}")

    def _merge(self, files, entries):
        for fp in files:
            self.files.add(fp)
            d, _, n = fp.rpartition("/")
            self.dirs.setdefault(d, set()).add(n)
        self.entries.update(entries)

    @staticmethod
    def _parse(path: str):
        files, entries = [], {}
        with open(path, "rb") as f:
            sig, ver, tree_size = struct.unpack("<III", f.read(12))
            if sig != VPK_SIGNATURE or ver not in (1, 2):
                raise ValueError(f"not a VPK v1/v2 file (signature {sig:#x}, version {ver})")
            header = 12 if ver == 1 else 28
            f.seek(header)
            tree = f.read(tree_size)
        data_start = header + tree_size
        n, find, unpack = len(tree), tree.find, struct.unpack_from
        pos = 0

        def cstr():
            nonlocal pos
            e = find(b"\0", pos)
            s = tree[pos:e].decode("utf-8", errors="replace")
            pos = e + 1
            return s

        while pos < n:
            ext = cstr()
            if not ext:
                break
            while True:
                d = cstr()
                if not d:
                    break
                while True:
                    name = cstr()
                    if not name:
                        break
                    _crc, preload, arch, offset, length, _term = unpack("<IHHIIH", tree, pos)
                    pos += 18
                    pre = tree[pos:pos + preload]
                    pos += preload
                    dd = "" if d == " " else d
                    fp = ((dd + "/") if dd else "") + ("" if name == " " else name) + ("" if ext == " " else "." + ext)
                    fp = fp.replace("\\", "/").lower()
                    files.append(fp)
                    if fp.startswith(VPK_READ_PREFIXES) or fp.endswith(".vmat_c") or (fp.startswith("materials/") and fp.endswith(".vtex_c")):
                        entries[fp] = (path, arch, (data_start + offset) if arch == 0x7FFF else offset, length, pre)
        return files, entries

    def has(self, rel: str) -> bool:
        return rel in self.files

    def listdir(self, rel_dir: str):
        return self.dirs.get(rel_dir.strip("/").lower(), set())

    def read(self, rel: str):
        ent = self.entries.get(rel.lower())
        if not ent:
            return None
        dir_path, arch, offset, length, pre = ent
        if length == 0:
            return pre
        fn = dir_path if arch == 0x7FFF else dir_path.replace("_dir.vpk", f"_{arch:03d}.vpk")
        try:
            with open(fn, "rb") as f:
                f.seek(offset)
                return pre + f.read(length)
        except Exception:
            return None

_CS2_VPK = None

def cs2_vpk() -> Cs2Vpk:
    global _CS2_VPK
    if _CS2_VPK is None:
        v = Cs2Vpk()
        if USE_CS2_VPK:
            for sub in CS2_VPK_FOLDERS:
                folder = os.path.join(CS2_GAME_DIR, sub)
                try:
                    names = sorted(x for x in os.listdir(folder)
                                   if x.lower().endswith("_dir.vpk") and not x.lower().startswith(CS2_VPK_SKIP_PREFIXES))
                except Exception:
                    continue
                for nm in names:
                    v.add_dir_vpk(os.path.join(folder, nm))
        _CS2_VPK = v
    return _CS2_VPK

def cs2_file_exists(rel: str) -> bool:
    """File present in the CS2 install: VPK index first, then the extracted folders."""
    rel = rel_norm(rel).lower()
    if cs2_vpk().has(rel):
        return True
    return any(file_exists(os.path.join(root, rel)) for root in (CS2_VERIFY_FOLDER, *CS2_EXTRA_VERIFY_FOLDERS))

STUB_CS2_VMATS = set()     # CS2 vmats that compile to the error shader (Valve shipped them without textures)

@lru_cache(maxsize=100_000)
def cs2_vmat_is_stub(rel: str) -> bool:
    """True when CS2's compiled vmat (in the VPK) uses core/error.vfx, i.e. it was never ported properly and has no
    texture: referencing it in a map shows the error material. Needs the VPK data to be readable; False otherwise."""
    data = cs2_vpk().read(rel_norm(rel).lower() + "_c")
    return bool(data) and b"error.vfx" in data

def cs2_listdir(rel_dir: str):
    """File names directly inside rel_dir across the VPKs and the extracted folders."""
    rel_dir = rel_norm(rel_dir).strip("/")
    names = set(cs2_vpk().listdir(rel_dir))
    for root in (CS2_VERIFY_FOLDER, *CS2_EXTRA_VERIFY_FOLDERS):
        try:
            names.update(listdir_cached(os.path.join(root, rel_dir)))
        except Exception:
            pass
    return names

# ======================================================================== CS2 INSTALL LOOKUPS

def _hashed_color_vtex_name(folder_rel: str, base: str):
    base_low = base.lower()
    best_c, best = None, None
    for f in cs2_listdir(folder_rel):
        fl = f.lower()
        if not fl.startswith(base_low + "_color_tga_"):
            continue
        if fl.endswith(".vtex_c"):
            best_c = f
        elif fl.endswith(".vtex"):
            best = f
    return best_c or best

@lru_cache(maxsize=500_000)
def try_resolve_cs2_texture_for_vtf(asset_rel_vtf: str):
    """Texture already shipped in the CS2 install. -> (marker, rel path) or (None, None).
    The first value is only a truthy marker ('cs2:<rel>'); nothing is copied from the install."""
    asset_rel_vtf = normalize_candidate(asset_rel_vtf)
    base_no_ext = os.path.splitext(asset_rel_vtf)[0]
    folder_rel = os.path.dirname(base_no_ext)
    base = os.path.basename(base_no_ext)
    for rel in (base_no_ext + ".vtex_c", base_no_ext + ".vtex"):
        if cs2_file_exists(rel):
            return "cs2:" + rel, rel
    hashed = _hashed_color_vtex_name(folder_rel, base)
    if hashed:
        rel = rel_norm(os.path.join(folder_rel, hashed))
        return "cs2:" + rel, rel
    rel_color = rel_norm(os.path.join(folder_rel, base + "_color.tga"))
    if cs2_file_exists(rel_color):
        return "cs2:" + rel_color, rel_color
    return None, None

@lru_cache(maxsize=500_000)
def cs2_compiled_texture_exists(asset_rel: str) -> bool:
    """A source image (.tga/.png/...) counts as present when CS2 has its compiled .vtex_c
    (either '<name>.vtex_c' or '<name>_<ext>_<hash>.vtex_c')."""
    base_no_ext, ext = os.path.splitext(asset_rel)
    if cs2_file_exists(base_no_ext + ".vtex_c"):
        return True
    stem = os.path.basename(base_no_ext).lower()
    prefix = f"{stem}_{ext[1:].lower()}_"
    return any(f.lower().startswith(prefix) and f.lower().endswith(".vtex_c")
               for f in cs2_listdir(os.path.dirname(base_no_ext)))

# ======================================================================== VMAT WRITER

def comment_original_vmt(vmt_text: str) -> str:
    lines = vmt_text.splitlines()
    out = ["// =================== BEGIN ORIGINAL VMT (%d lines) ===================" % len(lines)]
    out.extend("// " + ln for ln in lines)
    out.append("// ==================== END ORIGINAL VMT ====================")
    return "\n".join(out)

def generate_vmat(
    vmat_path: Path, shader: str, texture_color_path: str, original_vmt_text: str,
    surfaceprop=None, alphatest=False, translucent=False, additive=False, selfillum=False,
    alphatestreference=None, texture_normal_path=None, texture_roughness_path=None,
    texture_detail_path=None, detail_scale=None, detail_blend_factor=None, detail_blend_mode=None,
    flipbook=None, specular_indirect=False, cubemap_scalar=None, roughness_scalar=None,
    texture2_path=None, tex2_scroll=None, texture_translucency_path=None, nocull=False,
):
    shader_name = SHADER_MAP.get((shader or "").lower().strip(), "csgo_vertexlitgeneric.vfx")
    # Additive: only csgo_unlitgeneric has a confirmed additive switch (F_BLEND_MODE 4); a saved
    # vertexlit vmat dropped F_ADDITIVE_BLEND (no such option) and rendered opaque/black.
    if additive and shader_name != "csgo_unlitgeneric.vfx" and not (LIT_ADDITIVE_VIA_TRANSLUCENT and shader_name == "csgo_lightmappedgeneric.vfx"):
        shader_name = "csgo_unlitgeneric.vfx"
        texture2_path = None
    is_lightmapped = shader_name == "csgo_lightmappedgeneric.vfx"
    is_unlit = shader_name == "csgo_unlitgeneric.vfx"

    L = ['"Layer0"', "{", f'    "Shader" "{shader_name}"']

    def rough_line(key):
        if texture_roughness_path:
            L.append(f'    "{key}" "{texture_roughness_path}"')
        elif roughness_scalar is not None:
            L.append(f'    "{key}" "[{roughness_scalar:.6f} {roughness_scalar:.6f} {roughness_scalar:.6f} 1.000000]"')

    if is_lightmapped:
        L.append(f'    "TextureLayer1Color" "{texture_color_path}"')
        if texture_normal_path:
            L.append(f'    "TextureLayer1Normal" "{texture_normal_path}"')
        rough_line("TextureLayer1Roughness")
        if texture_translucency_path:
            L.append(f'    "TextureLayer1Translucency" "{texture_translucency_path}"')
        L += [f'    "TextureLayer1AmbientOcclusion" "{DEFAULT_AO}"',
              '    "F_LAYERS" "0"',
              '    "g_vLayer1Tint" "[1.000000 1.000000 1.000000]"',
              '    "F_TEXTURETRANSFORMS" "0"']
        if texture_detail_path:
            L.append(f'    "TextureLayer1Detail" "{texture_detail_path}"')
            if detail_scale is not None:
                L.append(f'    "g_vLayer1DetailScale" "[{detail_scale:.6f} {detail_scale:.6f}]"')
            bf = 1.0 if detail_blend_factor is None else detail_blend_factor
            L.append(f'    "g_vLayer1DetailTintAndBlend" "[1.000000 1.000000 1.000000 {bf:.6f}]"')
            L.append('    "F_DETAILTEXTURE" "1"')
            L.append(f'    "F_DETAILBLENDMODE" "{detail_blend_mode if detail_blend_mode is not None else 0}"')
    elif is_unlit:
        L.append(f'    "TextureColor" "{texture_color_path}"')
        if texture_translucency_path:
            L.append(f'    "TextureTranslucency" "{texture_translucency_path}"')
        if texture2_path:
            L += ['    "F_TWOTEXTURE" "1"', f'    "Texture2Color" "{texture2_path}"']
    else:
        L.append(f'    "TextureColor" "{texture_color_path}"')
        if texture_normal_path:
            L.append(f'    "TextureNormal" "{texture_normal_path}"')
        rough_line("TextureRoughness")
        if texture_translucency_path:
            L.append(f'    "TextureTranslucency" "{texture_translucency_path}"')

    if not is_unlit:
        if specular_indirect:
            L.append('    "F_SPECULAR_INDIRECT" "1"')
        if cubemap_scalar is not None:
            L.append(f'    "g_flCubeMapScalar" "{cubemap_scalar:.6f}"')

    # Unlit shader: single F_BLEND_MODE dropdown (confirmed from a saved vmat: 4 = Additive;
    # vertexlit confirmed F_TRANSLUCENT and F_ALPHA_TEST (no additive option);
    # other numbers assumed from dropdown order). Lightmapped/vertexlit shaders: separate flags;
    # F_TRANSLUCENT confirmed from a saved vmat; F_ALPHA_TEST name unconfirmed.
    if is_unlit:
        if additive:
            L.append(f'    "F_BLEND_MODE" "{BLEND_MODE_ADDITIVE}"')
        elif alphatest:
            L.append(f'    "F_BLEND_MODE" "{BLEND_MODE_ALPHATEST}"')
        elif translucent:
            L.append(f'    "F_BLEND_MODE" "{BLEND_MODE_TRANSLUCENT}"')
    else:
        # additive materials only reach here on the lightmapped shader (LIT_ADDITIVE_VIA_TRANSLUCENT); everything else moves to the unlit shader in convert_one_vmt
        if additive:
            L.append('    "F_TRANSLUCENT" "1"')
            L.append('    "F_ADDITIVE_BLEND" "1"')
        elif alphatest:
            L.append('    "F_ALPHA_TEST" "1"')
        elif translucent:
            L.append('    "F_TRANSLUCENT" "1"')
    if nocull:
        L.append('    "F_RENDER_BACKFACES" "1"')   # confirmed on unlit, vertexlit and lightmapped
        if not is_unlit and NOCULL_DONT_FLIP_NORMALS:
            L.append('    "F_DONT_FLIP_BACKFACE_NORMALS" "1"')   # vertexlit/lightmapped only
    if selfillum:
        L.append('    "F_SELF_ILLUM" "1"')
    if alphatest and alphatestreference is not None:
        L.append(f'    "g_flAlphaTestReference" "{float(alphatestreference):.6f}"')

    if flipbook:
        rows, cols, fps, nframes = flipbook
        if is_unlit:
            tpf = (1.0 / fps) if fps and fps > 0 else 0.1
            L += ['    "F_TEXTURE_ANIMATION" "1"',
                  f'    "g_vAnimationGrid" "[{cols} {rows}]"',
                  f'    "g_nNumAnimationCells" "{nframes}"',
                  f'    "g_flAnimationTimePerFrame" "{tpf:.3f}"']
        else:
            L += ['    "F_FLIPBOOK" "1"',
                  f'    "g_vFlipbookGrid" "[{cols} {rows}]"',
                  f'    "g_flFlipbookFrameRate" "{fps}"']

    if tex2_scroll and texture2_path and is_unlit:
        dx, dy = tex2_scroll
        L += ['    "DynamicParams"', "    {",
              f'        "g_vTex2CoordOffset" "frac( float2( {dx:.3f}, {dy:.3f} ) * time() )"',
              "    }"]

    if surfaceprop:
        L += ['    "SystemAttributes"', "    {",
              f'        "PhysicsSurfaceProperties" "{surfaceprop}"', "    }"]
    L.append("}")

    vmat = "\n".join(L)
    if original_vmt_text:
        vmat += "\n\n" + comment_original_vmt(original_vmt_text) + "\n"
    vmat_path.parent.mkdir(parents=True, exist_ok=True)
    vmat_path.write_text(vmat, encoding="utf-8")

# ======================================================================== VMT -> VMAT

def generate_overlay_vmat(vmat_path: Path, texture_color_path: str, mask_path, original_vmt_text: str):
    """Static overlays (CMapStaticOverlay: Source1 infodecal / info_overlay) need csgo_static_overlay.vfx. Layout copied
    from a working port of the same kind of material: translucent blend (F_BLEND_MODE 1), lit, alpha as a
    TextureTranslucency mask. Without the mask the overlay is drawn as an opaque rectangle (or not at all)."""
    L = ['"Layer0"', "{", '    "Shader" "csgo_static_overlay.vfx"',
         '    "F_BLEND_MODE" "1"', '    "F_LIT" "1"',
         '    "g_vColorTint" "[1.000000 1.000000 1.000000 0.000000]"',
         f'    "TextureColor" "{texture_color_path}"']
    if mask_path:
        L += ['    "g_flOpacityScale" "1.000"', f'    "TextureTranslucency" "{mask_path}"']
    L += ['    "TextureNormal" "materials/default/default_normal.tga"',
          '    "TextureRoughness" "materials/default/default_rough.tga"',
          '    "TextureMetalness" "materials/default/default_metal.tga"', "}"]
    vmat = "\n".join(L)
    if original_vmt_text:
        vmat += "\n\n" + comment_original_vmt(original_vmt_text) + "\n"
    vmat_path.parent.mkdir(parents=True, exist_ok=True)
    vmat_path.write_text(vmat, encoding="utf-8")

def static_overlay_materials(vmap_paths) -> set:
    out = set()
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
            if b"encoding binary" not in data[:100]:
                continue
            els = read_binary_dmx(data)
        except Exception:
            continue
        for e in els:
            if e["type"] != "CMapStaticOverlay":
                continue
            md = e["attrs"].get("meshData")
            if md and isinstance(md[2], int) and 0 <= md[2] < len(els):
                mv = els[md[2]]["attrs"].get("materials")
                for mat in (mv[2] if mv else []):
                    out.add(mat.replace("\\", "/").lower())
    return out

def convert_one_vmt(export_dir: Path, vmt_abs: str, vmat_rel: str):
    vmt_path = Path(vmt_abs)
    try:
        data = parse_vmt(vmt_path)
    except Exception as e:
        return False, f"VMT parse failed: {vmt_path} :: {e}"
    if data["shader"] in UNSUPPORTED_SHADERS:
        return False, f"UNSUPPORTED_SHADER:{data['shader']} :: {vmt_path}"
    if not data["basetexture"] and "water" in vmat_rel.lower():
        return False, f"UNSUPPORTED_SHADER:water(by path, shader={data['shader']}) :: {vmt_path}"
    if not data["basetexture"]:
        return False, f"VMT has no $basetexture: {vmt_path}"

    vmat_out = export_dir / normalize_candidate(vmat_rel)
    is_overlay = normalize_candidate(vmat_rel).replace("\\", "/").lower() in OVERLAY_VMATS
    shader = data["shader"]
    shader_name = SHADER_MAP.get(shader, "csgo_vertexlitgeneric.vfx")
    # vertexlit has no additive option (a saved vmat dropped F_ADDITIVE_BLEND), so additive
    # materials go on the unlit shader, whose F_BLEND_MODE 4 = Additive is confirmed.
    if data["additive"] and shader_name != "csgo_unlitgeneric.vfx" and not (LIT_ADDITIVE_VIA_TRANSLUCENT and shader_name == "csgo_lightmappedgeneric.vfx"):
        shader = "unlitgeneric"
        shader_name = SHADER_MAP.get(shader, "csgo_unlitgeneric.vfx")
    is_unlit = shader_name == "csgo_unlitgeneric.vfx"
    env_is_cubemap = bool(data.get("envmap") and str(data["envmap"]).lower() == "env_cubemap") and not is_unlit
    cube_scalar = parse_envmaptint_scalar(data.get("envmaptint")) if env_is_cubemap else None

    additive_flag = data["additive"]
    if shader == "sprite" and SPRITE_DEFAULT_ADDITIVE and not (data["additive"] or data["translucent"] or data["alphatest"]):
        additive_flag = True
    common = dict(
        surfaceprop=data["surfaceprop"], alphatest=data["alphatest"], translucent=data["translucent"],
        additive=additive_flag, nocull=data["nocull"], selfillum=data["selfillum"], alphatestreference=data["alphatestreference"],
    )

    # ---- base colour texture
    try:
        color_rel, anim = export_color_texture(export_dir, data["basetexture"], True)
    except TextureMissing:
        # not in any Source1 root: maybe the texture already ships with CS2
        want = normalize_candidate("materials/" + strip_materials_prefix(data["basetexture"]) + ".vtf")
        p2, rel2 = try_resolve_cs2_texture_for_vtf(want)
        if p2:
            tex_ref = rel2[:-2] if rel2.lower().endswith(".vtex_c") else rel2   # never reference .vtex_c
            if is_overlay:
                generate_overlay_vmat(vmat_out, tex_ref, None, data["text"])
                return True, str(vmat_out)
            generate_vmat(vmat_out, shader, tex_ref, data["text"], specular_indirect=env_is_cubemap,
                          cubemap_scalar=cube_scalar, roughness_scalar=0.8 if env_is_cubemap else None, **common)
            return True, str(vmat_out)
        MISSING_TEXTURES.add(normalize_source1_vtf_dep(data["basetexture"]))
        MISSING_TEXTURE_USERS.setdefault(normalize_source1_vtf_dep(data["basetexture"]), set()).add(vmat_rel)
        return False, f"VTF missing for basetexture: {data['basetexture']}"
    except Exception as e:
        return False, f"VTF decode failed: {data['basetexture']} :: {e}"

    if is_overlay:
        mask_rel = None
        try:
            tw, th, trgba = vtf_extract_frames_rgba_cached(str(resolve_vtf(data["basetexture"])))[1][0]
            alpha = Image.frombytes("RGBA", (tw, th), trgba).getchannel(3)
            if alpha.getextrema()[0] < 255:
                mask_rel = _tex_base(data["basetexture"], "_trans") + ".tga"
                (export_dir / mask_rel).parent.mkdir(parents=True, exist_ok=True)
                alpha.convert("RGB").save(export_dir / mask_rel)
        except Exception:
            mask_rel = None
        resized = []
        for _rel in (color_rel, mask_rel):          # static overlays can fail to draw when a texture side is not a power of two
            if not _rel:
                continue
            try:
                _p = export_dir / _rel
                _im = Image.open(_p)
                _w, _h = _im.size
                _nw, _nh = (1 << (_w - 1).bit_length()), (1 << (_h - 1).bit_length())
                if (_nw, _nh) != (_w, _h):
                    _im.load()
                    _im.resize((_nw, _nh), Image.LANCZOS).save(_p)
                    resized.append(f"{_w}x{_h}->{_nw}x{_nh}")
            except Exception:
                pass
        generate_overlay_vmat(vmat_out, color_rel, mask_rel, data["text"])
        return True, str(vmat_out) + ("" if mask_rel else " (overlay: texture has no alpha, no mask written)") \
            + (f" (overlay textures resized to a power of two: {', '.join(resized)})" if resized else "")

    flipbook = (anim[0], anim[1], data["fps"], anim[2]) if anim else None

    # ---- second texture + scroll (UnlitTwoTexture)
    tex2_rel, tex2_scroll = None, None
    if is_unlit and data["texture2"] and shader == "unlittwotexture":
        try:
            tex2_rel, _ = export_color_texture(export_dir, data["texture2"], False)
            for var, rate, angle in data["scrolls"]:
                if var == "$texture2transform":
                    a = math.radians(angle)
                    tex2_scroll = (rate * math.cos(a), rate * math.sin(a))
        except TextureMissing:
            MISSING_TEXTURES.add(normalize_source1_vtf_dep(data["texture2"]))
            MISSING_TEXTURE_USERS.setdefault(normalize_source1_vtf_dep(data["texture2"]), set()).add(vmat_rel)
        except Exception:
            tex2_rel = None

    # ---- normal map
    normal_rel, normal_frame = None, None
    if data["normalmap"] and not is_unlit:
        normal_frame = _load_vtf_first_frame(data["normalmap"])
        if normal_frame is not None:
            try:
                nw, nh, nrgba = normal_frame
                normal_rel = _tex_base(data["normalmap"], "_normal") + ".tga"
                if data.get("ssbump"):
                    nrgba = ssbump_to_normal_rgba(nrgba, nw, nh)
                save_static(nrgba, nw, nh, export_dir / normal_rel)
                write_texture_settings_txt(export_dir / normal_rel, {"legacy_source1_inverted_normal": "1"})
            except Exception:
                normal_rel = None

    # ---- roughness: separate mask textures use RGB luminance, flagged alpha uses alpha
    rough_rel, roughness_scalar = None, None
    mask_frame, mask_ref, mask_channel = None, None, "L"
    if not is_unlit:
        if data["envmapmask"] or data["phongmask"]:
            mask_ref = data["envmapmask"] or data["phongmask"]
            mask_frame = _load_vtf_first_frame(mask_ref)
        elif data["normalmapalphaenvmapmask"] and normal_frame is not None:
            mask_ref, mask_frame, mask_channel = data["normalmap"], normal_frame, "A"
        elif data["basealphaenvmapmask"]:
            try:
                mask_frame = vtf_extract_frames_rgba_cached(str(resolve_vtf(data["basetexture"])))[1][0]
                mask_ref, mask_channel = data["basetexture"], "A"
            except Exception:
                mask_frame = None
    if mask_frame is not None and mask_ref:
        try:
            rw, rh, rrgba = mask_frame
            rough_rel = _tex_base(mask_ref, "_rough") + ".tga"
            save_static(_extract_mask_from_rgba(rrgba, rw, rh, mask_channel, True), rw, rh, export_dir / rough_rel)
        except Exception:
            rough_rel = None
    if env_is_cubemap and not rough_rel:
        roughness_scalar = 0.8

    # ---- detail texture
    detail_rel = detail_scale = detail_bf = detail_bm = None
    if data["detail"] and not is_unlit:
        dframe = _load_vtf_first_frame(data["detail"])
        if dframe is not None:
            try:
                dw, dh, drgba = dframe
                detail_rel = _tex_base(data["detail"], "_color") + ".tga"
                save_static(drgba, dw, dh, export_dir / detail_rel)
                detail_scale = _to_float(data["detailscale"], None) if data["detailscale"] is not None else None
                detail_bf = _to_float(data["detailblendfactor"], None) if data["detailblendfactor"] is not None else None
                v = _to_float(data["detailblendmode"], None) if data["detailblendmode"] is not None else None
                detail_bm = int(v) if v is not None else None
            except Exception:
                detail_rel = detail_scale = detail_bf = detail_bm = None

    # ---- separate translucency texture (lightmapped: TextureLayer1Translucency, others TextureTranslucency). Every shader ignores the
    #      colour texture's own alpha, so without this mask an alpha-tested / translucent material draws its background
    trans_rel = None
    if not data["additive"] and (data["translucent"] or data["alphatest"]) and not anim:     # unlit too: confirmed in game (cattails drew their background until the mask was set)
        try:
            tw, th, trgba = vtf_extract_frames_rgba_cached(str(resolve_vtf(data["basetexture"])))[1][0]
            alpha = Image.frombytes("RGBA", (tw, th), trgba).getchannel(3)
            if alpha.getextrema()[0] < 255:
                trans_rel = _tex_base(data["basetexture"], "_trans") + ".tga"
                (export_dir / trans_rel).parent.mkdir(parents=True, exist_ok=True)
                alpha.convert("RGB").save(export_dir / trans_rel)
        except Exception:
            trans_rel = None

    generate_vmat(
        vmat_out, shader, color_rel, data["text"], texture_translucency_path=trans_rel,
        texture_normal_path=normal_rel, texture_roughness_path=rough_rel,
        texture_detail_path=detail_rel, detail_scale=detail_scale,
        detail_blend_factor=detail_bf, detail_blend_mode=detail_bm,
        flipbook=flipbook, specular_indirect=env_is_cubemap, cubemap_scalar=cube_scalar,
        roughness_scalar=roughness_scalar, texture2_path=tex2_rel, tex2_scroll=tex2_scroll, **common,
    )
    return True, str(vmat_out)

SE_BEGIN = "\t// ===== BEGIN events generated by vmap_convert_merged (replaced on every run) ====="
SE_END = "\t// ===== END events generated by vmap_convert_merged ====="
SE_HEADER = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
             'format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->')

def _kv3_scan(text: str):
    """Brace/bracket balance of a KV3 text file, ignoring // and /* */ comments and "strings".
    -> (brace_depth_at_eof, bracket_depth_at_eof, index of the '}' that closes the root block or -1)"""
    depth = br = 0
    root_close = -1
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "<" and text.startswith("<!--", i):          # kv3 header line contains braces
            j = text.find("-->", i + 4)
            i = n if j < 0 else j + 3
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and root_close < 0:
                root_close = i
        elif c == "[":
            br += 1
        elif c == "]":
            br -= 1
        i += 1
    return depth, br, root_close

EVENT_TOKEN_RE = re.compile(rb'[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+')

def event_names_to_sounds(export_dir: Path, vmap_paths, sound_events: dict, loop_names: set, sound_report: list):
    """Entities already renamed to a path-derived event name ('mysounds.mh_blade_loop1') no longer contain the
    .vsnd path, so re-derive the sound from the name itself: dots -> folders, then look for
    sound/<path>.wav|mp3 in the Source1 roots. Same result as the first run, nothing stale is carried over.
    -> list of (event name, status) for the log"""
    known = {k.lower() for k in sound_events}
    index = load_soundscripts()
    seen, rows = set(), []
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
        except Exception:
            continue
        for m in EVENT_TOKEN_RE.finditer(data):
            tok = m.group(0).decode("ascii", errors="ignore")
            low = tok.lower()
            if low in seen or low in known or low in index:
                continue
            seen.add(low)
            name = tok[len(SOUNDEVENT_PREFIX):] if SOUNDEVENT_PREFIX and tok.startswith(SOUNDEVENT_PREFIX) else tok
            rest = name.replace(".", "/").lower()
            found = None
            for e in (".wav", ".mp3"):
                p, _r = find_in_source1("sound/" + rest + e)
                if p:
                    found = p
                    break
            if not found:
                continue
            dest_rel = "sounds/" + rest + os.path.splitext(found)[1].lower()
            place_sound(export_dir, found, dest_rel, sound_report)
            sound_events[tok] = "sounds/" + rest + ".vsnd"
            if is_looping_sound(found):
                loop_names.add(tok)
            rows.append((tok, f"re-derived from Source1 sound/{rest}{os.path.splitext(found)[1].lower()}"))
    return rows

def read_generated_events(export_dir: Path) -> dict:
    """Events from our own BEGIN/END block in the addon's soundevents file (previous runs).
    -> {name: ([vsnd files], volume text or None, had_base_line)}"""
    f = export_dir / "soundevents" / "soundevents_addon.vsndevts"
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    m = re.search(re.escape(SE_BEGIN.strip()) + r"(.*?)" + re.escape(SE_END.strip()), txt, flags=re.S)
    if not m:
        return {}
    out = {}
    for em in re.finditer(r'^[ \t]*"([^"]+)"[ \t]*=\s*\{(.*?)^[ \t]*\}', m.group(1), flags=re.S | re.M):
        body = em.group(2)
        files = re.findall(r'"(sounds/[^"]+\.vsnd)"', body)
        vol = re.search(r"volume\s*=\s*([0-9.]+)", body)
        if files:
            out[em.group(1)] = (files, vol.group(1) if vol else None, bool(re.search(r'^\s*base\s*=', body, flags=re.M)))
    return out

def sound_ranges(vmap_paths, sound_events: dict, vols: dict = None, levels: dict = None):
    """ambient_generic entities in the .vmap files -> {event name: audible radius}. radius keyvalue (default 1250);
    spawnflag 1 'Play everywhere' = SOUND_EVERYWHERE_RADIUS. An event used by several entities gets the largest."""
    by_key = {}
    for name, v in sound_events.items():
        by_key[name.lower()] = name
        for f in (v[0] if isinstance(v, tuple) else [v]):
            by_key.setdefault(f.lower(), name)
    out = {}
    for vp in vmap_paths:
        ents, _err = vmap_entities(str(vp))
        for e in ents:
            if e["cls"].lower() != "ambient_generic":
                continue
            p = {k.lower(): v for k, v in e["props"].items()}
            msg = (p.get("message") or "").strip().replace("\\", "/").lstrip(_WAVE_PREFIX_CHARS).lower()
            if not msg:
                continue
            name = by_key.get(msg)
            if not name:
                base, ext = os.path.splitext(msg)
                if ext in (".vsnd", ".wav", ".mp3"):
                    key = (base if base.startswith("sounds/") else "sounds/" + base.removeprefix("sound/")) + ".vsnd"
                    name = by_key.get(key)
            if not name:
                continue
            try:
                flags = int(float(p.get("spawnflags", "0") or 0))
            except ValueError:
                flags = 0
            if os.path.splitext(msg)[1] not in (".wav", ".mp3", ".vsnd") and levels is not None:
                # message is a soundscript name: Source1 ignores the entity's radius and Volume ("soundscripts ignore this
                # entity's settings"); the script's own soundlevel and volume apply
                load_soundscripts()
                if not (flags & 1):
                    levels[name] = max(levels.get(name, 0), _SS_LEVEL.get(msg, 75))
                continue
            try:
                _rv = str(p.get("radius", "")).strip()
                rad = SOUND_DEFAULT_RADIUS if _rv == "" else float(_rv)
                if rad <= 0:
                    rad = SOUND_EVERYWHERE_RADIUS      # Source1: radius 0 -> no attenuation (plays everywhere)
            except ValueError:
                rad = SOUND_DEFAULT_RADIUS
            if flags & 1:
                rad = SOUND_EVERYWHERE_RADIUS
            if rad < SOUND_EVERYWHERE_RADIUS:
                rad = max(rad * SOUND_RANGE_SCALE, SOUND_RANGE_MIN)
            out[name] = max(out.get(name, 0.0), rad)
            if vols is not None:        # Source1 ambient_generic: the "Volume (0-10)" keyvalue is stored as health; volume = health / 10 (default 10)
                try:
                    _hv = str(p.get("health", "")).strip()
                    vols[name] = max(vols.get(name, 0.0), min(max(float(_hv), 0.0), 10.0) / 10.0 if _hv != "" else 1.0)
                except ValueError:
                    vols[name] = max(vols.get(name, 0.0), 1.0)
    return out

def _source1_soundlevel(radius: float) -> int:
    """ambient_generic (Source SDK sound.cpp, ComputeSoundlevel): dB = 40 + 20*log10(radius / 36), truncated to an int."""
    return int(40 + 20 * math.log10(radius / 36.0))

def _source1_gain_level(level: int, dist: float) -> float:
    """Gain at a distance for a sound of this sound level in dB (engine: snd_refdb 60, snd_refdist 36)."""
    dist_mult = (10 ** ((60 - level) / 20.0)) / 36.0
    rel = dist * dist_mult
    gain = 1.0 if rel <= 1.0 else 1.0 / rel
    gmin = SOUND_SOURCE1_GAIN_MIN
    if gain < gmin:
        gain = max(0.0, gmin * (2.0 - rel * gmin))
    return gain

def _source1_curve_points_level(level: int):
    """(distance, gain) points for a sound level; the distance unit is the point where gain starts to fall (36 x 10^((dB-60)/20))."""
    d1 = 36.0 * 10 ** ((level - 60) / 20.0)
    mult = [0.0, 1.0, 2.0, 3.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0]
    pts = [(m * d1, _source1_gain_level(level, m * d1)) for m in mult]
    pts.append((200.0 * d1, 0.0))
    return pts

def _source1_gain(radius: float, dist: float) -> float:
    """Gain Source1 gives an ambient_generic with this radius at this distance (engine: snd_refdb 60, snd_refdist 36):
    dist_mult = 10^((60 - dB)/20) / 36; relative = dist * dist_mult; gain = 1/relative (never above 1);
    below snd_gain_min the gain drops to 0 over the same distance again."""
    dist_mult = (10 ** ((60 - _source1_soundlevel(radius)) / 20.0)) / 36.0
    rel = dist * dist_mult
    gain = 1.0 if rel <= 1.0 else 1.0 / rel
    gmin = SOUND_SOURCE1_GAIN_MIN
    if gain < gmin:
        gain = max(0.0, gmin * (2.0 - rel * gmin))
    return gain

def _source1_curve_points(radius: float):
    """(distance, gain) points following the Source1 model out to where it reaches 0 (~20 x radius)."""
    mult = [0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 7.5, 10.0]
    pts = [(m * radius, _source1_gain(radius, m * radius)) for m in mult]
    pts.append((20.0 * radius, 0.0))
    return pts

def _curve_points_lines(prop: str, pts):
    out = [f'\t\t{prop} = ', '\t\t[']
    for x, y in pts:
        out += ['\t\t\t[', f'\t\t\t\t{x:.1f}, {y:.4f}, 0.0, 0.0,', '\t\t\t\t2.0, 3.0,', '\t\t\t],']
    out.append('\t\t]')
    return out

def _curve_lines(prop: str, radius: float, end_vol: float):
    return [f'\t\t{prop} = ', '\t\t[',
            '\t\t\t[', '\t\t\t\t0.0, 1.0, 0.0, 0.0,', '\t\t\t\t2.0, 3.0,', '\t\t\t],',
            '\t\t\t[', f'\t\t\t\t{radius:.1f}, {end_vol:.1f}, 0.0, 0.0,', '\t\t\t\t2.0, 3.0,', '\t\t\t],',
            '\t\t]']

def write_soundevents(export_dir: Path, events: dict, log_copy: str, loop_names=(), ranges=None, vols=None, levels=None):
    """Merge one csgo_mega soundevent per converted sound into soundevents/soundevents_addon.vsndevts
    (Valve's addon template layout: quoted names, vsnd_files_track_01). An existing file is kept; our
    block between the BEGIN/END markers is replaced on each run. Names already defined elsewhere in the
    file are skipped. The block goes just before the root block's closing brace; if the file has no
    closing brace it is added, and if the braces are otherwise unbalanced nothing is written.
    Looping needs cue markers in the .wav."""
    dest = export_dir / "soundevents" / "soundevents_addon.vsndevts"
    old = dest.read_text(encoding="utf-8-sig", errors="replace") if dest.exists() else None   # -sig: drop a BOM
    if old is not None:
        # The engine only treats the file as KeyValues3 when '<!-- kv3 ...' is the very first thing in it. A BOM, blank
        # lines before it, CRLF line endings or a different/duplicated header make it fall back to the old KeyValues
        # parser ("is not a KeyValues3 file and will be converted" + "expected '{', found '='" errors).
        old = old.replace("\r\n", "\n").replace("\r", "\n")
        old = re.sub(r"[ \t]*<!--\s*kv3[^>]*?-->[ \t]*\n?", "", old, flags=re.I)
        old = SE_HEADER + "\n" + old.lstrip()
    existing = set()
    if old is not None:
        outside = re.sub(re.escape(SE_BEGIN.strip()) + r".*?" + re.escape(SE_END.strip()), "", old, flags=re.S)
        existing = set(re.findall(r'^\s*"([^"]+)"\s*=', outside, flags=re.M))
    block, skipped = [SE_BEGIN], []
    use_base = bool(LOOP_EVENT_BASE) and LOOP_EVENT_BASE in existing   # an undefined base makes the whole file fail to compile
    if LOOP_EVENT_BASE and not use_base and loop_names:
        print(f'NOTE: "{LOOP_EVENT_BASE}" is not defined in {dest.name}; looping events keep type csgo_mega without a base. '
              f'Copy that event from Valve\'s template soundevents file into yours to use it.')
    for name, vsnd in sorted(events.items()):
        if name in existing:
            skipped.append(name)
            continue
        files, vol = (vsnd if isinstance(vsnd, tuple) else ([vsnd], None))
        try:
            _v = float(vol) if vol is not None and 0 < float(vol) <= 1 else 1.0
        except ValueError:
            _v = 1.0
        if vols and name in vols:                  # ambient_generic Volume (health / 10); several entities on one event: the loudest
            _v *= vols[name]
        vol_txt = f"{_v:.3f}" if _v != 1.0 else "1.0"
        if use_base and name in loop_names:
            block += [f'\t"{name}" =', '\t{', f'\t\tbase = "{LOOP_EVENT_BASE}"', f'\t\tvolume = {vol_txt}']
        else:
            block += [f'\t"{name}" =', '\t{', '\t\ttype = "csgo_mega"', f'\t\tvolume = {vol_txt}', '\t\tpitch = 1.0', '\t\tmixgroup = "Amb_Common"']
        if len(files) == 1:
            block.append(f'\t\tvsnd_files_track_01 = "{files[0]}"')
        else:
            block += ['\t\tvsnd_files_track_01 = ', '\t\t[']
            block += [f'\t\t\t"{f}",' for f in files]
            block.append('\t\t]')
        if SOUND_RANGE_CURVES and SOUND_SOURCE1_FALLOFF and levels and name in levels and not (ranges and name in ranges):
            block += _curve_points_lines("distance_volume_mapping_curve", _source1_curve_points_level(levels[name]))
        elif SOUND_RANGE_CURVES and ranges and name in ranges:
            if SOUND_SOURCE1_FALLOFF and ranges[name] < SOUND_EVERYWHERE_RADIUS and ranges[name] > 0:
                block += _curve_points_lines("distance_volume_mapping_curve", _source1_curve_points(ranges[name]))
            else:
                block += _curve_lines("distance_volume_mapping_curve", ranges[name], 1.0 if ranges[name] >= SOUND_EVERYWHERE_RADIUS else 0.0)
            block += _curve_lines("distance_unfiltered_stereo_mapping_curve", ranges[name], 1.0 if ranges[name] >= SOUND_EVERYWHERE_RADIUS else 0.0)
        block += ['\t}', '']
    block.append(SE_END)
    block_text = "\n".join(block) + "\n"
    Path(log_copy).write_text(block_text, encoding="utf-8")
    if old is None:
        text = SE_HEADER + "\n{\n\n" + block_text + "\n}\n"
    else:
        cleaned = re.sub(r"[ \t]*" + re.escape(SE_BEGIN.strip()) + r".*?" + re.escape(SE_END.strip()) + r"\n?",
                         "", old, flags=re.S)
        depth, br, root_close = _kv3_scan(cleaned)
        if br != 0 or depth not in (0, 1):
            print(f"WARNING: {dest} has unbalanced braces/brackets (braces {depth:+d}, brackets {br:+d}); "
                  f"left untouched. Fix it, or paste the events from {log_copy}.")
            return
        if depth == 1:       # root block never closed: append our block and the missing '}'
            print(f"NOTE: {dest} had no closing '}}' for the root block; added one.")
            text = cleaned.rstrip() + "\n\n" + block_text + "\n}\n"
        else:
            text = cleaned[:root_close].rstrip() + "\n\n" + block_text + "\n" + cleaned[root_close:]
        d2, b2, _ = _kv3_scan(text)
        if d2 != 0 or b2 != 0:
            print(f"WARNING: merged {dest.name} would be unbalanced ({d2:+d}, {b2:+d}); not written. Events are in {log_copy}")
            return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(text.encode("utf-8"))                       # no BOM, LF endings
    if not dest.read_bytes().startswith(b"<!-- kv3 "):
        print(f"WARNING: {dest} does not start with the KV3 header; the tools will not compile it.")
    print(f"Soundevents: {len(events) - len(skipped)} written to {dest}"
          + (f" ({len(skipped)} already defined elsewhere, skipped)" if skipped else ""))

def normalize_source1_vtf_dep(ref: str) -> str:
    return normalize_candidate("materials/" + strip_materials_prefix(ref) + ".vtf")

# ----- explicit .vtex requests (kept from the old pipeline)

def write_simple_vtex(vtex_path: Path, input_image_rel: str, output_format: str = "DXT5", linear: bool = False):
    input_image_rel = input_image_rel.replace("\\", "/")
    cs = "linear" if linear else "srgb"
    dmx = (
        '<!-- dmx encoding keyvalues2_noids 1 format vtex 1 -->\n"CDmeVtex"\n{\n'
        '    "m_inputTextureArray" "element_array"\n    [\n        "CDmeInputTexture"\n        {\n'
        '            "m_name" "string" "0"\n'
        f'            "m_fileName" "string" "{input_image_rel}"\n'
        f'            "m_colorSpace" "string" "{cs}"\n            "m_typeString" "string" "2D"\n'
        '            "m_imageProcessorArray" "element_array"\n            [\n            ]\n        }\n    ]\n'
        '    "m_outputTypeString" "string" "2D"\n'
        f'    "m_outputFormat" "string" "{output_format}"\n'
        '    "m_textureOutputChannelArray" "element_array"\n    [\n        "CDmeTextureOutputChannel"\n        {\n'
        '            "m_inputTextureArray" "string_array" [ "0" ]\n'
        '            "m_srcChannels" "string" "rgba"\n            "m_dstChannels" "string" "rgba"\n'
        '            "m_mipAlgorithm" "CDmeImageProcessor"\n            {\n'
        '                "m_algorithm" "string" ""\n                "m_stringArg" "string" ""\n'
        '                "m_vFloat4Arg" "vector4" "0 0 0 0"\n            }\n'
        f'            "m_outputColorSpace" "string" "{cs}"\n'
        '        }\n    ]\n}\n'
    )
    vtex_path.parent.mkdir(parents=True, exist_ok=True)
    vtex_path.write_text(dmx, encoding="utf-8")

def convert_one_vtf_to_vtex(export_dir: Path, vtf_abs: str, vtex_rel: str):
    vtex_rel = normalize_candidate(vtex_rel)
    out_vtex_path = export_dir / vtex_rel
    stem = Path(vtex_rel).stem
    out_img_path = out_vtex_path.parent / f"{stem}{ANIM_EXT}"
    out_img_rel = normalize_candidate(str(Path(vtex_rel).parent).replace("\\", "/") + f"/{stem}{ANIM_EXT}")
    try:
        _hdr, frames = vtf_extract_frames_rgba_cached(vtf_abs)
    except Exception as e:
        return False, f"VTF decode failed: {vtf_abs} :: {e}"
    if len(frames) > 1:
        w, h, _ = frames[0]
        build_atlas([r for (_, _, r) in frames], w, h, out_img_path)
    else:
        w, h, rgba = frames[0]
        save_png(rgba, w, h, out_img_path)
    write_simple_vtex(out_vtex_path, out_img_rel)
    return True, str(out_vtex_path)

# ======================================================================== SOUND HELPERS

def wav_loop_info(path: str):
    """-> (cue_points, smpl_loops) read from a RIFF/WAVE file; (0, 0) if none or not a wav."""
    cues = loops = 0
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return 0, 0
            f.read(4)
            if f.read(4) != b"WAVE":
                return 0, 0
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
                body = f.read(min(size, 64)) if cid in (b"cue ", b"smpl") else b""
                if cid == b"cue " and len(body) >= 4:
                    cues = struct.unpack("<I", body[:4])[0]
                elif cid == b"smpl" and len(body) >= 36:
                    loops = struct.unpack("<I", body[28:32])[0]
                f.seek(size - len(body) + (size & 1), 1)
    except Exception:
        pass
    return cues, loops

def is_looping_sound(path: str) -> bool:
    low = os.path.basename(path).lower()
    if any(h in low for h in LOOP_NAME_HINTS):
        return True
    c, l = wav_loop_info(path)
    return (c + l) > 0

def wav_format_info(path: str):
    """-> (format tag, channels, sample rate, bits) from the fmt chunk, or None if not a readable RIFF/WAVE.
    tag 1 = PCM, 2 = MS ADPCM, 3 = float, 17 = IMA ADPCM, 0xFFFE = extensible."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return None
            f.read(4)
            if f.read(4) != b"WAVE":
                return None
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
                if cid == b"fmt " and size >= 16:
                    tag, ch, rate, _br, _ba, bits = struct.unpack("<HHIIHH", f.read(16))
                    if tag == 0xFFFE and size >= 26:
                        f.read(8)
                        tag = struct.unpack("<H", f.read(2))[0]
                    return tag, ch, rate, bits
                f.seek(size + (size & 1), 1)
    except Exception:
        return None

def _wav_raw_chunks(path: str, wanted=(b"cue ", b"smpl")):
    out = []
    with open(path, "rb") as f:
        f.read(12)
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            body = f.read(size)
            if size & 1:
                f.read(1)
            if cid in wanted:
                out.append(hdr + body + (b"\0" if size & 1 else b""))
    return out

def _wav_pcm16_pure(src: str, info) -> bytes:
    """8/24/32-bit integer PCM and 32/64-bit float -> 16-bit PCM bytes, no third-party modules (ADPCM still needs miniaudio)."""
    import array
    tag, _ch, _rate, bits = info
    data = None
    with open(src, "rb") as f:
        f.read(12)
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"data":
                data = f.read(size)
                break
            f.seek(size + (size & 1), 1)
    if data is None:
        raise ValueError("no data chunk")
    if tag == 1 and bits == 8:
        out = array.array("h", [(b - 128) << 8 for b in data])
    elif tag == 1 and bits == 24:
        n = len(data) // 3
        out = array.array("h", [struct.unpack_from("<h", data, 3 * i + 1)[0] for i in range(n)])
    elif tag == 1 and bits == 32:
        n = len(data) // 4
        out = array.array("h", [struct.unpack_from("<h", data, 4 * i + 2)[0] for i in range(n)])
    elif tag == 3 and bits in (32, 64):
        fl = array.array("f" if bits == 32 else "d")
        fl.frombytes(data[: len(data) // (bits // 8) * (bits // 8)])
        out = array.array("h", [max(-32768, min(32767, int(round(v * 32767.0)))) for v in fl])
    else:
        raise ValueError(f"cannot convert tag {tag} / {bits}-bit without miniaudio")
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()

def wav_to_pcm16(src: str, dst: str, info):
    import wave
    _tag, ch, rate, _bits = info
    try:
        import miniaudio
        dec = miniaudio.decode_file(src, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=ch, sample_rate=rate)
        pcm, n_ch, sr = dec.samples.tobytes(), dec.nchannels, dec.sample_rate
    except ImportError:
        pcm, n_ch, sr = _wav_pcm16_pure(src, info), ch, rate     # miniaudio not installed: 8/24/32-bit and float still convert
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(dst, "wb") as w:
        w.setnchannels(n_ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    extra = _wav_raw_chunks(src)
    if extra:
        with open(dst, "r+b") as f:
            f.seek(0, 2)
            for c in extra:
                f.write(c)
            end = f.tell()
            f.seek(4)
            f.write(struct.pack("<I", end - 8))

def mp3_to_wav(src: str, dst: str, add_cue: bool):
    import miniaudio, wave
    dec = miniaudio.decode_file(src, output_format=miniaudio.SampleFormat.SIGNED16)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(dst, "wb") as w:
        w.setnchannels(dec.nchannels)
        w.setsampwidth(2)
        w.setframerate(dec.sample_rate)
        w.writeframes(dec.samples.tobytes())
    if add_cue:   # one cue point at sample 0, appended after the data chunk
        cue = struct.pack("<4sII", b"cue ", 4 + 24, 1) + struct.pack("<II4sIII", 1, 0, b"data", 0, 0, 0)
        with open(dst, "r+b") as f:
            f.seek(0, 2)
            f.write(cue)
            end = f.tell()
            f.seek(4)
            f.write(struct.pack("<I", end - 8))

def place_sound(export_dir: Path, found: str, dest_rel: str, report: list):
    """Copy (or convert) one Source1 sound. -> (final dest_rel, copied_new: bool)"""
    ext = os.path.splitext(found)[1].lower()
    looping = is_looping_sound(found)
    cues, loops = wav_loop_info(found) if ext == ".wav" else (0, 0)
    action = "copied"
    final_rel = dest_rel
    if ext == ".mp3" and ((looping and CONVERT_LOOP_MP3_TO_WAV) or CONVERT_ALL_MP3_TO_WAV):
        final_rel = os.path.splitext(dest_rel)[0] + ".wav"
        dst = export_dir / rel_norm(final_rel)
        if dst.exists():
            action = "converted (already present)"
        else:
            try:
                mp3_to_wav(found, str(dst), looping and ADD_LOOP_CUE_TO_CONVERTED)
                action = "mp3 -> wav" + (" + cue@0" if looping and ADD_LOOP_CUE_TO_CONVERTED else "")
            except Exception as e:
                final_rel, action = dest_rel, f"copied as mp3 (convert failed: {e}; pip install miniaudio?)"
    note = ""
    finfo = wav_format_info(found) if ext == ".wav" else None
    if finfo:
        note = f"fmt={finfo[0]} ch={finfo[1]} {finfo[2]}Hz {finfo[3]}bit; "
        if CONVERT_NONPCM_WAV and (finfo[0] != 1 or finfo[3] != 16):
            try:
                wav_to_pcm16(found, str(export_dir / rel_norm(final_rel)), finfo)
                report.append(f"{final_rel}\tsrc={os.path.basename(found)}\tloop={'yes' if looping else 'no'}\tcues={cues}\tsmpl_loops={loops}\twav -> pcm16\t{note}")
                return final_rel, True
            except Exception as e:
                note += f"pcm16 convert failed: {e}; "
    if looping and ext == ".mp3" and final_rel.endswith(".mp3"):
        note += "LOOP SOUND IS MP3: will play once"
    elif looping and ext == ".wav" and not (cues or loops):
        note += "looping by name but wav has no cue/loop points"
    report.append(f"{final_rel}\tsrc={os.path.basename(found)}\tloop={'yes' if looping else 'no'}\tcues={cues}\tsmpl_loops={loops}\t{action}\t{note}")
    if final_rel == dest_rel:
        return final_rel, copy_asset_to_export(export_dir, found, final_rel)
    return final_rel, action.startswith("mp3")

# ======================================================================== SOURCE1 SOUNDSCRIPTS

SOUNDSCRIPT_NAME_RE = re.compile(rb'[A-Za-z][A-Za-z0-9_]{0,63}(?:\.[A-Za-z0-9_]{1,63})+')
_WAVE_PREFIX_CHARS = "*#@><^)(}$!?~`"

_SS_LEVEL = {}      # lower soundscript name -> its soundlevel in dB (filled by parse_soundscript_text)
_SNDLVL_NAMES = {"SNDLVL_NONE": 0, "SNDLVL_IDLE": 60, "SNDLVL_TALKING": 60, "SNDLVL_STATIC": 66, "SNDLVL_NORM": 75, "SNDLVL_GUNFIRE": 140}

def _parse_sndlvl(txt: str):
    t = txt.strip().strip('"').upper()
    if t in _SNDLVL_NAMES:
        return _SNDLVL_NAMES[t]
    m = re.fullmatch(r"SNDLVL_(\d+)DB", t)
    if m:
        return int(m.group(1))
    try:
        return int(float(t))
    except ValueError:
        return None

def parse_soundscript_text(text: str) -> dict:
    """game_sounds*.txt -> {lower name: (Name, [wave paths], volume or None)} (all waves, incl. rndwave).
    Also records each script's soundlevel in _SS_LEVEL."""
    toks = KV_TOK_RE.findall(_strip_comments_keep_quotes(text))
    out, i, n = {}, 0, len(toks)
    while i < n:
        t = toks[i]
        if i + 1 < n and toks[i + 1] == "{" and t not in ("{", "}"):
            name = t.strip('"')
            depth, j, waves, vol = 1, i + 2, [], None
            while j < n and depth > 0:
                x = toks[j]
                if x == "{":
                    depth += 1
                elif x == "}":
                    depth -= 1
                else:
                    k = x.strip('"').lower()
                    if j + 1 < n and toks[j + 1] not in ("{", "}"):
                        if k == "wave":
                            waves.append(toks[j + 1].strip('"'))
                            j += 1
                        elif k == "volume" and vol is None:
                            vol = toks[j + 1].strip('"')
                            j += 1
                        elif k == "soundlevel" and name.lower() not in _SS_LEVEL:
                            _lv = _parse_sndlvl(toks[j + 1])
                            if _lv is not None:
                                _SS_LEVEL[name.lower()] = _lv
                            j += 1
                j += 1
            out[name.lower()] = (name, waves, vol)
            i = j
        else:
            i += 1
    return out

@lru_cache(maxsize=1)
def load_soundscripts() -> dict:
    """Index of every soundscript in the Source1 roots (map root first; first definition wins).
    -> {lower name: (Name, [waves], volume, defining file)}"""
    index = {}
    for root in SOURCE1_ROOTS:
        sdir = None
        for cand in ("scripts",):
            q = find_ci(Path(root), cand)
            if q is not None and q.is_dir():
                sdir = q
        if sdir is None:
            continue
        try:
            files = sorted(f for f in os.listdir(sdir)
                           if f.lower().endswith(".txt") and (f.lower().startswith("game_sounds")
                                                              or f.lower().startswith("npc_sounds")))
        except Exception:
            continue
        for f in files:
            try:
                parsed = parse_soundscript_text(Path(sdir, f).read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for k, (nm, waves, vol) in parsed.items():
                index.setdefault(k, (nm, waves, vol, f"{os.path.basename(root)}/scripts/{f}"))
    return index

@lru_cache(maxsize=1)
def _cs2_soundevent_blob() -> bytes:
    """Lower-cased bytes of the CS2 install's soundevents (VPK entries and/or extracted files), for a
    best-effort name search; compressed files will not match."""
    parts = []
    v = cs2_vpk()
    for rel in sorted(v.entries):
        if rel.endswith((".vsndevts_c", ".vsndevts")):
            data = v.read(rel)
            if data:
                parts.append(data.lower())
    for root in (CS2_VERIFY_FOLDER, *CS2_EXTRA_VERIFY_FOLDERS):
        folder = os.path.join(root, "soundevents")
        try:
            for f in os.listdir(folder):
                if f.lower().endswith((".vsndevts_c", ".vsndevts")):
                    parts.append(Path(folder, f).read_bytes().lower())
        except Exception:
            pass
    return b"\n".join(parts)

def vmap_soundscript_names(vmap_paths) -> dict:
    """Soundscript names found as plain strings in the .vmap files -> {lower name: Name as defined}."""
    index = load_soundscripts()
    found = {}
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
        except Exception:
            continue
        for m in SOUNDSCRIPT_NAME_RE.finditer(data):
            key = m.group(0).decode("ascii", errors="ignore").lower()
            if key in index:
                found[key] = index[key][0]
    return found

def convert_soundscripts(export_dir: Path, vmap_paths, sound_events: dict, loop_names: set, sound_report: list):
    """Port the CS:GO/Source1 soundscripts the map refers to: copy the waves and define a soundevent with
    the SAME name. -> report lines."""
    index = load_soundscripts()
    names = vmap_soundscript_names(vmap_paths)
    cs2_blob = _cs2_soundevent_blob()
    per_file = {}
    for _k, (_nm, _w, _v, _src) in index.items():
        per_file[_src] = per_file.get(_src, 0) + 1
    lines = [f"# {len(index)} soundscripts indexed from {len(per_file)} script file(s); "
             f"{len(names)} of them are used by the map"]
    if not index:
        lines.append("# NO scripts/game_sounds*.txt (or npc_sounds*.txt) found under any Source1 root, so nothing can be "
                     "resolved. Extract the game's scripts folder (it is inside pak01_dir.vpk) next to the sounds.")
    lines += [f"#   {src}: {c}" for src, c in sorted(per_file.items())]
    for key in sorted(names):
        nm, waves, vol, src = index[key]
        if cs2_blob and nm.lower().encode() in cs2_blob:
            lines.append(f"{nm}\talready defined in the CS2 install (best-effort check); skipped\t{src}")
            continue
        vsnds, missing, from_cs2 = [], [], []
        for w in waves:
            rel = w.lstrip(_WAVE_PREFIX_CHARS).replace("\\", "/").lstrip("/")
            if not rel:
                continue
            base, ext = os.path.splitext(rel)
            vsnd = ("sounds/" + base + ".vsnd").lower()
            if SOUND_PREFER_CS2_FILES and cs2_file_exists(vsnd + "_c"):
                vsnds.append(vsnd)
                from_cs2.append(rel)
                continue
            p, _r = find_in_source1("sound/" + rel)
            if not p and not ext:
                for e in (".wav", ".mp3"):
                    p, _r = find_in_source1("sound/" + rel + e)
                    if p:
                        break
            if not p:
                missing.append(rel)
                continue
            dest_rel = "sounds/" + base.lower() + os.path.splitext(p)[1].lower()
            place_sound(export_dir, p, dest_rel, sound_report)
            vsnds.append(vsnd)
            if is_looping_sound(p):
                loop_names.add(nm)
        vsnds = list(dict.fromkeys(vsnds))
        if vsnds:
            sound_events[nm] = (vsnds, vol)
        status = "ok" if vsnds and not missing else ("PARTIAL" if vsnds else "NO WAVES FOUND")
        lines.append(f"{nm}\t{status}\twaves={len(waves)} found={len(vsnds)}\tdefined in {src}"
                     + (f"\tUSING CS2's OWN FILE (not copied): {'; '.join(from_cs2)}" if from_cs2 else "")
                     + (f"\tMISSING: {'; '.join(missing)}" if missing else ""))
    # strings that look like soundscript names but are not defined in any script file found
    ext_skip = {"vmat", "vsnd", "vmdl", "vpcf", "vtex", "tga", "png", "jpg", "wav", "mp3", "dmx", "vmap", "vmt", "vtf", "txt", "vdata"}
    cand = set()
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
        except Exception:
            continue
        for m in SOUNDSCRIPT_NAME_RE.finditer(data):
            t = m.group(0).decode("ascii", errors="ignore")
            last = t.rsplit(".", 1)[-1]
            if t.lower() in index or last.lower() in ext_skip or not t[0].isupper() or len(last) < 4:
                continue
            cand.add(t)
    if cand:
        lines.append("# UNRESOLVED: look like soundscript names but are not defined in any script file found:")
        lines += [f"{t}\tNOT DEFINED" for t in sorted(cand)]
    return lines

VPCF_HEADER = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
               'format:vpcf61:version{39d3ce1d-9442-4407-9b78-8317145b1732} -->\n')


def _vpcf_spawn_initializers(life: float) -> str:
    """m_Initializers block: spawn at control point 0, fixed lifetime. CS2's particle editor drops the old C_INIT_CreateWithinSphere /
    C_INIT_RandomLifeTime (a trail saved in the editor came back with no initializers and drew nothing); these are the classes the
    steam particles use, which work."""
    ind = "\t\t"
    lines = ["\tm_Initializers =", "\t[", ind + "{", ind + '\t_class = "C_INIT_CreateWithinSphereTransform"', ind + "},"]
    lines += _init_float_lines(ind, life, life, 1)
    lines.append("\t]")
    return "\n".join(lines) + "\n"


def make_sprite_particle(export_dir: Path, vpcf_rel: str):
    """particles/<dir>/<name>.vpcf has no Source1 equivalent, but Source1 sprite VMT materials/sprites/<name>.vmt does
    (the importer turns env_sprite into a particle named after the sprite). Build a simple additive sprite particle from
    that sprite's texture. Header/texture layout follows a Hammer-saved vpcf61 file; operators follow skink52's glowfix.py. -> (ok, message)"""
    stem = os.path.splitext(os.path.basename(vpcf_rel))[0]
    vmt, _root = find_in_source1(f"materials/sprites/{stem}.vmt")
    if not vmt:
        return False, f"no Source1 materials/sprites/{stem}.vmt to build a sprite particle from"
    try:
        data = parse_vmt(Path(vmt))
        if not data["basetexture"]:
            return False, f"{vmt} has no $basetexture"
        color_rel, _anim = export_color_texture(export_dir, data["basetexture"], False)
        vtex_rel = os.path.splitext(color_rel)[0] + "_particle.vtex"   # own name: the .tga already feeds the vmat
        write_simple_vtex(export_dir / vtex_rel, color_rel)
    except Exception as e:
        return False, f"sprite texture export failed: {e}"
    body = VPCF66_HEADER + (
        "{\n\t_class = \"CParticleSystemDefinition\"\n\tm_nBehaviorVersion = 12\n\tm_nMaxParticles = 16\n"
        "\tm_ConstantColor = [ 255, 255, 255, 255 ]\n"
        f"\tm_flConstantRadius = {SPRITE_PARTICLE_RADIUS}\n"
        "\tm_Renderers =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_RenderSprites\"\n"
        "\t\t\tm_nOutputBlendMode = \"PARTICLE_OUTPUT_BLEND_MODE_ADD\"\n"
        "\t\t\tm_vecTexturesInput =\n\t\t\t[\n\t\t\t\t{\n"
        f"\t\t\t\t\tm_hTexture = resource:\"{vtex_rel}\"\n" "\t\t\t\t},\n\t\t\t]\n\t\t},\n\t]\n"
        "\tm_Emitters =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_ContinuousEmitter\"\n\t\t\tm_flEmitRate = 40.0\n\t\t},\n\t]\n"
        + _vpcf_spawn_initializers(0.15) +
        "\tm_Operators =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_Decay\"\n\t\t},\n\t\t{\n\t\t\t_class = \"C_OP_PositionLock\"\n\t\t},\n"
        "\t\t{\n\t\t\t_class = \"C_OP_FadeInSimple\"\n\t\t\tm_flFadeInTime = 0.3\n\t\t},\n"
        "\t\t{\n\t\t\t_class = \"C_OP_FadeOutSimple\"\n\t\t\tm_flFadeOutTime = 0.3\n\t\t},\n\t]\n}\n")
    out = export_dir / normalize_candidate(vpcf_rel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return True, f"generated from {vmt} -> {vtex_rel}"

# ---- vpcf66 value structs, copied field-for-field from what the CS2 particle editor saves ----
VPCF66_HEADER = ('<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} '
                 'format:vpcf66:version{dd571be4-586e-4f41-8b2d-5bbc969e1c53} -->\n')

def _kv_f(v) -> str:
    return f"{float(v):.4f}".rstrip("0").rstrip(".") if float(v) != int(float(v)) else f"{float(v):.1f}"

def _pf_lines(ind: str, ptype: str = "PF_TYPE_LITERAL", **ov) -> list:
    """A CPerParticleFloatInput struct (editor's full field list). ov: field name -> value."""
    d = [("m_nType", f'"{ptype}"'), ("m_nMapType", '"PF_MAP_TYPE_DIRECT"'), ("m_flLiteralValue", "0.0"),
         ("m_NamedValue", '""'), ("m_nControlPoint", "0"), ("m_nScalarAttribute", "3"), ("m_nVectorAttribute", "6"),
         ("m_nVectorComponent", "0"), ("m_bReverseOrder", "false"), ("m_flRandomMin", "0.0"), ("m_flRandomMax", "1.0"),
         ("m_bHasRandomSignFlip", "false"), ("m_nRandomSeed", "-1"), ("m_nRandomMode", '"PF_RANDOM_MODE_CONSTANT"'),
         ("m_strSnapshotSubset", '""'), ("m_flLOD0", "0.0"), ("m_flLOD1", "0.0"), ("m_flLOD2", "0.0"), ("m_flLOD3", "0.0"),
         ("m_nNoiseInputVectorAttribute", "0"), ("m_flNoiseOutputMin", "0.0"), ("m_flNoiseOutputMax", "1.0"),
         ("m_flNoiseScale", "0.1"), ("m_vecNoiseOffsetRate", "[ 0.0, 0.0, 0.0 ]"), ("m_flNoiseOffset", "0.0"),
         ("m_nNoiseOctaves", "1"), ("m_nNoiseTurbulence", '"PF_NOISE_TURB_NONE"'), ("m_nNoiseType", '"PF_NOISE_TYPE_PERLIN"'),
         ("m_nNoiseModifier", '"PF_NOISE_MODIFIER_NONE"'), ("m_flNoiseTurbulenceScale", "1.0"), ("m_flNoiseTurbulenceMix", "0.5"),
         ("m_flNoiseImgPreviewScale", "1.0"), ("m_bNoiseImgPreviewLive", "true"), ("m_flNoCameraFallback", "0.0"),
         ("m_bUseBoundsCenter", "false"), ("m_nInputMode", '"PF_INPUT_MODE_CLAMPED"'), ("m_flMultFactor", "1.0"),
         ("m_flInput0", "0.0"), ("m_flInput1", "1.0"), ("m_flOutput0", "0.0"), ("m_flOutput1", "1.0"),
         ("m_flNotchedRangeMin", "0.0"), ("m_flNotchedRangeMax", "1.0"), ("m_flNotchedOutputOutside", "0.0"),
         ("m_flNotchedOutputInside", "1.0"), ("m_nRoundType", '"PF_ROUND_TYPE_NEAREST"'), ("m_nBiasType", '"PF_BIAS_TYPE_STANDARD"'),
         ("m_flBiasParameter", "0.0")]
    out = ["{"]
    for k, v in d:
        out.append(f"\t{k} = {ov.get(k, v)}")
    out += ["\tm_Curve = ", "\t{", "\t\tm_spline = [  ]", "\t\tm_tangents = [  ]",
            "\t\tm_vDomainMins = [ 0.0, 0.0 ]", "\t\tm_vDomainMaxs = [ 0.0, 0.0 ]", "\t}",
            "\tm_flCompareValue = 0.0", "}"]
    return [ind + ln for ln in out]

def _pvec_literal_lines(ind: str, vec) -> list:
    """A CPerParticleVecInput struct holding a literal vector (editor's full field list)."""
    out = ["{", "\tm_nType = \"PVEC_TYPE_LITERAL\"",
           f"\tm_vLiteralValue = [ {_kv_f(vec[0])}, {_kv_f(vec[1])}, {_kv_f(vec[2])} ]",
           "\tm_LiteralColor = [ 0, 0, 0 ]", "\tm_NamedValue = \"\"", "\tm_bFollowNamedValue = false",
           "\tm_nVectorAttribute = 6", "\tm_vVectorAttributeScale = [ 1.0, 1.0, 1.0 ]", "\tm_nControlPoint = 0",
           "\tm_nDeltaControlPoint = 0", "\tm_vCPValueScale = [ 1.0, 1.0, 1.0 ]",
           "\tm_vCPRelativePosition = [ 0.0, 0.0, 0.0 ]", "\tm_vCPRelativeDir = [ 1.0, 0.0, 0.0 ]"]
    for comp in ("m_FloatComponentX", "m_FloatComponentY", "m_FloatComponentZ", "m_FloatInterp"):
        out.append(f"\t{comp} = ")
        out += ["\t" + ln for ln in _pf_lines("")]
    out += ["\tm_flInterpInput0 = 0.0", "\tm_flInterpInput1 = 1.0", "\tm_vInterpOutput0 = [ 0.0, 0.0, 0.0 ]",
            "\tm_vInterpOutput1 = [ 1.0, 1.0, 1.0 ]", "\tm_Gradient = ", "\t{", "\t\tm_Stops = [  ]", "\t}",
            "\tm_vRandomMin = [ 0.0, 0.0, 0.0 ]", "\tm_vRandomMax = [ 0.0, 0.0, 0.0 ]", "}"]
    return [ind + ln for ln in out]

def _init_float_lines(ind: str, vmin: float, vmax: float, field: int, signflip: bool = False) -> list:
    """C_INIT_InitFloat with a uniform random value written to a particle attribute (1 life duration, 4 rotation, 5 rotation speed)."""
    out = [ind + "{", ind + '\t_class = "C_INIT_InitFloat"', ind + "\tm_InputValue = "]
    out += _pf_lines(ind + "\t", "PF_TYPE_RANDOM_UNIFORM", m_flRandomMin=_kv_f(vmin), m_flRandomMax=_kv_f(vmax),
                     m_bHasRandomSignFlip="true" if signflip else "false")
    out += [ind + f"\tm_nOutputField = {field}", ind + "},"]
    return out

def make_heatwave_normal(export_dir: Path):
    """Soft, noisy tangent-space normal map for the refraction sprite: bumps fade to a flat normal at the edge so no
    ring or outline is displaced. -> vtex path (relative)."""
    N = 128
    rng = random.Random(7)
    waves = [(rng.uniform(2.0, 5.0), rng.uniform(2.0, 5.0), rng.uniform(0, 6.283), rng.uniform(0.5, 1.0)) for _ in range(6)]
    H = [[0.0] * N for _ in range(N)]
    for y in range(N):
        for x in range(N):
            u, v = (x + .5) / N * 2 - 1, (y + .5) / N * 2 - 1
            rr = (u * u + v * v) ** .5
            env = max(0.0, 1.0 - rr)
            env = env * env * (3 - 2 * env)                       # smoothstep: 0 at the rim and flat there
            h = sum(a * math.sin(fx * u * 3.1416 + fy * v * 3.1416 + ph) for fx, fy, ph, a in waves)
            H[y][x] = h * env
    img = Image.new("RGBA", (N, N))
    px = img.load()
    k = STEAM_HEATWAVE_NORMAL_STRENGTH
    for y in range(N):
        for x in range(N):
            dx = (H[y][min(N - 1, x + 1)] - H[y][max(0, x - 1)]) * .5 * k
            dy = (H[min(N - 1, y + 1)][x] - H[max(0, y - 1)][x]) * .5 * k
            nx, ny, nz = -dx, -dy, 1.0
            ln = (nx * nx + ny * ny + nz * nz) ** .5
            u, v = (x + .5) / N * 2 - 1, (y + .5) / N * 2 - 1
            rr = min(1.0, (u * u + v * v) ** .5)
            al = int(255 * (1 - rr * rr) ** 2 + .5)               # opacity falls to 0 at the rim
            px[x, y] = (int((nx / ln * .5 + .5) * 255 + .5), int((ny / ln * .5 + .5) * 255 + .5),
                        int((nz / ln * .5 + .5) * 255 + .5), al)
    rel = "materials/particle/steam_heatwave_normal.png"
    (export_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    img.save(export_dir / rel)
    vtex = "materials/particle/steam_heatwave_normal.vtex"
    write_simple_vtex(export_dir / vtex, rel, "DXT5", linear=True)
    return vtex

def _steam_renderer_extras(emissive: bool, refract: bool) -> list:
    """Extra C_OP_RenderSprites fields (tab-indented as renderer members) for lighting / fog / brightness."""
    ind = "\t\t\t"
    out = []

    def fl(name, value):
        out.append(f"{ind}{name} = ")
        out.extend(_pf_lines(ind, "PF_TYPE_LITERAL", m_flLiteralValue=_kv_f(value)))
    if not refract:
        if emissive and STEAM_SELF_ILLUM is not None:
            fl("m_flSelfIllumAmount", STEAM_SELF_ILLUM)
        if STEAM_DIFFUSE_AMOUNT is not None:
            fl("m_flDiffuseAmount", STEAM_DIFFUSE_AMOUNT)
        if STEAM_OVERBRIGHT is not None and float(STEAM_OVERBRIGHT) != 1.0:
            fl("m_flOverbrightFactor", STEAM_OVERBRIGHT)
    fog = {"disabled": "PARTICLE_FOG_DISABLED", "enabled": "PARTICLE_FOG_ENABLED"}.get(str(STEAM_FOG).strip().lower())
    if fog:
        out.append(f'{ind}m_nFogType = "{fog}"')
    return out

def _steam_speed_vecs(speed: float, spread: float):
    """(min, max) local speed vectors with the jet along STEAM_FORWARD_AXIS and +-spread on the other two axes."""
    ax = STEAM_FORWARD_AXIS.strip().lower()
    sign = -1.0 if ax.startswith("-") else 1.0
    i = "xyz".index(ax.lstrip("-")) if ax.lstrip("-") in ("x", "y", "z") else 0
    vmin, vmax = [-spread] * 3, [spread] * 3
    vmin[i] = vmax[i] = sign * speed
    return tuple(vmin), tuple(vmax)

def steam_initializers_text(speed: float, spread: float, life: float, roll: float) -> str:
    ind = "\t\t"
    vmin, vmax = _steam_speed_vecs(speed, spread)
    ax = {"x": 0, "y": 1, "z": 2}.get(str(STEAM_EMIT_AXIS).strip().lower(), 0)
    vmin, vmax = [-spread] * 3, [spread] * 3
    vmin[ax] = vmax[ax] = speed
    out = [ind + "{", ind + '\t_class = "C_INIT_CreateWithinSphereTransform"',
           ind + "\tm_LocalCoordinateSystemSpeedMin = "]
    out += _pvec_literal_lines(ind + "\t", vmin)
    out.append(ind + "\tm_LocalCoordinateSystemSpeedMax = ")
    out += _pvec_literal_lines(ind + "\t", vmax)
    out.append(ind + "},")
    out += _init_float_lines(ind, life, life, 1)
    out += _init_float_lines(ind, 0.0, 360.0, 4, True)
    out += _init_float_lines(ind, -roll, roll, 5, True)
    return "\n".join(out)


def _steam_num(kv: dict, key: str, default: float) -> float:
    for k, v in kv.items():
        if k.lower() == key.lower():
            try:
                return float(str(v).split()[0])
            except (ValueError, IndexError):
                break
    return float(default)

def _steam_str(kv: dict, key: str, default: str = "") -> str:
    return next((str(v) for k, v in kv.items() if k.lower() == key.lower()), default)

def _steam_status(msg: str) -> str:
    return msg if msg.startswith("SKIPPED") else f"FAILED: {msg}"

def _sn(v: float) -> str:
    """number for a file name: 120 -> '120', 2.5 -> '2d5'"""
    return f"{float(v):g}".replace(".", "d").replace("-", "m")

def steam_particle_name(kv: dict) -> str:
    """The particle's file name carries the Source1 values that shaped it, so the vmap's effect_name is enough to rebuild
    it on any run (no env_steam entity, hash or manifest needed):
    env_steam_s<Speed>_p<SpreadSpeed>_z<StartSize>-<EndSize>_r<Rate>_j<JetLength>_l<rollspeed>_a<renderamt>_c<r>-<g>-<b>_e<emissive>_t<type>"""
    try:
        r, g, b = [max(0, min(255, int(float(x)))) for x in _steam_str(kv, "rendercolor", "255 255 255").split()[:3]]
    except ValueError:
        r = g = b = 255
    emissive = int(_steam_num(kv, "spawnflags", 0)) & 1
    return ("env_steam_s{}_p{}_z{}-{}_r{}_j{}_l{}_a{}_c{}-{}-{}_e{}_t{}".format(
        _sn(_steam_num(kv, "Speed", 120)), _sn(_steam_num(kv, "SpreadSpeed", 15)),
        _sn(_steam_num(kv, "StartSize", 10)), _sn(_steam_num(kv, "EndSize", 25)),
        _sn(_steam_num(kv, "Rate", 26)), _sn(_steam_num(kv, "JetLength", 80)), _sn(_steam_num(kv, "rollspeed", 8)),
        int(_steam_num(kv, "renderamt", 255)), r, g, b, emissive, int(_steam_num(kv, "type", 0)))).lower()

_SN = r"m?\d+(?:d\d+)?"
STEAM_NAME_RE = re.compile(
    rf"^env_steam_s({_SN})_p({_SN})_z({_SN})-({_SN})_r({_SN})_j({_SN})_l({_SN})_a(\d+)_c(\d+)-(\d+)-(\d+)_e([01])_t(\d+)$")

def parse_steam_particle_name(stem: str):
    """Inverse of steam_particle_name -> env_steam keyvalues (strings), or None if the name is not one of ours."""
    m = STEAM_NAME_RE.match(stem.lower())
    if not m:
        return None
    n = lambda t: str(float(t.replace("d", ".").replace("m", "-")))   # noqa: E731
    g = m.groups()
    return {"Speed": n(g[0]), "SpreadSpeed": n(g[1]), "StartSize": n(g[2]), "EndSize": n(g[3]), "Rate": n(g[4]),
            "JetLength": n(g[5]), "rollspeed": n(g[6]), "renderamt": g[7], "rendercolor": f"{g[8]} {g[9]} {g[10]}",
            "spawnflags": g[11], "type": g[12]}

def make_steam_particle(export_dir: Path, name: str, kv: dict):
    """Source1 env_steam keyvalues -> particles/steam/<name>.vpcf, following C_SteamJet (Source SDK 2013):
      * every particle lives JetLength / Speed seconds
      * spawns at the entity origin with velocity = Speed*forward + rand(-SpreadSpeed, SpreadSpeed)*(right, up), constant
        (no gravity, no drag), roll = random 0-360 deg turning at rand(-rollspeed, rollspeed)
      * size lerps StartSize -> EndSize over the lifetime
      * alpha = sin(pi * age / lifetime) * renderamt / 255  (0 -> peak at half life -> 0); the particle fades in and out
        linearly here, which is the closest simple operator (check in the particle editor)
      * colour = rendercolor when the Emissive flag (spawnflags 1) is set; otherwise Source1 uses world lighting
        (STEAM_UNLIT_TINT here)
    -> (ok, message, entity-settings lines). Operator/field names are Source 2 particle classes written from memory of
    Hammer-saved vpcf61 files: open the result in the particle editor and check."""
    speed = _steam_num(kv, "Speed", 120)
    spread = _steam_num(kv, "SpreadSpeed", 15)
    size0 = _steam_num(kv, "StartSize", 10)
    size1 = _steam_num(kv, "EndSize", 25)
    rate = _steam_num(kv, "Rate", 26)
    jet = _steam_num(kv, "JetLength", 80)
    roll = _steam_num(kv, "rollspeed", 8)
    alpha = max(0, min(255, int(round(_steam_num(kv, "renderamt", 255) * STEAM_ALPHA_SCALE))))
    flags = int(_steam_num(kv, "spawnflags", 0))
    emissive = bool(flags & 1)
    if emissive:
        try:
            r, g, b = [max(0, min(255, int(float(x)))) for x in _steam_str(kv, "rendercolor", "255 255 255").split()[:3]]
        except ValueError:
            r = g = b = 255
        r, g, b = (min(255, c + a) for c, a in zip((r, g, b), STEAM_EMISSIVE_ADD))
    else:
        r, g, b = STEAM_UNLIT_TINT
    heat = int(_steam_num(kv, "type", 0)) == 1
    if heat and STEAM_HEATWAVE_MODE == "skip":
        return False, ("SKIPPED: env_steam type 1 is the heat-wave variant (a distortion with no visible steam in Source1) and "
                       "STEAM_HEATWAVE_MODE is \"skip\"; nothing built"), []
    refract = heat and STEAM_HEATWAVE_MODE == "refract"
    if refract:
        r = g = b = 255
    if speed <= 0:
        return False, "Speed is 0 (Source1 divides JetLength by Speed); nothing to build", []
    life = max(0.05, jet / speed)
    max_p = int(rate * life) + 6
    start_r = max(size0, 1.0) * STEAM_SIZE_TO_RADIUS
    end_scale = max(size1, 1.0) / max(size0, 1.0)

    refract_amt = 0.25 if STEAM_HEATWAVE_REFRACT is None else float(STEAM_HEATWAVE_REFRACT)
    refract_note = ""
    if refract and STEAM_HEATWAVE_REFRACT is None:
        # Source1 keeps the heat-wave distortion in the MATERIAL ($refractamount of sprites/heatwave.vmt, which env_steam type 1 always draws), not on the entity or the particle
        _hm = STEAM_HEATWAVE_TEXTURE[11:].strip().replace("\\", "/") if STEAM_HEATWAVE_TEXTURE.lower().startswith("source1vmt:") else "sprites/heatwave"
        _hv = None
        try:
            _hp, _hr = find_in_source1("materials/" + _hm + ".vmt")
            if _hp:
                _m = re.search(r'"?\$refractamount"?\s+"?(-?[0-9]*\.?[0-9]+)', parse_vmt(Path(_hp)).get("text", ""), flags=re.I)
                if _m:
                    _hv = float(_m.group(1))
        except Exception:
            _hv = None
        if _hv is None:
            _hv = refract_amt
            refract_note = f"; WARNING refract amount {refract_amt:g} is NOT the Source1 one: materials/{_hm}.vmt (with its $refractamount) was not found under SOURCE1_ROOTS - extract it there, or set STEAM_HEATWAVE_REFRACT"
        else:
            refract_note = f"; refract amount {_hv:g} taken from Source1 material {_hm}"
        refract_amt = _hv
    if refract:
        if STEAM_HEATWAVE_TEXTURE.lower().startswith("source1vmt:"):
            _vref = STEAM_HEATWAVE_TEXTURE[11:].strip().replace("\\", "/")
            try:
                _vp, _r = find_in_source1("materials/" + _vref + ".vmt")
                if not _vp:
                    raise FileNotFoundError(f"materials/{_vref}.vmt not found in SOURCE1_ROOTS")
                _vd = parse_vmt(Path(_vp))
                if not _vd.get("normalmap"):
                    raise ValueError("the material has no $normalmap")
                _crel, _a = export_color_texture(export_dir, _vd["normalmap"], False)
                vtex_rel = os.path.splitext(_crel)[0] + "_heatnormal.vtex"
                write_simple_vtex(export_dir / vtex_rel, _crel, "DXT5", linear=True)
                tex_note = f"refraction using the normal map of Source1 material {_vref} ({_vd['normalmap']}) -> {vtex_rel}"
            except Exception as _ex:
                vtex_rel = make_heatwave_normal(export_dir)
                tex_note = f"Source1 material {_vref} unavailable ({_ex}); fell back to the generated soft normal map ({vtex_rel})"
        elif STEAM_HEATWAVE_TEXTURE.lower().startswith("source1:"):
            _ref = STEAM_HEATWAVE_TEXTURE[8:].strip()
            try:
                _crel, _a = export_color_texture(export_dir, _ref, False)
                vtex_rel = os.path.splitext(_crel)[0] + "_refract.vtex"
                write_simple_vtex(export_dir / vtex_rel, _crel, linear=True)
                tex_note = f"refraction using Source1 texture {_ref} ({vtex_rel}); it is not a normal map, so expect a rough shimmer"
            except Exception as _ex:
                vtex_rel = make_heatwave_normal(export_dir)
                tex_note = f"Source1 texture {_ref} unavailable ({_ex}); fell back to the generated soft normal map ({vtex_rel})"
        elif STEAM_HEATWAVE_TEXTURE:
            vtex_rel = STEAM_HEATWAVE_TEXTURE
            tex_note = f"refraction using stock texture {vtex_rel}"
            if USE_CS2_VPK and cs2_vpk().loaded and not cs2_file_exists(vtex_rel + "_c"):
                tex_note += " (NOT found in the CS2 VPK index: check the path, STEAM_HEATWAVE_TEXTURE)"
        else:
            vtex_rel = make_heatwave_normal(export_dir)
            tex_note = f"refraction using a generated soft normal map ({vtex_rel})"
    else:
        tex_note = ""
        try:
            color_rel, _a = export_color_texture(export_dir, STEAM_TEXTURE_SOURCE1, False)
            tex_note = f"Source1 texture {STEAM_TEXTURE_SOURCE1}"
        except Exception:
            N = 64
            img = Image.new("RGBA", (N, N))
            px = img.load()
            for y in range(N):
                for x in range(N):
                    d = (((x + .5) / N - .5) ** 2 + ((y + .5) / N - .5) ** 2) ** .5 * 2
                    a = int(max(0.0, 1 - d) ** 1.6 * 255 + .5)
                    px[x, y] = (255, 255, 255, a)
            color_rel = "materials/particle/steam_soft_color.png"
            (export_dir / color_rel).parent.mkdir(parents=True, exist_ok=True)
            img.save(export_dir / color_rel)
            tex_note = "generated soft puff (Source1 texture not found)"
        vtex_rel = os.path.splitext(color_rel)[0] + "_particle.vtex"
        write_simple_vtex(export_dir / vtex_rel, color_rel)

    if refract:
        renderer = "\n".join(
            ["\t\t{", '\t\t\t_class = "C_OP_RenderSprites"'] + _steam_renderer_extras(emissive, True)
            + ["\t\t\tm_bRefract = true", "\t\t\tm_flRefractAmount = "]
            + _pf_lines("\t\t\t", "PF_TYPE_LITERAL", m_flLiteralValue=_kv_f(refract_amt))
            + ["\t\t\tm_nRefractBlurRadius = 1", '\t\t\tm_nRefractBlurType = "BLURFILTER_BOX"',
               "\t\t\tm_vecTexturesInput =", "\t\t\t[", "\t\t\t\t{", f'\t\t\t\t\tm_hTexture = resource:"{vtex_rel}"',
               "\t\t\t\t},", "\t\t\t]", "\t\t},"])
    else:
        renderer = "\n".join(
            ["\t\t{", '\t\t\t_class = "C_OP_RenderSprites"'] + _steam_renderer_extras(emissive, False)
            + ["\t\t\tm_vecTexturesInput =", "\t\t\t[", "\t\t\t\t{",
               f'\t\t\t\t\tm_hTexture = resource:"{vtex_rel}"', "\t\t\t\t},", "\t\t\t]", "\t\t},"])

    body = VPCF66_HEADER + f"""{{
	_class = "CParticleSystemDefinition"
	m_nBehaviorVersion = 12
	m_nMaxParticles = {max_p}
	m_ConstantColor = [ {r}, {g}, {b}, {alpha} ]
	m_flConstantRadius = {start_r:.2f}
	m_Renderers =
	[
{renderer}
	]
	m_Emitters =
	[
		{{
			_class = "C_OP_ContinuousEmitter"
			m_flEmitRate = {rate:.2f}
		}},
	]
	m_Initializers =
	[
{steam_initializers_text(speed, spread, life, STEAM_HEATWAVE_SPIN if refract else roll)}
	]
	m_Operators =
	[
		{{
			_class = "C_OP_BasicMovement"
		}},
		{{
			_class = "C_OP_SpinUpdate"
		}},
		{{
			_class = "C_OP_InterpolateRadius"
			m_flStartTime = 0.0
			m_flEndTime = 1.0
			m_flStartScale = 1.0
			m_flEndScale = {end_scale:.4f}
		}},
		{{
			_class = "C_OP_FadeAndKill"
			m_flStartFadeInTime = 0.0
			m_flEndFadeInTime = 0.5
			m_flStartFadeOutTime = 0.5
			m_flEndFadeOutTime = 1.0
			m_flStartAlpha = 0.0
			m_flEndAlpha = 0.0
		}},
		{{
			_class = "C_OP_Decay"
		}},
	]
}}
"""
    out = export_dir / "particles" / "steam" / f"{name}.vpcf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    lines = [f"{name}\teffect_name=particles/steam/{name}.vpcf\t"
             f"lifetime={life:.3f}s speed={speed:g} spread={spread:g} rate={rate:g}/s max_particles={max_p} "
             f"size {size0:g}->{size1:g} (radius {start_r:.1f} x{end_scale:.2f}) roll=+-{roll:g} "
             f"colour {r} {g} {b} ({'emissive' if emissive else 'lit in Source1; STEAM_UNLIT_TINT here'}) alpha<={alpha}\t"
             f"texture: {tex_note}{refract_note if refract else ''}"]
    if heat:
        lines.append(f"{name}\tNOTE: env_steam type 1 is the heat-wave (distortion) variant; built as "
                     + ("a refracting sprite (no visible steam)" if refract else "visible soft sprites, not a distortion"))
    return True, "ok", lines

def _angles_forward(ang: str) -> str:
    """Source qangle 'pitch yaw roll' -> world direction of the entity's forward axis, as text."""
    try:
        pitch, yaw = [math.radians(float(x)) for x in str(ang).split()[:2]]
    except ValueError:
        return "?"
    x, y, z = math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), -math.sin(pitch)
    names = {(1, 0, 0): "east (+X)", (-1, 0, 0): "west (-X)", (0, 1, 0): "north (+Y)", (0, -1, 0): "south (-Y)",
             (0, 0, 1): "UP (+Z)", (0, 0, -1): "DOWN (-Z)"}
    nm = names.get((round(x), round(y), round(z))) if max(abs(x - round(x)), abs(y - round(y)), abs(z - round(z))) < 0.01 else None
    return f"({x:.2f}, {y:.2f}, {z:.2f})" + (f" = {nm}" if nm else "")

# ---- reading entities from a .vmap (binary DMX "binary 9" as saved by CS2 Hammer, or KeyValues2 text) ----

def read_binary_dmx(d: bytes):
    """Parse a binary DMX file (encoding 'binary 9', e.g. a CS2 .vmap) -> list of elements
    {"type": str, "name": str, "attrs": {name: (basetype, is_array, value)}, "offs": {name: byte offset of the value (arrays: first item)}}.
    Element-reference values are element indexes (-1 = none). The offsets are for patching values in place. Parsed by _DmxDoc.
    The last result is kept (passes in a row read the same unchanged vmap), so callers must treat it as read-only.
    Raises ValueError if the layout does not add up (so callers can fall back)."""
    import hashlib
    key = (len(d), hashlib.blake2b(d, digest_size=16).digest())
    if key in _DMX_PARSE_CACHE:
        return _DMX_PARSE_CACHE[key]
    head = d[:80].split(b"\n", 1)[0].decode("latin-1", errors="ignore")
    if "encoding binary 9" not in head:
        raise ValueError(f"unsupported DMX header: {head.strip()[:60]}")
    try:
        doc = _DmxDoc(bytes(d), offsets=True)
    except (struct.error, IndexError, KeyError) as ex:
        raise ValueError(f"binary DMX parse failed: {ex}")
    idx = {id(e): i for i, e in enumerate(doc.els)}

    def conv(bt, v):
        if bt == 1:
            return -1 if v is None else idx[id(v)]
        if bt == 4:
            return bool(v)
        if bt == 5 and not v.isascii():
            return v.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
        if bt == 7:
            return v & 0xFFFFFFFF
        return v
    out = []
    for e in doc.els:
        el = {"type": e.type, "name": e.name, "attrs": {}}
        for name, t, v in e.attrs:
            bt = t - 32 if t >= 32 else t
            if bt in (1, 4, 5, 7):                       # the rest is already in the old reader's form
                v = [conv(bt, x) for x in v] if t >= 32 else conv(bt, v)
            el["attrs"][name] = (bt, t >= 32, v)
        if e.attrs:
            el["offs"] = e.offs
        out.append(el)
    _DMX_PARSE_CACHE.clear()
    _DMX_PARSE_CACHE[key] = out
    return out


_DMX_PARSE_CACHE = {}      # read_binary_dmx: (size, content hash) -> elements of the last file parsed

def _fmt_vec(v, nd=1) -> str:
    return " ".join(f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else str(x) for x in v)

def vmap_entities(path: str):
    """Entities in a .vmap (cached until the file changes) -> see _vmap_entities"""
    st = os.stat(path)
    return _vmap_entities(path, st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=8)
def _vmap_entities(path: str, _mtime_ns=0, _size=0):
    """Entities in a .vmap -> ([{"cls", "name", "origin", "angles", "props"}], error text or "").
    props = every keyvalue as text. Binary DMX (CS2 Hammer's default) and KeyValues2 text are both read."""
    try:
        data = Path(path).read_bytes()
    except Exception as ex:
        return [], f"cannot read: {ex}"
    ents = []
    if b"encoding binary" in data[:100]:
        try:
            els = read_binary_dmx(data)
        except ValueError as ex:
            return [], str(ex)
        for e in els:
            if e["type"] != "CMapEntity":
                continue
            ep = e["attrs"].get("entity_properties")
            if not ep or not (0 <= ep[2] < len(els)):
                continue
            props = {k: str(v[2]) for k, v in els[ep[2]]["attrs"].items() if v[0] == 5 and not v[1]}
            ents.append({"cls": props.get("classname", ""), "name": props.get("targetname", ""),
                         "origin": _fmt_vec(e["attrs"]["origin"][2]) if "origin" in e["attrs"] else "?",
                         "angles": _fmt_vec(e["attrs"]["angles"][2]) if "angles" in e["attrs"] else "?",
                         "props": props})
        return ents, ""
    if "keyvalues2" not in data[:200].decode("latin-1", errors="ignore").lower():
        return [], "unrecognised .vmap format"
    stack, found = [{}], []
    for raw in data.decode("utf-8", errors="ignore").splitlines():
        m = _KV2_LINE_RE.match(raw)
        if m:
            stack[-1][m.group(1)] = m.group(3)
            continue
        ln = raw.strip()
        if ln == "{":
            stack.append({})
        elif ln == "}" and len(stack) > 1:
            blk = stack.pop()
            if blk.get("classname"):
                found.append((blk, stack[-1]))
    for blk, parent in found:      # the parent entity block holds origin/angles (read after it was filled)
        ents.append({"cls": blk.get("classname", ""), "name": blk.get("targetname", ""),
                     "origin": blk.get("origin") or parent.get("origin", "?"),
                     "angles": blk.get("angles") or parent.get("angles", "?"), "props": dict(blk)})
    return ents, ""

def _overlay_texture_size(mat: str):
    """Source1 texture size x $decalscale for an overlay material -> (w, h, scale) or None."""
    try:
        base = mat.replace("\\", "/").lower()
        if base.endswith(".vmat"):
            base = base[:-5]
        vp, _r = find_in_source1(base + ".vmt")
        if not vp:
            return None
        data = parse_vmt(Path(vp))
        vtf = resolve_vtf(data["basetexture"]) if data.get("basetexture") else None
        if not vtf:
            return None
        h = parse_vtf_header(vtf)
        m = re.search(r'"?\$decalscale"?\s+"?([0-9.]+)', data.get("text", ""), flags=re.I)
        return h["width"], h["height"], float(m.group(1)) if m else 1.0
    except Exception:
        return None

_ADDON_DIR = None      # set by main(): the addon folder the converted materials/textures were written to

def _addon_texture_size(mat: str):
    """Size of the colour texture the converted addon material actually uses (what Hammer draws with) -> (w, h, 1.0) or None."""
    try:
        if not _ADDON_DIR:
            return None
        base = mat.replace("\\", "/").lower()
        vm = Path(_ADDON_DIR) / (base if base.endswith(".vmat") else base + ".vmat")
        if not vm.is_file():
            return None
        mm = re.search(r'"(?:TextureColor|TextureLayer1Color)"\s+"([^"]+)"', vm.read_text(errors="ignore"))
        if not mm:
            return None
        tp = Path(_ADDON_DIR) / mm.group(1)
        if not tp.is_file():
            return None
        hdr = tp.read_bytes()[:32]
        if tp.suffix.lower() == ".tga" and len(hdr) >= 18:
            w, h = struct.unpack("<HH", hdr[12:16])
        elif hdr[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", hdr[16:24])
        else:
            return None
        return (w, h, 1.0) if w > 0 and h > 0 else None
    except Exception:
        return None

def static_overlay_report(vmap_paths, export_dir: Path):
    """CMapStaticOverlay elements (Hammer's decals / Source1 infodecal+info_overlay) are not CMapEntity, so the
    entity checks never see them. -> report lines: one per overlay with origin, material, whether the mesh is still the
    importer's placeholder (a flat 2x2 quad, angles 0, scale 1) or has been edited, the suggested size from the Source1
    texture, and whether the material exists in the addon / CS2."""
    lines, notes, total, n_ph = [], [], 0, 0
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
        except Exception:
            continue
        if b"encoding binary" not in data[:100]:
            notes.append(f"# {os.path.basename(str(vp))}: not a binary .vmap; overlays not listed")
            continue
        try:
            els = read_binary_dmx(data)
        except ValueError as ex:
            notes.append(f"# {os.path.basename(str(vp))}: {ex}")
            continue
        for e in els:
            if e["type"] != "CMapStaticOverlay":
                continue
            a = e["attrs"]
            total += 1
            mats, pos = [], []
            md = a.get("meshData")
            if md and isinstance(md[2], int) and 0 <= md[2] < len(els):
                mesh = els[md[2]]["attrs"]
                mv = mesh.get("materials")
                mats = list(mv[2]) if mv else []
                vd = mesh.get("vertexData")
                if vd and isinstance(vd[2], int) and 0 <= vd[2] < len(els):
                    for si in els[vd[2]]["attrs"].get("streams", (0, 0, []))[2]:
                        if isinstance(si, int) and 0 <= si < len(els) and els[si]["name"].lower().startswith("position"):
                            pos = list(els[si]["attrs"].get("data", (0, 0, []))[2])
            sc = a["scales"][2] if "scales" in a else (1.0, 1.0, 1.0)
            ang = a["angles"][2] if "angles" in a else (0.0, 0.0, 0.0)
            if pos:
                xs, ys = [q[0] for q in pos], [q[1] for q in pos]
                ext = ((max(xs) - min(xs)) * abs(sc[0]), (max(ys) - min(ys)) * abs(sc[1]))
                is_ph = len(pos) == 4 and all(abs(abs(q[0]) - 1) < 1e-3 and abs(abs(q[1]) - 1) < 1e-3 for q in pos)      # the importer's +-1 quad, whatever its scale / angles / depth
            else:
                ext, is_ph = None, False
            tg = a.get("projectionTargets")
            ntg = len(tg[2]) if tg else 0
            for mat in mats or ["?"]:
                where = "?"
                ts = None
                if mat != "?":
                    rel = mat.replace("\\", "/").lower()
                    if file_exists(str(export_dir / (rel + "_c"))) or file_exists(str(export_dir / rel)):
                        where = "in addon"
                    elif cs2_file_exists(rel + "_c"):
                        where = "in CS2"
                    else:
                        where = "MISSING"
                    ts = _overlay_texture_size(mat)
                if is_ph:
                    n_ph += 1
                    status = "PLACEHOLDER"
                elif ext is None:
                    status = "NO MESH"
                elif ts and abs(ext[0] - ts[0] * ts[2]) <= 0.1 * ts[0] * ts[2] and abs(ext[1] - ts[1] * ts[2]) <= 0.1 * ts[1] * ts[2]:
                    status = "OK (size matches texture)"
                else:
                    status = "EDITED"
                sugg = (f"suggest size {ts[0] * ts[2]:g}x{ts[1] * ts[2]:g} -> scales {ts[0] * ts[2] / 2:g} {ts[1] * ts[2] / 2:g} 1 (mesh is +-1)"
                        if ts else "suggest size: Source1 texture not found")
                cur = f"{ext[0]:g}x{ext[1]:g}" if ext else "?"
                lines.append((mat, f"{status}\t{os.path.basename(str(vp))}\t{_fmt_vec(a['origin'][2]) if 'origin' in a else '?'}\t{mat}\t{where}"
                             f"\tsize={cur}\tscales={_fmt_vec(sc)}\tangles={_fmt_vec(ang)}\t{sugg}"
                             f"\tprojectionTargets={ntg}\tprojectionFar={a.get('projectionFar', (0, 0, '?'))[2]}"
                             f"\thidden={a.get('force_hidden', (0, 0, '?'))[2]}"))
    head = [f"# {total} static overlay(s) (CMapStaticOverlay); {n_ph} still the importer's placeholder quad (2x2, angles 0, scale 1)",
            "# To fix one by hand in Hammer: set its Scale to the 'scales' value (the quad is +-1 so scale = size/2) and rotate it with Angles so the quad lies on the surface. Rerun: it flips to OK / EDITED."]
    return head + notes + [t for _m, t in sorted(lines)]      # grouped by material: overlays of one material share a size

def _vmf_tree(path: str):
    stack, pending = [("root", {}, [])], ""
    for raw in open(path, encoding="utf-8", errors="replace"):
        ln = raw.strip()
        if ln == "{":
            stack.append((pending, {}, []))
        elif ln == "}":
            node = stack.pop()
            stack[-1][2].append(node)
        elif ln.startswith('"'):
            m = re.match(r'"([^"]*)"\s+"(.*)"\s*$', ln)
            if m:
                stack[-1][1][m.group(1).lower()] = m.group(2)
        elif ln:
            pending = ln
    return stack[0]

def _v_sub(a, b): return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
def _v_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _v_cross(a, b): return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
def _v_norm(a):
    n = math.sqrt(_v_dot(a, a)) or 1.0
    return [x / n for x in a]

def _vmf_solids(root):
    """-> [[(outward unit normal, point on plane, material)]] one list per convex brush"""
    out = []
    def walk(node):
        for ch in node[2]:
            if ch[0] == "solid":
                faces, pts = [], []
                for sd in ch[2]:
                    if sd[0] != "side":
                        continue
                    pl = [[float(x) for x in t.split()] for t in re.findall(r"\(([^)]*)\)", sd[1].get("plane", ""))]
                    if len(pl) != 3:
                        continue
                    n = _v_norm(_v_cross(_v_sub(pl[1], pl[0]), _v_sub(pl[2], pl[0])))
                    faces.append([n, pl[0], sd[1].get("material", "").lower()])
                    pts += pl
                if faces and pts:
                    c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
                    for f in faces:
                        if _v_dot(f[0], _v_sub(f[1], c)) < 0:
                            f[0] = [-x for x in f[0]]
                    out.append([tuple(f) for f in faces])
            elif ch[0] in ("world", "entity"):
                walk(ch)
    walk(root)
    return out

def _surface_under(solids, p, tol=3.0):
    """nearest brush face the point lies on (within tol units) -> (unit normal, point on the face) or None"""
    best = None
    for faces in solids:
        for n, p0, mat in faces:
            if mat.startswith("tools/") and "nodraw" not in mat:
                continue
            d = _v_dot(n, _v_sub(p, p0))
            if abs(d) > tol:
                continue
            q = [p[k] - n[k] * d for k in range(3)]
            if all(_v_dot(n2, _v_sub(q, p2)) <= 1.0 for n2, p2, _m in faces):
                if best is None or abs(d) < best[0]:
                    best = (abs(d), n, q)
    return (best[1], best[2]) if best else None

def _src_angles_from_axes(x, z):
    """Source QAngle (pitch, yaw, roll) whose forward axis is x and up axis is z (both unit, orthogonal)."""
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -x[2]))))
    yaw = math.degrees(math.atan2(x[1], x[0])) if abs(x[0]) + abs(x[1]) > 1e-6 else 0.0
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    best = (9.0, 0.0)
    for r in range(-180, 180):          # solve roll against Source's AngleVectors up vector
        cr, sr = math.cos(math.radians(r)), math.sin(math.radians(r))
        up = [cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp]
        err = sum((up[k] - z[k]) ** 2 for k in range(3))
        if err < best[0]:
            best = (err, float(r))
    return pitch, yaw, best[1]

def _overlay_axes(n, decal_yaw):
    """local axes of the overlay quad. CS2 projects along the overlay's local +Z, so Z points INTO the surface (-normal), Y is the
    texture's up, and X = Y x Z is the viewer's LEFT (the corner x signs are flipped when writing so the image is not mirrored).
    Found by testing: Z = +normal made every overlay invisible; turning it 180 degrees showed them mirrored.
    Walls: Y up the wall; floors/ceilings: viewer's right from the decal's yaw."""
    if abs(n[2]) > 0.9:
        a = math.radians(decal_yaw)
        x = [math.cos(a), math.sin(a), 0.0]
        x = _v_norm(_v_sub(x, [n[k] * _v_dot(x, n) for k in range(3)]))
    else:
        x = _v_norm(_v_cross([0.0, 0.0, 1.0], n))
    return [-q for q in x], [-q for q in n]

def _angles_axes(p, y, r):
    """Source AngleVectors: (forward, up) unit vectors for a QAngle in degrees"""
    sp, cp = math.sin(math.radians(p)), math.cos(math.radians(p))
    sy, cy = math.sin(math.radians(y)), math.cos(math.radians(y))
    sr, cr = math.sin(math.radians(r)), math.cos(math.radians(r))
    return [cp * cy, cp * sy, -sp], [cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp]

def fix_static_overlays(vmap_paths, vmf_path: str, in_place=False):
    """Rebuild the importer's placeholder overlay quads: size = Source1 texture x $decalscale, orientation = the brush face under
    the matching infodecal in the .vmf (Z = surface normal), origin moved NORMAL_OFFSET above that face. Only same-size
    values are overwritten (vertex positions, origin, angles), in a COPY of the vmap. -> report lines"""
    lines = []
    try:
        tree = _vmf_tree(vmf_path)
    except Exception as ex:
        return [f"# cannot read {vmf_path}: {ex}"]
    solids = _vmf_solids(tree)
    decals = []
    for ent in [c for c in tree[2] if c[0] == "entity"]:
        kv = ent[1]
        if kv.get("classname", "").lower() in ("infodecal", "info_overlay") and kv.get("texture", kv.get("material")):
            o = [float(x) for x in kv.get("origin", "0 0 0").split()[:3]]
            ang = [float(x) for x in kv.get("angles", "0 0 0").split()[:3]]
            decals.append(dict(tex=(kv.get("texture") or kv.get("material")).lower().replace("\\", "/"), o=o, yaw=ang[1], used=False))
    lines.append(f"# {len(decals)} infodecal/info_overlay in the .vmf, {len(solids)} brushes. CHANGE = this overlay differs from where the .vmf says it should be ('*' marks the values to change); OK = already right.")
    for vp in vmap_paths:
        try:
            data = bytearray(Path(vp).read_bytes())
            els = read_binary_dmx(bytes(data))
        except Exception as ex:
            lines.append(f"# {os.path.basename(str(vp))}: {ex}")
            continue
        changed = 0
        for e in els:
            if e["type"] != "CMapStaticOverlay" or "offs" not in e:
                continue
            a, offs = e["attrs"], e["offs"]
            md = a.get("meshData")
            mesh = els[md[2]]["attrs"] if md else {}
            mats = mesh.get("materials", (0, 0, []))[2]
            mat = (mats[0] if mats else "").replace("\\", "/").lower()
            _sc = a["scales"][2] if "scales" in a else (1.0, 1.0, 1.0)
            _an = a["angles"][2] if "angles" in a else (0.0, 0.0, 0.0)
            _ps = []
            if mesh.get("vertexData"):
                for si in els[mesh["vertexData"][2]]["attrs"].get("streams", (0, 0, []))[2]:
                    if els[si]["name"].lower().startswith("position"):
                        _ps = els[si]["attrs"]["data"][2]
            if STATIC_OVERLAY_ONLY_PLACEHOLDERS and in_place and not (len(_ps) == 4 and all(abs(abs(q[0]) - 1) < 1e-3 and abs(abs(q[1]) - 1) < 1e-3 for q in _ps)):
                _fn = ""
                if STATIC_OVERLAY_PROJECTION_FAR and in_place and "projectionFar" in offs and "projectionFar" in a \
                        and abs(a["projectionFar"][2] - float(STATIC_OVERLAY_PROJECTION_FAR)) > 1e-3:
                    struct.pack_into("<f", data, offs["projectionFar"], float(STATIC_OVERLAY_PROJECTION_FAR))
                    _fn = f"; projection depth {a['projectionFar'][2]:g} -> {float(STATIC_OVERLAY_PROJECTION_FAR):g}"
                    changed += 1
                lines.append(f"SKIPPED (not the importer's +-1 quad; left as is{_fn})\t{_fmt_vec(a['origin'][2])}\t{mat}\tmesh corners {[tuple(round(c, 2) for c in q) for q in _ps[:4]]}\tscales {_fmt_vec(_sc)}\tangles {_fmt_vec(_an)}\tprojectionFar {a.get('projectionFar', (0, 0, '?'))[2]}")
                continue
            key = mat[len("materials/"):-5] if mat.startswith("materials/") and mat.endswith(".vmat") else mat
            ox, oy, oz = a["origin"][2]
            cand = [d for d in decals if not d["used"] and d["tex"] == key
                    and sum((d["o"][k] - (ox, oy, oz)[k]) ** 2 for k in range(3)) < 40.0 ** 2]    # the importer puts an overlay exactly 16 units off the .vmf decal (and an edited one can move further); the nearest unused decal of the same texture wins
            if not cand:
                lines.append(f"NO VMF MATCH\t{_fmt_vec(a['origin'][2])}\t{mat}")
                continue
            d = min(cand, key=lambda q: sum((q["o"][k] - (ox, oy, oz)[k]) ** 2 for k in range(3)))
            d["used"] = True
            ts = _overlay_texture_size(mat)
            if not ts:
                lines.append(f"NO TEXTURE\t{_fmt_vec(a['origin'][2])}\t{mat}\t(Source1 texture not found; set SOURCE1_ROOTS)")
                continue
            surf = _surface_under(solids, d["o"])
            if not surf:
                lines.append(f"NO SURFACE\t{_fmt_vec(a['origin'][2])}\t{mat}\t(no brush face within 3 units of the decal origin)")
                continue
            n, q = surf
            xax, zax = _overlay_axes(n, d["yaw"])
            pitch, yaw, roll = _src_angles_from_axes(xax, zax)
            pitch, yaw, roll = (round(_q, 1) + 0.0 for _q in (pitch, yaw, roll))      # no '-0.0'
            w, h = ts[0] * ts[2], ts[1] * ts[2]
            neworg = [q[k] + n[k] * STATIC_OVERLAY_NORMAL_OFFSET for k in range(3)]
            vs = mesh.get("vertexData")
            vd_attr = mesh.get("faceVertexData")
            pos_off = None
            if vs:
                for si in els[vs[2]]["attrs"].get("streams", (0, 0, []))[2]:
                    if els[si]["name"].lower().startswith("position"):
                        pos_off = (els[si]["offs"]["data"], els[si]["attrs"]["data"][2])
            if not pos_off or len(pos_off[1]) != 4 or "origin" not in offs or "angles" not in offs:
                lines.append(f"NO MESH\t{_fmt_vec(a['origin'][2])}\t{mat}")
                continue
            cur_ang = a["angles"][2] if "angles" in a else (0.0, 0.0, 0.0)
            cf, cu = _angles_axes(*cur_ang)
            tf, tu = _angles_axes(pitch, yaw, roll)
            pts = pos_off[1]
            cur_w = (max(q[0] for q in pts) - min(q[0] for q in pts)) * abs(a["scales"][2][0] if "scales" in a else 1.0)
            cur_h = (max(q[1] for q in pts) - min(q[1] for q in pts)) * abs(a["scales"][2][1] if "scales" in a else 1.0)
            rot_ok = _v_dot(cf, tf) > 0.999 and _v_dot(cu, tu) > 0.999
            org_ok = sum((a["origin"][2][k] - neworg[k]) ** 2 for k in range(3)) < 1.0
            size_ok = abs(cur_w - w) < 1.0 and abs(cur_h - h) < 1.0
            lines.append(("OK\t" if rot_ok and org_ok and size_ok else "CHANGE\t") + f"{mat}\t"
                         f"origin {_fmt_vec(a['origin'][2])} -> {_fmt_vec(neworg)}{'' if org_ok else ' *'}\t"
                         f"angles {_fmt_vec(cur_ang)} -> {pitch:.1f} {yaw:.1f} {roll:.1f}{'' if rot_ok else ' *'}\t"
                         f"size {cur_w:g}x{cur_h:g} -> {w:g}x{h:g}{'' if size_ok else ' *'} (mesh corners +-{w / 2:g}, +-{h / 2:g}, scale 1 1 1)\t"
                         f"surface normal {_fmt_vec([round(_q, 2) + 0.0 for _q in n], 2)}")
            if not in_place:
                continue
            struct.pack_into("<3f", data, offs["origin"], *neworg)
            struct.pack_into("<3f", data, offs["angles"], pitch, yaw, roll)
            if "scales" in offs:
                struct.pack_into("<3f", data, offs["scales"], 1.0, 1.0, 1.0)
            if STATIC_OVERLAY_PROJECTION_FAR and "projectionFar" in offs:
                struct.pack_into("<f", data, offs["projectionFar"], float(STATIC_OVERLAY_PROJECTION_FAR))
            signs = []
            for i, (px, py, _pz) in enumerate(pos_off[1]):
                sx = 1 if px > 0 else -1
                sy = 1 if py > 0 else -1
                signs.append((sx, sy))
                struct.pack_into("<3f", data, pos_off[0] + 12 * i, sx * w / 2, sy * h / 2, 0.0)
            # texture coordinates: the importer's layout is u = (1 - x)/2, v = (1 + y)/2 per corner; write that (plus the chosen flip) as absolute values
            _flip = STATIC_OVERLAY_TEX_FLIP
            _tc = None
            if vd_attr and mesh.get("edgeVertexIndices") and mesh.get("edgeFaceIndices"):
                for _si in els[mesh["faceVertexData"][2]]["attrs"].get("streams", (0, 0, []))[2]:
                    if els[_si]["name"].lower().startswith("texcoord"):
                        _tc = els[_si]["offs"]["data"]
            if _tc is not None:
                _evi, _efi = mesh["edgeVertexIndices"][2], mesh["edgeFaceIndices"][2]
                for _he, (_vi, _fi) in enumerate(zip(_evi, _efi)):
                    if _fi < 0 or _vi >= len(signs):
                        continue
                    _u, _v = (1 - signs[_vi][0]) / 2, (1 + signs[_vi][1]) / 2
                    if "u" in _flip:
                        _u = 1 - _u
                    if "v" in _flip:
                        _v = 1 - _v
                    struct.pack_into("<2f", data, _tc + 8 * _he, _u, _v)
            changed += 1
            lines.append(f"FIXED\t{mat}\tfind it at current origin {_fmt_vec((ox, oy, oz))}\tset Origin {_fmt_vec(neworg)}\tAngles {pitch:.1f} {yaw:.1f} {roll:.1f}\tScale 1 1 1\tquad corners +-{w / 2:g} x +-{h / 2:g}\ttexture flip '{_flip}'\tsurface normal {_fmt_vec([round(_q, 2) + 0.0 for _q in n], 2)}")
        if changed:
            if in_place:
                bak = _backup_vmap(vp, "overlayfix")
                if not os.path.isfile(bak) or os.path.getsize(bak) != os.path.getsize(str(vp)):
                    lines.append(f"# BACKUP FAILED ({bak}); {vp} was NOT patched")
                    continue
                Path(vp).write_bytes(bytes(data))
                lines.append(f"# {changed} overlay(s) patched IN PLACE in {vp}; original saved as {bak}. Reopen the map in Hammer.")
                print(f"  static overlays: patched {changed} in {vp}\n    backup of the original: {bak}")
        elif in_place:
            _msg = (f"# nothing patched in {os.path.basename(str(vp))} (so no backup was made): every overlay is either already right, edited by hand "
                    f"(STATIC_OVERLAY_ONLY_PLACEHOLDERS = {STATIC_OVERLAY_ONLY_PLACEHOLDERS}), or has no matching decal in the .vmf")
            lines.append(_msg)
            print("  static overlays: " + _msg[2:])
    return lines

def _vmf_sides(root):
    """-> [dict(n, d, mat, ua, us, va, vs)] every brush side of the .vmf with its plane and texture mapping"""
    out = []
    def walk(node):
        for ch in node[2]:
            if ch[0] == "solid":
                planes, allpts = [], []
                first = len(out)
                for sd in ch[2]:
                    if sd[0] != "side":
                        continue
                    k = sd[1]
                    pl = [[float(x) for x in t.split()] for t in re.findall(r"\(([^)]*)\)", k.get("plane", ""))]
                    allpts += pl if len(pl) == 3 else []
                    ua = re.match(r"\[([^\]]*)\]\s*(\S+)", k.get("uaxis", ""))
                    va = re.match(r"\[([^\]]*)\]\s*(\S+)", k.get("vaxis", ""))
                    if len(pl) != 3 or not ua or not va:
                        continue
                    try:
                        n = _v_norm(_v_cross(_v_sub(pl[1], pl[0]), _v_sub(pl[2], pl[0])))
                        planes.append((n, _v_dot(n, pl[0])))
                        out.append(dict(n=n, d=_v_dot(n, pl[0]), solid=planes, mat=k.get("material", "").lower().replace("\\", "/").replace(" ", "_"),
                                        us=float(ua.group(2)), vs=float(va.group(2)), disp=any(c[0] == "dispinfo" for c in sd[2]),
                                        ua=[float(x) for x in ua.group(1).split()[:4]], va=[float(x) for x in va.group(1).split()[:4]]))
                    except ValueError:
                        pass
                if allpts:      # make every plane of this brush point outwards, so "inside the brush" can be tested
                    _bb = ([min(q[j] for q in allpts) for j in range(3)], [max(q[j] for q in allpts) for j in range(3)])
                    for _sd in out[first:]:
                        _sd["bb"] = _bb
                    cen = [sum(q[j] for q in allpts) / len(allpts) for j in range(3)]
                    for i, (n_, d_) in enumerate(planes):
                        if _v_dot(n_, cen) - d_ > 0:
                            planes[i] = ([-x for x in n_], -d_)
            elif ch[0] in ("world", "entity"):
                walk(ch)
    walk(root)
    return out

def _solve3(a, b, c):
    """intersection point of three planes (n, d) with n.p = d, or None"""
    cr = _v_cross(b[0], c[0])
    det = _v_dot(a[0], cr)
    if abs(det) < 1e-6:
        return None
    c31, c12 = _v_cross(c[0], a[0]), _v_cross(a[0], b[0])
    return [(a[1] * cr[i] + b[1] * c31[i] + c[1] * c12[i]) / det for i in range(3)]

def _solid_index(sides):
    """.vmf brushes by size: {(dx, dy, dz) rounded to 0.1: [(min corner, [sides of that brush])]}. A copy of a brush that was moved in Hammer keeps its size."""
    import itertools
    groups = {}
    for sd in sides:
        groups.setdefault(id(sd["solid"]), []).append(sd)
    out = {}
    for ss in groups.values():
        pl = ss[0]["solid"]
        if len(pl) < 4 or len(pl) > 24:
            continue
        vs = []
        for a, b, c in itertools.combinations(pl, 3):
            pt = _solve3(a, b, c)
            if pt and all(_v_dot(n, pt) - d <= 0.05 for n, d in pl):
                vs.append(pt)
        if not vs:
            continue
        lo = [min(v[k] for v in vs) for k in range(3)]
        hi = [max(v[k] for v in vs) for k in range(3)]
        out.setdefault(tuple(round(hi[k] - lo[k], 1) for k in range(3)), []).append((lo, ss))
    return out

def _moved_brush_targets(me, fcs, solid_idx):
    """Faces of this mesh that match no .vmf plane in place, but belong to a closed piece of geometry that is the same size as a .vmf brush:
    a copy that was moved in Hammer (texture lock kept the mapping, so shift = .vmf shift - translation.axis / scale). Each face found gets
    a synthetic target {ua, va, us, vs} for the moved position. -> number of faces matched"""
    opp, efi = me["edgeOppositeIndices"][2], me["edgeFaceIndices"][2]
    nf = len(me["faceEdgeIndices"][2])
    nxt = me["edgeNextIndices"][2]
    byface = {fc["fi"]: fc for fc in fcs if fc.get("pts")}
    nb = {}
    for fi, he in ((fc["fi"], me["faceEdgeIndices"][2][fc["fi"]]) for fc in byface.values()):
        h, s_ = he, set()
        for _ in range(64):
            oh = opp[h] if h < len(opp) else -1
            if 0 <= oh < len(efi):
                s_.add(efi[oh])
            h = nxt[h]
            if h == he:
                break
        nb[fi] = s_
    seen, found = set(), 0
    for f0, fc0 in byface.items():
        if f0 in seen or not fc0.get("nomatch"):
            continue
        comp, q = [], [f0]
        seen.add(f0)
        while q:
            f = q.pop()
            comp.append(f)
            for g in nb.get(f, ()):
                if g in byface and g not in seen and 0 <= g < nf:
                    seen.add(g)
                    q.append(g)
        allp = [pt for f in comp for pt in byface[f]["pts"]]
        lo = [min(pt[k] for pt in allp) for k in range(3)]
        hi = [max(pt[k] for pt in allp) for k in range(3)]
        dm = [hi[k] - lo[k] for k in range(3)]
        cands = []
        for dx in (-0.1, 0.0, 0.1):
            for dy in (-0.1, 0.0, 0.1):
                for dz in (-0.1, 0.0, 0.1):
                    cands += solid_idx.get((round(dm[0] + dx, 1), round(dm[1] + dy, 1), round(dm[2] + dz, 1)), [])
        best = None
        for slo, ss in cands:
            T = [lo[k] - slo[k] for k in range(3)]
            good = 0
            for f in comp:
                c_ = byface[f]
                q_ = [[pt[k] - T[k] for k in range(3)] for pt in c_["pts"]]
                if any(sd["mat"] == c_["key"] and abs(abs(_v_dot(sd["n"], c_["n"])) - 1) < 1e-3 and all(abs(_v_dot(sd["n"], x) - sd["d"]) < 0.6 for x in q_) for sd in ss):
                    good += 1
            if best is None or good > best[0]:
                best = (good, T, ss)
        if not best or best[0] < 0.9 * len(comp):
            continue
        T, ss = best[1], best[2]
        for f in comp:
            c_ = byface[f]
            if not c_.get("nomatch"):
                continue
            q_ = [[pt[k] - T[k] for k in range(3)] for pt in c_["pts"]]
            hit = [sd for sd in ss if sd["mat"] == c_["key"] and abs(abs(_v_dot(sd["n"], c_["n"])) - 1) < 1e-3 and all(abs(_v_dot(sd["n"], x) - sd["d"]) < 0.6 for x in q_)]
            if not hit or len({(tuple(sd["ua"]), tuple(sd["va"]), sd["us"], sd["vs"]) for sd in hit}) != 1:
                continue
            sd = hit[0]
            if not sd["us"] or not sd["vs"] or len(sd["ua"]) != 4 or len(sd["va"]) != 4:
                continue
            ua, va = list(sd["ua"]), list(sd["va"])
            ua[3] -= sum(T[k] * ua[k] for k in range(3)) / sd["us"]
            va[3] -= sum(T[k] * va[k] for k in range(3)) / sd["vs"]
            c_["tgt"] = dict(ua=ua, va=va, us=sd["us"], vs=sd["vs"])
            c_["nomatch"] = False
            found += 1
    return found

def _affine_fit2d(src, dst):
    """Least-squares 2D affine map src -> dst: ((a0, a1, a2), (b0, b1, b2)) with u' = a0*u + a1*v + a2, v' = b0*u + b1*v + b2; None if degenerate."""
    n = len(src)
    A = [[sum(s_[i] * s_[j] for s_ in src) for j in range(2)] + [sum(s_[i] for s_ in src)] for i in range(2)]
    A.append([sum(s_[j] for s_ in src) for j in range(2)] + [float(n)])
    out = []
    for k in (0, 1):
        M = [A[i][:] + [sum(d_[k] * s_[i] for s_, d_ in zip(src, dst)) if i < 2 else sum(d_[k] for d_ in dst)] for i in range(3)]
        for i in range(3):
            p = max(range(i, 3), key=lambda r: abs(M[r][i]))
            if abs(M[p][i]) < 1e-12:
                return None
            M[i], M[p] = M[p], M[i]
            for j in range(3):
                if j != i:
                    f_ = M[j][i] / M[i][i]
                    M[j] = [a_ - f_ * b_ for a_, b_ in zip(M[j], M[i])]
        out.append(tuple(M[i][3] / M[i][i] for i in range(3)))
    return tuple(out)

_OVERLAP = []        # faces on several .vmf brushes with different mappings: (material, vertices, scale, candidate scales)
_UNMATCHED = []      # (material, vertices, scale) of faces no .vmf side could be found for; read by the audit tooling


def fix_face_scales(vmap_paths, vmf_path: str, in_place=True):
    """Brush faces whose texture scale the importer got wrong (it does not read the Source1 textures): compare every CMapMesh face
    with the .vmf brush side on the same plane + material. Ratio = vmap scale / .vmf scale per axis; outside FACE_SCALE_BAD_RATIO the
    face is broken: scale := scale / ratio and shift := shift * ratio (the importer multiplies the shift by the same factor it divides
    the scale by). A material whose matched faces all share one broken ratio also gets it applied to its unmatched faces.
    Same-size float values only, patched in place with a backup. -> report lines"""
    lo, hi = FACE_SCALE_BAD_RATIO
    _scale_bad = lambda sc: min(abs(sc[0]), abs(sc[1])) < 0.01 or max(abs(sc[0]), abs(sc[1])) > 8      # noqa: E731  only absurd scales are repaired; deliberate ones (0.125, 2, 4...) stay
    lines = []
    try:
        sides = _vmf_sides(_vmf_tree(vmf_path)) if vmf_path else []
    except Exception as ex:
        return [f"# cannot read {vmf_path}: {ex}"]
    idx = {}           # material -> {floor(plane distance): [sides]}   (a face looks in the neighbouring buckets of +d and -d instead of scanning every side)
    _ts_memo = {}
    def _ts_of(mat_):
        if mat_ not in _ts_memo:
            _ts_memo[mat_] = _overlay_texture_size(mat_)
        return _ts_memo[mat_]
    _modal_scale = {}
    _all_idx = None      # every material's sides by plane, for a face whose own material has none
    _alias_by_base = None      # .vmf material file name -> materials, built on first use
    solid_idx = None   # built on first use: .vmf brushes by size (for copies that were moved in Hammer)
    disp_by_mat = {}   # displacement sides: their faces are the displaced grid, not on the flat base plane, so they cannot be matched by plane
    for sd in sides:
        idx.setdefault(sd["mat"], {}).setdefault(tuple(round(x, 3) for x in sd["n"]), {}).setdefault(math.floor(sd["d"]), []).append(sd)
        if sd.get("disp") and sd.get("bb"):
            disp_by_mat.setdefault(sd["mat"], []).append(sd)
    lines.append(f"# {len(sides)} brush sides in {os.path.basename(vmf_path) if vmf_path else '(no .vmf: only the texture coordinates are checked)'}. Columns: action, material, faces, detail. "
                 f"A face is broken when its scale is below 0.01 or above 8 AND vmap scale / vmf scale is outside {lo:g}..{hi:g}.")
    for vp in vmap_paths:
        try:
            data = bytearray(Path(vp).read_bytes())
            if b"encoding binary" not in bytes(data[:100]):
                continue
            els = read_binary_dmx(bytes(data))
        except Exception as ex:
            lines.append(f"# {os.path.basename(str(vp))}: {ex}")
            continue
        stat = __import__("collections").Counter()
        faces = []      # (mat, scale_off, uoff, voff, sc, u, v, ratio or None)
        subs = []       # displacement meshes: (first face, last face + 1, (file offset, values) of the subdivided surface's texture coordinates)
        for e in els:
            if e["type"] != "CMapMesh":
                continue
            a = e["attrs"]
            md = a.get("meshData")
            if not md or not isinstance(md[2], int):
                continue
            o = a["origin"][2] if "origin" in a else (0.0, 0.0, 0.0)
            ident = all(abs(x) < 1e-4 for x in (a["angles"][2] if "angles" in a else (0, 0, 0))) and \
                all(abs(x - 1) < 1e-4 for x in (a["scales"][2] if "scales" in a else (1, 1, 1)))
            me = els[md[2]]["attrs"]
            mats = me.get("materials", (0, 0, []))[2]
            fd, vd = me.get("faceData"), me.get("vertexData")
            if not fd or not vd or not all(k in me for k in ("faceEdgeIndices", "edgeVertexIndices", "edgeNextIndices", "vertexDataIndices")):
                continue
            st = {els[i]["name"].split(":")[0]: els[i] for i in els[fd[2]]["attrs"].get("streams", (0, 0, []))[2] if isinstance(i, int)}
            if not all(k in st for k in ("textureScale", "textureAxisU", "textureAxisV", "materialindex")):
                continue
            if not all("offs" in st[k] and "data" in st[k]["offs"] for k in ("textureScale", "textureAxisU", "textureAxisV")):
                continue
            pos = None
            for si in els[vd[2]]["attrs"].get("streams", (0, 0, []))[2]:
                if isinstance(si, int) and els[si]["name"].lower().startswith("position"):
                    pos = els[si]["attrs"]["data"][2]
            if not pos:
                continue
            tcs, tgs, nrs = None, None, None
            if me.get("faceVertexData") and "edgeVertexDataIndices" in me:
                for si in els[me["faceVertexData"][2]]["attrs"].get("streams", (0, 0, []))[2]:
                    if isinstance(si, int) and els[si]["name"].lower().startswith("texcoord") and "data" in els[si].get("offs", {}):
                        tcs = (els[si]["offs"]["data"], els[si]["attrs"]["data"][2])
                    elif isinstance(si, int) and els[si]["name"].lower().startswith("tangent") and "data" in els[si].get("offs", {}):
                        tgs = (els[si]["offs"]["data"], els[si]["attrs"]["data"][2])
                    elif isinstance(si, int) and els[si]["name"].lower().startswith("normal"):
                        nrs = els[si]["attrs"]["data"][2]
            evd = me["edgeVertexDataIndices"][2] if tcs else []
            evi, nxt, vdi = me["edgeVertexIndices"][2], me["edgeNextIndices"][2], me["vertexDataIndices"][2]
            scs, us, vs_, mis = (st[k]["attrs"]["data"][2] for k in ("textureScale", "textureAxisU", "textureAxisV", "materialindex"))
            _mesh_start = len(faces)
            _sub = None
            try:
                _sdi = me.get("subdivisionData")
                if _sdi and isinstance(_sdi[2], int):
                    for si in els[_sdi[2]]["attrs"].get("streams", (0, 0, []))[2]:
                        if isinstance(si, int) and els[si]["name"].lower().startswith("texcoord") and "data" in els[si].get("offs", {}):
                            _sub = (els[si]["offs"]["data"], els[si]["attrs"]["data"][2])
            except Exception:
                _sub = None
            _fdi = me["faceDataIndices"][2] if "faceDataIndices" in me else None
            for f, he in enumerate(me["faceEdgeIndices"][2]):
                fd_ = _fdi[f] if _fdi and f < len(_fdi) and 0 <= _fdi[f] else f      # face -> face data row (identity in every vmap seen so far)
                if fd_ >= len(scs) or not (0 <= mis[fd_] < len(mats)):
                    continue
                mat = mats[mis[fd_]].replace("\\", "/").lower()
                ratio, uvv, tgt = None, None, None
                pts, n, key, nomatch = None, None, None, False
                if ident:
                    pts, hs, h = [], [], he
                    for _ in range(64):
                        pts.append([pos[vdi[evi[h]]][k] + o[k] for k in range(3)])
                        hs.append(h)
                        h = nxt[h]
                        if h == he:
                            break
                    if tcs and len(pts) >= 3 and all(0 <= evd[x] < len(tcs[1]) for x in hs if x < len(evd)) and max(hs) < len(evd):
                        uvv = [(pts[i], evd[hs[i]]) for i in range(len(pts))]
                    if len(pts) >= 3 and sides:
                        n = _v_norm(_v_cross(_v_sub(pts[1], pts[0]), _v_sub(pts[2], pts[0])))
                        # a vertex that is a fraction of a unit off (the importer rounds some brush corners) tilts a triangle's normal by ~0.001,
                        # which moves "plane distance" by several units at map coordinates in the thousands: snap near-axis normals to the axis
                        # and measure the distance at the face centre instead of at one corner
                        for _k in range(3):
                            if abs(n[_k]) > 0.999:
                                n = [(1.0 if n[_k] > 0 else -1.0) if j == _k else 0.0 for j in range(3)]
                                break
                        _c0 = [sum(q[j] for q in pts) / len(pts) for j in range(3)]
                        key = mat[len("materials/"):-5] if mat.startswith("materials/") and mat.endswith(".vmat") else mat
                        if key not in idx:
                            # the material was renamed on its way into the vmap (a different folder, e.g. mytextures/stone-blocks for giza/stone-blocks):
                            # the .vmf's material with the same file name stands in for it, if there is exactly one
                            if _alias_by_base is None:
                                _alias_by_base = {}
                                for _k in idx:
                                    _alias_by_base.setdefault(_k.rsplit("/", 1)[-1], set()).add(_k)
                            _al = _alias_by_base.get(key.rsplit("/", 1)[-1], ())
                            if len(_al) == 1:
                                key = next(iter(_al))
                                stat["material renamed in the vmap, matched to the .vmf material with the same file name"] += 1
                        _bk = idx.get(key, {})
                        # a side matches when the normals agree and every vertex of the face lies on its plane (0.6 units: covers the importer's rounding).
                        # Sides are grouped by their own exact normal and looked up by the face centre's distance along that normal: a small tilt of a
                        # triangle's own normal would move its plane distance by metres at map coordinates in the thousands
                        cands = []
                        for _nk, _bb in _bk.items():
                            if abs(_v_dot(_nk, n)) <= 0.99:
                                continue
                            _fl = math.floor(_v_dot(_nk, _c0))
                            for b_ in (_fl - 1, _fl, _fl + 1):
                                for s_ in _bb.get(b_, ()):
                                    if all(abs(_v_dot(s_["n"], q) - s_["d"]) < 0.6 for q in pts):
                                        cands.append(s_)
                        if not cands:
                            # a sliver (a triangle a fraction of a unit thick, left over when the importer cut a brush into many pieces) has no usable
                            # normal of its own: a vertex that is 0.1 off tilts it by tens of degrees. Ignore the normal and look for any side of this
                            # material whose plane holds every vertex and whose brush contains the whole face
                            for _nk, _bb in _bk.items():
                                _fl = math.floor(_v_dot(_nk, _c0))
                                for b_ in (_fl - 1, _fl, _fl + 1):
                                    for s_ in _bb.get(b_, ()):
                                        if all(abs(_v_dot(s_["n"], q) - s_["d"]) < 0.6 for q in pts) and all(_v_dot(n_, q) - d_ <= 0.6 for n_, d_ in s_["solid"] for q in pts):
                                            cands.append(s_)
                            if cands:
                                stat["sliver triangle matched by its vertices instead of its own normal"] += 1
                        if not cands:
                            # vertices a little further off the plane (the importer rounds some corners of rotated brushes by 1-2 units): same lookup with a
                            # 2.5 unit tolerance, only where the normals agree and the whole face lies inside the brush
                            for _nk, _bb in _bk.items():
                                if abs(_v_dot(_nk, n)) <= 0.99:
                                    continue
                                _fl = math.floor(_v_dot(_nk, _c0))
                                for b_ in range(_fl - 3, _fl + 4):
                                    for s_ in _bb.get(b_, ()):
                                        if all(abs(_v_dot(s_["n"], q) - s_["d"]) < 2.5 for q in pts) and all(_v_dot(n_, q) - d_ <= 2.5 for n_, d_ in s_["solid"] for q in pts):
                                            cands.append(s_)
                            if cands:
                                stat["face a little off its .vmf plane (rounded corners) matched with a 2.5 unit tolerance"] += 1
                            if not cands:
                                # a whole flat face parallel to its .vmf plane but pushed out by up to ~6 units (chamfered / rounded pillars): take only the nearest
                                # such plane, and only if the face is flat (all vertices at one distance), nearly parallel, and inside the brush
                                _best = []
                                for _nk, _bb in _bk.items():
                                    if abs(_v_dot(_nk, n)) <= 0.999:
                                        continue
                                    _fl = math.floor(_v_dot(_nk, _c0))
                                    for b_ in range(_fl - 7, _fl + 8):
                                        for s_ in _bb.get(b_, ()):
                                            _ds = [_v_dot(s_["n"], q) - s_["d"] for q in pts]
                                            if max(_ds) - min(_ds) > 0.6 or max(abs(x) for x in _ds) >= 6.0:
                                                continue
                                            _tol = max(abs(x) for x in _ds) + 0.6
                                            if all(_v_dot(n_, q) - d_ <= _tol for n_, d_ in s_["solid"] for q in pts):
                                                _best.append((max(abs(x) for x in _ds), s_))
                                if _best:
                                    _m = min(x for x, _ in _best)
                                    cands = [s_ for x, s_ in _best if x <= _m + 0.5]
                                    stat["flat face pushed off its .vmf plane (up to 6 units) matched to the nearest plane"] += 1
                        if len(cands) > 1:
                            # brushes that overlap in this plane (adjacent blocks, stacked boxes): keep the one the face actually lies inside
                            cen = [sum(q[j] for q in pts) / len(pts) for j in range(3)]
                            cv = [c for c in cands if all(_v_dot(n_, q) - d_ <= 0.6 for n_, d_ in c["solid"] for q in pts)]      # the whole face inside the brush
                            if cv:
                                cands = cv
                            c0 = [c for c in cands if all(_v_dot(n_, cen) - d_ <= 0.6 for n_, d_ in c["solid"])]
                            if c0:
                                cands = c0
                        if len(cands) > 1:
                            # several brushes share this plane + material (e.g. crates rotated differently on one floor): keep the sides whose texture
                            # axes are the ones stored on this face, then those whose shift agrees with the stored one modulo 16 (the importer's reduction)
                            c1 = [c for c in cands if len(c["ua"]) == 4 and sum(abs(c["ua"][k] - us[fd_][k]) + abs(c["va"][k] - vs_[fd_][k]) for k in range(3)) < 0.02]
                            if c1:
                                cands = c1
                            if len(cands) > 1:
                                c2 = [c for c in cands if len(c["ua"]) == 4 and all(min((c[ax][3] - cur[3]) % 16.0, 16.0 - (c[ax][3] - cur[3]) % 16.0) < 0.1
                                                                                      for ax, cur in (("ua", us[fd_]), ("va", vs_[fd_])))]
                                if c2:
                                    cands = c2
                            if len(cands) > 1 and uvv:
                                # still several (e.g. neighbouring tiles that differ only in shift): the stored texture coordinates of a face the importer got
                                # right follow exactly one of them
                                _tsz = _ts_of(mat)
                                if _tsz:
                                    def _uverr(c):
                                        e_ = 0.0
                                        for (P_, ti_) in uvv:
                                            nu_ = (sum(P_[k] * c["ua"][k] for k in range(3)) / c["us"] + c["ua"][3]) / _tsz[0]
                                            nv_ = (sum(P_[k] * c["va"][k] for k in range(3)) / c["vs"] + c["va"][3]) / _tsz[1]
                                            du_, dv_ = (tcs[1][ti_][0] - nu_) % 1.0, (tcs[1][ti_][1] - nv_) % 1.0
                                            e_ = max(e_, min(du_, 1 - du_), min(dv_, 1 - dv_))
                                        return e_
                                    c3 = [c for c in cands if c["us"] and c["vs"] and len(c["ua"]) == 4 and len(c["va"]) == 4 and _uverr(c) < 0.004]
                                    if c3:
                                        cands = c3
                        if cands:
                            sd = min(cands, key=lambda c: abs(scs[fd_][0] / c["us"] - 1) + abs(scs[fd_][1] / c["vs"] - 1))      # coplanar sides: take the closest scale
                            if sd["us"] and sd["vs"]:
                                ratio = (scs[fd_][0] / sd["us"], scs[fd_][1] / sd["vs"])
                            # unambiguous: every coplanar side of this material maps the texture identically -> its mapping is the truth for this face
                            if len(sd["ua"]) == 4 and len(sd["va"]) == 4 and all(
                                    max(abs(c["ua"][k] - sd["ua"][k]) + abs(c["va"][k] - sd["va"][k]) for k in range(4)) < 0.01
                                    and abs(c["us"] - sd["us"]) < 1e-4 and abs(c["vs"] - sd["vs"]) < 1e-4 for c in cands):
                                tgt = sd
                        else:
                            # displacement: the face lies over a displacement brush of this material (its base block's footprint, any height)
                            # not only absurd scales: the importer also leaves displacement faces at a plausible-looking but wrong ~1/32 scale
                            _ds = [c for c in disp_by_mat.get(key, [])
                                   if abs(_v_dot(c["n"], n)) > 0.3 and all(
                                       c["bb"][0][j] - (16 if abs(c["n"][j]) < 0.5 else 256) <= _c0[j] <= c["bb"][1][j] + (16 if abs(c["n"][j]) < 0.5 else 256)
                                       for j in range(3))]
                            if len(_ds) > 1:      # several displacements stacked over this spot: the one whose base block is closest in height
                                _gap = lambda c: sum(max(c["bb"][0][j] - _c0[j], _c0[j] - c["bb"][1][j], 0.0) for j in range(3) if abs(c["n"][j]) >= 0.5)   # noqa: E731
                                _g0 = min(_gap(c) for c in _ds)
                                _ds = [c for c in _ds if _gap(c) <= _g0 + 24]
                            if len({(tuple(c["ua"]), tuple(c["va"]), c["us"], c["vs"]) for c in _ds}) == 1:
                                tgt = _ds[0]
                                stat["on a displacement (mapping copied from the displacement's base face)"] += 1
                            else:
                                stat["no .vmf brush on this plane + material"] += 1
                                nomatch = True
                        if cands and not tgt:
                            _OVERLAP.append((key, pts, tuple(scs[fd_]), sorted({(round(c["us"], 4), round(c["vs"], 4)) for c in cands})))
                            stat["several .vmf brushes with different mappings overlap this face (left as imported)"] += 1
                faces.append(dict(mat=mat, so=st["textureScale"]["offs"]["data"] + 8 * fd_, uo=st["textureAxisU"]["offs"]["data"] + 16 * fd_,
                                  vo=st["textureAxisV"]["offs"]["data"] + 16 * fd_, sc=scs[fd_], u=us[fd_], v=vs_[fd_], ratio=ratio, tgt=tgt,
                                  uv=uvv, tco=tcs[0] if tcs else None, tcd=tcs[1] if tcs else None, tgs=tgs, nrs=nrs,
                                  fi=f, pts=pts if ident else None, n=n, key=key, nomatch=nomatch))
            if _sub and ident and tcs and len(faces) > _mesh_start:
                subs.append((_mesh_start, len(faces), _sub, tuple(o)))
            if ident and sides and any(fc_["nomatch"] for fc_ in faces[_mesh_start:]) and all(k_ in me for k_ in ("edgeOppositeIndices", "edgeFaceIndices")):
                if solid_idx is None:
                    solid_idx = _solid_index(sides)
                _nm = _moved_brush_targets(me, faces[_mesh_start:], solid_idx)
                if _nm:
                    stat["on a copy of a .vmf brush that was moved in Hammer (mapping copied, shift corrected for the move)"] += _nm
                    stat["no .vmf brush on this plane + material"] -= _nm
            if ident and sides:
                # a sliver (under 1 unit across) with no .vmf side: nothing to copy, and the importer's scale for it is arbitrary (0.0006, 77...). It cannot
                # be seen, so give it the scale most of this material's .vmf sides use instead of leaving a number that looks broken in Hammer
                for fc_ in faces[_mesh_start:]:
                    if not fc_["nomatch"] or fc_["tgt"] or not fc_["pts"] or fc_["key"] not in idx:
                        continue
                    _p = fc_["pts"]
                    if len(_p) != 3:
                        continue
                    _cr = _v_cross(_v_sub(_p[1], _p[0]), _v_sub(_p[2], _p[0]))
                    _L = max(math.dist(_p[0], _p[1]), math.dist(_p[1], _p[2]), math.dist(_p[0], _p[2]))
                    if not _L or math.sqrt(sum(x * x for x in _cr)) / _L >= 1.0:
                        continue
                    if fc_["key"] not in _modal_scale:
                        _cn = __import__("collections").Counter((round(sd_["us"], 4), round(sd_["vs"], 4)) for _b in idx[fc_["key"]].values() for _l in _b.values() for sd_ in _l
                                                                if sd_["us"] and sd_["vs"] and not sd_.get("disp"))
                        _modal_scale[fc_["key"]] = (_cn.most_common(1)[0][0], list(_cn)) if _cn else None
                    if not _modal_scale[fc_["key"]]:
                        continue
                    _ms, _all = _modal_scale[fc_["key"]]
                    if any(abs(fc_["sc"][0] - a_) < 0.02 * a_ and abs(fc_["sc"][1] - b_) < 0.02 * b_ for a_, b_ in _all):
                        continue
                    fc_["tgt"] = dict(ua=list(fc_["u"]), va=list(fc_["v"]), us=_ms[0], vs=_ms[1])
                    fc_["nomatch"] = False
                    stat["sliver with no .vmf side given the material's usual scale"] += 1
                    stat["no .vmf brush on this plane + material"] -= 1
                # same geometry, different material (a water shader, a swapped texture) and an absurd scale: the .vmf face on this plane still
                # says how the original was mapped, so take its mapping rather than leave a scale like 0.002
                for fc_ in faces[_mesh_start:]:
                    if not fc_["nomatch"] or fc_["tgt"] or not fc_["pts"] or not _scale_bad(fc_["sc"]) or fc_["key"].startswith("tools/"):
                        continue
                    if _all_idx is None:
                        _all_idx = {}
                        for _k in idx:
                            if _k.startswith("tools/"):
                                continue
                            for _nkey, _bb_ in idx[_k].items():
                                for _b, _l in _bb_.items():
                                    _all_idx.setdefault(_nkey, {}).setdefault(_b, []).extend(sd_ for sd_ in _l if not sd_.get("disp"))
                    _pp, _nn = fc_["pts"], fc_["n"]
                    _cc = [sum(q[j] for q in _pp) / len(_pp) for j in range(3)]
                    _hit = []
                    for _tol in (0.6, 2.5):
                        for _nk, _bb_ in _all_idx.items():
                            if abs(_v_dot(_nk, _nn)) <= 0.99:
                                continue
                            _fl = math.floor(_v_dot(_nk, _cc))
                            for b_ in range(_fl - 3, _fl + 4):
                                for sd_ in _bb_.get(b_, ()):
                                    if len(sd_["ua"]) == 4 and len(sd_["va"]) == 4 and sd_["us"] and sd_["vs"] \
                                            and all(abs(_v_dot(sd_["n"], q) - sd_["d"]) < _tol for q in _pp) \
                                            and all(_v_dot(n_, q) - d_ <= _tol for n_, d_ in sd_["solid"] for q in _pp):
                                        _hit.append(sd_)
                        if _hit:
                            break
                    if _hit and len({(tuple(h_["ua"]), tuple(h_["va"]), h_["us"], h_["vs"]) for h_ in _hit}) == 1:
                        fc_["tgt"] = dict(ua=list(_hit[0]["ua"]), va=list(_hit[0]["va"]), us=_hit[0]["us"], vs=_hit[0]["vs"])
                        fc_["nomatch"] = False
                        stat["absurd scale on a face whose .vmf brush has a different material: mapping copied from that brush"] += 1
                        stat["no .vmf brush on this plane + material"] -= 1
                _UNMATCHED.extend((fc_["mat"], fc_["pts"], tuple(fc_["sc"])) for fc_ in faces[_mesh_start:] if fc_["nomatch"] and not fc_["tgt"])
        # a whole part of the level that was moved by the same amount in Hammer (a floor, a level): the faces left over are the translated copies
        # of .vmf geometry. Find the translations that many materials agree on, then re-test the leftover faces against the .vmf at that offset
        _left = [fc_ for fc_ in faces if fc_.get("nomatch") and not fc_["tgt"] and fc_.get("pts") and fc_["key"] in idx]
        if MATCH_MOVED_LEVELS and len(_left) >= 8:
            import itertools
            from collections import Counter as _C
            _votes, _vsum, _bymat = _C(), _C(), {}
            for fc_ in _left:
                _bymat.setdefault(fc_["key"], []).append(fc_)
            for k_, fl_ in _bymat.items():
                _ss = [sd_ for _b in idx[k_].values() for _l in _b.values() for sd_ in _l if sd_.get("bb")]
                if not _ss or len(_ss) > 400:
                    continue
                _W = {tuple(round(c_) for c_ in cc_) for sd_ in _ss for cc_ in itertools.product(*[(sd_["bb"][0][j], sd_["bb"][1][j]) for j in range(3)])}
                _V = {tuple(round(c_) for c_ in q_) for fc_ in fl_[:300] for q_ in fc_["pts"]}
                if len(_V) * len(_W) > 3_000_000:
                    continue
                _cn = _C()
                for v_ in _V:
                    for w_ in _W:
                        _cn[(v_[0] - w_[0], v_[1] - w_[1], v_[2] - w_[2])] += 1
                for t_, n_ in _cn.most_common(3):
                    if n_ >= 8 and t_ != (0, 0, 0):
                        _votes[t_] += 1
                        _vsum[t_] += n_
            _Ts = [t_ for t_, n_ in _vsum.most_common(6) if _votes[t_] >= 2 or n_ >= 40]
            _nt = 0
            for fc_ in _left:
                _hits = []
                for T_ in _Ts:
                    q_ = [[pt_[k] - T_[k] for k in range(3)] for pt_ in fc_["pts"]]
                    _bk2 = idx.get(fc_["key"], {})
                    for _nk, _bb in _bk2.items():
                        if abs(_v_dot(_nk, fc_["n"])) <= 0.99:
                            continue
                        _fl = math.floor(_v_dot(_nk, [sum(x_[j] for x_ in q_) / len(q_) for j in range(3)]))
                        for b_ in (_fl - 1, _fl, _fl + 1):
                            for sd_ in _bb.get(b_, ()):
                                if sd_["us"] and sd_["vs"] and len(sd_["ua"]) == 4 and len(sd_["va"]) == 4 \
                                        and all(abs(_v_dot(sd_["n"], x_) - sd_["d"]) < 0.6 for x_ in q_) \
                                        and all(_v_dot(n_, x_) - d_ <= 0.6 for n_, d_ in sd_["solid"] for x_ in q_):
                                    _hits.append((T_, sd_))
                if _hits and len({(h_[0], tuple(h_[1]["ua"]), tuple(h_[1]["va"]), h_[1]["us"], h_[1]["vs"]) for h_ in _hits}) == 1:
                    T_, sd_ = _hits[0]
                    ua_, va_ = list(sd_["ua"]), list(sd_["va"])
                    ua_[3] -= sum(T_[k] * ua_[k] for k in range(3)) / sd_["us"]
                    va_[3] -= sum(T_[k] * va_[k] for k in range(3)) / sd_["vs"]
                    fc_["tgt"] = dict(ua=ua_, va=va_, us=sd_["us"], vs=sd_["vs"])
                    fc_["nomatch"] = False
                    _nt += 1
            if _nt:
                stat["geometry moved as a whole in Hammer (offset " + "; ".join("%d %d %d" % t_ for t_ in _Ts) + "): mapping copied, shift corrected for the move"] += _nt
                stat["no .vmf brush on this plane + material"] -= _nt
        per = {}
        for fc in faces:
            per.setdefault(fc["mat"], []).append(fc)
        fixed = {}      # material -> [fixed by match, fixed by material factor, left alone, texture coordinates rewritten]
        uv_done, no_tex = set(), set()
        for mat, fl in per.items():
            matched = [fc["ratio"] for fc in fl if fc["ratio"]]
            bad = [fc["ratio"] for fc in fl if fc["ratio"] and _scale_bad(fc["sc"]) and not (lo <= fc["ratio"][0] <= hi and lo <= fc["ratio"][1] <= hi)]
            fac = None
            if matched and len(bad) >= 0.8 * len(matched):
                rr = sorted(bad)
                fac = rr[len(rr) // 2]
                if sum(1 for r in bad if abs(r[0] / fac[0] - 1) < 0.02 and abs(r[1] / fac[1] - 1) < 0.02) < 0.8 * len(bad):
                    fac = None
            cnt = fixed.setdefault(mat, [0, 0, 0, 0])
            ts_known = bool(_addon_texture_size(mat) or _overlay_texture_size(mat))
            ts = _addon_texture_size(mat) or _overlay_texture_size(mat)         # (width, height, decalscale): the converted addon texture first (what Hammer draws), then the Source1 one
            if not ts and not any(_scale_bad(fc["sc"]) for fc in fl):
                # no Source1 or addon texture: use the size the stored coordinates imply (what Hammer itself used for this material). Not done for
                # materials with broken scales: their stored coordinates may follow either the old or the corrected numbers, so a size inferred from them is a guess
                tv_ = [__import__("collections").Counter(), __import__("collections").Counter()]
                for fc in fl[:400]:
                    if not fc["uv"]:
                        continue
                    (p0, i0) = fc["uv"][0]
                    for ci, ax in ((0, fc["u"]), (1, fc["v"])):
                        for (p1, i1) in fc["uv"][1:]:
                            dp = sum((p1[k] - p0[k]) * ax[k] for k in range(3)) / fc["sc"][ci]
                            du = fc["tcd"][i1][ci] - fc["tcd"][i0][ci]
                            if abs(dp) > 2.0 and abs(du) > 1e-9:
                                tv_[ci][round(dp / du)] += 1
                                break
                if tv_[0] and tv_[1]:
                    (tu_, nu_), (tw_, nw_) = tv_[0].most_common(1)[0], tv_[1].most_common(1)[0]
                    if tu_ > 0 and tw_ > 0 and nu_ >= 0.6 * sum(tv_[0].values()) and nw_ >= 0.6 * sum(tv_[1].values()):
                        ts = (tu_, tw_, 1.0)
            for fc in fl:
                r = fc["ratio"]
                fix_scale = False
                if r and _scale_bad(fc["sc"]) and not (lo <= r[0] <= hi and lo <= r[1] <= hi):
                    fix_scale = True
                elif r and not (lo <= r[0] <= hi and lo <= r[1] <= hi) and all(
                        any(abs(math.log2(max(x, 1e-9)) - round(math.log2(max(x, 1e-9)))) < 0.06 and abs(round(math.log2(max(x, 1e-9)))) >= 3 for x in (r[0], r[1]))
                        for _ in (0,)) and fc["tgt"] is None:
                    # a scale that is not "absurd" (0.016) but exactly a power of two (8x to 512x) away from the one .vmf brush it sits on, with
                    # nothing in the .vmf to explain it: the importer's divide, not a deliberate change
                    fix_scale = True
                elif not r and fac and _scale_bad(fc["sc"]):
                    r, fix_scale = fac, True
                elif not r and _scale_bad(fc["sc"]):
                    cnt[2] += 1
                if fix_scale and (r[0] <= 0 or r[1] <= 0):
                    fix_scale = False
                sc2, u2, v2 = list(fc["sc"]), list(fc["u"]), list(fc["v"])
                tg = fc["tgt"]
                if tg and tg["us"] and tg["vs"] and (abs(fc["sc"][0] - tg["us"]) > 1e-4 * max(1.0, abs(tg["us"])) or abs(fc["sc"][1] - tg["vs"]) > 1e-4 * max(1.0, abs(tg["vs"]))
                                                     or any(abs(fc["u"][k] - tg["ua"][k]) > 0.01 or abs(fc["v"][k] - tg["va"][k]) > 0.01 for k in range(4))):
                    # the importer reduces the shift (to 0..16) and rounds the axes; the .vmf values are the original mapping
                    sc2, u2, v2 = [tg["us"], tg["vs"]], list(tg["ua"]), list(tg["va"])
                    struct.pack_into("<2f", data, fc["so"], *sc2)
                    struct.pack_into("<4f", data, fc["uo"], *u2)
                    struct.pack_into("<4f", data, fc["vo"], *v2)
                    cnt[0] += 1
                    fix_scale = True
                elif fix_scale:
                    sc2 = [fc["sc"][0] / r[0], fc["sc"][1] / r[1]]
                    u2[3], v2[3] = fc["u"][3] * r[0], fc["v"][3] * r[1]
                    struct.pack_into("<2f", data, fc["so"], *sc2)
                    struct.pack_into("<f", data, fc["uo"] + 12, u2[3])
                    struct.pack_into("<f", data, fc["vo"] + 12, v2[3])
                    cnt[0 if fc["ratio"] else 1] += 1
                # stored texture coordinates: Hammer draws these, not the scale/shift numbers (those only matter once a face is edited).
                # Source1 formula (verified on this vmap): u = (P.axisU / scaleU + shiftU) / texture width, P = world position
                if ts and fc["uv"] and not _scale_bad(sc2):
                    wrongs = fix_scale and ts_known      # a size inferred from the stored coordinates is only trusted to repair faces that disagree with their numbers
                    if not wrongs:
                        for (P, ti) in fc["uv"]:           # does every stored coordinate equal the formula (up to whole texture repeats)?
                            nu = (sum(P[k] * u2[k] for k in range(3)) / sc2[0] + u2[3]) / ts[0]
                            nv = (sum(P[k] * v2[k] for k in range(3)) / sc2[1] + v2[3]) / ts[1]
                            du, dv = (fc["tcd"][ti][0] - nu) % 1.0, (fc["tcd"][ti][1] - nv) % 1.0
                            if min(du, 1 - du) > 0.003 or min(dv, 1 - dv) > 0.003:
                                wrongs = True
                                break
                    if wrongs:
                        for (P, ti) in fc["uv"]:
                            if (fc["tco"], ti) in uv_done:
                                continue
                            uv_done.add((fc["tco"], ti))
                            nu = (sum(P[k] * u2[k] for k in range(3)) / sc2[0] + u2[3]) / ts[0]
                            nv = (sum(P[k] * v2[k] for k in range(3)) / sc2[1] + v2[3]) / ts[1]
                            struct.pack_into("<2f", data, fc["tco"] + 8 * ti, nu, nv)
                            fc.setdefault("newtc", {})[ti] = (nu, nv)
                        cnt[3] += 1
                elif fix_scale and not ts:
                    cnt[2] += 0
                    no_tex.add(mat)
        # tangents: Hammer stores one per face corner (the direction of increasing U across the face, w = +-1 handedness) and normal-mapped
        # materials are lit with them. They were left pointing along the OLD texture axis on every face whose coordinates were rewritten.
        # Hammer's rule, checked against 156,000 untouched face corners of the goldeneye vmap (99.0% match): T = the surface direction in which
        # U grows and V stays constant (from the face's positions and texture coordinates), projected perpendicular to the stored normal;
        # w = +1 when cross(normal, T) points the way V grows. A face is only touched when its OLD tangent followed that rule with its OLD
        # coordinates (displacements and other odd faces do not, and are left alone).
        n_tan = n_tan_skip = 0
        def _face_tangents(fc_, tc_of):
            out_ = {}
            P_ = [p_ for (p_, _t) in fc_["uv"]]
            UV_ = [tc_of(t_) for (_p, t_) in fc_["uv"]]
            for i_ in range(1, len(P_) - 1):
                e1_, e2_ = _v_sub(P_[i_], P_[0]), _v_sub(P_[i_ + 1], P_[0])
                du1, dv1, du2, dv2 = UV_[i_][0] - UV_[0][0], UV_[i_][1] - UV_[0][1], UV_[i_ + 1][0] - UV_[0][0], UV_[i_ + 1][1] - UV_[0][1]
                det = du1 * dv2 - du2 * dv1
                if abs(det) < 1e-12:
                    continue
                T_ = [(e1_[k] * dv2 - e2_[k] * dv1) / det for k in range(3)]
                B_ = [(e2_[k] * du1 - e1_[k] * du2) / det for k in range(3)]
                for (_p, t_) in fc_["uv"]:
                    nn_ = fc_["nrs"][t_]
                    ln_ = math.sqrt(_v_dot(nn_, nn_))
                    if ln_ < 1e-9:
                        continue
                    nn_ = [c / ln_ for c in nn_[:3]]
                    d_ = _v_dot(nn_, T_)
                    Tp = [T_[k] - nn_[k] * d_ for k in range(3)]
                    lt_ = math.sqrt(_v_dot(Tp, Tp))
                    if lt_ < 1e-9:
                        continue
                    Tp = [c / lt_ for c in Tp]
                    out_[t_] = (Tp, 1.0 if _v_dot(_v_cross(nn_, Tp), B_) > 0 else -1.0)
                return out_
            return out_
        for fc in faces:
            if not fc.get("newtc") or not fc.get("tgs") or not fc.get("nrs") or not fc["uv"]:
                continue
            try:
                old_ = _face_tangents(fc, lambda t_: fc["tcd"][t_])
                new_ = _face_tangents(fc, lambda t_: fc["newtc"].get(t_, tuple(fc["tcd"][t_][:2])))
                tgo, tgd = fc["tgs"]
                ok_ = bool(old_) and len(old_) == len(fc["uv"]) and len(new_) == len(old_) and all(
                    _v_dot(old_[t_][0], tgd[t_][:3]) > 0.999 and old_[t_][1] == tgd[t_][3] for t_ in old_)
                if not ok_:
                    n_tan_skip += 1
                    continue
                for t_, (T_, w_) in new_.items():
                    struct.pack_into("<4f", data, tgo + 16 * t_, T_[0], T_[1], T_[2], w_)
                n_tan += 1
            except Exception:
                n_tan_skip += 1
        if n_tan:
            stat["tangents recomputed to follow the rewritten texture coordinates"] += n_tan
        if n_tan_skip:
            stat["faces whose old tangent did not follow its coordinates (tangent left as is)"] += n_tan_skip
        # displacements keep their own copy of the texture coordinates for every vertex of the subdivided surface; Hammer draws these, so when the
        # corner coordinates of a base face changed they have to move with them: for each face the old -> new corner mapping is a 2D affine map
        n_sub = 0
        stale_disp = []
        for (_f0, _f1, (_so, _sv), _org) in subs:
            try:
                _fits = []
                for fc_ in faces[_f0:_f1]:
                    if not fc_["uv"] or not fc_.get("newtc"):
                        continue
                    _src = [tuple(fc_["tcd"][ti_][:2]) for (_P, ti_) in fc_["uv"]]
                    _dst = [fc_["newtc"].get(ti_, tuple(fc_["tcd"][ti_][:2])) for (_P, ti_) in fc_["uv"]]
                    if len(_src) < 3 or max(abs(a_[0] - b_[0]) + abs(a_[1] - b_[1]) for a_, b_ in zip(_src, _dst)) < 1e-4:
                        continue
                    _fit = _affine_fit2d(_src, _dst)
                    if _fit:
                        _us, _vs = [q[0] for q in _src], [q[1] for q in _src]
                        _fits.append((_fit, min(_us), max(_us), min(_vs), max(_vs)))
                for i_, (u_, v_) in enumerate(_sv if _fits else ()):
                    _best = None
                    for (_fit, u0, u1, v0, v1) in _fits:
                        mu_, mv_ = 0.02 * (u1 - u0) + 1e-3, 0.02 * (v1 - v0) + 1e-3
                        if u0 - mu_ <= u_ <= u1 + mu_ and v0 - mv_ <= v_ <= v1 + mv_:
                            _area = (u1 - u0) * (v1 - v0)
                            if _best is None or _area < _best[0]:
                                _best = (_area, _fit)
                    if _best:
                        (a0, a1, a2), (b0, b1, b2) = _best[1]
                        struct.pack_into("<2f", data, _so + 8 * i_, a0 * u_ + a1 * v_ + a2, b0 * u_ + b1 * v_ + b2)
                        n_sub += 1
                # entries that still lie outside every face's corner range were not matched to a face: the table is out of date for this mesh
                _boxes = []
                for fc_ in faces[_f0:_f1]:
                    if fc_["uv"]:
                        _c = [fc_.get("newtc", {}).get(ti_, tuple(fc_["tcd"][ti_][:2])) for (_P, ti_) in fc_["uv"]]
                        _du, _dv = max(q[0] for q in _c) - min(q[0] for q in _c), max(q[1] for q in _c) - min(q[1] for q in _c)
                        _boxes.append((min(q[0] for q in _c) - 0.02 * _du - 1e-3, max(q[0] for q in _c) + 0.02 * _du + 1e-3,
                                       min(q[1] for q in _c) - 0.02 * _dv - 1e-3, max(q[1] for q in _c) + 0.02 * _dv + 1e-3))
                if _boxes:
                    _cur = struct.unpack_from("<%df" % (2 * len(_sv)), data, _so)
                    _out = sum(1 for i_ in range(len(_sv)) if not any(b0 <= _cur[2 * i_] <= b1 and c0 <= _cur[2 * i_ + 1] <= c1 for (b0, b1, c0, c1) in _boxes))
                    if _out > 0.25 * len(_sv):
                        stale_disp.append(_org)
            except Exception as ex_:
                lines.append(f"# displacement texture coordinates not updated for one mesh: {ex_}")
        if n_sub:
            stat["displacement surface texture coordinates moved with their base face"] += n_sub
        if stale_disp:
            lines.append(f"# {len(stale_disp)} displacement mesh(es) still have an out-of-date subdivided-surface texture table (origins): "
                         + "; ".join("%.0f %.0f %.0f" % o_ for o_ in stale_disp))
            print(f"  [NOTE] {len(stale_disp)} displacement mesh(es) look unfinished in Hammer (stale subdivision texture table, usually from a map fixed on an earlier run). "
                  "Select them and nudge one step and back to rebuild it. Origins: " + "; ".join("%.0f %.0f %.0f" % o_ for o_ in stale_disp[:12]))
        if no_tex:
            print("  [NOTE] face scales: no texture size known for " + ", ".join(sorted(no_tex)[:6]) + ": their texture coordinates were NOT rewritten (see the face_scale_fix log)")
        total = sum(c[0] + c[1] + c[3] for c in fixed.values())
        _tg = sum(1 for fc in faces if fc["tgt"])
        lines.append(f"# {os.path.basename(str(vp))}: {len(faces)} brush faces, {_tg} matched to exactly one .vmf mapping" +
                     "".join(f"; {_n} faces: {_k}" for _k, _n in stat.items()))
        print(f"  face scales: {_tg} of {len(faces)} brush faces matched to the .vmf" + "".join(f"; {_n} {_k}" for _k, _n in stat.items()))
        for mat, (a_, b_, c_, d_) in sorted(fixed.items()):
            if a_ or b_ or c_ or d_:
                if a_ or b_ or d_:
                    lines.append(f"FIXED\t{mat}\t{a_ + b_} faces re-scaled ({a_} matched to the .vmf, {b_} by the material's common factor), {d_} faces with texture coordinates rewritten"
                                 + (f"; {c_} out-of-range faces without a .vmf match left alone" if c_ else "")
                                 + ("; no texture size known, so its texture coordinates could NOT be rewritten" if mat in no_tex else ""))
                else:
                    lines.append(f"NOT FIXED\t{mat}\t{c_} faces\tscale out of range (<0.01 or >8) but no .vmf side matches the face")
        if total and in_place:
            bak = _backup_vmap(vp, "scalefix")
            if not os.path.isfile(bak) or os.path.getsize(bak) != os.path.getsize(str(vp)):
                lines.append(f"# BACKUP FAILED ({bak}); {vp} was NOT patched")
                continue
            Path(vp).write_bytes(bytes(data))
            lines.append(f"# {total} face(s) patched (scale/shift and/or texture coordinates) IN PLACE in {vp}; original saved as {bak}. Reopen the map in Hammer.")
            print(f"  face scales: patched {total} faces in {vp}\n    backup of the original: {bak}")
        elif total:
            lines.append(f"# {total} face(s) would be fixed (FACE_SCALE_FIX in place is off)")
        else:
            lines.append(f"# {os.path.basename(str(vp))}: no broken face scales found (nothing patched, no backup)")
            print(f"  face scales: no broken face scales in {os.path.basename(str(vp))}")
    return lines

def report_absurd_scale_faces(vmap_paths, vmf_path: str):
    """Every brush face whose texture scale is still out of range (<0.01 or >8) after the repair, with what the Source1 files say about its
    material: the .vmf's scales for that material, the .vmt's $basetexture and texture size. A face whose scale equals one the .vmf
    really uses for that material is legitimate (deliberate stretched scales); the rest are broken, and a material whose .vmf sides all
    share one scale tells you what these faces should be. Needs no extra files. -> report lines (also lists every face's position)."""
    import collections
    lines = []
    try:
        sides = _vmf_sides(_vmf_tree(vmf_path)) if vmf_path else []
    except Exception:
        sides = []
    bymat = collections.defaultdict(list)
    for sd in sides:
        bymat[sd["mat"]].append(sd)
    rows = []        # (material, mesh label, centroid, scale, shift)
    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
            if b"encoding binary" not in data[:100]:
                continue
            els = read_binary_dmx(data)
        except Exception:
            continue
        par = {}
        for i, e in enumerate(els):
            ch = e["attrs"].get("children")
            if ch and isinstance(ch[2], list):
                for c in ch[2]:
                    if isinstance(c, int):
                        par[c] = i
        for ei, e in enumerate(els):
            if e["type"] != "CMapMesh":
                continue
            a = e["attrs"]
            md = a.get("meshData")
            if not md or not isinstance(md[2], int):
                continue
            o = a["origin"][2] if "origin" in a else (0.0, 0.0, 0.0)
            me = els[md[2]]["attrs"]
            mats = me.get("materials", (0, 0, []))[2]
            fd, vd = me.get("faceData"), me.get("vertexData")
            if not fd or not vd:
                continue
            st = {els[i]["name"].split(":")[0]: els[i] for i in els[fd[2]]["attrs"].get("streams", (0, 0, []))[2] if isinstance(i, int)}
            if not all(k in st for k in ("textureScale", "textureAxisU", "textureAxisV", "materialindex")):
                continue
            pos = None
            for si in els[vd[2]]["attrs"].get("streams", (0, 0, []))[2]:
                if isinstance(si, int) and els[si]["name"].lower().startswith("position"):
                    pos = els[si]["attrs"]["data"][2]
            if not pos:
                continue
            evi, nxt, vdi = me["edgeVertexIndices"][2], me["edgeNextIndices"][2], me["vertexDataIndices"][2]
            scs, us, vs_, mis = (st[k]["attrs"]["data"][2] for k in ("textureScale", "textureAxisU", "textureAxisV", "materialindex"))
            owner, p_ = "world", par.get(ei)
            while p_ is not None:
                if els[p_]["type"] == "CMapEntity":
                    ep = els[p_]["attrs"].get("entity_properties")
                    cl = els[ep[2]]["attrs"].get("classname", (0, 0, ""))[2] if ep and isinstance(ep[2], int) and ep[2] >= 0 else ""
                    owner = ("entity " + cl) if cl else "entity"
                    break
                p_ = par.get(p_)
            if owner.startswith("entity trigger_"):
                continue                         # triggers are never drawn: their texture scale does not matter
            for f, he in enumerate(me["faceEdgeIndices"][2]):
                if f >= len(scs) or not (0 <= mis[f] < len(mats)):
                    continue
                sc = scs[f]
                if not (min(abs(sc[0]), abs(sc[1])) < 0.01 or max(abs(sc[0]), abs(sc[1])) > 8):
                    continue
                mat = mats[mis[f]].replace("\\", "/").lower()
                if mat.startswith("materials/tools"):
                    continue
                pts, h = [], he
                for _ in range(64):
                    pts.append([pos[vdi[evi[h]]][k] + o[k] for k in range(3)])
                    h = nxt[h]
                    if h == he:
                        break
                cen = [sum(q[j] for q in pts) / len(pts) for j in range(3)]
                key = mat[len("materials/"):-5] if mat.startswith("materials/") and mat.endswith(".vmat") else mat
                rows.append((key, owner, cen, tuple(sc), (us[f][3], vs_[f][3])))
    if not rows:
        return ["# no brush faces with an out-of-range texture scale remain"]
    permat = collections.defaultdict(list)
    for r in rows:
        permat[r[0]].append(r)
    legit_total = 0
    summary, perface = [], []
    for key, rs in sorted(permat.items(), key=lambda kv: -len(kv[1])):
        vsd = bymat.get(key, [])
        vsc = collections.Counter((round(sd["us"], 4), round(sd["vs"], 4)) for sd in vsd)
        info = ""
        try:
            vp_, _r = find_in_source1(key + ".vmt")
            if vp_:
                dd = parse_vmt(Path(vp_))
                vtf = resolve_vtf(dd["basetexture"]) if dd.get("basetexture") else None
                hd = parse_vtf_header(vtf) if vtf else None
                info = f"vmt {os.path.relpath(vp_, SOURCE1_ROOTS[0]) if SOURCE1_ROOTS and str(vp_).startswith(SOURCE1_ROOTS[0]) else vp_}; $basetexture {dd.get('basetexture')}" + (f" ({hd['width']}x{hd['height']})" if hd else "")
            else:
                info = "no .vmt found in the Source1 folders"
        except Exception:
            info = "vmt lookup failed"
        nlegit = 0
        for r in rs:
            legit = any(abs(r[3][0] - a_) <= 0.01 * max(abs(a_), 1e-6) + 1e-4 and abs(r[3][1] - b_) <= 0.01 * max(abs(b_), 1e-6) + 1e-4 for (a_, b_) in vsc)
            nlegit += legit
            perface.append(f"{'SAME AS .VMF' if legit else 'BROKEN'}\t{key}\t{r[1]}\t{r[2][0]:.0f} {r[2][1]:.0f} {r[2][2]:.0f}\t{r[3][0]:.5g} x {r[3][1]:.5g}\tshift {r[4][0]:.1f} {r[4][1]:.1f}")
        legit_total += nlegit
        nbad = len(rs) - nlegit
        dom = vsc.most_common(1)[0] if vsc else None
        if nbad == 0:
            verdict = "all faces use a scale the .vmf also has for this material: legitimate, not broken"
        elif dom and dom[1] >= 0.9 * len(vsd):
            verdict = f"{nbad} broken; every .vmf side of this material uses {dom[0][0]:g} x {dom[0][1]:g}, so that is what they should be"
        elif dom:
            verdict = f"{nbad} broken; the .vmf uses {', '.join(f'{a_:g}x{b_:g} ({n})' for (a_, b_), n in vsc.most_common(3))} for this material (no single answer)"
        else:
            verdict = f"{nbad} broken; material not in the .vmf"
        sample = min((r for r in rs), key=lambda r: min(abs(r[3][0]), abs(r[3][1])))
        summary.append(f"{len(rs)}\t{key}\t{verdict}\tvmap scale e.g. {sample[3][0]:.5g} x {sample[3][1]:.5g}\t{info}")
    lines.append(f"# {len(rows)} brush faces still have a texture scale below 0.01 or above 8 (tools/ materials excluded): {legit_total} equal a scale the .vmf also uses for that material (legitimate), {len(rows) - legit_total} look broken.")
    lines.append("# Per material: faces, material, verdict from the .vmf scales of that material, an example vmap scale, the Source1 .vmt and texture size")
    lines += summary
    lines.append("")
    lines.append("# Every face: verdict, material, owner (world / entity classname), centre x y z, vmap scale, vmap shift")
    lines += perface
    return lines

def find_same_name_elsewhere(missing, per_asset=3):
    """For each missing asset, files with the same name (any folder, any extension) in the Source1 folders and in CS2: a wrong folder or
    extension in the reference is the usual reason something is 'missing'. -> {asset: ['Source1: path', 'CS2: path']}"""
    want = {}
    for a in missing:
        stem = os.path.splitext(os.path.basename(a.lower()))[0]
        if stem:
            want.setdefault(stem, []).append(a)
    found = {a: [] for a in missing}

    def add(stem, label, path):
        for a in want.get(stem, ()):
            if len(found[a]) < per_asset and path.lower() != a.lower() and not any(path in x for x in found[a]):
                found[a].append(f"{label}: {path}")
    try:
        c = cs2_vpk()
        if c.loaded:
            for f in c.files:
                st = os.path.splitext(f.rsplit("/", 1)[-1])[0]
                if st in want:
                    add(st, "CS2", f)
    except Exception:
        pass
    for root in SOURCE1_ROOTS:
        for sub in ("materials", "models", "sound", "sounds", "particles"):
            try:
                base = find_ci(Path(root), sub)
            except Exception:
                base = None
            if base is None or not Path(base).is_dir():
                continue
            for dp, _dn, fn in os.walk(base):
                for f in fn:
                    st = os.path.splitext(f)[0].lower()
                    if st in want:
                        add(st, "Source1", os.path.relpath(os.path.join(dp, f), root).replace("\\", "/"))
    return found

# ---- static overlays that sit on brush ENTITIES -> parented translucent quads (CS2 static overlays do not project onto entities) ----

class _DmxEl:
    __slots__ = ("type", "name", "guid", "attrs", "offs")
    def __init__(self, type_, name, guid):
        self.type, self.name, self.guid, self.attrs, self.offs = type_, name, guid, [], None
    def attr(self, name):
        for a_ in self.attrs:
            if a_[0] == name:
                return a_
        return None
    def get(self, name, default=None):
        a_ = self.attr(name)
        return default if a_ is None else a_[2]
    def set(self, name, t, v):
        a_ = self.attr(name)
        if a_:
            a_[1], a_[2] = t, v
        else:
            self.attrs.append([name, t, v])

_DMX_FMT = {2: "<i", 3: "<f", 4: "<B", 7: "<i", 8: "<4B", 9: "<2f", 10: "<3f", 11: "<4f", 12: "<3f", 13: "<4f", 14: "<16f", 15: "<Q", 16: "<B"}

class _DmxDoc:
    """Binary DMX 9 (CS2 .vmap) reader/writer. load()+save() reproduces the original bytes (checked on the jacs / goldeneye /
    tomgreens maps). Element references are objects; new strings are appended to the string table; element order is kept.
    offsets=True also records, per element, the byte offset of each attribute value (e.offs; arrays: the first item)."""
    def __init__(self, data: bytes, offsets=False):
        d = data
        self.head = d[:d.index(b"\n") + 2]
        pos = len(self.head)
        u32 = lambda p_: struct.unpack_from("<I", d, p_)[0]           # noqa: E731
        def cstr(p_):
            e_ = d.index(b"\0", p_)
            return d[p_:e_].decode("utf-8", "surrogateescape"), e_ + 1
        def skip(bt, p_):
            if bt == 5:
                return cstr(p_)[1]
            if bt == 6:
                return p_ + 4 + u32(p_)
            return p_ + struct.calcsize(_DMX_FMT[bt]) if bt != 1 else p_ + 4
        p0 = pos
        npre = u32(pos); pos += 4
        for _ in range(npre):
            na = u32(pos); pos += 4
            for _ in range(na):
                _n, pos = cstr(pos)
                t = d[pos]; pos += 1
                if t >= 32:
                    c = u32(pos); pos += 4
                    for _ in range(c):
                        pos = skip(t - 32, pos)
                else:
                    pos = skip(t, pos)
        self.prefix = d[p0:pos]
        nstr = u32(pos); pos += 4
        self.strings = []
        for _ in range(nstr):
            s_, pos = cstr(pos)
            self.strings.append(s_)
        nel = u32(pos); pos += 4
        self.els = []
        for _ in range(nel):
            ti, ni = struct.unpack_from("<ii", d, pos)
            self.els.append(_DmxEl(self.strings[ti], self.strings[ni] if ni >= 0 else "", d[pos + 8:pos + 24]))
            pos += 24
        for e in self.els:
            na = u32(pos); pos += 4
            for _ in range(na):
                name = self.strings[u32(pos)]; pos += 4
                t = d[pos]; pos += 1
                arr = t >= 32
                bt = t - 32 if arr else t
                def one(p_, inline):
                    if bt == 1:
                        i_ = struct.unpack_from("<i", d, p_)[0]
                        return (self.els[i_] if i_ >= 0 else None), p_ + 4
                    if bt == 5:
                        if inline:
                            return cstr(p_)
                        return self.strings[u32(p_)], p_ + 4
                    if bt == 6:
                        n_ = u32(p_)
                        return d[p_ + 4:p_ + 4 + n_], p_ + 4 + n_
                    f_ = _DMX_FMT[bt]
                    v_ = struct.unpack_from(f_, d, p_)
                    return (v_[0] if len(v_) == 1 else v_), p_ + struct.calcsize(f_)
                if arr:
                    c = u32(pos); pos += 4
                    if offsets:
                        e.offs = e.offs or {}
                        e.offs[name] = pos
                    v = []
                    for _ in range(c):
                        x, pos = one(pos, True)
                        v.append(x)
                else:
                    if offsets:
                        e.offs = e.offs or {}
                        e.offs[name] = pos
                    v, pos = one(pos, False)
                e.attrs.append([name, t, v])
        if pos != len(d):
            raise ValueError(f"DMX parse ended at {pos} of {len(d)} bytes")
        self.root = self.els[0]

    def reachable(self):
        seen, st = set(), [self.root]
        while st:
            e = st.pop()
            if e is None or id(e) in seen:
                continue
            seen.add(id(e))
            for _n, t, v in e.attrs:
                if t == 1:
                    st.append(v)
                elif t == 33:
                    st.extend(v)
        return seen

    def save_bytes(self) -> bytes:
        idx = {id(e): i_ for i_, e in enumerate(self.els)}
        sidx = {s_: i_ for i_, s_ in enumerate(self.strings)}
        strings = list(self.strings)
        def S(s_):
            if s_ not in sidx:
                sidx[s_] = len(strings); strings.append(s_)
            return sidx[s_]
        for e in self.els:
            S(e.type); S(e.name)
            for n_, t, v in e.attrs:
                S(n_)
                if t == 5:
                    S(v)
        def enc(t, v, inline):
            if t & 32:
                return struct.pack("<I", len(v)) + b"".join(enc(t - 32, x, True) for x in v)
            if t == 1:
                return struct.pack("<i", -1 if v is None else idx[id(v)])
            if t == 5:
                return v.encode("utf-8", "surrogateescape") + b"\0" if inline else struct.pack("<I", sidx[v])
            if t == 6:
                return struct.pack("<I", len(v)) + v
            f_ = _DMX_FMT[t]
            return struct.pack(f_, *(v if isinstance(v, (tuple, list)) else (v,)))
        out = bytearray(self.head) + self.prefix + struct.pack("<I", len(strings))
        for s_ in strings:
            out += s_.encode("utf-8", "surrogateescape") + b"\0"
        out += struct.pack("<I", len(self.els))
        for e in self.els:
            out += struct.pack("<ii", sidx[e.type], sidx[e.name]) + e.guid
        for e in self.els:
            out += struct.pack("<I", len(e.attrs))
            for n_, t, v in e.attrs:
                out += struct.pack("<I", sidx[n_]) + bytes([t]) + enc(t, v, False)
        return bytes(out)

def _dmx_clone(e, memo):
    if e is None:
        return None
    if id(e) in memo:
        return memo[id(e)]
    n = _DmxEl(e.type, e.name, bytes(random.getrandbits(8) for _ in range(16)))
    memo[id(e)] = n
    for name, t, v in e.attrs:
        if t == 1:
            v = _dmx_clone(v, memo)
        elif t == 33:
            v = [_dmx_clone(x, memo) for x in v]
        elif isinstance(v, list):
            v = list(v)
        n.attrs.append([name, t, v])
    return n

def _qrot(ang, v):
    """Source/CS2 pitch-yaw-roll rotation of v (roll about X, then pitch about Y, then yaw about Z)."""
    p, y, r = (math.radians(x) for x in ang)
    x, yv, z = v
    cr, sr = math.cos(r), math.sin(r); yv, z = yv * cr - z * sr, yv * sr + z * cr
    cp, sp = math.cos(p), math.sin(p); x, z = x * cp + z * sp, -x * sp + z * cp
    cy, sy = math.cos(y), math.sin(y); x, yv = x * cy - yv * sy, x * sy + yv * cy
    return (x, yv, z)

def _node_world(node, pts, parent_of):
    """node origin/angles/scales applied to local points, then the transforms of the CMapGroup(s) it sits in. Children of a
    brush ENTITY are stored in world space (their own origin is already the world position), so an entity's transform is not applied."""
    def one(nd, ps):
        o, a, s = nd.get("origin") or (0, 0, 0), nd.get("angles") or (0, 0, 0), nd.get("scales") or (1, 1, 1)
        return [tuple(c + o[i] for i, c in enumerate(_qrot(a, (v[0] * s[0], v[1] * s[1], v[2] * s[2])))) for v in ps]
    pts = one(node, pts)
    p = parent_of.get(id(node))
    while p is not None and p.type == "CMapGroup":
        pts = one(p, pts)
        p = parent_of.get(id(p))
    return pts

def _mesh_polys(mesh):
    """-> [(local polygon [(x,y,z)], material path)] for a CMapMesh element."""
    md = mesh.get("meshData")
    if md is None:
        return []
    pos = None
    for s_ in md.get("vertexData").get("streams"):
        if s_.name.startswith("position"):
            pos = s_.get("data")
    mats = md.get("materials") or []
    mi = None
    fd = md.get("faceData")
    if fd is not None:
        for s_ in fd.get("streams"):
            if s_.name.startswith("materialindex"):
                mi = s_.get("data")
    fe, nx, ev, vdi = md.get("faceEdgeIndices"), md.get("edgeNextIndices"), md.get("edgeVertexIndices"), md.get("vertexDataIndices")
    fdi = md.get("faceDataIndices")
    out = []
    if pos is None or fe is None or nx is None or ev is None or vdi is None:
        return out
    for f, he0 in enumerate(fe):
        poly, he = [], he0
        for _ in range(64):
            poly.append(pos[vdi[ev[he]]])
            he = nx[he]
            if he == he0:
                break
        fi = fdi[f] if fdi and f < len(fdi) and fdi[f] >= 0 else f
        m_ = mats[mi[fi]] if mi and 0 <= fi < len(mi) and 0 <= mi[fi] < len(mats) else (mats[0] if mats else "")
        if len(poly) >= 3:
            out.append((poly, str(m_).replace("\\", "/").lower()))
    return out

def _poly_normal(pts):
    nx = ny = nz = 0.0
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        nx += (p[1] - q[1]) * (p[2] + q[2]); ny += (p[2] - q[2]) * (p[0] + q[0]); nz += (p[0] - q[0]) * (p[1] + q[1])
    l_ = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / l_, ny / l_, nz / l_) if l_ > 1e-9 else None

def _overlap_2d(rect, poly, eps=0.5):
    """rect = (x0, y0, x1, y1), poly = convex [(x, y)] -> True when they overlap by more than eps on every separating axis."""
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        ex, ey = q[0] - p[0], q[1] - p[1]
        l_ = math.hypot(ex, ey)
        if l_ > 1e-9:
            axes.append((-ey / l_, ex / l_))
    rc = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
    for ax in axes:
        a = [ax[0] * x + ax[1] * y for x, y in rc]
        b = [ax[0] * x + ax[1] * y for x, y in poly]
        if min(max(a), max(b)) - max(min(a), min(b)) <= eps:
            return False
    return True

OVERLAY_ONLY_ATTRS = ("projectionTargets", "renderOrder", "disabledInLowQuality", "useBaseNormals", "projectionFar",
                      "MaterialAdjustmentParamsStruct", "projectOnBackFaces", "backFacingAngle", "projectionMode")

def _lz4_block(src: bytes, out_size: int) -> bytes:
    dst = bytearray(); i = 0; n = len(src)
    while i < n:
        tok = src[i]; i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1; lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]; i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]; i += 1; ml += b
                if b != 255:
                    break
        ml += 4
        st = len(dst) - off
        if off >= ml:
            dst += dst[st:st + ml]
        else:
            for k in range(ml):
                dst.append(dst[st + k])
    return bytes(dst[:out_size])

def decode_vtex_c(data: bytes):
    """CS2 compiled texture (.vtex_c) -> PIL RGBA image of mip 0. LZ4 / uncompressed mips; DXT1, DXT5, BC4, BC5, BC7, RGBA8888, BGRA8888.
    Raises ValueError on anything else. Adapted from skink52/cs2mapping create/vtexread.py (MIT), after ValveResourceFormat's Texture.cs."""
    import io
    import numpy as np
    block = {1: 8, 2: 16, 20: 16, 21: 16, 27: 8}
    def mip_size(fmt, w, h):
        if fmt in block:
            return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block[fmt]
        if fmt in (4, 28):
            return w * h * 4
        raise ValueError(f"texture format {fmt} not supported")
    _size, _hv, _ver, boff, bcount = struct.unpack("<IHHII", data[:16])
    blocks = {}
    for k in range(bcount):
        p = 8 + boff + k * 12
        t, o, sz = struct.unpack_from("<4sII", data, p)
        blocks[t] = (p + 4 + o, sz)
    off, sz = blocks[b"DATA"]
    w, h, depth, fmt, nmip = struct.unpack_from("<HHHBB", data, off + 20)
    eoff, ecount = struct.unpack_from("<II", data, off + 32)
    comp, method = None, 0
    p = off + 32 + eoff
    for k in range(ecount):
        t, o, _s = struct.unpack_from("<III", data, p + k * 12)
        if t == 4:
            q = p + k * 12 + 4 + o
            method, moff, mips = struct.unpack_from("<III", data, q)
            comp = struct.unpack_from("<%di" % mips, data, q + 4 + moff)
    if method == 2:
        raise ValueError("zstd-compressed mips not supported")
    pos = off + sz
    for j in range(nmip - 1, 0, -1):
        ms = mip_size(fmt, max(1, w >> j), max(1, h >> j)) * max(1, depth)
        if comp is not None:
            ms = min(ms, comp[j])
        pos += ms
    full = mip_size(fmt, w, h)
    raw = _lz4_block(data[pos:pos + comp[0]], full) if comp is not None and comp[0] < full else data[pos:pos + full]
    if fmt in (4, 28):
        a = np.frombuffer(raw, np.uint8).reshape(h, w, 4)
        if fmt == 28:
            a = a[..., [2, 1, 0, 3]]
        return Image.fromarray(a.copy(), "RGBA")
    fourcc = {1: b"DXT1", 2: b"DXT5"}.get(fmt, b"DX10")
    hdr = struct.pack("<4sI6I44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, h, w, len(raw), 0, 1)
    pf = struct.pack("<II4s5I", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    out = hdr + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    if fourcc == b"DX10":
        out += struct.pack("<5I", {20: 98, 21: 83, 27: 80}[fmt], 3, 0, 1, 0)
    return Image.open(io.BytesIO(out + raw)).convert("RGBA")

def _cs2_overlay_textures(mat: str):
    """Overlay material shipped with CS2 (not in the addon): the compiled .vmat_c lists the textures it uses as plain strings
    (external reference table). -> (color .vtex, translucency .vtex or None, [every .vtex found]) or None when CS2 / the material
    cannot be read. Paths come back without '_c'."""
    try:
        v = cs2_vpk()
        if not v.loaded:
            return None
        base = mat.replace("\\", "/").lower()
        data = v.read((base if base.endswith(".vmat") else base + ".vmat") + "_c")
        if not data:
            return None
        found = []
        for m_ in re.finditer(rb"materials/[a-z0-9_\-./]+?\.vtex(?:_c)?(?![a-z0-9_])", data, flags=re.I):
            p_ = m_.group(0).decode("latin-1").lower()
            p_ = p_[:-2] if p_.endswith("_c") else p_
            if p_ not in found:
                found.append(p_)
        col = next((p_ for p_ in found if "_color" in os.path.basename(p_)), None)
        tr = next((p_ for p_ in found if "_trans" in os.path.basename(p_)), None)
        return (col, tr, found) if col else None
    except Exception:
        return None

def _backup_vmap(vp, kind):
    """Copy a vmap into BACKUP_DIR before a pass writes it -> the backup's path. The first copy ever made of a map is kept as
    <map>.original.vmap (never pruned); each change also gets <map>.<time>.before_<kind>.vmap, of which the newest BACKUP_KEEP are kept."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(str(vp)))[0]
    orig = os.path.join(BACKUP_DIR, stem + ".original.vmap")
    if not os.path.exists(orig):
        shutil.copy2(str(vp), orig)
    t = time.time()
    dst = os.path.join(BACKUP_DIR, f"{stem}.{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}{int(t * 1000) % 1000:03d}.before_{kind}.vmap")
    shutil.copy2(str(vp), dst)
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(stem + ".") and ".before_" in f and f.endswith(".vmap")
                 and re.fullmatch(r"\d{8}-\d{9}", f[len(stem) + 1:].split(".")[0]))
    for f in old[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return dst


_MAP_CACHE = {}     # path -> (file stamp, vmapedit.Map) of a map a pass opened and left unchanged, for the next pass


def _map_stamp(path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size


def _open_map(vp):
    """vmapedit.Map of vp. Reuses the copy the previous pass handed back with _keep_map when the file has not changed since; otherwise
    loads it. Hand it back with _keep_map only if this pass changed nothing (a saved map is reloaded: the file holds 32-bit floats)."""
    vmapedit, _dmx = _bundled_vmapedit()
    key = os.path.abspath(str(vp))
    hit = _MAP_CACHE.pop(key, None)
    if hit and hit[0] == _map_stamp(key):
        return hit[1]
    return vmapedit.Map(key)


def _keep_map(m, vp):
    """hand an UNCHANGED map back for the next pass (only the last one is kept)"""
    key = os.path.abspath(str(vp))
    _MAP_CACHE.clear()
    _MAP_CACHE[key] = (_map_stamp(key), m)


def _save_map(m, vp, kind):
    """back the vmap up (BACKUP_DIR, as before_<kind>) and save m over it -> the backup's path. OSError (Hammer holds the file) propagates."""
    _MAP_CACHE.pop(os.path.abspath(str(vp)), None)
    bak = _backup_vmap(vp, kind)
    m.save(str(vp))
    return bak


def _game_dir(export_dir: Path):
    """the addon's game folder (content\\csgo_addons\\x -> game\\csgo_addons\\x), or None when export_dir is not under a content folder"""
    parts = [x.lower() for x in export_dir.parts]
    if "content" not in parts:
        return None
    k = len(parts) - 1 - parts[::-1].index("content")
    return Path(*export_dir.parts[:k], "game", *export_dir.parts[k + 1:])


def _move_old_backups():
    """backups and done-records that older versions kept in the logs folder -> BACKUP_DIR (once)"""
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return 0
    moved = 0
    for f in names:
        if ".vmap.before_" in f or f.endswith((".water_done.txt", ".lighting_done.txt", ".radar_written.txt")):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            if not os.path.exists(os.path.join(BACKUP_DIR, f)):
                try:
                    shutil.move(os.path.join(LOG_DIR, f), os.path.join(BACKUP_DIR, f))
                    moved += 1
                except OSError:
                    pass
    return moved

def _safe_write(path: Path, data: bytes):
    """Write a file in the addon; skip when it already holds exactly this; retry briefly (Hammer / the resource compiler can hold a
    freshly written content file open). -> None on success, else a reason string."""
    last = None
    for attempt in range(6):
        try:
            if path.is_file() and path.read_bytes() == data:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return None
        except OSError as ex:
            last = ex
            time.sleep(0.4)
    return f"cannot write {path} ({last}); Hammer or another program probably has it open - close Hammer and rerun"

def _build_quad_material(mat: str, export_dir: Path):
    """Quad material for one overlay material (done once per material per run). -> (quad vmat path, colour ref, translucency ref or None,
    (w, h, 1.0) or None, log note) or a string saying why not."""
    vm = export_dir / (mat if mat.endswith(".vmat") else mat + ".vmat")
    txt = vm.read_text(errors="ignore") if vm.is_file() else ""
    mc = re.search(r'"TextureColor"\s+"([^"]+)"', txt)
    mt = re.search(r'"TextureTranslucency"\s+"([^"]+)"', txt)
    src_note, ts = "", _addon_texture_size(mat)
    qb = (mat[:-5] if mat.endswith(".vmat") else mat) + "_quad"
    if mc:
        col_ref, tr_ref = mc.group(1), (mt.group(1) if mt else None)
    else:
        # CS2's own overlay material: its textures are compiled .vtex_c in the VPK, and a vmat cannot point at those directly (Hammer
        # shows the error material), so the colour texture is decoded to a TGA + an alpha mask TGA in the addon, the same way the
        # Source1 materials are done.
        cs = _cs2_overlay_textures(mat)
        if not cs:
            return ("the overlay material is not in the addon folder and CS2's own copy could not be read (CS2 VPK not loaded, "
                    "material not in it, or no texture references in it): no texture to build the quad material from")
        try:
            vt = cs2_vpk().read(cs[0] + "_c")
            if not vt:
                raise ValueError(f"{cs[0]}_c is not in the indexed CS2 files")
            im = decode_vtex_c(vt)
        except Exception as ex:
            return f"CS2's colour texture {cs[0]} could not be decoded ({ex})"
        import io
        col_ref = qb + "_color.tga"
        b_ = io.BytesIO(); im.convert("RGB").save(b_, "TGA")
        err = _safe_write(export_dir / col_ref, b_.getvalue())
        tr_ref = None
        if not err and im.getchannel(3).getextrema()[0] < 255:
            tr_ref = qb + "_trans.tga"
            b_ = io.BytesIO(); im.getchannel(3).convert("RGB").save(b_, "TGA")
            err = _safe_write(export_dir / tr_ref, b_.getvalue())
        if err:
            return err
        ts = (im.size[0], im.size[1], 1.0)
        src_note = f"; CS2 texture {cs[0]} ({im.size[0]}x{im.size[1]}) decoded to {col_ref}" + (f" + {tr_ref}" if tr_ref else " (fully opaque, no mask)")
    qmat = qb + ".vmat"
    err = _safe_write(export_dir / qmat, ("\n".join(['"Layer0"', "{", '    "Shader" "csgo_vertexlitgeneric.vfx"', f'    "TextureColor" "{col_ref}"']
                                                  + ([f'    "TextureTranslucency" "{tr_ref}"'] if tr_ref else [])
                                                  + ['    "F_TRANSLUCENT" "1"', '    "F_RENDER_BACKFACES" "1"', "}"]) + "\n").encode("utf-8"))
    if err:
        return err
    return qmat, col_ref, tr_ref, ts, src_note

def convert_entity_overlays(vmap_paths, export_dir: Path):
    """CS2 static overlays do not project onto entities (func_breakable, doors, ...). For each CMapStaticOverlay whose projection
    volume reaches a face of exactly one brush entity (and no world brush face), the overlay is replaced by a translucent quad
    (CMapMesh, same position/angles/size/uv, laid QUAD_OFFSET above that face) parented to the entity, so it moves with it.
    Overlays that also touch world brushes, touch two entities, lie on slanted faces or whose material is not in the addon stay overlays.
    Re-running is safe: converted overlays no longer exist as overlays."""
    lines, total, mat_cache = [], 0, {}
    for vp in vmap_paths:
        try:
            raw = Path(vp).read_bytes()
            if b"encoding binary" not in raw[:100]:
                lines.append(f"# {os.path.basename(str(vp))}: not a binary .vmap; skipped")
                continue
            doc = _DmxDoc(raw)
        except Exception as ex:
            lines.append(f"# {os.path.basename(str(vp))}: cannot read ({ex}); skipped")
            continue
        els = doc.els
        parent_of = {}
        for e in els:
            if e.type in ("CMapEntity", "CMapGroup", "CMapWorld"):
                for c in e.get("children") or []:
                    parent_of[id(c)] = e
        ovs = [e for e in els if e.type == "CMapStaticOverlay" and id(e) in parent_of]
        if not ovs:
            continue
        meshes = []        # (mesh element, owning entity or None, world polygons [(pts, material)], aabb)
        for e in els:
            if e.type != "CMapMesh" or id(e) not in parent_of:
                continue
            owner = parent_of[id(e)]
            polys = []
            for loc, mat in _mesh_polys(e):
                if mat.startswith("materials/tools/"):
                    continue
                polys.append((_node_world(e, loc, parent_of), mat))
            if not polys:
                continue
            allp = [p for pl, _m in polys for p in pl]
            bb = ([min(p[i] for p in allp) for i in range(3)], [max(p[i] for p in allp) for i in range(3)])
            meshes.append((e, owner if owner.type == "CMapEntity" else None, polys, bb))
        next_id = max((e.get("nodeID") or 0) for e in els if e.attr("nodeID")) + 1
        made, notes = [], []
        for ov in ovs:
            md = ov.get("meshData")
            mats = (md.get("materials") or []) if md is not None else []
            mat = str(mats[0]).replace("\\", "/").lower() if mats else ""
            vloc = None
            for s_ in md.get("vertexData").get("streams"):
                if s_.name.startswith("position"):
                    vloc = s_.get("data")
            if not vloc or len(vloc) != 4:
                continue
            ang = ov.get("angles") or (0, 0, 0)
            ex_, ey_, n_ = _qrot(ang, (1, 0, 0)), _qrot(ang, (0, 1, 0)), _qrot(ang, (0, 0, 1))
            O = ov.get("origin")
            pts_w = _node_world(ov, list(vloc), parent_of)
            loc2 = [(sum((p[i] - O[i]) * ex_[i] for i in range(3)), sum((p[i] - O[i]) * ey_[i] for i in range(3))) for p in pts_w]
            rect = (min(p[0] for p in loc2), min(p[1] for p in loc2), max(p[0] for p in loc2), max(p[1] for p in loc2))
            far = ov.get("projectionFar")
            far = 64.0 if far is None else far
            lo = [min(min(p[i] for p in pts_w), O[i]) - far - 1 for i in range(3)]
            hi = [max(max(p[i] for p in pts_w), O[i]) + far + 1 for i in range(3)]
            hits_e, hits_w, slanted = [], [], 0
            for m_, own, polys, bb in meshes:
                if any(bb[1][i] < lo[i] or bb[0][i] > hi[i] for i in range(3)):
                    continue
                for pl, _pmat in polys:
                    fn = _poly_normal(pl)
                    if fn is None:
                        continue
                    t = sum((pl[0][i] - O[i]) * n_[i] for i in range(3))
                    if t < -0.5 or t > far + 0.5:
                        continue
                    pl2 = [(sum((p[i] - O[i]) * ex_[i] for i in range(3)), sum((p[i] - O[i]) * ey_[i] for i in range(3))) for p in pl]
                    if not _overlap_2d(rect, pl2):
                        continue
                    d_ = sum(fn[i] * n_[i] for i in range(3))
                    if d_ > -0.99:
                        if d_ > -0.7:         # back faces / perpendicular faces are not projected on
                            continue
                        slanted += 1
                        continue
                    (hits_e if own is not None else hits_w).append((t, own, pl2, m_))
            nid = ov.get("nodeID")
            where = "origin %.1f %.1f %.1f" % tuple(O)
            if not hits_e and not slanted:
                continue
            if not hits_e:
                notes.append(f"LEFT\tnode {nid}\t{mat}\t{where}\tonly slanted entity face(s) under it ({slanted}); the quad would not lie flat")
                continue
            if hits_w:
                notes.append(f"LEFT\tnode {nid}\t{mat}\t{where}\ttouches {len(hits_w)} world brush face(s) as well as an entity: a quad would cover only part of it")
                continue
            hits_e.sort(key=lambda h: h[0])
            best = hits_e[0]
            if any(h[1] is not best[1] and h[0] - best[0] <= 0.5 for h in hits_e):
                notes.append(f"LEFT\tnode {nid}\t{mat}\t{where}\ttouches faces of two different entities at the same depth")
                continue
            ent = best[1]
            pr = ent.get("entity_properties")
            props = {a_[0]: a_[2] for a_ in pr.attrs if a_[1] == 5} if pr is not None else {}
            who = f"{props.get('classname', '?')} '{props.get('targetname', '')}'"
            qm_ = mat_cache.get(mat)
            if qm_ is None:
                qm_ = mat_cache[mat] = _build_quad_material(mat, Path(export_dir))
            if isinstance(qm_, str):
                notes.append(f"LEFT\tnode {nid}\t{mat}\t{where}\ton {who}; {qm_}")
                continue
            qmat, col_ref, tr_ref, ts, src_note = qm_
            tw, th = (ts[0], ts[1]) if ts else (256.0, 256.0)
            # texcoords per vertex of the overlay's single face -> axes / scale / shift in the mesh's local space
            tc = None
            for s_ in md.get("faceVertexData").get("streams"):
                if s_.name.startswith("texcoord"):
                    tc = s_.get("data")
            fe, nx, evx, vdi, evd = md.get("faceEdgeIndices"), md.get("edgeNextIndices"), md.get("edgeVertexIndices"), md.get("vertexDataIndices"), md.get("edgeVertexDataIndices")
            vt, he = {}, fe[0]
            for _ in range(4):
                vt[vdi[evx[he]]] = tc[evd[he]]
                he = nx[he]
            ok_uv = len(vt) == 4 and all(k in vt for k in range(4))
            axes = None
            if ok_uv:
                x0, y0, x1, y1, x3, y3 = vloc[0][0], vloc[0][1], vloc[1][0], vloc[1][1], vloc[3][0], vloc[3][1]
                det = (x1 - x0) * (y3 - y0) - (y1 - y0) * (x3 - x0)
                if abs(det) > 1e-6:
                    sol = []
                    for k in (0, 1):
                        du, dv = vt[1][k] - vt[0][k], vt[3][k] - vt[0][k]
                        gx = (du * (y3 - y0) - (y1 - y0) * dv) / det
                        gy = ((x1 - x0) * dv - du * (x3 - x0)) / det
                        c = vt[0][k] - gx * x0 - gy * y0
                        err = max(abs(gx * v[0] + gy * v[1] + c - vt[i_][k]) for i_, v in enumerate(vloc))
                        gl = math.hypot(gx, gy)
                        if err > 1e-3 or gl < 1e-9:
                            sol = None
                            break
                        tex = tw if k == 0 else th
                        sol.append(((gx / gl, gy / gl, 0.0, c * tex), 1.0 / (gl * tex)))
                    axes = sol
            q = _dmx_clone(ov, {})
            q.type = "CMapMesh"
            q.attrs = [a_ for a_ in q.attrs if a_[0] not in OVERLAY_ONLY_ATTRS]
            q.set("nodeID", 2, next_id); next_id += 1
            q.set("referenceID", 15, random.getrandbits(64))
            q.set("randomSeed", 2, random.getrandbits(31))
            t_face = best[0]
            q.set("origin", 10, tuple(float(O[i] + n_[i] * (t_face - OVERLAY_QUAD_OFFSET)) for i in range(3)))
            qm = q.get("meshData")
            qm.set("materials", 37, [qmat])
            if axes:
                for s_ in qm.get("faceData").get("streams"):
                    if s_.name.startswith("textureScale"):
                        s_.set("data", s_.attr("data")[1], [(axes[0][1], axes[1][1])])
                    elif s_.name.startswith("textureAxisU"):
                        s_.set("data", s_.attr("data")[1], [axes[0][0]])
                    elif s_.name.startswith("textureAxisV"):
                        s_.set("data", s_.attr("data")[1], [axes[1][0]])
            doc.els.append(q)
            made.append((ov, q, ent))
            notes.append(f"CONVERTED\tnode {nid} -> {q.get('nodeID')}\t{mat}\t{where}\tquad on {who}, {t_face - OVERLAY_QUAD_OFFSET:.2f} units from the overlay plane"
                         + ("" if ts else "; texture size unknown (256 assumed for the uv axes)") + ("" if axes else "; uv axes left as the overlay's")
                         + ("" if tr_ref else "; no translucency mask for this decal") + src_note)
        if made:
            for ov, q, ent in made:
                par = parent_of[id(ov)]
                par.attr("children")[2] = [c for c in par.get("children") if c is not ov]
                ent.attr("children")[2] = list(ent.get("children") or []) + [q]
            # new sub-elements of the quads must be in the element list too; dropped overlays' private sub-elements are removed
            seen_ids = {id(e) for e in doc.els}
            st = [q for _o, q, _e in made]
            while st:
                e = st.pop()
                for _n, t, v in e.attrs:
                    for x in ([v] if t == 1 else v if t == 33 else []):
                        if x is not None and id(x) not in seen_ids:
                            seen_ids.add(id(x)); doc.els.append(x); st.append(x)
            after = doc.reachable()
            dead = set()
            for ov, _q, _e in made:
                st = [ov]
                while st:
                    e = st.pop()
                    if id(e) in dead or id(e) in after:
                        continue
                    dead.add(id(e))
                    for _n, t, v in e.attrs:
                        for x in ([v] if t == 1 else v if t == 33 else []):
                            if x is not None:
                                st.append(x)
            doc.els = [e for e in doc.els if id(e) not in dead]
            data = doc.save_bytes()
            try:
                chk = read_binary_dmx(data)
                assert sum(1 for e in chk if e["type"] == "CMapStaticOverlay") == len(ovs) - len(made)
            except Exception as ex:
                notes.append(f"# {os.path.basename(str(vp))}: the edited file failed its re-read check ({ex}); NOT written")
                made = []
            if made:
                try:
                    bak = _backup_vmap(vp, "overlayquads")
                    Path(vp).write_bytes(data)
                except OSError as ex:
                    notes.append(f"# {os.path.basename(str(vp))}: could not write the map ({ex}); nothing was changed in it. Close Hammer and rerun.")
                    print(f"  [WARN] overlay quads: could not write {vp} ({ex}); close Hammer and rerun")
                    made = []
            if made:
                notes.append(f"# {len(made)} overlay(s) converted to parented quads in {vp}; original saved as {bak}. Reopen the map in Hammer.")
                print(f"  overlay quads: converted {len(made)} of {len(ovs)} static overlays in {os.path.basename(str(vp))} (backup {bak})")
                total += len(made)
        if notes:
            lines.append(f"# {os.path.basename(str(vp))}: {len(ovs)} static overlay(s); LEFT = stays a static overlay (reason given)")
            lines += notes
    return [f"# {total} static overlay(s) converted to quads parented to brush entities (CS2 static overlays do not project onto entities)"] + lines


# ---- report: entity outputs (IO connections) that are in the .vmf but not in the .vmap. READ-ONLY: nothing is changed ----

_IO_SPECIAL = ("!activator", "!caller", "!self", "!player", "!picker", "!pvsplayer", "!enemy", "!target")

def _vmf_entities_io(path: str):
    """-> [{"cls","name","origin" (tuple or None),"bbox" ((lo),(hi)) or None,"conns":[(output,target,input,param,delay,times)]}]
    Keeps repeated output names (a plain dict would collapse them). Connection values are 'target,input,param,delay,times' with ','
    or ESC (0x1b) between the fields."""
    ents, depth, cur, sect, pending = [], 0, None, None, ""
    for raw in open(path, encoding="utf-8", errors="replace"):
        ln = raw.strip()
        if ln == "{":
            depth += 1
            if depth == 1 and pending == "entity":
                cur = {"cls": "", "name": "", "origin": None, "pts": [], "conns": []}
            elif cur is not None and depth == 2:
                sect = pending
            continue
        if ln == "}":
            if cur is not None and depth == 2:
                sect = None
            if depth == 1 and cur is not None:
                pts = cur.pop("pts")
                cur["bbox"] = ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]) if pts else None
                ents.append(cur)
                cur = None
            depth -= 1
            continue
        if ln.startswith('"'):
            m = re.match(r'"([^"]*)"\s+"(.*)"\s*$', ln)
            if not m or cur is None:
                continue
            k, v = m.group(1), m.group(2)
            if depth == 1:
                kl = k.lower()
                if kl == "classname":
                    cur["cls"] = v.lower()
                elif kl == "targetname":
                    cur["name"] = v
                elif kl == "origin":
                    try:
                        cur["origin"] = tuple(float(x) for x in v.split()[:3])
                    except ValueError:
                        pass
            elif depth == 2 and sect == "connections":
                esc = "\x1b" in v
                f = v.split("\x1b") if esc else v.split(",")
                if len(f) >= 5:
                    try:
                        cur["conns"].append((k, f[0], f[1], f[2] if esc else ",".join(f[2:-2]), float(f[-2]), int(float(f[-1]))))
                    except ValueError:
                        pass
            elif depth == 3 and sect == "solid" and k.lower() == "plane":
                for t in re.findall(r"\(([^)]*)\)", v):
                    try:
                        cur["pts"].append(tuple(float(x) for x in t.split()))
                    except ValueError:
                        pass
            continue
        if ln:
            pending = ln
    return ents

def _vmap_entities_io(path: str):
    doc = _DmxDoc(Path(path).read_bytes())
    parent_of = {}
    for e in doc.els:
        if e.type in ("CMapEntity", "CMapGroup", "CMapWorld"):
            for c in e.get("children") or []:
                parent_of[id(c)] = e
    out = []
    for e in doc.els:
        if e.type != "CMapEntity":
            continue
        pr = e.get("entity_properties")
        props = {a_[0]: a_[2] for a_ in pr.attrs if a_[1] == 5} if pr is not None else {}
        pts = []
        for k in e.get("children") or []:
            if k.type == "CMapMesh":
                for loc, _m in _mesh_polys(k):
                    pts += _node_world(k, loc, parent_of)
        bbox = ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]) if pts else None
        conns = []
        for c in e.get("connectionsData") or []:
            _par = c.get("overrideParam") or ""
            conns.append((c.get("outputName") or "", c.get("targetName") or "", c.get("inputName") or "", "" if _par.strip().lower() == "(null)" else _par,
                          float(c.get("delay") or 0.0), int(c.get("timesToFire") if c.get("timesToFire") is not None else -1)))
        out.append({"cls": (props.get("classname") or "").lower(), "name": props.get("targetname") or "",
                    "origin": tuple(e.get("origin") or (0, 0, 0)), "bbox": bbox, "conns": conns, "used": False})
    return out

def report_dropped_io(vmap_paths, vmf_path: str):
    """Compare every entity output in the .vmf with the .vmap's. -> report lines. Changes nothing.
    Owners are matched by name (+ classname), unnamed ones by classname and position / brush bounds. A dropped output is flagged by
    whether its target still exists in the vmap, so outputs of removed / replaced logic (an edited map) are told apart from ones the
    importer lost."""
    import fnmatch
    from collections import Counter
    if not vmf_path or not Path(vmf_path).is_file():
        return ["# no .vmf: dropped outputs cannot be listed"]
    vents = [e for e in _vmf_entities_io(vmf_path) if e["conns"]]
    nconn = sum(len(e["conns"]) for e in vents)
    lines = [f"# outputs (IO) in the .vmf that are not in the .vmap. {len(vents)} .vmf entities with outputs, {nconn} outputs. Nothing was changed.",
             "# OWNER = the entity that fires the output; TARGET = what it fires at. Matching is by name (+classname), or for unnamed owners by classname and position / brush bounds.",
             "# TARGET EXISTS: an entity with that name (or a matching wildcard / classname) is in the vmap, so the output is probably a real loss.",
             "# TARGET GONE: nothing in the vmap has that name, so the logic was most likely removed or replaced when the map was edited.",
             "# TARGET SPECIAL: !activator / !self / ... resolved at run time.  OWNER NOT FOUND: no vmap entity matches the owner, so its outputs cannot be compared (listed once, no judgement).", ""]
    for vp in vmap_paths:
        try:
            ments = _vmap_entities_io(vp)
        except Exception as ex:
            lines.append(f"# {os.path.basename(str(vp))}: cannot read ({ex})")
            continue
        names = {}
        for m in ments:
            if m["name"]:
                names.setdefault(m["name"].lower(), []).append(m)
        all_names, all_cls = set(names), {m["cls"] for m in ments}
        equips = {m["name"].lower() for m in ments if m["cls"] == "game_player_equip" and m["name"]}
        psys = {m["name"].lower() for m in ments if m["cls"] in ("info_particle_system", "env_particle_glow") and m["name"]}

        def norm_in(t, i):                       # inputs the script renames: Use on equips, TurnOn / TurnOff on converted env_steam
            tl, il = t.lower(), i.lower()
            if il == "use" and tl in equips:
                return "triggerforactivatedplayer"
            if tl in psys and il in ("turnon", "turnoff", "showsprite", "hidesprite", "startspark_", "stopspark_", "sparkonce_"):
                return "start" if il in ("turnon", "showsprite", "startspark", "sparkonce") else "stop"
            return il
        def target_status(t):
            tl = t.lower()
            if tl in _IO_SPECIAL:
                return "TARGET SPECIAL"
            if tl in all_names or tl in all_cls:
                return "TARGET EXISTS"
            if any(c in tl for c in "*?") and (any(fnmatch.fnmatchcase(n, tl) for n in all_names) or any(fnmatch.fnmatchcase(c, tl) for c in all_cls)):
                return "TARGET EXISTS"
            return "TARGET GONE"
        def dist(v, m):
            if v["bbox"] and m["bbox"]:
                cv = [(v["bbox"][0][i] + v["bbox"][1][i]) / 2 for i in range(3)]
                cm = [(m["bbox"][0][i] + m["bbox"][1][i]) / 2 for i in range(3)]
                sz = max(abs((v["bbox"][1][i] - v["bbox"][0][i]) - (m["bbox"][1][i] - m["bbox"][0][i])) for i in range(3))
                return math.dist(cv, cm) + (1e6 if sz > 4.0 else 0.0)
            if v["origin"] and not v["bbox"] and not m["bbox"]:
                return math.dist(v["origin"], m["origin"])
            return 1e9
        dropped, notfound, matched = [], [], 0
        for v in vents:
            cand = [m for m in ments if m["cls"] == v["cls"] and not m["used"]]
            if v["name"]:
                cand = [m for m in cand if m["name"].lower() == v["name"].lower()]
            else:
                cand = [m for m in cand if not m["name"]]
            best = min(cand, key=lambda m: dist(v, m), default=None)
            if best is None or (not v["name"] and dist(v, best) > (3.0 if v["bbox"] else 1.0)):
                notfound.append(v)
                continue
            best["used"] = True
            matched += 1
            miss = Counter((o.lower(), t.lower(), norm_in(t, i), p, round(d, 3), n) for o, t, i, p, d, n in v["conns"])
            miss.subtract(Counter((o.lower(), t.lower(), norm_in(t, i), p, round(d, 3), n) for o, t, i, p, d, n in best["conns"]))
            for (o, t, i, p, d, n), c in miss.items():
                for _ in range(max(c, 0)):
                    dropped.append((v, o, t, i, p, d, n))
        stats = Counter(target_status(t) for _v, _o, t, _i, _p, _d, _n in dropped)
        lines.append(f"# {os.path.basename(str(vp))}: owners matched {matched} of {len(vents)}; outputs dropped from matched owners: {len(dropped)} "
                     f"({stats['TARGET EXISTS']} target exists, {stats['TARGET GONE']} target gone, {stats['TARGET SPECIAL']} special); "
                     f"owners not found {len(notfound)} ({sum(len(v['conns']) for v in notfound)} outputs not compared)")
        by = Counter((o, i) for _v, o, _t, i, _p, _d, _n in dropped if target_status(_t) == "TARGET EXISTS")
        if by:
            lines.append("# probable losses by output -> input (target still exists): " + ", ".join(f"{o}->{i} x{c}" for (o, i), c in by.most_common(12)))
        order = {"TARGET EXISTS": 0, "TARGET SPECIAL": 1, "TARGET GONE": 2}
        for v, o, t, i, p, d, n in sorted(dropped, key=lambda r: (order[target_status(r[2])], r[0]["cls"], r[0]["name"].lower(), r[1])):
            who = f"{v['cls']} '{v['name']}'" if v["name"] else f"{v['cls']} (unnamed, {' '.join(str(round(x)) for x in (v['origin'] or [(a + b) / 2 for a, b in zip(*v['bbox'])]))})"
            lines.append(f"{target_status(t)}\t{who}\t{o}\t-> {t}\t{i}\t{p!r}\tdelay {d:g}\ttimes {n}")
        for v in notfound[:400]:
            who = f"{v['cls']} '{v['name']}'" if v["name"] else f"{v['cls']} (unnamed, {' '.join(str(round(x)) for x in (v['origin'] or [(a + b) / 2 for a, b in zip(*v['bbox'])]))})"
            lines.append(f"OWNER NOT FOUND\t{who}\t{len(v['conns'])} output(s): " + "; ".join(sorted({f"{o}->{t}.{i}" for o, t, i, _p, _d, _n in v['conns']}))[:200])
        lines.append("")
    return lines


# .vmf class -> classes it can have in the vmap (importer conversions)
_ENT_ALIAS = {"light": ("light_omni2",), "light_spot": ("light_barn",), "func_physbox_multiplayer": ("func_physbox",), "func_illusionary": ("func_brush",),
              "func_wall": ("func_brush",), "func_wall_toggle": ("func_brush",), "env_sprite": ("info_particle_system", "env_particle_glow"),
              "env_sprite_oriented": ("info_particle_system", "env_particle_glow"), "env_glow": ("info_particle_system", "env_particle_glow"),
              "env_steam": ("info_particle_system",), "env_steamjet": ("info_particle_system",),  "env_spritetrail": ("info_particle_system", "env_spritetrail"), "env_smokestack": ("info_particle_system",),
              "func_breakable_surf": ("func_breakable", "func_shatterglass"), "trigger_gravity": ("trigger_gravity", "trigger_multiple"),
              "func_monitor": ("func_brush",), "trigger_once": ("trigger_once", "trigger_multiple"), 
              "weapon_p228": ("weapon_p250",), "weapon_usp": ("weapon_usp_silencer",), "weapon_mp5navy": ("weapon_mp5sd",), "weapon_scout": ("weapon_ssg08",),
              "weapon_m3": ("weapon_nova",), "weapon_sg550": ("weapon_scar20",), "weapon_sg552": ("weapon_sg556",), "weapon_tmp": ("weapon_mp9",),
              "weapon_galil": ("weapon_galilar",), "weapon_p2000": ("weapon_hkp2000",), "light_dynamic": ("light_barn", "light_omni2"), "color_correction": ("post_processing_volume",)}
# classes the importer folds into the world or never makes an entity of (counted, not listed)
_ENT_EXPECTED = {"worldspawn", "func_detail", "func_ladder", "info_overlay", "infodecal", "env_cubemap", "info_node", "info_node_hint", "info_node_air",
                 "func_areaportal", "func_areaportalwindow", "func_occluder", "func_viscluster", "info_lighting", "env_fog_controller", "shadow_control",
                 "env_tonemap_controller", "sky_camera", "env_sun", "water_lod_control", "func_precipitation", "func_dustmotes", "point_spotlight",
                 "env_lightglow", "fog_volume", "info_null", "func_vehicleclip", "info_overlay_transition", "env_detail_controller", "vgui_screen"}


def report_missing_entities(vmap_paths, vmf_path: str):
    """Every .vmf entity with no counterpart in the .vmap -> report lines. Changes nothing.
    Named entities are matched by name (a push_<mass>_ prefix from PROP_MASS_RENAME is ignored), unnamed ones by position (point entities) or
    brush bounds. A leftover .vmf entity with an unused vmap entity of the same class and the same brush size / name elsewhere is listed as MOVED."""
    if not vmf_path or not Path(vmf_path).is_file():
        return ["# no .vmf: missing entities cannot be listed"]
    from collections import Counter
    vents = _vmf_entities_io(vmf_path)
    lines = []
    total_missing = 0
    for vp in vmap_paths:
        try:
            ments = _vmap_entities_io(vp)
        except Exception as ex:
            lines.append(f"# {os.path.basename(str(vp))}: cannot read ({ex})")
            continue
        def nm(x):
            return _PROP_NAME_HAS_MASS_RE.sub("", x["name"]).lower()
        def centre(x):
            return tuple((a + b) / 2 for a, b in zip(*x["bbox"])) if x["bbox"] else tuple(x["origin"] or (0, 0, 0))
        def size(x):
            return tuple(b - a for a, b in zip(*x["bbox"])) if x["bbox"] else None
        bycls = {}
        for x in ments:
            x["used"] = False
            bycls.setdefault(x["cls"], []).append(x)
        expected, left = Counter(), []
        todo = [v for v in vents if v["cls"]]
        for v in todo:
            if v["cls"] in _ENT_EXPECTED:
                expected[v["cls"]] += 1
                v["hit"] = True
                continue
            v["hit"] = False
            pool = [x for c in (v["cls"],) + _ENT_ALIAS.get(v["cls"], ()) for x in bycls.get(c, []) if not x["used"]]
            if v["name"]:
                cand = [x for x in pool if nm(x) == v["name"].lower()]
                best = min(cand, key=lambda x: math.dist(centre(v), centre(x)), default=None)
            else:
                best = min(pool, key=lambda x: (bool(nm(x)), math.dist(centre(v), centre(x))), default=None)
                if best is not None:
                    tol = 4.0 if v["bbox"] else 2.0
                    sv, sx = size(v), size(best)
                    if math.dist(centre(v), centre(best)) > tol or (sv and sx and max(abs(a - b) for a, b in zip(sv, sx)) > 4.0):
                        best = None
            if best is not None:
                best["used"] = True
                v["hit"] = True
            else:
                left.append(v)
        moved, changed, edit = [], [], []
        def iou(a, b):
            inter = 1.0
            for i in range(3):
                lo, hi = max(a[0][i], b[0][i]), min(a[1][i], b[1][i])
                if hi < lo - 1:
                    return 0.0
                inter *= max(hi - lo, 1.0)
            va = math.prod(max(a[1][i] - a[0][i], 1.0) for i in range(3))
            vb = math.prod(max(b[1][i] - b[0][i], 1.0) for i in range(3))
            return inter / (va + vb - inter)
        for v in left:
            pool = [x for c in (v["cls"],) + _ENT_ALIAS.get(v["cls"], ()) for x in bycls.get(c, []) if not x["used"]]
            sv = size(v)
            # same class, same brush size and name, somewhere else -> moved
            cand = [x for x in pool if nm(x) == v["name"].lower() and (not sv or (size(x) and max(abs(a - b) for a, b in zip(sv, size(x))) <= 1.0))]
            if not v["name"] and sv:
                cand = [x for x in pool if not nm(x) and size(x) and max(abs(a - b) for a, b in zip(sv, size(x))) <= 1.0]
            best = min(cand, key=lambda x: math.dist(centre(v), centre(x)), default=None)
            if best is not None and (v["name"] or math.dist(centre(v), centre(best)) <= 2048):
                best["used"] = True
                moved.append((v, best))
                continue
            edit.append(v)
        # edited in place, closest pairs first: a brush entity of any class mostly over the same box, or a point entity of the class within 128 units
        pairs = []
        for v in edit:
            if v["bbox"]:
                pairs += [(-iou(v["bbox"], x["bbox"]), id(v), v, x) for xs in bycls.values() for x in xs if not x["used"] and x["bbox"] and iou(v["bbox"], x["bbox"]) >= 0.25]
            else:
                pool = [x for c in (v["cls"],) + _ENT_ALIAS.get(v["cls"], ()) for x in bycls.get(c, []) if not x["used"] and not x["bbox"]]
                pairs += [(math.dist(centre(v), centre(x)) / 1e4, id(v), v, x) for x in pool if math.dist(centre(v), centre(x)) <= 128]
        done = set()
        for _r, _i, v, x in sorted(pairs, key=lambda r: (r[0], r[1])):
            if id(v) in done or x["used"]:
                continue
            x["used"] = True
            done.add(id(v))
            changed.append((v, x))
        missing = [v for v in edit if id(v) not in done]
        total_missing += len(missing)
        def who(v):
            return f"{v['cls']}\t{v['name'] or '(unnamed)'}\t{' '.join(str(round(c)) for c in centre(v))}"
        lines.append(f"# {os.path.basename(str(vp))}: {len(todo)} .vmf entities; {len(missing)} missing, {len(moved)} moved, {len(changed)} edited; "
                     f"{sum(expected.values())} of classes the importer folds into the world or never keeps (not listed)")
        mc = Counter(v["cls"] for v in missing)
        if mc:
            lines.append("# missing by class: " + ", ".join(f"{c} x{n}" for c, n in mc.most_common()))
        if expected:
            lines.append("# not listed (expected): " + ", ".join(f"{c} x{n}" for c, n in expected.most_common()))
        # many copies of one name (192 x z14nadea), or many unnamed of one class (80 smoke grenades): one line with the area they span
        groups = {}
        for v in missing:
            groups.setdefault((v["cls"], v["name"].lower()), []).append(v)
        for (_c, _n), vs in sorted(groups.items()):
            if _n or len(vs) > 6:
                cs = [centre(v) for v in vs]
                area = "" if len(vs) == 1 else "\t(x%d, within %s .. %s)" % (len(vs), " ".join(str(round(min(c[i] for c in cs))) for i in range(3)),
                                                                            " ".join(str(round(max(c[i] for c in cs))) for i in range(3)))
                lines.append(f"MISSING\t{who(vs[0]) if len(vs) == 1 else vs[0]['cls'] + chr(9) + (vs[0]['name'] or '(unnamed)') + chr(9)}{area}"
                             + (f"\t{sum(len(v['conns']) for v in vs)} output(s)" if any(v["conns"] for v in vs) else ""))
            else:
                for v in vs:
                    lines.append(f"MISSING\t{who(v)}" + (f"\t{len(v['conns'])} output(s)" if v["conns"] else ""))
        for v, x in sorted(changed, key=lambda r: (r[0]["cls"], r[0]["name"])):
            sv, sx = size(v), size(x)
            what = [f"now {x['cls']}"] if x["cls"] != v["cls"] else []
            if (x["name"] or "") != (v["name"] or ""):
                what.append(f"name {v['name'] or '(none)'} -> {x['name'] or '(none)'}")
            if sv and sx and max(abs(a - b) for a, b in zip(sv, sx)) > 1.0:
                what.append("size " + " ".join(str(round(a)) for a in sv) + " -> " + " ".join(str(round(a)) for a in sx))
            if math.dist(centre(v), centre(x)) > 2:
                what.append("centre -> " + " ".join(str(round(c)) for c in centre(x)))
            lines.append(f"EDITED\t{who(v)}\t{', '.join(what) or 'changed'} (probably the same entity edited in Hammer)")
        for v, x in sorted(moved, key=lambda r: (r[0]["cls"], r[0]["name"])):
            lines.append(f"MOVED\t{who(v)}\t-> {' '.join(str(round(c)) for c in centre(x))}")
        lines.append("")
    lines.insert(0, "# .vmf entities with no counterpart in the .vmap (deleted in Hammer, dropped by the importer, or changed beyond matching). Nothing was changed.\n"
                    "# Named ones are matched by name (+ the classes the importer converts to, e.g. light -> light_omni2, env_sprite -> info_particle_system),\n"
                    "# unnamed ones by position / brush bounds. MOVED = same class, name and brush size somewhere else. EDITED = something now fills the same space\n"
                    "# (a brush entity over mostly the same box, or a point entity of the class within 128 units) but its class, name or size changed.")
    return lines


def find_overlay_vmf(vmap_paths):
    """STATIC_OVERLAY_VMF if set; otherwise the .vmf under <Source1 root>/maps (or <root>/*/maps) whose name is most like a scanned vmap's."""
    import difflib
    import glob
    if STATIC_OVERLAY_VMF:
        return STATIC_OVERLAY_VMF if os.path.isfile(STATIC_OVERLAY_VMF) else ""
    norm = lambda n: re.sub(r"[^a-z0-9]", "", os.path.splitext(os.path.basename(str(n)))[0].lower())   # noqa: E731
    cands = []
    for root in SOURCE1_ROOTS:
        for pat in ("maps/*.vmf", "*/maps/*.vmf"):
            cands += glob.glob(os.path.join(root, pat))
    best, best_r = "", 0.0
    for c in cands:
        for vp in vmap_paths:
            r = difflib.SequenceMatcher(None, norm(c), norm(vp)).ratio()
            if r > best_r:
                best, best_r = c, r
    return best if best_r >= 0.75 else ""

def find_env_steam(vmap_paths):
    """env_steam / env_steamjet entities, plus info_particle_system entities that carry the Source1 steam keys
    (Speed, JetLength...), in the .vmap files ->
    [(vmap name, targetname, origin, angles, keyvalues dict, classname, current effect_name)], notes."""
    found, notes = [], []
    for vp in vmap_paths:
        ents, err = vmap_entities(str(vp))
        if err:
            notes.append(f"# {os.path.basename(str(vp))}: entities could not be read ({err}); "
                         f"add env_steam keyvalues to STEAM_EFFECTS by hand")
            continue
        for e in ents:
            cls = e["cls"].lower()
            low = {k.lower() for k in e["props"]}
            if cls in ("env_steam", "env_steamjet") or (cls == "info_particle_system" and {"speed", "jetlength"} <= low):
                found.append((os.path.basename(str(vp)), e["name"], e["origin"], e["angles"], dict(e["props"]),
                              cls, e["props"].get("effect_name", "")))
    return found, notes

# ======================================================================== ENTITY SOUND CHECK

SOUND_KEY_HINTS = ("sound", "noise", "message", "snd")
_KV2_LINE_RE = re.compile(r'^\s*"([^"]*)"\s+"([^"]*)"\s+"((?:[^"\\]|\\.)*)"\s*$')

def _event_names_in_file(export_dir: Path) -> set:
    f = export_dir / "soundevents" / "soundevents_addon.vsndevts"
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    txt = re.sub(r"//[^\n]*", "", txt)
    return set(re.findall(r'^\s*"([^"]+)"\s*=', txt, flags=re.M))

def check_sound_entities(vmap_paths, sound_events: dict, export_dir: Path):
    """For every entity key in the (text keyvalues2) .vmap whose value looks like a sound, say whether it already
    names a soundevent. Binary .vmap files only get a token-level report (no entity context)."""
    events = _event_names_in_file(export_dir) | set(sound_events)
    events_l = {e.lower(): e for e in events}
    by_vsnd = {}
    for name, v in sound_events.items():
        for f in (v[0] if isinstance(v, tuple) else [v]):
            by_vsnd.setdefault(f.lower(), name)
    lines, counts = [], {"OK": 0, "CHANGE": 0, "UNKNOWN": 0}

    def judge(val: str):
        v = val.strip().replace("\\", "/")
        vl = v.lstrip(_WAVE_PREFIX_CHARS).lower()
        if vl in events_l:
            return "OK", ""
        base, ext = os.path.splitext(vl)
        if ext in (".vsnd", ".wav", ".mp3"):
            key = (base if base.startswith("sounds/") else "sounds/" + base.removeprefix("sound/")) + ".vsnd"
            if key in by_vsnd:
                return "CHANGE", by_vsnd[key]
            return "UNKNOWN", "sound path without a matching soundevent"
        return None, ""

    for vp in vmap_paths:
        try:
            data = Path(vp).read_bytes()
        except Exception:
            continue
        head = data[:200].decode("latin-1", errors="ignore").lower()
        _ents, _err = vmap_entities(str(vp))
        if not _err and _ents:
            for _e in _ents:
                for k, val in _e["props"].items():
                    kl = k.lower()
                    if kl in ("classname", "targetname") or not val:
                        continue
                    if not any(h in kl for h in SOUND_KEY_HINTS) and not val.lower().endswith((".vsnd", ".wav", ".mp3")):
                        continue
                    st, sug = judge(val)
                    if st:
                        counts[st] += 1
                        lines.append(f"{st}\t{os.path.basename(str(vp))}\t{_e['cls']}\t{_e['name']}\t{k}\t{val}\t{sug}")
            continue
        if "keyvalues2" not in head:
            lines.append(f"# {vp}: binary .vmap, no entity context; token-level check only")
            toks = {m.group(0).decode("ascii", errors="ignore") for m in
                    re.finditer(rb'[A-Za-z0-9_\-./\\]{3,}', data)}
            for t in sorted(toks):
                st, sug = judge(t)
                if st:
                    counts[st] += 1
                    lines.append(f"{st}\t{os.path.basename(str(vp))}\t-\t-\t-\t{t}\t{sug}")
            continue
        text = data.decode("utf-8", errors="ignore")
        stack = [{}]
        for raw in text.splitlines():
            ln = raw.strip()
            m = _KV2_LINE_RE.match(raw)
            if m:
                stack[-1][m.group(1).lower()] = m.group(3)
                continue
            if ln == "{":
                stack.append({})
                continue
            if ln == "}" and len(stack) > 1:
                blk = stack.pop()
                cls = blk.get("classname")
                if cls:
                    for k, val in blk.items():
                        if k in ("classname", "targetname") or not val:
                            continue
                        if not any(h in k for h in SOUND_KEY_HINTS) and not val.lower().endswith((".vsnd", ".wav", ".mp3")):
                            continue
                        st, sug = judge(val)
                        if st:
                            counts[st] += 1
                            lines.append(f"{st}\t{os.path.basename(str(vp))}\t{cls}\t{blk.get('targetname', '')}\t{k}\t{val}\t{sug}")
    head = ["# OK = value already is a soundevent name | CHANGE = value is a sound path, use the event named in the last column | "
            "UNKNOWN = sound path with no matching event",
            f"# OK {counts['OK']}   CHANGE {counts['CHANGE']}   UNKNOWN {counts['UNKNOWN']}"]
    return head + lines

# ======================================================================== ASSET RESOLUTION

# ======================================================================== MODEL IMPORT (cs_mdl_import.exe)

DMX_PATH_RE = PATH_RE_TEXT

@lru_cache(maxsize=200_000)
def parse_vmdl_dependencies(vmdl_path: str):
    deps = set()
    try:
        text = Path(vmdl_path).read_text(encoding="utf-8", errors="ignore")
        deps.update(re.findall(r'filename\s*=\s*["\']?([^"\']+\.dmx)["\']?', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.dmx)"', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.(?:vmat|vtex|vtex_c|tga|png|jpg|jpeg))"', text, flags=re.IGNORECASE))
        deps.update(re.findall(r'"([^"]+\.(?:vmdl|vmdl_c))"', text, flags=re.IGNORECASE))
    except Exception:
        pass
    return frozenset(d for d in (normalize_dep_must_be_rooted(x) for x in deps) if d)

@lru_cache(maxsize=200_000)
def extract_nul_tokens(path: str) -> tuple:
    try:
        data = Path(path).read_bytes()
    except Exception:
        return tuple()
    out = []
    for seg in data.split(b"\x00"):
        if 2 <= len(seg) <= 8192:
            s = seg.decode("utf-8", errors="ignore").strip()
            if s:
                out.append(s)
    return tuple(out)

@lru_cache(maxsize=200_000)
def parse_dmx_deps_nul(dmx_path: str):
    """(rooted dependency paths, bare *.vmat names with no folder)"""
    rooted, orphans = set(), set()
    for tok in extract_nul_tokens(dmx_path):
        for m in DMX_PATH_RE.findall(tok):
            t = m.strip().strip('"').strip("'").replace("\\", "/").lstrip("/")
            if "/" in t:
                nr = normalize_dep_must_be_rooted(t)
                if nr:
                    rooted.add(nr)
            elif t.lower().endswith(".vmat"):
                orphans.add(t)
    return frozenset(rooted), frozenset(orphans)

def _mdl_parts(rel: str):
    return [p for p in rel.replace("\\", "/").split("/") if p]

def _mdl_path(root: Path, rel: str) -> Path:
    return root.joinpath(*_mdl_parts(rel))

def mdl_sidecar_problems(source1_root: Path, mdl_rel: str):
    mdl = _mdl_path(source1_root, mdl_rel)
    problems = []
    if not mdl.is_file():
        problems.append(".mdl")
    if not mdl.with_name(mdl.stem + ".vvd").is_file():
        problems.append(".vvd")
    if not any(mdl.with_name(mdl.stem + s).is_file() for s in MDL_VTX_SUFFIXES):
        problems.append(".vtx")
    return problems

def mdl_output_footprint(export_dir: Path, mdl_rel: str):
    """(output .vmdl, output _refs dir) for a Source1 model path like 'models/props/crate.mdl'."""
    parts = _mdl_parts(mdl_rel)
    stem = parts[-1].rsplit(".", 1)[0]
    d = export_dir.joinpath(*parts[:-1])
    return d / f"{stem}.vmdl", d / f"{stem}_refs"

def _newest_mtime(path: Path) -> float:
    if not path.exists():
        return -1.0
    if path.is_file():
        try:
            return path.stat().st_mtime
        except Exception:
            return -1.0
    newest = -1.0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                newest = max(newest, f.stat().st_mtime)
        except Exception:
            continue
    return newest

def _count_recent(path: Path, t_ref: float) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return 1 if path.stat().st_mtime >= t_ref else 0
        except Exception:
            return 0
    n = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and f.stat().st_mtime >= t_ref:
                n += 1
        except Exception:
            continue
    return n

def mdl_success_detect(export_dir: Path, mdl_rel: str, t0: float):
    """Success = the .vmdl was written after the start time (refs-only output is partial)."""
    t_ref = t0 - MDL_MTIME_TOLERANCE
    vmdl, refs = mdl_output_footprint(export_dir, mdl_rel)
    refs_n = _count_recent(refs, t_ref)
    if _count_recent(vmdl, t_ref) > 0:
        return True, f"vmdl written ({refs_n} ref files updated)"
    if refs_n:
        return False, f"partial output: {refs_n} ref files but no .vmdl"
    return False, "no model-scoped outputs updated"

def _recent_minidumps(source1_root: Path, t_ref: float) -> int:
    n = 0
    for base in (source1_root, source1_root / "models", Path.cwd()):
        if not base.exists():
            continue
        for mdmp in base.glob("cs_mdl_import_*_accessviolation.mdmp"):
            try:
                if mdmp.stat().st_mtime >= t_ref:
                    n += 1
                mdmp.unlink()
            except Exception:
                pass
    return n

def _kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass

def _import_attempt(source1_root: Path, mdl_rel: str, export_dir: Path, list_path: Path, stdout_log: Path):
    """One importer run -> (success, stalled, return_code, message)."""
    rel_win = "\\".join(_mdl_parts(mdl_rel))
    list_path.write_text(rel_win + "\n", encoding="utf-8", errors="ignore")
    args = [CS_MDL_IMPORT_EXE, "-nop4", "-game", IMPORT_GAME, "-r",
            "-i", str(source1_root), "-o", str(export_dir), "-l", str(list_path), "-v"]
    vmdl, refs = mdl_output_footprint(export_dir, mdl_rel)
    t0 = time.time()

    with stdout_log.open("a", encoding="utf-8", errors="ignore") as log:
        log.write("\n" + "=" * 78 + f"\n[IMPORT] {rel_win}\n[CMD] {' '.join(args)}\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                args, cwd=str(source1_root), stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
        except Exception as e:
            return False, False, -1, f"failed to start: {e}"

        stalled, reason = False, ""
        last_activity, last_sig = time.time(), None
        try:
            while proc.poll() is None:
                time.sleep(MDL_POLL_SECONDS)
                now = time.time()
                try:
                    size = stdout_log.stat().st_size
                except Exception:
                    size = -1
                sig = (size, _newest_mtime(vmdl), _newest_mtime(refs))
                if sig != last_sig:
                    last_sig, last_activity = sig, now
                if now - last_activity >= MDL_MAX_IDLE_SECONDS:
                    reason = f"no activity for {MDL_MAX_IDLE_SECONDS}s"
                elif now - t0 >= MDL_MAX_RUNTIME_SECONDS:
                    reason = f"exceeded {MDL_MAX_RUNTIME_SECONDS}s"
                if reason:
                    stalled = True
                    _kill_tree(proc)
                    log.write(f"[STALL] Killed ({reason}): {rel_win}\n")
                    log.flush()
                    break
        except KeyboardInterrupt:
            _kill_tree(proc)
            raise
        rc = proc.returncode if proc.returncode is not None else -999
        log.flush()

    crashed = _recent_minidumps(source1_root, t0 - MDL_MTIME_TOLERANCE)
    note = " [crash dump found]" if crashed else ""
    if stalled:
        return False, True, rc, f"stalled ({reason}){note}"
    ok, msg = mdl_success_detect(export_dir, mdl_rel, t0)
    return ok, False, rc, msg + note

def import_model(source1_root: Path, mdl_rel: str, export_dir: Path, list_path: Path, stdout_log: Path):
    """Run cs_mdl_import.exe on one model with retries -> (ok, message)."""
    last = ""
    for attempt in range(1, MDL_MAX_RETRIES + 1):
        ok, stalled, rc, msg = _import_attempt(source1_root, mdl_rel, export_dir, list_path, stdout_log)
        if ok:
            return True, msg
        last = f"attempt {attempt}: rc={rc} | {msg}"
        if stalled:
            break
        time.sleep(0.3)
    return False, last

# ======================================================================== ASSET RESOLUTION

@lru_cache(maxsize=500_000)
def find_asset(asset_rel_path: str):
    """-> (kind, found_path, extra). Order: already in CS2 -> Source1 roots."""
    asset = normalize_candidate(asset_rel_path)
    ext = os.path.splitext(asset)[1].lower()

    # 1. already shipped with CS2 (a vmat that compiles to the error shader does not count: port it from Source1 if possible)
    if ext == ".vmat" and USE_CS2_VPK and cs2_vmat_is_stub(asset):
        if find_in_source1(os.path.splitext(asset)[0] + ".vmt")[0]:
            STUB_CS2_VMATS.add(asset)
        else:
            STUB_CS2_VMATS.add(asset + "  (no Source1 .vmt found to port from)")
            return ("cs2_verify", "cs2:" + asset + "_c", None)
    elif cs2_file_exists(asset):
        return ("cs2_verify", "cs2:" + asset, None)
    if ext in COMPILED_EXTS and ext != ".vmat" and cs2_file_exists(asset + "_c"):
        return ("cs2_verify", "cs2:" + asset + "_c", None)
    if ext == ".vmat" and asset not in STUB_CS2_VMATS and cs2_file_exists(asset + "_c") and not any(x.startswith(asset + " ") for x in STUB_CS2_VMATS):
        return ("cs2_verify", "cs2:" + asset + "_c", None)
    if ext in IMAGE_EXTS and cs2_compiled_texture_exists(asset):
        return ("cs2_verify", "cs2:" + asset, None)

    # 2. Source1 roots
    base_no_ext = os.path.splitext(asset)[0]
    if ext in (".vmat", ".vmt"):
        p, r = find_in_source1(base_no_ext + ".vmt")
        if p:
            return ("source1_vmt", p, r)
    elif ext == ".vtex":
        p, r = find_in_source1(base_no_ext + ".vtf")
        if p:
            return ("source1_vtex", p, r)
    elif ext == ".vtf":
        p, r = find_in_source1(asset)
        if p:
            return ("source1_vtf", p, r)
    elif ext == ".vmdl":
        p, r = find_in_source1(base_no_ext + ".mdl")
        if p:
            return ("source1_model", p, r)
    elif ext in IMAGE_EXTS:
        p, r = find_in_source1(asset)
        if p:
            return ("source1_raw", p, r)
    elif ext == ".vsnd":
        # CS2 map references 'sounds/x.vsnd'; source is 'sounds/x.wav|mp3', which the CS2 resource
        # compiler turns into x.vsnd_c. Source1 keeps them in 'sound/x.wav|mp3'.
        rest = base_no_ext[len("sounds/"):] if base_no_ext.startswith("sounds/") else base_no_ext
        for sub in ("sound/", "sounds/"):
            for e in (".wav", ".mp3"):
                p, r = find_in_source1(sub + rest + e)
                if p:
                    return ("source1_sound", p, r)
    elif ext in (".wav", ".mp3"):
        p, r = find_in_source1(asset)
        if not p and asset.startswith("sounds/"):
            p, r = find_in_source1("sound/" + asset[len("sounds/"):])
        if p:
            return ("source1_sound", p, r)
    return (None, None, None)

# ======================================================================== MAIN

SCRIPT_VERSION = "2026-11-01a 3.5 water volumes, radar, lighting rebuild (rebuild_lighting.py merged in)"

def main():
    global EXPORT_DIR
    print(f"Running: {os.path.abspath(__file__)}  (version {SCRIPT_VERSION})")
    _v = cs2_vpk()
    if USE_CS2_VPK:
        for _p, _c in _v.loaded:
            print(f"  CS2 VPK index: {os.path.relpath(_p, CS2_GAME_DIR)}  ({_c:,} files)")
        for _e in _v.errors:
            print(f"  [WARN] CS2 VPK not read: {_e}")
        if not _v.loaded:
            print(f"  [WARN] no CS2 *_dir.vpk found under {CS2_GAME_DIR}; using extracted folders only")
        else:
            for _top in ("particles/", "sounds/", "soundevents/", "materials/", "models/"):
                print(f"  CS2 reference {_top:<13} {sum(1 for _f in _v.files if _f.startswith(_top)):,} files in VPK")
    if CS2_VERIFY_FOLDER and os.path.isdir(CS2_VERIFY_FOLDER):
        print(f"  CS2 extracted folder in use as well: {CS2_VERIFY_FOLDER}")
    vmaps, primary = enumerate_vmaps(VMAP_PATH)
    if not vmaps:
        print("No VMAP files found to scan.")
        return
    OVERLAY_VMATS.update(static_overlay_materials(vmaps))
    vmap_root = Path(VMAP_PATH)
    if EXPORT_DIR is None:
        EXPORT_DIR = str(derive_export_dir(primary if primary else vmap_root))
    export_dir = Path(EXPORT_DIR)
    log_prefix = primary.stem if primary else vmap_root.name if vmap_root.is_dir() else vmaps[0].stem

    def log(name):
        return os.path.join(LOG_DIR, f"{log_prefix}_{name}.txt")

    # geometry repairs first, so materials they bring back (a restored floor) are converted by the asset scan below
    _ov_vmf = find_overlay_vmf(vmaps)
    if TOOL_MATERIAL_PATH_FIX:
        _tp = fix_tool_material_paths(vmaps, export_dir)
        Path(log("tool_material_paths")).write_text("\n".join(_tp), encoding="utf-8")
        print("  tool material paths: " + _tp[0].split(": ")[-1] + f"  (details: {Path(log('tool_material_paths')).name})")
    if CULLED_FACE_FIX and _ov_vmf:
        _cf = restore_culled_faces(vmaps, _ov_vmf)
        Path(log("culled_faces")).write_text("\n".join(_cf), encoding="utf-8")
        print("  culled faces: " + _cf[0].split(": ")[-1] + f"  (details: {Path(log('culled_faces')).name})")
    if ORIGIN_DUPLICATE_FIX and _ov_vmf:
        _od = remove_origin_duplicates(vmaps, _ov_vmf, BACKUP_DIR)
        Path(log("origin_duplicates")).write_text("\n".join(_od), encoding="utf-8")
        print("  origin duplicates: " + _od[0].split(": ")[-1] + f"  (details: {Path(log('origin_duplicates')).name})")
    if DISPLAY_BRUSH_TO_WORLD:
        _db = convert_display_brushes(vmaps)
        Path(log("display_brushes")).write_text("\n".join(_db), encoding="utf-8")
        print("  display brushes: " + _db[0].split(": ")[-1] + f"  (details: {Path(log('display_brushes')).name})")
    if MIRRORED_TEXTURE_FIX:
        _mt = fix_mirrored_textures(vmaps, export_dir)
        Path(log("mirrored_textures")).write_text("\n".join(_mt), encoding="utf-8")
        print("  mirrored textures: " + _mt[0].split(": ")[-1] + f"  (details: {Path(log('mirrored_textures')).name})")

    def scan_one(vp: Path):
        return str(vp), extract_assets_from_vmap(str(vp))

    all_matches, vmap_counts = set(), []
    with ThreadPoolExecutor(max_workers=VMAP_SCAN_WORKERS) as ex:
        for vp_str, extracted in ex.map(scan_one, vmaps):
            all_matches.update(extracted)
            vmap_counts.append((vp_str, len(extracted)))

    Path(log("vmaps_scanned")).write_text("\n".join(str(v) for v in vmaps), encoding="utf-8")
    Path(log("vmap_asset_counts")).write_text("\n".join(f"{c}\t{p}" for p, c in vmap_counts), encoding="utf-8")
    Path(log("raw_extracted_union")).write_text("\n".join(sorted(all_matches)), encoding="utf-8")

    dropped_by_root, filtered = [], []
    sprite_refs = {}
    sound_report = []
    generated_particles = []
    steam_built = {}      # particle name -> make_steam_particle result (built once per run)
    loop_event_names = set()
    sound_events = {}      # event name -> 'sounds/x.vsnd' (or (list of vsnds, volume)) for converted Source1 sounds
    if CONVERT_SOUNDSCRIPTS and COPY_SOUNDS:
        ss_lines = convert_soundscripts(export_dir, vmaps, sound_events, loop_event_names, sound_report)
        Path(log("soundscripts")).write_text("\n".join(ss_lines), encoding="utf-8")
    for a in sorted(all_matches):
        a2 = normalize_candidate(a)
        if a2.endswith(".spr"):
            # Source1 sprites ("sprites/glow01.spr") are really materials/sprites/glow01.vmt
            body = a2[len("materials/"):] if a2.startswith("materials/") else a2
            mat = normalize_candidate("materials/" + body[:-4] + ".vmat")
            sprite_refs[a2] = mat
            a2 = mat
        (filtered if passes_root_filter(a2) else dropped_by_root).append(a2)
    Path(log("dropped_by_rootfilter")).write_text("\n".join(sorted(set(dropped_by_root))), encoding="utf-8")

    valid_assets, invalid_assets = [], []
    for asset in filtered:
        (valid_assets if is_valid_asset_path(asset) else invalid_assets).append(asset)

    processed, copied, converted = set(), set(), set()
    failed, models_todo, unsupported = [], set(), set()
    models_imported, models_skipped, orphan_records = set(), set(), set()
    status = {}                       # asset -> why it ended up without an output

    import_enabled = IMPORT_MODELS
    if IMPORT_MODELS and not Path(CS_MDL_IMPORT_EXE).exists():
        print(f"[WARN] cs_mdl_import.exe not found ({CS_MDL_IMPORT_EXE}); models will only be listed.")
        import_enabled = False
    mdl_list_path = Path(log("mdl_single_import"))
    mdl_stdout_log = Path(log("cs_mdl_import_stdout"))
    requested_all, verified_only = set(), set()

    queue = deque(valid_assets)
    enqueued = set(valid_assets)

    referrers = {}                    # asset -> a few of the things that asked for it (for the missing_reasons log)
    cur_asset = [""]

    def note_referrer(dep: str, via: str):
        r = referrers.setdefault(dep, [])
        if via and via not in r and len(r) < 4:
            r.append(via)
    for _a in valid_assets:
        note_referrer(_a, "the map (.vmap)")

    def enqueue(dep: str):
        dep = normalize_candidate(dep)
        if dep and passes_root_filter(dep):
            note_referrer(dep, cur_asset[0])
        if dep and passes_root_filter(dep) and not dep.endswith("meshinfo.txt") \
                and dep not in processed and dep not in enqueued:
            queue.append(dep)
            enqueued.add(dep)

    def exists_out(rel: str) -> bool:
        return (export_dir / rel).exists()

    def drain_queue():
        while queue:
            asset = normalize_candidate(queue.popleft())
            if asset in processed:
                continue
            cur_asset[0] = asset
            processed.add(asset)
            requested_all.add(asset)
            kind, found, _extra = find_asset(asset)
            if not kind:
                if asset.endswith(".vpcf"):
                    _stem = os.path.splitext(os.path.basename(asset))[0]
                    _skv = parse_steam_particle_name(_stem) if (GENERATE_STEAM_PARTICLES and asset.startswith("particles/steam/")) else None
                    if _skv is not None:
                        if _stem not in steam_built:
                            steam_built[_stem] = make_steam_particle(export_dir, _stem, _skv)
                            generated_particles.extend(steam_built[_stem][2] if steam_built[_stem][0]
                                                       else [f"{_stem}\t{_steam_status(steam_built[_stem][1])}"])
                        if steam_built[_stem][0]:
                            copied.add(asset)
                        else:
                            status[asset] = steam_built[_stem][1]
                        continue
                    if asset.startswith(("particles/spritetrail/", "particles/spark/")):     # written (and rewritten every run) by the steam / trail / spark passes
                        copied.add(asset)
                        continue
                    ok_p, msg_p = (make_sprite_particle(export_dir, asset) if GENERATE_SPRITE_PARTICLES
                                   else (False, "sprite particle generation disabled"))
                    if ok_p:
                        generated_particles.append(f"{asset}\t{msg_p}")
                        copied.add(asset)
                        continue
                    status[asset] = ("particle system not in CS2; Source1 .pcf particles cannot be converted "
                                     f"({msg_p}); replace with a CS2 particle in Hammer")
                elif asset.endswith(".vsnd"):
                    status[asset] = "not in CS2 and no matching sound/*.wav|mp3 found in any Source1 root"
                else:
                    status[asset] = "not in CS2 and no Source1 source found in any root"
                continue

            if kind == "cs2_verify":
                verified_only.add(asset)

            elif kind == "source1_vmt":
                vmat_rel = (os.path.splitext(asset)[0] + ".vmat").replace("+", "_")
                if exists_out(vmat_rel) and not OVERWRITE_EXISTING_VMATS:
                    ok, info = True, "kept existing"
                else:
                    ok, info = convert_one_vmt(export_dir, found, vmat_rel)
                if ok:
                    converted.add(vmat_rel)
                    for d in parse_vmat_dependencies(str(export_dir / vmat_rel)):
                        enqueue(d)
                elif info.startswith("UNSUPPORTED_SHADER:"):
                    shader_name = info.split(' :: ')[0].split(':', 1)[1]
                    unsupported.add(f"{asset}\t{shader_name}")
                    status[asset] = f"unsupported shader ({shader_name}); no vmat written"
                else:
                    failed.append(f"{asset} :: {info}")
                    status[asset] = f"VMT found but conversion failed: {info}"

            elif kind == "source1_vtex":
                ok, info = convert_one_vtf_to_vtex(export_dir, found, asset)
                if not ok:
                    failed.append(f"{asset} :: {info}")

            elif kind == "source1_vtf":
                verified_only.add(asset)

            elif kind == "source1_raw":
                if copy_asset_to_export(export_dir, found, asset):
                    copied.add(asset)

            elif kind == "source1_sound":
                if COPY_SOUNDS:
                    if asset.endswith(".vsnd"):
                        rest = os.path.splitext(asset)[0]
                        rest = rest[len("sounds/"):] if rest.startswith("sounds/") else rest
                        dest_rel = "sounds/" + rest + os.path.splitext(found)[1].lower()
                    else:
                        dest_rel = asset
                    if asset.endswith(".vsnd"):
                        _r = os.path.splitext(asset)[0]
                        _r = _r[len("sounds/"):] if _r.startswith("sounds/") else _r
                        sound_events[SOUNDEVENT_PREFIX + _r.replace("/", ".")] = asset
                        if is_looping_sound(found):
                            loop_event_names.add(SOUNDEVENT_PREFIX + _r.replace("/", "."))
                    dest_rel, new_copy = place_sound(export_dir, found, dest_rel, sound_report)
                    if new_copy:
                        copied.add(asset if dest_rel == asset else f"{asset}\t<- {dest_rel}")
                    if dest_rel != asset and exists_out(dest_rel):
                        verified_only.add(asset)       # '<x>.vsnd' is satisfied by the copied '<x>.wav|mp3'
                else:
                    verified_only.add(asset)

            elif kind == "source1_model":
                mdl_rel = os.path.splitext(asset)[0] + ".mdl"
                imported = False
                mdl_mats = parse_mdl_materials(found)         # materials/<cdmaterials>/<name>.vmat from the .mdl
                if import_enabled:
                    if exists_out(asset) and not OVERWRITE_EXISTING_MODELS:
                        imported = True                       # already in the addon folder
                    else:
                        problems = mdl_sidecar_problems(Path(_extra), mdl_rel) if PRECHECK_SIDECARS else []
                        if problems:
                            models_skipped.add(f"{asset}\tmissing {', '.join(problems)}\t<- {found}")
                        else:
                            ok, msg = import_model(Path(_extra), mdl_rel, export_dir, mdl_list_path, mdl_stdout_log)
                            if ok:
                                imported = True
                                models_imported.add(f"{asset}\t{msg}")
                            else:
                                failed.append(f"{asset} :: model import failed :: {msg}")
                else:
                    imported = exists_out(asset)              # importer off: a model already in the addon is fine
                if imported:
                    # dependencies of what the importer produced (materials, refs, ...)
                    vmdl_out, refs_out = mdl_output_footprint(export_dir, mdl_rel)
                    if vmdl_out.is_file():
                        for d in parse_vmdl_dependencies(str(vmdl_out)):
                            enqueue(d)
                    if refs_out.is_dir():
                        for fp in refs_out.rglob("*.dmx"):
                            rooted, orphans = parse_dmx_deps_nul(str(fp))
                            for d in rooted:
                                enqueue(d)
                            for o in orphans:
                                # bare name in the DMX: find its folder via the .mdl material list,
                                # else guess materials/<model folder>/<name>
                                cands = [p for p in mdl_mats if p.rsplit("/", 1)[-1] == o.lower()]
                                if not cands:
                                    guess = normalize_candidate("materials/" + asset.rsplit("/", 1)[0] + "/" + o)
                                    if find_in_source1(os.path.splitext(guess)[0] + ".vmt")[0]:
                                        cands = [guess]
                                        enqueue(guess)
                                orphan_records.add((o, asset, tuple(cands)))
                else:
                    models_todo.add(f"{asset}\t<- {found}")
                    verified_only.add(asset)     # reported in the models log, not as missing
                # materials named in the Source1 .mdl itself
                for vmat in mdl_mats:
                    if find_in_source1(os.path.splitext(vmat)[0] + ".vmt")[0]:
                        enqueue(vmat)

    drain_queue()
    if ENABLE_STRATEGY_B_MODEL_SCAN:
        cur_asset[0] = "scan of the models already in the addon folder"
        for d in scan_addon_models_for_deps(export_dir):
            enqueue(d)
        drain_queue()

    def exists_hashed_color_vtex(folder_rel: str, base: str) -> bool:
        d = export_dir / folder_rel
        if not d.is_dir():
            return False
        base_low = base.lower()
        try:
            return any(f.lower().startswith(base_low + "_color_tga_") and f.lower().endswith((".vtex", ".vtex_c"))
                       for f in os.listdir(d))
        except Exception:
            return False

    def source1_request_satisfied(req: str) -> bool:
        low = req.lower()
        if low.endswith(".vmt") and low.startswith("materials/"):
            return exists_out(os.path.splitext(req)[0] + ".vmat")
        if low.endswith(".vsnd"):
            base_no_ext = os.path.splitext(req)[0]
            return exists_out(base_no_ext + ".wav") or exists_out(base_no_ext + ".mp3")
        if low.endswith(".vtf") and low.startswith("materials/"):
            base_no_ext = os.path.splitext(req)[0]
            cands = [base_no_ext + s for s in (".vtex", ".vtex_c", "_color.vtex", "_color.vtex_c", "_color.tga", "_color.png")]
            return any(exists_out(c) for c in cands) or \
                exists_hashed_color_vtex(os.path.dirname(base_no_ext), os.path.basename(base_no_ext))
        return False

    missing_final = set()
    for a in sorted(requested_all):
        if a in verified_only or exists_out(a) or source1_request_satisfied(a):
            continue
        missing_final.add(a)
    for t in MISSING_TEXTURES:
        if not source1_request_satisfied(t):
            missing_final.add(t)

    Path(log("processed")).write_text("\n".join(sorted(processed)), encoding="utf-8")
    Path(log("missing")).write_text("\n".join(sorted(missing_final)), encoding="utf-8")
    Path(log("copied")).write_text("\n".join(sorted(copied)), encoding="utf-8")
    Path(log("converted_vmats")).write_text("\n".join(sorted(converted)), encoding="utf-8")
    Path(log("failed")).write_text("\n".join(failed), encoding="utf-8")
    Path(log("models_to_convert")).write_text("\n".join(sorted(models_todo)), encoding="utf-8")
    Path(log("unsupported_shaders")).write_text("\n".join(sorted(unsupported)), encoding="utf-8")
    reasons = []
    same_name = {}
    if MISSING_SUGGESTIONS and missing_final:
        same_name = find_same_name_elsewhere(missing_final)
    kinds = {}
    for a in sorted(missing_final):
        users = sorted(MISSING_TEXTURE_USERS.get(a, ()))
        if status.get(a):
            why = status[a]
        elif a in MISSING_TEXTURES:
            why = "texture not found in any Source1 root or in CS2" + (f" (needed by {', '.join(users[:3])}{' ...' if len(users) > 3 else ''})" if users else "")
        else:
            why = "referenced, but no output was produced (the converter did not report why; see the failed / unsupported logs)"
        extra = []
        if referrers.get(a):
            extra.append("referenced by: " + "; ".join(referrers[a]))
        if same_name.get(a):
            extra.append("same name exists elsewhere: " + "; ".join(same_name[a]))
        reasons.append("\t".join([a, why] + extra))
        ext = os.path.splitext(a)[1].lstrip(".") or "?"
        kinds[ext] = kinds.get(ext, 0) + 1
    if kinds:
        print("  missing assets: " + ", ".join(f"{n} .{k}" for k, n in sorted(kinds.items(), key=lambda t: -t[1])) +
              f" (reasons, who referenced them and same-name files elsewhere: {log('missing_reasons')})")
    Path(log("missing_reasons")).write_text("\n".join(reasons), encoding="utf-8")
    rederived = event_names_to_sounds(export_dir, vmaps, sound_events, loop_event_names, sound_report) \
        if REDERIVE_EVENTS_FROM_NAMES else []
    kept_events = []
    if KEEP_PREVIOUS_SOUNDEVENTS:
        # entities renamed to the event name no longer contain the .vsnd path, so the harvest does not see the
        # sound again; keep what an earlier run generated instead of dropping it from the file
        for _n, (_files, _vol, _had_base) in read_generated_events(export_dir).items():
            if _n not in sound_events:
                sound_events[_n] = (_files, _vol)
                kept_events.append(_n)
                if _had_base:
                    loop_event_names.add(_n)
    Path(log("soundevent_map")).write_text(
        "\n".join(sorted(f"{', '.join(v[0] if isinstance(v, tuple) else [v])}\t-> {k}"
                         + ("\t(kept from an earlier run; no longer found in the vmap)" if k in kept_events else "")
                         for k, v in sound_events.items())),
        encoding="utf-8")
    if rederived:
        Path(log("soundevents_rederived")).write_text("\n".join(f"{n}\t{why}" for n, why in rederived), encoding="utf-8")
    Path(log("sound_report")).write_text("\n".join(sorted(sound_report)), encoding="utf-8")
    if WRITE_SOUNDEVENTS and sound_events:
        _vols = {} if SOUND_ENTITY_VOLUME else None
        _levels = {}
        _ranges = sound_ranges(vmaps, sound_events, _vols, _levels) if (SOUND_RANGE_CURVES or SOUND_ENTITY_VOLUME) else {}
        if not SOUND_RANGE_CURVES:
            _ranges = {}
        if _vols:
            _vols = {n: v for n, v in _vols.items() if n not in kept_events and v != 1.0}      # events kept from an earlier run already carry their volume
            if _vols:
                Path(log("soundevent_entity_volume")).write_text(
                    "# ambient_generic Volume (health/10) multiplied into the event volume\n" + "\n".join(f"{n}\t{v:g}" for n, v in sorted(_vols.items())),
                    encoding="utf-8")
        if _ranges:
            Path(log("soundevent_ranges")).write_text(
                "\n".join(f"{n}\t{'everywhere' if r >= SOUND_EVERYWHERE_RADIUS else int(r)}" for n, r in sorted(_ranges.items())),
                encoding="utf-8")
        write_soundevents(export_dir, sound_events, log("soundevents_generated"), loop_event_names, _ranges, _vols, _levels)
    if GENERATE_STEAM_PARTICLES:
        steam_log = ["# env_steam -> info_particle_system. Replace each entity with an info_particle_system at the same "
                     "origin/angles: effect_name = the particle below, start_active = 1 for InitialState 1.",
                     "# Source1 emits along the entity's forward axis (the direction column below). Give the info_particle_system the same angles; "
                     f"the particle sends the jet along its local {STEAM_EMIT_AXIS.upper()} axis (STEAM_EMIT_AXIS)."]
        built = steam_built
        if STEAM_FROM_VMAP:
            ents, notes_s = find_env_steam(vmaps)
            steam_log += notes_s
            steam_log.append("# vmap\tclass targetname\torigin\tangles\tSource1 jet direction\tcurrent effect_name\t-> effect_name to use\tstart_active")
            for _vm, _tn, _org, _ang, _kv, _cls, _eff in ents:
                _nm = steam_particle_name(_kv)
                if _nm not in built:
                    built[_nm] = make_steam_particle(export_dir, _nm, _kv)
                    generated_particles.extend(built[_nm][2] if built[_nm][0] else [f"{_nm}\t{_steam_status(built[_nm][1])}"])
                _want = f"particles/steam/{_nm}.vpcf"
                if not built[_nm][0]:
                    _todo = _steam_status(built[_nm][1])
                elif _cls != "info_particle_system":
                    _todo = f"{_want}   (replace this {_cls} with an info_particle_system)"
                elif _eff.lower() == _want:
                    _todo = "OK, already uses it"
                else:
                    _todo = f"{_want}   (CHANGE effect_name)"
                steam_log.append(f"{_vm}\t{_cls} {_tn or '(unnamed)'}\torigin {_org}\tangles {_ang}\t"
                                 f"{_angles_forward(_ang)}\tnow: {_eff or '-'}\t-> {_todo}\tstart_active="
                                 f"{'1' if _steam_str(_kv, 'InitialState', '0').strip() == '1' else '0'}")
            if not ents and not notes_s:
                steam_log.append("# no env_steam entities found in the .vmap files")
        for _nm, _kv in STEAM_EFFECTS.items():
            ok_s, msg_s, ln_s = make_steam_particle(export_dir, _nm, _kv)
            generated_particles.extend(ln_s if ok_s else [f"{_nm}\t{_steam_status(msg_s)}"])
            steam_log.append(f"(STEAM_EFFECTS)\t{_nm}\t" + ("effect_name=particles/steam/" + _nm + ".vpcf" if ok_s else _steam_status(msg_s)))
        Path(log("env_steam")).write_text("\n".join(steam_log), encoding="utf-8")
    Path(log("generated_particles")).write_text("\n".join(sorted(generated_particles)), encoding="utf-8")
    Path(log("sound_entity_check")).write_text(
        "\n".join(check_sound_entities(vmaps, sound_events, export_dir)), encoding="utf-8")
    if STUB_CS2_VMATS:
        Path(log("cs2_stub_materials")).write_text(
            "# CS2 ships these vmats with the error shader and no textures; they were re-ported from Source1 where possible\n"
            + "\n".join(sorted(STUB_CS2_VMATS)), encoding="utf-8")
    _mv = _move_old_backups()
    if _mv:
        print(f"  backups: moved {_mv} backup / record file(s) from the logs folder to {BACKUP_DIR}")
    rename_plus_refs(vmaps, BACKUP_DIR)
    _ov_vmf = find_overlay_vmf(vmaps)
    globals()["_ADDON_DIR"] = export_dir
    if CONVERT_FIRE_SPARKS:
        _fs = convert_fire_and_sparks(vmaps, export_dir)
        Path(log("fire_sparks")).write_text("\n".join(_fs), encoding="utf-8")
        print("  fire / sparks: " + _fs[0].split(": ")[-1] + f" ported  (details: {Path(log('fire_sparks')).name})")
    if CONVERT_STEAM_TRAILS:
        _st = convert_steam_and_trails(vmaps, export_dir)
        Path(log("steam_trails")).write_text("\n".join(_st), encoding="utf-8")
        print("  steam / sprite trails: " + _st[0].split(": ")[-1] + f" converted  (details: {Path(log('steam_trails')).name})")
    if FACE_SCALE_FIX:
        _fsl = fix_face_scales(vmaps, _ov_vmf, in_place=True)
        Path(log("face_scale_fix")).write_text("\n".join(_fsl), encoding="utf-8")
        _abs = report_absurd_scale_faces(vmaps, _ov_vmf)
        Path(log("absurd_scales")).write_text("\n".join(_abs), encoding="utf-8")
        if _abs and _abs[0].startswith("# ") and "still have" in _abs[0]:
            print("  face scales: " + _abs[0][2:].split(": ")[0] + f"  (details: {Path(log('absurd_scales')).name})")
        if not _ov_vmf:
            print("  [NOTE] face scales: no matching .vmf found, so wrong texture SCALES cannot be fixed (set STATIC_OVERLAY_VMF); texture coordinates were still checked")
    _ov_lines = static_overlay_report(vmaps, export_dir)
    if _ov_vmf:
        print(f"  static overlays: using {_ov_vmf} for the surface directions")
        _fix = fix_static_overlays(vmaps, _ov_vmf, in_place=STATIC_OVERLAY_PATCH_IN_PLACE)
        Path(log("static_overlay_fix")).write_text("\n".join(_fix), encoding="utf-8")
        _kinds = __import__("collections").Counter(_l.split("\t")[0].split(" (")[0] for _l in _fix if not _l.startswith("#"))
        print("  static overlays: " + ", ".join(f"{_n} {_k}" for _k, _n in sorted(_kinds.items())) +
              ("" if STATIC_OVERLAY_PATCH_IN_PLACE else
               "  -- nothing was written to the vmap: STATIC_OVERLAY_PATCH_IN_PLACE is False"))
        # with a .vmf the per-overlay lines above (all PLACEHOLDER, all identical values) carry nothing the fix lines do not: keep a material summary + the fix table
        _mat_where = {}
        for _ln in _ov_lines:
            _c = _ln.split("\t")
            if len(_c) > 4 and not _ln.startswith("#"):
                _mat_where.setdefault((_c[3], _c[4]), 0)
                _mat_where[(_c[3], _c[4])] += 1
        _head = [_ov_lines[0], "# Materials used by the overlays (count, where the vmat is):"]
        _head += [f"#   {_n}x  {_m}  [{_w}]" for (_m, _w), _n in sorted(_mat_where.items())]
        _head += ["# Set each overlay below to the right-hand values (the '*' ones differ). Origin is the overlay's own origin; Angles are pitch yaw roll; "
                  "size = quad corners; keep Scale 1 1 1. Rerun to check: fixed ones turn OK.", ""]
        _fix_sorted = sorted(_fix[1:], key=lambda _l: (_l.split("\t")[1] if "\t" in _l else "", _l))
        _ov_lines = _head + [_fix[0]] + _fix_sorted
    elif STATIC_OVERLAY_VMF:
        _ov_lines.insert(1, f"# NO ANGLES BELOW: STATIC_OVERLAY_VMF is set to {STATIC_OVERLAY_VMF!r} but no file exists at that path "
                            "(check the drive letter / spelling; use r\"C:\\...\\file.vmf\" with the r prefix for backslashes).")
        print(f"  [WARN] STATIC_OVERLAY_VMF not found: {STATIC_OVERLAY_VMF}")
    else:
        _ov_lines.insert(1, "# NO ANGLES/ORIENTATION BELOW: the vmap does not contain them, and no matching .vmf was found under SOURCE1_ROOTS/maps. "
                            "Set STATIC_OVERLAY_VMF (top of the script) to the decompiled .vmf and rerun.")
        print("  [NOTE] static overlays: set STATIC_OVERLAY_VMF to list the angles each overlay needs (the vmap alone cannot tell)")
    Path(log("static_overlays")).write_text("\n".join(_ov_lines), encoding="utf-8")
    if OVERLAY_ENTITY_QUADS:
        _oq = convert_entity_overlays(vmaps, export_dir)
        Path(log("overlay_entity_quads")).write_text("\n".join(_oq), encoding="utf-8")
        print("  overlay quads: " + _oq[0][2:])
    if PROP_MASS_RENAME:
        _pm = rename_props_by_mass(vmaps, BACKUP_DIR, in_place=PROP_MASS_IN_PLACE)
        Path(log("prop_mass_rename")).write_text("\n".join(_pm), encoding="utf-8")
        print("  prop masses: " + (_pm[0][2:] if _pm else "none") + f"  (details: {Path(log('prop_mass_rename')).name})")
    if TRIGGER_MATERIAL_FIX:
        _tm = fix_trigger_materials(vmaps)
        Path(log("trigger_materials")).write_text("\n".join(_tm), encoding="utf-8")
        print("  triggers: " + _tm[0].split(": ")[-1] + f"  (details: {Path(log('trigger_materials')).name})")
    if HEAL_TRIGGER_REPORT:
        _ht = report_heal_triggers(vmaps)
        Path(log("heal_triggers")).write_text("\n".join(_ht), encoding="utf-8")
        if _ht[0].split(": ")[-1] != "0":
            print("  [NOTE] heal triggers: " + _ht[0][2:] + f"  (details: {Path(log('heal_triggers')).name})")
    if NULL_PARAM_FIX:
        _np = fix_null_params(vmaps)
        Path(log("null_params")).write_text("\n".join(_np), encoding="utf-8")
        print("  null params: " + _np[0].split(": ")[-1] + f"  (details: {Path(log('null_params')).name})")
    if EQUIP_CHECK:
        _eq = check_player_equips(vmaps)
        Path(log("player_equips")).write_text("\n".join(_eq), encoding="utf-8")
        print("  equips: " + _eq[0][2:].split(". ")[0].replace("game_player_equip: ", "") + f"  (details: {Path(log('player_equips')).name})")
    if BRUSH_RENDER_FIX:
        _br = fix_brush_render(vmaps, BACKUP_DIR)
        Path(log("brush_render")).write_text("\n".join(_br), encoding="utf-8")
        print("  brushes: " + _br[0][2:].split(". ")[0] + f"  (details: {Path(log('brush_render')).name})")
    if WATER_VOLUMES:
        _wv = add_water_volumes(vmaps, export_dir, BACKUP_DIR, _ov_vmf)
        Path(log("water")).write_text("\n".join(_wv), encoding="utf-8")
        print("  " + (_wv[0][2:].split(". ")[0] if _wv else "water: none") + f"  (details: {Path(log('water')).name})")
    if LIGHTING_REBUILD in ("once", "always"):
        _lt = rebuild_map_lighting(_main_vmaps(vmaps, primary), _ov_vmf, BACKUP_DIR)
        Path(log("lighting")).write_text("\n".join(_lt), encoding="utf-8")
        for _l in _lt:
            if _l.startswith("# "):
                print("  lighting: " + _l[2:])
    if ENTITY_SEAM_REPORT:
        _es = report_entity_seams(_main_vmaps(vmaps, primary))
        Path(log("entity_seams")).write_text("\n".join(_es), encoding="utf-8")
        print("  entity seams: " + _es[0][2:].split(". ")[0].replace("entity seams: ", "") + f"  (details: {Path(log('entity_seams')).name})")
    if RADAR:
        _rd = make_radars(vmaps, primary, export_dir, BACKUP_DIR)
        Path(log("radar")).write_text("\n".join(_rd), encoding="utf-8")
        for _l in _rd:
            if _l.startswith("# ") and not _l.startswith("# radar:"):
                print("  radar: " + _l[2:])
        print(f"  radar: {sum(1 for _l in _rd if _l.startswith('WROTE'))} file(s) written, {sum(1 for _l in _rd if _l.startswith('UNCHANGED'))} unchanged, "
              f"{sum(1 for _l in _rd if _l.startswith('KEPT'))} kept as you edited them  (details: {Path(log('radar')).name})")
    if REPORT_MISSING_ENTITIES and _ov_vmf:
        _me = report_missing_entities(vmaps, _ov_vmf)
        Path(log("missing_entities")).write_text("\n".join(_me), encoding="utf-8")
        for _l in _me:
            if _l.startswith("# ") and ".vmf entities;" in _l:
                print("  missing entities: " + _l[2:].split(";")[1].strip() + f"  (details: {Path(log('missing_entities')).name})")
    if REPORT_DROPPED_IO and _ov_vmf:
        _io = report_dropped_io(vmaps, _ov_vmf)
        Path(log("dropped_io")).write_text("\n".join(_io), encoding="utf-8")
        for _l in _io:
            if _l.startswith("# ") and "owners matched" in _l:
                print("  dropped IO: " + _l[2:].replace("outputs dropped from matched owners", "outputs dropped") + f"  (details: {Path(log('dropped_io')).name})")
    if GENERATE_STEAM_PARTICLES and cs2_vpk().loaded:
        _kw = ("heat", "distort", "refract", "warp", "haze", "ripple", "wave", "shimmer", "noise", "normal", "smoke_normal", "water")
        _cands = sorted(f for f in cs2_vpk().files if f.startswith("materials/particle/") and f.endswith((".vtex_c", ".vmat_c"))
                        and any(k in os.path.basename(f) for k in _kw))
        Path(log("heatwave_texture_candidates")).write_text(
            "# CS2 particle textures whose names suggest a distortion/normal map, for STEAM_HEATWAVE_TEXTURE (use the .vtex path, without _c).\n"
            "# Refraction wants a NORMAL-style map (flat = RGB 128,128,255); ring/shockwave textures draw dark rings.\n"
            + "\n".join(c[:-2] if c.endswith("_c") else c for c in _cands), encoding="utf-8")
    if cs2_vpk().loaded:
        _pk = {"fire": ("fire", "flame", "burn", "ember", "torch"), "spark": ("spark", "electric", "zap")}
        _lines = ["# CS2 stock particles whose names suggest fire or sparks (for env_fire / env_spark, whose visuals do not work in CS2).",
                  "# Open one in Source 2 Viewer (pak01_dir.vpk) to see it; the Particle Editor cannot open stock particles."]
        for _kind, _words in _pk.items():
            _c = sorted(f[:-2] for f in cs2_vpk().files if f.startswith("particles/") and f.endswith(".vpcf_c") and any(w in os.path.basename(f) for w in _words))
            _lines += ["", f"## {_kind} ({len(_c)})"] + _c
        Path(log("fire_spark_particle_candidates")).write_text("\n".join(_lines), encoding="utf-8")
    Path(log("sprites_mapped")).write_text(
        "\n".join(f"{k}\t-> {v}" for k, v in sorted(sprite_refs.items())), encoding="utf-8")
    Path(log("models_imported")).write_text("\n".join(sorted(models_imported)), encoding="utf-8")
    Path(log("models_skipped")).write_text("\n".join(sorted(models_skipped)), encoding="utf-8")
    orphan_lines = []
    for o, model, cands in sorted(orphan_records):
        if not cands:
            orphan_lines.append(f"{o}\t(model: {model})\t-> UNRESOLVED: not in the .mdl material list, "
                                f"no VMT found in the model's folder")
            continue
        states = []
        for c in cands:
            if c in converted:
                states.append(f"{c} (converted)")
            elif c in verified_only:
                states.append(f"{c} (already in CS2)")
            elif exists_out(c):
                states.append(f"{c} (exists in addon)")
            else:
                states.append(f"{c} (NOT converted - see missing_reasons)")
        orphan_lines.append(f"{o}\t(model: {model})\t-> " + "; ".join(states))
    Path(log("dmx_orphan_vmats")).write_text("\n".join(orphan_lines), encoding="utf-8")

    print("================================================")
    print(f"Log prefix: {log_prefix}")
    print(f"VMAPs scanned: {len(vmaps)}")
    print(f"Raw extracted strings (union): {len(all_matches)}")
    print(f"Dropped by root filter: {len(set(dropped_by_root))}")
    print(f"Valid assets found: {len(valid_assets)}")
    print(f"Processed (seen): {len(processed)}")
    print(f"Already in CS2 (skipped): {len(verified_only) - len(models_todo)}")
    print(f"Converted (VMT->VMAT): {len(converted)}")
    print(f"Copied (raw images / sounds): {len(copied)}")
    print(f"Models imported (cs_mdl_import): {len(models_imported)}")
    print(f"Models skipped (missing .vvd/.vtx): {len(models_skipped)}")
    print(f"Models still needing conversion: {len(models_todo)}")
    print(f"Unsupported shaders (e.g. water): {len(unsupported)}")
    print(f"Missing (final, disk-checked): {len(missing_final)}")
    print(f"Failed conversions: {len(failed)}")
    print(f"Invalid filtered out: {len(invalid_assets)}")
    print("Addon folder (assets go here):", str(export_dir))
    print("Logs folder:", LOG_DIR)
    print("Backups folder:", BACKUP_DIR)
    print("================================================")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Importer killed. Files already written stay in the addon folder; rerun to resume.")
        sys.exit(130)
