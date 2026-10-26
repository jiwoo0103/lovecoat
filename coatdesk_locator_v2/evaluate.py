from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

from coatdesk_ab.data import load_photo, read, save, sha
from coatdesk_locator.model import build_detector, choose_detection, overlap
from coatdesk_locator.pipeline import FrozenPanelClassifiers, PlacardLocator, rgb_tensor
from .experiment import CLASSIFIERS, EXTRA, KINDS, OLD_RESULT, OUTPUT, SIDE, WORK, build_v2, change, check
from .pipeline import PlacardLocatorV2


def atomic(path,value):
    temp=path.with_suffix(".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temp,path)


def summarize(rows):
    n=len(rows)
    return {"count":n,"located":sum(r["location"]["valid"] for r in rows),
            "reference_coverage_99":sum(r["location"]["valid"] and r["coverage"]>=.99 for r in rows),
            "iou_75":sum(r["location"]["valid"] and r["iou"]>=.75 for r in rows),
            "mean_iou":float(np.mean([r["iou"] for r in rows])),
            "shape_correct":sum(r["shape_correct"] for r in rows),"size_correct":sum(r["size_correct"] for r in rows),
            "both_correct":sum(r["shape_correct"] and r["size_correct"] for r in rows),
            "pipeline_seconds":sum(sum(r.get("timing",{}).values()) for r in rows),
            "pending_or_wrong":[r["code"] for r in rows if not(r["shape_correct"] and r["size_correct"])],
            "reference_review_candidates":[r["code"] for r in rows if not r["location"]["valid"] or r["coverage"]<.99]}


def baseline():
    result={}
    for r in read(OLD_RESULT/"predictions.json"):
        result[r["code"]]={"location":r["location"],"shape":r["predicted_shape"],"size":r["predicted_size"],
                           "shape_correct":r["shape_correct"],"size_correct":r["size_correct"],"iou":r["iou"],"coverage":r["coverage"]}
    for r in read(EXTRA/"predictions.json"):
        result[r["code"]]={k:r[k] for k in ("location","shape","size","shape_correct","size_correct","iou","coverage")}
    assert len(result)==924
    return result


def freeze_evaluation(work,output):
    check(work)
    path=output/"evaluation-protocol.json"
    if not path.exists():
        completed=read(work/"final/complete.json")
        output.mkdir(parents=True,exist_ok=False)
        shutil.copyfile(work/"final/model.pt",output/"detector.pt")
        for name in ("protocol.json","training-labels.json","regression-labels.json","baseline-dev.json"):
            shutil.copyfile(work/name,output/name)
        for name in ("TORCHVISION_LICENSE","initialization-source.json"):
            shutil.copyfile(OLD_RESULT/name,output/name)
        for stage in ("inner","final"):
            (output/"training-logs"/stage).mkdir(parents=True)
            for name in ("complete.json","epochs.jsonl"):
                shutil.copyfile(work/stage/name,output/"training-logs"/stage/name)
        save(path,{"weights_sha256":sha(output/"detector.pt"),"fit_count":completed["fit_count"],"epochs":completed["epochs"],
                   "code_sha256":sha(Path(__file__)),"regression_labels_sha256":sha(work/"regression-labels.json"),
                   "baseline_prediction_sha256":{str(p):sha(p) for p in (OLD_RESULT/"predictions.json",EXTRA/"predictions.json")},
                   "holdout_previously_inspected":True,"no_post_regression_tuning":True,"score_threshold":.5,"crop_margin":.08,
                   "classifiers_sha256":{str(p):sha(p) for p in sorted(CLASSIFIERS.glob("panel-*.pt"))},
                   "classifiers_retrained":False,"app_integrated":False})
        (output/"crops").mkdir()
        (output/"original-thumbnails").mkdir()
    protocol=read(path)
    if sha(output/"detector.pt")!=protocol["weights_sha256"] or sha(Path(__file__))!=protocol["code_sha256"]:
        raise ValueError("Frozen evaluation implementation/weights changed")
    if sha(work/"regression-labels.json")!=protocol["regression_labels_sha256"]:
        raise ValueError("Frozen regression labels changed")
    for name,digest in {**protocol["baseline_prediction_sha256"],**protocol["classifiers_sha256"]}.items():
        if sha(name)!=digest:
            raise ValueError("Frozen baseline predictions/classifiers changed")
    return protocol


def cpu_evaluate(work,output):
    os.environ["CUDA_VISIBLE_DEVICES"]="-1"
    torch.set_num_threads(4)
    if torch.cuda.is_available():
        raise ValueError("GPU must be disabled for this CPU run")
    protocol=freeze_evaluation(work,output)
    if (output/"results.json").exists():
        print(json.dumps({"already_completed":"cpu_evaluation"}))
        return
    rows=read(work/"regression-labels.json")
    before=baseline()
    started=time.perf_counter()
    locator=PlacardLocatorV2(output/"detector.pt","cpu")
    classifiers=FrozenPanelClassifiers(CLASSIFIERS,"cpu")
    loading=time.perf_counter()-started
    photo=load_photo(rows[0]["source"])
    found=locator.locate(photo)
    if found["valid"]:
        classifiers.predict(photo.crop(found["crop_box"]))
    predictions=read(output/"progress.json") if (output/"progress.json").exists() else []
    if [r["code"] for r in predictions]!=[r["code"] for r in rows[:len(predictions)]]:
        raise ValueError("Checkpoint belongs to different regression records")
    for row in rows[len(predictions):]:
        if sha(row["source"])!=row["sha256"]:
            raise ValueError("Original photo changed")
        t0=time.perf_counter()
        photo=load_photo(row["source"])
        t1=time.perf_counter()
        found=locator.locate(photo)
        t2=time.perf_counter()
        predicted={"shape":None,"size":None,"shape_scores":{},"size_scores":{}}
        crop=photo.crop(found["crop_box"]) if found["crop_box"] else None
        if found["valid"]:
            predicted=classifiers.predict(crop)
        t3=time.perf_counter()
        iou,_=overlap(found["box"],row["box"])
        _,coverage=overlap(found["crop_box"],row["box"])
        result={"code":row["code"],"role":row["role"],"source":row["source"],"source_sha256":row["sha256"],
                "truth_shape":row["shape"],"truth_size":row["size"],"reference_box":row["box"],
                "location":found,"iou":iou,"coverage":coverage,**predicted,
                "shape_correct":predicted["shape"]==row["shape"],"size_correct":predicted["size"]==row["size"],
                "baseline":before[row["code"]],"timing":{"decode":t1-t0,"locate":t2-t1,"classify":t3-t2}}
        predictions.append(result)
        if crop is not None:
            crop.save(output/"crops"/f"{row['code']}.png")
            ImageDraw.Draw(photo).rectangle(found["crop_box"],outline="#0a8cdf" if found["valid"] else "#bf8600",width=5)
        photo.thumbnail((270,480))
        photo.save(output/"original-thumbnails"/f"{row['code']}.jpg",quality=92)
        if len(predictions)%25==0 or len(predictions)==len(rows):
            atomic(output/"progress.json",predictions)
            print(json.dumps({"evaluated":len(predictions),"total":len(rows),
                              "pending_or_wrong":[r["code"] for r in predictions if not(r["shape_correct"] and r["size_correct"])]}),flush=True)
    freeze_evaluation(work,output)
    save(output/"predictions.json",predictions)
    save(output/"results.json",{"all":summarize(predictions),
                               "by_role":{role:summarize([r for r in predictions if r["role"]==role]) for role in sorted({r["role"] for r in predictions})},
                               "by_shape":{shape:summarize([r for r in predictions if r["truth_shape"]==shape]) for shape in sorted({r["truth_shape"] for r in predictions})},
                               "baseline_count":924,"baseline_both_correct":sum(r["baseline"]["shape_correct"] and r["baseline"]["size_correct"] for r in predictions),
                               "baseline_reference_coverage_99":sum(r["baseline"]["location"]["valid"] and r["baseline"]["coverage"]>=.99 for r in predictions),
                               "model_loading_seconds":loading,"gpu_disabled":True,"torch_threads":4,
                               "evaluation_protocol_sha256":sha(output/"evaluation-protocol.json"),"weights_sha256":protocol["weights_sha256"]})
    print(json.dumps(summarize(predictions)),flush=True)


def paired_cpu(work,output):
    os.environ["CUDA_VISIBLE_DEVICES"]="-1"
    torch.set_num_threads(4)
    freeze_evaluation(work,output)
    if torch.cuda.is_available():
        raise ValueError("GPU must be disabled")
    predictions=read(output/"predictions.json")
    rng=random.Random(20261026)
    selected=[]
    for shape in ("adult_male","adult_female","child"):
        selected+=rng.sample([r for r in predictions if r["truth_shape"]==shape and r["code"] not in ("2950","1345")],10)
    selected+=[next(r for r in predictions if r["code"]==code) for code in ("2950","1345")]
    locators={"baseline":PlacardLocator(OLD_RESULT/"detector.pt","cpu"),"new":PlacardLocatorV2(output/"detector.pt","cpu")}
    classifiers=FrozenPanelClassifiers(CLASSIFIERS,"cpu")
    warm=load_photo(selected[0]["source"])
    for locator in locators.values():
        found=locator.locate(warm)
        if found["valid"]:
            classifiers.predict(warm.crop(found["crop_box"]))
    timings=[]
    differences=[]
    for i,row in enumerate(selected):
        for name in (("baseline","new") if i%2==0 else ("new","baseline")):
            t0=time.perf_counter()
            photo=load_photo(row["source"])
            t1=time.perf_counter()
            found=locators[name].locate(photo)
            t2=time.perf_counter()
            predicted=classifiers.predict(photo.crop(found["crop_box"])) if found["valid"] else {"shape":None,"size":None}
            t3=time.perf_counter()
            timings.append({"code":row["code"],"model":name,"decode":t1-t0,"locate":t2-t1,"classify":t3-t2,"total":t3-t0})
            if name=="new" and (predicted["shape"],predicted["size"])!=(row["shape"],row["size"]):
                differences.append(row["code"])
    result={"count":32,"threads":4,"gpu_disabled":True,"order":"Alternating baseline/new per photograph",
            "new_argmax_differences":differences,"rows":timings,
            "means":{name:{key:float(np.mean([r[key] for r in timings if r["model"]==name])) for key in ("decode","locate","classify","total")} for name in locators}}
    save(output/"cpu-paired-benchmark.json",result)
    print(json.dumps({"cpu_paired_means":result["means"],"differences":differences}),flush=True)


@torch.inference_mode()
def stress(work,output):
    freeze_evaluation(work,output)
    torch.set_num_threads(4)
    if not torch.cuda.is_available():
        raise ValueError("GPU is required for this separate stress run")
    old=build_detector().cuda().eval()
    old.load_state_dict(torch.load(OLD_RESULT/"detector.pt",map_location="cuda",weights_only=True))
    new=build_v2().cuda().eval()
    new.load_state_dict(torch.load(output/"detector.pt",map_location="cuda",weights_only=True))
    rows=read(work/"regression-labels.json")
    finished=read(output/"stress-progress.json") if (output/"stress-progress.json").exists() else []
    if [r["code"] for r in finished]!=[r["code"] for r in rows[:len(finished)]]:
        raise ValueError("Stress resume records differ")
    for row in rows[len(finished):]:
        photo=load_photo(row["source"])
        item={"code":row["code"],"variants":{}}
        for name,model,frame in (("baseline",old,(360,640)),("new",new,(SIDE,SIDE))):
            pixels=np.asarray(photo.resize(frame,Image.Resampling.LANCZOS))
            scale=np.array([frame[0]/photo.width,frame[1]/photo.height]*2)
            box=np.array(row["box"])*scale
            variants=[change(pixels,box,kind) for kind in KINDS]
            inputs=[rgb_tensor(Image.fromarray(v)).cuda() for v,_ in variants]
            predicted=model(inputs)
            values=[]
            for kind,p,(_,truth) in zip(KINDS,predicted,variants,strict=True):
                p["boxes"]*=p["boxes"].new_tensor([photo.width/frame[0],photo.height/frame[1]]*2)
                found=choose_detection(p,photo.width,photo.height)
                true_original=(np.asarray(truth)/scale).tolist()
                iou,_=overlap(found["box"],true_original)
                _,coverage=overlap(found["crop_box"],true_original)
                values.append({"kind":kind,"score":found["score"],"valid":found["valid"],"iou":iou,"coverage":coverage})
            item["variants"][name]=values
        finished.append(item)
        if len(finished)%25==0 or len(finished)==len(rows):
            atomic(output/"stress-progress.json",finished)
            print(json.dumps({"stress_photos":len(finished),"total":len(rows)}),flush=True)
    summary={}
    for name in ("baseline","new"):
        all_cases=[v for r in finished for v in r["variants"][name]]
        summary[name]={"photos":len(finished),"inputs":len(all_cases),"valid":sum(r["valid"] for r in all_cases),
                       "confident_wrong_location":sum(r["valid"] and r["iou"]<.5 for r in all_cases),
                       "reference_coverage_99":sum(r["valid"] and r["coverage"]>=.99 for r in all_cases),
                       "all_9_accepted_photos":sum(all(v["valid"] for v in r["variants"][name]) for r in finished),
                       "all_9_covered_photos":sum(all(v["valid"] and v["coverage"]>=.99 for v in r["variants"][name]) for r in finished),
                       "worst_score_mean":float(np.mean([min(v["score"] for v in r["variants"][name]) for r in finished])),
                       "by_kind":{kind:{"valid":sum(r["valid"] for r in all_cases if r["kind"]==kind),
                                        "count":sum(r["kind"]==kind for r in all_cases)} for kind in KINDS}}
    save(output/"stress-results.json",{"summary":summary,"rows":finished,
                                      "derived_inputs_not_new_photos":True,"no_training_or_threshold_change":True,
                                      "stress_test":"Nine deterministic perturbations of each model's preprocessed whole-photo input"})
    print(json.dumps(summary),flush=True)


def verify(work,output):
    protocol=freeze_evaluation(work,output)
    source=check(work,arrays=True)
    rows=read(work/"regression-labels.json")
    predictions=read(output/"predictions.json")
    if [r["code"] for r in rows]!=[r["code"] for r in predictions]:
        raise ValueError("Prediction/label identity mismatch")
    if summarize(predictions)!=read(output/"results.json")["all"]:
        raise ValueError("Result summary differs from individual predictions")
    for r in read(work/"training-labels.json")+rows:
        if sha(r["source"])!=r["sha256"]:
            raise ValueError("Original source changed")
    for r in predictions:
        if r["location"]["crop_box"]:
            with Image.open(output/"crops"/f"{r['code']}.png") as crop:
                if not np.array_equal(np.asarray(crop),np.asarray(load_photo(r["source"]).crop(r["location"]["crop_box"]))):
                    raise ValueError("Exported crop is not actual inference input/candidate")
    fitted={r["code"] for r in read(work/"training-labels.json")}
    if fitted&{r["code"] for r in rows}:
        raise ValueError("Training/regression overlap")
    if sha(work/"final/model.pt")!=protocol["weights_sha256"]:
        raise ValueError("Exported model differs from final fitted model")
    save(output/"verification.json",{"original_fronts_checked":len(fitted)+len(rows),"fit_regression_code_overlap":False,
                                      "existing_models_code_labels_unchanged":True,"new_training_cache_verified":True,
                                      "actual_crops_pixel_checked":len(predictions),"frozen_classifiers":True,
                                      "weights_sha256":protocol["weights_sha256"],"training_protocol_sha256":sha(work/"protocol.json"),
                                      "score_threshold":source["score_threshold"],"app_integrated":False,
                                      "new_packages_installed":False,"external_writes":False,"commit_push":False})
    print(json.dumps({"verified_fronts":len(fitted)+len(rows),"actual_crops":len(predictions),"weights":protocol["weights_sha256"]}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("phase",choices=["cpu","paired","stress","verify"])
    parser.add_argument("--work",type=Path,default=WORK)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    args=parser.parse_args()
    {"cpu":cpu_evaluate,"paired":paired_cpu,"stress":stress,"verify":verify}[args.phase](args.work,args.output)


if __name__=="__main__":
    main()
