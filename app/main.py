import os
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import firebase_admin
from firebase_admin import credentials

from app.core.config import (
    APP_TITLE,
    APP_VERSION,
    APP_DESCRIPTION,
    CORS_ORIGINS,
    CORS_CREDENTIALS,
    CORS_METHODS,
    CORS_HEADERS
)

# Initialize Firebase Admin SDK
firebase_credentials = os.getenv("FIREBASE_CREDENTIALS")
if firebase_credentials:
    try:
        cred_dict = json.loads(firebase_credentials)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("Firebase Admin SDK initialized successfully")
    except Exception as e:
        print(f"Warning: Failed to initialize Firebase Admin SDK: {e}")
else:
    print("Warning: FIREBASE_CREDENTIALS environment variable not set")

from app.api.routes import (
    quizzes,
    flashcards,
    library,
    sessions,
    analytics,
    users,
    reviews,
    results,
    leaderboard,
    categories,
    websocket,
    live_multiplayer,
    notes,
    course_pack,
    study_sets,
    ai_generation,
    video
)

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=APP_DESCRIPTION
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_CREDENTIALS,
    allow_methods=CORS_METHODS,
    allow_headers=CORS_HEADERS,
)

# Include routers
app.include_router(quizzes.router)
app.include_router(flashcards.router)
app.include_router(notes.router)
app.include_router(course_pack.router)
app.include_router(study_sets.router)
app.include_router(ai_generation.router)
app.include_router(video.router)
app.include_router(library.router)
app.include_router(sessions.router)
app.include_router(analytics.router)
app.include_router(users.router)
app.include_router(reviews.router)
app.include_router(results.router)
app.include_router(leaderboard.router)
app.include_router(categories.router)
app.include_router(live_multiplayer.router)
app.include_router(websocket.router)

@app.get("/")
async def root():
    return {
        "success": True,
        "message": "Quiz API is running!",
        "version": APP_VERSION,
        "endpoints": "/docs for API documentation"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint for server wake-up pings"""
    return {"status": "ok", "version": APP_VERSION}

# Local development:
# uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
