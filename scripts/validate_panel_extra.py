"""Fresh, visually labelled holdout for the already frozen panel pipeline."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import random
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coatdesk_ab.data import SIZES, load_photo, read, save, sha
from coatdesk_ab.locator import WholePanelLocator
from coatdesk_locator.labels import face_refinement
from scripts.extra_validation import original_body

WORK = Path(".local/panel-extra-20261025")
OUTPUT = Path("model-results/20261025-panel-extra-validation")
SOURCE = Path(r"C:\Users\playj\Desktop\Download")
PREVIOUS = Path(".local/ab-work-20261024-r3")
DETECTOR = Path("model-results/20261025-panel-detector/detector.pt")
CLASSIFIERS = Path("model-results/20261024-four-model-comparison/models")
PAGE_SIZE = 36


def frozen_files():
    paths = [DETECTOR, *(CLASSIFIERS / f"panel-{t}.pt" for t in ("shape", "size"))]
    paths += sorted(Path("coatdesk_locator").glob("*.py"))
    paths += sorted(Path("coatdesk_ab").glob("*.py"))
    paths += [PREVIOUS / "prepared.json", Path("model-results/20261024-extra-validation/labels.jsonl")]
    return {str(p): sha(p) for p in paths}


def check_frozen(work):
    if frozen_files() != read(work / "frozen-files.json"):
        raise ValueError("Existing model, code or labels changed")


def hash_folder(code):
    return [{"code": code, "source": str(p), "sha256": sha(p)}
            for p in sorted((SOURCE / f"0_{code}").glob("img*.jpg"))]


def select(work):
    work.mkdir(parents=True, exist_ok=False)
    save(work / "frozen-files.json", frozen_files())
    old = read(PREVIOUS / "prepared.json")["records"]
    used = {r["code"] for r in old}
    inventory = {r["code"]: r for r in read(Path(".local/size-dataset-work/inventory.json"))}
    labels = read(Path(".local/size-dataset-work/reviewed_labels.json"))
    previously_ambiguous = {r["code"] for r in read(Path(".local/extra-validation-20261024/visual_exclusions.json"))["pairs"]}
    fronts = {p.parent.name[2:]: p for p in SOURCE.glob("0_*/img_0_0_0.jpg")}
    fresh = sorted((c for c in fronts if int(c) > 1108 and c not in used), key=int)
    old_hash = [(c, int(inventory[c]["body_dhash"], 16)) for c in sorted(used)]
    nearest, eligible, pools = {}, [], defaultdict(list)
    for code in fresh:
        if code in previously_ambiguous or code not in labels:
            continue
        value = int(inventory[code]["body_dhash"], 16)
        nearest[code] = min(((value ^ h).bit_count(), c) for c, h in old_hash)
        if nearest[code][0] > 6:
            eligible.append(code)
            pools[labels[code][1]].append(code)
    rng = random.Random(20261025)
    for pool in pools.values():
        rng.shuffle(pool)
    chosen, chosen_hashes = [], []
    for size in sorted(pools, key=lambda s: (len(pools[s]), s)):
        count = 0
        for code in pools[size]:
            value = int(inventory[code]["body_dhash"], 16)
            if any((value ^ h).bit_count() <= 6 for h in chosen_hashes):
                continue
            chosen.append(code)
            chosen_hashes.append(value)
            count += 1
            if count >= 160:
                break
    with ThreadPoolExecutor(max_workers=6) as pool:
        old_photos = [r for group in pool.map(hash_folder, sorted(used)) for r in group]
        proposed = list(pool.map(hash_folder, sorted(chosen)))
    old_photo_hashes = {r["sha256"] for r in old_photos}
    seen, exact_excluded, photos, kept = set(), [], [], []
    for group in proposed:
        code = group[0]["code"]
        hashes = {r["sha256"] for r in group}
        if hashes & (seen | old_photo_hashes):
            exact_excluded.append(code)
            continue
        if sha(fronts[code]) != inventory[code]["sha256"]:
            raise ValueError(f"Original front changed: {code}")
        kept.append(code)
        photos.extend(group)
        seen.update(hashes)
    save(work / "selection.json", {"live_fronts": len(fronts), "unused_after_early_range": len(fresh),
         "candidate_sizes": dict(Counter('/'.join(labels[c]) for c in fresh if c in labels)),
         "early_range_excluded": "0001-1108", "used_codes": sorted(used),
         "previously_ambiguous_codes": sorted(previously_ambiguous), "dhash_minimum_exclusive": 6,
         "eligible_count": len(eligible), "maximum_per_size": 160, "seed": 20261025,
         "selected_codes": kept, "exact_excluded": exact_excluded,
         "source_photos": photos, "previous_source_photos": old_photos,
         "selection_uses_current_model_predictions": False})
    all_codes = sorted(used | set(kept))
    (work / "body").mkdir()

    def body(code):
        image = original_body(fronts[code])
        feature = np.asarray(image.resize((32, 40)), dtype=np.float32).ravel() / 255
        image.thumbnail((140, 200))
        image.save(work / "body" / f"{code}.jpg", quality=93)
        return feature

    with ThreadPoolExecutor(max_workers=5) as pool:
        features = np.array(list(pool.map(body, all_codes)))
    index = {c: i for i, c in enumerate(all_codes)}
    old_codes = sorted(used)
    before = features[[index[c] for c in old_codes]]
    after = features[[index[c] for c in kept]]

    def mse(a, b):
        return np.maximum(0, np.sum(a*a, 1)[:, None] + np.sum(b*b, 1)[None, :] - 2*a@b.T) / a.shape[1]

    old_dist, new_dist = mse(after, before), mse(after, after)
    np.fill_diagonal(new_dist, np.inf)
    audit = []
    for i, code in enumerate(kept):
        oi, ni = int(old_dist[i].argmin()), int(new_dist[i].argmin())
        audit.append({"code": code, "old": old_codes[oi], "peer": kept[ni],
                      "old_rmse": float(np.sqrt(old_dist[i, oi])), "peer_rmse": float(np.sqrt(new_dist[i, ni])),
                      "nearest_old_dhash": nearest[code]})
    save(work / "duplicate-audit.json", audit)
    (work / "audit-pages").mkdir()
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 18)
    for page, start in enumerate(range(0, len(audit), 18)):
        group = audit[start:start+18]
        canvas = Image.new("RGB", (1260, math.ceil(len(group)/3)*231), "#e6e9ed")
        draw = ImageDraw.Draw(canvas)
        for i, item in enumerate(group):
            bx, by = i % 3*420, i//3*231
            for k, (name, code) in enumerate((("NEW", item["code"]), ("OLD", item["old"]), ("PEER", item["peer"]))):
                x = bx+k*140
                draw.text((x+2, by+2), f"{name} {code}", fill="black", font=font)
                with Image.open(work / "body" / f"{code}.jpg") as image:
                    canvas.paste(image, (x+(140-image.width)//2, by+27))
        canvas.save(work / "audit-pages" / f"page-{page:02}.jpg", quality=95)
    print(json.dumps({"selected": len(kept), "sizes": dict(Counter(labels[c][1] for c in kept)),
                      "duplicate_pages": math.ceil(len(kept)/18), "exact_excluded": exact_excluded}), flush=True)


def render_labels(work, rows):
    folder = work / "label-pages"
    folder.mkdir()
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 19)
    for page, start in enumerate(range(0, len(rows), PAGE_SIZE)):
        canvas = Image.new("RGB", (1440, math.ceil(len(rows[start:start+PAGE_SIZE])/6)*260), "#edf2f6")
        draw = ImageDraw.Draw(canvas)
        for j, row in enumerate(rows[start:start+PAGE_SIZE]):
            photo = load_photo(row["source"])
            x0, y0, x1, y1 = row["box"]
            bw, bh = x1-x0, y1-y0
            view = (max(0, round(x0-.5*bw)), max(0, round(y0-.4*bh)),
                    min(photo.width, round(x1+.5*bw)), min(photo.height, round(y1+.35*bh)))
            im = photo.crop(view)
            ImageDraw.Draw(im).rectangle((x0-view[0], y0-view[1], x1-view[0]-1, y1-view[1]-1), outline="#e00025", width=3)
            im.thumbnail((234, 230))
            x, y = j%6*240, j//6*260
            draw.text((x+5,y+3),row["code"], font=font, fill="black")
            canvas.paste(im,(x+(240-im.width)//2,y+28))
        canvas.save(folder / f"page-{page:02}.jpg", quality=96)


def draft(work):
    check_frozen(work)
    selected = read(work / "selection.json")
    if not (work / "audit-review.json").exists():
        raise ValueError("Review duplicate audit first")
    reviewed = read(work / "audit-review.json")
    if set(reviewed["pages"]) != set(range(math.ceil(len(selected["selected_codes"])/18))):
        raise ValueError("Every duplicate page must be reviewed")
    excluded = set(reviewed["excluded"])
    labels = read(Path(".local/size-dataset-work/reviewed_labels.json"))
    locator = WholePanelLocator(PREVIOUS)
    rows = []
    for code in selected["selected_codes"]:
        if code in excluded:
            continue
        source = SOURCE / f"0_{code}" / "img_0_0_0.jpg"
        photo = load_photo(source)
        rough = locator.locate(photo)
        if rough["box"] is None:
            rough["box"] = [400, 300, 680, 700]
        box, face = face_refinement(photo, rough["box"])
        rows.append({"code": code, "source": str(source), "sha256": sha(source),
                     "box": box, "draft_box": list(box), "template_box": rough["box"], "face_box": face,
                     "coordinate_size": list(photo.size), "prior_visual_inventory": labels[code],
                     "role": "fresh_holdout", "review_status": "pending"})
        if len(rows)%50 == 0:
            print(json.dumps({"drafted":len(rows)}), flush=True)
    save(work / "draft-labels.json", rows)
    render_labels(work, rows)
    print(json.dumps({"drafted":len(rows),"blind_pages":math.ceil(len(rows)/PAGE_SIZE),
                      "current_models_used":False}),flush=True)


def reviewed_records(rows, review, used):
    """Require exhaustive independent readings before admitting a holdout."""
    if review.get("predictions_seen") is not False:
        raise ValueError("Labels must be read before current model predictions")
    expected_pages = set(range(math.ceil(len(rows)/PAGE_SIZE)))
    if {int(k) for k in review["pages"]} != expected_pages:
        raise ValueError("Every blind label page must be read")
    if len({r["code"] for r in rows}) != len(rows) or {r["code"] for r in rows} & set(used):
        raise ValueError("Duplicate or previously used holdout code")
    if any(int(r["code"]) <= 1108 for r in rows):
        raise ValueError("Early repeated-clothing range is forbidden")
    corrections = review.get("boxes", {})
    if set(corrections) - {r["code"] for r in rows}:
        raise ValueError("Box correction outside holdout")
    output = []
    for page in sorted(expected_pages):
        group = rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        tokens = review["pages"][str(page)].split()
        if len(group) != len(tokens):
            raise ValueError(f"Page {page}: {len(tokens)} labels for {len(group)} photos")
        for row, token in zip(group, tokens, strict=True):
            if token[0] not in "mfc" or token[1:] not in SIZES:
                raise ValueError(f"Unsupported label: {token}")
            shape = {"m":"adult_male", "f":"adult_female", "c":"child"}[token[0]]
            age = "child" if token[0] == "c" else "adult"
            if [age, token[1:]] != row["prior_visual_inventory"]:
                raise ValueError(f"Reading disagrees with earlier visual inventory: {row['code']}")
            box = corrections.get(row["code"], row["box"])
            width, height = row["coordinate_size"]
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError(f"Invalid box: {row['code']}")
            dx, dy = (x1-x0)*.02, (y1-y0)*.02
            box = [max(0,math.floor(x0-dx)),max(0,math.floor(y0-dy)),
                   min(width,math.ceil(x1+dx)),min(height,math.ceil(y1+dy))]
            output.append({**row, "box":box,"reviewed_box_before_margin":corrections.get(row["code"], row["box"]),
                           "shape":shape,"size":token[1:],"raw_text":token[1:],"age":age,
                           "gender":{"m":"male","f":"female","c":"provisional_male"}[token[0]],
                           "child_gender_provisional":token[0]=="c", "review_status":"codex_visually_reviewed",
                           "independent_human_review":False,"use_for_training":False,
                           "label_basis":"Visual placard text and user-confirmed male/female reference silhouettes"})
    return output


def seal(work, output):
    check_frozen(work)
    selection = read(work / "selection.json")
    review = read(work / "label-review.json")
    rows = reviewed_records(read(work / "draft-labels.json"), review, selection["used_codes"])
    output.mkdir(parents=True,exist_ok=False)
    for row in rows:
        source = Path(row["source"])
        if sha(source) != row["sha256"]:
            raise ValueError(f"Source changed: {row['code']}")
        target = Path("photos") / f"0_{row['code']}" / source.name
        (output / target).parent.mkdir(parents=True)
        shutil.copyfile(source, output / target)
        if sha(output / target) != row["sha256"]:
            raise ValueError("Copy checksum mismatch")
        row["front_path"] = target.as_posix()
    with (output / "labels.jsonl").open("x",encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row,ensure_ascii=False)+"\n")
    write_csv(output / "labels.csv", rows, ["code","age","gender","shape","size","role","source","front_path","sha256"])
    for name in ("selection.json","duplicate-audit.json","audit-review.json","label-review.json","frozen-files.json","draft-labels.json"):
        shutil.copyfile(work/name, output/name)
    for folder in ("audit-pages","label-pages"):
        shutil.copytree(work/folder,output/folder)
    for name in ("box-corrections-grid.jpg","manual-boxes-reviewed.jpg","manual-boxes-final.jpg"):
        shutil.copyfile(work/name,output/name)
    protocol = {"version":"20261025-fresh-panel-holdout-v1", "new_training_performed":False,
                "ground_truth_sealed_before_predictions":True,"labels_sha256":sha(output/"labels.jsonl"),
                "code_sha256":sha(Path(__file__)),"frozen_files":frozen_files(),"count":len(rows),
                "sizes":dict(Counter(r["size"] for r in rows)),"shapes":dict(Counter(r["shape"] for r in rows)),
                "page_hashes":{str(p.relative_to(output)):sha(p) for folder in ("label-pages","audit-pages") for p in (output/folder).glob("*.jpg")},
                "review_hashes":{name:sha(output/name) for name in ("audit-review.json","label-review.json")},
                "annotation_boundary_margin":.02,"current_models_used_for_ground_truth":False,
                "draft_method":"Retired template code + light face/dark outline; all boxes, shapes, sizes visually read before evaluation",
                "independent_human_review":False,"new_shooting_day":False,
                "limitations":"Same original photo collection; no fresh child, XS, 2XL or 3XL coverage. Conservative clothing duplicate screening cannot guarantee physical garment identity."}
    protocol["page_hashes"].update({p.name:sha(p) for p in output.glob("manual-boxes-*.jpg")})
    save(output/"protocol.json",protocol)
    print(json.dumps({"sealed":len(rows),"sizes":protocol["sizes"],"shapes":protocol["shapes"],"label_sha256":protocol["labels_sha256"]}),flush=True)


def write_csv(path, rows, fields):
    with path.open("x",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def check_protocol(output):
    protocol = read(output/"protocol.json")
    if protocol["frozen_files"] != frozen_files() or protocol["labels_sha256"] != sha(output/"labels.jsonl"):
        raise ValueError("Frozen models or holdout labels changed")
    if protocol["code_sha256"] != sha(Path(__file__)):
        raise ValueError("Evaluation code changed after labels were sealed")
    if not protocol["ground_truth_sealed_before_predictions"]:
        raise ValueError("Ground truth must precede predictions")
    return protocol


def metrics_for(rows):
    n = len(rows)
    return {"count":n,"located":sum(r["location"]["valid"] for r in rows),
            "iou_at_50":sum(r["iou"]>=.5 for r in rows),"iou_at_75":sum(r["iou"]>=.75 for r in rows),
            "mean_iou":sum(r["iou"] for r in rows)/n,
            "crop_covers_reference_99":sum(r["coverage"]>=.99 for r in rows),
            "shape_correct":sum(r["shape_correct"] for r in rows),
            "size_correct":sum(r["size_correct"] for r in rows),
            "both_correct":sum(r["shape_correct"] and r["size_correct"] for r in rows),
            "decode_seconds":sum(r["timing"]["decode"] for r in rows),
            "locator_seconds":sum(r["timing"]["locate"] for r in rows),
            "classification_seconds":sum(r["timing"]["classify"] for r in rows),
            "pipeline_seconds":sum(sum(r["timing"].values()) for r in rows)}


def evaluate(work, output):
    protocol = check_protocol(output)
    if (output/"predictions.json").exists():
        raise FileExistsError("Evaluation already exists; do not overwrite")
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    import torch
    from coatdesk_locator.model import overlap
    from coatdesk_locator.pipeline import FrozenPanelClassifiers, PlacardLocator
    torch.set_num_threads(4)
    if torch.cuda.is_available():
        raise ValueError("CPU validation must disable GPU")
    rows = [json.loads(line) for line in (output/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    t0 = time.perf_counter()
    locator = PlacardLocator(DETECTOR,"cpu")
    classifiers = FrozenPanelClassifiers(CLASSIFIERS,"cpu")
    loading = time.perf_counter()-t0
    warm = load_photo(rows[0]["source"])
    found = locator.locate(warm)
    if found["valid"]:
        classifiers.predict(warm.crop(found["crop_box"]))
    (output/"crops").mkdir()
    (output/"original-thumbnails").mkdir()
    predictions = []
    started = time.perf_counter()
    for row in rows:
        if sha(row["source"]) != row["sha256"]:
            raise ValueError("Original changed before inference")
        t0 = time.perf_counter()
        photo = load_photo(row["source"])
        t1 = time.perf_counter()
        found = locator.locate(photo)
        t2 = time.perf_counter()
        predicted = {"shape":None,"size":None,"shape_scores":{},"size_scores":{}}
        if found["valid"]:
            crop = photo.crop(found["crop_box"])
            predicted = classifiers.predict(crop)
        t3 = time.perf_counter()
        iou, _ = overlap(found["box"],row["box"]) if found["valid"] else (0.,0.)
        _, coverage = overlap(found["crop_box"],row["box"]) if found["valid"] else (0.,0.)
        result = {"code":row["code"],"true_shape":row["shape"],"true_size":row["size"],
                  "reference_box":row["box"],"location":found,"iou":iou,"coverage":coverage,
                  **predicted,"shape_correct":predicted["shape"]==row["shape"],"size_correct":predicted["size"]==row["size"],
                  "timing":{"decode":t1-t0,"locate":t2-t1,"classify":t3-t2}}
        predictions.append(result)
        if found["valid"]:
            crop.save(output/"crops"/f"{row['code']}.png")
            ImageDraw.Draw(photo).rectangle(found["crop_box"],outline="#098ee5",width=5)
        photo.thumbnail((270,480))
        photo.save(output/"original-thumbnails"/f"{row['code']}.jpg",quality=92)
        if len(predictions)%25==0:
            print(json.dumps({"evaluated":len(predictions),"shape_errors":sum(not p["shape_correct"] for p in predictions),
                              "size_errors":sum(not p["size_correct"] for p in predictions)}),flush=True)
    results = {"all":metrics_for(predictions),"by_size":{},"by_shape":{},
               "cpu":{"gpu_disabled":True,"torch_threads":torch.get_num_threads(),"model_load_seconds":loading,
                      "wall_seconds_with_checks_and_exports":time.perf_counter()-started},
               "protocol_sha256":sha(output/"protocol.json"),"labels_sha256":protocol["labels_sha256"]}
    for field in ("size","shape"):
        for value in sorted({r["true_"+field] for r in predictions}):
            results["by_"+field][value]=metrics_for([r for r in predictions if r["true_"+field]==value])
    check_protocol(output)
    save(output/"predictions.json",predictions)
    save(output/"results.json",results)
    write_csv(output/"predictions.csv",predictions,["code","true_shape","shape","shape_correct","true_size","size","size_correct","iou","coverage"])
    print(json.dumps(results["all"]),flush=True)


def report(output):
    check_protocol(output)
    results, predictions = read(output/"results.json"),read(output/"predictions.json")
    protocol = read(output/"protocol.json")
    m = results["all"]
    names = {"adult_male":"성인 남성","adult_female":"성인 여성","child":"아동",None:"보류"}
    summary = (f'새 검증 {m["count"]}벌 · 남녀 {m["shape_correct"]}/{m["count"]} · '
               f'사이즈 {m["size_correct"]}/{m["count"]} · 둘 다 {m["both_correct"]}/{m["count"]}')
    cards = []
    for p in sorted(predictions,key=lambda r:(r["shape_correct"] and r["size_correct"],int(r["code"]))):
        ok = p["shape_correct"] and p["size_correct"]
        scores = " / ".join(f'{names[k]} {v:.2%}' for k,v in p["shape_scores"].items())
        images = f'<img loading="lazy" src="original-thumbnails/{p["code"]}.jpg" alt="원본과 파란 AI 추출 영역">'
        if p["location"]["valid"]:
            images += f'<img loading="lazy" src="crops/{p["code"]}.png" alt="실제 AI 입력 판넬">'
        cards.append(f'<article class="{"ok" if ok else "error"}" data-code="{p["code"]}"><h2>{p["code"]} · {"정답" if ok else "오답/보류"}</h2>'
                     f'<p>정답: {names[p["true_shape"]]} / {p["true_size"]}<br>예측: {names[p["shape"]]} / {p["size"]}</p>'
                     f'<div class="images">{images}</div><p>{html.escape(scores)}<br>박스 IoU {p["iou"]:.3f} · 기준 박스 포함 {p["coverage"]:.2%}</p>'
                     f'<a href="photos/0_{p["code"]}/img_0_0_0.jpg">복사한 정면 원본</a><details><summary>분류 점수 전체 (보정된 확률 아님)</summary><pre>'
                     f'{html.escape(json.dumps({"shape":p["shape_scores"],"size":p["size_scores"]},ensure_ascii=False,indent=2))}</pre></details></article>')
    document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>추가 판넬 검증</title>'
                '<style>body{background:#eef2f6;color:#142339;font:16px system-ui;margin:24px}header{position:sticky;top:0;background:#eef2f6;padding:8px;z-index:1}'
                'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:18px}article{background:white;border:2px solid #9cb8ad;border-radius:12px;padding:16px}'
                '.error{border:4px solid #c94242}.images{display:flex;height:320px;gap:12px}.images img{width:48%;object-fit:contain}pre{white-space:pre-wrap}input{padding:10px}</style>'
                f'<header><h1>새 사진 추가 검증</h1><p>{summary}</p><p>기존 모델 재학습·설정 변경 없음. 정답은 예측 전에 Codex가 육안 판독. 같은 촬영 자료이며 아동/XS/2XL/3XL 추가 검증 없음.</p>'
                '<label>번호 검색 <input id="search" placeholder="예: 1303"></label> <label><input id="errors" type="checkbox"> 오답만</label><span id="count"></span></header><main>'
                +''.join(cards)+'</main><script>function filter(){let n=0;for(const a of document.querySelectorAll("article")){const show=a.dataset.code.includes(document.querySelector("#search").value)&&(!document.querySelector("#errors").checked||a.classList.contains("error"));a.hidden=!show;if(show)n++;}document.querySelector("#count").textContent=" 표시 "+n+"벌";}document.querySelectorAll("input").forEach(e=>e.addEventListener("input",filter));filter();</script></html>')
    with (output/"검수보기.html").open("x",encoding="utf-8") as stream:
        stream.write(document)
    errors = [p for p in predictions if not(p["shape_correct"] and p["size_correct"])]
    text = (f'# 추가 사진 검증 (2026-10-25)\n\n{summary}\n\n'
            f'- 새 라벨 {m["count"]}벌. 성별 분포: {protocol["shapes"]}. 사이즈 분포: {protocol["sizes"]}.\n'
            '- 기존 1090벌과 코드·사진 해시 중복 없음. 0001~1108번 제외, 옷 몸통 dHash>6, 최근접 기존/신규 옷 비교 전수 육안 검토. 물리적으로 같은 옷을 모두 구별했다는 보장은 아님.\n'
            '- 현재 모델 예측을 보지 않고 모든 남녀·사이즈·박스 라벨을 확정/해시 고정. Codex 검수이며 독립 담당자 검수 아님. 기존 모델과 설정 재학습/튜닝 없음.\n'
            f'- 위치 검출 {m["located"]}/{m["count"]}, IoU≥0.5 {m["iou_at_50"]}, IoU≥0.75 {m["iou_at_75"]}, 평균 IoU {m["mean_iou"]:.4f}.\n'
            f'- 추출 박스가 정답 사각형 99% 이상 포함: {m["crop_covers_reference_99"]}/{m["count"]}. 머리 픽셀 분할 정확도와 다른 지표.\n'
            f'- CPU 실측 (GPU 비활성화·4스레드): 총 추론 {m["pipeline_seconds"]:.2f}초, 평균 {m["pipeline_seconds"]/m["count"]:.3f}초/벌. 모델 첫 로딩·해시 검사·파일 저장 제외, JPEG 읽기·AI 위치·두 분류 포함.\n'
            '- 같은 촬영 묶음의 새 미사용 사진. 새 촬영일/배경, 아동, XS/2XL/3XL, 판넬 없음/여러 개에 대한 추가 정확도는 확인하지 않음. 앱·설치파일 연결 없음.\n\n'
            f'오답/보류: {[(p["code"],p["true_shape"],p["shape"],p["true_size"],p["size"]) for p in errors]}\n\n'
            '[실제 원본·AI 추출·전체 점수 검수](검수보기.html), `labels.jsonl`, `predictions.json`, `results.json`, `protocol.json` 참조.\n')
    with (output/"RESULTS.md").open("x",encoding="utf-8") as stream:
        stream.write(text)
    print(json.dumps({"gallery_cards":len(cards),"errors":[p["code"] for p in errors]}),flush=True)


def verify(work, output):
    protocol = check_protocol(output)
    selection = read(output/"selection.json")
    records = [json.loads(line) for line in (output/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    predictions = read(output/"predictions.json")
    if [r["code"] for r in records] != [r["code"] for r in predictions]:
        raise ValueError("Prediction/label order differs")
    if {r["code"] for r in records} & set(selection["used_codes"]):
        raise ValueError("Previously used code found")
    if metrics_for(predictions) != read(output/"results.json")["all"]:
        raise ValueError("Reported metrics differ from individual predictions")
    for path, expected in {**protocol["page_hashes"], **protocol["review_hashes"]}.items():
        if sha(output/path) != expected:
            raise ValueError("Review changed after seal")
    all_photos = selection["source_photos"]+selection["previous_source_photos"]
    for row in all_photos:
        if sha(row["source"]) != row["sha256"]:
            raise ValueError(f"Original changed: {row['source']}")
    for row, p in zip(records,predictions,strict=True):
        if sha(output/row["front_path"]) != row["sha256"]:
            raise ValueError("Copied front changed")
        if p["location"]["valid"]:
            photo = load_photo(row["source"])
            with Image.open(output/"crops"/f"{row['code']}.png") as crop:
                if not np.array_equal(np.asarray(crop),np.asarray(photo.crop(p["location"]["crop_box"]))):
                    raise ValueError("Exported crop is not the actual pipeline input")
    import re
    gallery = (output/"검수보기.html").read_text(encoding="utf-8")
    links = re.findall(r'<img[^>]+src="([^"]+)"',gallery)
    if len(re.findall(r'<article ',gallery)) != len(records) or any(not (output/link).is_file() for link in links):
        raise ValueError("Gallery incomplete or missing photos")
    old_hashes = {r["sha256"] for r in selection["previous_source_photos"]}
    if old_hashes & {r["sha256"] for r in selection["source_photos"]}:
        raise ValueError("An exact old photograph was reused")
    result = {"count":len(records),"original_photos_checked":len(all_photos),
              "copied_fronts_checked":len(records),"exported_crops_pixel_checked":sum(p["location"]["valid"] for p in predictions),
              "no_previously_used_codes_or_photo_hashes":True,"models_and_inference_code_unchanged":True,
              "labels_and_reviews_unchanged_since_seal":True,"gallery_images_checked":len(links),
              "browser_render_verified":False,"new_training_performed":False,"app_integrated":False,
              "external_writes":False,"commit_or_push":False}
    save(output/"verification.json",result)
    print(json.dumps(result),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["select", "draft", "seal", "evaluate", "report", "verify"])
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.phase in ("select","draft"):
        {"select":select,"draft":draft}[args.phase](args.work)
    elif args.phase in ("seal","evaluate","verify"):
        {"seal":seal,"evaluate":evaluate,"verify":verify}[args.phase](args.work,args.output)
    else:
        report(args.output)


if __name__ == "__main__":
    main()
