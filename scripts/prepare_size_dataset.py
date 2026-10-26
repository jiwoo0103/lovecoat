"""Package explicitly reviewed size labels without predicting labels or editing sources."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageOps

SIZES = ("2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SEED = 20261024
SPLIT_DIR = {"train": "학습용", "validation": "검증용"}
AGE_DIR = {"adult": "성인", "child": "아동"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_mapping(path: Path, sheet_name: str = "20251020") -> dict:
    """Read the explicit Q:R correspondence; never join old garment rows by code."""
    fingerprint = digest(path)
    with ZipFile(path) as book:
        shared = ["".join(cell.itertext()) for cell in ET.fromstring(book.read("xl/sharedStrings.xml"))]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.find("s:sheets", NS) if s.get("name") == sheet_name)
        relation = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        relationships = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in relationships if r.get("Id") == relation)
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        xml = ET.fromstring(book.read(member))
        cells = {}
        for cell in xml.findall(".//s:c", NS):
            address = cell.get("r")
            if address not in {f"{column}{row}" for column in "QR" for row in range(3, 11)}:
                continue
            value = cell.find("s:v", NS)
            value = value.text if value is not None else ""
            if cell.get("t") == "s":
                value = shared[int(value)]
            elif value and cell.get("t") not in ("s", "inlineStr"):
                number = float(value)
                value = str(int(number)) if number.is_integer() else value
            cells[address] = value
    correspondence = []
    for row in range(3, 10):
        size, numbers = cells[f"Q{row}"], cells[f"R{row}"]
        if size != SIZES[row - 3] or not numbers:
            raise ValueError(f"Unsupported size correspondence at row {row}")
        correspondence.append({"size_label": size, "child_size_group": numbers,
                               "visible_numbers": numbers.split("/"), "source_cells": f"Q{row}:R{row}"})
    if digest(path) != fingerprint:
        raise ValueError("Workbook changed while being read")
    return {"workbook": str(path.resolve()), "sha256": fingerprint, "sheet": sheet_name,
            "range": "Q3:R10", "adult_sizes": [cells[f"Q{row}"] for row in range(3, 11)],
            "child_correspondence": correspondence}


def normalise_label(age: str, raw_text: str, mapping: dict) -> tuple[str, str]:
    if age == "adult" and raw_text in mapping["adult_sizes"]:
        return raw_text, ""
    if age == "child":
        for row in mapping["child_correspondence"]:
            if raw_text in row["visible_numbers"]:
                return row["size_label"], row["child_size_group"]
    raise ValueError(f"Unmapped visual label: {age}/{raw_text}")


def select_balanced(labels: dict, per_size: int = 100) -> list[str]:
    """Retain every child; spread adult sampling across 150-code capture blocks."""
    rng = random.Random(SEED)
    strata = defaultdict(list)
    for code, (age, text) in labels.items():
        strata[(age, text)].append(code)
    result = []
    for (age, _), codes in sorted(strata.items()):
        if age == "child":
            result.extend(codes)
            continue
        buckets = defaultdict(list)
        for code in codes:
            buckets[(int(code) - 1) // 150].append(code)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        order = list(buckets)
        rng.shuffle(order)
        picked = []
        target = min(per_size, len(codes))
        while len(picked) < target:
            for key in order:
                if buckets[key] and len(picked) < target:
                    picked.append(buckets[key].pop())
        result.extend(picked)
    return sorted(result, key=int)


def group_related(codes: list[str], inventory: dict, files: dict) -> dict[str, str]:
    """Keep exact duplicate files and close body dHash candidates in one split."""
    parents = {code: code for code in codes}

    def find(code):
        while parents[code] != code:
            parents[code] = parents[parents[code]]
            code = parents[code]
        return code

    def union(a, b):
        a, b = find(a), find(b)
        parents[max(a, b)] = min(a, b)

    seen = {}
    for index, code in enumerate(codes):
        for photo in files[code]:
            fingerprint = photo["sha256"]
            if fingerprint in seen:
                union(code, seen[fingerprint])
            seen[fingerprint] = code
        body_hash = int(inventory[code]["body_dhash"], 16)
        for previous in codes[:index]:
            if (body_hash ^ int(inventory[previous]["body_dhash"], 16)).bit_count() <= 2:
                union(code, previous)
    return {code: find(code) for code in codes}


def assign_splits(codes: list[str], labels: dict, groups: dict, fraction: float = .2) -> dict[str, str]:
    """Optimise held-out counts while respecting indivisible duplicate groups."""
    grouped = defaultdict(list)
    for code in codes:
        grouped[groups[code]].append(code)
    totals = Counter(tuple(labels[code]) for code in codes)
    targets = {key: max(1, round(count * fraction)) if count > 1 else 0 for key, count in totals.items()}
    counts = {key: Counter(tuple(labels[c]) for c in members) for key, members in grouped.items()}

    def score(validation):
        value = sum((validation[key] - targets[key]) ** 2 / max(1, targets[key]) for key in totals)
        for key, total in totals.items():
            if total > 1 and validation[key] in (0, total):
                value += 10000
        return value

    best_score, best = float("inf"), set()
    for attempt in range(40):
        rng = random.Random(SEED + attempt)
        order = list(grouped)
        rng.shuffle(order)
        chosen, validation = set(), Counter()
        current = score(validation)
        for _ in range(5):
            changed = False
            for key in order:
                sign = -1 if key in chosen else 1
                trial = validation.copy()
                for stratum, count in counts[key].items():
                    trial[stratum] += sign * count
                candidate = score(trial)
                if candidate < current:
                    chosen.symmetric_difference_update({key})
                    validation, current = trial, candidate
                    changed = True
            if not changed:
                break
            rng.shuffle(order)
        if current < best_score:
            best_score, best = current, chosen
    result = {code: "validation" if groups[code] in best else "train" for code in codes}
    for stratum, count in totals.items():
        partitions = {result[c] for c in codes if tuple(labels[c]) == stratum}
        if count > 1 and len(partitions) != 2:
            raise ValueError(f"Cannot represent {stratum} in both splits without leaking duplicate groups")
    return result


def inspect_files(source: Path, code: str, inventory: dict) -> list[dict]:
    result = []
    for path in sorted((source / f"0_{code}").iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Source symlink: {path}")
        fingerprint = digest(path)
        with Image.open(path) as image:
            image.verify()
        if path.name == "img_0_0_0.jpg" and fingerprint != inventory[code]["sha256"]:
            raise ValueError(f"Reviewed photograph changed: {code}")
        result.append({"name": path.name, "sha256": fingerprint, "bytes": path.stat().st_size,
                       "use_for_size_ocr": path.name == "img_0_0_0.jpg"})
    if not any(photo["use_for_size_ocr"] for photo in result):
        raise ValueError(f"Missing reviewed front: {code}")
    return result


def copy_checked(source: Path, target: Path, fingerprint: str) -> None:
    """Exclusive creation, byte-preserving copy, before/after source verification."""
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != fingerprint:
        raise ValueError(f"Source changed: {source}")
    with target.open("xb") as stream:
        stream.write(payload)
    if digest(target) != fingerprint or digest(source) != fingerprint:
        raise ValueError(f"Copy verification failed: {target}")


def preview(source: Path, target: Path) -> None:
    with Image.open(source) as original:
        original.draft("RGB", (1080, 1920))
        image = ImageOps.exif_transpose(original).convert("RGB")
        width, height = image.size
        # A broad viewing crop, deliberately not claimed as an annotated text box.
        image = image.crop((int(width * .28), int(height * .10), int(width * .74), int(height * .55)))
        image.thumbnail((360, 600))
        width, height = image.size
        gray = np.asarray(image.convert("L"))
        ink = (gray[:, int(width * .42):int(width * .62)] < 95).sum(axis=1)
        rows = np.flatnonzero(ink > max(3, width * .015))
        if len(rows):
            top = max(0, int(rows[0]) - 10)
            image = image.crop((0, top, width, min(height, top + int(width * .95))))
        image.save(target, quality=92)


def write_extras(output: Path, records: list[dict], mapping: dict, summary: dict) -> None:
    has_gender = all("gender" in row for row in records)
    gender_note = (
        "- `gender`: 남성/남아 `male`, 여성/여아 `female`. 성인은 사용자가 확인한 짧은 머리(A·0035번)와 "
        "묶은 머리(B·0001번) 판넬을 기준으로 Codex가 한 벌씩 판독했습니다.\n"
        "- 아동은 사용자 지시에 따라 전부 남아로 임시 지정했습니다. `gender_provisional=true`, "
        "`gender_label_source=user_provisional_rule`로 표시하며, 옷을 보고 성별을 검증한 결과가 아닙니다.\n"
        "- 성인 판넬의 성별 대응 기준만 사용자가 확인했습니다. 각 사진의 라벨 전체를 사람이 검수한 것은 아닙니다. "
        "`성별기준.json`에 기준·근거 사진·임시 여부를 기록했습니다.\n"
        "- 옷 카테고리는 이번 데이터에 라벨링하지 않았습니다."
        if has_gender else "- 성별·옷 카테고리는 이번 데이터에 라벨링하지 않았습니다."
    )
    rows = ["| 구분 | 사이즈 | 선별 | 학습 | 검증 |", "|---|---|---:|---:|---:|"]
    rows.extend(f"| {r['age_ko']} | {r['size_label']} | {r['selected_garments']} | {r['train']} | {r['validation']} |"
                for r in summary["distribution"])
    guide = f"""# 외투 사이즈 라벨 데이터

2026-10-24 · {summary['selected_garments']}벌 · 원본 사진 {summary['copied_original_photos']:,}장

`검수보기.html`을 열면 사진과 라벨을 함께 볼 수 있습니다. 학습용 {summary['split_counts']['train']}벌,
검증용 {summary['split_counts']['validation']}벌을 서로 다른 폴더에 넣었습니다.

## 라벨의 의미

- Codex가 번호가 표시된 사진 모음에서 정면 사진을 직접 보고 글자를 판독했습니다. 기존 OCR 출력을 정답으로 복사하지 않았습니다.
- `raw_text`: 사진 판넬에 실제로 적힌 글자/숫자. 예: 아동 `110`.
- `child_size_group`: 엑셀의 아동 묶음. 예: `100/110`.
- `size_label`: 엑셀 대응표의 사이즈. 예: 위 `110`은 아동 `S`.
- `age`: 성인 `adult`, 아동 `child`. 사용자가 설명한 영문/숫자 판넬 구분을 적용했습니다.
- `human_verified=false`: AI의 사진 판독 라벨이며 담당자의 독립 검수는 아직 없습니다. 모델 정확도를 발표할 때는 검증 라벨을 별도로 검수해야 합니다.
{gender_note}

## 폴더와 학습 입력

`학습용 또는 검증용 / 성인 또는 아동 / 사이즈 / 0_외투코드 / 사진과 label.json` 구조입니다.
성인은 영문 사이즈, 아동은 `80_90` 같은 숫자 묶음으로 폴더를 만들었습니다.
각 폴더의 `label.json`, 전체 `labels.jsonl`, `labels.csv`, 분할별 `labels.csv`에 같은 라벨을 기록했습니다.
코드의 앞자리 0은 JSON과 폴더명에 보존했습니다. CSV를 엑셀로 가져올 때 코드 열은 텍스트로 지정하세요.

옷 한 벌에 속한 사진은 전부 같은 분할에 복사했습니다. **사이즈 글자 OCR에는 `front_path`로 지정한 정면 사진만 사용하세요.**
옆·뒤 사진은 옷 묶음을 보존하기 위해 함께 복사한 참고 자료이며, 그 사진의 글자까지 판독했다는 뜻이 아닙니다.
`files_sha256.csv`의 `use_for_size_ocr=True`도 이 정면 사진을 가리킵니다.
`검수이미지`는 화면 확인용 축소 이미지입니다. 정밀한 글자 위치 상자를 주석 처리한 자료가 아니므로
Tesseract 추가 학습용 글자 줄 이미지를 만들 때는 별도로 글자 영역을 잘라야 합니다.

## 선별과 분할

- 원본 폴더: `{summary['source']}`.
- 정면 {summary['visually_transcribed_fronts']:,}장 판독 목록에서 성인 사이즈별 최대 100벌을 뽑았습니다.
  150개 번호 단위의 촬영 구간을 고르게 순회해 비슷한 앞번호 사진에만 치우치지 않게 했습니다.
- 아동은 확인한 139벌을 모두 포함했습니다. 성인 3XL도 확인한 68벌 전부를 포함했습니다.
- 성인 2XS와 아동 60·70 판넬은 확인하지 못했으며 사진을 복제하거나 합성해 빈 종류를 채우지 않았습니다.
  아동 170은 2벌뿐이므로 학습 1벌·검증 1벌입니다. 이 종류의 성능을 안정적으로 평가하기에는 자료가 부족합니다.
- 같은 외투코드, 동일 파일 SHA-256, 몸통 dHash 해밍 거리 2 이하인 유사 사진 후보를 하나의 그룹으로 묶어 분할했습니다.
  유사도 검사는 모든 유사 사진을 찾아낸다는 보장이 아닙니다. 새로운 촬영일 자료는 추후 별도 시험 자료로 확보하세요.
- 분할 비율은 약 80:20이며, 아동의 개별 숫자 표기도 가능한 한 양쪽에 포함했습니다. 재현 seed: `{summary['seed']}`.
- 성인 668벌·아동 139벌입니다. 종류별 원본 수량이 달라 성인/아동이 정확히 반반은 아닙니다.
  후속 학습에서는 아동과 드문 사이즈의 가중치를 조절해야 합니다.

{chr(10).join(rows)}

## 근거와 검증

- 엑셀 `{mapping['workbook']}`의 `{mapping['sheet']}!Q3:R10`에서 대응표만 읽었습니다.
  엑셀의 과거 외투코드별 정답을 이번 사진에 연결하지 않았습니다. 세부 근거와 엑셀 SHA-256은 `사이즈기준.json`에 있습니다.
- 원본 사진은 파일 내용을 그대로 복사하고 SHA-256으로 대조했습니다. 원본과 엑셀을 수정하지 않았습니다.
- 1582번은 원본에 2번 사진이 없어 2장만 복사하고 옷과 라벨은 유지했습니다.
- 0027번은 비표준 파일명과 회전된 원본 1장, 0982번은 사이즈 판넬이 없는 사진이므로 선별에서 제외했습니다.
- `선별전_사진판독목록.json`은 선별 전 판독 기록입니다. 실제 학습/검증 사진 목록은 `labels.jsonl`이 기준입니다.
- 파일 대조·라벨/폴더 일치·분할 중복 검사는 `verification.json`을 확인하세요.
- 검수 HTML의 스크립트와 파일 경로를 점검했습니다. 로컬 파일 URL에 대한 브라우저 보안 제한으로 자동 브라우저 화면 검증은 수행하지 못했습니다 (`review_page_checks.json`).
- **이번 작업은 데이터 구성까지입니다. 새 모델 학습이나 모델 정확도 측정은 수행하지 않았습니다.**
"""
    with (output / "데이터안내.md").open("x", encoding="utf-8") as stream:
        stream.write(guide)
    fields = ["code", "split", "age", "age_ko", "size_label", "raw_text", "child_size_group", "front_path"]
    if has_gender:
        fields.extend(["gender", "gender_ko", "gender_provisional", "gender_label_source"])
    data = [{key: r[key] for key in fields} for r in records]
    html = """<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>외투 사이즈 라벨 검수</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#18202b;font:16px/1.5 system-ui,'Malgun Gothic',sans-serif}
header{padding:22px 28px;background:white;border-bottom:1px solid #ccd2da}h1{font-size:24px;margin:0 0 8px}
p{margin:6px 0}nav{position:sticky;top:0;background:#eef2f7;padding:14px 28px;display:flex;gap:10px;flex-wrap:wrap;z-index:1}
select,input,button{font:inherit;border:1px solid #a7b1bf;border-radius:6px;padding:7px;background:white}button{cursor:pointer}
main{padding:22px 28px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px}
article{background:white;border:1px solid #c7d0da;border-radius:8px;padding:12px}article h2{font-size:18px;margin:0}
img{display:block;width:100%;height:370px;object-fit:contain;background:#fafafa;margin:10px 0}
.tag{display:inline-block;background:#e0eaf8;color:#233c5b;border-radius:4px;padding:2px 8px;margin:6px 0}
.provisional{background:#fff0c2;color:#684900}
footer{padding:10px 28px 28px}a{color:#174f9d}#count{align-self:center}
</style><header><h1>외투 사이즈 라벨 검수</h1>
<p>807벌 · 학습 645벌 / 검증 162벌 · 성인 668벌 / 아동 139벌</p>
<p>Codex 사진 직접 판독 · 담당자 독립 검수 전 · 모델 학습 전</p>
__GENDER_NOTE__
<p>아동은 판넬 숫자와 엑셀 사이즈를 함께 표시합니다. 사진을 누르면 복사한 정면 원본이 열립니다.</p>
<a href="데이터안내.md">데이터 안내</a> · <a href="labels.csv">전체 라벨 CSV</a> · <a href="분포.csv">종류별 수량</a>
</header><nav><select id="split"><option value="">학습·검증 전체</option value="train">학습용</option><option value="validation">검증용</option></select>
<select id="age"><option value="">성인·아동 전체</option><option value="adult">성인</option><option value="child">아동</option></select>
__GENDER_NAV__
<select id="size"><option value="">모든 사이즈</option></select>
<input id="code" aria-label="외투코드 검색" placeholder="외투코드 검색" size="12">
<button id="prev">이전</button><button id="next">다음</button><span id="count"></span></nav><main id="cards"></main>
<footer>옆·뒤 사진은 묶음 보존용 참고 자료입니다. 사이즈 OCR 입력에는 정면 사진만 사용하세요.</footer>
<script>const records=__DATA__;const sizeOrder=['2XS','XS','S','M','L','XL','2XL','3XL'];
const $=id=>document.getElementById(id);let page=0;const limit=48;
$('size').replaceChildren(new Option('모든 사이즈',''),...sizeOrder.map(s=>new Option(s,s)));
function text(tag,value){const el=document.createElement(tag);el.textContent=value;return el}
function render(){const list=records.filter(r=>(!$('split').value||r.split===$('split').value)&&(!$('age').value||r.age===$('age').value)&&(!r.gender||!$('gender').value||r.gender===$('gender').value)&&(!$('size').value||r.size_label===$('size').value)&&r.code.includes($('code').value.trim()));
const pages=Math.max(1,Math.ceil(list.length/limit));page=Math.min(page,pages-1);$('count').textContent=`${list.length}벌 · ${page+1}/${pages}쪽`;
$('prev').disabled=page===0;$('next').disabled=page>=pages-1;$('cards').replaceChildren();
for(const r of list.slice(page*limit,(page+1)*limit)){const card=document.createElement('article');card.append(text('h2',`${r.code} · ${r.age_ko}${r.gender_ko?' · '+r.gender_ko:''} · ${r.size_label}`));
const badge=text('span',r.split==='train'?'학습용':'검증용');badge.className='tag';card.append(badge);
if(r.gender_provisional){const warning=text('span','남아: 임시 지정');warning.className='tag provisional';card.append(warning)}
card.append(text('p',`사진 표기: ${r.raw_text}${r.child_size_group?' → '+r.child_size_group:''}`));
const link=document.createElement('a');link.href=encodeURI(r.front_path);link.target='_blank';link.rel='noopener';const img=document.createElement('img');
img.src=`검수이미지/${r.code}.jpg`;img.alt=`${r.code}의 사이즈 판넬`;img.loading='lazy';link.append(img);card.append(link);$('cards').append(card)}}
for(const id of ['split','age','size','code',...(records[0].gender?['gender']:[])])$(id).addEventListener(id==='code'?'input':'change',()=>{page=0;render()});
$('prev').onclick=()=>{page--;render()};$('next').onclick=()=>{page++;render()};render();</script></html>"""
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
    gender_note_html = ""
    gender_nav = ""
    if has_gender:
        counts = summary["age_gender_counts"]
        gender_note_html = (
            f"<p>성인 남성 {counts['adult_male']}벌 · 성인 여성 {counts['adult_female']}벌 · "
            f"아동 남아 {counts['child_male']}벌(사용자 임시 기준) · 아동 여아 {counts['child_female']}벌</p>"
            "<p>성인: 확인된 머리 판넬 기준. 아동: 옷을 보고 판별한 성별 정답이 아니며 추후 변경 예정.</p>"
            '<a href="성별기준.json">성별 기준</a> · <a href="성별분포.csv">성별·사이즈 수량</a>'
            ' · <a href="성별기준사진/A_0035_panel.png">남성 기준 사진</a>'
            ' · <a href="성별기준사진/B_0001_panel.png">여성 기준 사진</a>'
        )
        gender_nav = ('<select id="gender" aria-label="성별 필터"><option value="">성별 전체</option>'
                      '<option value="male">남성·남아</option><option value="female">여성·여아</option></select>')
        html = html.replace("외투 사이즈 라벨 검수", "외투 사이즈·성별 라벨 검수")
    html = html.replace("__GENDER_NOTE__", gender_note_html).replace("__GENDER_NAV__", gender_nav)
    with (output / "검수보기.html").open("x", encoding="utf-8") as stream:
        stream.write(html)


def build(source: Path, labels_path: Path, inventory_path: Path, workbook: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Dataset output and original source must be separate directories")
    if output.exists():
        raise FileExistsError(f"Output already exists; refusing to overwrite: {output}")
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    inventory = {row["code"]: row for row in json.loads(inventory_path.read_text(encoding="utf-8"))}
    mapping = read_mapping(workbook)
    for age, raw_text in labels.values():
        normalise_label(age, raw_text, mapping)
    codes = select_balanced(labels)
    print(f"Inspecting {len(codes)} garments before copying", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        file_lists = list(pool.map(lambda c: inspect_files(source, c, inventory), codes))
    files = dict(zip(codes, file_lists, strict=True))
    groups = group_related(codes, inventory, files)
    splits = assign_splits(codes, labels, groups)
    output.mkdir()
    (output / "검수이미지").mkdir()
    records, file_manifest = [], []
    for index, code in enumerate(codes):
        age, raw_text = labels[code]
        size, child_group = normalise_label(age, raw_text, mapping)
        destination = Path(SPLIT_DIR[splits[code]], AGE_DIR[age], (child_group or size).replace("/", "_"), f"0_{code}")
        folder = output / destination
        folder.mkdir(parents=True)
        for photo in files[code]:
            original = source / f"0_{code}" / photo["name"]
            copy_checked(original, folder / photo["name"], photo["sha256"])
            file_manifest.append({"code": code, "split": splits[code], "path": (destination / photo["name"]).as_posix(),
                                  "source_path": str(original), **photo})
        preview(source / f"0_{code}" / "img_0_0_0.jpg", output / "검수이미지" / f"{code}.jpg")
        record = {"code": code, "split": splits[code], "age": age, "age_ko": AGE_DIR[age],
                  "raw_text": raw_text, "size_label": size, "child_size_group": child_group,
                  "front_path": (destination / "img_0_0_0.jpg").as_posix(),
                  "source_folder": str(source / f"0_{code}"), "split_group": groups[code],
                  "front_sha256": inventory[code]["sha256"], "photo_count": len(files[code]),
                  "annotation_method": "codex_visual_transcription", "human_verified": False,
                  "precise_text_box_annotated": False,
                  "notes": "2번 사진 없음; 옷 묶음 유지" if len(files[code]) < 3 else ""}
        write_json(folder / "label.json", record)
        records.append(record)
        if (index + 1) % 100 == 0:
            print(f"Copied and hash-checked {index + 1}/{len(codes)} garments", flush=True)
    if digest(workbook) != mapping["sha256"]:
        raise ValueError("Size correspondence workbook changed")
    with (output / "labels.jsonl").open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_csv(output / "labels.csv", records)
    write_csv(output / "files_sha256.csv", file_manifest)
    for split, dirname in SPLIT_DIR.items():
        write_csv(output / dirname / "labels.csv", [r for r in records if r["split"] == split])
    write_json(output / "사이즈기준.json", mapping)
    write_json(output / "선별전_사진판독목록.json", {"annotation_method": "codex_visual_transcription",
               "human_verified": False, "reviewed_fronts": labels,
               "excluded": {"0027": "정면 파일명 비표준·회전 원본 1장; 판넬 L, 선별 제외",
                            "0982": "정면 사진에 머리·사이즈 판넬 없음"}})
    distribution = []
    for age in ("adult", "child"):
        for size in SIZES:
            available = [c for c in labels if labels[c][0] == age and normalise_label(*labels[c], mapping)[0] == size]
            selected = [r for r in records if r["age"] == age and r["size_label"] == size]
            if age == "child" and size == "3XL":
                continue
            distribution.append({"age": age, "age_ko": AGE_DIR[age], "size_label": size,
                                 "available_garments": len(available), "selected_garments": len(selected),
                                 "train": sum(r["split"] == "train" for r in selected),
                                 "validation": sum(r["split"] == "validation" for r in selected)})
    write_csv(output / "분포.csv", distribution)
    summary = {"status": "complete", "dataset_version": "2026-10-24.v1", "seed": SEED,
               "source": str(source), "visually_transcribed_fronts": len(labels), "selected_garments": len(records),
               "copied_original_photos": len(file_manifest), "preview_images": len(records),
               "copied_original_bytes": sum(p["bytes"] for p in file_manifest),
               "split_counts": dict(Counter(r["split"] for r in records)),
               "age_counts": dict(Counter(r["age"] for r in records)), "distribution": distribution,
               "duplicate_candidate_groups": len(set(groups.values())),
               "group_method": "whole garment + exact file SHA256 + body dHash Hamming <= 2",
               "human_verified": False, "model_training_performed": False,
               "checks": {"copies_match_source_sha256": True, "sources_unchanged_after_copy": True,
                          "workbook_unchanged": True, "all_original_images_decode": True,
                          "garment_overlap_between_splits": False, "duplicate_group_overlap_between_splits": False}}
    write_json(output / "summary.json", summary)
    write_extras(output, records, mapping, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.labels, args.inventory, args.workbook, args.output)
    print(json.dumps({key: result[key] for key in ("selected_garments", "copied_original_photos", "split_counts", "age_counts")},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
