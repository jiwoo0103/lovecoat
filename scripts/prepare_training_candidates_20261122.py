"""Prepare review-only data candidates. Never train or replace application assets."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import posixpath
import random
import time
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "model-results/20261122-training-candidates"
BOOK = Path("D:/2026 외투 통합 리스트(~1112).xlsx")
SHEET = "2026 통합 외투리스트(~1112)"
PHOTOS = Path("C:/Users/playj/Desktop/Download")
OLD = ROOT / ".local/robust-four-models-20261102"
GENERIC = ROOT / ".local/category-first-20261027-01/assets/efficientnet_b0_rwightman-7f5810bc.pth"
CLASSES = [str(n) for n in range(60, 171, 10)] + ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL"]
SEED = 20261122
SUITS = {str(n) for n in range(6828, 6840)} | {"7037", "7412"}
DRESSES = {"6616", "7438"}
PREVIEW_PROMPTS = {
    "2XS": 'Use case: text-localization. Edit target: the supplied real clothing dataset photograph. Make one careful synthetic size-label preview. Replace ONLY the black printed text "XS" inside the white face-shaped mannequin head sign with the exact uppercase text "2XS" (three characters: digit 2, capital X, capital S). Match the existing bold plain sans-serif printed lettering, ink black, edge softness, perspective, lighting and central alignment; fit the three characters naturally within the original face area. Preserve the woman-shaped head silhouette, hair bun, sign outline and ears. Preserve the SAME black quilted jacket, every button and seam, mannequin body, stand, fabric background, framing and aspect ratio. Do not add logos, captions, labels, borders or a collage. Change only the characters within the sign; all clothing and all other content must stay unchanged. This is a preview for synthetic training data, not a new fashion photograph.',
    "60": 'Use case: text-localization. Edit the supplied real child clothing photograph for a synthetic training preview. Replace ONLY the black printed "80" in the white face-shaped head sign with exactly "60" (digit six then zero). Match the original black heavy sans-serif typeface, the original numeral height, spacing, alignment, edge softness, lighting and slight perspective. Preserve the boy-shaped silhouette and hair, ears, border, face background, same black child jacket, red inner collar, all snaps and stitching, mannequin and white cloth background, original full image framing and portrait aspect ratio. Do not change the size, fit, color, type, details or shape of the jacket. No added text, labels, collage, decoration or watermark. Only substitute the digits inside the sign.',
    "70": 'Use case: text-localization. Edit target is the supplied real child clothing photograph. Produce a synthetic label preview by replacing only the black printed "80" on the face-shaped child sign with exactly "70". Match the original bold sans-serif type, black color, digit height, baseline, spacing, perspective and edge softness. Keep the original boy head outline and hair, ears, paper texture, same black child jacket, red collar lining, every snap and seam, mannequin stand, background, framing and portrait aspect ratio. Do not invent any clothing detail or resize the jacket. No other added text, watermark, collage or labels. The only intended change is 80 to 70 inside the existing sign.',
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def log(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=False), flush=True)


def workbook():
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    records = []
    with ZipFile(BOOK) as z:
        strings = ["".join(t.text or "" for t in s.findall(".//m:t", ns))
                   for s in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        links = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = ET.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet", ns)
        sheet = next(s for s in sheets if s.get("name") == SHEET)
        target = links[sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        for row in ET.fromstring(z.read(member)).findall("m:sheetData/m:row", ns):
            values = {}
            for cell in row:
                col = "".join(filter(str.isalpha, cell.get("r")))
                if col not in {"B", "C", "D", "E", "G", "H", "I"}:
                    continue
                value = cell.find("m:v", ns)
                text = value.text if value is not None else ""
                if cell.get("t") == "s":
                    text = strings[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(t.text or "" for t in cell.findall(".//m:t", ns))
                if cell.find("m:f", ns) is not None and col in {"E", "H", "I"}:
                    raise ValueError("Formula labels require explicit cached-value verification")
                values[col] = (text or "").strip()
            code = values.get("C", "")
            if int(row.get("r")) < 3 or not code.isdigit():
                continue
            code = code.zfill(4)
            folder = PHOTOS / values["D"]
            records.append({"code": code, "sheet": SHEET, "row": int(row.get("r")),
                            "source": str(folder / "img_0_0_0.jpg"),
                            "other_angles": [str(folder / f"img_0_0_{a}.jpg") for a in (1, 2)],
                            "category": values["E"], "gender": values["G"], "age": values["H"],
                            "size": values["I"], "label_authority": "latest_user_workbook",
                            "capture_bin": int(code) // 500})
    if len(records) != 7329 or len({r["code"] for r in records}) != len(records):
        raise ValueError("Unexpected source snapshot")
    return records


def body_image(image):
    """Selection-only crop; never used as the proposed training preprocessing."""
    w, h = image.size
    small = np.asarray(image.convert("L").resize((216, 384)))
    present = (small[12:240, 65:151] < 130).sum(axis=1) >= 7
    consecutive = np.convolve(present.astype(int), np.ones(4, dtype=int), "valid")
    locations = np.flatnonzero(consecutive == 4)
    head_y = int(locations[0] + 12) if len(locations) else 115
    # Remove the printed sign from diversity features, retain the body below it.
    top = min(.62, (head_y + 48) / 384)
    return image.crop((int(w * .08), int(h * top), int(w * .92), int(h * .96)))


def inspect(row):
    blob = Path(row["source"]).read_bytes()
    with Image.open(io.BytesIO(blob)) as original:
        dimensions = original.size
        original.draft("RGB", (540, 960))
        image = ImageOps.exif_transpose(original).convert("RGB")
        body = body_image(image)
        thumb = ImageOps.pad(body, (224, 224), color="white")
        color = np.asarray(body.resize((48, 64))).astype(np.float32) / 255
        dh = np.asarray(body.convert("L").resize((9, 8)))
        dhash = sum(int(v) << i for i, v in enumerate((dh[:, :-1] > dh[:, 1:]).flatten()))
    return ({**row, "sha256": hashlib.sha256(blob).hexdigest(), "dimensions": dimensions,
             "selection_body_dhash": f"{dhash:016x}", "color_mean": color.mean((0, 1)).tolist()},
            np.asarray(thumb).transpose(2, 0, 1).copy())


def inventory():
    if (OUT / "inventory.json").exists():
        if read(OUT / "input-snapshot.json")["book_sha256"] != digest(BOOK):
            raise ValueError("Input changed since preparation")
        return
    import torch
    from torchvision.models import efficientnet_b0

    rows = workbook()
    OUT.mkdir(parents=True, exist_ok=True)
    save("input-snapshot.json", {"workbook": str(BOOK), "sheet": SHEET, "book_sha256": digest(BOOK),
                               "generic_features_sha256": digest(GENERIC), "seed": SEED,
                               "scope": "data_candidates_and_synthetic_previews_only_no_training"})
    torch.set_num_threads(4)
    model = efficientnet_b0(weights=None)
    model.load_state_dict(torch.load(GENERIC, map_location="cpu", weights_only=True))
    model.classifier = torch.nn.Identity()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval().requires_grad_(False)
    mean = torch.tensor([.485, .456, .406], device=device)[None, :, None, None]
    std = torch.tensor([.229, .224, .225], device=device)[None, :, None, None]
    inventory_rows, embeddings, batch = [], [], []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=6) as pool, torch.inference_mode():
        for meta, image in pool.map(inspect, rows):
            inventory_rows.append(meta)
            batch.append(image)
            if len(batch) == 32 or len(inventory_rows) == len(rows):
                value = torch.from_numpy(np.stack(batch)).to(device).float().div_(255)
                features = model((value - mean) / std).cpu().numpy()
                embeddings.extend(features)
                batch.clear()
            if len(inventory_rows) % 256 == 0:
                log("feature_extraction_inference_only", completed=len(inventory_rows), total=len(rows), device=device)
    features = np.asarray(embeddings, dtype=np.float32)
    features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-9)
    # A fixed projection keeps deterministic diversity selection inexpensive.
    projection = np.random.default_rng(SEED).normal(size=(features.shape[1], 96)).astype(np.float32)
    features = features @ projection
    features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-9)
    np.savez_compressed(OUT / "selection-features.npz", features=features)
    save("inventory.json", inventory_rows)
    log("inventory_complete", count=len(rows), seconds=round(time.monotonic() - start, 1),
        category_counts=dict(Counter(r["category"] for r in rows)))


class Groups:
    def __init__(self, codes):
        self.parent = {c: c for c in codes}

    def root(self, c):
        while self.parent[c] != c:
            self.parent[c] = self.parent[self.parent[c]]
            c = self.parent[c]
        return c

    def join(self, a, b):
        if a in self.parent and b in self.parent:
            a, b = self.root(a), self.root(b)
            self.parent[max(a, b)] = min(a, b)


def annotate_and_group(rows, features):
    prior_inv = {r["code"]: r for r in read(OLD / "inventory.json")}
    annotations = read(OLD / "annotations.json")
    mapping = {n: entry["size_label"] for entry in read(ROOT / "resources/ai/size_mapping.json")["child_correspondence"]
               for n in entry["visible_numbers"]}
    frozen = read(OLD / "frozen-dataset.json")["records"]
    bycode = {r["code"]: r for r in rows}
    uf = Groups(bycode)
    for codes in read(OLD / "deduplication.json")["groups"].values():
        for c in codes[1:]:
            uf.join(codes[0], c)
    for prior in frozen:
        uf.join(prior["code"], prior["group"])
    same_sha = {}
    for r in rows:
        if r["sha256"] in same_sha:
            uf.join(r["code"], same_sha[r["sha256"]])
        same_sha[r["sha256"]] = r["code"]
    # Similarity is conservative split linkage, never proof that two clothes are identical.
    near = []
    values = np.asarray([int(r["selection_body_dhash"], 16) for r in rows], dtype=np.uint64)
    colors = np.asarray([r["color_mean"] for r in rows])
    for i, r in enumerate(rows):
        distances = np.bitwise_count(values[:i] ^ values[i])
        candidates = np.flatnonzero(distances <= 3)
        for j in candidates:
            other = rows[int(j)]
            if (r["age"] == other["age"] and r["size"] == other["size"]
                    and r["category"] == other["category"]
                    and np.linalg.norm(colors[i] - colors[j]) < .025
                    and np.sum((features[i] - features[j]) ** 2) < .02):
                uf.join(r["code"], other["code"])
                near.append({"a": other["code"], "b": r["code"], "dhash_distance": int(distances[j]),
                             "status": "possible_repeat_split_link_only_not_deduplication_truth"})
    exposures = defaultdict(set)
    for old in frozen:
        if old["code"] in bycode:
            exposures[uf.root(old["code"])].add(old["split"])
    fixed = {g: "train" if "train" in splits else "dev" if "dev" in splits else "test"
             for g, splits in exposures.items()}
    # Rare real 60 photographs: 2 fit, 1 dev, 1 test, subject to whole-group constraints.
    for code, split in [("6836", "train"), ("6837", "train"), ("6838", "dev"), ("6839", "test")]:
        group = uf.root(code)
        if group not in fixed:
            fixed[group] = split
    for r in rows:
        old = prior_inv.get(r["code"])
        if old and old["sha256"] != r["sha256"]:
            raise ValueError("Prior annotation no longer matches original photo: " + r["code"])
        raw, authority = None, "untranscribed_child_original_number"
        if r["age"] == "성인":
            raw, authority = r["size"], "latest_user_workbook_adult_literal"
        elif r["code"] in {"6836", "6837", "6838", "6839"}:
            raw, authority = "60", "assistant_visual_review_of_original_20261122"
        elif old and annotations.get(r["code"], {}).get("raw_size"):
            raw = annotations[r["code"]]["raw_size"]
            authority = "prior_visual_transcription_hash_matched"
        if r["age"] == "유아동" and raw is not None and mapping.get(raw) != r["size"]:
            raw, authority = None, "prior_raw_conflicts_with_current_workbook_requires_review"
        if raw is not None and raw not in CLASSES:
            raise ValueError("Unexpected raw size")
        r.update(group="group-" + uf.root(r["code"]), raw_size=raw, raw_size_authority=authority,
                 source_kind="real", fit_eligible=raw is not None)
        if r["category"] == "기타":
            r["reviewed_other_type"] = "우주복·일체형 스키복류" if str(int(r["code"])) in SUITS else (
                "원피스" if str(int(r["code"])) in DRESSES else "앞 전체가 열리지 않는 상의")
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["group"]].append(r)
    rng = random.Random(SEED)
    order = list(grouped)
    rng.shuffle(order)
    # Balance new groups by age and current category without moving historical groups out of fit.
    counts = defaultdict(Counter)
    assignments = {}
    for group in order:
        rr = grouped[group]
        key = (rr[0]["category"], rr[0]["age"])
        split = fixed.get(group.removeprefix("group-"))
        if split is None:
            split = min(("train", "dev", "test"), key=lambda s: counts[key][s] / {
                "train": .70, "dev": .15, "test": .15}[s])
        assignments[group] = split
        counts[key][split] += len(rr)
    for r in rows:
        r["split"] = assignments[r["group"]]
    save("possible-repeat-links.json", near)
    save("inventory-annotated.json", rows)
    return rows


def representative_key(row, task):
    # Repeated clothing with a different printed size remains useful to the size classifier.
    return (row["group"], row["raw_size"]) if task == "size" else row["group"]


def diverse_pick(indices, rows, features, n, task):
    """Farthest-first within underrepresented label/age/capture strata."""
    if not indices or n <= 0:
        return []
    indices = sorted(indices, key=lambda i: rows[i]["code"])
    n = min(n, len(indices))
    selected, used_groups, allocated = [], set(), Counter()
    keys = [(rows[i]["age"], rows[i]["gender"], rows[i]["size"], rows[i]["capture_bin"]) if task == "category"
            else (rows[i]["age"], rows[i]["gender"], rows[i]["category"], rows[i]["capture_bin"]) for i in indices]
    matrix = features[indices]
    distance = np.ones(len(indices), dtype=np.float32)
    remaining = np.ones(len(indices), dtype=bool)
    for _ in range(n):
        candidates = np.flatnonzero(remaining)
        if not len(candidates):
            break
        fairness = np.array([1 / (1 + allocated[keys[j]]) for j in candidates])
        score = (.2 + distance[candidates]) * fairness
        j = int(candidates[int(np.argmax(score))])
        i = indices[j]
        selected.append(i)
        allocated[keys[j]] += 1
        used_groups.add(representative_key(rows[i], task))
        remaining[j] = False
        # One representative per known/conservative split group for each task.
        for k in candidates:
            if representative_key(rows[indices[k]], task) in used_groups:
                remaining[k] = False
        d = np.sum((matrix - matrix[j]) ** 2, axis=1)
        distance = np.minimum(distance, d)
    return selected


def select(rows, features, task, split, target):
    eligible = [i for i, r in enumerate(rows) if r["split"] == split and r["code"] != "1495"
                and (task == "category" or r["fit_eligible"])]
    field = "category" if task == "category" else "raw_size"
    labels = ["자켓", "코트", "조끼", "기타"] if task == "category" else CLASSES
    pools = {label: [i for i in eligible if rows[i][field] == label] for label in labels}
    capacities = {label: len({representative_key(rows[i], task) for i in pool}) for label, pool in pools.items()}
    quotas = {label: 0 for label in labels}
    # Water-fill guarantees rare categories/sizes are not lost among common M/L photographs.
    for _ in range(min(target, sum(capacities.values()))):
        available = [label for label in labels if quotas[label] < capacities[label]]
        label = min(available, key=lambda c: quotas[c])
        quotas[label] += 1
    chosen = []
    for label in labels:
        chosen.extend(diverse_pick(pools[label], rows, features, quotas[label], task))
    # A conservative group can contain several labels. Remove any duplicate group after selection.
    result, groups = [], set()
    for i in chosen:
        key = representative_key(rows[i], task)
        if key not in groups:
            result.append({**rows[i], "task": task, "selection_status": "candidate_not_frozen_or_trained"})
            groups.add(key)
    if len(result) < target:
        remaining = [i for i in eligible if representative_key(rows[i], task) not in groups]
        for i in diverse_pick(remaining, rows, features, target - len(result), task):
            result.append({**rows[i], "task": task, "selection_status": "candidate_not_frozen_or_trained"})
    return sorted(result, key=lambda r: int(r["code"]))


def export_csv(name, rows):
    fields = ["code", "split", "category", "age", "gender", "size", "raw_size", "raw_size_authority",
              "group", "source_kind", "source", "sha256", "sheet", "row", "selection_status"]
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(category, size, summary):
    import base64

    cards = []
    # Review all rare/other rows and representative visual spread, not thousands of duplicate thumbnails.
    sample = {r["code"]: r for r in category if r["category"] == "기타"}
    for split in ("train", "dev", "test"):
        for c in ("자켓", "코트", "조끼"):
            pool = [r for r in category if r["split"] == split and r["category"] == c]
            for r in pool[::max(1, len(pool) // 12)][:12]:
                sample[r["code"]] = r
    for r in size:
        if r["raw_size"] in ("60", "70", "170", "3XL"):
            sample[r["code"]] = r
    for r in sorted(sample.values(), key=lambda r: int(r["code"])):
        with Image.open(r["source"]) as image:
            image.draft("RGB", (270, 480))
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((210, 374))
            b = io.BytesIO()
            image.save(b, format="JPEG", quality=76)
        encoded = base64.b64encode(b.getvalue()).decode()
        caption = f'{r["code"]} · {r["category"]} · {r["age"]} · {r["size"]} · {r["split"]}'
        cards.append(f'<figure data-text="{html.escape(caption, quote=True)}"><a href="{Path(r["source"]).as_uri()}">'
                     f'<img loading="lazy" src="data:image/jpeg;base64,{encoded}"></a>'
                     f'<figcaption>{html.escape(caption)}</figcaption></figure>')
    totals = html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>재학습 데이터 후보</title>'
    page += '<style>body{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:30px;background:#f5f6f8;color:#172235}'
    page += 'h1{font-size:27px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}'
    page += 'figure{margin:0;background:white;padding:10px;border-radius:8px}img{width:100%;height:360px;object-fit:contain}'
    page += 'figcaption{font-size:14px;line-height:1.7}input{padding:12px;width:min(350px,90%);margin:18px 0}pre{white-space:pre-wrap}'
    page += 'table{border-collapse:collapse;background:white;margin:18px 0;min-width:420px}td,th{padding:10px 18px;border:1px solid #d8dde5;text-align:right}'
    page += 'td:first-child,th:first-child{text-align:left}details{margin:24px 0}a{color:#1553a0}</style>'
    page += '<h1>재학습 데이터 후보 · 2026-11-22</h1><p>데이터 후보와 합성 시안 준비 단계입니다. 학습·앱 모델 교체는 하지 않았습니다.</p>'
    page += '<p>최신 사용자 엑셀의 분류를 유지했습니다. 아래는 기타 전체와 대표 사진입니다. 전체 목록은 CSV/JSON에 있습니다.</p>'
    page += '<p>사진을 누르면 원본을 엽니다. train=학습 후보, dev=개발 검증 후보, test=최종 평가 후보. 아직 최종 동결하지 않았습니다.</p>'
    page += '<h2>후보 수량</h2><table><tr><th>모델</th><th>학습</th><th>개발 검증</th><th>최종 평가</th></tr>'
    for task, title in [("category", "카테고리"), ("size", "사이즈 실사")]:
        page += '<tr><td>' + title + '</td>' + ''.join(f'<td>{summary[task][s]["count"]:,}</td>' for s in ("train", "dev", "test")) + '</tr>'
    page += '</table><p><a href="category-candidates.csv" download>카테고리 전체 목록 CSV</a> · '
    page += '<a href="size-candidates.csv" download>사이즈 전체 목록 CSV</a></p>'
    page += '<p>카테고리는 범용 이미지 특징과 성인/아동·사이즈·촬영 구간을 이용해 시각적 다양성을 고른 후보입니다. 재질이나 옷 종류의 상세 정답을 새로 확정한 것은 아닙니다.</p>'
    page += '<p>아동 60 실사 4벌과 70 실사 7벌을 확보했습니다. 성인 문자 2XS는 실사가 없어 합성 시안만 만들었습니다. '
    page += '170 실사는 학습용 1벌뿐이며 독립 평가 사진이 없습니다. 사진이 적은 종류의 정확도를 확정할 수 없습니다.</p>'
    for task, title, labels in [("category", "카테고리별 분포", ["자켓", "코트", "조끼", "기타"]),
                                ("size", "사이즈 원문별 실사 분포", CLASSES)]:
        page += '<details' + (' open' if task == 'category' else '') + '><summary>' + title + '</summary>'
        page += '<table><tr><th>종류</th><th>학습</th><th>개발 검증</th><th>최종 평가</th></tr>'
        for label in labels:
            page += '<tr><td>' + label + '</td>' + ''.join(f'<td>{summary[task][s]["labels"].get(label, 0):,}</td>' for s in ("train", "dev", "test")) + '</tr>'
        page += '</table></details>'
    if (OUT / "synthetic-previews/comparison.jpg").exists():
        page += '<h2>사이즈 합성 시안</h2><p>왼쪽은 원본, 오른쪽은 합성 시안입니다. 대량 합성이나 학습 투입 전 검토용이며 실사 평가 정답으로 쓰지 않습니다.</p>'
        page += '<p>문자만 바뀐 듯 보여도 생성 과정에서 해상도와 다른 픽셀이 달라져, 현재 시안은 학습에 자동으로 넣지 않습니다.</p>'
        page += '<p><a href="synthetic-previews/comparison.jpg"><img style="width:min(100%,1000px);height:1000px;object-fit:contain" src="synthetic-previews/comparison.jpg"></a></p>'
        page += '<p><a href="synthetic-previews/manifest.json">합성 원본·출처·프롬프트·검토 기록</a></p>'
    page += '<input id="q" placeholder="코드·분류·성인/유아동·train/dev/test 검색"><div class="grid">' + ''.join(cards) + '</div>'
    page += '<details><summary>상세 선별 기록과 검증 한계</summary><pre>' + totals + '</pre></details><script>'
    page += 'document.querySelector("#q").addEventListener("input",e=>{for(const f of document.querySelectorAll("figure"))'
    page += 'f.hidden=!f.dataset.text.toLowerCase().includes(e.target.value.toLowerCase())})</script></html>'
    (OUT / "시작하기.html").write_text(page, encoding="utf-8")


def prepare():
    inventory()
    features = np.load(OUT / "selection-features.npz")["features"]
    rows = annotate_and_group(read(OUT / "inventory.json"), features)
    category, size = [], []
    for split, n in [("train", 3000), ("dev", 500), ("test", 500)]:
        category.extend(select(rows, features, "category", split, n))
    for split, n in [("train", 2000), ("dev", 300), ("test", 300)]:
        size.extend(select(rows, features, "size", split, n))
    for task, selected in [("category", category), ("size", size)]:
        for key in ("group", "sha256", "code"):
            assigned = defaultdict(set)
            for r in selected:
                assigned[r[key]].add(r["split"])
            if any(len(s) != 1 for s in assigned.values()):
                raise AssertionError("Cross-split leakage: " + task + "/" + key)
        if any(r["source_kind"] != "real" for r in selected):
            raise AssertionError("Synthetic previews must not enter real candidate lists")
        export_csv(task + "-candidates.csv", selected)
        save(task + "-candidates.json", selected)
    summary = {"status": "review_only_not_training_ready", "trained": False, "production_changed": False,
               "category": {}, "size": {}, "target_size_classes": CLASSES,
               "size_missing_real_classes": [c for c in CLASSES if not any(r["raw_size"] == c for r in rows)],
               "child_raw_transcription_pending": sum(r["raw_size"] is None for r in rows),
               "selection_features": "generic_ImageNet_EfficientNet_B0_body_embeddings_plus_strata_not_material_truth",
               "augmentation_proposal": {"each_train_original_per_epoch": 1, "random_augmented_per_original": [1, 2],
                                         "kinds": ["dark", "bright", "contrast", "warm", "cool", "shadow_left", "shadow_right", "blur"],
                                         "originals_unchanged": True, "generate_only_after_group_split": True},
               "limitations": ["후보 선별 단계이며 7,329벌 전체의 새 개별 육안 라벨을 만든 것은 아닙니다.",
                               "Feature diversity is not a verified taxonomy of fabric or garment subtype.",
                               "Conservative near-repeat groups are not proof of identical garments; review links before freezing.",
                               "Retrospective validation; previously inspected photographs are not a new blind test.",
                               "60 has four observed real photographs; raw 170 may have no independent evaluation group.",
                               "Adult 2XS real examples have not been confirmed in the latest workbook.",
                               "Synthetic previews are quarantined, carry parent identity, and are never evaluation ground truth."]}
    for task, selected in [("category", category), ("size", size)]:
        field = "category" if task == "category" else "raw_size"
        for split in ("train", "dev", "test"):
            rr = [r for r in selected if r["split"] == split]
            summary[task][split] = {"count": len(rr), "labels": dict(Counter(r[field] for r in rr)),
                                    "ages": dict(Counter(r["age"] for r in rr)),
                                    "capture_bins": dict(Counter(r["capture_bin"] for r in rr))}
    if read(OUT / "input-snapshot.json")["book_sha256"] != digest(BOOK):
        raise AssertionError("Source changed during preparation")
    save("summary.json", summary)
    save("verification.json", {"unique_source_codes": len(rows), "source_workbook_unchanged": True,
                               "candidate_hash_group_code_cross_split_overlap": 0,
                               "synthetic_in_real_candidates": 0, "optimizer_or_training_used": False,
                               "candidate_counts": {"category": len(category), "size": len(size)}})
    report(category, size, summary)
    log("candidates_complete", category=summary["category"], size=summary["size"])


def preview_review():
    """Document generated previews; no automatic promotion to training data."""
    from PIL import ImageDraw, ImageFont

    rows = {r["code"]: r for r in read(OUT / "inventory-annotated.json")}
    cases = [("60", "1186", "child-60-from-1186.png"),
             ("70", "1186", "child-70-from-1186.png"),
             ("2XS", "0409", "adult-2XS-from-0409.png")]
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 23)
    canvas = Image.new("RGB", (1080, 3 * 720), "#eeeeee")
    draw = ImageDraw.Draw(canvas)
    notes = []
    for idx, (label, code, name) in enumerate(cases):
        row = rows[code]
        if row["split"] != "train":
            raise ValueError("Preview parent is not in the training partition")
        path = OUT / "synthetic-previews" / name
        with Image.open(row["source"]) as original, Image.open(path) as generated:
            a, b = ImageOps.exif_transpose(original).convert("RGB"), generated.convert("RGB")
            aa = a.resize((216, 384))
            bb = b.resize((216, 384))
            delta = np.abs(np.asarray(aa).astype(float) - np.asarray(bb).astype(float))
            # Whole-frame changes are recorded honestly. This is not a pixel-exact text replacement.
            mae = float(delta.mean())
            original_dimensions, generated_dimensions = a.size, b.size
            for col, image in enumerate((a, b)):
                thumb = ImageOps.contain(image, (520, 670))
                x = col * 540 + (540 - thumb.width) // 2
                y = idx * 720 + 45
                canvas.paste(thumb, (x, y))
            draw.text((12, idx * 720 + 10), f"Original {code}: {row['raw_size']}", font=font, fill="black")
            draw.text((552, idx * 720 + 10), f"Synthetic preview: {label}", font=font, fill="black")
        notes.append({"raw_size": label, "source_kind": "synthetic_preview", "parent_code": code,
                      "parent_group": row["group"], "parent_split": row["split"],
                      "parent_source": row["source"], "parent_sha256": row["sha256"],
                      "output": str(path), "sha256": digest(path), "original_dimensions": original_dimensions,
                      "generated_dimensions": generated_dimensions, "resized_whole_frame_mae_0_255": round(mae, 3),
                      "text_visually_checked": True, "pixel_exact_outside_text": False,
                      "training_approved": False, "evaluation_eligible": False,
                      "generator": "built_in_image_gen",
                      "final_prompt": PREVIEW_PROMPTS[label],
                      "review_note": "문자 판독과 전체 외형은 확인했지만 원본과 해상도·픽셀이 달라 현재는 시안 전용입니다."})
    canvas.save(OUT / "synthetic-previews/comparison.jpg", quality=92)
    save("synthetic-previews/manifest.json", {"status": "preview_only_not_training_approved", "records": notes,
                                            "all_synthetic_inherit_parent_train_group": True,
                                            "next_stage_constraint": "학습용 제작 시 문자 영역 외 원본 보존을 검증하고 실사 검증·평가에 합성본을 넣지 않습니다."})
    report(read(OUT / "category-candidates.json"), read(OUT / "size-candidates.json"), read(OUT / "summary.json"))
    log("synthetic_previews_reviewed", count=len(notes))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--review-previews", action="store_true")
    args = parser.parse_args()
    if args.review_previews:
        preview_review()
    elif args.inventory_only:
        inventory()
    else:
        prepare()
