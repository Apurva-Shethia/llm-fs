# LLM Resume Assistant & RAG Matching System

Python project for resume file operations via LLM tool-calling, plus a semantic search pipeline that matches candidates to job descriptions using Gemini embeddings and ChromaDB.

## Features

### LLM File Assistant
- Read resume files (TXT, PDF, DOCX) with metadata
- List and search files within a configurable base directory
- Write files with atomic writes and overwrite protection
- Multi-step LLM tool-calling for natural language tasks

### RAG Resume Matching
- Section-aware resume chunking (basics, education, work experience)
- Gemini embedding generation with retry on rate limits
- ChromaDB vector storage at `data/chroma_db/`
- Hybrid matching: 70% semantic similarity + 30% keyword/requirement boost
- JSON output with match scores, matched skills, excerpts, and reasoning

## Project Structure

```
llm-fs/
├── fs_tools.py              # File system tools (read, list, search, write)
├── llm_file_assistant.py    # LLM tool-calling assistant and CLI
├── resume_rag.py            # Chunking, embeddings, ChromaDB vector store
├── job_matcher.py           # Semantic and hybrid resume-to-job matching
├── data_generator.py        # Synthetic resume and job description data
├── example_usage.py         # End-to-end usage demo
├── analysis.ipynb           # Metrics, latency analysis, visualizations
├── start.sh                 # One-command setup and pipeline runner
├── requirements.txt
├── .env.example
└── data/
    ├── synthetic_resumes/   # 30 JSON resumes
    ├── job_descriptions/    # 5 JSON job descriptions
    └── chroma_db/           # Vector store (auto-created, gitignored)
```

## Setup

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables:

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini API key (required for embeddings and LLM assistant) |
| `GEMINI_MODEL` | Gemini model name. Default: `gemini-2.5-flash` |
| `USE_LOCAL_EMBEDDINGS` | Set to `1` for offline local embeddings (testing only) |
| `FILE_ASSISTANT_BASE_DIR` | Base directory for file operations. Default: current directory |
| `FILE_ASSISTANT_MAX_BYTES` | Max file size for reads. Default: `5000000` |
| `FILE_ASSISTANT_MAX_CHARS` | Max characters returned by `read_file`. Default: `200000` |
| `LLM_TOOL_MAX_CALLS` | Max tool-call iterations per query. Default: `6` |

The CLI and RAG modules load `.env` automatically on startup.

## Usage

### LLM File Assistant

```bash
venv/bin/python llm_file_assistant.py "Read all resumes in the samples folder"
venv/bin/python llm_file_assistant.py "Find resumes mentioning cashier"
venv/bin/python llm_file_assistant.py "Create a summary file for samples/resume_009.docx"
```

All file paths are restricted to `FILE_ASSISTANT_BASE_DIR`. Set this to a dedicated folder for production use.

### RAG Matching Pipeline

Run the full pipeline (setup, index resumes, match jobs):

```bash
./start.sh
```

Or run each step manually:

```bash
venv/bin/python resume_rag.py      # Index resumes into ChromaDB
venv/bin/python job_matcher.py     # Match job descriptions; saves data/matching_results.json
venv/bin/python example_usage.py   # End-to-end demo
```

Generate fresh synthetic data:

```bash
venv/bin/python data_generator.py
```

### Analysis

```bash
jupyter notebook analysis.ipynb
```

## Architecture

```
Input Layer
├─ Resumes (PDF/DOCX/JSON) → fs_tools.read_file()
└─ Job Descriptions (JSON)

Processing Layer (resume_rag.py)
├─ ResumeChunker: Section-aware chunking
├─ EmbeddingService: Gemini embeddings
└─ ResumeVectorDB: ChromaDB persistent storage

Matching Layer (job_matcher.py)
├─ Semantic search via embeddings
├─ Hybrid search: 70% semantic + 30% keyword
├─ Scoring on 0–100 scale with must-have filtering
└─ Top-K candidate ranking

Output Layer
└─ JSON with match scores, skills, excerpts, reasoning
```

## Output Format

```json
{
  "job_description": "Company - Job Title",
  "job_id": "jd_01",
  "search_timestamp": "2026-06-10T...",
  "top_matches": [
    {
      "candidate_name": "John Doe",
      "resume_path": "data/synthetic_resumes/resume_001.json",
      "match_score": 92,
      "matched_skills": ["Python", "Machine Learning", "TensorFlow"],
      "relevant_excerpts": ["..."],
      "reasoning": "Strong match in work experience. Meets all must-have requirements."
    }
  ]
}
```

## Dataset

- **30 synthetic resumes** — varied roles, skills, experience (1–15 years), and education
- **5 job descriptions** — ML Engineer, Software Engineer II, Platform Engineer, Data Engineer roles with required skills and experience thresholds

## Configuration

- **Chunking**: Edit `ResumeChunker` in `resume_rag.py`
- **Embedding model**: `EmbeddingService(model="text-embedding-004")` in `resume_rag.py`
- **Hybrid weights**: Adjust semantic/keyword ratio in `hybrid_search()` in `job_matcher.py`
- **Top-K results**: `matcher.match_candidates(jd, top_k=10)` in `job_matcher.py`

## Troubleshooting

**Missing API key**

```bash
export GEMINI_API_KEY='your-key'
```

**ChromaDB issues** — delete and rebuild:

```bash
rm -rf data/chroma_db/
venv/bin/python resume_rag.py
```

**Slow embedding generation** — use `USE_LOCAL_EMBEDDINGS=1` for offline testing, or reduce batch size in `resume_rag.py`.

## Dependencies

Core packages: `google-genai`, `chromadb`, `numpy`, `PyPDF2`, `python-docx`, `python-dotenv`. See `requirements.txt` for versions.
