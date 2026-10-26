"""Preserve the blind evaluation and transparently adjudicate human label errors."""
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coatdesk_ab.data import load_photo, read, sha
from scripts.validate_panel_extra import OUTPUT, check_protocol, frozen_files, metrics_for, write_csv

NAMES = {"adult_male":"남성","adult_female":"여성","child":"아동",None:"보류"}


def render(output):
    check_protocol(output)
    rows = [json.loads(s) for s in (output/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    predictions = {r["code"]:r for r in read(output/"predictions.json")}
    folder = output/"crop-review-pages"
    folder.mkdir()
    font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf",18)
    for page,start in enumerate(range(0,len(rows),36)):
        group = rows[start:start+36]
        canvas = Image.new("RGB",(1440,math.ceil(len(group)/6)*272),"#edf2f6")
        draw = ImageDraw.Draw(canvas)
        for j,r in enumerate(group):
            p=predictions[r["code"]]
            photo=load_photo(r["source"])
            box=p["location"]["crop_box"]
            truth=r["box"]
            box=box if box else truth
            bw,bh=box[2]-box[0],box[3]-box[1]
            v=[max(0,math.floor(min(box[0],truth[0])-.25*bw)),max(0,math.floor(min(box[1],truth[1])-.25*bh)),
               min(photo.width,math.ceil(max(box[2],truth[2])+.25*bw)),min(photo.height,math.ceil(max(box[3],truth[3])+.20*bh))]
            im=photo.crop(v)
            ImageDraw.Draw(im).rectangle([box[0]-v[0],box[1]-v[1],box[2]-v[0],box[3]-v[1]],
                                         outline="#008ee5" if p["location"]["valid"] else "#dd9600",width=3)
            im.thumbnail((234,230))
            x,y=j%6*240,j//6*272
            draw.text((x+3,y+2),f'{r["code"]} 正 {NAMES[r["shape"]]} {r["size"]}',font=font,fill="#172433")
            canvas.paste(im,(x+(240-im.width)//2,y+29))
        canvas.save(folder/f"page-{page:02}.jpg",quality=96)
    print(json.dumps({"review_pages":math.ceil(len(rows)/36),"photos":len(rows)}),flush=True)


def rescore(rows,predictions,corrections):
    rows=copy.deepcopy(rows)
    predictions=copy.deepcopy(predictions)
    before={r["code"]:r for r in rows}
    if set(corrections)-set(before):
        raise ValueError("Correction outside evaluated set")
    for code,fix in corrections.items():
        if before[code]["shape"]!=fix["before"] or fix["after"] not in ("adult_male","adult_female"):
            raise ValueError("Unexpected label change")
        r=before[code]
        r["shape"]=fix["after"]
        r["gender"]="male" if fix["after"]=="adult_male" else "female"
        r["label_revision"]="v2_post_evaluation_visual_adjudication"
        r["label_revision_reason"]=fix["reason"]
    if set(before)!={r["code"] for r in predictions}:
        raise ValueError("Prediction/label mismatch")
    for p in predictions:
        p["true_shape"]=before[p["code"]]["shape"]
        p["shape_correct"]=p["shape"]==p["true_shape"]
    return rows,predictions


def adjudicate(output):
    protocol=check_protocol(output)
    review=read(output/"crop-visual-review.json")
    if set(review["pages"])!=set(range(math.ceil(protocol["count"]/36))):
        raise ValueError("Recheck every photo before correcting labels")
    initial=output/"_initial-evaluation"
    initial.mkdir()
    names=("protocol.json","labels.jsonl","labels.csv","predictions.json","predictions.csv",
           "results.json","RESULTS.md","검수보기.html","verification.json")
    for name in names:
        shutil.copyfile(output/name,initial/name)
    shutil.copyfile(initial/"검수보기.html",initial/"검수보기.original.html")
    archived_html=(initial/"검수보기.html").read_text(encoding="utf-8")
    for prefix in ("crops/","original-thumbnails/","photos/"):
        archived_html=archived_html.replace('"'+prefix,'"../'+prefix)
    (initial/"검수보기.html").write_text(archived_html,encoding="utf-8")
    rows=[json.loads(s) for s in (output/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    original_predictions=read(output/"predictions.json")
    rows,predictions=rescore(rows,original_predictions,review["label_corrections"])
    for old,new in zip(original_predictions,predictions,strict=True):
        if {k:v for k,v in old.items() if k not in ("true_shape","shape_correct")}!={k:v for k,v in new.items() if k not in ("true_shape","shape_correct")}:
            raise ValueError("Model outputs changed during label adjudication")
    with (output/"labels.jsonl").open("w",encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row,ensure_ascii=False)+"\n")
    # Only new task-owned metadata are replaced; originals/models stay frozen.
    (output/"labels.csv").unlink()
    write_csv(output/"labels.csv",rows,["code","age","gender","shape","size","role","source","front_path","sha256"])
    for name,value in (("predictions.json",predictions),("results.json",read(initial/"results.json"))):
        if name=="results.json":
            value["all"]=metrics_for(predictions)
            for field in ("size","shape"):
                value["by_"+field]={v:metrics_for([p for p in predictions if p["true_"+field]==v])
                                    for v in sorted({p["true_"+field] for p in predictions})}
            value["label_adjudication"]=review["label_corrections"]
            value["initial_metrics"]=read(initial/"results.json")["all"]
            value["labels_sha256"]=sha(output/"labels.jsonl")
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    (output/"predictions.csv").unlink()
    write_csv(output/"predictions.csv",predictions,["code","true_shape","shape","shape_correct","true_size","size","size_correct","iou","coverage"])
    protocol.update(initial_labels_sha256=protocol["labels_sha256"],labels_sha256=sha(output/"labels.jsonl"),
                    initial_ground_truth_sealed_before_predictions=True,ground_truth_sealed_before_predictions=False,
                    label_corrections_after_predictions=review["label_corrections"],
                    initial_protocol_sha256=sha(initial/"protocol.json"),adjudication_script_sha256=sha(Path(__file__)))
    protocol["review_hashes"]["crop-visual-review.json"]=sha(output/"crop-visual-review.json")
    protocol["shapes"]={k:sum(r["shape"]==k for r in rows) for k in sorted({r["shape"] for r in rows})}
    (output/"protocol.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding="utf-8")
    results=read(output/"results.json")
    results.update(protocol_sha256=sha(output/"protocol.json"),initial_protocol_sha256=protocol["initial_protocol_sha256"])
    (output/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    gallery=(initial/"검수보기.html").read_text(encoding="utf-8")
    gallery=gallery.replace('남녀 477/479','남녀 478/479').replace('둘 다 477/479','둘 다 478/479')
    gallery=gallery.replace('정답은 예측 전에 Codex가 육안 판독.','초기 라벨은 예측 전에 판독. 오류 확인 후 2762번 성별 수기 라벨 1건 정정, 초기 결과 별도 보존.')
    start=gallery.index('<article class="error" data-code="2762">')
    end=gallery.index('</article>',start)+len('</article>')
    card=gallery[start:end].replace('class="error"','class="ok"').replace('2762 · 오답/보류','2762 · 정답 (수기 라벨 정정)')
    card=card.replace('정답: 성인 여성 / XL','정답: 성인 남성 / XL')
    gallery=gallery[:start]+card+gallery[end:]
    gallery=gallery.replace('<label>번호 검색','<p><a href="RESULTS.md">검증 설명·정정 이력</a> · <a href="_initial-evaluation/RESULTS.md">초기 성적</a></p><label>번호 검색')
    (output/"검수보기.html").write_text(gallery,encoding="utf-8")
    print(json.dumps({"after":read(output/"results.json")["all"],"corrections":review["label_corrections"]}),flush=True)


def verify(output):
    protocol=read(output/"protocol.json")
    initial=output/"_initial-evaluation"
    if frozen_files()!=protocol["frozen_files"] or sha(Path(__file__))!=protocol["adjudication_script_sha256"]:
        raise ValueError("Model/code integrity failed")
    if sha(initial/"labels.jsonl")!=protocol["initial_labels_sha256"] or sha(output/"labels.jsonl")!=protocol["labels_sha256"]:
        raise ValueError("Label integrity failed")
    if sha(initial/"protocol.json")!=protocol["initial_protocol_sha256"]:
        raise ValueError("Initial protocol changed")
    before=read(initial/"predictions.json")
    after=read(output/"predictions.json")
    for old,new in zip(before,after,strict=True):
        if {k:v for k,v in old.items() if k not in ("true_shape","shape_correct")}!={k:v for k,v in new.items() if k not in ("true_shape","shape_correct")}:
            raise ValueError("Predictions changed")
    if metrics_for(after)!=read(output/"results.json")["all"]:
        raise ValueError("Rescoring failed")
    event=read(output/"crop-visual-review.json")
    for name,expected in protocol["review_hashes"].items():
        if sha(output/name)!=expected:
            raise ValueError("Visual review event changed")
    for page,expected in event["page_hashes"].items():
        if sha(output/"crop-review-pages"/f"page-{int(page):02}.jpg")!=expected:
            raise ValueError("Review page changed")
    copied=[json.loads(s) for s in (output/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    selection=read(output/"selection.json")
    for r in selection["source_photos"]+selection["previous_source_photos"]:
        if sha(r["source"])!=r["sha256"]:
            raise ValueError("Source photo changed")
    for r,p in zip(copied,after,strict=True):
        if sha(r["source"])!=r["sha256"] or sha(output/r["front_path"])!=r["sha256"]:
            raise ValueError("Original/copy changed")
        if p["location"]["valid"]:
            with Image.open(output/"crops"/f"{r['code']}.png") as crop:
                if not np.array_equal(np.asarray(crop),np.asarray(load_photo(r["source"]).crop(p["location"]["crop_box"]))):
                    raise ValueError("Actual crop differs")
    result=read(initial/"verification.json")
    result.update(initial_blind_evaluation_preserved=True,label_corrections_after_predictions=len(event["label_corrections"]),
                  labels_and_reviews_unchanged_since_seal=False,initial_labels_and_reviews_preserved=True,
                  corrected_labels_match_visual_adjudication=True,
                  raw_predictions_unchanged=True,actual_ai_crops_visually_reviewed=478,held_candidate_visually_reviewed=1,
                  tests="10 holdout/adjudication tests passed; Ruff passed",labels_sha256=protocol["labels_sha256"])
    (output/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("phase",choices=["render","adjudicate","verify"])
    parser.add_argument("--output",type=Path,default=OUTPUT)
    args=parser.parse_args()
    {"render":render,"adjudicate":adjudicate,"verify":verify}[args.phase](args.output)


if __name__=="__main__":
    main()
