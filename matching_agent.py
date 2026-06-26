"""
LangGraph-based candidate matching agent with multi-round screening
and human-in-the-loop feedback.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

load_dotenv()

from agent_tools import (
    DEFAULT_MODEL,
    compare_candidates,
    extract_requirements,
    generate_interview_questions,
    get_vector_db,
    rag_search,
    read_resume_file,
    resolve_candidate,
)

QueryType = Literal["match", "compare", "explain", "questions", "refine", "done"]
FeedbackAction = Literal["refine", "next_round", "done"]


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    jd: Optional[Dict[str, Any]]
    requirements: Optional[Dict[str, Any]]
    all_candidates: List[Dict[str, Any]]
    shortlist: List[Dict[str, Any]]
    final_candidates: List[Dict[str, Any]]
    screening_round: int
    report: Optional[str]
    feedback: Optional[str]
    feedback_action: Optional[FeedbackAction]
    rankings_history: List[Dict[str, Any]]
    query_type: str
    last_user_query: str
    compare_ids: List[str]
    explain_names: List[str]
    question_candidate_id: Optional[str]


def _get_llm():
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=DEFAULT_MODEL, temperature=0.2)


def _latest_user_text(state: AgentState) -> str:
    if state.get("last_user_query"):
        return state["last_user_query"]
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _ai(content: str) -> Dict[str, Any]:
    return {"messages": [AIMessage(content=content)]}


def _snapshot_rankings(candidates: List[Dict[str, Any]], round_num: int, label: str) -> Dict[str, Any]:
    return {
        "round": round_num,
        "label": label,
        "rankings": [
            {
                "rank": idx + 1,
                "candidate_name": c.get("candidate_name", c.get("name", "Unknown")),
                "match_score": c.get("match_score", c.get("score", 0)),
            }
            for idx, c in enumerate(candidates)
        ],
    }


def route_intent(state: AgentState) -> Dict[str, Any]:
    user_text = _latest_user_text(state)
    lowered = user_text.lower()

    if state.get("feedback") and state.get("query_type") == "refine":
        return {"query_type": "refine", "last_user_query": user_text}

    if state.get("feedback") and state.get("shortlist"):
        return {"last_user_query": user_text}

    if any(word in lowered for word in ("compare", "side by side", "versus", " vs ")):
        query_type = "compare"
    elif any(word in lowered for word in ("why", "rank higher", "ranked higher", "explain")):
        query_type = "explain"
    elif any(word in lowered for word in ("question", "interview", "screening")):
        query_type = "questions"
    elif state.get("shortlist") and any(
        word in lowered for word in ("refine", "adjust", "prioritize", "instead", "more", "less")
    ):
        query_type = "refine"
    else:
        query_type = "match"

    return {"query_type": query_type, "last_user_query": user_text}


def parse_jd(state: AgentState) -> Dict[str, Any]:
    user_text = _latest_user_text(state)
    extracted = extract_requirements(user_text)
    jd = extracted.get("jd", {})
    requirements = extracted.get("requirements", {})

    summary = (
        f"Parsed job requirements for {requirements.get('title', 'role')} at "
        f"{requirements.get('company', 'N/A')}.\n"
        f"Required skills: {', '.join(requirements.get('required_skills', [])) or 'N/A'}\n"
        f"Must-haves: {', '.join(requirements.get('must_haves', [])) or 'N/A'}"
    )
    return {
        "jd": jd,
        "requirements": requirements,
        "screening_round": 1,
        **_ai(summary),
    }


def extract_requirements_node(state: AgentState) -> Dict[str, Any]:
    jd = state.get("jd") or {}
    jd_text = json.dumps(jd) if jd else _latest_user_text(state)
    extracted = extract_requirements(jd_text)
    requirements = extracted.get("requirements", {})
    content = (
        "Extracted requirements:\n"
        f"- Must-haves: {', '.join(requirements.get('must_haves', [])) or 'None'}\n"
        f"- Nice-to-haves: {', '.join(requirements.get('nice_to_haves', [])) or 'None'}\n"
        f"- Minimum experience: {requirements.get('min_years_experience', 0)} years"
    )
    return {"requirements": requirements, "jd": extracted.get("jd", jd), **_ai(content)}


def search_resumes_node(state: AgentState) -> Dict[str, Any]:
    screening_round = state.get("screening_round", 1)
    top_k = 30 if screening_round <= 1 else 10
    jd = state.get("jd") or {}
    query = _latest_user_text(state)

    result = rag_search(query=query, top_k=top_k, jd=jd)
    matches = result.get("matches", [])

    content = (
        f"Round {screening_round}: searched resume database and retrieved "
        f"{len(matches)} candidate matches."
    )
    return {"all_candidates": matches, **_ai(content)}


def rank_candidates_node(state: AgentState) -> Dict[str, Any]:
    screening_round = state.get("screening_round", 1)
    candidates = list(state.get("all_candidates", []))
    candidates.sort(key=lambda item: item.get("match_score", 0), reverse=True)

    if screening_round <= 1:
        shortlist = candidates[:10]
        final_candidates: List[Dict[str, Any]] = []
    elif screening_round == 2:
        shortlist = candidates[:10]
        final_candidates = candidates[:5]
    else:
        shortlist = state.get("shortlist", candidates[:10])
        final_candidates = candidates[:3]

    snapshot = _snapshot_rankings(
        shortlist if screening_round <= 2 else final_candidates,
        screening_round,
        f"round_{screening_round}",
    )
    history = list(state.get("rankings_history", []))
    history.append(snapshot)

    lines = ["Ranked candidates:"]
    for idx, candidate in enumerate(shortlist[:5], start=1):
        lines.append(
            f"{idx}. {candidate.get('candidate_name')} "
            f"(score {candidate.get('match_score', 0)})"
        )

    return {
        "shortlist": shortlist,
        "final_candidates": final_candidates,
        "rankings_history": history,
        **_ai("\n".join(lines)),
    }


def _borderline_suggestions(candidate: Dict[str, Any], requirements: Dict[str, Any]) -> str:
    score = candidate.get("match_score", 0)
    if not (50 <= score <= 70):
        return ""
    required = {s.lower() for s in requirements.get("required_skills", [])}
    matched = {s.lower() for s in candidate.get("matched_skills", [])}
    missing = sorted(required - matched)
    if not missing:
        return "Borderline: strengthen portfolio evidence for role-specific impact."
    return f"Borderline: gain demonstrable experience with {', '.join(missing[:3])}."


def _invoke_llm(llm, prompt: str) -> Optional[str]:
    try:
        response = llm.invoke(prompt)
        return str(response.content).strip()
    except Exception:
        return None


def _template_candidate_analysis(
    candidate: Dict[str, Any],
    requirements: Dict[str, Any],
    suggestion: str,
) -> str:
    matched = ", ".join(candidate.get("matched_skills", [])) or "None listed"
    required = ", ".join(requirements.get("required_skills", [])) or "None listed"
    return (
        f"Strengths: matched skills ({matched}); score {candidate.get('match_score', 0)}.\n"
        f"Gaps: required skills not fully evidenced ({required}).\n"
        f"Improvement suggestion: {suggestion or 'Collect stronger project evidence for top requirements.'}"
    )


def generate_report_node(state: AgentState) -> Dict[str, Any]:
    requirements = state.get("requirements") or {}
    shortlist = state.get("shortlist", [])
    llm = _get_llm()

    report_sections: List[str] = ["# Candidate Match Report", ""]
    analysis_targets = shortlist[:10]
    batched_analysis: Dict[str, str] = {}

    batch_prompt = f"""Create concise match analyses for these candidates.

Role: {requirements.get('title', 'Unknown')}
Required skills: {', '.join(requirements.get('required_skills', []))}
Must-haves: {', '.join(requirements.get('must_haves', []))}

Candidates JSON:
{json.dumps(analysis_targets, indent=2)}

Return JSON array where each item has:
candidate_name, strengths, gaps, improvement_suggestion
"""
    batch_text = _invoke_llm(llm, batch_prompt)
    llm_available = batch_text is not None
    if batch_text:
        try:
            text = batch_text
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            parsed = json.loads(text)
            if isinstance(parsed, list):
                for item in parsed:
                    name = item.get("candidate_name")
                    if not name:
                        continue
                    batched_analysis[name] = (
                        f"Strengths: {item.get('strengths', '')}\n"
                        f"Gaps: {item.get('gaps', '')}\n"
                        f"Improvement suggestion: {item.get('improvement_suggestion', '')}"
                    )
        except Exception:
            batched_analysis = {}

    for idx, candidate in enumerate(analysis_targets, start=1):
        suggestion = _borderline_suggestions(candidate, requirements)
        name = candidate.get("candidate_name", "Unknown")
        analysis = batched_analysis.get(name)
        if not analysis and llm_available:
            single_prompt = f"""Write a concise match analysis for this candidate.

Role: {requirements.get('title', 'Unknown')}
Required skills: {', '.join(requirements.get('required_skills', []))}
Must-haves: {', '.join(requirements.get('must_haves', []))}

Candidate: {name}
Score: {candidate.get('match_score')}
Matched skills: {', '.join(candidate.get('matched_skills', []))}
Reasoning: {candidate.get('reasoning', '')}
Excerpts: {' | '.join(candidate.get('relevant_excerpts', [])[:2])}

Include strengths, gaps, and one improvement suggestion.
"""
            analysis = _invoke_llm(llm, single_prompt)
        if not analysis:
            analysis = _template_candidate_analysis(candidate, requirements, suggestion)

        report_sections.append(f"## {idx}. {name} (Score: {candidate.get('match_score')})")
        report_sections.append(analysis.strip())
        if suggestion and suggestion not in analysis:
            report_sections.append(f"Improvement note: {suggestion}")
        report_sections.append("")

    report = "\n".join(report_sections)
    return {"report": report, **_ai(report)}


def _contains_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if " " in phrase:
            if phrase in text:
                return True
        elif re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False


def human_feedback_node(state: AgentState) -> Dict[str, Any]:
    feedback = interrupt(
        {
            "prompt": (
                "Review the match report above. You can:\n"
                "- Refine requirements (e.g., 'prioritize React over Node.js')\n"
                "- Continue to deep screening round\n"
                "- Finish now"
            )
        }
    )
    feedback_text = str(feedback).strip()
    lowered = feedback_text.lower()
    if _contains_phrase(lowered, ("done", "finish", "stop", "exit", "end")):
        action: FeedbackAction = "done"
    elif _contains_phrase(lowered, ("next round", "deep", "screen", "round 2", "round 3")):
        action = "next_round"
    else:
        action = "refine"

    return {
        "feedback": feedback_text,
        "feedback_action": action,
        "last_user_query": feedback_text,
        **_ai(f"Received feedback. Next action: {action}."),
    }


def process_feedback_node(state: AgentState) -> Dict[str, Any]:
    feedback = state.get("feedback") or _latest_user_text(state)
    requirements = dict(state.get("requirements") or {})
    jd = dict(state.get("jd") or {})

    llm = _get_llm()
    prompt = f"""Update hiring requirements based on recruiter feedback.

Current requirements JSON:
{json.dumps(requirements, indent=2)}

Feedback:
{feedback}

Return ONLY updated JSON with keys:
required_skills, must_haves, nice_to_haves, min_years_experience, description
"""
    try:
        response = llm.invoke(prompt)
        text = response.content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        updated = json.loads(text)
        for key, value in updated.items():
            requirements[key] = value
            jd[key] = value
    except Exception:
        if "tensorflow" in feedback.lower():
            for skill in ("TensorFlow", "Machine Learning"):
                if skill not in requirements.get("required_skills", []):
                    requirements.setdefault("required_skills", []).append(skill)
                    jd.setdefault("required_skills", []).append(skill)
        if "react" in feedback.lower() and "React" not in requirements.get("required_skills", []):
            requirements.setdefault("required_skills", []).append("React")
            jd.setdefault("required_skills", []).append("React")

    explanation = (
        "Updated requirements based on your feedback. Re-ranking candidates with the new criteria."
    )
    return {
        "requirements": requirements,
        "jd": jd,
        "feedback": None,
        "feedback_action": None,
        "screening_round": 1,
        "last_user_query": feedback,
        **_ai(explanation),
    }


def compare_candidates_node(state: AgentState) -> Dict[str, Any]:
    user_text = _latest_user_text(state)
    shortlist = state.get("shortlist", [])

    candidate_ids: List[str] = []
    if "top 3" in user_text.lower() or "top three" in user_text.lower():
        candidate_ids = [c.get("candidate_name", "") for c in shortlist[:3]]
    else:
        names = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z']+)+", user_text)
        candidate_ids = names[:3]

    if not candidate_ids and shortlist:
        candidate_ids = [c.get("candidate_name", "") for c in shortlist[:3]]

    result = compare_candidates(candidate_ids)
    matrix = result.get("comparison_matrix", {})
    lines = ["# Side-by-Side Candidate Comparison", ""]
    names = matrix.get("names", [])
    for idx, name in enumerate(names):
        lines.append(f"### {name}")
        lines.append(f"- Experience: {matrix.get('experience_years', [])[idx]} years")
        lines.append(f"- Skills: {', '.join(matrix.get('skills', [])[idx])}")
        lines.append(f"- Education: {', '.join(matrix.get('education', [])[idx])}")
        lines.append(f"- Location: {matrix.get('locations', [])[idx]}")
        lines.append("")

    if result.get("missing"):
        lines.append(f"Could not resolve: {', '.join(result['missing'])}")

    content = "\n".join(lines)
    return {"compare_ids": candidate_ids, **_ai(content)}


def explain_ranking_node(state: AgentState) -> Dict[str, Any]:
    user_text = _latest_user_text(state)
    history = state.get("rankings_history", [])
    shortlist = state.get("shortlist", [])

    names = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z']+)+", user_text)
    name_a = names[0] if len(names) > 0 else None
    name_b = names[1] if len(names) > 1 else None
    lowered = user_text.lower()

    if not name_a and len(shortlist) >= 2:
        if "top" in lowered and "second" in lowered:
            name_a = shortlist[0].get("candidate_name")
            name_b = shortlist[1].get("candidate_name")
        else:
            name_a = shortlist[0].get("candidate_name")
            name_b = shortlist[1].get("candidate_name")

    def _find_rank(name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        for candidate in shortlist:
            if candidate.get("candidate_name", "").lower() == name.lower():
                return candidate
        return None

    cand_a = _find_rank(name_a)
    cand_b = _find_rank(name_b)

    rank_changes = ""
    if len(history) >= 2:
        before = {r["candidate_name"]: r["rank"] for r in history[-2]["rankings"]}
        after = {r["candidate_name"]: r["rank"] for r in history[-1]["rankings"]}
        deltas = []
        for candidate_name, rank in after.items():
            if candidate_name in before and before[candidate_name] != rank:
                deltas.append(
                    f"{candidate_name}: {before[candidate_name]} -> {rank}"
                )
        if deltas:
            rank_changes = "Ranking changes after refinement: " + "; ".join(deltas)

    if cand_a and cand_b:
        score_delta = cand_a.get("match_score", 0) - cand_b.get("match_score", 0)
        content = (
            f"Why {cand_a.get('candidate_name')} ranked above {cand_b.get('candidate_name')}:\n"
            f"- Score difference: {score_delta} points\n"
            f"- {cand_a.get('candidate_name')}: {cand_a.get('reasoning', 'Stronger overall match.')}\n"
            f"- {cand_b.get('candidate_name')}: {cand_b.get('reasoning', 'Good match with gaps.')}\n"
            f"- Matched skills: {', '.join(cand_a.get('matched_skills', []))} vs "
            f"{', '.join(cand_b.get('matched_skills', []))}"
        )
        if rank_changes:
            content += f"\n{rank_changes}"
    else:
        content = "I need two candidate names or an existing shortlist to explain ranking differences."

    return {"explain_names": [n for n in [name_a, name_b] if n], **_ai(content)}


def generate_questions_node(state: AgentState) -> Dict[str, Any]:
    user_text = _latest_user_text(state)
    requirements = state.get("requirements") or {}
    shortlist = state.get("shortlist", [])

    candidate_id = state.get("question_candidate_id")
    if not candidate_id:
        names = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z']+)+", user_text)
        candidate_id = names[0] if names else None
    if not candidate_id and shortlist and "top" in user_text.lower():
        candidate_id = shortlist[0].get("candidate_name")
    if not candidate_id and shortlist:
        candidate_id = shortlist[0].get("candidate_name")

    if not candidate_id:
        return _ai("Please specify which candidate should receive interview questions.")

    result = generate_interview_questions(candidate_id, requirements)
    if not result.get("success"):
        return _ai(result.get("error", "Unable to generate interview questions."))

    lines = [
        f"# Interview Questions for {result.get('candidate_name')}",
        "",
    ]
    for idx, question in enumerate(result.get("questions", []), start=1):
        lines.append(f"{idx}. {question}")

    return {
        "question_candidate_id": result.get("candidate_id"),
        **_ai("\n".join(lines)),
    }


def multi_round_node(state: AgentState) -> Dict[str, Any]:
    screening_round = state.get("screening_round", 1) + 1
    shortlist = state.get("shortlist", [])
    requirements = state.get("requirements") or {}
    llm = _get_llm()

    deep_results: List[Dict[str, Any]] = []
    for candidate in shortlist[:10]:
        resume = resolve_candidate(candidate.get("candidate_name", ""))
        resume_text = ""
        if resume and resume.get("resume_path"):
            file_result = read_resume_file(resume["resume_path"])
            if file_result.get("success"):
                resume_text = str(file_result.get("content", ""))[:4000]

        prompt = f"""Deep screening analysis for round 2.

Role requirements: {json.dumps(requirements, indent=2)}
Candidate summary: {json.dumps(candidate, indent=2)}
Full resume excerpt:
{resume_text}

Return JSON with keys: score (0-100), strengths (array), gaps (array), summary (string)
"""
        try:
            response = llm.invoke(prompt)
            text = response.content.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            analysis = json.loads(text)
        except Exception:
            analysis = {
                "score": candidate.get("match_score", 0),
                "strengths": candidate.get("matched_skills", []),
                "gaps": [],
                "summary": candidate.get("reasoning", ""),
            }

        enriched = {
            **candidate,
            "deep_score": analysis.get("score", candidate.get("match_score", 0)),
            "strengths": analysis.get("strengths", []),
            "gaps": analysis.get("gaps", []),
            "deep_summary": analysis.get("summary", ""),
        }
        deep_results.append(enriched)

    deep_results.sort(key=lambda item: item.get("deep_score", 0), reverse=True)
    final_candidates = deep_results[:5]
    snapshot = _snapshot_rankings(final_candidates, screening_round, "deep_analysis")
    history = list(state.get("rankings_history", []))
    history.append(snapshot)

    lines = [f"Round {screening_round} deep analysis complete. Top finalists:"]
    for idx, candidate in enumerate(final_candidates, start=1):
        lines.append(
            f"{idx}. {candidate.get('candidate_name')} "
            f"(deep score {candidate.get('deep_score', 0)})"
        )

    return {
        "screening_round": screening_round,
        "final_candidates": final_candidates,
        "all_candidates": deep_results,
        "rankings_history": history,
        **_ai("\n".join(lines)),
    }


def final_recommendation_node(state: AgentState) -> Dict[str, Any]:
    screening_round = 3
    finalists = state.get("final_candidates") or state.get("shortlist", [])[:5]
    requirements = state.get("requirements") or {}
    llm = _get_llm()

    lines = ["# Final Hire Recommendation", ""]
    for candidate in finalists[:5]:
        prompt = f"""Final hiring recommendation for this candidate.

Role: {requirements.get('title', 'Unknown')}
Requirements: {json.dumps(requirements, indent=2)}
Candidate: {json.dumps(candidate, indent=2)}

Return JSON with keys:
decision (HIRE or NO_HIRE), confidence (0-100), rationale (string), risks (array)
"""
        try:
            response = llm.invoke(prompt)
            text = response.content.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            decision = json.loads(text)
        except Exception:
            score = candidate.get("deep_score", candidate.get("match_score", 0))
            decision = {
                "decision": "HIRE" if score >= 70 else "NO_HIRE",
                "confidence": score,
                "rationale": candidate.get("deep_summary", candidate.get("reasoning", "")),
                "risks": candidate.get("gaps", []),
            }

        lines.append(f"## {candidate.get('candidate_name')}")
        lines.append(f"- Decision: **{decision.get('decision', 'NO_HIRE')}**")
        lines.append(f"- Confidence: {decision.get('confidence', 0)}%")
        lines.append(f"- Rationale: {decision.get('rationale', '')}")
        risks = decision.get("risks", [])
        if risks:
            lines.append(f"- Risks: {', '.join(risks)}")
        lines.append("")

    snapshot = _snapshot_rankings(finalists[:5], screening_round, "final_recommendation")
    history = list(state.get("rankings_history", []))
    history.append(snapshot)

    content = "\n".join(lines)
    return {
        "screening_round": screening_round,
        "report": content,
        "rankings_history": history,
        **_ai(content),
    }


def _route_from_intent(state: AgentState) -> str:
    mapping = {
        "match": "parse_jd",
        "compare": "compare_candidates_node",
        "explain": "explain_ranking_node",
        "questions": "generate_questions_node",
        "refine": "process_feedback_node",
        "done": END,
    }
    return mapping.get(state.get("query_type", "match"), "parse_jd")


def _route_after_feedback(state: AgentState) -> str:
    action = state.get("feedback_action")
    if action == "refine":
        return "search_resumes"
    if action == "next_round":
        return "multi_round"
    return END


def build_matching_graph():
    get_vector_db(reset_collection=False)

    builder = StateGraph(AgentState)

    builder.add_node("route_intent", route_intent)
    builder.add_node("parse_jd", parse_jd)
    builder.add_node("extract_requirements", extract_requirements_node)
    builder.add_node("search_resumes", search_resumes_node)
    builder.add_node("rank_candidates", rank_candidates_node)
    builder.add_node("generate_report", generate_report_node)
    builder.add_node("human_feedback", human_feedback_node)
    builder.add_node("process_feedback", process_feedback_node)
    builder.add_node("compare_candidates_node", compare_candidates_node)
    builder.add_node("explain_ranking_node", explain_ranking_node)
    builder.add_node("generate_questions_node", generate_questions_node)
    builder.add_node("multi_round", multi_round_node)
    builder.add_node("final_recommendation", final_recommendation_node)

    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", _route_from_intent)
    builder.add_edge("parse_jd", "extract_requirements")
    builder.add_edge("extract_requirements", "search_resumes")
    builder.add_edge("search_resumes", "rank_candidates")
    builder.add_edge("rank_candidates", "generate_report")
    builder.add_edge("generate_report", "human_feedback")
    builder.add_conditional_edges("human_feedback", _route_after_feedback)
    builder.add_edge("process_feedback", "search_resumes")
    builder.add_edge("multi_round", "final_recommendation")
    builder.add_edge("final_recommendation", END)
    builder.add_edge("compare_candidates_node", END)
    builder.add_edge("explain_ranking_node", END)
    builder.add_edge("generate_questions_node", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


class MatchingAgent:
    """High-level wrapper around the compiled LangGraph matching workflow."""

    def __init__(self) -> None:
        self.graph = build_matching_graph()

    def initial_state(self, user_query: str) -> Dict[str, Any]:
        return {
            "messages": [HumanMessage(content=user_query)],
            "last_user_query": user_query,
        }

    def resume_feedback(self, feedback: str) -> Any:
        from langgraph.types import Command

        return Command(resume=feedback)


graph = build_matching_graph()
