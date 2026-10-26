"""CPU-only inference for the three shipped, frozen models."""
from __future__ import annotations

import json
import os

import numpy as np
import torch
from PIL import Image

from coatdesk_ab.data import SHAPES, SIZES, letterbox, load_photo
from coatdesk_ab.network import PhotoClassifier
from coatdesk_locator.model import build_detector, choose_detection


def tensor(photo):
    return torch.from_numpy(np.asarray(photo).transpose(2,0,1).copy()).float()/255


class Models:
    def __init__(self,directory):
        torch.set_num_threads(max(1,min(4,os.cpu_count() or 1)))
        self.detector=build_detector().cpu().eval()
        self.detector.transform.fixed_size=(448,448)
        self.detector.load_state_dict(torch.load(directory/"detector.pt",map_location="cpu",weights_only=True))
        self.classifiers={}
        for task,classes in (("shape",SHAPES),("size",SIZES)):
            model=PhotoClassifier(len(classes)).cpu().eval()
            model.load_state_dict(torch.load(directory/f"panel-{task}.pt",map_location="cpu",weights_only=True))
            self.classifiers[task]=model
        mapping=json.loads((directory/"size_mapping.json").read_text(encoding="utf-8"))
        self.child_sizes={n:r["size_label"] for r in mapping["child_correspondence"] for n in r["visible_numbers"]}

    @torch.inference_mode()
    def analyze(self,path,thresholds,manual_roi=None):
        photo=load_photo(path)
        if manual_roi:
            box=[round(manual_roi[0]*photo.width),round(manual_roi[1]*photo.height),round(manual_roi[2]*photo.width),round(manual_roi[3]*photo.height)]
            location={"valid":True,"score":None,"crop_box":box,"manual":True}
        else:
            output=self.detector([tensor(photo.resize((448,448),Image.Resampling.LANCZOS))])[0]
            output["boxes"]*=output["boxes"].new_tensor([photo.width/448,photo.height/448]*2)
            location=choose_detection(output,photo.width,photo.height)
            location["valid"]=bool(location["crop_box"] and location["score"]>=thresholds["panel"])
            location["manual"]=False
        box=location.get("crop_box")
        roi=[box[0]/photo.width,box[1]/photo.height,box[2]/photo.width,box[3]/photo.height] if box else None
        result={"values":{},"candidates":{},"scores":{},"held":[],"roi":roi,"location":location,"review":True}
        if location["score"] is not None:
            result["scores"]["panel"]=location["score"]
        if not location["valid"]:
            result["held"]=["판넬 위치"]
            result["note"]=f'판넬 위치 확인 필요 · 점수 {location["score"]:.1%} / 기준 {thresholds["panel"]:.0%}. 후보 사진을 확인하거나 영역을 지정하세요.'
            return result
        square,_=letterbox(photo.crop(box))
        value=tensor(square).unsqueeze(0)
        value=(value-value.new_tensor([.485,.456,.406])[None,:,None,None])/value.new_tensor([.229,.224,.225])[None,:,None,None]
        probabilities={task:model(value).softmax(1)[0].tolist() for task,model in self.classifiers.items()}
        shape=SHAPES[int(np.argmax(probabilities["shape"]))]
        raw=SIZES[int(np.argmax(probabilities["size"]))]
        result["scores"].update(shape=max(probabilities["shape"]),size=max(probabilities["size"]))
        proposed={"age":"유아동" if shape=="child" else "성인","gender":"여성" if shape=="adult_female" else "남성"}
        result["candidates"].update(proposed)
        if result["scores"]["shape"]>=thresholds["shape"]:
            result["values"].update(proposed)
        else:
            result["held"].append("성별/성인·아동")
        size=self.child_sizes.get(raw,raw)
        result["candidates"]["size"]=size
        consistent=(raw.isdigit() and shape=="child") or (not raw.isdigit() and shape!="child")
        if result["scores"]["size"]>=thresholds["size"] and consistent:
            result["values"]["size"]=size
        else:
            result["held"].append("사이즈" if consistent else "연령·사이즈 불일치")
        result["probabilities"]={"shape":dict(zip(SHAPES,probabilities["shape"],strict=True)),"size":dict(zip(SIZES,probabilities["size"],strict=True))}
        result["raw_size"]=raw
        notes=["확인 필요: "+", ".join(result["held"])] if result["held"] else []
        if shape=="child":
            notes.append("아동 성별은 임시 남아 규칙이며 옷을 보고 확인하세요")
        if raw.isdigit():
            notes.append(f"아동 표기 {raw} → 사이즈 {size}")
        result["note"]=" · ".join(notes) or "기준 통과 · 제안 적용 후 사람이 확인 완료하세요."
        return result
