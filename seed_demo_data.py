# -*- coding: utf-8 -*-
"""
seed_demo_data.py
Nạp bộ dữ liệu mẫu cho PriorityLens (demo/bảo vệ đồ án).

Cách chạy: đặt file này CÙNG THƯ MỤC với app.py, models.py rồi chạy:
    python seed_demo_data.py

Script chỉ chèn dữ liệu nếu bảng Feedback đang trống, để tránh chèn trùng
nếu bạn lỡ chạy lại nhiều lần. Muốn seed lại từ đầu: xoá file
prioritylens.db (SQLite) rồi chạy lại.
"""

import random
from datetime import datetime, timedelta

import pytz

from app import app, db
from models import (
    Feedback, Feature, FeatureFeedback, FeatureSquad,
    Decision, ImpactFeedback, BehaviorLog, User,
)

random.seed(42)
VN_TZ = pytz.timezone('Asia/Ho_Chi_Minh')


def now():
    return datetime.now(VN_TZ).replace(tzinfo=None)


def days_ago(n):
    return now() - timedelta(days=n)


# ─────────────────────────────────────────────────────────────────
# 1. PHẢN HỒI (Feedback) — đã phân tích sẵn (để Dashboard/Feedback có
#    dữ liệu cảm xúc + cụm chủ đề ngay khi mở lên)
# ─────────────────────────────────────────────────────────────────

ANALYZED_FEEDBACK = [
    # (content, source, sentiment, topic, days_old)
    ("Mã QR thanh toán bị lỗi không quét được vào giờ cao điểm, khách đứng chờ rất lâu.", "merchant", "negative", "Lỗi quét mã QR thanh toán", 3),
    ("QR code load chậm, đôi khi phải tắt mở app lại mới quét được.", "merchant", "negative", "Lỗi quét mã QR thanh toán", 6),
    ("Máy quét QR bị đứng hình khi mạng yếu, mất luôn giao dịch.", "support", "negative", "Lỗi quét mã QR thanh toán", 10),
    ("App báo lỗi mã QR hết hạn dù mới tạo được vài giây.", "merchant", "negative", "Lỗi quét mã QR thanh toán", 18),
    ("Thỉnh thoảng quét QR bị treo, phải thử lại 2-3 lần mới thanh toán được.", "user", "negative", "Lỗi quét mã QR thanh toán", 27),

    ("Đối soát giao dịch cuối ngày bị chậm hơn 1 tiếng so với cam kết.", "merchant", "negative", "Đối soát giao dịch chậm", 2),
    ("Số liệu đối soát không khớp với sổ quỹ, phải tự cộng lại thủ công.", "merchant", "negative", "Đối soát giao dịch chậm", 8),
    ("Báo cáo đối soát ngày hôm qua tới trưa nay mới có, ảnh hưởng việc chốt sổ.", "support", "negative", "Đối soát giao dịch chậm", 14),
    ("Đối soát tự động hay bị trễ vào cuối tuần, không rõ nguyên nhân.", "merchant", "neutral", "Đối soát giao dịch chậm", 25),

    ("Loa Soundbox báo tiền chập chờn, có lúc không kêu dù đã nhận tiền.", "merchant", "negative", "Soundbox báo tiền không ổn định", 4),
    ("Soundbox kêu trễ 5-10 giây so với lúc khách chuyển khoản.", "merchant", "negative", "Soundbox báo tiền không ổn định", 9),
    ("Pin Soundbox tụt nhanh, phải sạc giữa ca bán hàng.", "support", "negative", "Soundbox báo tiền không ổn định", 16),
    ("Loa báo tiền đôi khi đọc sai số tiền giao dịch.", "merchant", "negative", "Soundbox báo tiền không ổn định", 22),

    ("Phí giao dịch merchant tăng so với gói ban đầu đăng ký, chưa thấy thông báo rõ ràng.", "merchant", "negative", "Phí giao dịch merchant cao", 5),
    ("Mức phí hiện tại hơi cao so với các ví khác đang cạnh tranh khu vực.", "merchant", "negative", "Phí giao dịch merchant cao", 12),
    ("Mong có gói phí ưu đãi hơn cho hộ kinh doanh nhỏ dưới 50 triệu/tháng.", "merchant", "neutral", "Phí giao dịch merchant cao", 20),

    ("Báo cáo doanh thu chỉ xem được theo ngày, không lọc được theo tuần hoặc theo mặt hàng.", "merchant", "negative", "Báo cáo doanh thu khó sử dụng", 7),
    ("Muốn xuất báo cáo doanh thu ra Excel nhưng không tìm thấy nút xuất file.", "merchant", "neutral", "Báo cáo doanh thu khó sử dụng", 11),
    ("Giao diện báo cáo doanh thu nhiều số liệu nhưng khó đọc trên điện thoại.", "user", "negative", "Báo cáo doanh thu khó sử dụng", 19),

    ("Gọi tổng đài hỏi về giao dịch treo phải chờ hơn 20 phút mới có người nghe máy.", "support", "negative", "Hỗ trợ CSKH phản hồi chậm", 6),
    ("Ticket hỗ trợ gửi 2 ngày chưa thấy phản hồi từ đội kỹ thuật.", "support", "negative", "Hỗ trợ CSKH phản hồi chậm", 13),
    ("Nhân viên hỗ trợ nhiệt tình nhưng thời gian xử lý sự cố còn chậm.", "support", "neutral", "Hỗ trợ CSKH phản hồi chậm", 21),

    ("Giao diện app gọn gàng, dễ tìm tính năng cần dùng hằng ngày.", "user", "positive", "Giao diện ứng dụng thân thiện", 5),
    ("Thao tác tạo mã QR và xem lịch sử giao dịch rất nhanh, thích cách sắp xếp.", "merchant", "positive", "Giao diện ứng dụng thân thiện", 15),
    ("So với trước đây app đã mượt hơn nhiều, đăng nhập nhanh.", "app_store", "positive", "Giao diện ứng dụng thân thiện", 23),
]

# ─────────────────────────────────────────────────────────────────
# 2. PHẢN HỒI CHƯA PHÂN TÍCH — để giữ số liệu "Phân tích AI (N mới)"
#    khác 0 khi bạn mở trang Feedback (không bắt buộc, chỉ để trông
#    thực tế hơn; muốn demo live gom nhóm AI, dùng file CSV riêng).
# ─────────────────────────────────────────────────────────────────

UNANALYZED_FEEDBACK = [
    ("Camera quét mã QR bị mờ, không lấy nét được ban đêm.", "user", 2),
    ("App bị crash khi mở lịch sử giao dịch quá 1 tháng.", "app_store", 4),
    ("Loa báo tiền cũ dùng lâu bị rè, nghe không rõ số tiền.", "merchant", 9),
    ("Không đăng nhập được sau khi cập nhật phiên bản mới nhất.", "play_store", 1),
    ("Muốn có tính năng quản lý tồn kho cơ bản ngay trong app.", "merchant", 17),
    ("Trợ lý AI Moni Merchant trả lời chưa sát câu hỏi thực tế.", "merchant", 12),
    ("Xin thêm tuỳ chọn xuất hoá đơn điện tử trực tiếp từ app.", "merchant", 26),
    ("Thông báo giao dịch bị trễ vài phút so với thực tế.", "support", 7),
    ("Ứng dụng tốn pin nhiều hơn hẳn so với bản cũ.", "app_store", 14),
    ("Mong có chế độ tối (dark mode) cho app merchant.", "user", 20),
    ("Phần cài đặt thông báo hơi rối, khó tìm mục cần tắt.", "user", 24),
    ("Thời gian xử lý hoàn tiền cho khách hơi lâu, khoảng 3-4 ngày.", "support", 29),
]

# ─────────────────────────────────────────────────────────────────
# 3. TÍNH NĂNG (Feature) trong Backlog — đủ mọi trạng thái/tình huống
# ─────────────────────────────────────────────────────────────────

FEATURES = [
    dict(name="Sửa lỗi quét mã QR thanh toán chậm", squad="Payments",
         description="Khắc phục hiện tượng QR treo/lỗi khi mạng yếu hoặc giờ cao điểm, liên quan cụm phản hồi 'Lỗi quét mã QR thanh toán'.",
         reach=18, impact=5, confidence=85, effort=2, status="backlog",
         cluster="Lỗi quét mã QR thanh toán"),
    dict(name="Tăng tốc đối soát giao dịch tự động", squad="Payments",
         description="Rút ngắn thời gian đối soát cuối ngày, đảm bảo số liệu khớp sổ quỹ merchant.",
         reach=14, impact=4, confidence=75, effort=3, status="backlog",
         cluster="Đối soát giao dịch chậm"),
    dict(name="Cải thiện độ ổn định Soundbox", squad="Merchant Growth",
         description="Xử lý độ trễ và lỗi đọc sai số tiền của loa báo tiền Soundbox.",
         reach=10, impact=4, confidence=80, effort=1.5, status="approved",
         decision_reason="Ảnh hưởng trực tiếp trải nghiệm bán hàng, đủ căn cứ dữ liệu (10 phản hồi/30 ngày), duyệt vào sprint gần nhất.",
         cluster="Soundbox báo tiền không ổn định"),
    dict(name="Giảm phí giao dịch cho hộ kinh doanh nhỏ", squad="Merchant Growth",
         description="Đề xuất gói phí ưu đãi cho merchant doanh thu dưới 50 triệu/tháng.",
         reach=8, impact=3, confidence=60, effort=4, status="rejected",
         decision_reason="Thay đổi chính sách phí ngoài thẩm quyền squad, cần Head of Product trình cấp cao hơn phê duyệt trước.",
         cluster="Phí giao dịch merchant cao"),
    dict(name="Nâng cấp báo cáo doanh thu theo tuần/mặt hàng", squad="Merchant Growth",
         description="Bổ sung bộ lọc thời gian và xuất Excel cho báo cáo doanh thu.",
         reach=12, impact=3, confidence=70, effort=2, status="backlog",
         cluster="Báo cáo doanh thu khó sử dụng"),
    dict(name="Rút ngắn thời gian phản hồi CSKH", squad="CSKH",
         description="Tối ưu quy trình tiếp nhận ticket hỗ trợ, giảm thời gian chờ trung bình.",
         reach=9, impact=4, confidence=75, effort=3, status="in_progress",
         decision_reason="Đã duyệt sprint trước, hiện đang triển khai cùng đội CSKH.",
         cluster="Hỗ trợ CSKH phản hồi chậm"),
    dict(name="Trợ lý AI Moni Merchant gợi ý chủ động", squad="Merchant Growth",
         description="Cải thiện độ chính xác câu trả lời của trợ lý AI cho merchant.",
         reach=6, impact=4, confidence=40, effort=2.5, status="backlog",
         cluster=None),  # Confidence < 50% -> minh hoạ khoá điểm RICE
    dict(name="Quản lý tồn kho cơ bản trong app", squad="Merchant Growth",
         description="Cho phép merchant nhỏ theo dõi số lượng hàng còn lại theo mặt hàng.",
         reach=5, impact=2, confidence=55, effort=5, status="backlog",
         cluster=None),
    dict(name="Tối ưu hiệu năng app giờ cao điểm", squad="Payments",
         description="Giảm tình trạng giật/lag khi lượng giao dịch tăng đột biến.",
         reach=20, impact=5, confidence=90, effort=2, status="done",
         decision_reason="Đã hoàn thành, ghi nhận Actual Impact thực tế sau sprint.",
         actual_impact=4, impact_note="Cải thiện rõ rệt nhưng chưa đạt kỳ vọng ban đầu do vẫn còn lag nhẹ ở một số dòng máy cũ.",
         cluster=None),
    dict(name="Cảnh báo giao dịch bất thường tức thời", squad="Risk & Fraud",
         description="Gửi cảnh báo tức thời khi phát hiện giao dịch có dấu hiệu bất thường.",
         reach=6, impact=4, confidence=65, effort=2, status="backlog",
         cluster=None),
]

# ─────────────────────────────────────────────────────────────────
# 4. BEHAVIOR LOG — để trang /behavior có KPI + biểu đồ không trống
# ─────────────────────────────────────────────────────────────────

PAGES = ['/', '/feedback', '/backlog', '/behavior', '/squads']
ROLES = ['squad_po', 'head_of_product', 'junior_po']


def seed():
    with app.app_context():
        if Feedback.query.count() > 0:
            print("⚠️  DB đã có dữ liệu Feedback — bỏ qua seeding để tránh trùng lặp.")
            print("    Muốn seed lại từ đầu: xoá file .db (SQLite) rồi chạy lại script.")
            return

        # --- Feedback đã phân tích ---
        for content, source, sentiment, topic, d in ANALYZED_FEEDBACK:
            db.session.add(Feedback(
                content=content, source=source, sentiment=sentiment,
                topic=topic, created_at=days_ago(d),
            ))

        # --- Feedback chưa phân tích ---
        for content, source, d in UNANALYZED_FEEDBACK:
            db.session.add(Feedback(
                content=content, source=source, created_at=days_ago(d),
            ))

        db.session.commit()
        print(f"✅ Đã tạo {len(ANALYZED_FEEDBACK) + len(UNANALYZED_FEEDBACK)} phản hồi.")

        # --- Feature + squad + decision + impact ---
        squad_po = User.query.filter_by(role='squad_po').first()
        head = User.query.filter_by(role='head_of_product').first()
        actor = squad_po or head or User.query.first()

        for f in FEATURES:
            feature = Feature(
                name=f['name'], description=f['description'],
                reach=f['reach'], impact=f['impact'],
                confidence=f['confidence'], effort=f['effort'],
                status=f['status'],
                decision_reason=f.get('decision_reason'),
            )
            if feature.confidence >= 50:
                feature.calculate_rice()
            else:
                feature.rice_score = 0
            db.session.add(feature)
            db.session.flush()  # để có feature.id

            db.session.add(FeatureSquad(feature_id=feature.id, squad=f['squad']))

            # Liên kết phản hồi gốc theo cụm chủ đề (nếu có)
            if f.get('cluster'):
                linked = Feedback.query.filter_by(topic=f['cluster']).all()
                for fb in linked:
                    db.session.add(FeatureFeedback(feature_id=feature.id, feedback_id=fb.id))

            # Ghi Decision cho các feature đã có quyết định
            if f['status'] in ('approved', 'rejected', 'in_progress', 'done') and actor:
                decision_type = 'rejected' if f['status'] == 'rejected' else 'approved'
                snap = f"R:{feature.reach}/I:{feature.impact}/C:{feature.confidence}%/E:{feature.effort}/Score:{feature.rice_score}"
                db.session.add(Decision(
                    feature_id=feature.id, user_id=actor.id,
                    decision_type=decision_type,
                    reason=f.get('decision_reason', 'Quyết định demo.'),
                    before_snapshot=snap, after_snapshot=snap,
                    feedback_ids=','.join(str(fb.id) for fb in
                                           (Feedback.query.filter_by(topic=f.get('cluster')).all()
                                            if f.get('cluster') else [])) or 'none',
                ))

            if 'actual_impact' in f and actor:
                db.session.add(ImpactFeedback(
                    feature_id=feature.id,
                    estimated_impact=feature.impact,
                    actual_impact=f['actual_impact'],
                    note=f.get('impact_note', ''),
                ))

        db.session.commit()
        print(f"✅ Đã tạo {len(FEATURES)} tính năng kèm squad/quyết định/impact.")

        # --- Behavior log ---
        count = 0
        for _ in range(90):
            event_type = random.choices(
                ['click', 'pageview', 'dropout'], weights=[55, 35, 10]
            )[0]
            db.session.add(BehaviorLog(
                event_type=event_type,
                page=random.choice(PAGES),
                element=random.choice([
                    'Nút Approve', 'Nút Reject', 'Xem chi tiết tính năng',
                    'Bộ lọc nguồn phản hồi', 'Biểu đồ Dashboard', 'Menu điều hướng',
                ]),
                user_type=random.choice(ROLES),
                created_at=now() - timedelta(
                    days=random.randint(0, 20),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59),
                ),
            ))
            count += 1
        db.session.commit()
        print(f"✅ Đã tạo {count} log hành vi (Behavior Tracker).")

        print("\n🎉 Seed dữ liệu demo hoàn tất. Có thể chạy `python app.py` và đăng nhập để xem.")


if __name__ == '__main__':
    seed()