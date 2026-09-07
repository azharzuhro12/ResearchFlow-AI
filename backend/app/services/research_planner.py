"""Research Planner — turns a research question into a structured plan.

Uses GLM (via GLMService) to generate the plan. Step 2 only PLANS:
no source searching, no citations, no actual research.
"""

from app.services.glm_service import GLMService
from app.services.json_utils import SafeJSONError, extract_json_object, string_list

PLANNER_SYSTEM_PROMPT = """You are the Research Planner of ResearchFlow AI, an autonomous research automation platform.

Convert the user's research question into a short, structured research plan.

Rules:
- Produce between 3 and 7 steps.
- Each step is one short sentence (max 20 words), written in Indonesian (Bahasa Indonesia).
- Steps must be concrete and directly relevant to the research question.
- Order the steps logically (e.g. identify, analyze, compare, review, summarize).
- You are ONLY planning. Do NOT perform the research, do NOT search for or mention specific sources, and do NOT include any citations or references.
- Do NOT invent findings or conclusions.

Respond with STRICT JSON only — no markdown, no code fences, no extra text — in exactly this shape:
{"plan": ["step 1", "step 2", "step 3"]}"""


class ResearchPlannerError(Exception):
    """Raised when a GLM response cannot be turned into a research plan."""


class ResearchPlanner:
    """Generates research plans from research questions using GLM."""

    def __init__(self, glm: GLMService) -> None:
        self.glm = glm

    async def generate_plan(self, question: str) -> list[str]:
        """Return a list of research steps for the given question."""
        user_prompt = (
            f"Research question: {question}\n\n"
            "Return the research plan as strict JSON, with every step written in Indonesian."
        )
        raw = await self.glm.complete(PLANNER_SYSTEM_PROMPT, user_prompt)
        return parse_plan_payload(raw)


def parse_plan_payload(raw: str) -> list[str]:
    """Safely parse GLM output into a list of plan steps.

    Never uses eval(). Tolerates code fences and prose around the JSON object.
    """
    try:
        return string_list(extract_json_object(raw), "plan")
    except SafeJSONError as exc:
        raise ResearchPlannerError(str(exc)) from exc
