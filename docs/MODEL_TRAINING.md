# 외투 글자·머리 판넬 모델

2026-10-24에 첫 두 모델을 실제 학습하고 `model-results/20261024-first`에 내보냈습니다.
현재 앱·설치파일은 기존 인식 엔진을 사용합니다. 모델 학습과 앱 연결은 별도 작업입니다.

## 학습과 평가

바탕화면 `외투_사이즈_학습데이터_20261024/labels.jsonl`의 `front_path`만 입력으로 사용했습니다.
옆·뒤 사진이나 검수용 축소 이미지를 모델 학습에 넣지 않았습니다. 원본 정면에서 글자와 머리 모양을 각각 추출했습니다.

- 기존 학습 645벌 안에서 중복 후보 그룹을 유지해 내부 학습 516벌·내부 점검 129벌을 나눴습니다.
- 아동 170처럼 학습 자료가 1벌인 표기는 내부 학습에 남겼습니다.
- 전처리·분류 설정은 내부 자료로 점검하고, 최종 모델은 기존 학습 645벌 전체로 다시 학습했습니다.
- 최종 검증 162벌은 가중치 학습·설정 선택에 사용하지 않았습니다. 증강 자료도 원래 분할 안에서만 만들었습니다.
- 글자: 공식 `tessdata_best/eng` 실수 모델을 Tesseract LSTM으로 추가 학습하고 정수 추론 모델로 내보냈습니다.
  학습 645벌의 글자를 -5/0/+5도로 회전한 1,935개 줄 자료, 최대 4,000 샘플 반복을 사용했습니다.
  베이스 모델에 보존된 학습률을 사용했으며, 재학습 가능한 원본 모델과 실제 명령·로그·해시를 기록했습니다.
- 판넬: 글자·옷이 제외된 머리 영역의 HOG 특징으로 가중 Softmax 회귀를 Adam으로 학습했습니다.
  학습 645벌을 5개 각도로 증강한 3,225개 입력을 사용했습니다. 정규화 강도는 내부 129벌로 선택했습니다.
- 판넬 정답은 성인 남성/성인 여성/아동입니다. 아동 남아는 학습 대상 성별이 아니라 사용자 임시 규칙입니다.

| 최종 검증 162벌 | 정답 | 비율 |
|---|---:|---:|
| 기존 OCR · 같은 새 글자 영역과 실행 설정 | 151 | 93.2% |
| 추가 학습한 글자 모델 | 162 | 100.0% |
| 학습한 머리 판넬 모델 | 161 | 99.4% |
| 글자와 판넬을 모두 맞힌 항목 | 161 | 99.4% |

3037번의 성인 남성 판넬을 여성으로 분류했습니다. 이 최종 검증 결과를 본 뒤 모델·추출 규칙을 다시 조정하지 않았습니다.
한 번의 자료 묶음에서 측정한 결과이므로 다른 촬영일·판넬·조명에서의 일반 정확도로 해석하지 않습니다.
성인 2XS·아동 60/70 자료가 없고 아동 170의 최종 검증 표본은 1벌입니다.
사이즈는 판독한 원문을 보존하며, 아동 숫자는 엑셀 대응표로 변환합니다. 검증하지 않은 표기와 머리/문자 모순은 확인 필요로 남깁니다.

## 산출물 확인

2026-10-24 추가 검증은 별도 `model-results/20261024-extra-validation`에 보존합니다.
초반 0001~1108번과 기존 807벌을 배제하고, 몸통 해시·최근접 사진 육안 비교에서 중복 의심 17벌을 더 제외한 성인 283벌입니다.
글자 275/283(97.2%), 남녀 판넬 282/283(99.6%), 동시 275/283입니다. 기존 OCR은 같은 추출 영역에서 266/283(94.0%)입니다.
예측 후 원본 재검수에서 1407번 수기 성별 라벨 1건을 여성→남성으로 바로잡았습니다.
초기 성적(글자 275·판넬 281·동시 274), 초기 라벨과 원시 예측은 `_initial-evaluation`에 보존하며,
`protocol.json`은 초기/정정 라벨의 SHA-256과 정정 사유를 기록합니다. 모델 가중치·추출 규칙·원시 예측은 바꾸지 않았습니다.
283벌 전체를 다시 육안 대조했지만 독립 담당자의 라벨 검수는 아닙니다.
아동과 3XL은 원본에 남은 미사용 표본이 없으며 XS/2XL도 각 2벌뿐입니다.
글자 오답 8벌은 영역 추출 문제를 보였고 실제 입력을 `error_evidence/corrected-errors-contact.jpg`에 보존했습니다.
추가 자료는 학습에 사용하지 않았습니다. 이후 이 자료를 개선에 사용한다면 별도의 새 최종 검증 자료를 확보해야 합니다.

- `model-results/20261024-first/results.html` 또는 `RESULTS.md`: 사람이 읽는 결과.
- `coat_size.traineddata`: 추가 학습한 글자 모델.
- `head_shape.npz`: 판넬 분류 가중치. NumPy의 `allow_pickle=False`로 읽습니다.
- `model-card.json`, `dataset-split.json`: 버전·원본 해시·최종/내부 분할·제약.
- `validation-results.json`, `validation-predictions.csv`: 최종 검증 162벌의 실제 출력.
- `ocr-training.log`, `ocr-training-command.json`, `head-training.json`: 실제 학습 기록.
- `end-to-end-check.json`: 내보낸 모델을 다시 읽고 정면 원본부터 추출·인식·아동 규칙을 실행한 확인 결과.
- `dataset-integrity-after-training.json`: 학습 후 전체 원본 사진·라벨·분할 무결성 검사.

기존 사진·라벨·SQLite·Google 시트·기존 `vendor/tesseract/tessdata/eng.traineddata`와 설치파일은 변경하지 않았습니다.
사진을 외부로 전송하지 않았습니다. 네트워크에서는 학습 가능한 공식 모델 약 15MB와 라이선스만 받았습니다.

## 개발 환경과 실행

학습 전용 의존성은 `requirements-training.txt`이며 앱 런타임 의존성과 분리합니다.
이번 환경은 Python 3.12.14, NumPy 2.5.4, Pillow 13.0.0, OpenCV 5.0.0.93 패키지,
프로젝트 안 Tesseract 5.5.3.20260724를 사용했습니다. 전역 패키지나 시스템 설정을 변경하지 않았습니다.
Tesseract 학습과 이 소규모 판넬 모델은 CPU로 실행했으며 추가 GPU 라이브러리를 설치하지 않았습니다.

재현 시 새로운 작업 폴더를 만들고, 그 안 `base/eng.traineddata`와 `base/LICENSE`, `base/source.json`을 준비합니다.
`base-model-source.json`에 기록된 공식 URL·SHA-256과 일치하는 파일을 사용하세요.
이미 존재하는 결과 폴더를 덮어쓰지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.train_models prepare --work <새작업폴더> --dataset <데이터폴더>
.\.venv\Scripts\python.exe -m scripts.train_models lstmf --work <새작업폴더>
.\.venv\Scripts\python.exe -m scripts.train_models shape --work <새작업폴더>
.\.venv\Scripts\python.exe -m scripts.train_models ocr --work <새작업폴더> --name ocr-inner --iterations 2000
.\.venv\Scripts\python.exe -m scripts.train_models evaluate --work <새작업폴더> --ocr-dir <새작업폴더>\ocr-inner --role inner_dev --name inner-evaluation
.\.venv\Scripts\python.exe -m scripts.train_models ocr --work <새작업폴더> --name ocr-final --iterations 4000 --final
.\.venv\Scripts\python.exe -m scripts.train_models evaluate --work <새작업폴더> --ocr-dir <새작업폴더>\ocr-final --name outer-evaluation
.\.venv\Scripts\python.exe -m scripts.export_models --work <새작업폴더> --output <새모델폴더>
```

Windows의 Tesseract 학습 목록은 LF 줄바꿈으로 기록합니다. 줄바꿈에 CR이 섞이면 `.lstmf` 파일명을 읽지 못하는 이 빌드의 동작을 실제 확인했습니다.
정답 줄 상자는 각 글자에 전체 글자 줄 영역을 연결하는 Tesseract LSTM 형식을 사용합니다.

한 벌을 명시적으로 시험하려면:

```powershell
.\.venv\Scripts\python.exe -m scripts.predict_trained --models model-results\20261024-first --front <정면사진경로>
```

공식 근거: [Tesseract 학습](https://tesseract-ocr.github.io/tessdoc/tess5/TrainingTesseract-5.html),
[학습 가능한 모델](https://tesseract-ocr.github.io/tessdoc/Data-Files),
[글자 줄 상자 형식](https://tesseract-ocr.github.io/tessdoc/tess4/Make-Box-Files.html).
