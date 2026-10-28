# 공개 저장소와 키 관리

이 저장소는 공개입니다. 코드·문서는 보존하되 사용자 데이터와 인증 정보는 공개하지 않습니다.

## 공개하는 파일

- 앱/학습/평가 Python 소스, QML, 테스트, 빌드·설치 스크립트, 요구사항과 문서.
- `resources/ai/manifest.json`의 가중치 파일명·해시, 비밀이 없는 사이즈 대응표와 라이선스.
- `resources/standard_sheet.json`의 표준 양식 서식·헤더·수식·보조목록. 실제 옷 데이터와 예시 입력은 제거했습니다.
- `docs/design/approved-blue-review.png`: 사용자가 선택한 생성 UI 시안. 옷 사진·문구는 합성 예시이며 개인 사진·작업 데이터가 아닙니다. 다른 사진·실험 이미지의 Git 제외 규칙은 유지합니다.

## 로컬에만 보관하는 파일

- 원본/복사 사진, 라벨, 실험 결과·검수 HTML: `model-results/`, `.local/`, 개인 사진 폴더.
- 학습 가중치 `.pt`와 기타 모델 바이너리. 현재 모델 4개와 카테고리 참조 조각/마스크도 Git에서 제외.
- `dist-releases/` 등의 설치파일·실행파일, 빌드 캐시, 가상환경과 내려받은 도구.
- 작업 DB, 작업 JSON, 로그인 토큰, OAuth 연결 JSON, 개인 키, `.env` 파일.

위 항목은 `.gitignore`로 제외합니다. `.gitignore`는 이미 커밋한 파일을 회수하는 기능이 아니므로 커밋 전 변경 목록을 확인합니다.
기존 로컬 파일은 삭제하거나 이동하지 않습니다. 공개 저장소는 개인 자료의 백업을 대신하지 않습니다.

## 인증 정보와 엑셀 전환

2.3부터 현재 앱의 Google 로그인·API·연결 JSON 설정은 제거했습니다. 로컬 `.xlsx` 파일에 날짜별 시트를 저장하며 AI 분석과 저장에 유료 API 키나 계정이 필요하지 않습니다. 새 설치물에는 Google 인증 모듈·연결 JSON·개인 토큰을 포함하지 않습니다.
이전 Google 실험의 로컬 인증 파일과 작업 기록은 삭제하거나 읽지 않습니다. 이전 Qt Widgets 원형 코드와 과거 검증 이력은 보존하지만 2.3 실행 경로·설치물에서 사용하지 않습니다.

- Google 관리자 연결 JSON과 실제 키·토큰을 코드, README, 테스트, GitHub 이슈나 로그에 넣지 않습니다.
- 개발용 비밀 값은 Git에서 제외한 로컬 설정이나 환경 변수로 전달합니다. 현재 앱이 `.env`를 자동으로 읽는다는 뜻은 아닙니다.
- 향후 GitHub Actions에서 빌드에 필요한 비밀을 쓸 경우 저장소의 Actions Secrets를 사용합니다. 이는 앱 사용자의 로그인 정보 저장소가 아닙니다.
- **공용 유료 API 키를 exe에 넣거나 빌드 때 주입해도 사용자가 추출할 수 있습니다.** 그런 키를 도입한다면 사용자별 키 입력이나 별도 서버 방식이 필요합니다. 현재 요청에서는 서버·유료 API를 추가하지 않습니다.
- Google Desktop OAuth는 비밀을 안전하게 숨길 수 없는 공개 클라이언트입니다. 웹 서버용 비밀 키와 혼동하지 않고, 브라우저 로그인·PKCE·사용자별 토큰을 사용합니다.
- 유효한 키가 공개되면 파일 삭제만으로 끝내지 않고 발급 서비스에서 폐기·재발급하고 이력 노출 여부를 확인합니다.

공식 자료: [GitHub Secrets](https://docs.github.com/en/actions/concepts/security/secrets),
[Google OAuth와 설치형 앱](https://developers.google.com/identity/protocols/oauth2/).

## 새 개발 PC에서 필요한 별도 파일

소스 clone에는 `.venv`, NSIS, 사진·라벨, 빌드, 모델 4개·카테고리 참조 자산이 포함되지 않습니다.
이전 2.1의 실행 차단 오류와 현재 릴리스의 검증 결과는 [진행 요약](PROGRESS_SUMMARY.md)에서 구분합니다.

AI 분석/설치 빌드를 재구성하려면 다음이 추가로 필요합니다.

1. Python 3.12와 `requirements.txt`/`requirements-dev.txt`의 고정 의존성을 프로젝트 내부 가상환경에 준비.
2. CPU용 PyTorch `2.11.0+cpu`, torchvision `0.26.0+cpu`를 같은 실행/빌드 환경에 준비. CUDA 비교 실험용 `requirements-ab.txt`와 구분.
3. 보관한 `detector.pt`, `panel-shape.pt`, `panel-size.pt`를 `resources/ai/`에 복원하고 manifest 해시를 확인.
4. 최종 카테고리 가중치와 고정 template 참조 자산을 별도로 준비하고 `scripts/prepare_category_runtime.py --model-dir <최종 실험 폴더> --references <고정 references 폴더> --output resources/ai/category`로 복원합니다. 모델 SHA-256은 `d48df504df994fa3414dba4e4efd979cd0080162883e7dd66b703e3d63b569fa`입니다. 비밀이 없는 실행 정책·해시 manifest만 Git에 보존합니다.
5. 설치 빌드는 프로젝트 내부의 NSIS 제작 도구가 추가로 필요. 일반 사용 PC에 Python/NSIS를 따로 설치하는 구성은 아님.
6. 엑셀 저장은 `requirements.txt`에 고정한 openpyxl/et-xmlfile을 포함하며 Excel 설치나 Google 연결 설정이 필요하지 않음.

학습·평가 재현에는 별도 보관한 사진·라벨·분할과 실험 설정도 필요합니다. 코드만으로 기존 실험 결과가 즉시 재현된다고 보장하지 않습니다.
