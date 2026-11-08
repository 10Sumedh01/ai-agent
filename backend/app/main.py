from fastapi import FastAPI

app=FastAPI(title="AI AGENT Platform")

@app.get("/health")
async def health():
    return{"status":"ok"}
