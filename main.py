import os
import sys

from fastapi import FastAPI, Response, Cookie, HTTPException, Depends
from pydantic import BaseModel
import redis
import uuid
from typing import Optional
from dotenv import load_dotenv
load_dotenv(override=True)

# Initialize FastAPI app
app = FastAPI()

# --- Redis Configuration and Initialization ---
# Recommended: Use environment variables for cloud deployment configuration
# Host will use REDIS_HOST environment variable, falling back to 'localhost'
REDIS_HOST = os.getenv("REDIS_HOST")
# Port will use REDIS_PORT environment variable, falling back to 6379
REDIS_PORT = int(os.getenv("REDIS_PORT"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME")
# Session expiration time for both Redis key and cookie max_age
SESSION_TIMEOUT_SECONDS = 100

# # Create Redis connection pool for better performance
# Create Redis connection pool for better performance.
try:
    print(f"Attempting to connect to Redis at {REDIS_HOST}:{REDIS_PORT}...")
    redis_pool = redis.ConnectionPool(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=0,
        password=os.getenv("REDIS_PASSWORD"),
        username=REDIS_USERNAME if REDIS_USERNAME else None,  # Use None if username is empty
        decode_responses=True,
    )
    # Perform an initial check to trigger connection/authentication failure early
    temp_r = redis.Redis(connection_pool=redis_pool)
    temp_r.ping()
    print("Redis connection pool established successfully.")

except redis.exceptions.AuthenticationError:
    print("CRITICAL: Redis connection failed due to AuthenticationError (check REDIS_PASSWORD).")
    sys.exit(1)
except redis.exceptions.ConnectionError as e:
    print(f"CRITICAL: Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT}: {e}")
    sys.exit(1)
except Exception as e:
    print(f"CRITICAL: An unexpected error occurred during Redis initialization: {e}")
    sys.exit(1)

# Dependency to get Redis client
def get_redis():
    print("Connecting to Redis")
    return redis.Redis(connection_pool=redis_pool)


# Pydantic models
class LoginRequest(BaseModel):
    username: str


class SessionData(BaseModel):
    user: str
    status: str


# Root endpoint
@app.get("/")
async def root():
    return {"message": "FastAPI Redis Session Management"}


# Login endpoint - creates a session
@app.post("/login")
async def login(request: LoginRequest, response: Response, r: redis.Redis = Depends(get_redis)):
    # Generate unique session ID
    session_id = str(uuid.uuid4())

    # Create session data
    session_data = {
        "user": request.username,
        "status": "active"
    }

    # Store session in Redis with expiration
    r.hset(f"session:{session_id}", mapping=session_data)
    r.expire(f"session:{session_id}", SESSION_TIMEOUT_SECONDS)

    # Set session cookie
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        max_age=SESSION_TIMEOUT_SECONDS
    )

    return {
        "message": "Logged in successfully",
        "session_id": session_id,
        "user": request.username
    }


# Get profile endpoint - retrieves session data
@app.get("/profile")
async def get_profile(
        session_id: Optional[str] = Cookie(None),
        r: redis.Redis = Depends(get_redis)
):
    if not session_id:
        raise HTTPException(status_code=403, detail="No session cookie found")

    # Get session data from Redis
    session_data = r.hgetall(f"session:{session_id}")

    if not session_data:
        raise HTTPException(status_code=404, detail="Session expired or not found")

    # Update session expiration time (sliding expiration)
    r.expire(f"session:{session_id}", SESSION_TIMEOUT_SECONDS)

    return {
        "session_id": session_id,
        "session_data": session_data
    }


# Logout endpoint - deletes session
@app.post("/logout")
async def logout(
        response: Response,
        session_id: Optional[str] = Cookie(None),
        r: redis.Redis = Depends(get_redis)
):
    if session_id:
        # Delete session from Redis
        r.delete(f"session:{session_id}")

    # Clear session cookie
    response.delete_cookie(key="session_id")

    return {"message": "Logged out successfully"}


# Set custom session data endpoint
@app.post("/set-session-data")
async def set_session_data(
        key: str,
        value: str,
        session_id: Optional[str] = Cookie(None),
        r: redis.Redis = Depends(get_redis)
):
    if not session_id:
        raise HTTPException(status_code=403, detail="No session cookie found")

    # Check if session exists
    if not r.exists(f"session:{session_id}"):
        raise HTTPException(status_code=404, detail="Session not found")

    # Set custom data in session
    r.hset(f"session:{session_id}", key, value)
    r.expire(f"session:{session_id}", SESSION_TIMEOUT_SECONDS)

    return {"message": f"Set {key} = {value} in session"}


# Get all sessions (for debugging)
@app.get("/admin/sessions")
async def get_all_sessions(r: redis.Redis = Depends(get_redis)):
    sessions = {}
    for key in r.scan_iter(match="session:*"):
        session_data = r.hgetall(key)
        sessions[key] = session_data
    return {"sessions": sessions}


# Health check endpoint
@app.get("/health")
async def health_check(r: redis.Redis = Depends(get_redis)):
    try:
        # Test Redis connection
        r.ping()
        return {"status": "healthy", "redis": "connected"}
    except Exception as health_check_exception:
        raise HTTPException(status_code=503, detail=f"Redis connection failed: {str(health_check_exception)}")
