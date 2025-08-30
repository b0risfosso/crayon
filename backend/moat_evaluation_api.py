# moat_evaluation_api.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI

router = APIRouter()
client = OpenAI()

class MoatEvalRequest(BaseModel):
    prompt: str

@router.post("/moat_evaluation")
def run_moat_evaluation(req: MoatEvalRequest):
    try:
        resp = client.responses.create(
            model="gpt-5",
            tools=[{"type": "web_search"}],
            reasoning={"effort": "medium"},
            input=req.prompt,
        )
        # Frontend expects {"output_text": "..."} where the value is the JSON block string.
        return {"output_text": resp.output_text}
    except Exception as e:
        # Keep surface simple for the frontend; log full details server-side.
        raise HTTPException(status_code=500, detail=str(e))
