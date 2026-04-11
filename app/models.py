from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

def gen_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sessions = relationship("PracticeSession", back_populates="user")
    value_scripts = relationship("ValueScript", back_populates="user")
    custom_scenarios = relationship("CustomScenario", back_populates="user")

class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    scenario_id = Column(String, nullable=False)
    scenario_title = Column(String, nullable=False)
    persona_id = Column(String, nullable=False)
    persona_name = Column(String, nullable=False)
    difficulty = Column(String, nullable=False, default="medium")
    context = Column(Text, nullable=True)
    transcript = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    user = relationship("User", back_populates="sessions")
    feedback = relationship("SessionFeedback", back_populates="session", uselist=False)

class SessionFeedback(Base):
    __tablename__ = "session_feedback"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("practice_sessions.id"), nullable=False)
    clarity = Column(Integer, nullable=False)
    empathy = Column(Integer, nullable=False)
    persuasion = Column(Integer, nullable=False)
    confidence = Column(Integer, nullable=False)
    overall = Column(Integer, nullable=False)
    strengths = Column(JSON, nullable=False, default=list)
    improvements = Column(JSON, nullable=False, default=list)
    suggested_phrasing = Column(Text, nullable=True)
    next_focus = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    session = relationship("PracticeSession", back_populates="feedback")

class ValueScript(Base):
    __tablename__ = "value_scripts"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    ideal_client = Column(Text, nullable=False)
    favorite_transaction = Column(Text, nullable=False)
    problem = Column(Text, nullable=False)
    result = Column(Text, nullable=False)
    timeframe = Column(Text, nullable=True)
    market = Column(Text, nullable=True)
    generated_scripts = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="value_scripts")

class CustomScenario(Base):
    __tablename__ = "custom_scenarios"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    input_topic = Column(Text, nullable=False)
    generated_data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="custom_scenarios")
