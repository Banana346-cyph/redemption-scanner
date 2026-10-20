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
