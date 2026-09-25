from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from models import db, Feedback, Feature
from dotenv import load_dotenv
from google import genai
import os
import json

# Phải nạp file .env trước để hệ thống nhận diện được API Key
load_dotenv() 

# Sau đó mới khởi tạo Client Gemini
client = genai.Client() 


app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///prioritylens.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Cấu hình Gemini
client = genai.Client()

with app.app_context():
    db.create_all()

# ─── TRANG CHỦ ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    total_feedback = Feedback.query.count()
    total_features = Feature.query.count()
    approved = Feature.query.filter_by(status='approved').count()
    rejected = Feature.query.filter_by(status='rejected').count()
    pending = Feature.query.filter_by(status='backlog').count()

    # Top 5 tính năng điểm cao nhất
    top_features = Feature.query.filter_by(status='backlog')\
        .order_by(Feature.rice_score.desc()).limit(5).all()

    return render_template('index.html',
        total_feedback=total_feedback,
        total_features=total_features,
        approved=approved,
        rejected=rejected,
        pending=pending,
        top_features=top_features)

# ─── FEEDBACK ────────────────────────────────────────────────────────────────
@app.route('/feedback')
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

            # Làm sạch response nếu có markdown
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
def delete_feedback(id):
    fb = Feedback.query.get_or_404(id)
    db.session.delete(fb)
    db.session.commit()
    flash('Đã xóa phản hồi', 'info')
    return redirect(url_for('feedback_list'))

# ─── BACKLOG ──────────────────────────────────────────────────────────────────
@app.route('/backlog')
def backlog():
    status_filter = request.args.get('status', 'backlog')
    features = Feature.query.filter_by(status=status_filter)\
        .order_by(Feature.rice_score.desc()).all()
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

    # Chỉ tính điểm khi Confidence >= 50%
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
            feature.calculate_rice()
            feature.status = 'backlog'
        except ValueError:
            flash('Giá trị điều chỉnh không hợp lệ', 'danger')
            return redirect(url_for('backlog'))
    else:
        feature.status = decision

    feature.decision_reason = reason
    db.session.commit()

    labels = {'approved': 'Phê duyệt', 'rejected': 'Từ chối', 'adjust': 'Điều chỉnh'}
    flash(f'Đã {labels.get(decision, decision)} tính năng "{feature.name}"', 'success')
    return redirect(url_for('backlog'))

@app.route('/feature/<int:id>/suggest', methods=['POST'])
def suggest_rice(id):
    """Gemini gợi ý điểm RICE dựa trên phản hồi liên quan"""
    feature = Feature.query.get_or_404(id)

    # Lấy feedback liên quan cùng topic
    related = Feedback.query.filter(
        Feedback.topic.ilike(f'%{feature.name[:10]}%')
    ).limit(10).all()

    feedback_text = '\n'.join([f'- [{fb.sentiment}] {fb.content}' for fb in related]) \
        if related else 'Chưa có phản hồi liên quan'

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

        response = gemini_model.generate_content(prompt)
        text = response.text.strip()

        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]

        result = json.loads(text.strip())
        return jsonify({'status': 'ok', 'suggestion': result})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    app.run(debug=True)