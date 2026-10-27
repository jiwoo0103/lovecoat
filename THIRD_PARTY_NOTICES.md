# 사용한 소프트웨어

이 앱은 무료 로컬 구성요소를 사용합니다. 공개 재배포 시 각 구성요소의 라이선스 조건을 확인하세요.
빌드에 포함된 라이선스 원문은 `_internal/licenses`에서 확인할 수 있습니다.
2.1 AI 빌드는 CPU 전용 PyTorch/torchvision과 로컬 학습 가중치를 포함합니다.
Tesseract 고지는 기존 2.0 배포에 해당하며, 2.1 인식 경로에는 사용하지 않습니다.

- PySide6 / Qt for Python: LGPLv3/GPLv3 또는 상용 라이선스. 이 빌드는 동적 라이브러리를 포함하는 폴더형 배포입니다. <https://doc.qt.io/qtforpython-6/licenses.html>
- NumPy: BSD 3-Clause. <https://numpy.org/doc/stable/license.html>
- Pillow: MIT-CMU. <https://github.com/python-pillow/Pillow/blob/main/LICENSE>
- PyTorch 2.11.0 CPU: BSD 계열 라이선스 및 포함 구성요소 고지. `_internal/licenses/torch`에 원문을 포함합니다. <https://github.com/pytorch/pytorch/blob/main/LICENSE>
- torchvision 0.26.0 CPU: BSD 3-Clause. 판넬 위치·ResNet 분류 모델 구조와 초기 가중치에 사용합니다. `_internal/licenses/torchvision` 및 `_internal/resources/ai/TORCHVISION_LICENSE`를 포함합니다. <https://github.com/pytorch/vision/blob/main/LICENSE>
- openpyxl 3.1.5 / et-xmlfile 2.0.0: MIT. Excel 파일 생성과 새 시트 추가에 사용합니다. <https://openpyxl.readthedocs.io/>, <https://et-xmlfile.readthedocs.io/>
- Tesseract 5.5.3 / tessdata_fast 영어 모델: Apache 2.0.
  <https://github.com/tesseract-ocr/tesseract>, <https://github.com/tesseract-ocr/tessdata_fast>
- Tesseract Windows 바이너리: UB Mannheim에서 만든 공식 프로젝트 배포 첨부 파일.
  설치 프로그램을 실행하지 않고 압축만 추출했습니다.
  <https://github.com/UB-Mannheim/tesseract/wiki>
  런타임에는 Leptonica 및 함께 배포되는 네이티브 라이브러리가 포함됩니다.
- PyInstaller: GPL 2.0 및 배포 예외 조항. <https://pyinstaller.org/en/stable/license.html>
- NSIS 3.13: 설치 프로그램 제작에 사용합니다. zlib/libpng 라이선스 및 포함 구성요소 고지: <https://nsis.sourceforge.io/License>. 빌드 도구는 설치 대상 PC에 설치하지 않습니다.

사진은 사용자가 제공한 원본을 로컬에서 읽습니다. 사진 자체는 실행 파일 배포 폴더에 포함되지 않습니다.
