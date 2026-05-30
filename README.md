# LLM File Assistant

A small Python project that lets an LLM read, search, and write resume files using safe, structured tools. It supports TXT, PDF, and DOCX resumes with configurable path and size limits.

## Features
- Read resume files (TXT, PDF, DOCX) with metadata
- List files in a directory with optional extension filter
- Search files for keywords with context snippets
- Write files safely with atomic writes and overwrite protection
- LLM tool-calling loop for multi-step tasks

## Project Structure
- fs_tools.py: File system tools (read, list, search, write)
- llm_file_assistant.py: LLM tool-calling assistant and CLI
- requirements.txt: Runtime dependencies
- .env.example: Example environment variable file

## Setup
1. Create and activate a virtual environment (optional but recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables
The assistant reads configuration from environment variables:

- FILE_ASSISTANT_BASE_DIR: Base directory allowed for all file operations. Default: current directory.
- FILE_ASSISTANT_MAX_BYTES: Maximum file size allowed for reads. Default: 5000000.
- FILE_ASSISTANT_MAX_CHARS: Maximum characters returned by read_file. Default: 200000.
- LLM_TOOL_MAX_CALLS: Max tool-call iterations per query. Default: 6.
- OPENAI_API_KEY: API key for OpenAI.
- OPENAI_MODEL: OpenAI model name. Default: gpt-4o-mini.
- LLM_PROVIDER: Provider name. Currently only "openai" is supported.

Example:

```bash
export FILE_ASSISTANT_BASE_DIR=/path/to/project
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-4o-mini
```

Using the example env file (recommended if you have many flags):

```bash
cp .env.example .env
```

The CLI loads .env automatically on startup (via python-dotenv).

## Usage
Run the CLI with a natural language query:

```bash
python llm_file_assistant.py "Read all resumes in the resumes folder"
```

Other examples:

```bash
python llm_file_assistant.py "Find resumes mentioning Python experience"
python llm_file_assistant.py "Create a summary file for resume_john_doe.pdf"
```

## Tool Behavior Notes
- All file paths are restricted to FILE_ASSISTANT_BASE_DIR.
- list_files raises an error if the directory is invalid or outside the base directory.
- read_file truncates output if it exceeds FILE_ASSISTANT_MAX_CHARS.
- search_in_file reads full content (subject to FILE_ASSISTANT_MAX_BYTES).
- write_file will not overwrite existing files unless allow_overwrite is set to true.

## Optional Dependencies
PDF and DOCX parsing require these packages:
- PyPDF2
- python-docx

These are already listed in requirements.txt.

## Safety and Best Practices
- Set FILE_ASSISTANT_BASE_DIR to a dedicated folder (for example, a resumes directory).
- Keep file size limits reasonable to avoid large payloads.
- Avoid enabling overwrite unless you explicitly need it.
