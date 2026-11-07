from fastapi import FastAPI

app = FastAPI(title="CQA evaluation API", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "api_version": "1"}
