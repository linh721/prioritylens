import os
import json
import csv
import io
import re
import unicodedata
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, make_response
from dotenv import load_dotenv
from google import genai
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Feedback, Feature, BehaviorLog, User, FeatureFeedback, Decision, FeatureSquad, ImpactFeedback

# 1. Nạp file .env và khởi tạo Client Gemini mới
load_dotenv() 
client = genai.Client() 

# 2. Cấu hình Ứng dụng Flask & Database
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
database_url = os.getenv('DATABASE_URL', '')

if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///prioritylens.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# 3. Cấu hình Hệ thống Đăng nhập (Flask-Login) bắt buộc đặt TRƯỚC các Route
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Vui lòng đăng nhập để tiếp tục'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def role_required(*roles):
    """Giới hạn thao tác ghi/chấm điểm theo vai trò trong quy trình."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                flash('Vai trò hiện tại chỉ được xem; thao tác này cần PO hoặc Head of Product.', 'danger')
                return redirect(request.referrer or url_for('index'))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def normalize_text(value):
    value = unicodedata.normalize('NFKC', value or '')
    value = re.sub(r'\s+', ' ', value.strip().lower())
    return value


def parse_import_date(value):
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def topic_cluster_stats(feedbacks):
    """Nhóm các phản hồi theo topic AI và tính Reach gợi ý 30 ngày.
    Dùng topic hiện có làm khóa nhóm để không cần thay đổi schema DB hiện tại.
    """
    cutoff = datetime.utcnow() - timedelta(days=30)
    groups = {}
    for fb in feedbacks:
        topic = (fb.topic or 'Chưa phân loại').strip()
        key = topic.lower()
        group = groups.setdefault(key, {'name': topic, 'feedbacks': [], 'ids': set()})
        group['feedbacks'].append(fb)
        if fb.created_at and fb.created_at >= cutoff:
            group['ids'].add(normalize_text(fb.content))
    result = []
    for group in groups.values():
        result.append({
            'name': group['name'],
            'count': len(group['feedbacks']),
            # BR4: Reach gợi ý chỉ dựa trên feedback duy nhất trong 30 ngày.
            'reach': len(group['ids']),
        })
    return sorted(result, key=lambda x: x['count'], reverse=True)

# 4. Khởi tạo Database và Tạo tài khoản Demo mặc định khi khởi động ứng dụng
with app.app_context():
    db.create_all()
    if not User.query.first():
        users = [
            User(username='head_product',
                 password_hash=generate_password_hash('password123'),
                 role='head_of_product'),
            User(username='squad_po',
                 password_hash=generate_password_hash('password123'),
                 role='squad_po'),
            User(username='junior_po',
                 password_hash=generate_password_hash('password123'),
                 role='junior_po'),
        ]
        for u in users:
            db.session.add(u)
        db.session.commit()

# ─── HỆ THỐNG ĐĂNG NHẬP / ĐĂNG XUẤT (Không được bọc bởi @login_required) ───────


# ─── TRANG CHỦ ───────────────────────────────────────────────────────────────
@app.route('/')
@login_required 
def index():
    total_feedback = Feedback.query.count()
    total_features = Feature.query.count()
    approved = Feature.query.filter_by(status='approved').count()
    rejected = Feature.query.filter_by(status='rejected').count()
    pending = Feature.query.filter_by(status='backlog').count()

    # Thay dòng cũ bằng dòng này (Xếp theo RICE giảm dần, nếu bằng điểm thì xếp theo Effort tăng dần, rồi đến Confidence giảm dần)
    top_features = Feature.query.filter_by(status='backlog')\
        .order_by(Feature.rice_score.desc(), Feature.effort.asc(), Feature.confidence.desc()).limit(5).all()


    return render_template('index.html',
        total_feedback=total_feedback,
        total_features=total_features,
        approved=approved,
        rejected=rejected,
        pending=pending,
        top_features=top_features)

# ─── FEEDBACK (QUẢN LÝ PHẢN HỒI) ──────────────────────────────────────────────
@app.route('/feedback')
@login_required 
def feedback_list():
    source_filter = request.args.get('source', '')
    sentiment_filter = request.args.get('sentiment', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Feedback.query
    if source_filter:
        query = query.filter_by(source=source_filter)
    if sentiment_filter:
        query = query.filter_by(sentiment=sentiment_filter)
    if date_from:
        parsed_from = parse_import_date(date_from)
        if parsed_from:
            query = query.filter(Feedback.created_at >= parsed_from)
    if date_to:
        parsed_to = parse_import_date(date_to)
        if parsed_to:
            parsed_to = parsed_to.replace(hour=23, minute=59, second=59)
            query = query.filter(Feedback.created_at <= parsed_to)

    feedbacks = query.order_by(Feedback.created_at.desc()).all()
    unanalyzed = Feedback.query.filter_by(sentiment=None).count()
    analyzed_feedbacks = Feedback.query.filter(Feedback.topic.isnot(None)).all()
    clusters = topic_cluster_stats(analyzed_feedbacks)

    return render_template('feedback.html',
        feedbacks=feedbacks,
        unanalyzed=unanalyzed,
        clusters=clusters,
        source_filter=source_filter,
        sentiment_filter=sentiment_filter,
        date_from=date_from,
        date_to=date_to)

@app.route('/feedback/add', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def add_feedback():
    content = request.form.get('content', '').strip()
    source = request.form.get('source', 'merchant').strip()
    date_value = request.form.get('date', '').strip()

    if not content:
        flash('Vui lòng nhập nội dung phản hồi', 'danger')
        return redirect(url_for('feedback_list'))
    if source not in {'merchant', 'user', 'support', 'app_store', 'play_store'}:
        flash('Nguồn phản hồi không hợp lệ.', 'danger')
        return redirect(url_for('feedback_list'))
    if Feedback.query.filter(Feedback.content == content).first():
        flash('Phản hồi trùng nội dung đã tồn tại; hệ thống không tạo bản ghi mới.', 'warning')
        return redirect(url_for('feedback_list'))

    created_at = parse_import_date(date_value) if date_value else datetime.utcnow()
    if date_value and created_at is None:
        flash('Ngày phản hồi không hợp lệ.', 'danger')
        return redirect(url_for('feedback_list'))

    fb = Feedback(content=content, source=source, created_at=created_at)
    db.session.add(fb)
    db.session.commit()
    flash('Đã thêm phản hồi thành công', 'success')
    return redirect(url_for('feedback_list'))

@app.route('/feedback/analyze', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def analyze_feedback():
    feedbacks = Feedback.query.filter_by(sentiment=None).all()

    if not feedbacks:
        return jsonify({'status': 'ok', 'message': 'Không có phản hồi mới cần phân tích'})

    analyzed = 0
    errors = 0

    for fb in feedbacks:
        try:
            prompt = f"""Phân tích phản hồi sau của người dùng ứng dụng MoMo. Hãy gán phản hồi vào một nhóm chủ đề (Feature Candidate) có tên ngắn, ổn định và mô tả ngắn. Đồng thời gán cảm xúc.

Phản hồi: {fb.content}

Chỉ trả về JSON:
{{
  \"sentiment\": \"positive\" hoặc \"negative\" hoặc \"neutral\",
  \"cluster_name\": \"tên nhóm chủ đề 3-7 từ\",
  \"cluster_description\": \"mô tả ngắn nhóm\"
}}"""

            chat = client.chats.create(model="gemini-3.6-flash")
            response = chat.send_message(prompt)
            text = response.text.strip()

            if '```' in text:
                blocks = text.split('```')
                for block in blocks:
                    cleaned = block.strip()
                    if cleaned.startswith('json'):
                        cleaned = cleaned[4:].strip()
                    if cleaned.startswith('{') and cleaned.endswith('}'):
                        text = cleaned
                        break

            result = json.loads(text.strip())
            sentiment = result.get('sentiment', 'neutral')
            if sentiment not in {'positive', 'negative', 'neutral'}:
                sentiment = 'neutral'
            cluster_name = (result.get('cluster_name') or result.get('topic') or 'Chưa phân loại').strip()
            fb.sentiment = sentiment
            fb.topic = cluster_name
            analyzed += 1

        except Exception:
            # Không ghi đè dữ liệu bằng kết quả giả khi Gemini lỗi; để sentiment=None
            # để lần chạy sau có thể retry đúng theo NFR3.
            errors += 1

    db.session.commit()
    return jsonify({
        'status': 'ok',
        'message': f'Đã phân tích {analyzed} phản hồi, gom nhóm theo chủ đề. Lỗi: {errors}'
    })


@app.route('/feedback/delete/<int:id>', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def delete_feedback(id):
    fb = Feedback.query.get_or_404(id)
    db.session.delete(fb)
    db.session.commit()
    flash('Đã xóa phản hồi', 'info')
    return redirect(url_for('feedback_list'))

@app.route('/feedback/import', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def import_feedback():
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Vui lòng chọn file CSV hợp lệ.', 'danger')
        return redirect(url_for('feedback_list'))

    try:
        raw = file.stream.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(raw))
        headers = {h.strip() for h in (reader.fieldnames or []) if h}
        if not ({'content', 'source'} <= headers or {'noi_dung', 'nguon'} <= headers):
            flash('CSV phải có cột content và source (hoặc noi_dung và nguon).', 'danger')
            return redirect(url_for('feedback_list'))

        existing = {normalize_text(f.content) for f in Feedback.query.all()}
        batch = set()
        imported = 0
        duplicates = 0
        invalid = 0

        for row in reader:
            content = (row.get('content') or row.get('noi_dung') or '').strip()
            source = (row.get('source') or row.get('nguon') or '').strip().lower()
            date_value = (row.get('date') or row.get('ngay') or '').strip()

            if not content or source not in {'merchant', 'user', 'support', 'app_store', 'play_store'}:
                invalid += 1
                continue
            key = normalize_text(content)
            if key in existing or key in batch:
                duplicates += 1
                continue

            created_at = parse_import_date(date_value)
            if date_value and created_at is None:
                invalid += 1
                continue

            kwargs = {'content': content, 'source': source}
            if created_at is not None:
                kwargs['created_at'] = created_at
            db.session.add(Feedback(**kwargs))
            batch.add(key)
            imported += 1

        db.session.commit()
        flash(f'Import thành công {imported} phản hồi; bỏ qua {duplicates} dòng trùng và {invalid} dòng không hợp lệ.', 'success')
    except UnicodeDecodeError:
        db.session.rollback()
        flash('Không đọc được CSV. Hãy lưu file ở UTF-8.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi import CSV: {e}', 'danger')

    return redirect(url_for('feedback_list'))


@app.route('/feature/from-topic', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def feature_from_topic():
    topic = request.form.get('topic', '').strip()
    if not topic:
        flash('Chưa chọn nhóm chủ đề.', 'danger')
        return redirect(url_for('feedback_list'))

    related = Feedback.query.filter(Feedback.topic == topic).all()
    if not related:
        flash('Nhóm chủ đề không có phản hồi liên kết.', 'danger')
        return redirect(url_for('feedback_list'))

    recent_cutoff = datetime.utcnow() - timedelta(days=30)
    recent_unique = {normalize_text(f.content) for f in related if f.created_at and f.created_at >= recent_cutoff}
    reach = len(recent_unique)
    feature = Feature(
        name=topic,
        description=f'Feature Candidate từ nhóm phản hồi "{topic}". Reach gợi ý 30 ngày = {reach}.',
        reach=reach,
        impact=3,
        confidence=50,
        effort=1
    )
    feature.rice_score = 0
    db.session.add(feature)
    db.session.flush()
    for fb in related:
        if not FeatureFeedback.query.filter_by(feature_id=feature.id, feedback_id=fb.id).first():
            db.session.add(FeatureFeedback(feature_id=feature.id, feedback_id=fb.id))
    db.session.add(FeatureSquad(feature_id=feature.id, squad='Core'))
    db.session.commit()
    flash(f'Đã tạo Feature Candidate "{topic}" với Reach gợi ý {reach}. Hãy nhập Impact, Confidence và Effort trước khi tính RICE.', 'success')
    return redirect(url_for('backlog'))

# ─── BACKLOG (QUẢN LÝ TÍNH NĂNG & ĐIỂM RICE) ──────────────────────────────────
@app.route('/backlog')
@login_required
def backlog():
    status_filter = request.args.get('status', 'backlog')

    features = Feature.query.filter_by(status=status_filter)\
        .order_by(
            Feature.rice_score.desc(),
            Feature.effort.asc(),
            Feature.confidence.desc()
        ).all()

    all_status_count = {
        'backlog': Feature.query.filter_by(status='backlog').count(),
        'approved': Feature.query.filter_by(status='approved').count(),
        'rejected': Feature.query.filter_by(status='rejected').count(),
    }

    # Lấy toàn bộ Feedback để cho phép PO liên kết Feedback thật với Feature
    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()

    # Lấy danh sách Feedback đã liên kết theo từng Feature
    feature_feedback_ids = {}

    for feature in features:
        feature_feedback_ids[feature.id] = {
            row.feedback_id
            for row in FeatureFeedback.query.filter_by(
                feature_id=feature.id
            ).all()
        }

    return render_template(
        'backlog.html',
        features=features,
        status_filter=status_filter,
        all_status_count=all_status_count,
        feedbacks=feedbacks,
        feature_feedback_ids=feature_feedback_ids
    )

@app.route('/feature/add', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def add_feature():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()

    if not name:
        flash('Vui lòng nhập tên tính năng', 'danger')
        return redirect(url_for('backlog'))

    try:
        reach = float(request.form.get('reach', 0))
        impact = float(request.form.get('impact', 0))
        confidence = float(request.form.get('confidence', 0))
        effort = float(request.form.get('effort', 1))
    except ValueError:
        flash('Giá trị điểm không hợp lệ', 'danger')
        return redirect(url_for('backlog'))

    if reach < 0 or impact not in {1, 2, 3, 4, 5} or not (0 <= confidence <= 100) or effort <= 0:
        flash('Tham số RICE không hợp lệ: Reach ≥ 0; Impact 1–5; Confidence 0–100%; Effort > 0.', 'danger')
        return redirect(url_for('backlog'))

    feature = Feature(
        name=name,
        description=description,
        reach=reach,
        impact=impact,
        confidence=confidence,
        effort=effort
    )

    if confidence >= 50:
        feature.calculate_rice()
    else:
        feature.rice_score = 0

    db.session.add(feature)
    db.session.flush()
    db.session.add(FeatureSquad(feature_id=feature.id, squad=(request.form.get('squad') or 'Core').strip()[:100] or 'Core'))
    db.session.commit()

    if confidence < 50:
        flash(f'⚠️ Tính năng "{name}" được thêm nhưng chưa tính điểm vì Confidence < 50%. Thu thập thêm phản hồi rồi cập nhật lại.', 'warning')
    else:
        flash(f'✅ Đã thêm tính năng "{name}" với điểm RICE: {feature.rice_score}', 'success')

    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/link-feedback', methods=['POST'])
@login_required
def link_feature_feedback(id):
    feature = Feature.query.get_or_404(id)

    selected_feedback_ids = request.form.getlist('feedback_ids')

    try:
        selected_feedback_ids = [
            int(feedback_id)
            for feedback_id in selected_feedback_ids
        ]
    except ValueError:
        flash('Danh sách Feedback không hợp lệ', 'danger')
        return redirect(url_for('backlog'))

    # Xóa toàn bộ liên kết cũ của Feature
    FeatureFeedback.query.filter_by(
        feature_id=feature.id
    ).delete()

    # Tạo lại liên kết theo các Feedback được chọn
    for feedback_id in selected_feedback_ids:
        feedback = Feedback.query.get(feedback_id)

        if feedback:
            link = FeatureFeedback(
                feature_id=feature.id,
                feedback_id=feedback.id
            )
            db.session.add(link)

    db.session.commit()

    flash(
        f'Đã liên kết {len(selected_feedback_ids)} Feedback với tính năng "{feature.name}"',
        'success'
    )

    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/decision', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def feature_decision(id):
    feature = Feature.query.get_or_404(id)
    decision = request.form.get('decision')
    reason = request.form.get('reason', '').strip()

    if not reason:
        flash('Vui lòng ghi lý do quyết định (bắt buộc theo US22)', 'danger')
        return redirect(url_for('backlog'))

    if decision not in {'approved', 'rejected', 'adjust'}:
        flash('Loại quyết định không hợp lệ.', 'danger')
        return redirect(url_for('backlog'))

    old_snapshot = f'R:{feature.reach}/I:{feature.impact}/C:{feature.confidence}%/E:{feature.effort}/Score:{feature.rice_score}'

    if decision == 'adjust':
        try:
            new_reach = float(request.form.get('reach', feature.reach))
            new_impact = float(request.form.get('impact', feature.impact))
            new_confidence = float(request.form.get('confidence', feature.confidence))
            new_effort = float(request.form.get('effort', feature.effort))

            if (
                new_reach < 0
                or new_impact not in {1, 2, 3, 4, 5}
                or not (0 <= new_confidence <= 100)
                or new_effort <= 0
            ):
                raise ValueError

            feature.reach = new_reach
            feature.impact = new_impact
            feature.confidence = new_confidence
            feature.effort = new_effort

            # BR3: Confidence < 50% thì không tính RICE
            if feature.confidence >= 50:
                feature.calculate_rice()
            else:
                feature.rice_score = 0
                flash(
                    '⚠️ Điểm RICE được đưa về 0 do Confidence thấp hơn 50%',
                    'warning'
                )

            feature.status = 'backlog'

        except ValueError:
            flash('Giá trị điều chỉnh không hợp lệ', 'danger')
            return redirect(url_for('backlog'))

    else:
        feature.status = decision

    feature.decision_reason = reason
    
    linked = FeatureFeedback.query.filter_by(feature_id=feature.id).all()
    related_ids_list = [x.feedback_id for x in linked]
    if not related_ids_list:
        related_feedback = Feedback.query.filter(Feedback.topic == feature.name).all()
        related_ids_list = [f.id for f in related_feedback[:50]]
    related_ids = ','.join(str(x) for x in related_ids_list) or 'none'
    new_snapshot = f'R:{feature.reach}/I:{feature.impact}/C:{feature.confidence}%/E:{feature.effort}/Score:{feature.rice_score}'

    db.session.add(Decision(
        feature_id=feature.id,
        user_id=current_user.id,
        decision_type=decision,
        reason=reason,
        before_snapshot=old_snapshot,
        after_snapshot=new_snapshot,
        feedback_ids=related_ids
    ))

    # US22: ghi lại telemetry + snapshot.
    log_entry = BehaviorLog(
        event_type=decision,
        page='/backlog',
        element=f"Feature: {feature.name} | Before: {old_snapshot} | After: {new_snapshot} | Feedback IDs: {related_ids} | Lý do: {reason}",
        user_type=current_user.role
    )
    db.session.add(log_entry)
    db.session.commit()

    labels = {'approved': 'Phê duyệt', 'rejected': 'Từ chối', 'adjust': 'Điều chỉnh'}
    flash(f'Đã {labels.get(decision, decision)} tính năng "{feature.name}"', 'success')
    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/status', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def update_feature_status(id):
    """US27: Cập nhật trạng thái tính năng từ Approved sang In Progress hoặc Done"""
    feature = Feature.query.get_or_404(id)
    new_status = request.form.get('status')
    
    if new_status in ['in_progress', 'done', 'approved']:
        old_status = feature.status
        feature.status = new_status
        db.session.add(BehaviorLog(event_type='status_change', page='/backlog', element=f'Feature: {feature.name} | {old_status} -> {new_status}', user_type=current_user.role))
        db.session.commit()
        flash(f'Đã cập nhật trạng thái tính năng "{feature.name}" sang thành công!', 'success')
    else:
        flash('Trạng thái không hợp lệ', 'danger')
        
    return redirect(url_for('backlog', status=request.form.get('current_filter', 'approved')))


@app.route('/feature/<int:id>/delete', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def delete_feature(id):
    feature = Feature.query.get_or_404(id)
    FeatureFeedback.query.filter_by(feature_id=id).delete()
    FeatureSquad.query.filter_by(feature_id=id).delete()
    Decision.query.filter_by(feature_id=id).delete()
    ImpactFeedback.query.filter_by(feature_id=id).delete()
    db.session.delete(feature)
    db.session.commit()
    flash(f'Đã xóa tính năng "{feature.name}".', 'info')
    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/restore', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def restore_feature(id):
    feature = Feature.query.get_or_404(id)
    reason = request.form.get('reason', '').strip() or 'Khôi phục khỏi Deprioritized để xem xét lại.'
    before = f'R:{feature.reach}/I:{feature.impact}/C:{feature.confidence}%/E:{feature.effort}/Score:{feature.rice_score}'
    feature.status = 'backlog'
    feature.decision_reason = reason
    db.session.add(Decision(feature_id=id, user_id=current_user.id, decision_type='restore', reason=reason, before_snapshot=before, after_snapshot=before, feedback_ids=','.join(str(x.feedback_id) for x in FeatureFeedback.query.filter_by(feature_id=id).all())))
    db.session.commit()
    flash(f'Đã khôi phục "{feature.name}" về Backlog.', 'success')
    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/feedback')
@login_required
def feature_feedback(id):
    feature = Feature.query.get_or_404(id)
    links = FeatureFeedback.query.filter_by(feature_id=id).all()
    feedbacks = Feedback.query.filter(Feedback.id.in_([x.feedback_id for x in links])).order_by(Feedback.created_at.desc()).all() if links else Feedback.query.filter_by(topic=feature.name).order_by(Feedback.created_at.desc()).all()
    return jsonify({'feature': feature.name, 'feedbacks': [{'id': f.id, 'content': f.content, 'source': f.source, 'sentiment': f.sentiment, 'topic': f.topic, 'date': f.created_at.strftime('%Y-%m-%d') if f.created_at else None} for f in feedbacks]})

@app.route('/feature/<int:id>/squad', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def assign_feature_squad(id):
    feature = Feature.query.get_or_404(id)
    squad = (request.form.get('squad') or 'Core').strip()[:100]
    if not squad:
        squad = 'Core'
    existing = FeatureSquad.query.filter_by(feature_id=id).first()
    if existing:
        existing.squad = squad
    else:
        db.session.add(FeatureSquad(feature_id=id, squad=squad))
    db.session.commit()
    flash(f'Đã gán "{feature.name}" cho squad {squad}.', 'success')
    return redirect(url_for('backlog'))

@app.route('/impact/<int:id>', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def record_actual_impact(id):
    feature = Feature.query.get_or_404(id)
    try:
        actual = float(request.form.get('actual_impact', ''))
    except ValueError:
        flash('Actual Impact phải là số.', 'danger')
        return redirect(url_for('backlog', status=feature.status))
    if not (1 <= actual <= 5):
        flash('Actual Impact phải nằm trong khoảng 1–5.', 'danger')
        return redirect(url_for('backlog', status=feature.status))
    db.session.add(ImpactFeedback(feature_id=id, estimated_impact=feature.impact, actual_impact=actual, note=request.form.get('note','').strip()))
    db.session.commit()
    flash(f'Đã ghi Actual Impact {actual}/5 cho "{feature.name}".', 'success')
    return redirect(url_for('backlog', status=feature.status))

@app.route('/feature/<int:id>/history')
@login_required
def feature_history(id):
    feature = Feature.query.get_or_404(id)
    decisions = Decision.query.filter_by(feature_id=id).order_by(Decision.created_at.desc()).all()
    return jsonify({'feature': feature.name, 'history': [{'type': d.decision_type, 'reason': d.reason, 'before': d.before_snapshot, 'after': d.after_snapshot, 'feedback_ids': d.feedback_ids, 'user': User.query.get(d.user_id).username if User.query.get(d.user_id) else None, 'at': d.created_at.isoformat()} for d in decisions]})

@app.route('/feedback/import-external', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def import_external_feedback():
    """US03: nhận dữ liệu review App Store/Play Store mô phỏng qua CSV."""
    file = request.files.get('external_csv')
    source = request.form.get('external_source', '').strip()
    if source not in {'app_store', 'play_store'} or not file or not file.filename.lower().endswith('.csv'):
        flash('Chọn nguồn App Store/Play Store và file CSV hợp lệ.', 'danger')
        return redirect(url_for('feedback_list'))
    raw = file.stream.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(raw))
    imported = duplicates = invalid = 0
    existing = {normalize_text(f.content) for f in Feedback.query.all()}
    for row in reader:
        content = (row.get('content') or row.get('review') or row.get('noi_dung') or '').strip()
        if not content:
            invalid += 1; continue
        key = normalize_text(content)
        if key in existing:
            duplicates += 1; continue
        date_value = (row.get('date') or row.get('ngay') or '').strip()
        created_at = parse_import_date(date_value) if date_value else datetime.utcnow()
        if date_value and created_at is None:
            invalid += 1; continue
        db.session.add(Feedback(content=content, source=source, created_at=created_at))
        existing.add(key); imported += 1
    db.session.commit()
    flash(f'Đã nhận {imported} review {source}; trùng {duplicates}; lỗi {invalid}.', 'success')
    return redirect(url_for('feedback_list'))

@app.route('/behavior/import-ga4', methods=['POST'])
@role_required('squad_po', 'head_of_product')
def import_ga4():
    """US04: nhập log GA4 dạng CSV mô phỏng."""
    file = request.files.get('ga4_csv')
    if not file or not file.filename.lower().endswith('.csv'):
        flash('Vui lòng chọn CSV GA4.', 'danger')
        return redirect(url_for('behavior_log'))
    raw = file.stream.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(raw))
    count = 0
    for row in reader:
        event = (row.get('event_type') or row.get('event_name') or 'ga4_event').strip()[:50]
        page = (row.get('page') or row.get('page_path') or '/').strip()[:100]
        element = (row.get('element') or row.get('event_label') or 'GA4').strip()[:100]
        db.session.add(BehaviorLog(event_type=f'ga4_{event}'[:50], page=page, element=element, user_type='ga4'))
        count += 1
    db.session.commit()
    flash(f'Đã nhập {count} sự kiện GA4.', 'success')
    return redirect(url_for('behavior_log'))

@app.route('/impact/<int:id>/history')
@login_required
def impact_history(id):
    feature = Feature.query.get_or_404(id)
    rows = ImpactFeedback.query.filter_by(feature_id=id).order_by(ImpactFeedback.created_at.desc()).all()
    return jsonify({'feature': feature.name, 'estimated_impact': feature.impact, 'history': [{'estimated': x.estimated_impact, 'actual': x.actual_impact, 'note': x.note, 'at': x.created_at.isoformat()} for x in rows]})

@app.route('/squads')
@login_required
def squad_overview():
    from sqlalchemy import func
    rows = db.session.query(FeatureSquad.squad, func.count(FeatureSquad.id)).group_by(FeatureSquad.squad).all()
    data = []
    for squad, count in rows:
        ids = [x.feature_id for x in FeatureSquad.query.filter_by(squad=squad).all()]
        features = Feature.query.filter(Feature.id.in_(ids)).order_by(Feature.rice_score.desc(), Feature.effort.asc(), Feature.confidence.desc()).all() if ids else []
        data.append({'squad': squad, 'count': count, 'top': [{'name': f.name, 'rice': f.rice_score, 'status': f.status} for f in features[:10]]})
    return render_template('squads.html', squads=data)

@app.route('/feature/<int:id>/suggest', methods=['POST'])
@login_required
def suggest_rice(id):
    """Gemini gợi ý điểm RICE dựa trên phản hồi liên quan"""
    feature = Feature.query.get_or_404(id)
    related = Feedback.query.filter(Feedback.topic.ilike(f'%{feature.name[:10]}%')).limit(10).all()
    feedback_text = '\n'.join([f'- [{fb.sentiment}] {fb.content}' for fb in related]) if related else 'Chưa có phản hồi liên quan'
    
    try:
        prompt = f"""Bạn là chuyên gia Product Management. Dựa trên thông tin sau, hãy gợi ý điểm RICE cho tính năng.

Tính năng: {feature.name}
Mô tả: {feature.description or 'Chưa có mô tả'}
Phản hồi liên quan:
{feedback_text}

Trả về JSON (chỉ JSON, không giải thích):
{{
  "reach": <số người dùng ảnh hưởng mỗi tháng, ước tính>,
  "impact": <1-5, mức độ cải thiện trải nghiệm>,
  "confidence": <0-100, độ tin cậy của ước tính>,
  "effort": <số tuần phát triển ước tính>,
  "reasoning": "<giải thích ngắn gọn bằng tiếng Việt>"
}}"""

        # Gọi AI qua Client thư viện mới
        chat = client.chats.create(model="gemini-3.6-flash")
        response = chat.send_message(prompt)
        text = response.text.strip()

        # Làm sạch chuỗi JSON nếu AI trả về kèm bọc codeblock markdown
        if '```' in text:
            blocks = text.split('```')
            for block in blocks:
                cleaned = block.strip()
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith('{') and cleaned.endswith('}'):
                    text = cleaned
                    break

        result = json.loads(text.strip())
        return jsonify({'status': 'ok', 'suggestion': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/feature/<int:id>/ai-insight', methods=['POST'])
@login_required
def ai_insight(id):
    """US17: AI phân tích chuyên sâu lý do điểm RICE và đưa ra đề xuất hành động cho PO MoMo"""
    feature = Feature.query.get_or_404(id)
    
    try:
        prompt = f"""Bạn là một Cố vấn trưởng quản lý sản phẩm (Head of Product) tại MoMo. 
Hãy phân tích các tham số RICE hiện tại của tính năng sau và viết một báo cáo đánh giá ngắn gọn, thực tế:

- Tên tính năng: {feature.name}
- Mô tả: {feature.description or 'Chưa có mô tả'}
- Điểm RICE hiện tại: {feature.rice_score} (Reach: {feature.reach}, Impact: {feature.impact}, Confidence: {feature.confidence}%, Effort: {feature.effort} tuần)

Yêu cầu cấu trúc phản hồi bằng tiếng Việt:
1. ĐÁNH GIÁ: Điểm số này phản ánh tính năng này thuộc nhóm nào (Ví dụ: "Quick Win" - làm nhanh ăn lớn, "Big Bet" - dự án lớn rủi ro cao, hoặc "Dự án tốn tài nguyên hiệu quả thấp").
2. RỦI RO & CƠ HỘI: Nhận xét về chỉ số Confidence và Effort. Có cần tối ưu thiết kế để giảm tuần phát triển (Effort) xuống không?
3. ĐỀ XUẤT HÀNH ĐỘNG: Gợi ý PO nên bấm Approve (Duyệt ngay vào Sprint), Reject (Từ chối) hay Adjust (Cần đi khảo sát thêm người dùng để tăng độ tự tin)."""

        chat = client.chats.create(model="gemini-3.6-flash")
        response = chat.send_message(prompt)
        
        return jsonify({
            'status': 'ok',
            'insight': response.text
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/conflict-detector')
@login_required
def conflict_detector():
    features = Feature.query.filter_by(status='backlog').all()
    warnings = []

    for f in features:

        # ==========================================================
        # ƯU TIÊN FEEDBACK ĐƯỢC LIÊN KẾT THẬT
        # ==========================================================
        linked_feedback_count = FeatureFeedback.query.filter_by(
            feature_id=f.id
        ).count()

        # Nếu Feature có Feedback liên kết thật,
        # dùng số lượng này làm nguồn dữ liệu chính.
        if linked_feedback_count > 0:
            feedback_count = linked_feedback_count

        # Nếu chưa có liên kết FeatureFeedback,
        # mới dùng cách fallback theo topic/name.
        else:
            feedback_count = Feedback.query.filter(
                Feedback.topic.ilike(f'%{f.name[:10]}%')
            ).count()

        # ==========================================================
        # CẢNH BÁO 1:
        # Confidence cao nhưng không có Feedback làm căn cứ
        # ==========================================================
        if f.confidence >= 80 and feedback_count == 0:
            warnings.append({
                'feature_id': f.id,
                'feature_name': f.name,
                'type': 'danger',
                'message': (
                    f'⚠️ Phát hiện thiếu căn cứ dữ liệu: '
                    f'Confidence đang ở mức {f.confidence}% '
                    f'nhưng hệ thống không tìm thấy Feedback liên kết '
                    f'với tính năng này.'
                )
            })

        # ==========================================================
        # CẢNH BÁO 2:
        # Effort cao nhưng Impact thấp
        # ==========================================================
        if f.effort > 8 and f.impact <= 2:
            warnings.append({
                'feature_id': f.id,
                'feature_name': f.name,
                'type': 'warning',
                'message': (
                    f'⚠️ Cảnh báo lãng phí nguồn lực: '
                    f'Tính năng tốn tới {f.effort} tuần phát triển '
                    f'nhưng Impact chỉ đạt {f.impact}/5.'
                )
            })

    return jsonify({
        'status': 'ok',
        'total_conflicts': len(warnings),
        'conflicts': warnings
    })

# ─── THỐNG KÊ & THEO DÕI HÀNH VI (BEHAVIOR LOGS) ──────────────────────────────
@app.route('/track', methods=['POST'])
def track_behavior():
    """Hàm ngầm ghi vết thao tác - Đã sửa lỗi bóc tách dữ liệu từ sendBeacon và Click"""
    # Xử lý bóc tách linh hoạt cho cả fetch thông thường và navigator.sendBeacon của Chrome
    if request.is_json:
        data = request.get_json()
    else:
        try:
            data = json.loads(request.data.decode('utf-8'))
        except Exception:
            data = {}

    event_type = data.get('event_type', 'click')
    page = data.get('page', '/')
    element = data.get('element', 'Unspecified Element')
    
    # Tự động lấy vai trò của người đang đăng nhập thực tế để gán nhãn kiểm toán
    user_type = current_user.role if (current_user and current_user.is_authenticated) else 'guest'

    log = BehaviorLog(
        event_type=event_type,
        page=page,
        element=element,
        user_type=user_type
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/behavior')
@login_required
def behavior_log():
    logs = BehaviorLog.query.order_by(BehaviorLog.created_at.desc()).limit(100).all()
    
    # Bổ sung đầy đủ các biến đếm để giao diện HTML hiển thị con số KPI
    click_count = BehaviorLog.query.filter_by(event_type='click').count()
    dropout_count = BehaviorLog.query.filter_by(event_type='dropout').count()
    pageview_count = BehaviorLog.query.filter_by(event_type='pageview').count()
    total_count = BehaviorLog.query.count() # Tổng tất cả sự kiện
    
    return render_template('behavior.html',
        logs=logs,
        click_count=click_count,
        dropout_count=dropout_count,
        pageview_count=pageview_count,
        total_count=total_count)

@app.route('/behavior/clear', methods=['POST'])
@role_required('head_of_product')
def clear_behavior_logs():
    """Hàm bổ trợ dọn sạch nhật ký Log hành vi khi PO yêu cầu làm trống database mẫu"""
    try:
        # Xóa sạch toàn bộ bản ghi trong bảng nhật ký hành vi
        BehaviorLog.query.delete()
        db.session.commit()
        flash('Đã dọn sạch toàn bộ nhật ký Log hành vi người dùng thành công!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Lỗi hệ thống khi xóa log: {str(e)}', 'danger')
        
    return redirect(url_for('behavior_log'))

@app.route('/api/chart-data')
@login_required
def chart_data():
    positive = Feedback.query.filter_by(sentiment='positive').count()
    negative = Feedback.query.filter_by(sentiment='negative').count()
    neutral = Feedback.query.filter_by(sentiment='neutral').count()
    unanalyzed = Feedback.query.filter_by(sentiment=None).count()

    backlog = Feature.query.filter_by(status='backlog').count()
    approved = Feature.query.filter_by(status='approved').count()
    rejected = Feature.query.filter_by(status='rejected').count()

    return jsonify({
        'sentiment': {
            'labels': ['Tích cực', 'Tiêu cực', 'Trung tính', 'Chưa phân tích'],
            'data': [positive, negative, neutral, unanalyzed],
            'colors': ['#198754', '#dc3545', '#6c757d', '#ffc107']
        },
        'backlog': {
            'labels': ['Chờ duyệt', 'Đã duyệt', 'Từ chối'],
            'data': [backlog, approved, rejected],
            'colors': ['#0d6efd', '#198754', '#dc3545']
        }
    })
@app.route('/api/behavior-chart')
@login_required
def behavior_chart_data():
    """US23: Chuẩn hóa dữ liệu thô từ cơ sở dữ liệu thành mảng phẳng tương thích 100% với Chart.js"""
    from sqlalchemy import func
    
    # 1. Thống kê top trang được truy cập nhiều nhất (Tương thích hoàn toàn với cả SQLite và PostgreSQL)
    page_stats = db.session.query(
        BehaviorLog.page.label('page_path'), 
        func.count(BehaviorLog.id).label('visit_count')
    ).group_by(BehaviorLog.page).order_by(func.count(BehaviorLog.id).desc()).limit(5).all()
    
    # Bóc tách dữ liệu bằng cách gọi chính xác tên nhãn cấu trúc (label) để tránh lỗi Tuple
    page_labels = [row.page_path if row.page_path else '/' for row in page_stats]
    page_values = [row.visit_count for row in page_stats]
    
    # 2. Thống kê phân bố loại sự kiện tương tác
    click_c = BehaviorLog.query.filter_by(event_type='click').count()
    view_c = BehaviorLog.query.filter_by(event_type='pageview').count()
    drop_c = BehaviorLog.query.filter_by(event_type='dropout').count()
    
    # Đảm bảo gộp thêm các sự kiện duyệt tính năng của PO vào mảng Click để biểu đồ không bị thiếu số liệu
    approved_c = BehaviorLog.query.filter_by(event_type='approved').count()
    rejected_c = BehaviorLog.query.filter_by(event_type='rejected').count()
    total_clicks = click_c + approved_c + rejected_c
    
    return jsonify({
        'pages': {
            'labels': page_labels,
            'data': page_values
        },
        'events': {
            'labels': ['Nhấp chuột (Click)', 'Xem trang (Pageview)', 'Thoát (Dropout)'],
            'data': [total_clicks, view_c, drop_c]
        }
    })

@app.route('/backlog/export-csv')
@login_required
def export_backlog_csv():
    """US26: Xuất danh sách tính năng đã phê duyệt ra file CSV"""
    # Lấy các tính năng đã được Approve hoặc đang làm
    features = Feature.query.filter(Feature.status.in_(['approved', 'in_progress', 'done'])).order_by(Feature.rice_score.desc()).all()
    
    # Tạo luồng dữ liệu file trong bộ nhớ
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Ghi dòng tiêu đề (Header)
    cw.writerow(['ID', 'Tên tính năng', 'Mô tả', 'Reach', 'Impact', 'Confidence', 'Effort', 'Điểm RICE', 'Trạng thái'])
    
    # Ghi dữ liệu
    for f in features:
        cw.writerow([f.id, f.name, f.description, f.reach, f.impact, f.confidence, f.effort, f.rice_score, f.status])
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=sprint_backlog_roadmap.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

@app.route('/api/rice-guidelines')
@login_required
def get_rice_guidelines():
    """US15: Trả về hướng dẫn định nghĩa khung chấm điểm RICE theo tiêu chuẩn MoMo"""
    return jsonify({
        'reach': 'Reach: Số lượng người dùng (Merchant/Khách hàng) bị ảnh hưởng bởi tính năng này trong vòng 30 ngày.',
        'impact': 'Impact: Thang đo từ 1-5 (5: Cực kỳ quan trọng, 3: Cao, 2: Vừa, 1: Thấp) về mức độ cải thiện trải nghiệm.',
        'confidence': 'Confidence: Độ tự tin của ước tính (%). Nếu dưới 50%, hệ thống sẽ tự động khóa tính điểm để yêu cầu lấy thêm feedback.',
        'effort': 'Effort: Số tuần làm việc (Người-Tuần) cần thiết để thiết kế, phát triển và hoàn thiện tính năng.'
    })


# ─── ĐĂNG NHẬP / ĐĂNG XUẤT (BỔ SUNG ĐỂ SỬA LỖI BUILDERROR) ───────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Sai tên đăng nhập hoặc mật khẩu', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
