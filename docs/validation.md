# 검증 안내

기준 버전: **0.4.3**. 읽기 전용 입력의 변환 회귀를 수정한 공개 source입니다. 이 문서는 구현·테스트의 범위를 설명하며 모든 AAF·DAW의 호환성이나 무결점 보안을 보증하지 않습니다.

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

## CI 재현과 등록 확인 · 2026-10-28

CI와 같은 Python 3.13 계열의 새 가상 환경에서 dependency 설치, `pip check`, Ruff, pytest와 CLI version 명령을 실행했습니다. Python 3.13.7에서 **51 passed, 0 failed, 0 skipped**, source coverage **89.25%**로 기존 80% gate를 통과했습니다. 로컬 macOS의 결과이며 GitHub의 Linux 실행 결과와 구별합니다.

기존 workflow는 actionlint 1.7.12에서 진단 없이 통과했습니다. 진단 전 head `08721d2`에서는 Actions가 활성화돼 있고 모든 action을 허용하지만 workflow·run·head check가 0개인 상태를 확인했습니다. 이 사실만으로 원인을 YAML 오류나 기본 branch 부재로 단정하지 않습니다.

동일한 job을 `python-ci.yml`로 옮기고 기존 push·pull request trigger에 `workflow_dispatch`를 추가했습니다. 새 파일 경로를 한 번 등록해 관찰하고, 이후 수동 재실행 경로를 제공합니다. 수동 trigger는 workflow 파일이 기본 branch에 있어야 사용할 수 있습니다. [GitHub 공식 조건](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

단일 진단 push `40e3953` 뒤 workflow가 active로 등록되고 [push CI](https://github.com/zeztto/aaf-trans-cli/actions/runs/40533769818)와 [PR CI](https://github.com/zeztto/aaf-trans-cli/actions/runs/40533773365)가 모두 성공했습니다. 두 실행은 읽기 전용 입력 수정 전의 0.4.2 source이며 각각 51 passed, coverage 89.25%를 확인했습니다. 이 관찰로 과거 미등록의 내부 원인까지 확정하지는 않습니다.

## 0.4.3 읽기 전용 입력 회귀

기존 `copy2`가 원본의 읽기 전용 mode를 변환용 복제본에도 전달해 쓰기 단계가 실패하던 경로를 수정했습니다. 변환용 임시 파일의 owner-write 권한을 유지하며 content만 복사합니다. POSIX 출력은 `0600`이고 원본의 권한·수정 시각·확장 속성을 출력에 복제하지 않습니다.

`0444`, `0400`, `0644` 입력 세 경우를 추가해 **54 passed, 0 failed, 0 skipped**, coverage **89.25%**를 확인했습니다. macOS의 Python 3.14 환경과, 수정 source를 다시 설치한 별도 Python 3.13.7 환경에서 전체 회귀와 Ruff가 통과했습니다. CLI는 `aaf-trans 0.4.3`입니다. 별도 synthetic CLI 검사에서도 세 입력의 content·mode·inode·mtime가 유지되고, 출력·내장 essence와 임시 파일 정리를 확인했습니다.

권한 mode 회귀 세 경우는 POSIX에서만 실행하며 다른 운영체제에서는 명시적인 이유와 함께 skip합니다. POSIX 표기를 보강한 뒤 해당 세 경우와 Ruff를 다시 확인했습니다.

새 수정의 GitHub CI는 해당 commit의 check로 별도 판단합니다. 앞의 0.4.2 CI 성공을 0.4.3의 실행 결과로 대체하지 않습니다.

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
| CLI | `--help`와 `--version` 실행, `aaf-trans 0.4.2` 확인 |
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

macOS의 Python 3.13·3.14 synthetic 회귀와 GitHub Linux/Python 3.13 CI를 위의 source revision별로 구별합니다. 지원 최소 Python 3.10·Windows·전체 운영체제 matrix, 악성 parser fuzz, 실제 DAW import·청취·대용량 파일 자원 검증은 별도입니다. POSIX mode 검사는 ACL·Windows 권한이나 hostile directory race를 포괄하지 않습니다. 해당 환경의 확인이 필요하면 원본을 보존한 별도 사본을 사용하세요.
