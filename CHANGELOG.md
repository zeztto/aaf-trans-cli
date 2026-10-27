# 변경 기록

## CI 유지관리 - 2026-10-28

- 기존 CI를 `python-ci.yml`로 옮기고 push·pull request trigger와 함께 수동 `workflow_dispatch` trigger를 제공합니다. 적용 조건과 등록 확인 범위를 검증 안내에 기록했습니다.
- 새 Python 3.13 환경에서 기존 CI 명령을 재현해 51개 회귀와 coverage 89.25%를 확인했습니다. 앱 source·test·제품 버전은 변경하지 않았습니다.
- GitHub의 workflow 등록·실행 성공은 로컬 검사 결과와 별도로 확인합니다.

## 0.4.2 - 2026-10-27

- GitHub CI 등록을 정상 push 이벤트로 다시 시도하기 위해 버전 metadata와 문서를 갱신했습니다.
- 기능 코드·회귀 테스트·의존성은 0.4.1과 동일합니다. CI의 등록·성공을 이 버전 변경만으로 주장하지 않습니다.

## 0.4.1 - 2026-10-27

- AAF 검사·메타데이터 정규화 CLI의 공개용 source snapshot을 구성했습니다.
- batch 변환 전에 모든 입력과 출력의 경로·symlink·hardlink 별칭을 비교해 다른 입력을 출력으로 교체할 수 있던 문제를 수정했습니다.
- 동일하거나 대소문자·Unicode 정규화(NFC/NFD)만 다른 출력 이름, 같은 기존 output identity를 사전 거부합니다.
- 입력 보존·별칭·case-only 충돌·잘못된 marker 회귀 테스트를 추가했습니다.
- 출력 marker의 경로 구분자·제어 문자·Windows 예약 문자를 보수적으로 거부합니다.
- 실제 작업 파일·운영 기록을 제외하고 synthetic 테스트와 일반화된 사용·보안 문서를 제공합니다.
- 자체 코드의 MIT와 pyaaf2의 별도 저작권·라이선스를 명시했습니다.
- 설치·개발 안내에 보안 수정된 pip 26.2.2 bootstrap 단계를 명시했습니다.
