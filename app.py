import os
import json
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, make_response
from dotenv import load_dotenv
from google import genai
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Feedback, Feature, BehaviorLog, User

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

    query = Feedback.query
    if source_filter:
        query = query.filter_by(source=source_filter)
    if sentiment_filter:
        query = query.filter_by(sentiment=sentiment_filter)

    feedbacks = query.order_by(Feedback.created_at.desc()).all()
    unanalyzed = Feedback.query.filter_by(sentiment=None).count()

    return render_template('feedback.html',
        feedbacks=feedbacks,
        unanalyzed=unanalyzed,
        source_filter=source_filter,
        sentiment_filter=sentiment_filter)

@app.route('/feedback/add', methods=['POST'])
@login_required
def add_feedback():
    content = request.form.get('content', '').strip()
    source = request.form.get('source', 'merchant')

    if not content:
        flash('Vui lòng nhập nội dung phản hồi', 'danger')
        return redirect(url_for('feedback_list'))

    fb = Feedback(content=content, source=source)
    db.session.add(fb)
    db.session.commit()
    flash('Đã thêm phản hồi thành công', 'success')
    return redirect(url_for('feedback_list'))

@app.route('/feedback/analyze', methods=['POST'])
@login_required
def analyze_feedback():
    feedbacks = Feedback.query.filter_by(sentiment=None).all()

    if not feedbacks:
        return jsonify({'status': 'ok', 'message': 'Không có phản hồi mới cần phân tích'})

    analyzed = 0
    errors = 0

    for fb in feedbacks:
        try:
            prompt = f"""Phân tích phản hồi sau của người dùng ứng dụng MoMo và trả về JSON.

Phản hồi: {fb.content}

Yêu cầu: Chỉ trả về JSON, không giải thích thêm, đúng format sau:
{{"sentiment": "positive" hoặc "negative" hoặc "neutral", "topic": "chủ đề ngắn 3-5 từ tiếng Việt"}}"""

            chat = client.chats.create(model="gemini-3.6-flash")
            response = chat.send_message(prompt)
            text = response.text.strip()

            if '```' in text:
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]

            result = json.loads(text.strip())
            fb.sentiment = result.get('sentiment', 'neutral')
            fb.topic = result.get('topic', 'Chưa phân loại')
            analyzed += 1

        except Exception as e:
            fb.sentiment = 'neutral'
            fb.topic = 'Lỗi phân tích'
            errors += 1

    db.session.commit()
    return jsonify({
        'status': 'ok',
        'message': f'Đã phân tích {analyzed} phản hồi. Lỗi: {errors}'
    })

@app.route('/feedback/delete/<int:id>', methods=['POST'])
@login_required
def delete_feedback(id):
    fb = Feedback.query.get_or_404(id)
    db.session.delete(fb)
    db.session.commit()
    flash('Đã xóa phản hồi', 'info')
    return redirect(url_for('feedback_list'))

@app.route('/feedback/import', methods=['POST'])
@login_required
def import_feedback():
    file = request.files.get('csv_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Vui lòng chọn file CSV hợp lệ', 'danger')
        return redirect(url_for('feedback_list'))

    stream = io.StringIO(file.stream.read().decode('utf-8'))
    reader = csv.DictReader(stream)
    count = 0

    for row in reader:
        content = row.get('content') or row.get('noi_dung') or ''
        source = row.get('source') or row.get('nguon') or 'support'
        if content.strip():
            fb = Feedback(content=content.strip(), source=source.strip())
            db.session.add(fb)
            count += 1

    db.session.commit()
    flash(f'Đã import {count} phản hồi từ file CSV', 'success')
    return redirect(url_for('feedback_list'))

# ─── BACKLOG (QUẢN LÝ TÍNH NĂNG & ĐIỂM RICE) ──────────────────────────────────
@app.route('/backlog')
@login_required 
def backlog():
    status_filter = request.args.get('status', 'backlog')
    
    # ─── ĐÂY LÀ DÒNG QUAN TRỌNG BỊ THIẾU HOẶC SAI TÊN BIẾN ───
    # Lấy danh sách tính năng theo bộ lọc trạng thái và áp dụng quy tắc BR6 (Xếp hạng đa tầng)
    features = Feature.query.filter_by(status=status_filter)\
        .order_by(Feature.rice_score.desc(), Feature.effort.asc(), Feature.confidence.desc()).all()
        
    all_status_count = {
        'backlog': Feature.query.filter_by(status='backlog').count(),
        'approved': Feature.query.filter_by(status='approved').count(),
        'rejected': Feature.query.filter_by(status='rejected').count(),
    }
    return render_template('backlog.html',
        features=features,
        status_filter=status_filter,
        all_status_count=all_status_count)

@app.route('/feature/add', methods=['POST'])
@login_required
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
    db.session.commit()

    if confidence < 50:
        flash(f'⚠️ Tính năng "{name}" được thêm nhưng chưa tính điểm vì Confidence < 50%. Thu thập thêm phản hồi rồi cập nhật lại.', 'warning')
    else:
        flash(f'✅ Đã thêm tính năng "{name}" với điểm RICE: {feature.rice_score}', 'success')

    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/decision', methods=['POST'])
@login_required
def feature_decision(id):
    feature = Feature.query.get_or_404(id)
    decision = request.form.get('decision')
    reason = request.form.get('reason', '').strip()

    if not reason:
        flash('Vui lòng ghi lý do quyết định (bắt buộc theo US22)', 'danger')
        return redirect(url_for('backlog'))

    if decision == 'adjust':
        try:
            feature.impact = float(request.form.get('impact', feature.impact))
            feature.confidence = float(request.form.get('confidence', feature.confidence))
            feature.effort = float(request.form.get('effort', feature.effort))
            
            # Khối chặn điểm RICE theo quy tắc BR3 của tài liệu PRD
            if feature.confidence >= 50:
                feature.calculate_rice()
            else:
                feature.rice_score = 0  
                flash(f'⚠️ Điểm RICE được đưa về 0 do Confidence thấp hơn 50%', 'warning')
                
            feature.status = 'backlog'
        except ValueError:
            flash('Giá trị điều chỉnh không hợp lệ', 'danger')
            return redirect(url_for('backlog'))
    else:
        feature.status = decision

    # GIỮ NGUYÊN ĐOẠN NÀY CỦA BẠN:
    feature.decision_reason = reason
    db.session.commit()

    labels = {'approved': 'Phê duyệt', 'rejected': 'Từ chối', 'adjust': 'Điều chỉnh'}
    flash(f'Đã {labels.get(decision, decision)} tính năng "{feature.name}"', 'success')
    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/status', methods=['POST'])
@login_required
def update_feature_status(id):
    """US27: Cập nhật trạng thái tính năng từ Approved sang In Progress hoặc Done"""
    feature = Feature.query.get_or_404(id)
    new_status = request.form.get('status')
    
    if new_status in ['in_progress', 'done', 'approved']:
        feature.status = new_status
        db.session.commit()
        flash(f'Đã cập nhật trạng thái tính năng "{feature.name}" sang thành công!', 'success')
    else:
        flash('Trạng thái không hợp lệ', 'danger')
        
    return redirect(url_for('backlog', status=request.form.get('current_filter', 'approved')))

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

# ─── THỐNG KÊ & THEO DÕI HÀNH VI (BEHAVIOR LOGS) ──────────────────────────────
@app.route('/track', methods=['POST'])
def track_behavior():
    data = request.get_json()
    log = BehaviorLog(
        event_type=data.get('event_type', 'click'),
        page=data.get('page', ''),
        element=data.get('element', ''),
        user_type=data.get('user_type', 'merchant')
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/behavior')
@login_required
def behavior_log():
    logs = BehaviorLog.query.order_by(BehaviorLog.created_at.desc()).limit(100).all()
    dropout_count = BehaviorLog.query.filter_by(event_type='dropout').count()
    click_count = BehaviorLog.query.filter_by(event_type='click').count()
    return render_template('behavior.html',
        logs=logs,
        dropout_count=dropout_count,
        click_count=click_count)

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
