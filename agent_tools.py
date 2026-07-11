"""
Agent tools for the LangGraph matching agent.
Wraps Milestone 2 RAG/matching capabilities and routes Milestone 1 file
operations through the filesystem MCP server.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from job_matcher import JobMatcher, load_job_descriptions
from mcp_client import get_mcp_manager
from resume_rag import ResumeVectorDB, load_resumes_from_files

DEFAULT_RESUME_DIR = os.getenv("MATCHING_AGENT_RESUME_DIR", "data/synthetic_resumes")
DEFAULT_JD_DIR = os.getenv("MATCHING_AGENT_JD_DIR", "data/job_descriptions")
DEFAULT_CHROMA_DIR = os.getenv("MATCHING_AGENT_CHROMA_DIR", "data/chroma_db")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_RESUME_INDEX: Dict[str, Dict[str, Any]] = {}
_RESUME_BY_NAME: Dict[str, Dict[str, Any]] = {}
_VECTOR_DB: Optional[ResumeVectorDB] = None
_JOB_MATCHER: Optional[JobMatcher] = None
_MCP_INITIALIZED = False


def _ensure_mcp() -> None:
    """Initialize MCP client connections on first use."""
    global _MCP_INITIALIZED
    if _MCP_INITIALIZED:
        return
    enable_web = os.getenv("MCP_ENABLE_WEB_SEARCH", "0") == "1"
    get_mcp_manager(enable_filesystem=True, enable_web_search=enable_web)
    _MCP_INITIALIZED = True


def _mcp_call(tool_name: str, arguments: Dict[str, Any], server: str = "filesystem") -> Dict[str, Any]:
    _ensure_mcp()
    return get_mcp_manager().call_tool(tool_name, arguments, server=server)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _load_resume_index(resume_dir: str = DEFAULT_RESUME_DIR) -> None:
    global _RESUME_INDEX, _RESUME_BY_NAME
    if _RESUME_INDEX:
        return

    for resume, resume_id in load_resumes_from_files(resume_dir):
        resume["id"] = resume.get("id", resume_id)
        resume["resume_path"] = str(Path(resume_dir) / f"{resume_id}.json")
        _RESUME_INDEX[resume_id] = resume
        _RESUME_BY_NAME[_normalize_key(resume.get("name", ""))] = resume


def get_vector_db(reset_collection: bool = False) -> ResumeVectorDB:
    global _VECTOR_DB, _JOB_MATCHER
    if _VECTOR_DB is None:
        _VECTOR_DB = ResumeVectorDB(persist_dir=DEFAULT_CHROMA_DIR, reset_collection=reset_collection)
        info = _VECTOR_DB.get_collection_info()
        if info.get("total_chunks", 0) == 0:
            resumes = load_resumes_from_files(DEFAULT_RESUME_DIR)
            _VECTOR_DB.add_resumes_batch(resumes)
            _VECTOR_DB.persist()
    if _JOB_MATCHER is None:
        _JOB_MATCHER = JobMatcher(_VECTOR_DB)
    _load_resume_index()
    return _VECTOR_DB


def get_job_matcher() -> JobMatcher:
    get_vector_db()
    assert _JOB_MATCHER is not None
    return _JOB_MATCHER


def resolve_candidate(candidate_id: str) -> Optional[Dict[str, Any]]:
    _load_resume_index()
    if candidate_id in _RESUME_INDEX:
        return _RESUME_INDEX[candidate_id]

    normalized = _normalize_key(candidate_id)
    if normalized in _RESUME_BY_NAME:
        return _RESUME_BY_NAME[normalized]

    for resume_id, resume in _RESUME_INDEX.items():
        if normalized == _normalize_key(resume_id):
            return resume
        if normalized in _normalize_key(resume.get("name", "")):
            return resume
    return None


def _get_llm():
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=DEFAULT_MODEL, temperature=0.2)


def _parse_json_response(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def list_resume_files(directory: str = DEFAULT_RESUME_DIR) -> Dict[str, Any]:
    """List resume files in a directory via the filesystem MCP server."""
    return _mcp_call("list_files", {"directory": directory, "extension": ".json"})


def read_resume_file(filepath: str) -> Dict[str, Any]:
    """Read a resume file via the filesystem MCP server."""
    return _mcp_call("read_file", {"filepath": filepath})


def rag_search(
    query: str,
    top_k: int = 10,
    jd: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    RAG search tool backed by JobMatcher hybrid search.

    Args:
        query: Natural language search query or JD text
        top_k: Number of candidates to return
        jd: Optional structured job description dict
    """
    matcher = get_job_matcher()

    if jd is None:
        jd = {
            "id": "query_search",
            "title": "Candidate Search",
            "company": "N/A",
            "description": query,
            "required_skills": _extract_skills_from_text(query),
            "must_haves": _extract_must_haves_from_text(query),
            "nice_to_haves": [],
            "min_years_experience": _extract_min_years_from_text(query),
            "location": "",
        }

    result = matcher.match_candidates(jd, top_k=top_k)
    return {
        "success": True,
        "query": query,
        "top_k": top_k,
        "matches": result.get("top_matches", []),
        "job_description": result.get("job_description", ""),
    }


def _heuristic_requirements_from_text(jd: str) -> Dict[str, Any]:
    skills = _extract_skills_from_text(jd)
    min_years = _extract_min_years_from_text(jd)
    must_haves = _extract_must_haves_from_text(jd)
    return {
        "title": "Candidate Search",
        "company": "N/A",
        "required_skills": skills,
        "must_haves": must_haves,
        "nice_to_haves": [],
        "min_years_experience": min_years,
        "description": jd,
    }


def extract_requirements(jd: str) -> Dict[str, Any]:
    """
    Parse must-have vs nice-to-have requirements from a job description.

    Args:
        jd: Job description text or JSON string
    """
    structured = _coerce_jd_input(jd)
    if structured:
        matcher = get_job_matcher()
        requirements = matcher.extract_requirements(structured)
        return {"success": True, "requirements": requirements, "jd": structured}

    llm = _get_llm()
    prompt = f"""Extract structured hiring requirements from this job description.

Return ONLY valid JSON with keys:
- title
- company
- required_skills (array of strings)
- must_haves (array of strings)
- nice_to_haves (array of strings)
- min_years_experience (integer)
- description (string)

Job description:
{jd}
"""
    try:
        response = llm.invoke(prompt)
        parsed = _parse_json_response(response.content)
        requirements = {
            "title": parsed.get("title", ""),
            "company": parsed.get("company", ""),
            "required_skills": parsed.get("required_skills", []),
            "must_haves": parsed.get("must_haves", []),
            "nice_to_haves": parsed.get("nice_to_haves", []),
            "min_years_experience": parsed.get("min_years_experience", 0),
            "description": parsed.get("description", jd),
        }
    except Exception:
        requirements = _heuristic_requirements_from_text(jd)

    return {
        "success": True,
        "requirements": requirements,
        "jd": {
            "id": "parsed_jd",
            **requirements,
        },
    }


def compare_candidates(candidate_ids: List[str]) -> Dict[str, Any]:
    """
    Head-to-head comparison of candidates.

    Args:
        candidate_ids: Resume IDs or candidate names
    """
    candidates: List[Dict[str, Any]] = []
    missing: List[str] = []

    for candidate_id in candidate_ids:
        resume = resolve_candidate(candidate_id)
        if resume is None:
            missing.append(candidate_id)
            continue
        candidates.append(
            {
                "candidate_id": resume.get("id", candidate_id),
                "name": resume.get("name", candidate_id),
                "experience_years": resume.get("experience_years", 0),
                "skills": resume.get("skills", []),
                "education": resume.get("education", []),
                "location": resume.get("location", ""),
                "work_history": resume.get("work_history", []),
                "resume_path": resume.get("resume_path", ""),
            }
        )

    comparison_matrix = {
        "names": [c["name"] for c in candidates],
        "experience_years": [c["experience_years"] for c in candidates],
        "skills": [c["skills"] for c in candidates],
        "education": [c["education"] for c in candidates],
        "locations": [c["location"] for c in candidates],
    }

    return {
        "success": len(candidates) > 0,
        "candidates": candidates,
        "comparison_matrix": comparison_matrix,
        "missing": missing,
    }


def generate_interview_questions(
    candidate_id: str,
    requirements: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate screening interview questions for a candidate.

    Args:
        candidate_id: Resume ID or candidate name
        requirements: Optional structured requirements dict
    """
    resume = resolve_candidate(candidate_id)
    if resume is None:
        return {"success": False, "error": f"Candidate not found: {candidate_id}"}

    req = requirements or {}
    llm = _get_llm()
    prompt = f"""Create 5 concise screening interview questions for this candidate and role.

Candidate:
Name: {resume.get('name')}
Skills: {', '.join(resume.get('skills', []))}
Experience: {resume.get('experience_years', 0)} years
Work history: {json.dumps(resume.get('work_history', [])[:2], indent=2)}

Role requirements:
Title: {req.get('title', 'Software Engineer')}
Required skills: {', '.join(req.get('required_skills', []))}
Must-haves: {', '.join(req.get('must_haves', []))}
Nice-to-haves: {', '.join(req.get('nice_to_haves', []))}

Return numbered questions only.
"""
    try:
        response = llm.invoke(prompt)
        questions = [
            line.strip()
            for line in response.content.splitlines()
            if re.match(r"^\d+[\).\s]", line.strip())
        ]
        if not questions:
            questions = [line.strip("- ").strip() for line in response.content.splitlines() if line.strip()][:5]
    except Exception:
        questions = [
            f"Describe your hands-on experience with {', '.join(resume.get('skills', [])[:2]) or 'the core stack'}.",
            f"How have you applied these skills in production over the last {max(resume.get('experience_years', 1), 1)} years?",
            "Tell me about a challenging project and your specific contribution.",
            f"Which requirement from {req.get('title', 'this role')} would be your steepest learning curve?",
            "What questions do you have about team expectations and success metrics?",
        ]

    return {
        "success": True,
        "candidate_id": resume.get("id", candidate_id),
        "candidate_name": resume.get("name", candidate_id),
        "questions": questions[:5],
    }


def _coerce_jd_input(jd: str) -> Optional[Dict[str, Any]]:
    jd = jd.strip()
    if not jd:
        return None

    path_match = re.search(r"[\w./-]+\.json", jd)
    if path_match:
        candidate_path = Path(path_match.group(0))
        if candidate_path.exists():
            with open(candidate_path, "r", encoding="utf-8") as handle:
                return json.load(handle)

    if jd.endswith(".json") and Path(jd).exists():
        with open(jd, "r", encoding="utf-8") as handle:
            return json.load(handle)

    try:
        parsed = json.loads(jd)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    jd_path = Path(DEFAULT_JD_DIR) / jd
    if jd_path.exists():
        with open(jd_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    return None


def _extract_skills_from_text(text: str) -> List[str]:
    known_skills = [
        "Python", "Java", "JavaScript", "TypeScript", "React", "Node.js", "Go",
        "TensorFlow", "PyTorch", "AWS", "Docker", "Kubernetes", "PostgreSQL",
        "Machine Learning", "Computer Vision", "SQL", "Rust", "C++",
    ]
    lowered = text.lower()
    return [skill for skill in known_skills if skill.lower() in lowered]


def _extract_must_haves_from_text(text: str) -> List[str]:
    must_haves: List[str] = []
    years = _extract_min_years_from_text(text)
    if years:
        must_haves.append(f"{years}+ years of experience")
    for skill in _extract_skills_from_text(text):
        must_haves.append(f"Experience with {skill}")
    return must_haves


def _extract_min_years_from_text(text: str) -> int:
    match = re.search(r"(\d+)\+?\s*years?", text.lower())
    return int(match.group(1)) if match else 0


def load_default_job_description(filename: str = "job_01.json") -> Dict[str, Any]:
    """Load a bundled job description for demos and tests."""
    jds = load_job_descriptions(DEFAULT_JD_DIR)
    for jd in jds:
        if jd.get("id") == filename.replace(".json", "") or filename in str(jd.get("id", "")):
            return jd
    target = Path(DEFAULT_JD_DIR) / filename
    if target.exists():
        with open(target, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return jds[0] if jds else {}
