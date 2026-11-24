from datetime import datetime
from db import transaction_scope, get_db

def create_reservation(user_id, room_id, reservation_date, start_time, end_time, purpose, head_count):
    """
    강의실 예약을 신청하고 검증합니다.
    트랜잭션 쓰기 잠금(BEGIN IMMEDIATE)을 활용해 동시 예약을 안전하게 처리합니다.
    """
    # 1. 날짜 및 요일 파싱 (0: 월요일 ~ 6: 일요일)
    try:
        date_obj = datetime.strptime(reservation_date, "%Y-%m-%d").date()
    except ValueError:
        return False, "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)"

    day_of_week = date_obj.weekday()

    # 2. 동시성 제어가 적용된 트랜잭션 범위 시작
    with transaction_scope() as db:
        # 강의실 존재 및 활성화 여부 확인
        room = db.execute(
            "SELECT * FROM rooms WHERE id = ? AND is_active = 1", 
            (room_id,)
        ).fetchone()

        if not room:
            return False, "존재하지 않거나 이용 불가능한 강의실입니다."

        # 수용 정원 검증
        if head_count > room["capacity"]:
            return False, f"이용 인원이 강의실 수용 정원({room['capacity']}명)을 초과했습니다."

        # 정규/보강 수업 일정 중복 검증 (시작 < ExistingEnd AND 종료 > ExistingStart)
        schedule_conflict = db.execute(
            """
            SELECT course_name FROM class_schedules
            WHERE room_id = ? 
              AND day_of_week = ?
              AND start_time < ? 
              AND end_time > ?
            """,
            (room_id, day_of_week, end_time, start_time)
        ).fetchone()

        if schedule_conflict:
            return False, f"정규 수업 일정('{schedule_conflict['course_name']}')과 시간이 중복됩니다."

        # 기존 예약과 중복 검증 (취소되지 않은 예약 대상)
        reservation_conflict = db.execute(
            """
            SELECT id FROM reservations
            WHERE room_id = ?
              AND reservation_date = ?
              AND status IN ('PENDING', 'APPROVED')
              AND start_time < ?
              AND end_time > ?
            """,
            (room_id, reservation_date, end_time, start_time)
        ).fetchone()

        if reservation_conflict:
            return False, "해당 시간대에 이미 예약된 내역이 있습니다."

        # 예약 등록 (기본 상태 APPROVED)
        cursor = db.execute(
            """
            INSERT INTO reservations 
            (user_id, room_id, reservation_date, start_time, end_time, purpose, head_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED')
            """,
            (user_id, room_id, reservation_date, start_time, end_time, purpose, head_count)
        )

        return True, {"reservation_id": cursor.lastrowid, "message": "예약이 성공적으로 완료되었습니다."}


def cancel_reservation(reservation_id, user_id, is_admin=False):
    """
    예약을 취소 처리합니다.
    """
    with transaction_scope() as db:
        res = db.execute(
            "SELECT user_id, status FROM reservations WHERE id = ?", 
            (reservation_id,)
        ).fetchone()

        if not res:
            return False, "존재하지 않는 예약입니다."

        # 일반 학생은 본인 예약만 취소 가능
        if not is_admin and res["user_id"] != user_id:
            return False, "본인의 예약만 취소할 수 있습니다."

        if res["status"] == "CANCELLED":
            return False, "이미 취소된 예약입니다."

        db.execute(
            "UPDATE reservations SET status = 'CANCELLED' WHERE id = ?",
            (reservation_id,)
        )
        return True, "예약이 정상적으로 취소되었습니다."


def get_available_rooms(reservation_date, start_time, end_time):
    """
    특정 날짜와 시간대에 예약 가능한 강의실 목록을 조회합니다.
    """
    db = get_db()
    
    try:
        date_obj = datetime.strptime(reservation_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    day_of_week = date_obj.weekday()

    query = """
        SELECT r.* FROM rooms r
        WHERE r.is_active = 1
        -- 수업 일정과 중복되지 않는 강의실
        AND NOT EXISTS (
            SELECT 1 FROM class_schedules cs
            WHERE cs.room_id = r.id
              AND cs.day_of_week = ?
              AND cs.start_time < ?
              AND cs.end_time > ?
        )
        -- 기존 예약과 중복되지 않는 강의실
        AND NOT EXISTS (
            SELECT 1 FROM reservations res
            WHERE res.room_id = r.id
              AND res.reservation_date = ?
              AND res.status IN ('PENDING', 'APPROVED')
              AND res.start_time < ?
              AND res.end_time > ?
        )
    """
    rooms = db.execute(
        query, 
        (day_of_week, end_time, start_time, reservation_date, end_time, start_time)
    ).fetchall()

    return [dict(room) for room in rooms]