"""Read-only source check; all outputs stay inside the supplied verification folder."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from coatdesk.domain import Task, save_task
from coatdesk.photos import crop_head, digest, discover, execute_copy, plan_copy
from coatdesk.recognition import find_tesseract, recognize

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
items, warnings = discover(args.source)
before = {p: digest(Path(p)) for item in items for p in item.photos.values()}
task = Task(str(args.source), items, selected=[0])
copy_result = execute_copy(plan_copy(items, args.source, args.output / "flat"))
repeat_result = execute_copy(plan_copy(items, args.source, args.output / "flat"))
contact = Image.new("RGB", (4 * 280, 4 * 330), "white")
draw = ImageDraw.Draw(contact)
ocr = []
for i, garment in enumerate(items):
    crop = crop_head(garment.front_path, task.default_roi)
    crop.thumbnail((270, 290))
    x, y = (i % 4) * 280, (i // 4) * 330
    contact.paste(crop, (x, y + 25))
    draw.text((x + 5, y + 5), garment.code, fill="black")
    garment.suggestion = recognize(garment, task.default_roi, [], find_tesseract())
    ocr.append({"code": garment.code, **{k: v for k, v in garment.suggestion.items() if k != "shape"}})
    print(json.dumps(ocr[-1], ensure_ascii=True), flush=True)
contact.save(args.output / "heads.jpg")
save_task(args.output / "sample.coat.json", task)
after = {p: digest(Path(p)) for p in before}
assert before == after, "Source photos changed!"
report = {"garments": len(items), "photos": len(before), "warnings": warnings,
          "copy": copy_result, "repeat": repeat_result, "source_unchanged": before == after, "ocr": ocr}
(args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "ocr"}, ensure_ascii=True))
