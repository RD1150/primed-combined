from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import httpx
from app.database import get_db
from app.config import get_settings
from app.auth import UserCreate, UserLogin, TokenResponse, register_user, login_user, get_current_user, UserResponse
from app.models import User, PracticeSession, SessionFeedback, ValueScript, CustomScenario

settings = get_settings()
router = APIRouter()

@router.post("/auth/register", response_model=TokenResponse)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await register_user(data, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration error: {str(e)}")

@router.post("/auth/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    return await login_user(data, db)

@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at)

class AIRequest(BaseModel):
    messages: list
    system: Optional[str] = None
    max_tokens: int = 1000

@router.post("/ai/generate")
async def ai_proxy(data: AIRequest, user: User = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        body = {"model": settings.anthropic_model, "max_tokens": data.max_tokens, "messages": data.messages}
        if data.system:
            body["system"] = data.system
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
                json=body
            )
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")

class SessionCreate(BaseModel):
    scenario_id: str
    scenario_title: str
    persona_id: str
    persona_name: str
    difficulty: str = "medium"
    context: Optional[str] = None

class SessionTurn(BaseModel):
    role: str
    content: str

class FeedbackCreate(BaseModel):
    clarity: int
    empathy: int
    persuasion: int
    confidence: int
    overall: int
    strengths: List[str]
    improvements: List[str]
    suggested_phrasing: Optional[str] = None
    next_focus: Optional[str] = None

class SessionResponse(BaseModel):
    id: str
    scenario_id: str
    scenario_title: str
    persona_id: str
    persona_name: str
    difficulty: str
    context: Optional[str]
    transcript: list
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    feedback: Optional[dict] = None

def _session_response(session, feedback=None):
    fb = None
    if feedback:
        fb = {"clarity": feedback.clarity, "empathy": feedback.empathy, "persuasion": feedback.persuasion, "confidence": feedback.confidence, "overall": feedback.overall, "strengths": feedback.strengths, "improvements": feedback.improvements, "suggested_phrasing": feedback.suggested_phrasing, "next_focus": feedback.next_focus}
    return SessionResponse(id=session.id, scenario_id=session.scenario_id, scenario_title=session.scenario_title, persona_id=session.persona_id, persona_name=session.persona_name, difficulty=session.difficulty, context=session.context, transcript=session.transcript, status=session.status, created_at=session.created_at, completed_at=session.completed_at, feedback=fb)

@router.post("/sessions", response_model=SessionResponse)
async def create_session(data: SessionCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session = PracticeSession(user_id=user.id, scenario_id=data.scenario_id, scenario_title=data.scenario_title, persona_id=data.persona_id, persona_name=data.persona_name, difficulty=data.difficulty, context=data.context, transcript=[], status="active")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_response(session)

@router.post("/sessions/{session_id}/turns", response_model=SessionResponse)
async def add_turn(session_id: str, turn: SessionTurn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PracticeSession).where(PracticeSession.id == session_id, PracticeSession.user_id == user.id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    transcript = list(session.transcript)
    transcript.append({"role": turn.role, "content": turn.content})
    session.transcript = transcript
    await db.commit()
    await db.refresh(session)
    return _session_response(session)

@router.post("/sessions/{session_id}/end", response_model=SessionResponse)
async def end_session(session_id: str, feedback_data: FeedbackCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PracticeSession).where(PracticeSession.id == session_id, PracticeSession.user_id == user.id).options(selectinload(PracticeSession.feedback)))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    feedback = SessionFeedback(session_id=session.id, clarity=feedback_data.clarity, empathy=feedback_data.empathy, persuasion=feedback_data.persuasion, confidence=feedback_data.confidence, overall=feedback_data.overall, strengths=feedback_data.strengths, improvements=feedback_data.improvements, suggested_phrasing=feedback_data.suggested_phrasing, next_focus=feedback_data.next_focus)
    db.add(feedback)
    await db.commit()
    await db.refresh(session)
    return _session_response(session, feedback)

@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PracticeSession).where(PracticeSession.user_id == user.id).options(selectinload(PracticeSession.feedback)).order_by(PracticeSession.created_at.desc()).limit(50))
    sessions = result.scalars().all()
    return [_session_response(s, s.feedback) for s in sessions]

class ValueScriptCreate(BaseModel):
    ideal_client: str
    favorite_transaction: str
    problem: str
    result: str
    timeframe: Optional[str] = None
    market: Optional[str] = None
    generated_scripts: dict

@router.post("/value-scripts")
async def save_value_script(data: ValueScriptCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    script = ValueScript(user_id=user.id, ideal_client=data.ideal_client, favorite_transaction=data.favorite_transaction, problem=data.problem, result=data.result, timeframe=data.timeframe, market=data.market, generated_scripts=data.generated_scripts)
    db.add(script)
    await db.commit()
    await db.refresh(script)
    return {"id": script.id, "created_at": script.created_at}

@router.get("/value-scripts")
async def list_value_scripts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ValueScript).where(ValueScript.user_id == user.id).order_by(ValueScript.created_at.desc()))
    return [{"id": s.id, "generated_scripts": s.generated_scripts, "created_at": s.created_at} for s in result.scalars().all()]

class CustomScenarioCreate(BaseModel):
    input_topic: str
    generated_data: dict

@router.post("/custom-scenarios")
async def save_custom_scenario(data: CustomScenarioCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    scenario = CustomScenario(user_id=user.id, input_topic=data.input_topic, generated_data=data.generated_data)
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return {"id": scenario.id, "created_at": scenario.created_at}

@router.get("/stats")
async def user_stats(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sessions_result = await db.execute(select(PracticeSession).where(PracticeSession.user_id == user.id))
    sessions = sessions_result.scalars().all()
    completed = [s for s in sessions if s.status == "completed"]
    feedback_result = await db.execute(select(SessionFeedback).where(SessionFeedback.session_id.in_([s.id for s in completed]))) if completed else None
    feedbacks = feedback_result.scalars().all() if feedback_result else []
    avg = {}
    if feedbacks:
        avg = {k: round(sum(getattr(f, k) for f in feedbacks) / len(feedbacks)) for k in ["clarity", "empathy", "persuasion", "confidence", "overall"]}
    return {"total_sessions": len(sessions), "completed_sessions": len(completed), "average_scores": avg, "member_since": user.created_at}
