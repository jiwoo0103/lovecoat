from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coatdesk_ab.data import load_photo, read, sha
from .evaluate import atomic
from .experiment import CLASSIFIERS, KINDS, OUTPUT, SIDE, WORK

NAMES={"adult_male":"성인 남성","adult_female":"성인 여성","child":"아동",None:"보류"}


def main(work,output):
    results=read(output/"results.json")
    predictions=read(output/"predictions.json")
    stress=read(output/"stress-results.json")
    benchmark=read(output/"cpu-paired-benchmark.json")
    before=read(work/"baseline-dev.json")
    inner=read(work/"inner/complete.json")
    selected=read(work/"inner/best-dev.json")
    final=read(work/"final/complete.json")
    protocol=read(work/"protocol.json")
    a=results["all"]
    old=stress["summary"]["baseline"]
    new=stress["summary"]["new"]
    example=next(r for r in predictions if r["code"]=="2950")
    def area(box):
        return (box[2]-box[0])*(box[3]-box[1])
    area_ratio=float(np.mean([area(p["location"]["box"])/area(p["baseline"]["location"]["box"]) for p in predictions]))
    old_mean_iou=float(np.mean([p["baseline"]["iou"] for p in predictions]))
    old_iou75=sum(p["baseline"]["location"]["valid"] and p["baseline"]["iou"]>=.75 for p in predictions)
    stress_pending=[(r["code"],v["kind"],round(v["score"],5)) for r in stress["rows"] for v in r["variants"]["new"] if not v["valid"]]
    decision=(a["both_correct"]>=results["baseline_both_correct"] and
              new["valid"]>=old["valid"] and new["reference_coverage_99"]>=old["reference_coverage_99"] and
              a["reference_coverage_99"]>=results["baseline_reference_coverage_99"] and
              new["confident_wrong_location"]<=old["confident_wrong_location"])
    card={"version":"whole-panel-448-v2","architecture":protocol["architecture"],"side":SIDE,"classes":["background","whole_placard"],
          "weights_sha256":sha(output/"detector.pt"),"fit_count":645,"inner_fit_count":516,"inner_dev_count":129,
          "epochs":final["epochs"],"score_threshold":.5,"crop_margin":.08,"classifier_weights_frozen":True,
          "classifier_weights_sha256":{str(p):sha(p) for p in sorted(CLASSIFIERS.glob("panel-*.pt"))},
          "input":protocol["input"],"pre_existing_regression_photos":924,"independent_new_test":False,
          "meets_current_comparison_checks":decision,"app_integrated":False}
    atomic(output/"model-card.json",card)
    with (output/"predictions.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["code","true_shape","true_size","shape","size","detector_score","accepted","iou","coverage","old_score","old_accepted"])
        for p in predictions:
            writer.writerow([p["code"],p["truth_shape"],p["truth_size"],p["shape"],p["size"],p["location"]["score"],
                             p["location"]["valid"],p["iou"],p["coverage"],p["baseline"]["location"]["score"],p["baseline"]["location"]["valid"]])
    cards=[]
    for p in sorted(predictions,key=lambda r:(r["shape_correct"] and r["size_correct"],r["code"]!="2950",int(r["code"]))):
        ok=p["shape_correct"] and p["size_correct"]
        scores=" / ".join(f'{NAMES[k]} {v:.1%}' for k,v in p["shape_scores"].items())
        loc=p["location"]
        caption="실제 분류 입력 판넬" if loc["valid"] else "보류 후보 · 자동 분류하지 않음"
        head=(f'<figure><img loading="lazy" src="crops/{p["code"]}.png" alt="{caption}"><figcaption>{caption}</figcaption></figure>' if loc["crop_box"] else '<p>위치 후보 없음</p>')
        cards.append(f'<article class="{"ok" if ok else "error"}" data-code="{p["code"]}"><h2>{p["code"]} · {"정답" if ok else "확인 필요"}</h2>'
                     f'<p>정답: {NAMES[p["truth_shape"]]} / {p["truth_size"]}<br>분류: {NAMES[p["shape"]]} / {p["size"] or "보류"}</p>'
                     f'<p>판넬 위치 점수: <b>{loc["score"]:.1%}</b> · {"통과" if loc["valid"] else "기준 50% 미달"}<br>'
                     f'기존 모델 위치 점수: {p["baseline"]["location"]["score"]:.1%}</p><div class="photos"><figure><img loading="lazy" src="original-thumbnails/{p["code"]}.jpg" alt="원본과 추출 범위"><figcaption>원본과 추출 범위</figcaption></figure>{head}</div>'
                     f'<p>{html.escape(scores)}</p><a href="{html.escape(Path(p["source"]).as_uri())}">정면 원본 열기</a>'
                     f'<details><summary>박스 비교·분류 점수</summary><p>위치 박스 IoU {p["iou"]:.3f} · 기준 박스 포함 {p["coverage"]:.2%}</p>'
                     f'<pre>{html.escape(json.dumps({"shape":p["shape_scores"],"size":p["size_scores"]},ensure_ascii=False,indent=2))}</pre></details></article>')
    document=('<!doctype html><html lang="ko"><meta charset="utf-8"><title>판넬 위치 모델 개선 비교</title>'
              '<style>body{font:16px system-ui;background:#eef2f6;color:#132238;margin:22px}header{position:sticky;top:0;background:#eef2f6;padding:8px;z-index:1}'
              'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:16px}article{padding:16px;background:white;border:2px solid #93b6a6;border-radius:12px}.error{border:4px solid #be4444}'
              '.photos{display:flex;gap:12px;height:350px}figure{margin:0;width:48%;height:100%}img{width:100%;height:310px;object-fit:contain}figcaption{font-size:13px}pre{white-space:pre-wrap}input{padding:9px}</style>'
              f'<header><h1>판넬 위치 모델 개선 비교</h1><p>924벌 · 남성/여성/아동과 사이즈 동시 정답: 기존 {results["baseline_both_correct"]}/924 → 새 모델 {a["both_correct"]}/924</p>'
              '<p>위치 기준 50%·추출 여백 8% 유지. 성별·사이즈 모델은 그대로입니다. 점수는 보정된 실제 정답 확률이 아닙니다. 아동 남녀는 분류하지 않습니다.</p>'
              '<p><a href="RESULTS.md">전체 결과·속도·한계</a> · <a href="2950_비교.jpg">2950번 비교</a></p>'
              '<label>번호 검색 <input id="search" placeholder="2950"></label> <label><input type="checkbox" id="errors"> 확인 필요만</label> <span id="count"></span></header><main>'
              +''.join(cards)+'</main><script>function filter(){let n=0;for(const a of document.querySelectorAll("article")){const show=a.dataset.code.includes(document.querySelector("#search").value)&&(!document.querySelector("#errors").checked||a.classList.contains("error"));a.hidden=!show;if(show)n++;}document.querySelector("#count").textContent="표시 "+n+"벌";}document.querySelectorAll("input").forEach(e=>e.addEventListener("input",filter));filter();</script></html>')
    with (output/"검수보기.html").open("w",encoding="utf-8") as stream:
        stream.write(document)
    font=ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf",18)
    canvas=Image.new("RGB",(1000,550),"#edf2f6")
    draw=ImageDraw.Draw(canvas)
    photo=load_photo(example["source"])
    for i,(name,found) in enumerate((("기존 320",example["baseline"]["location"]),("새 448",example["location"]))):
        crop=photo.crop(found["crop_box"])
        crop.thumbnail((450,390))
        x=i*500
        draw.text((x+18,15),f'{name} · 위치 점수 {found["score"]:.1%}',font=font,fill="#122238")
        draw.text((x+18,45),"통과" if found["valid"] else "보류 · 진단용 후보",font=font,fill="#137052" if found["valid"] else "#996000")
        canvas.paste(crop,(x+(500-crop.width)//2,90+(390-crop.height)//2))
    draw.text((18,510),"동일 원본 · 통과 기준 50% · 성별/사이즈 가중치 변경 없음",font=font,fill="#122238")
    canvas.save(output/"2950_비교.jpg",quality=96)
    (output/"review-pages").mkdir(exist_ok=True)
    flagged={r["code"] for r in predictions if not(r["shape_correct"] and r["size_correct"]) or r["coverage"]<.99 or r["iou"]<.75}
    # All 924 crops remain available in the gallery. Review boards prioritize
    # every metric failure plus an evenly spaced 64-photo sample.
    selected_rows=[p for i,p in enumerate(predictions) if p["code"] in flagged or i in set(np.linspace(0,len(predictions)-1,64,dtype=int))]
    for page,start in enumerate(range(0,len(selected_rows),36)):
        group=selected_rows[start:start+36]
        board=Image.new("RGB",(1440,math.ceil(len(group)/6)*270),"#edf2f6")
        d=ImageDraw.Draw(board)
        for j,p in enumerate(group):
            x,y=j%6*240,j//6*270
            d.text((x+3,y+3),f'{p["code"]} {NAMES[p["truth_shape"]]} {p["truth_size"]}',font=font,fill="#122238")
            if p["location"]["crop_box"]:
                with Image.open(output/"crops"/f"{p['code']}.png") as im:
                    im=im.convert("RGB")
                    im.thumbnail((232,235))
                    board.paste(im,(x+(240-im.width)//2,y+28))
        board.save(output/"review-pages"/f"page-{page:02}.jpg",quality=96)
    atomic(output/"visual-review-selection.json",{"flagged_codes":sorted(flagged),"selected_codes":[p["code"] for p in selected_rows],
                                              "method":"All metric failures plus 64 uniformly spaced source-order photographs; no ground-truth corrections or model tuning"})
    speed=benchmark["means"]
    text=(f'# 판넬 위치 모델 개선 비교 (2026-10-26)\n\n'
          f'동일 924벌의 남성/여성/아동·사이즈 동시 정답: **기존 {results["baseline_both_correct"]}/924 → 새 모델 {a["both_correct"]}/924**. '
          f'위치 검출 수락 {a["located"]}/924. [사진별 검수](검수보기.html), [2950번 비교](2950_비교.jpg).\n\n'
          '## 변경한 부분\n\n'
          '- SSDLite MobileNetV3-Large 구조·기존 일반 COCO 초기 가중치를 유지하고 전체 사진 입력을 320→448로 높였습니다. 원본 EXIF 미리보기에서 448×448으로 직접 축소합니다.\n'
          '- 자연스러운 세로 사진 좌표계의 회전·크기·위치 변형을 모델 좌표로 옮기고, 명암 범위·약한 흐림·옷 부분의 밝기 변형을 보강했습니다. 머리 전체가 화면 안에 남는 변형만 학습에 사용합니다.\n'
          '- 성별·사이즈 분류기 가중치, 위치 점수 기준 0.50, 추출 여백 8%는 고정했습니다. 기존 파일과 앱·설치파일을 교체하지 않았습니다.\n\n'
          '## 학습과 선택\n\n'
          f'기존 학습 사진 645벌만 사용했습니다. 내부 학습 516·검증 129벌과 기존 그룹 분할을 보존했습니다. 내부 검증에 원본·좌우/상하 1% 이동·0.9/1.1배 크기·0.85/1.15배 밝기, 9종(1,161입력)을 적용했습니다. 최대 40회 중 검증 박스 포함률을 우선해 **{inner["best_epoch"]}회**를 선택했습니다.\n\n'
          f'내부 변형 입력에서 기준 사각형 99% 이상 포함: 기존 {before["stress"]["coverage_99"]}/1161 → 새 모델 {selected["stress"]["coverage_99"]}/1161. '
          f'원본 129장에서는 기존 {before["original"]["coverage_99"]} → 새 모델 {selected["original"]["coverage_99"]}. '
          f'선택한 {final["epochs"]}회만큼 일반 COCO 초기 가중치에서 645벌 전체로 다시 학습했습니다. 기존 최종 모델에서 이어 학습하지 않았습니다. 매 회차 원자적 체크포인트로 재개할 수 있습니다.\n\n'
          '최종 924벌(이전 445+추가 479)은 학습·회차 선택에 사용하지 않았습니다. 이미 결과와 2950번 문제를 확인했던 같은 촬영 묶음이므로 **새로운 독립 검증 자료로 취급하지 않습니다.** 추가 479벌의 평가 후 성별 수기 라벨 정정 이력도 기존 결과에 그대로 보존했습니다.\n\n'
          '## 원본 사진 평가\n\n'
          f'- 남성/여성/아동 구분: {a["shape_correct"]}/924\n- 사이즈: {a["size_correct"]}/924\n- 두 항목 동시: {a["both_correct"]}/924\n'
          f'- 위치 기준 박스 포함률 99% 이상: 기존 {results["baseline_reference_coverage_99"]} → 새 모델 {a["reference_coverage_99"]}/924\n- 박스 IoU 0.75 이상: {a["iou_75"]}/924 · 평균 IoU {a["mean_iou"]:.4f}\n'
          f'- 보류/오답 번호: {a["pending_or_wrong"]}\n\n'
          f'원래 위치 박스 면적은 기존보다 평균 {(area_ratio-1)*100:.1f}% 넓어졌습니다. 정확한 경계 일치 지표인 평균 IoU는 기존 {old_mean_iou:.4f} → 새 모델 {a["mean_iou"]:.4f}, IoU≥0.75 수는 {old_iou75} → {a["iou_75"]}로 낮아졌습니다. 머리 영역 보존·보류 감소와 더 넉넉한 영역 사이의 차이가 있으므로 모든 위치 지표가 개선되었다고 주장하지 않습니다.\n\n'
          f'2950번 위치 점수는 기존 {example["baseline"]["location"]["score"]:.2%} → 새 모델 {example["location"]["score"]:.2%}, '
          f'분류 결과 {NAMES[example["shape"]]}/{example["size"]}. 점수는 보정된 실제 정답 확률이 아닙니다.\n\n'
          '## 작은 입력 변화 검사\n\n'
          f'기존 924장 각각에서 파생한 9종, 총 {len(KINDS)*924:,}개 입력입니다. 새 원본 사진 수가 늘어난 것은 아닙니다. 각 모델의 전처리된 전체 사진 입력에서 동일한 비율의 변형을 적용하고, 박스 포함률과 오검출도 함께 계산했습니다.\n\n'
          '| 항목 | 기존 320 | 새 448 |\n|---|---:|---:|\n'
          f'| 점수 기준 통과 | {old["valid"]}/{old["inputs"]} | {new["valid"]}/{new["inputs"]} |\n'
          f'| 기준 박스 99% 이상 포함 | {old["reference_coverage_99"]} | {new["reference_coverage_99"]} |\n'
          f'| 9종 모두 통과한 사진 | {old["all_9_accepted_photos"]}/924 | {new["all_9_accepted_photos"]}/924 |\n'
          f'| 통과했지만 IoU 0.5 미만 | {old["confident_wrong_location"]} | {new["confident_wrong_location"]} |\n\n'
          f'새 모델의 변형 입력 보류: {stress_pending}. 이 결과도 남겨두며 원본 924장 성적과 구분합니다.\n\n'
          '점수가 올라간 것만으로 개선으로 판정하지 않으며, 실제 위치·추출과 고정한 분류기의 결과를 함께 비교합니다. 박스 포함률은 머리 픽셀 분할 정확도와 다릅니다.\n\n'
          '## CPU 시간\n\n'
          f'GPU 비활성화·PyTorch FP32·4스레드, 같은 32장을 기존/새 모델 순서를 번갈아 비교했습니다. JPEG 읽기·위치·두 분류기를 포함하고 초기 로딩은 제외합니다.\n\n'
          '| 평균 시간/벌 | 기존 320 | 새 448 |\n|---|---:|---:|\n'
          f'| 위치 모델 | {speed["baseline"]["locate"]:.3f}초 | {speed["new"]["locate"]:.3f}초 |\n'
          f'| 사진 읽기~분류 전체 | {speed["baseline"]["total"]:.3f}초 | {speed["new"]["total"]:.3f}초 |\n\n'
          f'새 모델의 924장 전체도 GPU 없이 CPU로 실행했습니다. 누적 추론 {a["pipeline_seconds"]:.2f}초, 평균 {a["pipeline_seconds"]/924:.3f}초/벌입니다. 다른 CPU의 처리시간 보장은 아닙니다.\n\n'
          '## 범위와 다음 단계\n\n'
          '기존 원본·라벨·모델·추론 코드는 해시로 보호합니다. 새 모델/입력/분류기와 평가 코드를 평가 전에 고정했고 평가 후 튜닝하지 않았습니다. 원본 1,569장과 추출 PNG의 실제 입력 픽셀 일치를 검사합니다. 검수 범위와 테스트 결과는 `verification.json`과 `visual-review.json`을 따릅니다.\n\n'
          '같은 촬영 묶음의 결과이며 새 촬영일·배경·판넬 없음/여러 개·새 아동 자료에 대한 일반 성능은 확인하지 않았습니다. 아동 남녀는 분류하지 않고, 사이즈는 기존 17개 표기 중 선택합니다. 앱의 모델별 낮은 점수 확인 필요 기능은 후속 연결 단계입니다. 외부 쓰기·패키지 설치·커밋·푸시·배포는 하지 않았습니다.\n\n'
          '## 재현\n\n```powershell\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.experiment prepare\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.experiment inner\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.experiment final\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.evaluate cpu\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.evaluate paired\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.evaluate stress\n'
          '.\\.local\\ab-venv\\Scripts\\python.exe -B -m coatdesk_locator_v2.evaluate verify\n```\n\n'
          '`prepare`는 새 작업 폴더가 필요하며 기존 폴더를 덮어쓰지 않습니다. 학습/평가는 저장된 체크포인트에서 재개할 수 있고, 모델/라벨/코드가 바뀌면 중단합니다.\n')
    with (output/"RESULTS.md").open("w",encoding="utf-8") as stream:
        stream.write(text)
    print(json.dumps({"model_meets_comparison_checks":decision,"both_correct":a["both_correct"],"visual_review_boards":math.ceil(len(selected_rows)/36),"flagged":len(flagged)}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--work",type=Path,default=WORK)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    args=parser.parse_args()
    main(args.work,args.output)
