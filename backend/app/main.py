from fastapi import FastAPI

from app.api import auth, conversations, documents

app = FastAPI(title="AI Agent Platform")
app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(documents.router)


@app.get("/health")
async def health():
    return {"status": "ok"}