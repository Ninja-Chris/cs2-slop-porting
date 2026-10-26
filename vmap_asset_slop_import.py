#!/usr/bin/env python3
# vmap_asset_slop_import.py
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
#                        .tga / .png atlas. Additive: lightmapped = F_TRANSLUCENT + F_ADDITIVE_BLEND, others move to
#                        csgo_unlitgeneric (F_BLEND_MODE 4). Alpha test and translucent are exclusive on lit shaders.
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
# B. REPAIRS INSIDE THE VMAP (originals are copied to the logs folder as <name>.vmap.before_overlayfix / .before_scalefix;
#    both need the decompiled Source1 .vmf, auto-found under <Source1 root>/maps or set via STATIC_OVERLAY_VMF)
#   - static overlays (decals): the importer writes a tiny placeholder quad; rebuilt to the Source1 texture size x $decalscale,
#     laid on the surface found in the .vmf (origin, angles, mirrored UVs). <prefix>_static_overlay_fix.txt
#   - brush face texture mapping: the importer reduces shifts to 0..16 and (without the textures) mangles scales; the original
#     axes / scale / shift are copied back from the matching .vmf brush side and the face texture coordinates are rewritten
#     from the Source1 texture size. <prefix>_face_scale_fix.txt (faces it could not match are left as imported)
#
# C. EXTRAS
#   - soundevents/soundevents_addon.vsndevts: one event per converted sound (named like the path, e.g. mysounds.mh_blade_loop1;
#     set that name on the entity in Hammer, see <prefix>_sound_report.txt / _soundevent_map.txt), distance curves from the
#     ambient_generic radius, looping base event, plus ported Source1 soundscript names
#   - env_steam -> particles/steam/*.vpcf named after the Source1 keyvalues (see <prefix>_generated_particles.txt for the
#     effect_name to use). Type 1 (heat wave) is a refracting sprite; STEAM_HEATWAVE_TEXTURE = "source1vmt:sprites/heatwave"
#     uses the original material's normal map and refract amount
# Not done: water, Source1 .pcf particles, editing entity keyvalues in the vmap (sound event names are set by hand in Hammer).
#
# Stock Source 2 copies (the old COPYABLE_ROOTS) are no longer copied from, so a stale
# pre-converted .vmat can no longer win over the Source1 VMT.

import math
import pickle
import os
import random
import re
import shutil
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
VMAP_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\content\csgo_addons\cs2addonhere\maps"
EXPORT_DIR = None                      # None -> addon folder = parent of the "maps" folder
CS2_VERIFY_FOLDER = r"D:\Counter-Strike\Assets\CS2\cs2"
USE_CS2_VPK = True                     # read the CS2 install's *_dir.vpk indexes directly (always up to date, nothing to extract)
CS2_GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game"
CS2_VPK_FOLDERS = ["csgo", "csgo_core", "core"]      # folders under CS2_GAME_DIR whose *_dir.vpk files are indexed
CS2_VPK_SKIP_PREFIXES = ("shaders",)   # *_dir.vpk files starting with these are ignored (shader caches, not content)
CS2_EXTRA_VERIFY_FOLDERS = []              # more folders laid out like CS2_VERIFY_FOLDER (e.g. extracted particles\ or sounds\). Extracted folders are used
                                       # in addition to the VPK index (and alone if USE_CS2_VPK is False or no VPK is found)

# Source1 roots, searched in this order. Each root holds materials\, models\, sound\ ...
SOURCE1_ROOT = r"C:\map_sources\decompiledmaphere_d"            # the map's own content
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
STATIC_OVERLAY_PATCH_IN_PLACE = True   # True: patch the overlays inside the vmap itself (Hammer must be closed); the untouched file is first copied to the logs folder as <name>.vmap.before_overlayfix. False: only list, per overlay, the origin/angles/size it should have (static_overlay_fix log) and change nothing
STATIC_OVERLAY_ONLY_PLACEHOLDERS = True   # patching only touches overlays that are still the importer's untouched 2x2 quad (angles 0, scale 1). Any overlay you changed by hand (even just the scale) is skipped. False: patch every overlay that differs from the .vmf target
STATIC_OVERLAY_PROJECTION_FAR = None   # e.g. 8.0: every static overlay's projection depth is set to this (also on overlays that are already patched / hand-edited; the importer sometimes writes 64, which shows as a 2x2x64 box and the overlay is not visible). The overlay origin sits NORMAL_OFFSET above the surface, so only far - offset reaches into it. None = leave the depth alone
FACE_SCALE_FIX = True   # repair brush face texture scale/shift the importer got wrong (it does not read the Source1 textures: scale comes out as 0.25/256 etc.). Needs the .vmf (same auto-find as the overlays); patched in place, original kept in the logs folder as <name>.vmap.before_scalefix
LIT_ADDITIVE_VIA_TRANSLUCENT = True   # additive materials on csgo_lightmappedgeneric stay there with F_TRANSLUCENT 1 + F_ADDITIVE_BLEND 1 (confirmed from a saved lightmapped vmat: additive only becomes available once translucent is on). False: move them to csgo_unlitgeneric (F_BLEND_MODE 4) as before. Vertexlit materials are always moved to unlit (no confirmed additive flag there). Alpha test and translucent are exclusive on the lit shaders: alpha test wins unless the material is additive
MISSING_SUGGESTIONS = True   # missing_reasons log: also say what referenced each missing asset and whether a file with the same name exists elsewhere in the Source1 folders / CS2 (one extra scan of the folders when something is missing)
FACE_SCALE_BAD_RATIO = (0.75, 1.5)   # a face counts as broken when vmap scale / .vmf scale is outside this range on either axis (axis normalisation alone gives up to ~1.3)
STATIC_OVERLAY_TEX_FLIP = "v"          # how the texture is laid on the quad: "" (importer's UVs), "u", "v" or "uv" (mirror the image left-right / up-down / both). Absolute values are written, so reruns do not flip twice
STATIC_OVERLAY_NORMAL_OFFSET = 4.0     # the importer puts each overlay origin this far above the surface (+4 in Z on the floor decals)
SOUND_RANGE_MIN = 0.0                  # floor for the curve end distance after scaling (0 = none)
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
GENERATE_STEAM_PARTICLES = True        # write a particle for each STEAM_EFFECTS entry (env_steam replacement)
STEAM_FROM_VMAP = True                 # read env_steam entities from the (text) .vmap files and build a particle for each distinct set of keyvalues
STEAM_SIZE_TO_RADIUS = 0.5             # Source1 StartSize/EndSize -> CS2 radius (0.5 = Source1 size is the full quad width; 1.0 looked far too wide)
STEAM_EMISSIVE_ADD = (0, 0, 0)         # added to an Emissive jet's rendercolor (0 = use rendercolor as-is; it made no visible difference)
STEAM_HEATWAVE_MODE = "refract"        # env_steam type 1 (heat wave) is a distortion, not visible steam: "refract" = refracting sprites (no colour, just the shimmer), "sprite" = soft white steam instead, "skip" = build nothing
STEAM_HEATWAVE_TEXTURE = ""                # "" = generate a soft noisy normal map (default; raise STEAM_HEATWAVE_NORMAL_STRENGTH for a stronger ripple). Alternatives: "source1vmt:sprites/heatwave" = the real Source1 heat-wave material: converts its $normalmap (effects/fisheyelens_normal) as a proper normal map (needs that vtf + the vmt under SOURCE1_ROOTS); "source1:particle/particle_smokegrenade" = convert that Source1 texture and refract with it; "materials/particle/warp5_explosion.vtex" = stock CS2 shockwave texture (draws a dark ring); or any stock CS2 .vtex path (see the heat-wave candidates log)
STEAM_HEATWAVE_SPIN = 120.0              # heat-wave sprites: spin speed range (+- degrees/second). Source1 gives each heat-wave quad a randomly tilted normal every frame (a flutter) instead of rolling it; spinning the lens normal map is the closest CS2 equivalent. 0 = no flutter
STEAM_HEATWAVE_NORMAL_STRENGTH = 3.0      # strength of the generated normal map (bigger = stronger ripple)
STEAM_HEATWAVE_REFRACT = None          # refract amount. None = the $refractamount of the Source1 material when STEAM_HEATWAVE_TEXTURE is "source1vmt:..." (the heat-wave material says -0.007, which gives the invisible distortion the original has), otherwise 0.25 (what the stock reference particle uses). A number overrides both
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

VALID_PATH_CHARS_RE = re.compile(r'^[a-zA-Z0-9_\-./&() ]+$')

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
    dest = export_dir / Path(rel_norm(relative_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(source_path, str(dest))
        return True
    return False

# ======================================================================== VMAP HARVEST

PAT_BYTES = re.compile(rb'[A-Za-z0-9_\-./\\&() ]{2,}\.[A-Za-z0-9_]{1,8}')
PAT_ASCII_IN_STR = re.compile(r'[A-Za-z0-9_\-./\\&() ]{2,}\.[A-Za-z0-9_]{1,8}')

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
PATH_RE_TEXT = re.compile(rf'([A-Za-z0-9_\-./\\&() ]+\.{EXT_RE})', re.IGNORECASE)

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
    if t.endswith(suffix):
        return "materials/" + t
    return "materials/" + t + suffix

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
            cache = os.path.join(LOG_DIR, ".vpkcache2_" + re.sub(r"[^A-Za-z0-9]+", "_", path)[-120:] + ".pkl")
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
                    if fp.startswith(VPK_READ_PREFIXES) or fp.endswith(".vmat_c"):
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

    # ---- separate translucency texture (lightmapped shader reads it from TextureLayer1Translucency)
    trans_rel = None
    if shader_name != "csgo_unlitgeneric.vfx" and not data["additive"] and (data["translucent"] or data["alphatest"]) and not anim:
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

def sound_ranges(vmap_paths, sound_events: dict):
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
            try:
                rad = float(p.get("radius", "") or 0) or SOUND_DEFAULT_RADIUS
            except ValueError:
                rad = SOUND_DEFAULT_RADIUS
            if flags & 1:
                rad = SOUND_EVERYWHERE_RADIUS
            if rad < SOUND_EVERYWHERE_RADIUS:
                rad = max(rad * SOUND_RANGE_SCALE, SOUND_RANGE_MIN)
            out[name] = max(out.get(name, 0.0), rad)
    return out

def _curve_lines(prop: str, radius: float, end_vol: float):
    return [f'\t\t{prop} = ', '\t\t[',
            '\t\t\t[', '\t\t\t\t0.0, 1.0, 0.0, 0.0,', '\t\t\t\t2.0, 3.0,', '\t\t\t],',
            '\t\t\t[', f'\t\t\t\t{radius:.1f}, {end_vol:.1f}, 0.0, 0.0,', '\t\t\t\t2.0, 3.0,', '\t\t\t],',
            '\t\t]']

def write_soundevents(export_dir: Path, events: dict, log_copy: str, loop_names=(), ranges=None):
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
            vol_txt = f"{float(vol):.3f}" if vol is not None and 0 < float(vol) <= 1 else "1.0"
        except ValueError:
            vol_txt = "1.0"
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
        if SOUND_RANGE_CURVES and ranges and name in ranges:
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

def wav_to_pcm16(src: str, dst: str, info):
    import miniaudio, wave
    _tag, ch, rate, _bits = info
    dec = miniaudio.decode_file(src, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=ch, sample_rate=rate)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(dst, "wb") as w:
        w.setnchannels(dec.nchannels)
        w.setsampwidth(2)
        w.setframerate(dec.sample_rate)
        w.writeframes(dec.samples.tobytes())
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

def parse_soundscript_text(text: str) -> dict:
    """game_sounds*.txt -> {lower name: (Name, [wave paths], volume or None)} (all waves, incl. rndwave)."""
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
    body = VPCF_HEADER + (
        "{\n\t_class = \"CParticleSystemDefinition\"\n\tm_nBehaviorVersion = 12\n\tm_nMaxParticles = 16\n"
        "\tm_ConstantColor = [ 255, 255, 255, 255 ]\n"
        f"\tm_flConstantRadius = {SPRITE_PARTICLE_RADIUS}\n"
        "\tm_Renderers =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_RenderSprites\"\n"
        "\t\t\tm_nOutputBlendMode = \"PARTICLE_OUTPUT_BLEND_MODE_ADD\"\n"
        "\t\t\tm_vecTexturesInput =\n\t\t\t[\n\t\t\t\t{\n"
        f"\t\t\t\t\tm_hTexture = resource:\"{vtex_rel}\"\n" "\t\t\t\t},\n\t\t\t]\n\t\t},\n\t]\n"
        "\tm_Emitters =\n\t[\n\t\t{\n\t\t\t_class = \"C_OP_ContinuousEmitter\"\n\t\t\tm_flEmitRate = 40.0\n\t\t},\n\t]\n"
        "\tm_Initializers =\n\t[\n\t\t{\n\t\t\t_class = \"C_INIT_CreateWithinSphere\"\n\t\t\tm_fRadiusMin = 0.0\n\t\t\tm_fRadiusMax = 0.0\n\t\t},\n"
        "\t\t{\n\t\t\t_class = \"C_INIT_RandomLifeTime\"\n\t\t\tm_fLifetimeMin = 0.15\n\t\t\tm_fLifetimeMax = 0.15\n\t\t},\n\t]\n"
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
                _ra = re.search(r'"?\$refractamount"?\s+"?(-?[0-9.]+)', _vd.get("text", ""), flags=re.I)
                if _ra and STEAM_HEATWAVE_REFRACT is None:
                    refract_amt = float(_ra.group(1))
                    tex_note += f"; refract amount {refract_amt:g} taken from the material"
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
             f"texture: {tex_note}"]
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

_DMX_SIZES = {2: 4, 3: 4, 4: 1, 7: 4, 8: 4, 9: 8, 10: 12, 11: 16, 12: 12, 13: 16, 14: 64, 15: 8, 16: 1}

def read_binary_dmx(d: bytes):
    """Parse a binary DMX file (encoding 'binary 9', e.g. a CS2 .vmap) -> list of elements
    {"type": str, "name": str, "attrs": {name: (basetype, is_array, value)}}. Element-reference values are element indexes.
    Raises ValueError if the layout does not add up (so callers can fall back)."""
    head = d[:80].split(b"\n", 1)[0].decode("latin-1", errors="ignore")
    if "encoding binary 9" not in head:
        raise ValueError(f"unsupported DMX header: {head.strip()[:60]}")
    pos = d.index(b"\n") + 2                                   # '\n' and the NUL after the header line
    u32 = lambda p_: struct.unpack_from("<I", d, p_)[0]          # noqa: E731

    def cstr(p_):
        e = d.index(b"\0", p_)
        return d[p_:e].decode("utf-8", "replace"), e + 1

    def skip_value(bt, p_, arr):
        if bt == 5:
            return cstr(p_)[1]
        if bt == 6:
            return p_ + 4 + u32(p_)
        return p_ + _DMX_SIZES[bt]

    try:
        npre = u32(pos); pos += 4                               # prefix element(s): their strings are inline
        for _ in range(npre):
            na = u32(pos); pos += 4
            for _ in range(na):
                _n, pos = cstr(pos)
                t = d[pos]; pos += 1
                if t >= 32:
                    c = u32(pos); pos += 4
                    for _ in range(c):
                        pos = skip_value(t - 32, pos, True)
                else:
                    pos = skip_value(t, pos, False)
        nstr = u32(pos); pos += 4
        strs = []
        for _ in range(nstr):
            st, pos = cstr(pos)
            strs.append(st)
        nel = u32(pos); pos += 4
        els = []
        for _ in range(nel):
            ti, ni = struct.unpack_from("<ii", d, pos)
            pos += 8 + 16                                       # type name, element name, GUID
            els.append({"type": strs[ti], "name": strs[ni] if ni >= 0 else "", "attrs": {}})
        for e in els:
            na = u32(pos); pos += 4
            for _ in range(na):
                name = strs[u32(pos)]; pos += 4
                t = d[pos]; pos += 1
                arr = t >= 32
                bt = t - 32 if arr else t

                def one(p_):
                    if bt in (1, 2):
                        return struct.unpack_from("<i", d, p_)[0], p_ + 4
                    if bt == 3:
                        return struct.unpack_from("<f", d, p_)[0], p_ + 4
                    if bt == 4:
                        return bool(d[p_]), p_ + 1
                    if bt == 5:
                        return cstr(p_) if arr else (strs[u32(p_)], p_ + 4)
                    if bt == 6:
                        n = u32(p_)
                        return d[p_ + 4:p_ + 4 + n], p_ + 4 + n
                    if bt in (9, 10, 11, 12, 13, 14):
                        return struct.unpack_from("<%df" % (_DMX_SIZES[bt] // 4), d, p_), p_ + _DMX_SIZES[bt]
                    if bt == 8:
                        return tuple(d[p_:p_ + 4]), p_ + 4
                    if bt == 15:
                        return struct.unpack_from("<Q", d, p_)[0], p_ + 8
                    if bt in (7, 16):
                        return (u32(p_), p_ + 4) if bt == 7 else (d[p_], p_ + 1)
                    raise ValueError(f"unknown DMX attribute type {t}")
                if arr:
                    c = u32(pos); pos += 4
                    e.setdefault("offs", {})[name] = pos          # byte offset of the first element (for in-place patching)
                    v = []
                    for _ in range(c):
                        x, pos = one(pos)
                        v.append(x)
                else:
                    e.setdefault("offs", {})[name] = pos
                    v, pos = one(pos)
                e["attrs"][name] = (bt, arr, v)
    except (struct.error, IndexError, KeyError) as ex:
        raise ValueError(f"binary DMX parse failed: {ex}")
    if pos != len(d):
        raise ValueError(f"binary DMX parse ended at {pos} of {len(d)} bytes")
    return els

def _fmt_vec(v, nd=1) -> str:
    return " ".join(f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else str(x) for x in v)

@lru_cache(maxsize=8)
def vmap_entities(path: str):
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

def fix_static_overlays(vmap_paths, vmf_path: str, out_dir: str, in_place=False):
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
            for i, (px, py, pz) in enumerate(pos_off[1]):
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
                bak = os.path.join(out_dir, os.path.basename(str(vp)) + ".before_overlayfix")
                if os.path.exists(bak):          # keep the first backup (the untouched import); later patches get a timestamped one
                    bak += time.strftime(".%Y%m%d_%H%M%S")
                Path(bak).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(vp), bak)
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
                        out.append(dict(n=n, d=_v_dot(n, pl[0]), solid=planes, mat=k.get("material", "").lower().replace("\\", "/"),
                                        us=float(ua.group(2)), vs=float(va.group(2)),
                                        ua=[float(x) for x in ua.group(1).split()[:4]], va=[float(x) for x in va.group(1).split()[:4]]))
                    except ValueError:
                        pass
                if allpts:      # make every plane of this brush point outwards, so "inside the brush" can be tested
                    cen = [sum(q[j] for q in allpts) / len(allpts) for j in range(3)]
                    for i, (n_, d_) in enumerate(planes):
                        if _v_dot(n_, cen) - d_ > 0:
                            planes[i] = ([-x for x in n_], -d_)
            elif ch[0] in ("world", "entity"):
                walk(ch)
    walk(root)
    return out

def fix_face_scales(vmap_paths, vmf_path: str, out_dir: str, in_place=True):
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
    for sd in sides:
        idx.setdefault(sd["mat"], {}).setdefault(math.floor(sd["d"]), []).append(sd)
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
            tcs = None
            if me.get("faceVertexData") and "edgeVertexDataIndices" in me:
                for si in els[me["faceVertexData"][2]]["attrs"].get("streams", (0, 0, []))[2]:
                    if isinstance(si, int) and els[si]["name"].lower().startswith("texcoord") and "data" in els[si].get("offs", {}):
                        tcs = (els[si]["offs"]["data"], els[si]["attrs"]["data"][2])
            evd = me["edgeVertexDataIndices"][2] if tcs else []
            evi, nxt, vdi = me["edgeVertexIndices"][2], me["edgeNextIndices"][2], me["vertexDataIndices"][2]
            scs, us, vs_, mis = (st[k]["attrs"]["data"][2] for k in ("textureScale", "textureAxisU", "textureAxisV", "materialindex"))
            for f, he in enumerate(me["faceEdgeIndices"][2]):
                if f >= len(scs) or not (0 <= mis[f] < len(mats)):
                    continue
                mat = mats[mis[f]].replace("\\", "/").lower()
                ratio, uvv, tgt = None, None, None
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
                        d = _v_dot(n, pts[0])
                        key = mat[len("materials/"):-5] if mat.startswith("materials/") and mat.endswith(".vmat") else mat
                        _bk = idx.get(key, {})
                        _bs = sorted({b_ for c_ in (math.floor(d), math.floor(-d)) for b_ in (c_ - 1, c_, c_ + 1)})
                        cands = [s_ for b_ in _bs for s_ in _bk.get(b_, [])
                                 if (_v_dot(s_["n"], n) > 0.999 and abs(s_["d"] - d) < 0.5) or (_v_dot(s_["n"], n) < -0.999 and abs(s_["d"] + d) < 0.5)]
                        if len(cands) > 1:
                            # brushes that overlap in this plane (adjacent blocks, stacked boxes): keep the one the face actually lies inside
                            cen = [sum(q[j] for q in pts) / len(pts) for j in range(3)]
                            c0 = [c for c in cands if all(_v_dot(n_, cen) - d_ <= 0.6 for n_, d_ in c["solid"])]
                            if c0:
                                cands = c0
                        if len(cands) > 1:
                            # several brushes share this plane + material (e.g. crates rotated differently on one floor): keep the sides whose texture
                            # axes are the ones stored on this face, then those whose shift agrees with the stored one modulo 16 (the importer's reduction)
                            c1 = [c for c in cands if len(c["ua"]) == 4 and sum(abs(c["ua"][k] - us[f][k]) + abs(c["va"][k] - vs_[f][k]) for k in range(3)) < 0.02]
                            if c1:
                                cands = c1
                            if len(cands) > 1:
                                c2 = [c for c in cands if len(c["ua"]) == 4 and all(min((c[ax][3] - cur[3]) % 16.0, 16.0 - (c[ax][3] - cur[3]) % 16.0) < 0.1
                                                                                      for ax, cur in (("ua", us[f]), ("va", vs_[f])))]
                                if c2:
                                    cands = c2
                        if cands:
                            sd = min(cands, key=lambda c: abs(scs[f][0] / c["us"] - 1) + abs(scs[f][1] / c["vs"] - 1))      # coplanar sides: take the closest scale
                            if sd["us"] and sd["vs"]:
                                ratio = (scs[f][0] / sd["us"], scs[f][1] / sd["vs"])
                            # unambiguous: every coplanar side of this material maps the texture identically -> its mapping is the truth for this face
                            if len(sd["ua"]) == 4 and len(sd["va"]) == 4 and all(
                                    max(abs(c["ua"][k] - sd["ua"][k]) + abs(c["va"][k] - sd["va"][k]) for k in range(4)) < 0.01
                                    and abs(c["us"] - sd["us"]) < 1e-4 and abs(c["vs"] - sd["vs"]) < 1e-4 for c in cands):
                                tgt = sd
                        else:
                            stat["no .vmf brush on this plane + material"] += 1
                        if cands and not tgt:
                            stat["several .vmf brushes with different mappings overlap this face (left as imported)"] += 1
                faces.append(dict(mat=mat, so=st["textureScale"]["offs"]["data"] + 8 * f, uo=st["textureAxisU"]["offs"]["data"] + 16 * f,
                                  vo=st["textureAxisV"]["offs"]["data"] + 16 * f, sc=scs[f], u=us[f], v=vs_[f], ratio=ratio, tgt=tgt,
                                  uv=uvv, tco=tcs[0] if tcs else None, tcd=tcs[1] if tcs else None))
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
            ts = _overlay_texture_size(mat)         # (width, height, decalscale) of the Source1 texture, or None
            if not ts and not any(_scale_bad(fc["sc"]) for fc in fl):
                # no Source1 texture: use the size the stored coordinates imply (what Hammer itself used for this material)
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
                    wrongs = fix_scale
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
                        cnt[3] += 1
                elif fix_scale and not ts:
                    cnt[2] += 0
                    no_tex.add(mat)
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
            bak = os.path.join(out_dir, os.path.basename(str(vp)) + ".before_scalefix")
            if os.path.exists(bak):
                bak += time.strftime(".%Y%m%d_%H%M%S")
            Path(bak).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(vp), bak)
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

SCRIPT_VERSION = "2026-10-26zk cleanup"

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
                vmat_rel = os.path.splitext(asset)[0] + ".vmat"
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
        _ranges = sound_ranges(vmaps, sound_events) if SOUND_RANGE_CURVES else {}
        if _ranges:
            Path(log("soundevent_ranges")).write_text(
                "\n".join(f"{n}\t{'everywhere' if r >= SOUND_EVERYWHERE_RADIUS else int(r)}" for n, r in sorted(_ranges.items())),
                encoding="utf-8")
        write_soundevents(export_dir, sound_events, log("soundevents_generated"), loop_event_names, _ranges)
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
                                 f"{'0' if _steam_str(_kv, 'InitialState', '1').strip() == '0' else '1'}")
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
    _ov_vmf = find_overlay_vmf(vmaps)
    if FACE_SCALE_FIX:
        _fsl = fix_face_scales(vmaps, _ov_vmf, LOG_DIR, in_place=True)
        Path(log("face_scale_fix")).write_text("\n".join(_fsl), encoding="utf-8")
        if not _ov_vmf:
            print("  [NOTE] face scales: no matching .vmf found, so wrong texture SCALES cannot be fixed (set STATIC_OVERLAY_VMF); texture coordinates were still checked")
    _ov_lines = static_overlay_report(vmaps, export_dir)
    if _ov_vmf:
        print(f"  static overlays: using {_ov_vmf} for the surface directions")
        _fix = fix_static_overlays(vmaps, _ov_vmf, LOG_DIR, in_place=STATIC_OVERLAY_PATCH_IN_PLACE)
        Path(log("static_overlay_fix")).write_text("\n".join(_fix), encoding="utf-8")
        _kinds = __import__("collections").Counter(_l.split("\t")[0].split(" (")[0] for _l in _fix if not _l.startswith("#"))
        print("  static overlays: " + ", ".join(f"{_n} {_k}" for _k, _n in sorted(_kinds.items())) +
              ("" if STATIC_OVERLAY_PATCH_IN_PLACE else
               "  -- nothing was written to the vmap: STATIC_OVERLAY_PATCH_IN_PLACE is False"))
        for _l in _fix:
            if _l.startswith("# ") and "patched" in _l:
                print("  " + _l)
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
    if GENERATE_STEAM_PARTICLES and cs2_vpk().loaded:
        _kw = ("heat", "distort", "refract", "warp", "haze", "ripple", "wave", "shimmer", "noise", "normal", "smoke_normal", "water")
        _cands = sorted(f for f in cs2_vpk().files if f.startswith("materials/particle/") and f.endswith((".vtex_c", ".vmat_c"))
                        and any(k in os.path.basename(f) for k in _kw))
        Path(log("heatwave_texture_candidates")).write_text(
            "# CS2 particle textures whose names suggest a distortion/normal map, for STEAM_HEATWAVE_TEXTURE (use the .vtex path, without _c).\n"
            "# Refraction wants a NORMAL-style map (flat = RGB 128,128,255); ring/shockwave textures draw dark rings.\n"
            + "\n".join(c[:-2] if c.endswith("_c") else c for c in _cands), encoding="utf-8")
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
    print("================================================")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Importer killed. Files already written stay in the addon folder; rerun to resume.")
        sys.exit(130)
