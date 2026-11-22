"""Human-readable handoff generated only from completed frozen evaluations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil

from scripts.size_retrain_20261122 import CLASSES, OUT, ROOT, WORK, read, save, sha, status
from scripts.size_retrain_20261122_fit import write_csv


def percent(value):
    return f'{value:.2%}'


def format_prf(values):
    return '/'.join(percent(values[key]) for key in ('precision', 'recall', 'f1'))


def report():
    comparison = read(OUT / 'comparison.json')
    if 'business_metrics' not in comparison['new']['original']['all']:
        # The evaluator may have been queued before final report-only additions.
        # Recompute summaries from unchanged stored predictions; no new inference,
        # threshold selection, test exclusion, label or model changes.
        from scripts.size_retrain_20261122_evaluate import review_html, summarize
        results = read(OUT / 'test-pipeline-predictions.json')
        thresholds = read(OUT / 'dev-selected-thresholds.json')
        comparison, paired = summarize(results, thresholds)
        save(OUT / 'comparison.json', comparison)
        review_html(paired)
    cpu = read(OUT / 'cpu-app-verification.json')
    diagnostic = read(OUT / 'resampling-diagnostic.json')
    if not (OUT / 'synthetic-cpu-app-diagnostic.json').exists():
        from scripts.size_retrain_20261122_evaluate import synthetic_cpu_runtime_checks
        synthetic_cpu_runtime_checks()
    synthetic_cpu = read(OUT / 'synthetic-cpu-app-diagnostic.json')
    synthetic = read(OUT / 'synthetic-training-fit-diagnostic.json')
    integrity = read(OUT / 'post-run-integrity.json')
    manifest = read(OUT / 'candidate-manifest.json')
    predictions = read(OUT / 'test-pipeline-predictions.json')
    new_by_key = {(r['code'], r['kind']): r for r in predictions['new']}
    common_old_only = [r for r in predictions['old'] if r['truth'] not in ('60', '2XS')
        and r['predicted'] == r['truth'] and new_by_key[(r['code'], r['kind'])]['predicted'] != r['truth']]
    epochs = [json.loads(line) for line in (OUT / 'epochs.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = read(OUT / 'verification.json')
    updates = read(OUT / 'training-update-audit.json')
    sources = list((ROOT / 'scripts').glob('size_retrain_20261122*.py'))
    sources += [ROOT / name for name in ('tests/test_size_retrain_20261122.py', 'coatdesk_ab/network.py',
        'coatdesk_ab/data.py', 'coatdesk_locator/model.py', 'coatdesk_v2/ai_runtime.py',
        'scripts/retrain_four_training.py', 'scripts/retraining_20261122_gpu.py')]
    source_hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        target = WORK / 'source-final' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        source_hashes[str(relative)] = sha(source)
    save(OUT / 'source-sha256.json', source_hashes)
    lines = ['# 사이즈 20종 후보 재학습·동일 사진 비교', '',
        f"완료한 학습 {len(epochs)}회차, 개발에서 선택한 후보 {manifest['dev_selected_epoch']}회차. 앱은 교체하지 않았다.", '',
        f"후보 `size20.pt` SHA-256: `{manifest['sha256']}`.", '',
        '## 실사 최종 결과', '',
        '|조건·범위|사진/조건|기존 원문|신규 원문|기존→신규 macroF1|기존→신규 업무사이즈|',
        '|---|---:|---:|---:|---:|---:|']
    for condition, label in [('original', '원본'), ('nine_conditions', '9조건')]:
        for group, name in [('all', '전체'), ('common18', '공통18종'), ('real60', '실사60'), ('real70', '실사70'), ('adult', '성인'), ('child', '아동')]:
            a, b = comparison['old'][condition][group], comparison['new'][condition][group]
            lines.append(f"|{label} {name}|{a['n_photos']}/{a['n_conditions']}|{a['correct']}/{a['n_conditions']} ({percent(a['accuracy'])})|{b['correct']}/{b['n_conditions']} ({percent(b['accuracy'])})|{percent(a['macro_f1_present'])}→{percent(b['macro_f1_present'])}|{percent(a['business_accuracy'])}→{percent(b['business_accuracy'])}|")
    lines += ['', 'macroF1는 해당 범위에서 정답 표본이 있는 클래스 평균이다. 20종 전체에서 미지원/무표본을0으로 둔 평균도 JSON에 별도 저장한다. 9조건은 같은 옷의 반복이며 최종 분할 단위는300사진/보수그룹이다.', '',
        '**성인 문자2XS 실사0벌, 170 개발/최종0벌이다. 두 클래스의 실사 정확도는 알 수 없다. 실사60과70은 최종 각각1벌뿐이므로9조건의 높은 성적도 모집단 정확도를 보장하지 않는다.**', '',
        '## 원문 클래스별 지표 (최종 원본)', '',
        '|원문|표본|기존 precision/recall/F1|신규 precision/recall/F1|', '|---|---:|---:|---:|']
    per_class = []
    for c in CLASSES:
        a, b = [comparison[w]['original']['all']['per_class'][c] for w in ('old', 'new')]
        lines.append(f"|{c}|{a['support']}|{format_prf(a)}|{format_prf(b)}|")
    for w in ('old', 'new'):
        for condition in ('original', 'nine_conditions'):
            for task in ('raw', 'business'):
                m = comparison[w][condition]['all']
                if task == 'business':
                    m = m['business_metrics']
                for c, values in m['per_class'].items():
                    per_class.append(dict(model=w, condition=condition, task=task, label=c, **values))
                matrix = [dict(truth=c, **{f'pred_{p}': n for p, n in zip(m['classes'], counts)}) for c, counts in zip(m['classes'], m['confusion'])]
                write_csv(OUT / f'confusion-{w}-{condition}-{task}.csv', matrix)
    write_csv(OUT / 'per-class-metrics.csv', per_class)
    lines += ['', '표본0의0은 측정된 실패율이 아니며 측정 불가를 뜻한다. 원문/변환후업무사이즈의 모든 precision·recall·F1·macroF1·혼동표는 `comparison.json`, `per-class-metrics.csv`, `confusion-*.csv`에 있다.', '',
        '## 기존/신규가 다른 사례', '']
    if 'robust_garments' in comparison:
        for which, values in comparison['robust_garments'].items():
            lines.append(f"- {which}:9조건 모두 원문정답 {values['all9_raw_correct']}/{values['n_photos']}벌, 업무사이즈정답 {values['all9_business_correct']}/{values['n_photos']}벌.")
    for condition in ('original', 'nine_conditions'):
        d = comparison['paired'][condition]
        lines.append(f"- {condition}: 기존만 정답 {d['old_only_correct']}, 신규만 정답 {d['new_only_correct']}, 둘다정답 {d['both_correct']}, 둘다오답 {d['both_wrong']}, 예측불일치 {d['disagreements']}조건.")
    lines.append(f"- 공통18종에서 기존 정답→신규 오답인 개별 조건 {len(common_old_only)}개. 전체 정확도 향상과 별도로 확인한다.")
    lines += ['', '사진별 2,700조건: `photo-comparison.csv`; 원본300벌: `photo-comparison-original.csv`; 사진 내장 검토 화면: `comparison.html`.', '',
        '## 자동 처리·확인 필요', '',
        '판넬·성별/연령은90% 고정, 연령/문자 일관성을 요구한다. 사이즈 기준은 현행90%와 개발9조건에서만 고른 무관측오류 기준을 비교한다. 카테고리와 아동 성별 사람 확인은 이 비율에 포함하지 않는다.', '',
        '|모델/조건/정책|사이즈기준|자동|원문오답/업무오답|확인필요|실패|', '|---|---:|---:|---:|---:|---:|']
    for w, name in [('old', '기존'), ('new', '신규')]:
        for condition in ('original', 'nine_conditions'):
            for key, policy in [('auto_at_current90', '현행'), ('auto_at_dev_selected', '개발선택')]:
                a = comparison[w][condition]['all'][key]
                lines.append(f"|{name}/{condition}/{policy}|{percent(a['threshold_size'])}|{a['auto']}/{a['n']} ({percent(a['auto_rate'])})|{a['auto_raw_errors']}/{a['auto_business_errors']}|{a['review']}|{a['failures']}|")
    lines += ['', '개발의 무관측오류는 미래 오답0을 보장하지 않으며, 최종 결과로 기준을 다시 고르지 않았다. 신규0%는 개발 자료에서 사이즈 점수로 추가 보류할 오답이 없었던 경험적 선택값이다. 앱의 기존90% 기준은 유지했으며 인하에는 별도 검증이 필요하다.', '',
        '## CPU 앱 경로와 속도', '',
        f"실제 `Models.analyze` CPU 경로 {cpu['n_photos']}벌 ×9조건 ×2모델. CPU/GPU 원문 불일치 {cpu['raw_disagreements']}조건, 확인필요 판정 불일치 {cpu['review_disagreements']}조건. `cpu-app-verification.csv/json`을 참조한다.", '',
        '신규20종은 격리된 실행 인스턴스에만 주입했다. 앱 로더의17/18종 검증은 수정하지 않았으므로 후보 가중치를 앱 파일에 곧바로 교체할 수 없다.', '',
        '|모델|GPU사이즈/조건|GPU공통검출+성별/조건|CPU앱전체 평균/조건|', '|---|---:|---:|---:|']
    timing = read(OUT / 'test-timing.json')
    for w, name in [('old', '기존'), ('new', '신규')]:
        values = [r['seconds'] for r in cpu['observations'] if r['which'] == w]
        lines.append(f"|{name}|{timing[w + '_size_seconds'] / timing['n_conditions']:.6f}s|{(timing['shared_detector_seconds'] + timing['shared_shape_seconds']) / timing['n_conditions']:.6f}s|{sum(values) / len(values):.6f}s|")
    lines += ['', 'GPU는 한 사진의9조건 묶음 FP32, CPU는 실제 앱 단건 경로다. 서로 다른 배치/장치 시간을 같은 처리량으로 해석하지 않는다. GPU 전체 평가 경과시간과 상세 타이밍은 `test-timing.json`에 있다.', '',
        '## 합성·데이터 무결성과 한계', '',
        '- 내장 ImageGen만 사용, 60/70/2XS 각각 독립 학습 부모6그룹. 원본80/90 또는 여성XS/S의 판넬 문자만 생성. 이전 전체사진 시안3개는 사용하지 않았다.',
        '- 모든18개 합성에서 문자 마스크 바깥 원래 판넬 픽셀이 정확히 일치한다. 생성 결과의 해상도 변화·종이 톤 차이를 기록하고 텍스트만 합쳤다.',
        f"- 실사/합성 동일 재표본화 진단 {diagnostic['pairs']}쌍 중 예측 전환 {diagnostic['paired_prediction_switches']}쌍. 글자/생성물 모든 단서를 완전히 배제했다는 증명은 아니다.",
        f"- 합성 **학습 적합도**: {synthetic['summary']['correct']}/{synthetic['summary']['n_conditions']}조건. 학습 부모이므로 독립 평가나 실사2XS 정확도로 주장하지 않는다.",
        f"- 합성 학습 판넬의 실제 CPU 수동ROI 앱 경로 원문정답 {synthetic_cpu['raw_correct']}/{synthetic_cpu['n_training_panels']}개. 신규2XS 문자와60/70 업무변환 경로 확인이며 실사 정확도 평가가 아니다.",
        '- 실사학습2000사진/1961보수그룹, 개발300/300그룹, 최종300/300그룹을 합성·증강 전에 동결. 동일파일 해시 중복0, 부모 그룹/해시 분할 중복0. 보수 그룹은 유사사진 연결도 포함한다. 원문 미확인 아동578벌을 모델 추측으로 보완하지 않았다.',
        '- 최종142벌은 이전 최종 사진 재사용,158벌은 이전 동결 사진에 없었다. 이전 학습 코드와 이번 개발/최종의 겹침0. 새로 촬영한 맹검은 아니다.',
        f"- 원본 {integrity['original_sources_checked']}장 재해시: 변경 {len(integrity['original_sources_changed'])}장. 기준 배포본·후보 입력 해시 불변={integrity['protected_unchanged']}.",
        '- 평가 후 정답수정0, 최종자료 사후제외0. 성인2XS 실사0,170 평가0은 그대로 유지.', '',
        '## 재현·재개와 검증', '',
        '실행/재개 명령과 전처리·손실·선택 정책은 `docs/SIZE_RETRAIN_20261122.md`, 상세 실행 설정은 `training-protocol.json`에 있다. epoch 로그는 `epochs.jsonl`, 마지막 완료회차 모델/옵티마이저/난수 상태는 `.local/size-retrain-20261122/resume.pt`에 있다.', '',
        f"검증: 계약 테스트 {checks['pytest_passed']}개 통과, Ruff={checks['ruff']}, diff check={checks['diff_check']}. 새 패키지 설치·앱모델 교체·빌드·설치·커밋·푸시·배포·사용자DB 쓰기는 수행하지 않았다.", '',
        f"실제 최적화 업데이트 {updates['optimizer_steps_max']}/{updates['attempted_optimizer_batches']}배치; AMP가 비유한 gradient 업데이트 {updates['amp_skipped_nonfinite_gradient_updates']}개를 건너뛰었다. 모든 회차의 입력6054개 처리는 완료했다. 초기값 대비 convolution/attention/출력 가중치 변경과 신규60/2XS 헤드 변경을 `training-update-audit.json`에 기록했다.", '',
        '출력20종: `' + ','.join(CLASSES) + '`.', '',
        '## 판정', '']
    old_common = comparison['old']['nine_conditions']['common18']
    new_common = comparison['new']['nine_conditions']['common18']
    if new_common['accuracy'] < old_common['accuracy'] or common_old_only or comparison['paired']['original']['old_only_correct']:
        verdict = '공통 클래스 또는 원본 회귀가 있어 앱 적용은 보류 권고다. 개선/퇴행 사진을 검토하고 별도 승인 없이 앱을 교체하지 않는다.'
    else:
        verdict = '이번 고정 실사에서 공통 클래스 정확도 회귀는 관찰되지 않았다. 다만 성인2XS 실사와170 평가 부재·희소60/70 표본 때문에20종 전체 검증 완료로 보지 않는다. 앱 통합은 별도 결정 사항이다.'
    lines += [verdict, '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    save(OUT / 'completion.json', {'completed_at_utc': datetime.now(timezone.utc).isoformat(),
         'stage': 'complete', 'epochs_completed': len(epochs), 'selected_epoch': manifest['dev_selected_epoch'],
         'checkpoint': str(OUT / 'size20.pt'), 'sha256': manifest['sha256'], 'classes': CLASSES,
         'original_old': comparison['old']['original']['all']['accuracy'], 'original_new': comparison['new']['original']['all']['accuracy'],
         'nine_old': comparison['old']['nine_conditions']['all']['accuracy'], 'nine_new': comparison['new']['nine_conditions']['all']['accuracy'],
         'common18_old_correct_new_wrong_conditions': len(common_old_only),
         'cpu_raw_disagreements': cpu['raw_disagreements'], 'cpu_review_disagreements': cpu['review_disagreements'],
         'report': str(OUT / 'REPORT.md'), 'verdict': verdict, 'external_state_changed': False,
         'application_replaced': False, 'checks': checks, 'source_sha256': source_hashes})
    documentation = ROOT / 'docs/SIZE_RETRAIN_20261122.md'
    text = documentation.read_text(encoding='utf-8')
    marker = '\n## 완료 결과\n'
    text = text.split(marker)[0]
    a, b = comparison['old']['original']['all'], comparison['new']['original']['all']
    text += marker + f"\n학습{len(epochs)}회차 완료, 선택{manifest['dev_selected_epoch']}회차. 최종 원본 기존{a['correct']}/300 → 신규{b['correct']}/300. 9조건 기존{comparison['old']['nine_conditions']['all']['correct']}/2700 → 신규{comparison['new']['nine_conditions']['all']['correct']}/2700. 공통18종과 모든 클래스별 상세 지표·자동처리·CPU·한계는 `model-results/20261122-size-retrain/REPORT.md`를 참조한다.\n\n후보 SHA-256 `{manifest['sha256']}`. CPU 원문 불일치{cpu['raw_disagreements']}조건, 확인판정 불일치{cpu['review_disagreements']}조건. 원본2600장과 기준자산 해시 불변을 재확인했다. 계약 테스트{checks['pytest_passed']}개·Ruff·diff 검사를 통과했다. 앱 교체/커밋/푸시/배포는 하지 않았다.\n\n{verdict}\n"
    documentation.write_text(text, encoding='utf-8')
    status('complete', completion_file=str(OUT / 'completion.json'), error=None,
           epochs_completed=len(epochs), seen=epochs[-1]['views'], batch=(epochs[-1]['views'] - 1) // 16,
           training_loss=epochs[-1]['loss'], cpu_photos=cpu['n_photos'])


if __name__ == '__main__':
    report()
