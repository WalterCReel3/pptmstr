"""
Probes M1, R1-hostile and R2 from planning/2026-10-18-vector-displays-beside-typeset-text.md §7.

    .venv/bin/python scripts/figure_render_probe.py [m1|r1a|r1b|r1c|r2|r3|all]

M1   merman 0.9.1 with svg.pipeline=resvg-safe on five diagram families, then resvg.
R1a  nested <g>/<use> fan-out against resvg_py: time and peak RSS per depth, each
     depth in a child process under RLIMIT_AS and a wall-clock timeout.
R1b  url() to outside the document in fill/clip-path/mask/marker-start/filter:
     inotify on the target directory and a listening socket on 127.0.0.1.
R1c  font-family="Arial" (not loaded) under skip_system_fonts with generics mapped.
R2   whether svg_to_bytes releases the GIL: max gap of a 1 ms ticker thread.
R3   shapes under the sanitiser's caps that may cost far more than their element
     count (mask and clip chains, layers, pattern tiles, mid markers, dashes, filter
     functions): sanitised, then rasterised at 1600 px, each in a child under
     RLIMIT_AS and a wall-clock timeout; time and peak RSS, or the refusal.

No sanitiser runs anywhere here but in R3; resvg is fed the SVG as written. Nothing is
fetched from the network: every http:// target is a socket this script owns.
PNGs go to shots/figure-probe/, which /shots/ in .gitignore covers.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import resource
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any

import resvg_py
from fontTools.ttLib import TTFont
from imscribe import Face
from imscribe.fontfiles import font_file

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "shots" / "figure-probe"

FONT_FILES = [
    str(font_file(f)) for f in (Face.PROSE, Face.PROSE_BOLD, Face.PROSE_ITALIC, Face.CODE)
]


def family_name(path: str) -> str:
    """
    The typographic family (name ID 16) if present, else name ID 1.
    """
    names = TTFont(path, lazy=True)["name"]
    for nid in (16, 1):
        rec = names.getName(nid, 3, 1) or names.getName(nid, 1, 0)
        if rec is not None:
            return str(rec.toUnicode())
    raise ValueError(path)


SANS = family_name(FONT_FILES[0])
MONO = family_name(FONT_FILES[3])
BARE: dict[str, Any] = dict(font_files=FONT_FILES, skip_system_fonts=True)
MAPPED: dict[str, Any] = dict(
    BARE,
    font_family=SANS,
    sans_serif_family=SANS,
    serif_family=SANS,
    cursive_family=SANS,
    fantasy_family=SANS,
    monospace_family=MONO,
)


def png_size(png: bytes) -> tuple[int, int]:
    w, h = struct.unpack(">II", png[16:24])
    return w, h


def doc(body: str, w: int = 200, h: int = 100) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        f' width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="#fff"/>{body}</svg>'
    )


# --------------------------------------------------------------------------- M1

# Written the way Claude usually writes them: quoted labels with punctuation,
# edge labels, subgraphs, notes, alt blocks, cardinalities, composite states.
DIAGRAMS = {
    "flowchart": """flowchart TD
    A[User submits form] --> B{Valid input?}
    B -->|Yes| C[Save to database]
    B -->|No| D[Show validation errors]
    D --> A
    C --> E[(PostgreSQL)]
    C --> F[Send confirmation email]
    subgraph Background["Background jobs"]
        F --> G[Queue: email-worker]
        G --> H[SMTP relay]
    end
    H --> I([Done])
""",
    "sequence": """sequenceDiagram
    autonumber
    participant C as Client
    participant G as API Gateway
    participant A as Auth Service
    participant D as Database
    C->>G: POST /login {user, password}
    G->>A: validate(credentials)
    A->>D: SELECT * FROM users WHERE name = ?
    D-->>A: user row
    alt password matches
        A-->>G: 200 + JWT
        G-->>C: Set-Cookie: session=...
    else mismatch
        A-->>G: 401 Unauthorized
        G-->>C: 401
    end
    Note over C,G: The token expires after 15 minutes
""",
    "class": """classDiagram
    class Animal {
        <<abstract>>
        +String name
        +int age
        +makeSound()* void
    }
    class Dog {
        +String breed
        +fetch(item: Ball) bool
    }
    class Cat {
        -int lives
        +purr() void
    }
    class Owner {
        +List~Animal~ pets
        +adopt(a: Animal)
    }
    Animal <|-- Dog
    Animal <|-- Cat
    Owner "1" o-- "*" Animal : owns
""",
    "state": """stateDiagram-v2
    [*] --> Idle
    Idle --> Running : start()
    Running --> Paused : pause()
    Paused --> Running : resume()
    Running --> Failed : error
    Failed --> Idle : reset()
    Running --> [*] : complete
    state Running {
        [*] --> Fetching
        Fetching --> Processing
        Processing --> Fetching : more data
    }
    note right of Failed : Retries are exhausted
""",
    "er": """erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
    PRODUCT ||--o{ LINE_ITEM : "ordered in"
    CUSTOMER {
        int id PK
        string name
        string email UK
    }
    ORDER {
        int id PK
        int customer_id FK
        date created_at
    }
    LINE_ITEM {
        int order_id FK
        int product_id FK
        int quantity
    }
    PRODUCT {
        int id PK
        string sku
        decimal price
    }
""",
}

RESVG_SAFE = '{"svg":{"pipeline":"resvg-safe"}}'


def root_attrs(svg: str) -> dict[str, str | None]:
    m = re.search(r"<svg\b[^>]*>", svg)
    assert m
    tag = m.group(0)
    out: dict[str, str | None] = {}
    for a in ("width", "height", "viewBox", "style"):
        v = re.search(rf'\s{a}="([^"]*)"', tag)
        out[a] = v.group(1) if v else None
    return out


def counts(svg: str) -> dict[str, int]:
    return {
        t: len(re.findall(rf"<{t}\b", svg)) for t in ("foreignObject", "text", "style", "marker")
    }


def probe_m1() -> None:
    import merman

    m = merman.Merman()
    print(
        "merman",
        m.package_version(),
        "resvg_py",
        resvg_py.__version__,
        "resvg",
        resvg_py.__resvg_version__,
    )
    print("fonts:", SANS, "/", MONO, FONT_FILES)
    cat = json.loads(m.runtime_catalog_json())
    print("runtime_catalog capabilities.output_ids:", cat["capabilities"]["output_ids"])
    print("runtime_catalog output_contracts ids:", [c["id"] for c in cat["output_contracts"]])
    print("'png' anywhere in catalog JSON:", '"png"' in m.runtime_catalog_json())
    try:
        m.render_png(DIAGRAMS["flowchart"], None)
        print("render_png: returned bytes")
    except Exception as e:  # noqa: BLE001 -- the error text is the finding
        print("render_png:", type(e).__name__, getattr(e, "message", str(e))[:200])
    try:
        m.render_svg(DIAGRAMS["flowchart"], '{"svg":{"pipeline":"no-such"}}')
        print("unknown pipeline: accepted silently")
    except Exception as e:  # noqa: BLE001
        print("unknown pipeline:", getattr(e, "message", str(e))[:200])

    OUT.mkdir(parents=True, exist_ok=True)
    for name, src in DIAGRAMS.items():
        print(f"\n[{name}]")
        for label, opts in (("default", None), ("resvg-safe", RESVG_SAFE)):
            m.render_svg(src, opts)  # warm
            t = time.perf_counter()
            svg = m.render_svg(src, opts)
            dt = 1000 * (time.perf_counter() - t)
            print(f"  merman {label:10s} {dt:6.1f} ms  {len(svg):6d} B  {counts(svg)}")
        print("  root:", root_attrs(svg))
        hrefs = sorted(set(re.findall(r'(?:xlink:)?href="([^"]*)"', svg)))
        urls = sorted(set(re.findall(r"url\(([^)]*)\)", svg)))
        print("  hrefs:", hrefs[:8], " url():", urls[:8])
        (OUT / f"m1-{name}.svg").write_text(svg)
        sizes: tuple[tuple[str, dict[str, Any]], ...] = (
            ("natural", {}),
            ("width=800", {"width": 800}),
        )
        for wlabel, kw in sizes:
            resvg_py.svg_to_bytes(svg_string=svg, **MAPPED, **kw)  # warm
            t = time.perf_counter()
            png = resvg_py.svg_to_bytes(svg_string=svg, **MAPPED, **kw)
            dt = 1000 * (time.perf_counter() - t)
            (OUT / f"m1-{name}-{wlabel.replace('=', '')}.png").write_bytes(png)
            print(f"  resvg {wlabel:9s} {dt:6.1f} ms  -> {png_size(png)}")
        unmapped = resvg_py.svg_to_bytes(svg_string=svg, **BARE, width=800)
        mapped = resvg_py.svg_to_bytes(svg_string=svg, **MAPPED, width=800)
        print(f"  generics unmapped vs mapped differ: {unmapped != mapped}")
        m1_variants(name, svg)
    print(f"\nPNGs and SVGs in {OUT}")


def element_set(svg: str) -> str:
    """
    Distinct element names, total element count, and how many <use> point at a
    target that itself contains a <use> (what §3.4 step 5 would refuse).
    """
    import xml.etree.ElementTree as ET

    ns = "{http://www.w3.org/2000/svg}"
    root = ET.fromstring(svg)
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}
    uses = [e for e in root.iter() if e.tag == ns + "use"]
    chained = 0
    for u in uses:
        ref = (u.get("href") or u.get("{http://www.w3.org/1999/xlink}href") or "#")[1:]
        target = ids.get(ref)
        if target is not None and any(x.tag == ns + "use" for x in target.iter()):
            chained += 1
    tags = sorted({e.tag.replace(ns, "") for e in root.iter()})
    total = sum(1 for _ in root.iter())
    return f"{total} elements, {len(uses)} <use> ({chained} chained), {tags}"


def m1_variants(name: str, svg: str) -> None:
    """
    Three rewrites a sanitiser might make, each rendered and timed:
    - the nested label <svg>s' overflow:hidden made visible (label clipping);
    - <filter> elements removed with their url(#id) references left in place,
      which is what §3.4 as written produces (filter is not on the allow-list,
      and url(#id) survives steps 3 and 4);
    - <filter> elements and every reference to them removed.
    """
    refs = len(re.findall(r'filter[:=]\s*"?url\(', svg))
    print(
        f"  nested <svg>: {svg.count('<svg') - 1}, overflow:hidden {svg.count('overflow:hidden')},"
        f" overflow:visible {svg.count('overflow:visible')}, <filter> {svg.count('<filter')},"
        f" filter:url refs {refs}"
    )
    print(f"  element set: {element_set(svg)}")
    no_filter_el = re.sub(r"<filter\b.*?</filter>", "", svg, flags=re.S)
    no_filter = re.sub(r"filter:url\(#[^)]*\)", "filter:none", no_filter_el)
    no_filter = re.sub(r'filter="url\(#[^)]*\)"', "", no_filter)
    variants = {
        "overflow-visible": svg.replace("overflow:hidden", "overflow:visible"),
        "filter-elements-dropped-refs-kept": no_filter_el,
        "no-filter": no_filter,
    }
    full = resvg_py.svg_to_bytes(svg_string=svg, **MAPPED)
    for vname, v in variants.items():
        resvg_py.svg_to_bytes(svg_string=v, **MAPPED)
        t = time.perf_counter()
        png = resvg_py.svg_to_bytes(svg_string=v, **MAPPED)
        dt = 1000 * (time.perf_counter() - t)
        (OUT / f"m1-{name}-{vname}.png").write_bytes(png)
        print(f"  variant {vname:34s} {dt:6.1f} ms  differs from as-emitted: {png != full}")


# --------------------------------------------------------------------------- R1a


def fanout_svg(depth: int, fan: int) -> str:
    """
    L0 is one rect and one text; Lk is a <g> of `fan` <use> of L(k-1), so the
    rendered tree has fan**depth leaves from about depth*fan source elements.
    """
    defs = [
        '<g id="L0"><rect width="4" height="4" fill="#4a7"/><text y="4" font-size="3">x</text></g>'
    ]
    for k in range(1, depth + 1):
        uses = "".join(f'<use href="#L{k - 1}" x="{i % 4}" y="{i // 4}"/>' for i in range(fan))
        defs.append(f'<g id="L{k}">{uses}</g>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        f'<defs>{"".join(defs)}</defs><use href="#L{depth}"/></svg>'
    )


def r1a_child(depth: int, fan: int) -> None:
    svg = fanout_svg(depth, fan)
    t = time.perf_counter()
    try:
        resvg_py.svg_to_bytes(svg_string=svg, **MAPPED)
        status = "ok"
    except BaseException as e:  # noqa: BLE001 -- MemoryError/ValueError are both findings
        status = f"{type(e).__name__}: {str(e)[:120]}"
    dt = time.perf_counter() - t
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            {
                "bytes": len(svg),
                "s": round(dt, 3),
                "maxrss_mb": round(rss / 1024, 1),
                "status": status,
            }
        )
    )


AS_LIMIT = 2 * 1024**3
TIMEOUT_S = 30


def probe_r1a() -> None:
    print(f"each case in a child: RLIMIT_AS={AS_LIMIT >> 20} MiB, timeout {TIMEOUT_S}s")
    # Depth sweep at fan 10, then a fan sweep at depth 3 to find the costliest
    # case that usvg's node limit still admits.
    for fan, depths in ((10, range(1, 9)), (20, (3,)), (30, (3,)), (40, (3,)), (50, (3,))):
        for depth in depths:
            leaves = fan**depth

            def limit() -> None:
                resource.setrlimit(resource.RLIMIT_AS, (AS_LIMIT, AS_LIMIT))

            t = time.perf_counter()
            try:
                p = subprocess.run(
                    [sys.executable, __file__, "_r1a", str(depth), str(fan)],
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT_S,
                    preexec_fn=limit,
                )
                line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
                tail = p.stderr.strip().splitlines()[-1:] if p.returncode else []
                print(
                    f"  fan={fan} depth={depth} leaves={leaves:>12,}"
                    f"  rc={p.returncode} {line} {tail}"
                )
                stop = p.returncode != 0 or (line and json.loads(line)["s"] > 10)
            except subprocess.TimeoutExpired:
                print(
                    f"  fan={fan} depth={depth} leaves={leaves:>12,}"
                    f"  TIMEOUT after {time.perf_counter() - t:.1f}s"
                )
                stop = True
            if stop:
                print("  stopping: bound reached")
                break


# --------------------------------------------------------------------------- R1b

IN_OPEN, IN_ACCESS = 0x20, 0x01


class Inotify:
    def __init__(self, path: str) -> None:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK)
        if self.fd < 0 or libc.inotify_add_watch(self.fd, path.encode(), IN_OPEN | IN_ACCESS) < 0:
            raise OSError(ctypes.get_errno(), "inotify")

    def drain(self) -> list[str]:
        out: list[str] = []
        try:
            buf = os.read(self.fd, 65536)
        except BlockingIOError:
            return out
        i = 0
        while i < len(buf):
            _, mask, _, n = struct.unpack_from("iIII", buf, i)
            name = buf[i + 16 : i + 16 + n].rstrip(b"\0").decode()
            out.append(
                f"{name}:{'OPEN' if mask & IN_OPEN else ''}{'ACCESS' if mask & IN_ACCESS else ''}"
            )
            i += 16 + n
        return out


def tiny_png(w: int = 20, h: int = 20) -> bytes:
    raw = b"".join(b"\0" + b"\xff\0\0\xff" * w for _ in range(h))

    def c(k: bytes, b: bytes) -> bytes:
        return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b))

    return (
        b"\x89PNG\r\n\x1a\n"
        + c(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + c(b"IDAT", zlib.compress(raw))
        + c(b"IEND", b"")
    )


def probe_r1b() -> None:
    d = tempfile.mkdtemp(prefix="r1b-")
    target_svg = os.path.join(d, "ext.svg")
    Path(target_svg).write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><defs>'
        '<linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient>'
        '<clipPath id="g2"><rect width="5" height="5"/></clipPath></defs></svg>'
    )
    Path(os.path.join(d, "ext.png")).write_bytes(tiny_png())
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.05)
    port = srv.getsockname()[1]
    hits: list[str] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.settimeout(0.2)
                try:
                    hits.append(c.recv(200).decode(errors="replace").splitlines()[0])
                except Exception:  # noqa: BLE001
                    hits.append("<connect, no request line>")
                c.close()
            except TimeoutError:
                pass

    th = threading.Thread(target=accept, daemon=True)
    th.start()
    ino = Inotify(d)
    targets = {
        "abs": f"{target_svg}#g",
        "file://": f"file://{target_svg}#g",
        "http://": f"http://127.0.0.1:{port}/ext.svg#g",
        "relative": "ext.svg#g",
    }
    shape = '<rect x="10" y="10" width="80" height="80" {attr}/>'
    # Each template, and the same rect with the url() attribute absent: an
    # external url() that renders like the absent one was treated as no value.
    attrs = {
        "fill": ('fill="url({u})"', ""),
        "clip-path": ('fill="#000" clip-path="url({u})"', 'fill="#000"'),
        "mask": ('fill="#000" mask="url({u})"', 'fill="#000"'),
        "filter": ('fill="#000" filter="url({u})"', 'fill="#000"'),
        "style fill": ('style="fill:url({u})"', ""),
    }
    print(f"targets in {d}, listener on 127.0.0.1:{port}; resources_dir={d}")
    print(f"  inotify events before probe: {ino.drain()}")

    def render(body: str) -> bytes:
        return resvg_py.svg_to_bytes(svg_string=doc(body), **MAPPED, resources_dir=d)

    blank = render("")
    for aname, (tmpl, absent_attr) in attrs.items():
        absent = render(shape.format(attr=absent_attr))
        missing = render(shape.format(attr=tmpl.format(u="#nonexistent")))
        as_ = "attribute-absent" if missing == absent else "blank" if missing == blank else "other"
        print(f"  {aname}: url(#nonexistent) renders as {as_}")
        for tname, u in targets.items():
            status = "ok"
            try:
                out = render(shape.format(attr=tmpl.format(u=u)))
                drew = (
                    "as-if-attribute-absent"
                    if out == absent
                    else (
                        "as-url(#nonexistent)"
                        if out == missing
                        else "blank" if out == blank else "OTHER"
                    )
                )
            except Exception as e:  # noqa: BLE001
                status, drew = f"{type(e).__name__}: {str(e)[:80]}", ""
            time.sleep(0.1)
            print(f"  {aname:11s} {tname:9s} {status} {drew}  fs={ino.drain()} net={hits[:]}")
            hits.clear()
    for tname, u in targets.items():
        body = (
            '<defs><marker id="m" markerWidth="10" markerHeight="10">'
            '<circle cx="5" cy="5" r="5"/></marker></defs>'
            f'<path d="M20 50 L180 50" stroke="#000" marker-start="url({u})"/>'
        )
        resvg_py.svg_to_bytes(svg_string=doc(body), **MAPPED, resources_dir=d)
        time.sleep(0.1)
        print(f"  {'marker-start':11s} {tname:9s} fs={ino.drain()} net={hits[:]}")
        hits.clear()
    # Positive control: the inotify watch and the listener do see a known read.
    resvg_py.svg_to_bytes(
        svg_string=doc('<image width="20" height="20" href="ext.png"/>'), **MAPPED, resources_dir=d
    )
    time.sleep(0.1)
    print(f"  CONTROL <image href=ext.png> fs={ino.drain()}")
    with socket.create_connection(("127.0.0.1", port)) as c:
        c.sendall(b"GET /control HTTP/1.0\r\n\r\n")
    time.sleep(0.2)
    print(f"  CONTROL socket connect net={hits[:]}")
    stop.set()
    th.join()


# --------------------------------------------------------------------------- R1c


def probe_r1c() -> None:
    blank = resvg_py.svg_to_bytes(svg_string=doc(""), **MAPPED)
    print(
        f"generics mapped to {SANS!r}/{MONO!r}; skip_system_fonts=True; font_files=4 imscribe faces"
    )
    for fam in (
        "sans-serif",
        "Arial",
        "Arial, sans-serif",
        "'Arial', sans-serif",
        "NoSuchFace",
        "monospace",
        "Courier New",
        SANS,
    ):
        body = f'<text x="5" y="50" font-family="{fam}" font-size="20">Hello, figure</text>'
        out = resvg_py.svg_to_bytes(svg_string=doc(body), **MAPPED)
        print(f"  font-family={fam!r:24s} {'DREW' if out != blank else 'NOTHING DRAWN'}")
    print("  and with generics unmapped (font_files + skip_system_fonts only):")
    blank_b = resvg_py.svg_to_bytes(svg_string=doc(""), **BARE)
    for fam in ("Arial", "Arial, sans-serif", SANS):
        body = f'<text x="5" y="50" font-family="{fam}" font-size="20">Hello, figure</text>'
        out = resvg_py.svg_to_bytes(svg_string=doc(body), **BARE)
        print(f"  font-family={fam!r:24s} {'DREW' if out != blank_b else 'NOTHING DRAWN'}")
    print("  and with one family option mapped at a time:")
    for opt in ("font_family", "sans_serif_family", "serif_family"):
        kw = dict(BARE, **{opt: SANS})
        blank_o = resvg_py.svg_to_bytes(svg_string=doc(""), **kw)
        for fam in ("Arial", "Arial, sans-serif", "sans-serif"):
            body = f'<text x="5" y="50" font-family="{fam}" font-size="20">Hello, figure</text>'
            out = resvg_py.svg_to_bytes(svg_string=doc(body), **kw)
            print(
                f"  only {opt:17s} font-family={fam!r:22s}"
                f" {'DREW' if out != blank_o else 'NOTHING DRAWN'}"
            )


# --------------------------------------------------------------------------- R2

BIG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'>"
    + "".join(
        f"<rect x='{i % 40 * 20}' y='{i // 40 * 20}' width='18' height='18' rx='4' fill='#4a7'/>"
        f"<text x='{i % 40 * 20}' y='{i // 40 * 20 + 14}' font-size='10'>n{i}</text>"
        for i in range(1200)
    )
    + "</svg>"
)


def ticker(seconds: float, renders: list[float] | None) -> tuple[float, int, float]:
    """
    Tick every 1 ms for `seconds`; when `renders` is a list, a second thread
    renders BIG back to back meanwhile and appends each render's duration.
    Returns (max gap ms, ticks, p99 gap ms).
    """
    stop = threading.Event()

    def render() -> None:
        while not stop.is_set():
            t = time.perf_counter()
            resvg_py.svg_to_bytes(svg_string=BIG, **MAPPED)
            assert renders is not None
            renders.append(1000 * (time.perf_counter() - t))

    th = threading.Thread(target=render, daemon=True) if renders is not None else None
    if th:
        th.start()
    gaps: list[float] = []
    end = time.perf_counter() + seconds
    last = time.perf_counter()
    while (now := time.perf_counter()) < end:
        time.sleep(0.001)
        now = time.perf_counter()
        gaps.append(1000 * (now - last))
        last = now
    stop.set()
    if th:
        th.join()
    gaps.sort()
    return gaps[-1], len(gaps), gaps[int(0.99 * len(gaps))]


def probe_r2() -> None:
    print("switchinterval", sys.getswitchinterval())
    resvg_py.svg_to_bytes(svg_string=BIG, **MAPPED)  # warm
    mx, n, p99 = ticker(3.0, None)
    print(f"  no render:   ticks={n:5d} max gap {mx:7.2f} ms  p99 {p99:6.2f} ms")
    renders: list[float] = []
    mx, n, p99 = ticker(3.0, renders)
    print(
        f"  with render: ticks={n:5d} max gap {mx:7.2f} ms  p99 {p99:6.2f} ms  "
        f"renders={len(renders)} each {min(renders):.0f}-{max(renders):.0f} ms"
    )


# --------------------------------------------------------------------------- R3

R3_W, R3_H = 1600, 1200
R3_AS_LIMIT = 8 * 1024**3
R3_TIMEOUT_S = 60


def r3_cases() -> dict[str, str]:
    """
    Shapes that stay under the sanitiser's caps and may allocate far more than their
    element count suggests (fig-review finding 2). Each is a whole document at
    R3_W x R3_H; each is passed through the sanitiser before it is rendered.
    """
    full = f'width="{R3_W}" height="{R3_H}"'
    head = f'<svg xmlns="http://www.w3.org/2000/svg" {full} viewBox="0 0 {R3_W} {R3_H}">'

    def mask_chain(n: int) -> str:
        levels = "".join(
            f'<mask id="m{i}"><rect {full} fill="white"'
            + (f' mask="url(#m{i + 1})"' if i + 1 < n else "")
            + "/></mask>"
            for i in range(n)
        )
        return f'{head}<defs>{levels}</defs><rect {full} fill="red" mask="url(#m0)"/></svg>'

    def clip_chain(n: int) -> str:
        levels = "".join(
            f'<clipPath id="c{i}"'
            + (f' clip-path="url(#c{i + 1})"' if i + 1 < n else "")
            + f"><rect {full}/></clipPath>"
            for i in range(n)
        )
        return f'{head}<defs>{levels}</defs><rect {full} fill="red" clip-path="url(#c0)"/></svg>'

    def opacity_nest(n: int) -> str:
        return head + '<g opacity=".9">' * n + f'<rect {full} fill="red"/>' + "</g>" * n + "</svg>"

    def masked_nest(n: int) -> str:
        defs = f'<defs><mask id="m"><rect {full} fill="white"/></mask></defs>'
        return head + defs + '<g mask="url(#m)">' * n + f"<rect {full}/>" + "</g>" * n + "</svg>"

    def pattern(tile: int, transform: str = "") -> str:
        extra = f' patternTransform="{transform}"' if transform else ""
        return (
            f'{head}<defs><pattern id="p" width="{tile}" height="{tile}"'
            f' patternUnits="userSpaceOnUse"{extra}><rect width="{tile}" height="{tile}"'
            f' fill="red"/><circle r="5"/></pattern></defs><rect {full} fill="url(#p)"/></svg>'
        )

    def marker_mid(vertices: int) -> str:
        pts = " ".join(f"{i % R3_W} {(i * 7) % R3_H}" for i in range(vertices))
        marker = (
            '<marker id="k" markerWidth="40" markerHeight="40" refX="20" refY="20">'
            '<circle cx="20" cy="20" r="18" fill="blue" opacity=".5"/></marker>'
        )
        return (
            f'{head}<defs>{marker}</defs><path d="M{pts}" stroke="red" marker-mid="url(#k)"/></svg>'
        )

    def dashes(paths: int) -> str:
        line = f'<path d="M0 0 L{R3_W} {R3_H}" stroke="red" stroke-dasharray="0.001"/>'
        return head + line * paths + "</svg>"

    def blur() -> str:
        return head + f'<rect {full} fill="red" filter="blur(200px)"/>' * 50 + "</svg>"

    return {
        "mask chain 2,400": mask_chain(2_400),
        "mask chain 600": mask_chain(600),
        "clip-path chain 2,400": clip_chain(2_400),
        "opacity nest 62": opacity_nest(62),
        "mask on 62 nested groups": masked_nest(62),
        "pattern tile 16384": pattern(16_384),
        "pattern tile 100 scale(160)": pattern(100, "scale(160)"),
        "marker-mid 60,000 vertices": marker_mid(60_000),
        "dasharray .001 on 4,000 paths": dashes(4_000),
        "filter blur() on 50 rects": blur(),
    }


def r3_child(name: str) -> None:
    from pptmstr.figures.render import rasterise
    from pptmstr.figures.sanitise import Sanitised, sanitise

    out = sanitise(r3_cases()[name])
    if not isinstance(out, Sanitised):
        print(json.dumps({"status": f"refused: {out.reason}"}))
        return
    t = time.perf_counter()
    try:
        w, h, _ = rasterise(out.svg, R3_W, {})
        status = f"ok {w}x{h}"
    except BaseException as e:  # noqa: BLE001 -- MemoryError is a finding
        status = f"{type(e).__name__}: {str(e)[:120]}"
    dt = time.perf_counter() - t
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"s": round(dt, 2), "maxrss_mb": round(rss / 1024), "status": status}))


def probe_r3() -> None:
    print(
        f"each case sanitised, then rasterised at {R3_W} px in a child:"
        f" RLIMIT_AS={R3_AS_LIMIT >> 20} MiB, timeout {R3_TIMEOUT_S}s"
    )

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (R3_AS_LIMIT, R3_AS_LIMIT))

    for name in r3_cases():
        t = time.perf_counter()
        try:
            p = subprocess.run(
                [sys.executable, __file__, "_r3", name],
                capture_output=True,
                text=True,
                timeout=R3_TIMEOUT_S,
                preexec_fn=limit,
            )
            line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
            tail = p.stderr.strip().splitlines()[-1:] if p.returncode else []
            print(f"  {name:32s} rc={p.returncode} {line} {tail}")
        except subprocess.TimeoutExpired:
            print(f"  {name:32s} TIMEOUT after {time.perf_counter() - t:.1f}s")


def main(argv: list[str]) -> None:
    if argv[:1] == ["_r1a"]:
        r1a_child(int(argv[1]), int(argv[2]))
        return
    if argv[:1] == ["_r3"]:
        r3_child(argv[1])
        return
    which = argv[0] if argv else "all"
    probes = {
        "m1": probe_m1,
        "r1a": probe_r1a,
        "r1b": probe_r1b,
        "r1c": probe_r1c,
        "r2": probe_r2,
        "r3": probe_r3,
    }
    for name, fn in probes.items():
        if which in (name, "all"):
            print(f"\n===== {name}")
            fn()


if __name__ == "__main__":
    main(sys.argv[1:])
