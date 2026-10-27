# AAF Trans

**AAF 타임라인과 내장 PCM 메타데이터를 검사·정규화하는 Python CLI입니다.**

변환은 원본의 복제본에서 수행하며, 결과의 타임라인 구조와 내장 오디오 지문을 비교합니다. 지원 범위 밖이거나 모호한 상태는 추정해서 수정하지 않습니다.

버전: **0.4.2** · Python **3.10 이상** · [MIT](LICENSE) · [보안 경계](SECURITY.md) · [검증 안내](docs/validation.md)

## 기능

- AAF `Sequence.Length`와 component·transition의 길이 관계 검사·정규화
- top-level composition의 `UsageCode`, 누락된 Primary Timecode 보강
- sound track 이름·`PhysicalTrackNumber` 및 내장 PCM container·codec 메타데이터 검사
- 단일 파일·directory·여러 입력의 batch 처리, JSON 결과, dry-run
- 원본과 출력의 경로·파일 별칭 충돌 검사, 기존 출력 교체의 명시적 opt-in
- 변환 전후 object count·media timeline 구조·내장 essence fingerprint 비교

이 도구는 손상된 AAF 전체를 복구하는 프로그램이나 오디오 편집기가 아닙니다. 파일 파싱·메타데이터 검사 성공과 실제 DAW에서의 import 성공은 별개의 확인 단계입니다.

## 설치

저장소를 내려받은 directory에서 별도의 virtual environment를 사용합니다. PyPI에 이 프로젝트가 배포되었다는 뜻은 아닙니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2.2
.venv/bin/python -m pip install .
.venv/bin/aaf-trans --version
```

Windows에서는 `.venv/Scripts/python.exe`와 `.venv/Scripts/aaf-trans.exe`를 사용합니다. 이번 공개 검증은 macOS의 Python 3.14에서 수행했으며 Windows 실행은 별도 검증하지 않았습니다.

## 사용

```sh
# 검사: 수정이 필요한 파일도 exit code 1을 반환합니다.
aaf-trans inspect session.aaf
aaf-trans inspect session.aaf --essence-hash --json

# 원본 옆의 session.nuendo.aaf를 생성합니다.
aaf-trans convert session.aaf

# 쓰기 없이 수정 예정 항목만 확인합니다.
aaf-trans convert session.aaf --dry-run

# batch 출력 directory와 누락된 timecode의 FPS를 지정합니다.
aaf-trans convert first.aaf second.aaf -o converted --timecode-fps 25
aaf-trans convert input-directory --recursive -o converted --json
```

- `--timecode-fps`는 누락된 Primary Timecode에만 사용하며 `24`, `25`, `30`을 지원합니다. 기본값은 `25`입니다. 기존 timecode를 일괄 변경하지 않습니다.
- directory 탐색은 기본 출력 marker인 `.nuendo`가 붙은 파일을 제외합니다. 명시적으로 선택한 파일은 검사 대상이지만 batch의 다른 입력을 출력으로 삼을 수 없습니다.
- `--marker`로 출력 접미사를 바꿀 수 있습니다. 빈 값·`.`·`..`·경로 구분자·제어 문자·Windows 예약 문자는 허용하지 않습니다.
- 기존 출력은 기본적으로 보존합니다. 필요한 경우에만 `--overwrite`를 사용하세요. 이 옵션도 선택한 입력 파일이나 그 symlink·hardlink 별칭을 덮어쓰도록 허용하지 않습니다.
- batch 출력 이름이 대소문자나 Unicode 정규화(NFC/NFD)만 다른 경우도 거부합니다. case-sensitive 환경에서도 같은 기준을 적용해 다른 볼륨·운영체제로 이동할 때의 충돌을 줄입니다.
- `--skip-essence-hash`는 오디오 지문 검증을 생략합니다. 성능보다 검증이 중요하다면 기본값을 유지하세요.

`inspect`는 진단 통과 시 `0`, 수정 필요·입력 오류 시 `1`을 반환합니다. `convert`는 모든 입력 처리 성공 시 `0`, 사전 검사 또는 개별 파일 처리 실패가 있으면 `1`입니다. 인자 사용 오류는 `2`입니다. 경로 충돌 사전 검사는 batch 전체 쓰기 전에 끝나지만, 변환 중 개별 파일 오류는 다른 파일의 성공을 rollback하지 않습니다.

## 개발과 검증

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2.2
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest tests
.venv/bin/python -m ruff check src tests
```

테스트는 임시 directory에서 synthetic AAF와 짧은 PCM 데이터를 생성합니다. 실제 음악·고객 파일·DAW crash dump·로그를 포함하지 않습니다. 검증 범위와 재현 방법은 [검증 안내](docs/validation.md)에 있습니다.

## 구조

| 경로 | 책임 |
| --- | --- |
| `src/aaf_trans/cli.py` | 인자·batch 사전 검사·사람용/JSON 결과 |
| `src/aaf_trans/core.py` | 파일 검사·정규화·출력·불변 조건 검증 |
| `src/aaf_trans/edit_protocol.py` | composition·timecode·sound track 규칙 |
| `src/aaf_trans/models.py` | 진단·파일 보고서·변환 결과 |
| `tests/` | 합성 fixture와 성공·실패·원본 보존 회귀 |

## 라이선스와 비제휴

직접 작성한 코드는 [MIT](LICENSE)입니다. AAF 읽기·쓰기는 외부 라이브러리 `pyaaf2`에 의존하며 해당 저작권·라이선스를 [별도로 고지](THIRD_PARTY_NOTICES.md)합니다. 내장 parser나 AAF 표준을 독자 개발한 것으로 주장하지 않습니다.

Nuendo와 다른 DAW·제품명은 각 소유자의 상표입니다. 이 프로젝트는 해당 업체의 공식 도구가 아니며 제휴·보증을 받았다는 뜻이 아닙니다. 모든 DAW·AAF 변형의 호환성을 보장하지 않습니다.
