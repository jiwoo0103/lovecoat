from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageFilter
from torch import nn
from torch.utils.data import DataLoader, Dataset

from coatdesk_ab.data import load_photo, read, save, sha
from coatdesk_locator.dataset import box_affine, collate
from coatdesk_locator.model import build_detector, choose_detection, overlap

WORK = Path(".local/panel-detector-v2-20261026")
OUTPUT = Path("model-results/20261026-panel-detector-v2")
OLD_WORK = Path(".local/panel-detector-20261025")
OLD_RESULT = Path("model-results/20261025-panel-detector")
EXTRA = Path("model-results/20261025-panel-extra-validation")
CLASSIFIERS = Path("model-results/20261024-four-model-comparison/models")
BASE = Path(".local/ab-assets/ssdlite320_mobilenet_v3_large_coco-a79551df.pth")
SIDE = 448
SEED = 20261026
MAX_EPOCHS = 40
BATCH = 12
KINDS = ("original", "x_left", "x_right", "y_up", "y_down", "zoom_out", "zoom_in", "darker", "brighter")


def build_v2(base=None):
    model = build_detector(base)
    model.transform.fixed_size = (SIDE, SIDE)
    return model


def protected_files():
    paths = [BASE, OLD_RESULT/"detector.pt", OLD_WORK/"training/inner/best.pt", OLD_WORK/"box-labels.json",
             EXTRA/"labels.jsonl", EXTRA/"protocol.json", *(CLASSIFIERS/f"panel-{t}.pt" for t in ("shape","size"))]
    paths += sorted(Path("coatdesk_ab").glob("*.py"))+sorted(Path("coatdesk_locator").glob("*.py"))
    return {str(p):sha(p) for p in paths}


def native_affine(frame, native_size, angle, scale, dx=0., dy=0.):
    """Preserve portrait geometry even though the cached detector input is square."""
    width,height = native_size
    matrix = np.eye(3)
    matrix[:2] = cv2.getRotationMatrix2D((width/2,height/2),angle,scale)
    matrix[:2,2] += [dx*width,dy*height]
    to_frame = np.diag([frame[0]/width,frame[1]/height,1.])
    return (to_frame@matrix@np.linalg.inv(to_frame))[:2].astype(np.float32)


def change(pixels, box, kind):
    height,width = pixels.shape[:2]
    matrix = np.array([[1.,0.,0.],[0.,1.,0.]],dtype=np.float32)
    if kind in ("darker","brighter"):
        factor = .85 if kind=="darker" else 1.15
        return np.asarray(ImageEnhance.Brightness(Image.fromarray(pixels)).enhance(factor)),list(box)
    if kind.startswith("x_"):
        matrix[0,2] = width*(.01 if kind=="x_right" else -.01)
    elif kind.startswith("y_"):
        matrix[1,2] = height*(.01 if kind=="y_down" else -.01)
    elif kind.startswith("zoom_"):
        matrix = cv2.getRotationMatrix2D((width/2,height/2),0,.9 if kind=="zoom_out" else 1.1).astype(np.float32)
    elif kind=="original":
        return pixels.copy(),list(box)
    else:
        raise ValueError("Unknown stress variant")
    moved = box_affine(box,matrix).tolist()
    if not (0<=moved[0]<moved[2]<=width and 0<=moved[1]<moved[3]<=height):
        raise ValueError("Stress variant would clip the placard")
    return cv2.warpAffine(pixels,matrix,(width,height),flags=cv2.INTER_LINEAR,borderValue=(255,255,255)),moved


def configure():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    torch.backends.cudnn.benchmark=False
    torch.backends.cudnn.deterministic=True


def prepare(work):
    configure()
    work.mkdir(parents=True,exist_ok=False)
    old = read(OLD_WORK/"box-labels.json")["records"]
    fit = [dict(r,cache_index=i) for i,r in enumerate([r for r in old if r["role"] in ("inner_fit","inner_dev")])]
    dev = [r for r in fit if r["role"]=="inner_dev"]
    holdout = [r for r in old if r["role"] in ("outer_validation","additional_test")]
    extra = [json.loads(s) for s in (EXTRA/"labels.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(fit)==645 and len(dev)==129 and len(holdout)+len(extra)==924
    if {r["code"] for r in fit}&{r["code"] for r in holdout+extra}:
        raise ValueError("Regression photographs must never enter fitting data")
    save(work/"training-labels.json",fit)
    save(work/"regression-labels.json",holdout+extra)
    data=np.lib.format.open_memmap(work/"photos.npy",mode="w+",dtype=np.uint8,shape=(645,3,SIDE,SIDE))
    for i,r in enumerate(fit):
        if sha(r["source"])!=r["sha256"]:
            raise ValueError("Original changed")
        photo=load_photo(r["source"]).resize((SIDE,SIDE),Image.Resampling.LANCZOS)
        data[i]=np.asarray(photo).transpose(2,0,1)
    data.flush()
    old_array=np.load(OLD_WORK/"photos.npy",mmap_mode="r")
    stress=[]
    newer=np.lib.format.open_memmap(work/"dev.npy",mode="w+",dtype=np.uint8,shape=(len(dev)*len(KINDS),3,SIDE,SIDE))
    older=np.lib.format.open_memmap(work/"baseline-dev.npy",mode="w+",dtype=np.uint8,shape=(len(dev)*len(KINDS),3,640,360))
    for i,r in enumerate(dev):
        w,h=r["coordinate_size"]
        box=np.array(r["box"])*np.array([SIDE/w,SIDE/h]*2)
        old_box=np.array(r["box"])*np.array([360/w,640/h]*2)
        pixels=data[r["cache_index"]].transpose(1,2,0)
        previous=old_array[r["index"]].transpose(1,2,0)
        for j,kind in enumerate(KINDS):
            index=i*len(KINDS)+j
            image,moved=change(pixels,box,kind)
            old_image,old_moved=change(previous,old_box,kind)
            newer[index]=image.transpose(2,0,1)
            older[index]=old_image.transpose(2,0,1)
            stress.append({"index":index,"code":r["code"],"kind":kind,"box":moved,"old_box":old_moved})
    newer.flush()
    older.flush()
    save(work/"dev-labels.json",stress)
    code=[Path("coatdesk_locator_v2")/n for n in ("__init__.py","experiment.py","pipeline.py")]
    protocol={"version":"whole-panel-448-v2","seed":SEED,"side":SIDE,"batch":BATCH,"max_inner_epochs":MAX_EPOCHS,
              "fit_inner":516,"dev_inner":129,"fit_final":645,"regression_count":924,
              "input":"Full photograph EXIF preview directly resized to 448x448; actual crop uses original preview coordinates",
              "initialization":"Fresh generic COCO weights for both inner and final fitting; not old final/inner weights",
              "architecture":"Same SSDLite MobileNetV3-Large, six anchor maps, scale .10..70, background+placard",
              "score_threshold":.5,"crop_margin":.08,"frozen_classifiers":True,
              "augmentation":"Native-aspect rotation +-10deg, scale .65..1.35, translation +-8%, flip, brightness .70..1.30, contrast .75..1.35, occasional mild blur and independent coat brightness",
              "stress_variants":list(KINDS),"dev_stress_count":len(stress),
              "selection":"Max dev stress valid reference-box coverage>=99%, then identity coverage, then stress IoU>=.75, then mean IoU",
              "held_out_use":"Previously inspected 924-photo regression only; no fitting or epoch selection. Not a new blind test.",
              "source_lock":protected_files(),"code_sha256":{str(p):sha(p) for p in code},
              "data_sha256":{n:sha(work/n) for n in ("photos.npy","dev.npy","baseline-dev.npy","training-labels.json","dev-labels.json","regression-labels.json")},
              "torch":torch.__version__,"gpu":torch.cuda.get_device_name(0),"app_integrated":False}
    save(work/"protocol.json",protocol)
    print(json.dumps({"prepared_fit":645,"dev":129,"stress":len(stress),"regression_not_for_fit":924}),flush=True)


def check(work, arrays=False):
    protocol=read(work/"protocol.json")
    if protected_files()!=protocol["source_lock"]:
        raise ValueError("Existing models, code or labels changed")
    for path,digest in protocol["code_sha256"].items():
        if sha(path)!=digest:
            raise ValueError("Frozen experiment implementation changed")
    for name,digest in protocol["data_sha256"].items():
        if (arrays or not name.endswith(".npy")) and sha(work/name)!=digest:
            raise ValueError("Frozen experiment data changed")
    return protocol


class Fitting(Dataset):
    def __init__(self,work,final=False):
        self.array=np.load(work/"photos.npy",mmap_mode="r")
        records=read(work/"training-labels.json")
        if any(r["role"] not in ("inner_fit","inner_dev") for r in records):
            raise ValueError("Regression/held-out records are forbidden in fitting labels")
        roles={"inner_fit","inner_dev"} if final else {"inner_fit"}
        self.rows=[r for r in records if r["role"] in roles]

    def __len__(self):
        return len(self.rows)

    def __getitem__(self,index):
        row=self.rows[index]
        pixels=self.array[row["cache_index"]].transpose(1,2,0).copy()
        width,height=row["coordinate_size"]
        box=np.array(row["box"],dtype=np.float32)*np.array([SIDE/width,SIDE/height]*2,dtype=np.float32)
        matrix=native_affine((SIDE,SIDE),(width,height),random.uniform(-10,10),random.uniform(.65,1.35),random.uniform(-.08,.08),random.uniform(-.08,.08))
        moved=box_affine(box,matrix)
        if 0<=moved[0]<moved[2]<=SIDE and 0<=moved[1]<moved[3]<=SIDE:
            pixels=cv2.warpAffine(pixels,matrix,(SIDE,SIDE),flags=cv2.INTER_LINEAR,borderValue=(255,255,255))
            box=moved
        if random.random()<.5:
            pixels=np.ascontiguousarray(pixels[:,::-1])
            box=np.array([SIDE-box[2],box[1],SIDE-box[0],box[3]],dtype=np.float32)
        image=ImageEnhance.Brightness(Image.fromarray(pixels)).enhance(random.uniform(.70,1.30))
        image=ImageEnhance.Contrast(image).enhance(random.uniform(.75,1.35))
        if random.random()<.20:
            image=image.filter(ImageFilter.GaussianBlur(random.uniform(.1,.6)))
        pixels=np.asarray(image).copy()
        if random.random()<.25:
            y=min(SIDE,math.ceil(box[3]+.12*(box[3]-box[1])))
            if y<SIDE:
                context=ImageEnhance.Brightness(Image.fromarray(pixels[y:])).enhance(random.uniform(.65,1.35))
                pixels[y:]=np.asarray(context)
        image=torch.from_numpy(pixels.transpose(2,0,1).copy()).float()/255
        return image,{"boxes":torch.from_numpy(box.copy()).reshape(1,4),"labels":torch.ones(1,dtype=torch.int64)}


@torch.inference_mode()
def validate(model,work,baseline=False):
    model.eval()
    records=read(work/"dev-labels.json")
    array=np.load(work/("baseline-dev.npy" if baseline else "dev.npy"),mmap_mode="r")
    frame=(360,640) if baseline else (SIDE,SIDE)
    rows=[]
    for start in range(0,len(records),16):
        group=records[start:start+16]
        images=[torch.from_numpy(array[r["index"]].copy()).float().cuda()/255 for r in group]
        predictions=model(images)
        for r,p in zip(group,predictions,strict=True):
            found=choose_detection(p,*frame)
            truth=r["old_box"] if baseline else r["box"]
            iou,_=overlap(found["box"],truth)
            _,coverage=overlap(found["crop_box"],truth)
            rows.append({"code":r["code"],"kind":r["kind"],"valid":found["valid"],"score":found["score"],"iou":iou,"coverage":coverage})
    def summary(group):
        return {"count":len(group),"valid":sum(r["valid"] for r in group),
                "coverage_99":sum(r["valid"] and r["coverage"]>=.99 for r in group),
                "iou_75":sum(r["valid"] and r["iou"]>=.75 for r in group),
                "mean_iou":float(np.mean([r["iou"] for r in group]))}
    return {"stress":summary(rows),"original":summary([r for r in rows if r["kind"]=="original"]),"rows":rows}


def fit(work,stage):
    configure()
    check(work,arrays=True)
    folder=work/stage
    folder.mkdir(exist_ok=True)
    if (folder/"complete.json").exists():
        print(json.dumps({"already_completed":stage}),flush=True)
        return
    if not (work/"baseline-dev.json").exists():
        model=build_detector().cuda()
        model.load_state_dict(torch.load(OLD_WORK/"training/inner/best.pt",map_location="cuda",weights_only=True))
        save(work/"baseline-dev.json",validate(model,work,True))
        del model
        torch.cuda.empty_cache()
    configure()
    epochs=MAX_EPOCHS if stage=="inner" else read(work/"inner/complete.json")["best_epoch"]
    dataset=Fitting(work,stage=="final")
    batches=DataLoader(dataset,batch_size=BATCH,shuffle=True,num_workers=0,collate_fn=collate)
    model=build_v2(BASE).cuda()
    optimizer=torch.optim.AdamW([{"params":model.backbone.parameters(),"lr":.0002},
                                {"params":model.head.parameters(),"lr":.001}],weight_decay=.0001)
    scaler=torch.amp.GradScaler("cuda")
    history,best,best_epoch,first,previous=[],(-1,-1,-1,-1.),0,1,0.
    if (folder/"last.pt").exists():
        state=torch.load(folder/"last.pt",map_location="cpu",weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scaler.load_state_dict(state["scaler"])
        random.setstate(state["python_rng"])
        np.random.set_state(state["numpy_rng"])
        torch.set_rng_state(state["torch_rng"])
        torch.cuda.set_rng_state_all(state["cuda_rng"])
        history,best,best_epoch,first,previous=state["history"],state["best"],state["best_epoch"],state["epoch"]+1,state["seconds"]
        print(json.dumps({"resumed":stage,"next_epoch":first}),flush=True)
    started=time.perf_counter()
    for epoch in range(first,epochs+1):
        t0=time.perf_counter()
        factor=.1+.9*(1+math.cos(math.pi*(epoch-1)/MAX_EPOCHS))/2
        for group,lr in zip(optimizer.param_groups,[.0002,.001],strict=True):
            group["lr"]=lr*factor
        model.train()
        for module in model.modules():
            if isinstance(module,nn.BatchNorm2d):
                module.eval()
        loss_sum,seen=0.,0
        for images,targets in batches:
            images=[v.cuda(non_blocking=True) for v in images]
            targets=[{k:v.cuda(non_blocking=True) for k,v in t.items()} for t in targets]
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda",dtype=torch.float16):
                losses=model(images,targets)
                loss=sum(losses.values())
            if not bool(torch.isfinite(loss)):
                raise ValueError("Non-finite loss; previous completed checkpoint retained")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(),5.)
            scaler.step(optimizer)
            scaler.update()
            loss_sum+=float(loss.detach())*len(images)
            seen+=len(images)
        record={"stage":stage,"epoch":epoch,"fit_count":seen,"loss":loss_sum/seen}
        if stage=="inner":
            validation=validate(model,work)
            record["dev"]={k:v for k,v in validation.items() if k!="rows"}
            score=(validation["stress"]["coverage_99"],validation["original"]["coverage_99"],validation["stress"]["iou_75"],validation["stress"]["mean_iou"])
            if score>best:
                best,best_epoch=score,epoch
                torch.save(model.state_dict(),folder/"best.tmp")
                os.replace(folder/"best.tmp",folder/"best.pt")
                (folder/"best-dev.json").write_text(json.dumps(validation),encoding="utf-8")
        record["seconds"]=time.perf_counter()-t0
        history.append(record)
        checkpoint={"model":model.state_dict(),"optimizer":optimizer.state_dict(),"scaler":scaler.state_dict(),
                    "epoch":epoch,"history":history,"best":best,"best_epoch":best_epoch,
                    "python_rng":random.getstate(),"numpy_rng":np.random.get_state(),"torch_rng":torch.get_rng_state(),
                    "cuda_rng":torch.cuda.get_rng_state_all(),"seconds":previous+time.perf_counter()-started}
        torch.save(checkpoint,folder/"last.tmp")
        os.replace(folder/"last.tmp",folder/"last.pt")
        with (folder/"epochs.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(record)+"\n")
        print(json.dumps(record),flush=True)
    if stage=="final":
        torch.save(model.state_dict(),folder/"model.pt")
    save(folder/"complete.json",{"stage":stage,"epochs":epochs,"best_epoch":best_epoch if stage=="inner" else epochs,
                                  "best":best if stage=="inner" else None,"history":history,
                                  "fit_count":len(dataset),"seconds":previous+time.perf_counter()-started})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("phase",choices=["prepare","inner","final"])
    parser.add_argument("--work",type=Path,default=WORK)
    args=parser.parse_args()
    if args.phase=="prepare":
        prepare(args.work)
    else:
        fit(args.work,args.phase)


if __name__=="__main__":
    main()
