# 빈 강의실 대여 예약 서비스

공강 시간에 비어 있는 강의실을 찾아 예약하고, 이용 후 시설 문제를 신고할 수 있는 캠퍼스 서비스입니다.
소프트웨어 개발 교과목 2회차 개인 과제 (2221002 고겸, 2026.10).

## 주요 기능
- 날짜·교시·인원 조건으로 빈 강의실 검색 (파라미터 바인딩으로 SQL 인젝션 방지)
- 단독 사용(FULL)과 정원 안 공동 사용(SHARED) 예약, 동시 예약 오버부킹 방지 (`BEGIN IMMEDIATE` + 재검증)
- 이용 완료 예약 1건당 1회 시설 신고 (사진 첨부: png/jpg/jpeg/gif, 5MB 제한)
- 관리자 보강 등록 시 겹치는 예약 자동 취소와 인앱 알림, 예약 이력 추적·신고 답변

## 기술 스택
Python · Flask · SQLite · Jinja2 · Werkzeug(비밀번호 해시, 안전한 파일명)

## 실행 방법
```bash
pip install -r requirements.txt
python setup_db.py        # database.db 생성 + 강의실·시간표·테스트 계정
python app.py             # http://127.0.0.1:5000
```
테스트 계정은 `setup_db.py`에 있습니다 (로컬 확인용 기본 비밀번호).

동시성 테스트: `python test_concurrency_local.py`
