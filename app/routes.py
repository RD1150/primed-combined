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
from app.auth import UserCreate, UserLogin, TokenResponse, register_user, login_user, get_current_user, UserResponse, ForgotPasswordRequest, ResetPasswordRequest, request_password_reset, reset_password
from app.models import User, PracticeSession, SessionFeedback, ValueScript, CustomScenario, SavedPhrase, UserProfile
from app import prompts
import json

settings = get_settings()
router = APIRouter()

@router.post("/auth/register")
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await register_user(data, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration error: {str(e)}")

@router.post("/auth/login")
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    return await login_user(data, db)

@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at)

@router.post("/auth/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    await request_password_reset(data, db)
    return {"ok": True}

@router.post("/auth/reset-password", response_model=TokenResponse)
async def reset_password_endpoint(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    return await reset_password(data, db)

# ════════════════════════════════════════
# ANTHROPIC HELPERS (server-side, prompts locked in app/prompts.py)
# ════════════════════════════════════════

async def _anthropic(system: str, messages: list, max_tokens: int = 1000) -> str:
    """Call Anthropic with a server-owned system prompt and return concatenated text."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
                json={"model": settings.anthropic_model, "max_tokens": max_tokens, "system": system, "messages": messages},
            )
            payload = response.json()
            return "".join(b.get("text", "") for b in payload.get("content", [])).strip()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")


async def _anthropic_json(system: str, messages: list, max_tokens: int = 1200) -> dict:
    """Same as _anthropic but parse a JSON object out of the reply (tolerates code fences)."""
    text = await _anthropic(system, messages, max_tokens)
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean)
    except Exception:
        # last-ditch: grab the outermost {...}
        start, end = clean.find("{"), clean.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(clean[start:end + 1])
            except Exception:
                pass
        raise HTTPException(status_code=502, detail="AI returned malformed JSON")


async def _load_profile(user: User, db: AsyncSession) -> Optional[UserProfile]:
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    return result.scalar_one_or_none()


# ════════════════════════════════════════
# USER PROFILE (P0 — captured once, injected everywhere)
# ════════════════════════════════════════

class ProfileData(BaseModel):
    market: Optional[str] = None
    role_focus: List[str] = []
    brokerage: Optional[str] = None
    voice_sample: Optional[str] = None
    tone: Optional[str] = None

def _profile_dict(p: Optional[UserProfile]) -> dict:
    if not p:
        return {"market": None, "role_focus": [], "brokerage": None, "voice_sample": None, "tone": None, "complete": False}
    return {"market": p.market, "role_focus": p.role_focus or [], "brokerage": p.brokerage,
            "voice_sample": p.voice_sample, "tone": p.tone, "complete": bool(p.market and (p.voice_sample or p.tone))}

@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return _profile_dict(await _load_profile(user, db))

@router.put("/profile")
async def upsert_profile(data: ProfileData, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _load_profile(user, db)
    if not p:
        p = UserProfile(user_id=user.id)
        db.add(p)
    p.market = data.market
    p.role_focus = data.role_focus or []
    p.brokerage = data.brokerage
    p.voice_sample = data.voice_sample
    p.tone = data.tone
    await db.commit()
    await db.refresh(p)
    return _profile_dict(p)


# ════════════════════════════════════════
# MODE ENDPOINTS (locked prompts + profile injection)
# ════════════════════════════════════════

class PersonaIn(BaseModel):
    name: str
    traits: str = ""
    backstory: str = ""
    voice: str = ""
    tells: str = ""

class PracticeOpenerIn(BaseModel):
    persona: PersonaIn
    difficulty: str = "medium"
    scenario_title: str = ""
    scenario_desc: str = ""
    context: Optional[str] = None

@router.post("/practice/opener")
async def practice_opener(data: PracticeOpenerIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await _load_profile(user, db)
    system = prompts.practice_system(
        profile, persona_name=data.persona.name, persona_traits=data.persona.traits,
        persona_backstory=data.persona.backstory, persona_voice=data.persona.voice,
        persona_tells=data.persona.tells, difficulty=data.difficulty,
        scenario_title=data.scenario_title, scenario_desc=data.scenario_desc,
        extra_context=data.context or "")
    reply = await _anthropic(system, [{"role": "user", "content": prompts.PRACTICE_OPENER_USER_MSG}], max_tokens=500)
    return {"reply": reply}

class PracticeReplyIn(BaseModel):
    persona: PersonaIn
    difficulty: str = "medium"
    scenario_title: str = ""
    scenario_desc: str = ""
    context: Optional[str] = None
    transcript: list  # [{role:"agent"|"client", content:str}, ...]

@router.post("/practice/reply")
async def practice_reply(data: PracticeReplyIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await _load_profile(user, db)
    system = prompts.practice_system(
        profile, persona_name=data.persona.name, persona_traits=data.persona.traits,
        persona_backstory=data.persona.backstory, persona_voice=data.persona.voice,
        persona_tells=data.persona.tells, difficulty=data.difficulty,
        scenario_title=data.scenario_title, scenario_desc=data.scenario_desc,
        extra_context=data.context or "")
    messages = [{"role": "user" if t.get("role") == "agent" else "assistant", "content": t.get("content", "")}
                for t in data.transcript]
    reply = await _anthropic(system, messages, max_tokens=1000)
    return {"reply": reply}

class PracticeScoreIn(BaseModel):
    scenario_title: str
    persona_name: str
    persona_traits: str = ""
    difficulty: str = "medium"
    transcript: list  # [{role, content}, ...]

@router.post("/practice/score")
async def practice_score(data: PracticeScoreIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await _load_profile(user, db)
    system = prompts.scoring_system(profile, scenario_title=data.scenario_title,
                                    persona_name=data.persona_name, persona_traits=data.persona_traits,
                                    difficulty=data.difficulty)
    convo = "\n\n".join(f"{'AGENT' if t.get('role') == 'agent' else 'CLIENT'}: {t.get('content','')}" for t in data.transcript)
    return await _anthropic_json(system, [{"role": "user", "content": f"TRANSCRIPT:\n{convo}"}], max_tokens=1200)

class ScenarioIn(BaseModel):
    topic: str

@router.post("/scenario")
async def scenario_intel(data: ScenarioIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await _load_profile(user, db)
    return await _anthropic_json(prompts.scenario_system(profile),
                                 [{"role": "user", "content": prompts.scenario_user_msg(data.topic)}], max_tokens=1400)

class TalkTracksIn(BaseModel):
    ideal_client: str
    favorite_transaction: str
    problem: str
    result: str
    timeframe: Optional[str] = ""
    market: Optional[str] = ""

@router.post("/talk-tracks")
async def talk_tracks(data: TalkTracksIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await _load_profile(user, db)
    # fall back to profile market if the form leaves it blank
    market = data.market or (profile.market if profile else "") or ""
    system = prompts.talk_tracks_system(profile)
    user_msg = prompts.talk_tracks_user_msg(
        ideal_client=data.ideal_client, favorite_transaction=data.favorite_transaction,
        problem=data.problem, result=data.result, timeframe=data.timeframe or "", market=market)
    return await _anthropic_json(system, [{"role": "user", "content": user_msg}], max_tokens=1200)

class CoachHintRequest(BaseModel):
    transcript: list  # [{role: "agent"|"client", content: str}, ...]
    scenario_title: Optional[str] = None
    persona_name: Optional[str] = None
    difficulty: Optional[str] = None

@router.post("/coach-hint")
async def coach_hint(data: CoachHintRequest, user: User = Depends(get_current_user)):
    """Return one tactical move suggestion for what the agent should try next."""
    if not data.transcript:
        return {"hint": "Open with empathy before going into your value. Acknowledge what they said first."}
    convo = "\n".join([f"{'AGENT' if t.get('role') == 'agent' else 'CLIENT'}: {t.get('content','')}" for t in data.transcript])
    context_bits = []
    if data.scenario_title: context_bits.append(f"Scenario: {data.scenario_title}")
    if data.persona_name: context_bits.append(f"Client persona: {data.persona_name}")
    if data.difficulty: context_bits.append(f"Difficulty: {data.difficulty}")
    context_line = " · ".join(context_bits) if context_bits else ""
    system = (
        "You are an elite real estate sales coach watching an agent practice in real time. "
        "Look at the conversation so far and give ONE specific tactical suggestion for the agent's next move. "
        "Be direct and concrete — name the move (anchor, validate, reframe, isolate, mirror, ask a closing question, etc.). "
        "1–2 sentences max. Don't repeat advice the agent already followed. Don't be generic. Don't preach. "
        "Format: start with the move in CAPS, then the specific phrasing. "
        "Example: 'ANCHOR FIRST. Try: \"Most homes in this market are selling within 2% of list — let's set a number that lets us play offense.\"'"
    )
    user_msg = f"{context_line}\n\nConversation so far:\n{convo}\n\nWhat's the single best move the agent should make next?".strip()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
                json={"model": settings.anthropic_model, "max_tokens": 250, "system": system, "messages": [{"role": "user", "content": user_msg}]}
            )
            payload = response.json()
            hint = "".join(b.get("text", "") for b in payload.get("content", [])).strip()
            if not hint:
                raise HTTPException(status_code=502, detail="Empty coach response")
            return {"hint": hint}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Coach service error: {str(e)}")


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

# ════════════════════════════════════════
# SAVED PHRASE LIBRARY
# ════════════════════════════════════════

class SavedPhraseCreate(BaseModel):
    phrase: str
    client_context: Optional[str] = None
    scenario_title: Optional[str] = None
    persona_name: Optional[str] = None
    session_id: Optional[str] = None
    tag: Optional[str] = None

@router.post("/saved-phrases")
async def save_phrase(data: SavedPhraseCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not data.phrase or not data.phrase.strip():
        raise HTTPException(status_code=400, detail="Phrase cannot be empty")
    sp = SavedPhrase(
        user_id=user.id,
        session_id=data.session_id,
        phrase=data.phrase.strip(),
        client_context=data.client_context,
        scenario_title=data.scenario_title,
        persona_name=data.persona_name,
        tag=data.tag
    )
    db.add(sp)
    await db.commit()
    await db.refresh(sp)
    return {"id": sp.id, "phrase": sp.phrase, "client_context": sp.client_context, "scenario_title": sp.scenario_title, "persona_name": sp.persona_name, "tag": sp.tag, "session_id": sp.session_id, "created_at": sp.created_at}

@router.get("/saved-phrases")
async def list_saved_phrases(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SavedPhrase).where(SavedPhrase.user_id == user.id).order_by(SavedPhrase.created_at.desc()))
    rows = result.scalars().all()
    return [{"id": r.id, "phrase": r.phrase, "client_context": r.client_context, "scenario_title": r.scenario_title, "persona_name": r.persona_name, "tag": r.tag, "session_id": r.session_id, "created_at": r.created_at} for r in rows]

@router.delete("/saved-phrases/{phrase_id}")
async def delete_saved_phrase(phrase_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SavedPhrase).where(SavedPhrase.id == phrase_id, SavedPhrase.user_id == user.id))
    sp = result.scalar_one_or_none()
    if not sp:
        raise HTTPException(status_code=404, detail="Saved phrase not found")
    await db.delete(sp)
    await db.commit()
    return {"ok": True}


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


# ════════════════════════════════════════
# TEXT-TO-SPEECH (ElevenLabs)
# ════════════════════════════════════════

class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None

@router.post("/tts")
async def text_to_speech(data: TTSRequest, user: User = Depends(get_current_user)):
    if not settings.elevenlabs_api_key:
        raise HTTPException(status_code=503, detail="TTS not configured")
    voice = data.voice_id or settings.elevenlabs_voice_id
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "text": data.text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
                }
            )
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="TTS service error")
            from fastapi.responses import Response
            return Response(content=response.content, media_type="audio/mpeg")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TTS error: {str(e)}")


# ════════════════════════════════════════
# STRIPE SUBSCRIPTIONS
# ════════════════════════════════════════

import stripe as stripe_lib
from fastapi import Request
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, timezone

@router.post("/billing/create-checkout")
async def create_checkout(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Billing not configured")
    stripe_lib.api_key = settings.stripe_secret_key
    
    # Create or retrieve Stripe customer
    if not user.stripe_customer_id:
        customer = stripe_lib.Customer.create(email=user.email, name=user.name or user.email)
        user.stripe_customer_id = customer.id
        await db.commit()
    
    # Create checkout session
    session = stripe_lib.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url="https://primed-api.onrender.com/app?subscribed=true",
        cancel_url="https://primed-api.onrender.com/app?canceled=true",
        metadata={"user_id": user.id}
    )
    return {"checkout_url": session.url}


@router.post("/billing/portal")
async def billing_portal(user: User = Depends(get_current_user)):
    if not settings.stripe_secret_key or not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")
    stripe_lib.api_key = settings.stripe_secret_key
    session = stripe_lib.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url="https://primed-api.onrender.com/app"
    )
    return {"portal_url": session.url}


@router.get("/billing/status")
async def billing_status(user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    trial_days = 7
    trial_end = user.created_at.replace(tzinfo=timezone.utc) + timedelta(days=trial_days)
    in_trial = now < trial_end
    days_left = max(0, (trial_end - now).days)

    admin_set = {e.strip().lower() for e in (settings.admin_emails or "").split(",") if e.strip()}
    is_admin = user.email.lower() in admin_set

    is_active = user.subscription_status == "active"
    has_access = is_admin or in_trial or is_active

    return {
        "status": "admin" if is_admin else (user.subscription_status or "trial"),
        "in_trial": in_trial,
        "trial_days_left": days_left,
        "has_access": has_access,
        "is_active": is_active or is_admin,
        "stripe_customer_id": user.stripe_customer_id
    }


@router.post("/billing/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    stripe_lib.api_key = settings.stripe_secret_key
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe_lib.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook")
    
    # Handle subscription events
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        if user_id:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user:
                user.subscription_status = "active"
                user.stripe_subscription_id = session.get("subscription")
                await db.commit()
    
    elif event["type"] in ["customer.subscription.updated", "customer.subscription.deleted"]:
        sub = event["data"]["object"]
        customer_id = sub.get("customer")
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalar_one_or_none()
        if user:
            user.subscription_status = sub.get("status", "canceled")
            await db.commit()
    
    return JSONResponse({"received": True})
