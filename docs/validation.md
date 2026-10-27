# 검증 안내

기준 버전: **0.4.1**. 이 문서는 구현·테스트의 범위를 설명하며 모든 AAF·DAW의 호환성이나 무결점 보안을 보증하지 않습니다.

## 합성 데이터만 사용

`tests/conftest.py`가 pytest의 임시 directory에 AAF object graph를 만들며, 필요한 경우 짧은 무음 PCM을 함께 생성합니다. 실제 음악·고객 프로젝트·crash dump·개인 기록은 repository에 포함하지 않습니다.

## 재현

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2.2
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest tests
.venv/bin/python -m ruff check src tests
.venv/bin/aaf-trans --help
.venv/bin/aaf-trans --version
```

pytest는 source coverage 80% 이상의 gate를 유지합니다. 의존성 범위와 실제 설치 버전은 다를 수 있으므로 보안 감사는 설치한 정확한 버전을 기준으로 별도로 확인해야 합니다.

## 공개 준비 시 실행 기록

2026-10-27, 새 virtual environment에서 `pip install -e '.[dev]'` 후 확인했습니다. 개발 환경의 pip도 `26.2.2`로 갱신해 추가 점검했습니다. 아래 결과는 제품 코드의 synthetic 회귀이며, dependency advisory 감사는 설치된 전체 환경을 기준으로 별도로 수행합니다.

| 항목 | 관찰 결과 |
| --- | --- |
| 실행 환경 | macOS, Python 3.14.4 |
| 설치 버전 | pip 26.2.2, pyaaf2 1.7.1, pytest 9.1.1, pytest-cov 7.1.0, Ruff 0.17.3, coverage 7.17.0 |
| 전체 synthetic 회귀 | 51 passed, 0 failed, 0 skipped |
| source coverage | 89.25%, 80% gate 통과 |
| Ruff | `src tests` 검사 통과 |
| 의존성 일관성 | `pip check` 통과 |
| CLI | `--help`와 `--version` 실행, `aaf-trans 0.4.1` 확인 |
| wheel | 빌드 및 자체 MIT·pyaaf2 라이선스 고지 포함 확인 |

원본 보호 회귀의 최초 실행에서는 새 테스트 6건이 실패했고, batch 사전 검사를 적용한 뒤 통과했습니다. 추가한 Windows 예약 marker 세 경우와 Unicode 정규화가 동등한 출력의 overwrite·dry-run 두 경우도 수정 전 실패를 확인했습니다. 이 기록은 advisory 감사나 실제 DAW 호환성 확인을 대신하지 않습니다.

## 주요 확인 항목

- 잘못된 Sequence 길이, edit protocol·timecode·sound track·PCM 메타데이터
- 원본 SHA256 보존, 변환 전후 timeline·essence 지문
- 모호하거나 자동 수정 범위 밖인 AAF의 거부
- 기존 출력 보존, opt-in 교체, dry-run의 쓰기 없음
- batch 출력이 다른 입력과 같은 경로나 symlink·hardlink identity인 경우 모든 변환 전 거부
- batch의 대소문자·Unicode 정규화만 다른 출력, 기존 output identity 중복, 경로·예약·제어 문자를 포함한 잘못된 marker 거부
- 정상 batch, JSON 결과, 개별 파일 오류의 분리

## 남은 경계

macOS의 Python 3.14에서 synthetic 회귀를 실행했습니다. 지원 최소 Python 3.10·Windows·Linux의 전체 matrix, 악성 parser fuzz, 실제 DAW import·청취·대용량 파일 자원 검증은 이번 공개 작업에서 재실행하지 않았습니다. 해당 환경의 확인이 필요하면 원본을 보존한 별도 사본을 사용하세요.
