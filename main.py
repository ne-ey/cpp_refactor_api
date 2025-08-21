import os, json
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests

load_dotenv()

app = FastAPI(title="AI C++ Refactor API")

class RefactorRequest(BaseModel):
    code: str

@app.get("/ping")
def ping():
    return {"ok": True, "message": "pong"}

@app.post("/refactor")
def refactor(req: RefactorRequest):
    """
    Input:  { "code": "<C++ code>" }
    Output: { ok, suggestions[], optimized_code }
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    api_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    model   = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # No key? graceful fallback
    if not api_key:
        return {
            "ok": False,
            "error": "OPENROUTER_API_KEY missing in .env",
            "suggestions": [
                "Add your OpenRouter API key in .env",
                "Returning input as optimized_code (no AI changes)"
            ],
            "optimized_code": req.code
        }

    system_prompt = (
        "You are a C++ refactoring assistant. "
        "Always reply ONLY with a JSON object having exactly two keys: "
        "'suggestions' (array of short strings) and 'optimized_code' (string)."
    )
    user_prompt = (
        "Refactor to modern C++: use smart pointers where sensible, const-correctness, "
        "range-for, remove dead code, minor perf & safety fixes. "
        "Return ONLY JSON with keys 'suggestions' and 'optimized_code'.\n\n"
        f"CODE:\n{req.code}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 2000
    }

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter request error: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"OpenRouter {resp.status_code}: {resp.text}")

    data = resp.json()
    # Try to read content like Chat Completions
    content = None
    try:
        choices = data.get("choices")
        if choices and isinstance(choices, list):
            content = choices[0].get("message", {}).get("content") or choices[0].get("text")
    except Exception:
        pass
    if not content:
        content = data.get("content") or data.get("text") or json.dumps(data)

    text = content.strip()
    if text.startswith("```"):
        try:
            block = text.split("```", 2)[1]
            text = block.split("\n", 1)[1] if "\n" in block else block
        except Exception:
            pass

    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                parsed = json.loads(text[s:e+1])
            except Exception:
                parsed = None

    if not parsed:
        return {"ok": False, "error": "Model JSON parse failed", "model_text": text, "raw": data}

    suggestions = parsed.get("suggestions", [])
    optimized_code = parsed.get("optimized_code", "")

    return {"ok": True, "suggestions": suggestions, "optimized_code": optimized_code}

