import os, json, re, requests, time, logging
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import config


# ---------------- Load ENV ----------------
load_dotenv()

# ---------------- Logging Setup ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log"),   # Save logs to file
        logging.StreamHandler()           # Show logs in console
    ]
)
logger = logging.getLogger(__name__)

# ---------------- FastAPI Init ----------------
app = FastAPI(title="AI C++ Refactor API")


print(config.OPENROUTER_API_KEY)
print(config.OPENROUTER_MODEL)
# Middleware for request logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(
        f"{request.method} {request.url.path} "
        f"completed_in={duration:.2f}s "
        f"status_code={response.status_code}"
    )
    return response

# Enable CORS (frontend se call allow hoga)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Models ----------------
class RuleConfig(BaseModel):
    enable_dsa_rules: bool = True
    enable_style_rules: bool = True
    max_nested_loops: int = 2
    prefer_unordered_map_for_count: bool = True
    suggest_reserve_on_vectors: bool = True
    suggest_emplace_back: bool = True
    prefer_range_for: bool = True

class RefactorRequest(BaseModel):
    code: str
    rules: Optional[RuleConfig] = None  

class RuleResult(BaseModel):
    rule: str
    message: str
    line: Optional[int] = None
    severity: str = "info"  

# ---------------- Utils ----------------
def _line_no(code: str, match_start: int) -> int:
    return code[:match_start].count("\n") + 1

def run_builtin_rules(code: str, cfg: RuleConfig) -> List[RuleResult]:
    """Lightweight static analysis for C++."""
    results: List[RuleResult] = []

    # Rule: Prefer range-based for
    if cfg.enable_style_rules and cfg.prefer_range_for:
        for m in re.finditer(r"for\s*\(\s*(int|size_t)\s+\w+\s*=\s*0\s*;\s*\w+\s*<\s*\w+\.size\(\)\s*;", code):
            results.append(RuleResult(
                rule="prefer_range_for",
                message="Use range-based for when iterating entire container.",
                line=_line_no(code, m.start())
            ))

    # Rule: Nested loop detection
    if cfg.enable_dsa_rules and cfg.max_nested_loops > 0:
        for m in re.finditer(r"for\s*\(.*\)\s*\{[^{}]{0,120}for\s*\(", code, flags=re.DOTALL):
            results.append(RuleResult(
                rule="nested_loops",
                message="Nested loop detected; if O(n^2) consider hashing, prefix sums, or two-pointer.",
                line=_line_no(code, m.start()),
                severity="warning"
            ))

    # Rule: Prefer unordered_map for counting
    if cfg.enable_dsa_rules and cfg.prefer_unordered_map_for_count:
        for m in re.finditer(r"for\s*\(.*\)\s*\{[^{}]{0,200}(?:map|std::map)<", code, flags=re.DOTALL):
            results.append(RuleResult(
                rule="prefer_unordered_map_for_count",
                message="Counting frequencies? Prefer std::unordered_map for average O(1) access.",
                line=_line_no(code, m.start())
            ))

    # Rule: Suggest reserve on vectors
    if cfg.enable_style_rules and cfg.suggest_reserve_on_vectors:
        if re.search(r"std::vector<[^>]+>\s+\w+\s*;", code) and \
           re.search(r"for\s*\(.*\)\s*\{[^{}]{0,180}\.push_back\(", code, flags=re.DOTALL):
            results.append(RuleResult(
                rule="vector_reserve",
                message="Vector push_back in loop: call v.reserve(n) when size known to avoid re-allocations."
            ))

    # Rule: Suggest emplace_back
    if cfg.enable_style_rules and cfg.suggest_emplace_back:
        for m in re.finditer(r"\.push_back\s*\(\s*(?:std::)?make_pair\s*\(", code):
            results.append(RuleResult(
                rule="emplace_back",
                message="Use emplace_back instead of push_back(make_pair(...)).",
                line=_line_no(code, m.start())
            ))

    return results

# ---------------- Routes ----------------
@app.get("/ping")
def ping():
    return {"ok": True, "message": "pong"}

@app.post("/refactor")
def refactor(req: RefactorRequest):
    code = req.code
    cfg = req.rules or RuleConfig()

    # Local rule-based findings
    rules_findings = [r.dict() for r in run_builtin_rules(code, cfg)]

    api_key = os.getenv("OPENROUTER_API_KEY")
    api_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    model   = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # If API key missing → return only local analysis
    if not api_key:
        return {
            "ok": False,
            "error": "OPENROUTER_API_KEY missing in .env",
            "suggestions": [f"[rule:{f['rule']}] {f['message']}" for f in rules_findings],
            "optimized_code": code,
            "rules_findings": rules_findings
        }

    # ---------------- Prepare LLM Prompt ----------------
    system_prompt = (
        "You are a C++ refactoring assistant. Reply ONLY with JSON having keys "
        "'suggestions' (array of strings) and 'optimized_code' (string)."
    )
    rule_hints = []
    if cfg.prefer_range_for: rule_hints.append("prefer range-based for")
    if cfg.prefer_unordered_map_for_count: rule_hints.append("prefer unordered_map for counting")
    if cfg.suggest_reserve_on_vectors: rule_hints.append("suggest vector.reserve when size known")
    if cfg.suggest_emplace_back: rule_hints.append("prefer emplace_back over push_back(make_pair)")
    if cfg.max_nested_loops: rule_hints.append("avoid >2 nested loops if possible")

    user_prompt = (
        "Refactor to modern C++ (smart pointers where sensible, const-correctness, range-for, remove dead code, "
        "minor perf & safety fixes). Consider these project rules: "
        + "; ".join(rule_hints) + ". "
        "Return ONLY JSON with keys 'suggestions' and 'optimized_code'.\n\n"
        f"CODE:\n{code}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",   
        "X-Title": "Cpp Refactor API"              
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

    # ---------------- API Call ----------------
    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter request error: {e}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"OpenRouter {resp.status_code}: {resp.text}")

    data = resp.json()
    content = None
    try:
        choices = data.get("choices")
        if choices and isinstance(choices, list):
            content = choices[0].get("message", {}).get("content") or choices[0].get("text")
    except Exception:
        pass
    if not content:
        content = data.get("content") or data.get("text") or json.dumps(data)

    # Clean JSON block if wrapped in ```
    text = content.strip()
    if text.startswith("```"):
        try:
            block = text.split("```", 2)[1]
            text = block.split("\n", 1)[1] if "\n" in block else block
        except Exception:
            pass

    # Parse JSON response
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
        return {"ok": False, "error": "Model JSON parse failed", "model_text": text, "raw": data, "rules_findings": rules_findings}

    suggestions = parsed.get("suggestions", [])
    optimized_code = parsed.get("optimized_code", "")

    # Merge rule suggestions
    merged_suggestions = [f"[rule:{f['rule']}] {f['message']}" for f in rules_findings] + suggestions

    return {
        "ok": True,
        "suggestions": merged_suggestions,
        "optimized_code": optimized_code or code,
        "rules_findings": rules_findings
    }
