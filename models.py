from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_login import UserMixin

db = SQLAlchemy()


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(50), default='merchant')
    sentiment = db.Column(db.String(20))
    topic = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Feature(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    reach = db.Column(db.Float, default=0)
    impact = db.Column(db.Float, default=0)
    confidence = db.Column(db.Float, default=0)
    effort = db.Column(db.Float, default=1)
    rice_score = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='backlog')
    decision_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def calculate_rice(self):
        if self.effort > 0 and self.confidence >= 50:
            self.rice_score = round(
                (self.reach * self.impact * (self.confidence / 100))
                / self.effort,
                2
            )
        else:
            self.rice_score = 0

        return self.rice_score


class BehaviorLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50))
    page = db.Column(db.String(100))
    element = db.Column(db.String(100))
    user_type = db.Column(db.String(50), default='merchant')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='junior_po')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FeatureFeedback(db.Model):
    __tablename__ = 'feature_feedback'

    id = db.Column(db.Integer, primary_key=True)
    feature_id = db.Column(
        db.Integer,
        db.ForeignKey('feature.id'),
        nullable=False,
        index=True
    )
    feedback_id = db.Column(
        db.Integer,
        db.ForeignKey('feedback.id'),
        nullable=False,
        index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Decision(db.Model):
    __tablename__ = 'decision'

    id = db.Column(db.Integer, primary_key=True)
    feature_id = db.Column(
        db.Integer,
        db.ForeignKey('feature.id'),
        nullable=False,
        index=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False,
        index=True
    )
    decision_type = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    before_snapshot = db.Column(db.Text)
    after_snapshot = db.Column(db.Text)
    feedback_ids = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FeatureSquad(db.Model):
    __tablename__ = 'feature_squad'

    id = db.Column(db.Integer, primary_key=True)
    feature_id = db.Column(
        db.Integer,
        db.ForeignKey('feature.id'),
        nullable=False,
        index=True
    )
    squad = db.Column(
        db.String(100),
        nullable=False,
        default='Core'
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ImpactFeedback(db.Model):
    __tablename__ = 'impact_feedback'

    id = db.Column(db.Integer, primary_key=True)
    feature_id = db.Column(
        db.Integer,
        db.ForeignKey('feature.id'),
        nullable=False,
        index=True
    )
    estimated_impact = db.Column(db.Float, nullable=False)
    actual_impact = db.Column(db.Float, nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)