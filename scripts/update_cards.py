"""Nightly refresh: pull singles from Your Turn Games' public store listing and rebuild
cards.json (names, types, brigades, sets, prices, links) plus the small card pictures.

Picture sheets keep a fixed slot for every card (thumbs-index.json), so a night with no new
cards or picture changes rewrites nothing and makes no commit."""
import io, json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

STORE = "https://www.yourturngames.biz"
UA = {"User-Agent": "redemption-scanner-update/1.0 (+github.com/Banana346-cyph/redemption-scanner)"}
PER, COLS, TW, TH = 100, 10, 72, 100
TYPES = {"Hero": "H", "Evil Character": "EC", "Good Enhancement": "GE", "Evil Enhancement": "EE", "Fortress": "F",
         "Dominant": "D", "Site": "S", "Lost Soul": "LS", "Artifact": "A", "Curse": "CU", "Covenant": "CO", "City": "CI"}
BRIGADES = ["White", "Green", "Brown", "Black", "Crimson", "Silver", "Gray", "Gold", "Purple", "Blue", "Pale Green",
            "Red", "Orange", "Clay", "Evil Gold", "Teal", "Multi"]


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:  # network hiccup: wait and retry
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def fetch_products():
    out = []
    for page in range(1, 400):
        items = json.loads(get(f"{STORE}/products.json?limit=250&page={page}"))["products"]
        if not items:
            break
        out += items
    return out


def thumb(src):
    if not src:
        return None
    try:
        im = Image.open(io.BytesIO(get(src + ("&" if "?" in src else "?") + "width=144"))).convert("RGB")
        return im.resize((TW, TH), Image.LANCZOS)
    except Exception as e:
        print("picture failed:", src, e, file=sys.stderr)
        return None



# --- picture summaries (pics.bin): one 420-byte colour layout per store picture, used by the app to recognise a card
#     from its picture. Must stay in step with picFeat() in index.html.
PGW, PGH = 10, 14
def _trim_box(px, w, h):  # px: list of (r,g,b); cut white border lines (min channel > 200 on > 60% of the line)
    wt = [min(p) > 200 for p in px]
    row = lambda y: sum(wt[y * w:(y + 1) * w]) / w
    col = lambda x: sum(wt[x::w]) / h
    t = 0
    while t < h // 4 and row(t) > 0.6: t += 1
    b = h
    while b > h * 3 // 4 and row(b - 1) > 0.6: b -= 1
    l = 0
    while l < w // 4 and col(l) > 0.6: l += 1
    r = w
    while r > w * 3 // 4 and col(r - 1) > 0.6: r -= 1
    return t, b, l, r
def _feat(px, w, t, b, l, r, m=0.05):  # plain-Python version of the app's picFeat()
    import math
    h2, w2 = b - t, r - l
    y0, y1, x0, x1 = h2 * m, h2 * (1 - m), w2 * m, w2 * (1 - m)
    n = PGW * PGH; S = [[0.0, 0.0, 0.0] for _ in range(n)]; C = [0] * n
    cx = [math.floor((x + 0.5 - x0) / (x1 - x0) * PGW) for x in range(w2)]
    for y in range(h2):
        cy = math.floor((y + 0.5 - y0) / (y1 - y0) * PGH)
        if cy < 0 or cy >= PGH: continue
        for x in range(w2):
            c = cx[x]
            if c < 0 or c >= PGW: continue
            k = cy * PGW + c; p = px[(t + y) * w + l + x]
            S[k][0] += p[0]; S[k][1] += p[1]; S[k][2] += p[2]; C[k] += 1
    L, A, B = [], [], []
    for k in range(n):
        rr, gg, bb = (v / C[k] for v in S[k])
        L.append((rr + gg + bb) / 3); A.append(rr - gg); B.append((rr + gg) / 2 - bb)
    mean = sum(L) / n; sd = math.sqrt(sum((v - mean) ** 2 for v in L) / n)
    cl = lambda v: max(-127, min(127, round(v)))
    return bytes((cl(v) & 255) for v in [(x - mean) / (sd + 1) * 40 for x in L] + [a * 0.5 for a in A] + [x * 0.5 for x in B])
def write_pics(root, total):
    D = 3 * PGW * PGH; out = bytearray(); sheets = {}
    for ti in range(total):
        n, k = ti // PER, ti % PER
        if n not in sheets:
            p = os.path.join(root, f"thumbs-s{n:02d}.webp")
            sheets[n] = Image.open(p).convert("RGB") if os.path.exists(p) else None
        im = sheets[n]
        x, y = (k % COLS) * TW, (k // COLS) * TH
        tile = im.crop((x, y, x + TW, y + TH)) if im is not None and y + TH <= im.height else None
        px = list(tile.getdata()) if tile is not None else []
        if not px or max(max(p) for p in px) - min(min(p) for p in px) < 6:
            out += bytes([128]) * D; continue  # no picture in this slot
        t, b, l, r = _trim_box(px, TW, TH); out += _feat(px, TW, t, b, l, r)
    open(os.path.join(root, "pics.bin"), "wb").write(bytes(out))
    print("picture summaries:", total)

def main(root="."):
    singles = [p for p in fetch_products() if p.get("product_type") == "Single"]
    if len(singles) < 1000:  # the store listing looks broken: keep yesterday's data
        sys.exit(f"Only {len(singles)} singles found; not updating.")
    print("singles:", len(singles))

    # --- pictures: one fixed slot per card handle
    idx_path = os.path.join(root, "thumbs-index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else {"items": []}
    fresh_start = not index["items"]
    slots = {h: i for i, (h, _) in enumerate(index["items"])}
    todo = []
    for p in singles:
        src = (p.get("images") or [{}])[0].get("src", "")
        h = p["handle"]
        if h not in slots:
            slots[h] = len(index["items"]); index["items"].append([h, src]); todo.append(slots[h])
        elif index["items"][slots[h]][1] != src:
            index["items"][slots[h]][1] = src; todo.append(slots[h])
    sheets = {}
    def sheet(n):
        if n not in sheets:
            path = os.path.join(root, f"thumbs-s{n:02d}.webp")
            count = min(PER, len(index["items"]) - n * PER); rows = -(-count // COLS)
            base = Image.new("RGB", (COLS * TW, rows * TH), (221, 221, 221))
            if not fresh_start and os.path.exists(path):
                old = Image.open(path).convert("RGB"); base.paste(old, (0, 0))
            sheets[n] = base
        return sheets[n]
    print("pictures to fetch:", len(todo))
    with ThreadPoolExecutor(16) as ex:
        for i, im in zip(todo, ex.map(lambda i: thumb(index["items"][i][1]), todo)):
            s = sheet(i // PER); k = i % PER
            if im is not None:
                s.paste(im, ((k % COLS) * TW, (k // COLS) * TH))
    for n, im in sheets.items():
        im.save(os.path.join(root, f"thumbs-s{n:02d}.webp"), "WEBP", quality=72, method=6)
    json.dump(index, open(idx_path, "w"), separators=(",", ":"))
    if todo or not os.path.exists(os.path.join(root, "pics.bin")):
        write_pics(root, len(index["items"]))

    # --- card list
    groups = {}
    for p in sorted(singles, key=lambda p: p["title"].lower()):
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", p["title"])
        name, set_ = (m.group(1), m.group(2)) if m else (p["title"], "")
        name = name.strip()
        g = groups.setdefault(name, {"t": set(), "b": set(), "p": []})
        g["t"].update(TYPES[t] for t in p["tags"] if t in TYPES)
        g["b"].update(t for t in p["tags"] if t in BRIGADES)
        v = p["variants"][0]
        g["p"].append([set_, p["handle"], round(float(v["price"]), 2), 1 if v.get("available") else 0, slots[p["handle"]],
                       ".".join(sorted(TYPES[t] for t in p["tags"] if t in TYPES))])
    for g in groups.values():
        g["p"].sort(key=lambda x: (x[3] == 0, x[2]))
    cards = [[n, ".".join(sorted(g["t"])), g["p"], "|".join(sorted(g["b"]))] for n, g in sorted(groups.items(), key=lambda kv: kv[0].lower())]
    # ability text per card name, for the "read the ability" backup (loaded only when a name is unclear)
    texts = {}
    for p in sorted(singles, key=lambda p: p["title"].lower()):
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", p["title"])
        name = (m.group(1) if m else p["title"]).strip()
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.get("body_html") or "")).strip()
        lst = texts.setdefault(name, [])
        if t and t not in lst:
            lst.append(t)
    tpath = os.path.join(root, "texts.json")
    old_t = json.load(open(tpath)) if os.path.exists(tpath) else None
    if old_t != texts:
        json.dump(texts, open(tpath, "w"), separators=(",", ":"), ensure_ascii=False)
        print("texts updated")

    out = {"pricesFetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "thumbs": {"total": len(index["items"]), "per": PER, "cols": COLS, "tw": TW, "th": TH}, "cards": cards}
    old = json.load(open(os.path.join(root, "cards.json"))) if os.path.exists(os.path.join(root, "cards.json")) else {}
    if old.get("cards") == json.loads(json.dumps(cards)) and old.get("thumbs") == out["thumbs"] and not todo:
        print("No changes."); return
    json.dump(out, open(os.path.join(root, "cards.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    print("cards:", len(cards))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
