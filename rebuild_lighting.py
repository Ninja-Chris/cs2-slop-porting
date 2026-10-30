r"""Rebuild the lighting of a ported CS2 map (.vmap) from the Source 1 map (.vmf).

Valve's importer gets CS:GO lights wrong (brightness comes out as NaN, so Hammer resets it to 1000 lumens, and the
colour is turned into a 6600 K white).  This script throws away every light, light probe volume and visibility hint in
the vmap and builds them again from the .vmf, then runs the light probe, stationary light and vis passes on top.
Those passes are skink52's (github.com/skink52/cs2portdocs, tools/: dmx.py, kv2.py, vmapedit.py, lighting.py, visfit.py plus a
probe-volume pass, bundled unchanged below; AI-generated, shared by skink52 for the porting knowledge in them).

  1. delete       light_omni2, light_barn, light_rect, light_capsule, light_omni, light_ortho, light_spot, light,
                  env_combined_light_probe_volume and visibility_hint (hand-placed ones too)
  2. recreate     from the .vmf:  light -> light_omni2,  light_spot -> light_barn,  keeping targetname / parentname
        brightness   CS:GO intensity _light[3] x _lightHDR[3] x _lightscaleHDR, times (max rgb/255)^2.2 because the colour is
                     normalised to full brightness; legacy = I/265 (the importer's own scale), omni lumens = 1000 x legacy,
                     barn lumens = legacy x 1000/(4 pi) x the solid angle of the cone (matches the importer's own numbers)
        colour       the real _light colour (colormode 0), not the importer's white 6600 K
        range        _distance / _zero_percent_distance, else the distance where the light has faded out
        attenuation  CS2 lights are inverse square: lights with _constant_attn / _linear_attn get the normal brightness and are listed
        cone         light_spot _cone / _inner_cone -> light_barn size and softness; pitch / target / angles -> direction
        switching    lights that start dark, have a style, or are named and targeted by an output become Dynamic
                     (directlight 2) because a Static light cannot be switched off
  3. sun          light_environment is kept (the importer's values are right) -- --sun-scale scales its brightness
  4. underwater   Source water is lit by the lightmap, CS2 water volumes need lights: a few dim lights are put inside each
                  func_water / func_water_analog with no light already inside it (--no-underwater to skip)
  5. passes       light probe volumes, stationary lights and vis hints (skink52's code, bundled in this file).  The extra lights that pass adds to unlit rooms get the map's median brightness
                  (its template light is otherwise a 1000 lumen default)

Safe by default: writes <map>_rebuilt.vmap next to the original and leaves the original alone.
Close the map in Hammer first, or Hammer will overwrite the changes when you save.

Needs Python 3 with numpy and scipy:   py -m pip install numpy scipy
One file: it needs nothing else.

Usage:
  py rebuild_lighting.py "C:\...\maps\mymap.vmap" --vmf "C:\...\mymap.vmf"
  py rebuild_lighting.py mymap.vmap --vmf mymap.vmf --in-place            backs up to maps\backups\, then overwrites
  py rebuild_lighting.py mymap.vmap --vmf mymap.vmf --light-scale 0.8      all point / spot lights 20% dimmer
  py rebuild_lighting.py mymap.vmap --vmf mymap.vmf --sun-scale 0.9        sun 10% dimmer
  py rebuild_lighting.py mymap.vmap --vmf mymap.vmf --skip-passes          only rebuild the lights
  py rebuild_lighting.py mymap.vmap --vmf mymap.vmf --exposure 0.8 1.0     post_processing_volume min / max exposure
  py rebuild_lighting.py mymap.vmap --restore "maps\backups\mymap.20261029-190000.vmap"    undo

The light scale (1000 lumens per 265 intensity) and the spotlight lumens come from the importer's own output and from the
cs2mapping repo; they are not checked against a real CS2 compile.  Do a full lighting bake and compare a few rooms to
CS:GO, then use --light-scale / --sun-scale to tune.
"""
import sys, types

_MODULES = {}

_MODULES['dmx'] = '"""Read/write Source 2 binary DMX (encoding binary 9) - the .vmap format.\n\ndoc = dmx.load(path) -> Doc with .header, .prefix (list of attr lists), .elements\nElement = dict(type=bytes, name=bytes, guid=bytes(16), attrs=list[[name, type, value]])\nElement references are ints (index into doc.elements), -1 = null.\n"""\nimport struct\n\nT_ELEM, T_INT, T_FLOAT, T_BOOL, T_STR, T_BIN, T_TIME, T_COLOR, T_V2, T_V3, T_V4, T_QANG, T_QUAT, T_MAT, T_U64, T_U8 = range(1, 17)\nARR = 32\n_fix = {T_INT: (\'<i\', 4), T_FLOAT: (\'<f\', 4), T_BOOL: (\'<?\', 1), T_TIME: (\'<i\', 4), T_COLOR: (\'<4B\', 4), T_V2: (\'<2f\', 8),\n        T_V3: (\'<3f\', 12), T_V4: (\'<4f\', 16), T_QANG: (\'<3f\', 12), T_QUAT: (\'<4f\', 16), T_MAT: (\'<16f\', 64), T_U64: (\'<Q\', 8), T_U8: (\'<B\', 1)}\n\nclass R:\n    def __init__(s, d, p=0): s.d = d; s.p = p\n    def i(s): v = struct.unpack_from(\'<i\', s.d, s.p)[0]; s.p += 4; return v\n    def b(s): v = s.d[s.p]; s.p += 1; return v\n    def cstr(s): e = s.d.index(b\'\\0\', s.p); v = s.d[s.p:e]; s.p = e + 1; return v\n    def raw(s, n): v = s.d[s.p:s.p + n]; s.p += n; return v\n    def fmt(s, f, n):\n        v = struct.unpack_from(f, s.d, s.p); s.p += n\n        return v[0] if len(v) == 1 else list(v)\n\nclass Doc:\n    def __init__(s): s.header = b\'\'; s.prefix = []; s.elements = []\n\ndef _read_val(r, t, strtab, inline_str):\n    if t == T_ELEM:\n        v = r.i()\n        if v == -2: return (\'guid\', r.cstr())\n        return v\n    if t == T_STR: return r.cstr() if inline_str else strtab[r.i()]\n    if t == T_BIN: n = r.i(); return r.raw(n)\n    f, n = _fix[t]; return r.fmt(f, n)\n\ndef _read_attr(r, strtab, inline_str):\n    t = r.b()\n    if t > ARR:\n        n = r.i(); bt = t - ARR\n        if bt == T_STR:  # string arrays are always inline\n            return t, [r.cstr() for _ in range(n)]\n        return t, [_read_val(r, bt, strtab, inline_str) for _ in range(n)]\n    return t, _read_val(r, t, strtab, inline_str)\n\ndef loads(d):\n    doc = Doc(); e = d.index(b\'\\n\\x00\'); doc.header = d[:e + 1]\n    r = R(d, e + 2)\n    for _ in range(r.i()):\n        attrs = []\n        for _ in range(r.i()):\n            name = r.cstr(); t, v = _read_attr(r, None, True); attrs.append([name, t, v])\n        doc.prefix.append(attrs)\n    strtab = [r.cstr() for _ in range(r.i())]\n    n = r.i()\n    for _ in range(n):\n        doc.elements.append({\'type\': strtab[r.i()], \'name\': strtab[r.i()], \'guid\': r.raw(16), \'attrs\': []})\n    for el in doc.elements:\n        for _ in range(r.i()):\n            name = strtab[r.i()]; t, v = _read_attr(r, strtab, False); el[\'attrs\'].append([name, t, v])\n    if r.p != len(d): raise ValueError(\'trailing data %d/%d\' % (r.p, len(d)))\n    return doc\n\ndef load(path): return loads(open(path, \'rb\').read())\n\ndef _w_val(out, t, v, sidx, inline):\n    if t == T_ELEM:\n        if isinstance(v, tuple): out += struct.pack(\'<i\', -2) + v[1] + b\'\\0\'\n        else: out += struct.pack(\'<i\', v)\n    elif t == T_STR: out += (v + b\'\\0\') if inline else struct.pack(\'<i\', sidx[v])\n    elif t == T_BIN: out += struct.pack(\'<i\', len(v)) + v\n    else:\n        f, n = _fix[t]; out += struct.pack(f, *(v if isinstance(v, (list, tuple)) else [v]))\n\ndef _w_attr(out, t, v, sidx, inline):\n    out.append(t)\n    if t > ARR:\n        out += struct.pack(\'<i\', len(v)); bt = t - ARR\n        for x in v:\n            if bt == T_STR: out += x + b\'\\0\'\n            else: _w_val(out, bt, x, sidx, inline)\n    else: _w_val(out, t, v, sidx, inline)\n\ndef dumps(doc):\n    strs = []; sidx = {}\n    def add(s):\n        if s not in sidx: sidx[s] = len(strs); strs.append(s)\n    for el in doc.elements:\n        add(el[\'type\']); add(el[\'name\'])\n        for name, t, v in el[\'attrs\']:\n            add(name)\n            if t == T_STR: add(v)\n    out = bytearray(doc.header + b\'\\0\')\n    out += struct.pack(\'<i\', len(doc.prefix))\n    for attrs in doc.prefix:\n        out += struct.pack(\'<i\', len(attrs))\n        for name, t, v in attrs: out += name + b\'\\0\'; _w_attr(out, t, v, None, True)\n    out += struct.pack(\'<i\', len(strs))\n    for s in strs: out += s + b\'\\0\'\n    out += struct.pack(\'<i\', len(doc.elements))\n    for el in doc.elements: out += struct.pack(\'<ii\', sidx[el[\'type\']], sidx[el[\'name\']]) + el[\'guid\']\n    for el in doc.elements:\n        out += struct.pack(\'<i\', len(el[\'attrs\']))\n        for name, t, v in el[\'attrs\']: out += struct.pack(\'<i\', sidx[name]); _w_attr(out, t, v, sidx, False)\n    return bytes(out)\n\n# ---- helpers ----\ndef get(el, name, default=None):\n    for a in el[\'attrs\']:\n        if a[0] == name: return a[2]\n    return default\ndef geta(el, name):\n    for a in el[\'attrs\']:\n        if a[0] == name: return a\n    return None\ndef set_(el, name, t, v):\n    a = geta(el, name)\n    if a: a[1] = t; a[2] = v\n    else: el[\'attrs\'].append([name, t, v])\ndef delete(el, name): el[\'attrs\'] = [a for a in el[\'attrs\'] if a[0] != name]\n'

_MODULES['kv2'] = '"""Parse DMX keyvalues2 text (what Valve\'s importer writes for the main .vmap) into a dmx.Doc,\nso it can be edited and saved as binary 9 like Hammer does."""\nimport re, uuid\nimport dmx\n\nTYPES = {\'element\': 1, \'int\': 2, \'float\': 3, \'bool\': 4, \'string\': 5, \'binary\': 6, \'time\': 7, \'color\': 8, \'vector2\': 9,\n         \'vector3\': 10, \'vector4\': 11, \'qangle\': 12, \'quaternion\': 13, \'matrix\': 14, \'uint64\': 15, \'uint8\': 16}\n_tok = re.compile(r\'"((?:[^"\\\\]|\\\\.)*)"|([{}\\[\\],])|//[^\\n]*|(\\S+)\')\n\ndef _unesc(s): return s.replace(\'\\\\"\', \'"\').replace(\'\\\\n\', \'\\n\').replace(\'\\\\t\', \'\\t\').replace(\'\\\\\\\\\', \'\\\\\')\n\ndef _scalar(t, s):\n    if t == 2: return int(s)\n    if t == 3: return float(s)\n    if t == 4: return s.strip() not in (\'0\', \'false\', \'\')\n    if t == 5: return _unesc(s).encode(\'utf-8\')\n    if t == 6: return bytes.fromhex(\'\'.join(s.split()))\n    if t == 7: return int(round(float(s) * 10000))\n    if t == 8: return [int(x) for x in s.split()]\n    if t in (9, 10, 11, 12, 13, 14): return [float(x) for x in s.split()]\n    if t == 15: return int(s, 16) if s.lower().startswith(\'0x\') else int(s)\n    if t == 16: return int(s)\n    raise ValueError(t)\n\ndef loads(text):\n    if isinstance(text, bytes): text = text.decode(\'utf-8\')\n    nl = text.index(\'\\n\')\n    toks = [(m.group(1) is not None, m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3))) for m in _tok.finditer(text, nl + 1)\n            if m.group(1) is not None or m.group(2) or m.group(3)]\n    pos = [0]\n    def nxt(): t = toks[pos[0]]; pos[0] += 1; return t\n    def peek(): return toks[pos[0]] if pos[0] < len(toks) else (False, None)\n    elements = []; by_id = {}; refs = []   # refs: (element index, attr index, list index or None, guid)\n    def parse_element(etype):\n        q, b = nxt(); assert b == \'{\', b\n        idx = len(elements); el = {\'type\': etype.encode(), \'name\': b\'\', \'guid\': b\'\', \'attrs\': []}; elements.append(el)\n        while True:\n            q, name = nxt()\n            if not q and name == \'}\': break\n            q2, typ = nxt()\n            if typ == \'elementid\':\n                _, v = nxt(); g = uuid.UUID(v).bytes; el[\'guid\'] = g; by_id[g] = idx; continue\n            if typ.endswith(\'_array\'):\n                bt = TYPES[typ[:-6]]; nxt()  # [\n                vals = []\n                while True:\n                    qv, v = nxt()\n                    if not qv and v == \']\': break\n                    if not qv and v == \',\': continue\n                    if bt == 1:\n                        if v == \'element\':\n                            _, g = nxt()\n                            if g: refs.append((idx, len(el[\'attrs\']), len(vals), uuid.UUID(g).bytes)); vals.append(-1)\n                            else: vals.append(-1)\n                        else: vals.append(parse_element(v))\n                    else: vals.append(_scalar(bt, v))\n                el[\'attrs\'].append([name.encode(), bt + 32, vals]); continue\n            if typ in TYPES:\n                t = TYPES[typ]; _, v = nxt()\n                if t == 1:\n                    if v: refs.append((idx, len(el[\'attrs\']), None, uuid.UUID(v).bytes))\n                    el[\'attrs\'].append([name.encode(), 1, -1]); continue\n                if name == \'name\' and t == 5: el[\'name\'] = _unesc(v).encode(\'utf-8\'); continue\n                el[\'attrs\'].append([name.encode(), t, _scalar(t, v)]); continue\n            # inline element: "attr" "ElementType" { ... }\n            el[\'attrs\'].append([name.encode(), 1, parse_element(typ)])\n        return idx\n    tops = []\n    while pos[0] < len(toks):\n        q, etype = nxt(); tops.append(parse_element(etype))\n    for ei, ai, li, g in refs:\n        j = by_id.get(g, -1)\n        if li is None: elements[ei][\'attrs\'][ai][2] = j\n        else: elements[ei][\'attrs\'][ai][2][li] = j\n    # binary layout: root element first, $prefix_element$ second\n    root = next(i for i in tops if elements[i][\'type\'] == b\'CMapRootElement\')\n    pre = [i for i in tops if elements[i][\'type\'] == b\'$prefix_element$\']\n    order = [root] + pre + [i for i in range(len(elements)) if i != root and i not in pre]\n    remap = {o: n for n, o in enumerate(order)}\n    doc = dmx.Doc(); doc.header = b\'<!-- dmx encoding binary 9 format vmap 40 -->\\n\'\n    for o in order:\n        e = elements[o]; attrs = []\n        for n, t, v in e[\'attrs\']:\n            if t == 1: v = remap[v] if v >= 0 else -1\n            elif t == 33: v = [remap[x] if x >= 0 else -1 for x in v]\n            attrs.append([n, t, v])\n        doc.elements.append({**e, \'attrs\': attrs})\n    doc.prefix = [[list(a) for a in doc.elements[1][\'attrs\']]] if pre else []\n    return doc\n'

_MODULES['vmapedit'] = '"""Edit Hammer 5 .vmap files (binary DMX) without Hammer."""\nimport os, random, math, copy\nimport dmx\nfrom dmx import T_ELEM, T_INT, T_FLOAT, T_BOOL, T_STR, T_V3, T_QANG, T_U64, ARR\n\ndef guid(): return bytes(random.getrandbits(8) for _ in range(16))\n\nclass Map:\n    def __init__(s, path=None, data=None):\n        s.path = path\n        raw = data if data is not None else open(path, \'rb\').read()\n        if b\'encoding keyvalues2\' in raw[:80]:\n            import kv2; s.doc = kv2.loads(raw)\n        else:\n            s.doc = dmx.loads(raw)\n        s.E = s.doc.elements\n        s.root = next(i for i, e in enumerate(s.E) if e[\'type\'] == b\'CMapRootElement\')\n        s.world = dmx.get(s.E[s.root], b\'world\')\n\n    # ---------- basic element access ----------\n    def g(s, i, k, d=None): return dmx.get(s.E[i], k, d)\n    def type(s, i): return s.E[i][\'type\']\n    def children(s, i): return s.g(i, b\'children\') or []\n    def walk(s, i=None, parent=None):\n        """yield (node, parent) for every node under i (default world), depth first"""\n        i = s.world if i is None else i\n        for c in s.children(i):\n            yield c, i\n            yield from s.walk(c, i)\n    def entities(s):\n        return [n for n, _ in s.walk() if s.type(n) == b\'CMapEntity\']\n    def parent_of(s):\n        return {n: p for n, p in s.walk()}\n\n    # ---------- entity properties ----------\n    def props(s, i): return s.E[s.g(i, b\'entity_properties\')]\n    def P(s, i, k, d=None):\n        v = dmx.get(s.props(i), k.encode() if isinstance(k, str) else k)\n        return d if v is None else v.decode(\'utf-8\', \'replace\')\n    def setp(s, i, k, v):\n        k = k.encode() if isinstance(k, str) else k\n        v = v.encode() if isinstance(v, str) else v\n        dmx.set_(s.props(i), k, T_STR, v)\n    def delp(s, i, k): dmx.delete(s.props(i), k.encode() if isinstance(k, str) else k)\n    def cls(s, i): return s.P(i, \'classname\', \'\')\n    def name(s, i): return s.P(i, \'targetname\', \'\')\n    def origin(s, i): return list(s.g(i, b\'origin\', [0, 0, 0]))\n    def by_class(s, *c): return [e for e in s.entities() if s.cls(e) in c]\n\n    # ---------- connections ----------\n    def conns(s, i): return [s.E[c] for c in s.g(i, b\'connectionsData\') or []]\n    def conn_tuple(s, c):\n        return (dmx.get(c, b\'outputName\').decode(), dmx.get(c, b\'targetName\').decode(), dmx.get(c, b\'inputName\').decode(),\n                dmx.get(c, b\'overrideParam\').decode(), round(dmx.get(c, b\'delay\'), 3), dmx.get(c, b\'timesToFire\'))\n    def add_conn(s, i, out, target, inp, param=\'\', delay=0.0, times=-1):\n        c = s.new_el(b\'DmeConnectionData\', [\n            [b\'outputName\', T_STR, out.encode()], [b\'targetType\', T_INT, 7], [b\'targetName\', T_STR, target.encode()],\n            [b\'inputName\', T_STR, inp.encode()], [b\'overrideParam\', T_STR, param.encode()],\n            [b\'delay\', T_FLOAT, float(delay)], [b\'timesToFire\', T_INT, int(times)]])\n        dmx.geta(s.E[i], b\'connectionsData\')[2].append(c)\n        return c\n    def all_conns(s):\n        for e in s.entities():\n            for c in s.conns(e): yield e, c\n\n    # ---------- creation ----------\n    def new_el(s, typ, attrs, name=b\'\'):\n        s.E.append({\'type\': typ, \'name\': name, \'guid\': guid(), \'attrs\': attrs}); return len(s.E) - 1\n    def next_node_id(s):\n        m = 0\n        for e in s.E:\n            v = dmx.get(e, b\'nodeID\')\n            if isinstance(v, int) and v > m: m = v\n        return m + 1\n    def new_entity(s, classname, origin, props, angles=(0, 0, 0), parent=None):\n        pin = s.new_el(b\'DmElement\', [[b\'referenceName\', T_STR, b\'\'], [b\'targetReferenceID\', T_U64, 0], [b\'offsetOrigin\', T_V3, [0.0, 0.0, 0.0]],\n                                     [b\'offsetAngles\', T_QANG, [0.0, 0.0, 0.0]], [b\'pinAngles\', T_BOOL, True], [b\'twoWay\', T_BOOL, False]])\n        plug = s.new_el(b\'DmePlugList\', [[b\'names\', 37, []], [b\'dataTypes\', 34, []], [b\'plugTypes\', 34, []], [b\'descriptions\', 37, []]])\n        pr = [[b\'classname\', T_STR, classname.encode()]] + [[k.encode(), T_STR, str(v).encode()] for k, v in props.items()]\n        pe = s.new_el(b\'EditGameClassProps\', pr)\n        e = s.new_el(b\'CMapEntity\', [\n            [b\'nodeID\', T_INT, s.next_node_id()], [b\'referenceID\', T_U64, random.getrandbits(63)], [b\'children\', ARR + T_ELEM, []],\n            [b\'variableTargetKeys\', ARR + T_STR, []], [b\'variableNames\', ARR + T_STR, []], [b\'relayPlugData\', T_ELEM, plug],\n            [b\'connectionsData\', ARR + T_ELEM, []], [b\'entity_properties\', T_ELEM, pe], [b\'hitNormal\', T_V3, [0.0, 0.0, 1.0]],\n            [b\'isProceduralEntity\', T_BOOL, False], [b\'origin\', T_V3, [float(x) for x in origin]], [b\'angles\', T_QANG, [float(x) for x in angles]],\n            [b\'scales\', T_V3, [1.0, 1.0, 1.0]], [b\'transformLocked\', T_BOOL, False], [b\'transformPin\', T_ELEM, pin],\n            [b\'force_hidden\', T_BOOL, False], [b\'editorOnly\', T_BOOL, False], [b\'customVisGroup\', T_STR, b\'\'],\n            [b\'randomSeed\', T_INT, random.randint(1, 2**31 - 1)]])\n        dmx.geta(s.E[parent if parent is not None else s.world], b\'children\')[2].append(e)\n        return e\n    def remove_node(s, i):\n        for n, p in list(s.walk()):\n            if n == i:\n                a = dmx.geta(s.E[p], b\'children\'); a[2] = [x for x in a[2] if x != i]; return True\n        return False\n\n    # ---------- copying between maps ----------\n    def import_subtree(s, other, i, memo, node_id):\n        """deep-copy element i (and everything it references) from Map `other` into this map"""\n        if i in memo: return memo[i]\n        src = other.E[i]\n        if src[\'type\'] in (b\'CMapRootElement\', b\'CMapWorld\'):\n            raise ValueError(\'refusing to copy root/world\')\n        j = len(s.E); memo[i] = j\n        s.E.append({\'type\': src[\'type\'], \'name\': src[\'name\'], \'guid\': guid(), \'attrs\': []})\n        attrs = []\n        for name, t, v in src[\'attrs\']:\n            if t == T_ELEM and isinstance(v, int) and v >= 0: v = s.import_subtree(other, v, memo, node_id)\n            elif t == ARR + T_ELEM: v = [s.import_subtree(other, x, memo, node_id) if isinstance(x, int) and x >= 0 else x for x in v]\n            elif name == b\'nodeID\' and t == T_INT: v = node_id[0]; node_id[0] += 1\n            elif isinstance(v, list): v = list(v)\n            attrs.append([name, t, v])\n        s.E[j][\'attrs\'] = attrs\n        return j\n\n    # ---------- bounds ----------\n    def mesh_points(s, mesh_node):\n        """world-space vertex positions of a CMapMesh (mesh nodes keep local verts + node origin)"""\n        md = s.g(mesh_node, b\'meshData\')\n        if md is None: return []\n        vdata = s.g(md, b\'vertexData\')\n        o = s.g(mesh_node, b\'origin\', [0, 0, 0]); ang = s.g(mesh_node, b\'angles\', [0, 0, 0]); sc = s.g(mesh_node, b\'scales\', [1, 1, 1])\n        pts = []\n        for st in s.g(vdata, b\'streams\') or []:\n            if s.E[st][\'name\'].startswith(b\'position\'):\n                for p in s.g(st, b\'data\'):\n                    pts.append(xform([p[0] * sc[0], p[1] * sc[1], p[2] * sc[2]], o, ang))\n        return pts\n    def bounds(s, node):\n        pts = []\n        stack = [node]\n        while stack:\n            n = stack.pop()\n            if s.type(n) == b\'CMapMesh\': pts += s.mesh_points(n)\n            stack += s.children(n)\n        if not pts: return None\n        return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]\n\n    # ---------- save ----------\n    def prune(s):\n        """drop elements no longer reachable from the root / prefix; returns how many were removed"""\n        keep = set(); stack = [s.root]\n        stack += [i for i, e in enumerate(s.E) if e[\'type\'] == b\'$prefix_element$\']\n        while stack:\n            i = stack.pop()\n            if i in keep: continue\n            keep.add(i)\n            for _, t, v in s.E[i][\'attrs\']:\n                if t == T_ELEM and isinstance(v, int) and v >= 0: stack.append(v)\n                elif t == ARR + T_ELEM: stack += [x for x in v if isinstance(x, int) and x >= 0]\n        order = [i for i in range(len(s.E)) if i in keep]\n        remap = {o: n for n, o in enumerate(order)}\n        newE = []\n        for i in order:\n            e = s.E[i]; attrs = []\n            for name, t, v in e[\'attrs\']:\n                if t == T_ELEM and isinstance(v, int) and v >= 0: v = remap[v]\n                elif t == ARR + T_ELEM: v = [remap[x] if isinstance(x, int) and x >= 0 else x for x in v]\n                attrs.append([name, t, v])\n            newE.append({**e, \'attrs\': attrs})\n        removed = len(s.E) - len(newE)\n        s.doc.elements = s.E = newE; s.root = remap[s.root]; s.world = remap[s.world]\n        return removed\n    def dumps(s):\n        out = dmx.dumps(s.doc); dmx.loads(out); return out\n    def save(s, path):\n        out = s.dumps(); os.makedirs(os.path.dirname(path), exist_ok=True); open(path, \'wb\').write(out); return len(out)\n\n# ---------- math ----------\ndef rot_matrix(ang):\n    p, y, r = [math.radians(a) for a in ang]\n    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)\n    # Source QAngle (pitch, yaw, roll) -> forward/left/up columns\n    return [[cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy],\n            [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy],\n            [-sp, sr * cp, cr * cp]]\ndef xform(p, o, ang):\n    if not any(ang): return [p[0] + o[0], p[1] + o[1], p[2] + o[2]]\n    m = rot_matrix(ang)\n    return [m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + o[r] for r in range(3)]\ndef angle_fwd(ang):\n    m = rot_matrix(ang); return [m[0][0], m[1][0], m[2][0]]\n'

_MODULES['lighting'] = '"""Stationary (dynamic) light pass.\n\nCS:GO lights import as Static (directlight 1, baked only), so weapons / players render black. This picks a minimal set of lights\nper sealed compartment and switches them to directlight 3 (Stationary: dynamic direct light, baked shadows; max 4 may overlap), with ranges trimmed so no point in the map is\ninside more than MAX_OVERLAP stationary lights. Compartments come from voxelising the world geometry and flood-filling\nthe empty space; compartments that see sky (moondome / skybox) count as lit by light_environment when the map has one.\n"""\nimport collections, math\nimport numpy as np\nimport dmx\n\nTOOL_PASS = (\'materials/tools/toolstrigger\', \'materials/tools/toolsclip\', \'materials/tools/toolsplayerclip\', \'materials/tools/toolsnpcclip\',\n             \'materials/tools/toolsinvisible\', \'materials/tools/toolsinvisibleladder\', \'materials/tools/toolsareaportal\', \'materials/tools/toolsoccluder\',\n             \'materials/tools/toolshint\', \'materials/tools/toolsskip\', \'materials/tools/toolsfog\', \'materials/tools/toolslight\', \'materials/tools/toolsblocklight\',\n             \'materials/tools/toolsblock_los\', \'materials/tools/toolsbuyzone\', \'materials/tools/toolsbombsite\')\nMAX_OVERLAP = 4\nLIGHT_CLASSES = (\'light_omni2\', \'light_spot\', \'light_barn\', \'light_rect\', \'light_capsule\', \'light_omni\', \'light_ortho\')\n\ndef mesh_polys(m, node):\n    """[(material, [p0, p1, ...])] for a CMapMesh, world space"""\n    md = m.g(node, b\'meshData\')\n    if md is None: return []\n    E = m.E\n    get = lambda k: dmx.get(E[md], k)\n    vdi, evi, eni, fei, fdi = get(b\'vertexDataIndices\'), get(b\'edgeVertexIndices\'), get(b\'edgeNextIndices\'), get(b\'faceEdgeIndices\'), get(b\'faceDataIndices\')\n    mats = [x.decode().lower() for x in get(b\'materials\') or []]\n    pos = None; matidx = None\n    for st in dmx.get(E[get(b\'vertexData\')], b\'streams\'):\n        if E[st][\'name\'].startswith(b\'position\'): pos = dmx.get(E[st], b\'data\')\n    for st in dmx.get(E[get(b\'faceData\')], b\'streams\'):\n        if E[st][\'name\'].startswith(b\'materialindex\'): matidx = dmx.get(E[st], b\'data\')\n    if pos is None or not fei: return []\n    o = m.g(node, b\'origin\', [0, 0, 0])\n    out = []\n    for f, e0 in enumerate(fei):\n        loop = []; e = e0\n        for _ in range(64):\n            v = evi[e]; p = pos[vdi[v]]; loop.append((p[0] + o[0], p[1] + o[1], p[2] + o[2]))\n            e = eni[e]\n            if e == e0: break\n        mi = matidx[fdi[f]] if matidx else 0\n        out.append((mats[mi] if mi < len(mats) else \'\', loop))\n    return out\n\ndef world_polys(m):\n    """polygons of world geometry (meshes not inside entities)"""\n    polys = []\n    def walk(n):\n        for c in m.children(n):\n            t = m.type(c)\n            if t == b\'CMapMesh\': polys.extend(mesh_polys(m, c))\n            elif t == b\'CMapGroup\': walk(c)\n    walk(m.world)\n    return polys\n\ndef voxelise(polys, vs):\n    pts_all = np.array([p for _, l in polys for p in l], np.float64)\n    lo = pts_all.min(0) - 2 * vs; hi = pts_all.max(0) + 2 * vs\n    shape = tuple(int(x) for x in np.ceil((hi - lo) / vs))\n    solid = np.zeros(shape, np.uint8)   # 1 wall, 2 sky\n    for mat, loop in polys:\n        if mat.startswith(TOOL_PASS): continue\n        kind = 2 if (\'moondome\' in mat or \'toolsskybox\' in mat) else 1\n        P = np.array(loop, np.float64)\n        for i in range(1, len(P) - 1):\n            a, b, c = P[0], P[i], P[i + 1]\n            n = int(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / (vs * 0.4)) + 2\n            u, w = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))\n            k = (u + w) <= 1\n            q = a + np.outer(u[k], b - a) + np.outer(w[k], c - a)\n            idx = np.floor((q - lo) / vs).astype(int)\n            idx = np.clip(idx, 0, np.array(shape) - 1)\n            cur = solid[idx[:, 0], idx[:, 1], idx[:, 2]]\n            solid[idx[:, 0], idx[:, 1], idx[:, 2]] = np.maximum(cur, kind)\n    return solid, lo\n\ndef label_empty(solid):\n    from scipy import ndimage\n    lab, n = ndimage.label(solid == 0)\n    return lab, n\n\ndef run(m, c, vs=32.0):\n    lights = m.by_class(*LIGHT_CLASSES)\n    if not lights: return\n    polys = world_polys(m)\n    if not polys: return\n    solid, lo = voxelise(polys, vs)\n    lab, n = label_empty(solid)\n    shape = np.array(solid.shape)\n    exterior = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(), lab[:, :, 0].ravel(), lab[:, :, -1].ravel()])))\n    from scipy import ndimage\n    sky = ndimage.binary_dilation(solid == 2)\n    sky_labels = set(np.unique(lab[sky & (lab > 0)]))\n    has_env = bool(m.by_class(\'light_environment\'))\n    def vox(p): return tuple(np.clip(np.floor((np.array(p) - lo) / vs).astype(int), 0, shape - 1))\n    def region_of(p):\n        i = vox(p)\n        if lab[i]: return lab[i]\n        # light sits in a wall voxel: nearest empty voxel within 2 cells\n        best = None\n        for d in [(dx, dy, dz) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2)]:\n            j = tuple(np.clip(np.array(i) + d, 0, shape - 1))\n            if lab[j] and (best is None or sum(x * x for x in d) < best[0]): best = (sum(x * x for x in d), lab[j])\n        return best[1] if best else 0\n    by_region = collections.defaultdict(list)\n    for e in lights:\n        by_region[region_of(m.origin(e))].append(e)\n    # region voxel centres (subsampled) for coverage / overlap checks\n    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(n + 1))\n    centres = {}\n    for r in by_region:\n        if r == 0 or r in exterior: continue\n        idx = np.argwhere(lab == r)\n        if len(idx) > 4000: idx = idx[np.random.RandomState(r).choice(len(idx), 4000, replace=False)]\n        centres[r] = lo + (idx + 0.5) * vs\n    chosen = []            # (entity, pos, range)\n    all_samples = np.concatenate(list(centres.values())) if centres else np.zeros((0, 3))\n    overlap = np.zeros(len(all_samples), np.int32)\n    offs = {}; k = 0\n    for r, pts in centres.items(): offs[r] = (k, k + len(pts)); k += len(pts)\n    already = [e for e in lights if m.P(e, \'directlight\') in (\'2\', \'3\')]\n    for e in already:\n        p = np.array(m.origin(e)); rg = float(m.P(e, \'range\', \'512\') or 512)\n        overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n    # rooms players can reach (spawns / teleport targets) with no light at all get a new stationary light first\n    play = collections.Counter(region_of(m.origin(e)) for e in m.by_class(\'info_player_terrorist\', \'info_player_counterterrorist\', \'info_teleport_destination\'))\n    unlit = [r for r in play if r and r not in exterior and r not in by_region and sizes[r] >= 8]\n    tmpl = next(iter(m.by_class(\'light_omni2\')), None)\n    added = 0\n    for r in unlit:\n        idx = np.argwhere(lab == r)\n        want = np.array([idx[:, 0].mean(), idx[:, 1].mean(), idx[:, 2].max() - 1])\n        v = idx[np.argmin(((idx - want) ** 2).sum(1))]\n        p = lo + (v + 0.5) * vs\n        rg = float(np.linalg.norm((idx - v) * vs, axis=1).max()) + 64\n        while rg > 128 and (overlap[np.linalg.norm(all_samples - p, axis=1) < rg] >= MAX_OVERLAP).any(): rg *= 0.85\n        props = {a[0].decode(): a[2].decode() for a in m.props(tmpl)[\'attrs\'] if a[0] not in (b\'classname\', b\'targetname\')} if tmpl else {}\n        props.update({\'directlight\': \'3\', \'range\': \'%.0f\' % rg, \'enabled\': \'1\', \'color\': \'255 255 255\'})\n        m.new_entity(\'light_omni2\', p, props); added += 1\n        overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n    if added: c.log.append(\'added %d stationary light_omni2 to sealed rooms with spawns / teleport destinations that had no light at all\' % added)\n    report = {}; skipped_sky = 0\n    MIN_VOX = 64                                          # smaller pockets (lamp housings, vents) don\'t count as rooms\n    regions = [r for r in by_region if r and r not in exterior and r in centres and sizes[r] >= MIN_VOX]\n    if has_env: skipped_sky = sum(1 for r in regions if r in sky_labels); regions = [r for r in regions if r not in sky_labels]\n    regions = [r for r in regions if not any(e in already for e in by_region[r])]\n    covered = {r: np.zeros(len(centres[r]), bool) for r in regions}\n    def fit(p, rg):\n        while rg >= 128:\n            if not (overlap[np.linalg.norm(all_samples - p, axis=1) < rg] >= MAX_OVERLAP).any(): return rg\n            rg *= 0.85\n        return None\n    def take(r, e, p, rg):\n        nonlocal overlap\n        chosen.append((e, p, rg)); overlap += (np.linalg.norm(all_samples - p, axis=1) < rg)\n        covered[r] |= np.linalg.norm(centres[r] - p, axis=1) < rg\n    # pass 1: every room gets one light, smallest rooms first so the big ones can\'t crowd them out\n    for r in sorted(regions, key=lambda r: sizes[r]):\n        pts = centres[r]; best = None\n        for e in by_region[r]:\n            p = np.array(m.origin(e)); rg0 = float(m.P(e, \'range\', \'512\') or 512)\n            d = np.linalg.norm(pts - p, axis=1)\n            rg = fit(p, max(min(rg0, float(np.percentile(d, 95)) + 64), 192.0))\n            if rg is None: continue\n            gain = int((d < rg).sum())\n            if best is None or gain > best[0]: best = (gain, e, p, rg)\n        if best: take(r, best[1], best[2], best[3])\n        report[r] = 1 if best else 0\n    # pass 2: top up rooms that are still mostly uncovered\n    for r in sorted(regions, key=lambda r: covered[r].mean()):\n        pts = centres[r]\n        while report[r] and report[r] < 6 and covered[r].mean() < 0.85:\n            best = None\n            for e in by_region[r]:\n                if any(e is x[0] for x in chosen): continue\n                p = np.array(m.origin(e)); rg0 = float(m.P(e, \'range\', \'512\') or 512)\n                d = np.linalg.norm(pts - p, axis=1); left = d[~covered[r]]\n                if not len(left): continue\n                rg = fit(p, max(min(rg0, float(np.percentile(left, 90)) + 64), 192.0))\n                if rg is None: continue\n                gain = int(((d < rg) & ~covered[r]).sum())\n                if best is None or gain > best[0]: best = (gain, e, p, rg)\n            if best is None or best[0] < 0.08 * len(pts): break\n            take(r, best[1], best[2], best[3]); report[r] += 1\n    report = [(r, len(by_region[r]), report[r], round(float(covered[r].mean()), 2)) for r in regions]\n    for e, p, rg in chosen:\n        m.setp(e, \'directlight\', \'3\')\n        old = float(m.P(e, \'range\', \'0\') or 0)\n        if rg < old - 1: m.setp(e, \'range\', \'%.0f\' % rg)\n    none = [x for x in report if x[2] == 0]\n    if none: c.todo.append(\'%d compartments could not get a stationary light without going over %d overlapping - check them\' % (len(none), MAX_OVERLAP))\n    c.log.append(\'stationary lights: %d of %d lights switched Static -> Stationary (directlight 3) across %d sealed compartments, ranges trimmed so no spot is inside more than %d%s\'\n                 % (len(chosen), len(lights), len(report), MAX_OVERLAP, \'; %d compartments open to sky left to light_environment\' % skipped_sky if skipped_sky else \'\'))\n    weak = [x for x in report if x[3] < 0.6]\n    if weak: c.todo.append(\'%d compartments only partly covered by stationary light (overlap cap) - check weapons are lit there\' % len(weak))\n    return report\n'

_MODULES['visfit'] = '"""Vis compile time: hint boxes fitted to the play space, and sky shells out of vis where nothing needs them.\n\nThe CS2 vis compile (VIS3) spends most of its time clustering, sampling line of sight and merging clusters down to the 4,096 cap.\nHow many clusters it starts with depends on the visibility_hint that governs each part of the map (where hints overlap, the\nhighest resolution wins). So hints should only be fine where players can actually stand.\n\n1. Playable air: empty space at VS-unit voxels (world meshes that block players, incl. clips, detail and sky shells) connected\n   to a spawn or teleport destination, plus sealed compartments that hold gameplay entities (buttons, doors, triggers, trains,\n   weapons). The open void around the map never counts.\n2. Walkable band: playable air up to BAND units above something to stand on (world or entity meshes).\n3. Hints, on a 256-unit grid so they line up with the Lowest regions, Low and Lower never overlapping:\n     Low    (hintType 3, 64-unit grid)                         cells holding walkable band\n     Lower  (hintType 8, 64-unit grid, fewer initial clusters)  other playable air next to geometry (high ceilings, shafts)\n     Lowest (hintType 9, 256-unit grid)                         one box over the whole map: void, sealed-off rooms, open sky\n   Open sky far above an outdoor area gets no box of its own, so it falls to Lowest. all_lower=True makes the Low cells Lower too\n   (for maps whose vis merge stalls, e.g. mg_egypt_course). Boxes are merged while at least 75% (then 60%, 50%) of a box is its\n   own cells, aiming for at most 100 boxes.\n4. Sky shells (all-moondome / all-toolsskybox meshes) get Exclude from Vis unless they border playable air that is open once\n   every sky shell is gone. That test uses only vis-relevant geometry: Valve\'s docs say toolsnodraw is ignored by vis and by\n   its inside/outside test, and clips, triggers and meshes already excluded from vis don\'t count either.\n5. Optional extra_visex_box (world box): every world mesh entirely inside it is excluded from vis too (an unreachable\n   ex-3D-skybox, for example).\nExisting visibility_hint entities are replaced.\n"""\nimport collections\nimport numpy as np\nfrom scipy import ndimage\nimport dmx\nfrom lighting import mesh_polys\n\nPLAY = (\'info_player_counterterrorist\', \'info_player_terrorist\', \'info_player_start\', \'info_teleport_destination\')\nACTIVE = (\'func_button\', \'func_rot_button\', \'func_door\', \'func_door_rotating\', \'func_movelinear\', \'trigger_multiple\', \'trigger_once\',\n          \'trigger_teleport\', \'game_player_equip\', \'point_teleport\', \'func_tracktrain\', \'func_rotating\', \'func_physbox\')\nPHYS_PASS = (\'materials/tools/toolstrigger\', \'materials/tools/toolshint\', \'materials/tools/toolsskip\', \'materials/tools/toolsfog\',\n             \'materials/tools/toolslight\', \'materials/tools/toolsblocklight\', \'materials/tools/toolsblock_los\', \'materials/tools/toolsbuyzone\',\n             \'materials/tools/toolsbombsite\', \'materials/tools/toolsareaportal\', \'materials/tools/toolsoccluder\', \'materials/tools/toolsvisblocker\')\nCELL = 256.0\nBAND = 160.0\nCODE = {\'Low\': \'3\', \'Lower\': \'8\', \'Lowest\': \'9\'}\n\ndef is_shell(mats):\n    return bool(mats) and all(\'moondome\' in x or x == \'materials/tools/toolsskybox.vmat\' for x in mats)\n\ndef vis_relevant(mat):\n    if \'moondome\' in mat: return True\n    if mat.startswith(\'materials/tools/\'):\n        return mat.startswith((\'materials/tools/toolsskybox\', \'materials/tools/toolssolidblocklight\', \'materials/tools/toolsvisblocker\'))\n    return True\n\ndef meshes(m):\n    par = {n: p for n, p in m.walk()}\n    def in_ent(n):\n        p = par.get(n)\n        while p is not None:\n            if m.type(p) == b\'CMapEntity\': return p\n            p = par.get(p)\n        return None\n    out = []\n    for n, _ in m.walk():\n        if m.type(n) != b\'CMapMesh\': continue\n        pl = mesh_polys(m, n); mats = {x for x, _ in pl}\n        a = dmx.geta(m.E[n], b\'visexclude\')\n        out.append(dict(n=n, ent=in_ent(n), polys=pl, mats=mats, shell=is_shell(mats), visex=bool(a and a[2])))\n    return out\n\ndef raster(polys, lo, shape, vs, grid):\n    for L in polys:\n        L = np.asarray(L, np.float64)\n        if (np.floor((L.max(0) - lo) / vs) < 0).any() or (np.floor((L.min(0) - lo) / vs) >= shape).any(): continue\n        for i in range(1, len(L) - 1):\n            a, b, c = L[0], L[i], L[i + 1]\n            k = int(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / (vs * 0.4)) + 2\n            u, w = np.meshgrid(np.linspace(0, 1, k), np.linspace(0, 1, k)); s = (u + w) <= 1\n            q = a + np.outer(u[s], b - a) + np.outer(w[s], c - a)\n            idx = np.floor((q - lo) / vs).astype(int)\n            idx = idx[((idx >= 0) & (idx < shape)).all(1)]\n            grid[idx[:, 0], idx[:, 1], idx[:, 2]] = True\n\ndef _border(lab):\n    return set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),\n                                         lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))) - {0}\n\ndef boxes_from_cells(cells):\n    left = set(cells); out = []\n    while left:\n        i, j, k = min(left)\n        x1 = i\n        while (x1 + 1, j, k) in left: x1 += 1\n        y1 = j\n        while all((x, y1 + 1, k) in left for x in range(i, x1 + 1)): y1 += 1\n        z1 = k\n        while all((x, y, z1 + 1) in left for x in range(i, x1 + 1) for y in range(j, y1 + 1)): z1 += 1\n        for x in range(i, x1 + 1):\n            for y in range(j, y1 + 1):\n                for z in range(k, z1 + 1): left.discard((x, y, z))\n        out.append(((i, j, k), (x1 + 1, y1 + 1, z1 + 1)))\n    return out\n\ndef merge_boxes(boxes, own, other, fill=0.75):\n    """join boxes whose union is mostly own cells and holds none of the other hint type\'s cells (boxes stay disjoint)"""\n    boxes = [(tuple(a), tuple(b)) for a, b in boxes]\n    if len(boxes) < 2: return boxes\n    allc = np.array(list(own | other))\n    o = allc.min(0); sz = allc.max(0) - o + 1\n    def cum(cs):\n        g = np.zeros(sz + 1, np.int32)\n        for c in cs: g[tuple(np.array(c) - o + 1)] = 1\n        return g.cumsum(0).cumsum(1).cumsum(2)\n    O, T = cum(own), cum(other)\n    def cnt(S, a, b):\n        x0, y0, z0 = np.array(a) - o; x1, y1, z1 = np.array(b) - o\n        return int(S[x1, y1, z1] - S[x0, y1, z1] - S[x1, y0, z1] - S[x1, y1, z0] + S[x0, y0, z1] + S[x0, y1, z0] + S[x1, y0, z0] - S[x0, y0, z0])\n    while len(boxes) > 1:\n        A = np.array([bx[0] for bx in boxes]); B = np.array([bx[1] for bx in boxes])\n        best = None\n        for i in range(len(boxes)):\n            ua = np.minimum(A[i], A[i + 1:]); ub = np.maximum(B[i], B[i + 1:])\n            for a, b in zip(ua, ub):\n                v = int(np.prod(b - a))\n                if v > 4096: continue\n                n = cnt(O, a, b)\n                if n / v < fill or (best and n / v <= best[0]) or cnt(T, a, b): continue\n                ins = np.all((A >= a) & (B <= b), 1); ovl = np.all((A < b) & (B > a), 1)\n                if (ovl & ~ins).any(): continue\n                best = (n / v, tuple(a), tuple(b), ins)\n        if not best: break\n        _, a, b, ins = best\n        boxes = [bx for k, bx in enumerate(boxes) if not ins[k]] + [(a, b)]\n    return boxes\n\ndef plan(m, all_lower=False, extra_visex_box=None, vs=None, margin=256.0):\n    ms = meshes(m)\n    world = [x for x in ms if x[\'ent\'] is None]\n    allp = np.array([p for x in world for _, l in x[\'polys\'] for p in l])\n    lo = allp.min(0) - margin; hi = allp.max(0) + margin\n    if vs is None:     # keep the grids within the cloud workspace\'s memory\n        vs = next(v for v in (16.0, 24.0, 32.0, 48.0) if np.prod(np.ceil((hi - lo) / v)) < 1.2e8)\n    shape = np.array([int(v) for v in np.ceil((hi - lo) / vs)])\n    phys = np.zeros(shape, bool); visg = np.zeros(shape, bool); ent = np.zeros(shape, bool); sh = np.zeros(shape, np.int32)\n    raster([l for x in world for mt, l in x[\'polys\'] if not mt.startswith(PHYS_PASS)], lo, shape, vs, phys)\n    raster([l for x in world if not x[\'visex\'] and not x[\'shell\'] for mt, l in x[\'polys\'] if vis_relevant(mt)], lo, shape, vs, visg)\n    raster([l for x in ms if x[\'ent\'] is not None for mt, l in x[\'polys\'] if not mt.startswith(PHYS_PASS)], lo, shape, vs, ent)\n    shells = [x for x in world if x[\'shell\']]\n    for si, x in enumerate(shells, 1):\n        g = np.zeros(shape, bool); raster([l for _, l in x[\'polys\']], lo, shape, vs, g); sh[g] = si\n    lab, _ = ndimage.label(~(phys | (sh > 0)))\n    def comp_at(p, r=3):\n        i = np.floor((np.array(p, float) - lo) / vs).astype(int)\n        for rr in range(r + 1):\n            v = lab[tuple(slice(max(0, i[k] - rr), i[k] + rr + 1) for k in range(3))]; v = v[v > 0]\n            if v.size: return int(np.bincount(v).argmax())\n        return 0\n    seeds = {comp_at(np.array(m.origin(e), float) + [0, 0, 36]) for e in m.by_class(*PLAY)} - {0}\n    act = {comp_at(m.origin(e), 2) for e in m.entities() if m.cls(e) in ACTIVE or m.cls(e).startswith(\'weapon_\')} - {0}\n    border = _border(lab)\n    comps = (seeds | act) - border\n    P = np.isin(lab, list(comps))\n    del lab\n    solid = phys | (sh > 0) | ent\n    band = np.zeros(shape, bool)\n    for k in range(1, int(BAND / vs) + 1):\n        band[:, :, k:] |= solid[:, :, :-k]\n    band &= P\n    del solid\n    def cells_of(mask):\n        idx = np.argwhere(mask)\n        return set(map(tuple, np.floor((lo + (idx + 0.5) * vs) / CELL).astype(int)))\n    pc, bc = cells_of(P), cells_of(band)\n    geo = cells_of((phys & (sh == 0)) | visg | ent)\n    low = set(bc); lower = (pc - low) & geo\n    if all_lower: lower |= low; low = set()\n    for fill in (0.75, 0.6, 0.5):\n        hints = [(\'Low\', a, b) for a, b in merge_boxes(boxes_from_cells(low), low, lower, fill)] + \\\n                [(\'Lower\', a, b) for a, b in merge_boxes(boxes_from_cells(lower), lower, low, fill)]\n        if len(hints) <= 100: break\n    hints = [(t, np.array(a) * CELL, np.array(b) * CELL) for t, a, b in hints]\n    hints.append((\'Lowest\', np.floor(allp.min(0) / 256) * 256 - 256, np.ceil(allp.max(0) / 256) * 256 + 256))\n    # sky shells: keep the ones bordering playable air that is open once all sky shells are gone\n    vl, _ = ndimage.label(~visg)\n    leaky = P & np.isin(vl, list(_border(vl)))\n    del vl\n    near_leak = ndimage.binary_dilation(leaky, iterations=2)\n    keep_ids = set(np.unique(sh[near_leak & (sh > 0)])) - {0}\n    exclude = [x for si, x in enumerate(shells, 1) if si not in keep_ids and not x[\'visex\']]\n    extra = []\n    if extra_visex_box is not None:\n        a, b = np.array(extra_visex_box[0], float), np.array(extra_visex_box[1], float)\n        for x in world:\n            if x[\'visex\'] or x[\'shell\']: continue\n            bb = m.bounds(x[\'n\'])\n            if bb and (np.array(bb[0]) >= a).all() and (np.array(bb[1]) <= b).all(): extra.append(x)\n    stats = dict(vs=vs, play=P.sum() * vs ** 3 / 1e9, band=band.sum() * vs ** 3 / 1e9, shells=len(shells), kept=len(shells) - len(exclude),\n                 hints=dict(collections.Counter(h[0] for h in hints)))\n    return dict(hints=hints, exclude=exclude, extra=extra, stats=stats)\n\ndef apply(m, pl):\n    for e in m.by_class(\'visibility_hint\'): m.remove_node(e)\n    for t, mn, mx in pl[\'hints\']:\n        o = (mn + mx) / 2\n        m.new_entity(\'visibility_hint\', o, {\'box_mins\': \' \'.join(\'%g\' % v for v in mn - o), \'box_maxs\': \' \'.join(\'%g\' % v for v in mx - o),\n                                            \'hintType\': CODE[t]})\n    for x in pl[\'exclude\'] + pl[\'extra\']:\n        a = dmx.geta(m.E[x[\'n\']], b\'visexclude\')\n        if a: a[2] = True\n        else: dmx.set_(m.E[x[\'n\']], b\'visexclude\', dmx.T_BOOL, True)\n\ndef run(m, c, all_lower=False, extra_visex_box=None):\n    old = collections.Counter({\'3\': \'Low\', \'8\': \'Lower\', \'9\': \'Lowest\', \'2\': \'Medium\', \'0\': \'High\', \'1\': \'Normal\', \'10\': \'Reduced\'}\n                              .get(m.P(e, \'hintType\', \'\'), \'?\') for e in m.by_class(\'visibility_hint\'))\n    pl = plan(m, all_lower, extra_visex_box)\n    apply(m, pl)\n    s = pl[\'stats\']; h = s[\'hints\']\n    c.log.append(\'visfit (%g-unit voxels): %.1f billion cubic units of playable air, %.1f of it walkable band; hints %d Low, %d Lower, %d Lowest%s\'\n                 % (s[\'vs\'], s[\'play\'], s[\'band\'], h.get(\'Low\', 0), h.get(\'Lower\', 0), h.get(\'Lowest\', 0),\n                    (\' (replacing \' + \', \'.join(\'%d %s\' % (v, k) for k, v in old.items()) + \')\') if old else \'\'))\n    if s[\'shells\']:\n        c.log.append(\'visfit: %d of %d sky shell meshes set to Exclude from Vis, %d kept because they close playable air\'\n                     % (len(pl[\'exclude\']), s[\'shells\'], s[\'kept\']))\n    if pl[\'extra\']: c.log.append(\'visfit: %d meshes inside %s set to Exclude from Vis\' % (len(pl[\'extra\']), extra_visex_box))\n    c.todo.append(\'vis: if anything pops in or out of view where players stand, change the nearest Lower hint to Low (or Low to Medium)\')\n    return pl\n'

_MODULES['probes'] = '"""Light probe pass: env_combined_light_probe_volume fitted to each playable compartment.\n\nPlayers, weapons and props get their indirect light from light probe volumes; without them they render dark.\nUnlike daylight.py (one slab per 2048 units along the map\'s longest axis), this fits volumes to the actual\nempty space of each compartment a player can reach, split into tiles of at most TILE units so no single\nvolume covers a huge empty bounding box.\n\nPlayable compartments: empty-space regions (world geometry voxelised and flood-filled, as in lighting.py)\nholding a spawn or teleport destination. The open void outside the map never counts.\nExisting probe volumes are left alone unless replace=True.\n"""\nimport numpy as np\nfrom lighting import world_polys, voxelise, label_empty\n\nSEEDS = (\'info_player_counterterrorist\', \'info_player_terrorist\', \'info_player_start\', \'info_teleport_destination\')\nPROBE_CLASSES = (\'env_combined_light_probe_volume\', \'env_light_probe_volume\')\nTILE = 2048.0      # max volume edge length\nPAD = 48.0         # extra space around the compartment so probes cover the walls\nMIN_VOX = 8        # ignore tiny pockets\n\nPROPS = dict(StartDisabled=0, cubemaptexture=\'\', bakenearz=\'2.0\', bakefarz=\'4096.0\', lightgroup=\'\', moveable=0,\n             lightprobetexture=\'\', voxel_size=\'48.0\', flood_fill=1, voxelize=1, light_probe_volume_from_cubemap=0,\n             storage=-1, indoor_outdoor_level=0, edge_fade_dists=\'0 32 0\', clientSideEntity=1)\n\n\ndef run(m, c, vs=32.0, replace=False):\n    existing = m.by_class(*PROBE_CLASSES)\n    if existing and not replace:\n        c.log.append(\'probes: map already has %d light probe volumes - left alone (run the probes_replace pass to refit them)\' % len(existing))\n        return\n    polys = world_polys(m)\n    if not polys:\n        c.todo.append(\'probes: no world geometry found - no probe volumes added\')\n        return\n    solid, lo = voxelise(polys, vs)\n    lab, _ = label_empty(solid)\n    shape = np.array(lab.shape)\n    border = set(np.unique(np.concatenate([lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),\n                                           lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))) - {0}\n\n    def region_at(p, r=2):\n        i = np.clip(np.floor((np.asarray(p, float) - lo) / vs).astype(int), 0, shape - 1)\n        for rr in range(r + 1):\n            v = lab[tuple(slice(max(0, i[k] - rr), i[k] + rr + 1) for k in range(3))]\n            v = v[v > 0]\n            if v.size: return int(np.bincount(v).argmax())\n        return 0\n\n    regions, leaked = set(), 0\n    for e in m.by_class(*SEEDS):\n        r = region_at(np.array(m.origin(e), float) + [0, 0, 36])\n        if r in border: leaked += 1\n        elif r: regions.add(r)\n    if leaked:\n        c.todo.append(\'probes: %d spawns / teleport destinations sit in space connected to the outside of the map (a leak?) - no probes there\' % leaked)\n    if not regions:\n        c.todo.append(\'probes: no sealed playable compartments found - no probe volumes added\')\n        return\n\n    for e in existing: m.remove_node(e)\n    n_vol = 0\n    for r in sorted(regions):\n        idx = np.argwhere(lab == r)\n        if len(idx) < MIN_VOX: continue\n        P = lo + (idx + 0.5) * vs\n        tile = np.floor((P - P.min(0)) / TILE).astype(int)\n        for t in np.unique(tile, axis=0):\n            sel = P[(tile == t).all(1)]\n            mn = sel.min(0) - vs / 2 - PAD\n            mx = sel.max(0) + vs / 2 + PAD\n            o = (mn + mx) / 2\n            props = dict(PROPS, box_mins=\'%f %f %f\' % tuple(mn - o), box_maxs=\'%f %f %f\' % tuple(mx - o))\n            m.new_entity(\'env_combined_light_probe_volume\', o, props)\n            n_vol += 1\n    c.log.append(\'probes: %s%d env_combined_light_probe_volume fitted to %d playable compartments (tiles up to %g units)\'\n                 % (\'replaced %d existing volumes with \' % len(existing) if existing else \'\', n_vol, len(regions), TILE))\n    c.todo.append(\'probes: after a full bake, check players / weapons are lit evenly; if a room looks off, check its probe volume covers it\')\n'
_MODULES['splitfaces'] = '"""Split very large lit faces of a .vmap into tiles (development copy; pasted into rebuild_lighting.py).\n\nWhy: in CS2 a face is one lightmap chart, and a mesh (CMapMesh) gets one probe / cubemap for the whole mesh. A huge floor\ntherefore bakes blotchy and reflects the wrong place.  This cuts faces over `limit` square units on a world-aligned grid\n(`cell` units) and, optionally, gives every grid cell its own copy of the mesh.\n\nAll geometry is rebuilt from polygons: vertices are welded by position, every undirected edge gets two half-edges (the twin of\na boundary edge has face -1), and the corner data (texcoord / normal / tangent / vertex paint) is interpolated along cut edges.\nMeshes with subdivision data (importer displacements) and non-convex faces are left alone.\n"""\nimport numpy as np\n\nEPS = 0.01          # a point this close to a cut plane counts as on it\nWELD = 3            # vertex welding, decimals\n\n\n# ----------------------------------------------------------------------------------------------------------- reading\ndef _streams(m, node, part):\n    """-> {stream name: (element index, data list)} for meshData.<part>"""\n    md = m.g(node, b\'meshData\')\n    pd = m.g(md, part)\n    return {m.E[st][\'name\'].decode(): (st, m.g(st, b\'data\')) for st in (m.g(pd, b\'streams\') or [])}\n\n\ndef mesh_has_subdivision(m, node):\n    sd = m.g(m.g(node, b\'meshData\'), b\'subdivisionData\')\n    return bool(sd is not None and m.g(sd, b\'streams\'))\n\n\ndef mesh_read(m, node):\n    """-> (polys, materials).  poly = {\'attrs\': {faceData stream: value}, \'corners\': [{\'pos\': ndarray(3), \'fvd\': {stream: ndarray}}]}"""\n    md = m.g(node, b\'meshData\')\n    dest, nxt = m.g(md, b\'edgeVertexIndices\'), m.g(md, b\'edgeNextIndices\')\n    fe, fd = m.g(md, b\'faceEdgeIndices\'), m.g(md, b\'faceDataIndices\')\n    evd, vdi = m.g(md, b\'edgeVertexDataIndices\'), m.g(md, b\'vertexDataIndices\')\n    pos = _streams(m, node, b\'vertexData\')[\'position:0\'][1]\n    fvd = {k: v[1] for k, v in _streams(m, node, b\'faceVertexData\').items()}\n    fdat = {k: v[1] for k, v in _streams(m, node, b\'faceData\').items()}\n    polys = []\n    for f, h0 in enumerate(fe):\n        corners, h = [], h0\n        for _ in range(len(dest) + 1):\n            corners.append({\'pos\': np.array(pos[vdi[dest[h]]], float),\n                            \'fvd\': {k: np.array(v[evd[h]], float) for k, v in fvd.items()}})\n            h = nxt[h]\n            if h == h0:\n                break\n        polys.append({\'attrs\': {k: v[fd[f]] for k, v in fdat.items()}, \'corners\': corners})\n    return polys, [x.decode(\'utf-8\', \'replace\') if isinstance(x, (bytes, bytearray)) else x for x in m.g(md, b\'materials\')]\n\n\n# ------------------------------------------------------------------------------------------------------------ geometry\ndef poly_area(corners):\n    P = np.array([c[\'pos\'] for c in corners])\n    n = np.zeros(3)\n    for i in range(1, len(P) - 1):\n        n += np.cross(P[i] - P[0], P[i + 1] - P[0])\n    return float(np.linalg.norm(n) / 2)\n\n\ndef is_convex(corners, tol=1e-6):\n    P = np.array([c[\'pos\'] for c in corners])\n    n = np.zeros(3)\n    for i in range(1, len(P) - 1):\n        n += np.cross(P[i] - P[0], P[i + 1] - P[0])\n    ln = np.linalg.norm(n)\n    if ln < 1e-9:\n        return False\n    n /= ln\n    for i in range(len(P)):\n        c = np.dot(np.cross(P[(i + 1) % len(P)] - P[i], P[(i + 2) % len(P)] - P[(i + 1) % len(P)]), n)\n        if c < -tol * max(1.0, ln):\n            return False\n    return True\n\n\ndef _lerp(a, b, t):\n    out = {\'pos\': a[\'pos\'] + (b[\'pos\'] - a[\'pos\']) * t, \'fvd\': {}}\n    for k in a[\'fvd\']:\n        v = a[\'fvd\'][k] + (b[\'fvd\'][k] - a[\'fvd\'][k]) * t\n        if k.startswith(\'normal\'):\n            ln = np.linalg.norm(v)\n            if ln > 1e-9:\n                v = v / ln\n        out[\'fvd\'][k] = v\n    return out\n\n\ndef _edge_key(p, q):\n    a, b = tuple(np.round(p, WELD)), tuple(np.round(q, WELD))\n    return (a, b) if a <= b else (b, a)\n\n\ndef clip_poly(corners, axis, c):\n    """Cut a convex polygon with the plane  pos[axis] = c  -> (below, above) corner lists (either may be empty)."""\n    lo, hi = [], []\n    n = len(corners)\n    for i in range(n):\n        a, b = corners[i], corners[(i + 1) % n]\n        sa, sb = a[\'pos\'][axis] - c, b[\'pos\'][axis] - c\n        sa0, sb0 = abs(sa) < EPS, abs(sb) < EPS\n        if sa0:\n            lo.append(a)\n            hi.append(a)\n        elif sa < 0:\n            lo.append(a)\n        else:\n            hi.append(a)\n        if not sa0 and not sb0 and (sa < 0) != (sb < 0):\n            if tuple(np.round(a[\'pos\'], WELD)) <= tuple(np.round(b[\'pos\'], WELD)):\n                p = _lerp(a, b, sa / (sa - sb))\n            else:                                   # always interpolate from the same end, so both faces sharing the edge agree\n                p = _lerp(b, a, sb / (sb - sa))\n            p[\'pos\'][axis] = c                      # exactly on the plane, so both sides weld\n            lo.append(p)\n            hi.append(p)\n    return lo, hi\n\n\ndef _usable(corners):\n    return len(corners) >= 3 and poly_area(corners) > 1e-4\n\n\n# ----------------------------------------------------------------------------------------------------------- cutting\ndef cut_polys(polys, limit, cell, candidate, min_piece=None):\n    """Cut every candidate poly with area >= limit on the grid.  -> (new polys, cut count, skipped [(reason, area)])"""\n    min_piece = cell / 64.0 if min_piece is None else min_piece\n    plan, skipped = {}, []\n    for i, p in enumerate(polys):\n        if not candidate(p):\n            continue\n        area = poly_area(p[\'corners\'])\n        if area <= limit * 1.0001:                  # a tile of exactly cell x cell is fine\n            continue\n        if not is_convex(p[\'corners\']):\n            skipped.append((\'not convex\', area))\n            continue\n        P = np.array([c[\'pos\'] for c in p[\'corners\']])\n        planes = []\n        for axis in range(3):\n            lo, hi = P[:, axis].min(), P[:, axis].max()\n            if hi - lo <= cell:\n                continue\n            k = int(np.ceil((lo + EPS) / cell))\n            while k * cell < hi - EPS:\n                c = k * cell\n                if c - lo >= min_piece and hi - c >= min_piece:\n                    planes.append((axis, float(c)))\n                k += 1\n        if planes:\n            plan[i] = planes\n    # points where the planes cross the edges of cut faces: the neighbours across those edges need the same points\n    edge_pts = {}\n    for i, planes in plan.items():\n        cs = polys[i][\'corners\']\n        for j in range(len(cs)):\n            a, b = cs[j], cs[(j + 1) % len(cs)]\n            for axis, c in planes:\n                sa, sb = a[\'pos\'][axis] - c, b[\'pos\'][axis] - c\n                if abs(sa) >= EPS and abs(sb) >= EPS and (sa < 0) != (sb < 0):\n                    if tuple(np.round(a[\'pos\'], WELD)) <= tuple(np.round(b[\'pos\'], WELD)):\n                        p = a[\'pos\'] + (b[\'pos\'] - a[\'pos\']) * (sa / (sa - sb))\n                    else:\n                        p = b[\'pos\'] + (a[\'pos\'] - b[\'pos\']) * (sb / (sb - sa))\n                    p[axis] = c\n                    edge_pts.setdefault(_edge_key(a[\'pos\'], b[\'pos\']), {})[tuple(np.round(p, WELD))] = p\n    out, cut = [], 0\n    for i, p in enumerate(polys):\n        cs = p[\'corners\']\n        if edge_pts:\n            ncs = []\n            for j in range(len(cs)):\n                a, b = cs[j], cs[(j + 1) % len(cs)]\n                pts = edge_pts.get(_edge_key(a[\'pos\'], b[\'pos\']))\n                if pts:\n                    L = np.linalg.norm(b[\'pos\'] - a[\'pos\'])\n                    ts = sorted(((float(np.dot(q - a[\'pos\'], b[\'pos\'] - a[\'pos\']) / (L * L)), q) for q in pts.values()), key=lambda x: x[0])\n                    mids = [_lerp(a, b, t) for t, _ in ts]\n                    for mid, (_, q) in zip(mids, ts):\n                        mid[\'pos\'] = q.copy()\n                    ncs.extend(mids)\n                ncs.append(b)                       # corner j+1 (so the list starts at corner 1; rotated back below)\n            cs = ncs[-1:] + ncs[:-1]                # restore the original starting corner\n        if i in plan:\n            pieces = [cs]\n            for axis, c in plan[i]:\n                nxt = []\n                for pc in pieces:\n                    lo, hi = clip_poly(pc, axis, c)\n                    nxt += [x for x in (lo, hi) if _usable(x)]\n                pieces = nxt\n            for pc in pieces:\n                out.append({\'attrs\': p[\'attrs\'], \'corners\': pc, \'cut\': True})\n            cut += 1\n        else:\n            out.append({\'attrs\': p[\'attrs\'], \'corners\': cs})\n    return out, cut, skipped\n\n\n# ----------------------------------------------------------------------------------------------------------- writing\ndef mesh_write(m, node, polys):\n    """Rebuild the half-edge arrays and data streams of `node` from polygons.  Returns an error string or None (mesh untouched on error)."""\n    import dmx\n    md = m.g(node, b\'meshData\')\n    verts, vidx = [], {}\n\n    def vid(p):\n        k = tuple(np.round(p, WELD))\n        if k not in vidx:\n            vidx[k] = len(verts)\n            verts.append(p)\n        return vidx[k]\n\n    faces = []\n    for p in polys:\n        pairs = []\n        for c in p[\'corners\']:\n            k = vid(c[\'pos\'])\n            if pairs and pairs[-1][0] == k:\n                continue                            # two corners welded together: drop the repeat\n            pairs.append((k, c))\n        if len(pairs) > 1 and pairs[0][0] == pairs[-1][0]:\n            pairs.pop()\n        ids = [k for k, _ in pairs]\n        if len(ids) < 3 or len(set(ids)) != len(ids):\n            return \'degenerate or pinched face after welding\'\n        faces.append(({\'attrs\': p[\'attrs\'], \'corners\': [c for _, c in pairs]}, ids))\n    half = {}                                       # directed (origin, destination) -> half-edge index\n    dest, face_of, corner_of, edge_of, opp, nxt, fedge = [], [], [], [], [], [], []\n    for f, (p, ids) in enumerate(faces):\n        n = len(ids)\n        hs = []\n        for j in range(n):\n            u, v = ids[j - 1], ids[j]               # the half-edge into corner j runs from the previous corner to this one\n            h = half.get((u, v))\n            if h is None:                           # new edge: face side 2k, boundary twin 2k+1\n                k = len(dest) // 2\n                h = 2 * k\n                dest += [v, u]\n                face_of += [-1, -1]\n                corner_of += [None, None]\n                edge_of += [k, k]\n                opp += [h + 1, h]\n                nxt += [-1, -1]\n                half[(u, v)] = h\n                half[(v, u)] = h + 1\n            elif face_of[h] != -1:\n                return \'non-manifold edge (two faces use the same direction)\'\n            face_of[h] = f\n            corner_of[h] = (f, j)\n            hs.append(h)\n        for j in range(n):\n            nxt[hs[j]] = hs[(j + 1) % n]\n        fedge.append(hs[0])\n    H = len(dest)\n    # boundary loops: next boundary half-edge starts where this one ends\n    out_b = {}\n    for h in range(H):\n        if face_of[h] == -1:\n            origin = dest[opp[h]]\n            out_b.setdefault(origin, []).append(h)\n    for h in range(H):\n        if face_of[h] == -1:\n            lst = out_b.get(dest[h], [])\n            nxt[h] = lst.pop(0) if lst else opp[h]\n    nv = len(verts)\n    vedge = [-1] * nv\n    for h in range(H):\n        o = dest[opp[h]]\n        if vedge[o] == -1 or (face_of[h] != -1 and face_of[vedge[o]] == -1):\n            vedge[o] = h\n    if -1 in vedge:\n        return \'unused vertex\'\n    nf = len(faces)\n    # ---- write\n    def setv(el, name, val):\n        a = dmx.geta(m.E[el], name)\n        a[2] = val\n\n    setv(md, b\'vertexEdgeIndices\', vedge)\n    setv(md, b\'vertexDataIndices\', list(range(nv)))\n    setv(md, b\'edgeVertexIndices\', dest)\n    setv(md, b\'edgeOppositeIndices\', opp)\n    setv(md, b\'edgeNextIndices\', nxt)\n    setv(md, b\'edgeFaceIndices\', face_of)\n    setv(md, b\'edgeDataIndices\', edge_of)\n    setv(md, b\'edgeVertexDataIndices\', list(range(H)))\n    setv(md, b\'faceEdgeIndices\', fedge)\n    setv(md, b\'faceDataIndices\', list(range(nf)))\n    ne = H // 2\n\n    def resize(part, size, fill):\n        pd = m.g(md, part)\n        setv(pd, b\'size\', size)\n        for st in m.g(pd, b\'streams\') or []:\n            name = m.E[st][\'name\'].decode()\n            setv(st, b\'data\', fill(name))\n\n    resize(b\'vertexData\', nv, lambda name: [list(map(float, v)) for v in verts])\n    zero = {}\n    for k, (st, data) in _streams(m, node, b\'faceVertexData\').items():\n        zero[k] = [0.0] * len(data[0]) if data else [0.0]\n\n    def fvd_fill(name):\n        arr = [list(zero[name]) for _ in range(H)]\n        for h in range(H):\n            if corner_of[h] is not None:\n                f, j = corner_of[h]\n                arr[h] = [float(x) for x in faces[f][0][\'corners\'][j][\'fvd\'][name]]\n        return arr\n\n    # (zero template is taken from the old data before it is overwritten)\n    resize(b\'faceVertexData\', H, fvd_fill)\n    resize(b\'edgeData\', ne, lambda name: [0] * ne)\n    resize(b\'faceData\', nf, lambda name: [faces[f][0][\'attrs\'][name] for f in range(nf)])\n    sd = m.g(md, b\'subdivisionData\')\n    if sd is not None:\n        setv(sd, b\'subdivisionLevels\', [0] * H)\n    return None\n\n\n# ----------------------------------------------------------------------------------------------------------- checking\ndef mesh_check(m, node):\n    """Structural check of the half-edge arrays.  -> list of problems (empty = fine)"""\n    md = m.g(node, b\'meshData\')\n    g = lambda k: m.g(md, k)\n    dest, opp, nxt, face = g(b\'edgeVertexIndices\'), g(b\'edgeOppositeIndices\'), g(b\'edgeNextIndices\'), g(b\'edgeFaceIndices\')\n    fe, vedge = g(b\'faceEdgeIndices\'), g(b\'vertexEdgeIndices\')\n    evd, edi = g(b\'edgeVertexDataIndices\'), g(b\'edgeDataIndices\')\n    bad = []\n    H = len(dest)\n    if not (len(opp) == len(nxt) == len(face) == len(evd) == len(edi) == H):\n        return [\'array lengths differ\']\n    if sorted(evd) != list(range(len(evd))):\n        bad.append(\'edgeVertexDataIndices is not a permutation\')\n    for h in range(H):\n        if opp[opp[h]] != h:\n            bad.append(\'opposite of opposite of %d\' % h)\n        if dest[opp[h]] == dest[h]:\n            bad.append(\'half-edge %d is a loop\' % h)\n        if dest[h] != dest[opp[nxt[h]]] and False:\n            pass\n        if dest[opp[nxt[h]]] != dest[h]:\n            bad.append(\'next of %d does not start where it ends\' % h)\n        if face[h] != -1 and face[nxt[h]] != face[h]:\n            bad.append(\'next of %d leaves the face\' % h)\n    for f, h0 in enumerate(fe):\n        h, seen = h0, 0\n        while True:\n            if face[h] != f:\n                bad.append(\'face %d loop has a foreign half-edge\' % f)\n                break\n            h = nxt[h]\n            seen += 1\n            if h == h0 or seen > H:\n                break\n        if seen > H:\n            bad.append(\'face %d loop does not close\' % f)\n    for v, h in enumerate(vedge):\n        if dest[opp[h]] != v:\n            bad.append(\'vertexEdge of %d does not start at it\' % v)\n    dm = m.g(m.g(node, b\'meshData\'), b\'vertexData\')\n    for st in m.g(dm, b\'streams\'):\n        if len(m.g(st, b\'data\')) != m.g(dm, b\'size\'):\n            bad.append(\'vertexData stream length\')\n    return bad[:10]\n\n\n# ----------------------------------------------------------------------------------------------------------- whole-map pass\ndef _identity_xform(m, node):\n    o = m.g(node, b\'origin\', [0, 0, 0]); a = m.g(node, b\'angles\', [0, 0, 0]); sc = m.g(node, b\'scales\', [1, 1, 1])\n    ok = all(abs(x) < 1e-4 for x in a) and all(abs(x - 1) < 1e-4 for x in sc)\n    return ok, np.array(o, dtype=float)\n\n\ndef split_large(m, limit, cell, mode, skip_mat, log):\n    """Cut every lit face over `limit` sq units on a world grid of `cell`.  mode \'faces\' keeps one mesh; \'meshes\' also moves the\n    pieces into one mesh per grid cell (a cubemap / probe is assigned per mesh).  -> dict of counts + list of skipped notes"""\n    res = dict(meshes=0, faces=0, pieces=0, new_meshes=0, skipped=[])\n    owner = {}\n\n    def walk(n, label, cls):\n        for c in m.children(n):\n            t = m.type(c)\n            if t == b\'CMapMesh\':\n                owner[c] = (n, label, cls)\n            elif t == b\'CMapEntity\':\n                walk(c, \'%s %s\' % (m.cls(c), m.name(c) or \'-\'), m.cls(c))\n            else:\n                walk(c, label, cls)\n    walk(m.world, \'world\', \'world\')\n    for node, (parent, label, cls) in list(owner.items()):\n        if m.g(node, b\'meshData\') is None:\n            continue\n        if cls.startswith(\'func_water\') or cls.startswith(\'trigger_\'):\n            continue\n        ent_nodes = [e for e in m.entities() if m.cls(e) == cls and \'%s %s\' % (cls, m.name(e) or \'-\') == label] if cls != \'world\' else []\n        if ent_nodes and m.P(ent_nodes[0], \'lightmapstatic\', \'1\') == \'0\':\n            continue\n        try:\n            polys, mats = mesh_read(m, node)\n        except Exception:\n            continue\n        cand = lambda p: not any(x in mats[p[\'attrs\'][\'materialindex:0\']] for x in skip_mat)\n        big = [p for p in polys if cand(p) and poly_area(p[\'corners\']) > limit * 1.0001]\n        if not big:\n            continue\n        if mesh_has_subdivision(m, node):\n            res[\'skipped\'].append(\'%s (mesh %d): %d large face(s) in a displacement / subdivision mesh, not touched\' % (label, node, len(big)))\n            continue\n        ok, org = _identity_xform(m, node)\n        if not ok:\n            res[\'skipped\'].append(\'%s (mesh %d): %d large face(s), mesh is rotated or scaled, not touched\' % (label, node, len(big)))\n            continue\n        for p in polys:\n            p[\'corners\'] = [{\'pos\': c[\'pos\'] + org, \'fvd\': c[\'fvd\']} for c in p[\'corners\']]\n        newp, cut, sk = cut_polys(polys, limit, cell, cand)\n        for kind, area in sk:\n            res[\'skipped\'].append(\'%s (mesh %d): face of %d sq units %s, not cut\' % (label, node, area, kind))\n        if not cut:\n            continue\n        for p in newp:                              # corner dicts can be shared between pieces, so copy rather than shift in place\n            p[\'corners\'] = [{\'pos\': c[\'pos\'] - org, \'fvd\': c[\'fvd\']} for c in p[\'corners\']]\n        groups = {}\n        rest = []\n        if mode == \'meshes\':\n            for p in newp:\n                if p.get(\'cut\'):\n                    ctr = np.mean([c[\'pos\'] for c in p[\'corners\']], axis=0) + org\n                    groups.setdefault(tuple(int(x) for x in np.floor(ctr / cell)), []).append(p)\n                else:\n                    rest.append(p)\n        else:\n            rest = newp\n        if len(groups) <= 1 and not rest:\n            groups = {}\n            rest = newp\n        if len(groups) > 0 and rest:\n            pass\n        # write the original mesh first; if it has nothing left, it takes the first group\n        keys = sorted(groups)\n        first = rest if rest else groups[keys.pop(0)] if groups else newp\n        err = mesh_write(m, node, first)\n        if err:\n            res[\'skipped\'].append(\'%s (mesh %d): %s, not touched\' % (label, node, err))\n            continue\n        made = []\n        failed = False\n        for k in keys:\n            nid = [m.next_node_id()]\n            new = m.import_subtree(m, node, {}, nid)\n            e = mesh_write(m, new, groups[k])\n            if e:\n                failed = True\n                res[\'skipped\'].append(\'%s (mesh %d): tile %s: %s\' % (label, node, k, e))\n                break\n            made.append(new)\n        if failed:                                  # put every piece back in the original mesh\n            mesh_write(m, node, newp)\n            continue\n        if made:\n            from dmx import geta\n            arr = geta(m.E[parent], b\'children\')\n            arr[2].extend(made)\n        res[\'meshes\'] += 1\n        res[\'faces\'] += cut\n        res[\'pieces\'] += sum(1 for p in newp if p.get(\'cut\'))\n        res[\'new_meshes\'] += len(made)\n        log.append(\'%s (mesh %d): cut %d large face(s) into %d pieces%s\' % (label, node, cut, sum(1 for p in newp if p.get(\'cut\')), \', %d new mesh(es)\' % len(made) if made else \'\'))\n    return res\n'


def _install():
    """register the bundled modules so they can import each other by name"""
    for name in _MODULES:
        if name in sys.modules: continue
        sys.modules[name] = types.ModuleType(name)
    for name in ['dmx', 'kv2', 'vmapedit', 'lighting', 'visfit', 'probes', 'splitfaces']:
        mod = sys.modules[name]
        if getattr(mod, '_bundled', False): continue
        mod.__file__ = __file__; mod._bundled = True
        exec(compile(_MODULES[name], '<bundled %s.py>' % name, 'exec'), mod.__dict__)
_install()


import argparse, datetime, importlib, math, os, re, shutil, statistics, traceback


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


def ang_dir(pitch, yaw):
    p, y = math.radians(pitch), math.radians(yaw)
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))


def dir_to_ang(d):
    """direction -> Source pitch (positive = down) / yaw"""
    x, y, z = d
    h = math.hypot(x, y)
    return math.degrees(math.atan2(-z, h)), math.degrees(math.atan2(y, x))


def fmt(x):
    return ('%.6f' % x).rstrip('0').rstrip('.') if x != int(x) else '%d' % int(x)


                                                                                                               
                                                        
                                                                                                          
                                                     
                                                                   
                                                                                                                          
                         


                 
                   
                                                                        


                       
                                                                                                               
              
                                                  
                    
                    
                                     
                                   
                
                                                                                           
                           
                           
                           
                           
                                        
                 
                                               


                   
                                                                                                            
        
                                                       
                   
                   
                                                       
        
                                         
                      
                   
                                            


                
                                                                                                                                  
                                                                                                                                      
                                                                                                                                  
                                                                                                           

                                  
                                                     
                                                         
                       

                                
                               
                                    
                                                                                        
                  
                            
                               
                 
                                      
                     
                               
                  

                               
                                          
                       
                                 
                                
                             
                                                                                        
                        
                                                                                    
                                      
                                
                                                                        
                                                     
                                                                                    
                                      
                
                                             
                              
                    
                   

                                    
                               
                                         
                     
                  
                                                                            
                       
                           
                                       
                                                            
                                           
                        
                                                           
                                     
                                                                                       
                        
                     
                                                
                                 
                             
                        
                                     
                                      
                            
                         
                          
                                          
                                                                                                                         
                         
                                                                       
                        
                                                                                  
                                       
                                                   
                          
                         
                        
                                             
                                                        
                     
                                                       
                   
                                                          
                                  
                                   
                                                       
                                        
                                                                                                                  
                                         
                                
                                         
                                
                                                    
                                
                           
                                                                         
                               
                                  
                                       
                                            
                                                
                                    
                                                                   
                                  
                                           
                                                                                                                                       
                                                                     
                                                                             
                                  
                               
                                  
                                                                             
                                                                                                               
                                                                                   
                                                                           
                                
                                              
                                                                                                 
                                                 
                                                               
                            
                        
                             
                                             
                                                
                                         
                                                      
                                             
                                              
                                      
                                     
                                         
                                                                                                                                                                                                                                     
                                          
                                                   
                                                                  
                          
                        
                                          
                                                     
                            
                              
                                                        
                                       
                                           
                              
                                     
                                                                                                                                                                                                                    
                
                   
                                                       
                                                                   
                                                      
                                                                                                                        
                                                                                                                                                                                                                         
                                                                   
                  
                                                                                                                                                       
                  
                                                                                                                                                 
                                                                                                      
                
                                                                                                                                                    
                     
                                                                                                                                                                                                                                     
                             
                                          
                                    

                           
                                                                       
                
                                            
                                                                  
                                                                                              
                  


def dmx_get(el, k):
    import dmx
    return dmx.get(el, k)


def dmx_set(el, k, v):
    import dmx
    dmx.set_(el, k, dmx.T_STR, v)


def dmx_geta(el, k):
    import dmx
    return dmx.geta(el, k)


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
        for e in m.entities():
            k = m.cls(e)
            if k in DELETE_CLASSES and e in parent:
                from dmx import geta
                a = geta(m.E[parent[e]], b'children')
                a[2] = [x for x in a[2] if x != e]
                gone[k] += 1
        self.log.append('deleted: ' + ', '.join('%d %s' % (n, k) for k, n in gone.items() if n))
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
        for w in waters:
            bb = m.bounds(w)
            if not bb:
                continue
            lo, hi = bb
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
            self.todo.append('underwater lights are a heuristic (sparse grid, no light within 640 units); check the water areas and tune with --light-scale or delete them')

    # -------------------------------------------------- large faces (split)
    def split_faces(self):
        import splitfaces
        a = self.a
        notes = []
        skip_mat = ('materials/tools/', 'toolsnodraw', 'sky', 'moondome')
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
        for key, (tot, n, big, at) in sorted(groups.items(), key=lambda kv: -kv[1][2]):
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
        extra = [e for e in m.by_class('light_omni2', 'light_barn') if e not in mine]
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


def backup(path):
    d = os.path.join(os.path.dirname(os.path.abspath(path)), 'backups')
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    dst = os.path.join(d, '%s.%s%s' % (stem, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'), ext))
    shutil.copy2(path, dst)
    return dst


def find_vmf(vmap):
    base = os.path.splitext(vmap)[0]
    for cand in (base + '.vmf', os.path.join(os.path.dirname(os.path.dirname(vmap)), os.path.basename(base) + '.vmf')):
        if os.path.isfile(cand):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('vmap', help='path to the .vmap')
    ap.add_argument('--vmf', help='the Source 1 .vmf the map was ported from (default: same name next to the vmap)')
    ap.add_argument('-o', '--output', help='output path (default <map>_rebuilt.vmap next to the input)')
    ap.add_argument('--in-place', action='store_true', help='overwrite the input (backed up to backups\\ first)')
    ap.add_argument('--restore', metavar='BACKUP', help='copy BACKUP back over the vmap and exit (undo)')
    ap.add_argument('--light-scale', type=float, default=1.0, help='multiply the brightness of every recreated light (default 1)')
    ap.add_argument('--sun-scale', type=float, default=1.0, help='multiply the light_environment brightness (default 1)')
    ap.add_argument('--no-underwater', action='store_true', help='do not add lights inside water volumes')
    ap.add_argument('--large-face-area', type=float, default=262144.0, help='report lit faces larger than this many square units (default 262144 = 512 x 512); nothing is changed')
    ap.add_argument('--split-large-faces', choices=('off', 'faces', 'meshes'), default='meshes', help='cut lit faces over --large-face-area on a world grid (T-junctions repaired): "faces" keeps one mesh, "meshes" (default) also moves the pieces into one mesh per tile so each gets its own cubemap / probe; "off" only reports')
    ap.add_argument('--face-tile', type=float, default=512.0, help='grid size for --split-large-faces (default 512)')
    ap.add_argument('--exposure', type=float, nargs=2, metavar=('MIN', 'MAX'), help='set post_processing_volume min / max exposure')
    ap.add_argument('--skip-passes', action='store_true', help='do not run the probe / stationary light / vis passes')
    ap.add_argument('--skip-probes', action='store_true')
    ap.add_argument('--skip-lights', action='store_true', help='do not switch lights to Stationary')
    ap.add_argument('--skip-vis', action='store_true')
    ap.add_argument('--all-lower', action='store_true', help='vis: Lower hints instead of Low (for a vis compile that stalls)')
                                                                                                                                                                                                                                             
                                                                                                             
                                                                                                                                   
    a = ap.parse_args()

    if a.restore:
        if not os.path.isfile(a.restore):
            sys.exit('backup not found: %s' % a.restore)
        safety = backup(a.vmap) if os.path.isfile(a.vmap) else None
        shutil.copy2(a.restore, a.vmap)
        print('restored %s from %s' % (a.vmap, a.restore))
        if safety:
            print('(the version you just replaced was saved to %s)' % safety)
        return
    if not os.path.isfile(a.vmap):
        sys.exit('map not found: %s' % a.vmap)
    vmf = a.vmf or find_vmf(a.vmap)
    if not vmf or not os.path.isfile(vmf):
        sys.exit('the .vmf is needed: pass --vmf "C:\\...\\map.vmf"')
    try:
        importlib.import_module('numpy')
        importlib.import_module('scipy')
    except ImportError:
        sys.exit('needs numpy and scipy:  py -m pip install numpy scipy')
    import vmapedit, probes, lighting, visfit

    stem, ext = os.path.splitext(a.vmap)
    out = a.vmap if a.in_place else (a.output or stem + '_rebuilt' + ext)
    print('reading %s...' % vmf, flush=True)
    ents = parse_vmf(open(vmf, encoding='utf-8', errors='replace').read())
    nl = sum(1 for e in ents if e['kv'].get('classname', '').lower() in ('light', 'light_spot'))
    print('  %d entities, %d light / light_spot' % (len(ents), nl), flush=True)
    print('loading %s...' % a.vmap, flush=True)
    m = vmapedit.Map(a.vmap)
    rb = Rebuild(m, ents, a)
    try:
        rb.delete()
        rb.recreate()
        rb.sun()
        if not a.no_underwater:
            rb.underwater()
                            
                                                                                                                                                             
                                     
                                                           
                                   
        if a.exposure:
            for e in m.by_class('post_processing_volume'):
                m.setp(e, 'minexposure', fmt(a.exposure[0]))
                m.setp(e, 'maxexposure', fmt(a.exposure[1]))
            rb.log.append('post_processing_volume exposure set to %g - %g' % tuple(a.exposure))
        if not a.skip_passes:
            passes = []
            if not a.skip_probes:
                passes.append(('light probes', lambda: probes.run(m, rb, replace=True)))
            if not a.skip_lights:
                passes.append(('stationary lights', lambda: lighting.run(m, rb)))
            if not a.skip_vis:
                passes.append(('vis hints', lambda: visfit.run(m, rb, all_lower=a.all_lower)))
            for name, fn in passes:
                print('running %s...' % name, flush=True)
                fn()
            rb.normalise_added()
        if a.split_large_faces != 'off':
            print('splitting large faces...', flush=True)
            rb.split_faces()
        rb.large_faces()
        rb.log.append('removed %d orphaned elements' % m.prune())
    except Exception:
        traceback.print_exc()
        sys.exit('failed - nothing was saved, %s is unchanged' % a.vmap)

    bk = backup(a.vmap) if a.in_place else None
    m.save(os.path.abspath(out))
    lines = ['rebuild_lighting %s' % datetime.datetime.now().isoformat(timespec='seconds'), 'input:  %s' % a.vmap, 'vmf:    %s' % vmf, 'output: %s' % out]
    if bk:
        lines += ['backup: %s' % bk, 'undo:   py rebuild_lighting.py "%s" --restore "%s"' % (a.vmap, bk)]
    lines += ['', 'changes:'] + ['  - ' + x for x in rb.log] + ['', 'check in game:'] + ['  - ' + x for x in rb.todo]
                                        
                                                                                                                    
    if getattr(rb, 'split_notes', None):
        lines += ['', 'large faces split (%s):' % a.split_large_faces] + ['  - ' + x for x in rb.split_notes]
    lines += ['', 'large faces remaining (reported only, nothing was changed; over %d sq units, each is one lightmap chart):' % a.large_face_area] + ['  - ' + x for x in rb.faces_report]
    lines += ['', 'large faces that are probably fine:'] + ['  - ' + x for x in rb.faces_ignored]
    rep = out + '.lightlog.txt'
    with open(rep, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n' + '\n'.join(lines) + '\n\nreport saved to %s' % rep)
    print('\nReopen the map in Hammer and do a full lighting bake.')


if __name__ == '__main__':
    main()
