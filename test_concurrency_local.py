import concurrent.futures
import threading
import sqlite3
import time
from app import app, DB_PATH

def worker(user_id, room_id, date, head_count, start_period, duration_hours, barrier):
    """지정된 두 스레드가 정확히 동시에 요청을 날리도록 시뮬레이션하는 워커 함수"""
    client = app.test_client()
    
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['role'] = 'STUDENT'
        sess['name'] = f'테스트유저_{user_id}'

    print(f">> [테스트 스레드-{user_id}] 준비 완료. 동시 출발 신호(Barrier) 대기 중...")
    barrier.wait()  # 두 스레드가 여기서 대기하다가 정확히 마이크로초 단위로 동시 출발

    start_time = time.time()
    response = client.post('/confirm_reserve', data={
        'room_id': room_id,
        'date': date,
        'head_count': head_count,
        'purpose': 'Race Condition 방어 테스트',
        'start_period': start_period,
        'duration_hours': duration_hours
    }, follow_redirects=True)
    elapsed = time.time() - start_time
    
    print(f"<< [테스트 스레드-{user_id}] 요청 응답 수신 (소요 시간: {elapsed:.4f}초)")
    return user_id

def run_concurrency_test():
    # 1. 테스트 전 데이터 정리 (정원 30명짜리 강의실 1호, 2026-10-20 날짜의 기존 예약 삭제)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM reservations WHERE room_id = 1 AND date(start_time) = '2026-10-20'")
    conn.commit()
    conn.close()

    barrier = threading.Barrier(2)
    room_id = 1
    target_date = "2026-10-20"
    
    print("=" * 60)
    print(" [고도화된 동시성 테스트] 수강신청 상황(Race Condition) 시뮬레이션")
    print(f" 대상 강의실 ID: {room_id} (정원 30명) | 신청 인원: 각 30명 (단독 대여)")
    print("=" * 60)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # 사용자 2와 사용자 3이 정원 30명짜리 강의실에 30명 예약을 동시에(FULL 대여) 시도
        futures = [
            executor.submit(worker, 2, room_id, target_date, 30, 1, 1, barrier),
            executor.submit(worker, 3, room_id, target_date, 30, 1, 1, barrier)
        ]
        concurrent.futures.wait(futures)
        
    # 최종 결과 확인 (row_factory를 지정하지 않았으므로 튜플 인덱스로 접근)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT id, user_id, status, created_at FROM reservations WHERE room_id = ? AND date(start_time) = ?",
        (room_id, target_date)
    ).fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print(" [최종 검증 결과 보고]")
    print(f" - 데이터베이스에 기록된 최종 승인 건수: {len(rows)}건")
    for r in rows:
        # 튜플 인덱스 방식으로 수정 (0: id, 1: user_id, 2: status, 3: created_at)
        print(f"   * 예약 ID: {r[0]} | 사용자 ID: {r[1]} | 상태: {r[2]} | 생성시각: {r[3]}")
    print("-" * 60)
    
    if len(rows) == 1:
        print(" PASS: 오버 부킹(Over-booking) 방어 성공! 동시성 제어가 정상 작동합니다.")
    else:
        print(f" FAIL: {len(rows)}건이 중복 예약되었습니다.")
    print("=" * 60)

if __name__ == '__main__':
    run_concurrency_test()