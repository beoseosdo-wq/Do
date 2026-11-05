"""우리 가족 운동 관리 앱
실행: pip install -r requirements.txt && streamlit run app.py
배포: GitHub 저장소에 두 파일 업로드 → Streamlit Community Cloud에서 app.py 지정.
선택 설정(Cloud의 Settings → Secrets):
APP_PASSWORD = "가족만 아는 비밀번호"
APP_URL = "https://배포한주소.streamlit.app"
DB_PATH = "family_fitness.db"
주의: Cloud의 로컬 SQLite는 재배포/인스턴스 교체 시 사라질 수 있습니다.
장기 운영에는 외부 영구 DB로 전환하고, 그전에는 CSV 백업을 보관하세요.
"""
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import hmac
import sqlite3
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import qrcode
import streamlit as st

SPORTS = ["걷기", "달리기", "줄넘기", "헬스", "요가", "자전거", "수영", "등산"]
COLUMNS = ["기록ID", "가족", "날짜", "운동 종목", "운동 시간(분)"]


def connect_db(path):
    conn = sqlite3.connect(str(path), timeout=20)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect_db(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS members (name TEXT PRIMARY KEY)")
        conn.execute("""CREATE TABLE IF NOT EXISTS workouts (
            id TEXT PRIMARY KEY, member TEXT NOT NULL REFERENCES members(name),
            day TEXT NOT NULL, sport TEXT NOT NULL,
            minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 1440))""")
        # 최초 생성 때만 기본 구성원 등록. 이후 사용자가 수정한 목록을 유지합니다.
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        if not conn.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            conn.executemany("INSERT OR IGNORE INTO members VALUES (?)", [(n,) for n in ["아빠", "엄마", "나"]])
            conn.execute("INSERT INTO settings VALUES ('initialized', '1')")


def load_data(path):
    with connect_db(path) as conn:
        members = [r[0] for r in conn.execute("SELECT name FROM members ORDER BY rowid")]
        rows = conn.execute("SELECT id, member, day, sport, minutes FROM workouts ORDER BY day DESC, rowid DESC").fetchall()
    return members, pd.DataFrame(rows, columns=COLUMNS)


def save_record(path, rid, member, day, sport, minutes, update=False):
    if not sport.strip() or len(sport.strip()) > 40:
        raise ValueError("운동 종목은 1~40자로 입력해주세요.")
    if not 1 <= int(minutes) <= 1440:
        raise ValueError("운동 시간은 1~1,440분으로 입력해주세요.")
    date.fromisoformat(str(day))
    with connect_db(path) as conn:
        if update:
            cursor = conn.execute("UPDATE workouts SET member=?, day=?, sport=?, minutes=? WHERE id=?",
                                  (member, str(day), sport.strip(), int(minutes), rid))
            if cursor.rowcount != 1:
                raise ValueError("다른 접속에서 삭제된 기록입니다. 새로고침해주세요.")
        else:
            conn.execute("INSERT INTO workouts VALUES (?, ?, ?, ?, ?)",
                         (rid, member, str(day), sport.strip(), int(minutes)))


def summarize(members, records):
    base = pd.DataFrame({"가족": members})
    if records.empty:
        base["총 운동 횟수"] = 0
        base["총 운동 시간(분)"] = 0
    else:
        grouped = records.groupby("가족").agg(**{
            "총 운동 횟수": ("기록ID", "count"), "총 운동 시간(분)": ("운동 시간(분)", "sum")}).reset_index()
        base = base.merge(grouped, on="가족", how="left").fillna(0)
        base[["총 운동 횟수", "총 운동 시간(분)"]] = base[["총 운동 횟수", "총 운동 시간(분)"]].astype(int)
    return base


def flash(message):
    st.session_state["notice"] = message
    st.rerun()


def setting(key, default=""):
    # Secrets 파일 없이 로컬에서 실행하는 경우도 지원합니다.
    try:
        return st.secrets[key]
    except (FileNotFoundError, KeyError):
        return default


def main():
    st.set_page_config(page_title="우리 가족 운동 관리", page_icon="🏃", layout="wide")
    st.markdown("""<style>
    .block-container {max-width:1100px;padding-top:2rem;padding-bottom:3rem;}
    div[data-testid="stMetric"] {border:1px solid #E2E6F0;border-radius:16px;padding:16px;}
    .stButton>button,.stDownloadButton>button {border-radius:10px;min-height:44px;}
    </style>""", unsafe_allow_html=True)
    st.title("🏃 우리 가족 운동 관리")
    st.caption("작은 움직임을 함께 쌓아요 · 기록부터 통계까지 한 화면에서")
    password = str(setting("APP_PASSWORD"))
    if password and not st.session_state.get("authenticated", False):
        with st.form("login"):
            entered = st.text_input("가족 비밀번호", type="password")
            if st.form_submit_button("입장하기", type="primary"):
                if hmac.compare_digest(entered.encode(), password.encode()):
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("비밀번호를 확인해주세요.")
        st.stop()
    if password and st.button("로그아웃"):
        st.session_state.clear()
        st.rerun()
    if not password:
        st.info("🔒 현재 비밀번호가 없습니다. 주소를 아는 누구나 기록을 보고 바꿀 수 있습니다. 배포 설정의 Secrets에 APP_PASSWORD를 등록하는 것을 권장합니다.")
    db = str(setting("DB_PATH", "family_fitness.db"))
    init_db(db)
    members, records = load_data(db)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    st.warning("저장 안내: 현재 기록은 서버의 SQLite 파일에 저장됩니다. Streamlit Cloud 재배포·인스턴스 교체 시 유실될 수 있으니 아래 CSV 백업을 정기적으로 내려받으세요.")
    if st.button("🔄 최신 기록 불러오기", help="다른 가족이 추가한 기록을 새로 불러옵니다."):
        st.rerun()

    st.subheader("📊 우리 가족 전체 현황")
    c1, c2, c3 = st.columns(3)
    c1.metric("가족 구성원", f"{len(members)}명")
    c2.metric("총 운동 횟수", f"{len(records):,}회")
    total = int(records["운동 시간(분)"].sum()) if not records.empty else 0
    c3.metric("총 운동 시간", f"{total:,}분", help=f"{total // 60}시간 {total % 60}분")
    stats = summarize(members, records)
    st.dataframe(stats, hide_index=True, use_container_width=True)
    if total:
        st.bar_chart(stats.set_index("가족")[["총 운동 시간(분)"]], color="#1751D0", horizontal=True)
    else:
        st.caption("첫 운동을 기록하면 가족별 운동 시간 그래프가 표시됩니다.")

    st.subheader("✍️ 운동 기록 추가")
    if members:
        with st.form("add_workout", clear_on_submit=True):
            left, right = st.columns(2)
            member = left.selectbox("가족 구성원", members)
            day = right.date_input("운동한 날짜", value=today, max_value=today)
            sport_choice = left.selectbox("운동 종목", SPORTS + ["직접 입력"])
            custom = left.text_input("직접 입력할 운동 종목", max_chars=40, help="위에서 ‘직접 입력’을 선택한 경우 사용합니다.")
            minutes = right.number_input("운동 시간(분)", min_value=1, max_value=1440, value=30, step=5)
            if st.form_submit_button("＋ 운동 기록 저장", type="primary", use_container_width=True):
                try:
                    save_record(db, str(uuid4()), member, day, custom if sport_choice == "직접 입력" else sport_choice, minutes)
                    flash("운동 기록을 추가했어요! 오늘도 수고했어요 🎉")
                except (ValueError, sqlite3.Error) as exc:
                    st.error(f"저장하지 못했어요: {exc}")
    else:
        st.info("아래 가족 관리에서 구성원을 먼저 추가해주세요.")

    st.subheader("📋 전체 기록 · 수정 · 삭제")
    f1, f2 = st.columns(2)
    selected_member = f1.selectbox("가족 필터", ["전체 가족"] + members)
    selected_sport = f2.selectbox("운동 필터", ["전체 종목"] + sorted(records["운동 종목"].unique().tolist()))
    filtered = records.copy()
    if selected_member != "전체 가족":
        filtered = filtered[filtered["가족"] == selected_member]
    if selected_sport != "전체 종목":
        filtered = filtered[filtered["운동 종목"] == selected_sport]
    st.caption(f"현재 표시: {len(filtered):,}회 · {int(filtered['운동 시간(분)'].sum()) if not filtered.empty else 0:,}분 / 상단 통계는 필터와 무관한 전체 누적입니다.")
    st.dataframe(filtered.drop(columns="기록ID"), hide_index=True, use_container_width=True)
    if not filtered.empty:
        by_id = filtered.set_index("기록ID")
        def record_label(rid):
            r = by_id.loc[rid]
            return f"{r['날짜']} · {r['가족']} · {r['운동 종목']} · {r['운동 시간(분)']}분 · {rid[:8]}"
        rid = st.selectbox("수정하거나 삭제할 기록 선택", filtered["기록ID"].tolist(), format_func=record_label)
        record = by_id.loc[rid]
        with st.form(f"edit_{rid}"):
            e1, e2 = st.columns(2)
            edited_member = e1.selectbox("가족 수정", members, index=members.index(record["가족"]))
            edited_day = e2.date_input("날짜 수정", value=date.fromisoformat(record["날짜"]), max_value=today)
            edited_sport = e1.text_input("운동 종목 수정", value=record["운동 종목"], max_chars=40)
            edited_minutes = e2.number_input("운동 시간 수정(분)", min_value=1, max_value=1440, value=int(record["운동 시간(분)"]))
            confirm = st.checkbox("선택한 기록을 삭제하는 데 동의합니다. (삭제 버튼 사용 시 필수)")
            b1, b2 = st.columns(2)
            update = b1.form_submit_button("변경 사항 저장", type="primary", use_container_width=True)
            delete = b2.form_submit_button("선택한 기록 삭제", use_container_width=True)
            if update:
                try:
                    save_record(db, rid, edited_member, edited_day, edited_sport, edited_minutes, update=True)
                    flash("기록을 수정했어요.")
                except (ValueError, sqlite3.Error) as exc:
                    st.error(f"수정하지 못했어요: {exc}")
            if delete:
                if not confirm:
                    st.error("실수로 지우지 않도록 삭제 동의에 체크해주세요.")
                else:
                    with connect_db(db) as conn:
                        conn.execute("DELETE FROM workouts WHERE id=?", (rid,))
                    flash("선택한 기록을 삭제했어요.")
    else:
        st.info("표시할 기록이 없어요. 운동을 추가하거나 필터를 변경해주세요.")

    with st.expander("👨‍👩‍👧‍👦 가족 구성원 관리"):
        with st.form("add_member", clear_on_submit=True):
            name = st.text_input("새 구성원 이름", max_chars=30)
            if st.form_submit_button("가족 추가"):
                name = name.strip()
                if not name:
                    st.error("이름을 입력해주세요.")
                elif name in members or name == "전체 가족":
                    st.error("이미 있는 이름이거나 사용할 수 없는 이름이에요.")
                else:
                    try:
                        with connect_db(db) as conn:
                            conn.execute("INSERT INTO members VALUES (?)", (name,))
                        flash(f"{name}님을 추가했어요.")
                    except sqlite3.IntegrityError:
                        st.error("이미 추가된 이름이에요. 새로고침해주세요.")
        st.caption("기록이 있는 구성원은 삭제할 수 없습니다. 기록을 다른 구성원으로 수정하거나 먼저 삭제해주세요.")
        if members:
            remove_member = st.selectbox("삭제할 구성원", members)
            if st.button("기록 없는 구성원 삭제"):
                try:
                    with connect_db(db) as conn:
                        conn.execute("DELETE FROM members WHERE name=?", (remove_member,))
                    flash("구성원을 삭제했어요.")
                except sqlite3.IntegrityError:
                    st.error("이 구성원의 운동 기록이 남아 있어 삭제할 수 없어요.")

    with st.expander("💾 CSV 백업 · 복원"):
        st.download_button("전체 기록 CSV 다운로드", records.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"가족운동_백업_{today}.csv", mime="text/csv")
        st.caption("이 앱에서 내려받은 CSV만 복원하세요. 같은 기록ID는 건너뛰며 기존 기록을 덮어쓰지 않습니다. 기록이 없는 구성원은 CSV에 보관되지 않습니다.")
        upload = st.file_uploader("백업 CSV 선택 (최대 5MB)", type=["csv"])
        if st.button("CSV 기록 복원", disabled=upload is None):
            try:
                if upload.size > 5 * 1024 * 1024:
                    raise ValueError("5MB 이하 파일을 선택해주세요.")
                incoming = pd.read_csv(upload, dtype=str, keep_default_na=False)
                if list(incoming.columns) != COLUMNS:
                    raise ValueError("이 앱에서 내려받은 백업 파일 형식이 아닙니다.")
                validated = []
                for _, r in incoming.iterrows():
                    d = date.fromisoformat(r["날짜"])
                    m = int(r["운동 시간(분)"])
                    name, sport, rid = r["가족"].strip(), r["운동 종목"].strip(), r["기록ID"].strip()
                    if not name or len(name) > 30 or name == "전체 가족" or not sport or len(sport) > 40 or not rid or len(rid) > 100 or not 1 <= m <= 1440 or d > today:
                        raise ValueError("이름·날짜·종목·운동 시간·기록ID 중 유효하지 않은 값이 있어요.")
                    validated.append((rid, name, str(d), sport, m))
                count = 0
                with connect_db(db) as conn:
                    for row in validated:
                        conn.execute("INSERT OR IGNORE INTO members VALUES (?)", (row[1],))
                        count += conn.execute("INSERT OR IGNORE INTO workouts VALUES (?, ?, ?, ?, ?)", row).rowcount
                flash(f"{count}개 기록을 복원했어요. 중복 기록ID는 건너뛰었어요.")
            except (ValueError, sqlite3.Error, pd.errors.ParserError, UnicodeError, OverflowError) as exc:
                st.error(f"복원하지 못했어요. 기존 기록은 유지됩니다: {exc}")

    with st.expander("📱 앱 접속용 QR코드"):
        default_url = str(setting("APP_URL"))
        url = st.text_input("배포된 앱의 공개 주소", value=default_url, placeholder="https://my-family.streamlit.app")
        st.caption("QR은 앱 주소를 여는 용도입니다. 배포 후 주소를 입력하세요. GitHub 저장소 주소나 localhost 주소는 사용하지 마세요. QR 자체에는 비밀번호가 들어가지 않습니다.")
        if url:
            parsed = urlparse(url.strip())
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                st.error("http:// 또는 https://로 시작하는 올바른 주소를 입력해주세요.")
            elif len(url) > 1000:
                st.error("주소는 1,000자 이하로 입력해주세요.")
            else:
                image = qrcode.make(url.strip())
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                st.image(buffer.getvalue(), width=240, caption="휴대폰 카메라로 스캔하면 앱이 열려요")
                st.download_button("QR 이미지 다운로드", buffer.getvalue(), "family_fitness_qr.png", "image/png")
    st.caption("우리 가족 운동일지 · 운동 시간은 분 단위 · 날짜 기준: 대한민국")


if __name__ == "__main__":
    main()
