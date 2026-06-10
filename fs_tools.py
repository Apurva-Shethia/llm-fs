from __future__ import annotations  # Enable postponed evaluation of annotations.

import os  # Read environment variables for configuration.
import re  # Use regex for case-insensitive searching.
import tempfile  # Create temporary files for atomic writes.
from datetime import datetime  # Convert timestamps to readable strings.
from pathlib import Path  # Work with file system paths safely.
from typing import Dict, List, Optional  # Provide type hints for clarity.


def _get_env_int(name: str, default: int) -> int:  # Read an integer from env with fallback.
    raw = os.getenv(name, str(default))  # Fetch env var or default string.
    try:  # Try to parse the env value.
        value = int(raw)  # Convert to integer.
    except ValueError:  # Handle non-integer values.
        return default  # Fall back to default.
    return value if value > 0 else default  # Enforce a positive integer.


BASE_DIR = Path(os.getenv("FILE_ASSISTANT_BASE_DIR", "")).expanduser()  # Load base dir.
BASE_DIR = BASE_DIR if str(BASE_DIR) else Path.cwd()  # Default to current directory.
BASE_DIR = BASE_DIR.resolve()  # Normalize to an absolute path.
MAX_FILE_BYTES = _get_env_int("FILE_ASSISTANT_MAX_BYTES", 5_000_000)  # Limit file size.
MAX_CONTENT_CHARS = _get_env_int("FILE_ASSISTANT_MAX_CHARS", 200_000)  # Limit text length.


def _resolve_safe_path(raw_path: str) -> Dict[str, object]:  # Resolve and validate a path.
    if not raw_path:  # Reject empty paths.
        return {"success": False, "error": "Path is required"}  # Return error.
    path = Path(raw_path).expanduser()  # Expand tilde and build path.
    try:  # Try to resolve symlinks and relative segments.
        resolved = path.resolve()  # Compute absolute resolved path.
    except Exception as exc:  # Handle resolution errors.
        return {"success": False, "error": f"Invalid path: {exc}"}  # Return error.
    try:  # Enforce base directory containment.
        resolved.relative_to(BASE_DIR)  # Ensure path is inside base dir.
    except ValueError:  # Raised when path is outside base dir.
        return {"success": False, "error": f"Path outside base directory: {BASE_DIR}"}  # Error.
    return {"success": True, "path": resolved}  # Return resolved safe path.


def _check_file_size(path: Path) -> Optional[str]:  # Validate file size against a limit.
    size_bytes = path.stat().st_size  # Read file size from filesystem.
    if size_bytes > MAX_FILE_BYTES:  # Compare against configured limit.
        return f"File too large: {size_bytes} bytes (max {MAX_FILE_BYTES})"  # Error.
    return None  # Size is acceptable.


def _truncate_content(content: str) -> Dict[str, object]:  # Truncate text to max length.
    original_count = len(content)  # Record original character count.
    if original_count <= MAX_CONTENT_CHARS:  # Check if truncation is needed.
        return {  # Return content unchanged.
            "content": content,  # Keep full content.
            "truncated": False,  # Indicate no truncation.
            "original_char_count": original_count,  # Provide original count.
        }  # End response.
    return {  # Return truncated content.
        "content": content[:MAX_CONTENT_CHARS],  # Slice content to limit.
        "truncated": True,  # Indicate truncation happened.
        "original_char_count": original_count,  # Provide original count.
    }  # End response.


def _atomic_write_text(path: Path, content: str) -> None:  # Write content atomically.
    temp_path: Optional[Path] = None  # Track temporary file path for cleanup.
    try:  # Ensure we clean up temp files on failure.
        with tempfile.NamedTemporaryFile(  # Create a temporary file in same directory.
            mode="w",  # Open temp file in write mode.
            encoding="utf-8",  # Write using UTF-8 encoding.
            dir=str(path.parent),  # Keep temp file on same filesystem.
            delete=False,  # Keep file so we can move it later.
        ) as handle:  # Get a handle to the temp file.
            handle.write(content)  # Write the content to the temp file.
            temp_path = Path(handle.name)  # Record the temp file path.
        os.replace(temp_path, path)  # Atomically replace the target path.
    finally:  # Cleanup any leftover temp file.
        if temp_path and temp_path.exists():  # If temp still exists, remove it.
            try:  # Attempt to delete the temp file.
                temp_path.unlink()  # Remove the temp file.
            except OSError:  # Ignore cleanup errors.
                pass  # Best effort cleanup only.


def _file_metadata(path: Path) -> Dict[str, object]:  # Build a metadata dict for a file.
    stat = path.stat()  # Fetch filesystem stats for the path.
    try:  # Try to compute a relative path for display.
        relative_path = str(path.resolve().relative_to(BASE_DIR))  # Make path relative to base.
    except Exception:  # Fall back if relative path is not possible.
        relative_path = None  # Use None when outside base.
    return {  # Return a dictionary of metadata fields.
        "name": path.name,  # Store the file name.
        "path": str(path),  # Store the full file path as a string.
        "relative_path": relative_path,  # Store path relative to base directory.
        "size_bytes": stat.st_size,  # Store the file size in bytes.
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),  # Store modified time.
        "extension": path.suffix.lower(),  # Store the lowercase file extension.
    }  # End metadata dictionary.


def _read_txt(path: Path) -> str:  # Read text files safely.
    return path.read_text(encoding="utf-8", errors="ignore")  # Read text with UTF-8.


def _read_pdf(path: Path) -> str:  # Extract text from a PDF file.
    try:  # Try to import the PDF library.
        import PyPDF2  # type: ignore - import PDF parsing library.
    except Exception as exc:  # If the import fails, capture the error.
        raise RuntimeError("PyPDF2 is required to read PDF files") from exc  # Explain the error.

    text_parts: List[str] = []  # Accumulate text from each page.
    with path.open("rb") as handle:  # Open the file in binary mode.
        reader = PyPDF2.PdfReader(handle)  # Create a PDF reader.
        for page in reader.pages:  # Iterate through each page in the PDF.
            extracted = page.extract_text() or ""  # Extract text or use empty string.
            text_parts.append(extracted)  # Add extracted page text to list.
    return "\n".join(text_parts).strip()  # Join pages into one string.


def _read_docx(path: Path) -> str:  # Extract text from a DOCX file.
    try:  # Try to import the DOCX library.
        import docx  # type: ignore - import DOCX parsing library.
    except Exception as exc:  # If the import fails, capture the error.
        raise RuntimeError("python-docx is required to read DOCX files") from exc  # Explain error.

    document = docx.Document(str(path))  # Load the DOCX document.
    paragraphs = [p.text for p in document.paragraphs if p.text]  # Collect non-empty text.
    return "\n".join(paragraphs).strip()  # Join paragraph text into one string.


def read_file(filepath: str) -> Dict[str, object]:  # Read a resume file and return data.
    resolved = _resolve_safe_path(filepath)  # Resolve and validate the file path.
    if not resolved.get("success"):  # Check for a resolution error.
        return {"success": False, "error": resolved.get("error"), "metadata": {"path": filepath}}  # Error.
    path = resolved["path"]  # Use the resolved Path object.
    if not path.exists():  # Check if the path exists.
        return {"success": False, "error": "File not found", "metadata": {"path": str(path)}}  # Error.
    if not path.is_file():  # Ensure the path is a file, not a directory.
        return {"success": False, "error": "Path is not a file", "metadata": {"path": str(path)}}  # Error.

    size_error = _check_file_size(path)  # Validate size before reading.
    if size_error:  # Stop if file is too large.
        return {"success": False, "error": size_error, "metadata": _file_metadata(path)}  # Error.

    try:  # Attempt to read based on file extension.
        ext = path.suffix.lower()  # Normalize the extension for comparison.
        if ext == ".txt":  # Handle text files.
            content = _read_txt(path)  # Read text content.
        elif ext == ".pdf":  # Handle PDF files.
            content = _read_pdf(path)  # Extract PDF text.
        elif ext == ".docx":  # Handle DOCX files.
            content = _read_docx(path)  # Extract DOCX text.
        else:  # Handle unsupported file types.
            return {  # Return a structured error response.
                "success": False,  # Indicate failure.
                "error": f"Unsupported file extension: {ext or 'none'}",  # Explain issue.
                "metadata": _file_metadata(path),  # Include file metadata.
            }  # End error response.

        truncation = _truncate_content(content)  # Apply truncation if needed.
        return {  # Return a structured success response.
            "success": True,  # Indicate success.
            "content": truncation["content"],  # Provide (possibly truncated) content.
            "metadata": _file_metadata(path),  # Include file metadata.
            "truncated": truncation["truncated"],  # Indicate truncation.
            "original_char_count": truncation["original_char_count"],  # Provide original size.
            "max_chars": MAX_CONTENT_CHARS,  # Provide the max chars limit.
        }  # End success response.
    except Exception as exc:  # Catch any unexpected errors.
        return {"success": False, "error": str(exc), "metadata": _file_metadata(path)}  # Error.


def list_files(directory: str, extension: Optional[str] = None) -> List[Dict[str, object]]:  # List files.
    resolved = _resolve_safe_path(directory)  # Resolve and validate the directory path.
    if not resolved.get("success"):  # Stop if the path is invalid or unsafe.
        raise ValueError(str(resolved.get("error")))  # Raise a clear error.
    dir_path = resolved["path"]  # Use the resolved Path object.
    if not dir_path.exists() or not dir_path.is_dir():  # Validate directory path.
        raise ValueError("Directory not found or not a directory")  # Raise a clear error.

    normalized_ext = None  # Default to no extension filter.
    if extension:  # If a filter is provided, normalize it.
        normalized_ext = extension.lower()  # Make extension lowercase.
        if not normalized_ext.startswith("."):  # Add leading dot if missing.
            normalized_ext = f".{normalized_ext}"  # Ensure consistent extension format.

    items: List[Dict[str, object]] = []  # Collect file metadata entries.
    for entry in dir_path.iterdir():  # Iterate through directory contents.
        if not entry.is_file():  # Skip non-file entries.
            continue  # Continue to next entry.
        try:  # Resolve entry to avoid symlink escapes.
            resolved_entry = entry.resolve()  # Compute absolute path for entry.
        except Exception:  # Skip entries that cannot be resolved.
            continue  # Continue to next entry.
        try:  # Enforce base directory containment.
            resolved_entry.relative_to(BASE_DIR)  # Ensure entry is inside base dir.
        except ValueError:  # Skip entries outside base dir.
            continue  # Continue to next entry.
        if normalized_ext and resolved_entry.suffix.lower() != normalized_ext:  # Enforce filter.
            continue  # Skip files that do not match extension.
        items.append(_file_metadata(resolved_entry))  # Add metadata for this file.

    return sorted(items, key=lambda item: item["name"])  # Return items sorted by name.


def write_file(  # Write content to a file.
    filepath: str,  # Target file path.
    content: str,  # Content to write.
    allow_overwrite: bool = False,  # Control whether existing files may be overwritten.
) -> Dict[str, object]:  # Return structured status.
    resolved = _resolve_safe_path(filepath)  # Resolve and validate the file path.
    if not resolved.get("success"):  # Stop if the path is invalid or unsafe.
        return {"success": False, "error": resolved.get("error"), "metadata": {"path": filepath}}  # Error.
    path = resolved["path"]  # Use the resolved Path object.
    if path.exists() and not path.is_file():  # Prevent overwriting directories.
        return {"success": False, "error": "Path exists and is not a file", "metadata": {"path": str(path)}}  # Error.
    if path.exists() and not allow_overwrite:  # Block overwrites by default.
        return {"success": False, "error": "File already exists", "metadata": {"path": str(path)}}  # Error.
    try:  # Try writing the file.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create parent dirs if needed.
        _atomic_write_text(path, content)  # Write content atomically.
        return {"success": True, "metadata": _file_metadata(path)}  # Success response.
    except Exception as exc:  # Catch any write errors.
        return {"success": False, "error": str(exc), "metadata": {"path": str(path)}}  # Error.


def search_in_file(filepath: str, keyword: str) -> Dict[str, object]:  # Search file content.
    if not keyword:  # Validate keyword input.
        return {"success": False, "error": "Keyword must be provided", "matches": []}  # Error.

    resolved = _resolve_safe_path(filepath)  # Resolve and validate the file path.
    if not resolved.get("success"):  # Stop if the path is invalid or unsafe.
        return {"success": False, "error": resolved.get("error"), "matches": []}  # Error.
    path = resolved["path"]  # Use the resolved Path object.
    if not path.exists():  # Check if the path exists.
        return {"success": False, "error": "File not found", "matches": []}  # Error.
    if not path.is_file():  # Ensure the path is a file.
        return {"success": False, "error": "Path is not a file", "matches": []}  # Error.

    size_error = _check_file_size(path)  # Validate size before reading.
    if size_error:  # Stop if file is too large.
        return {"success": False, "error": size_error, "matches": []}  # Error.

    try:  # Attempt to read based on file extension.
        ext = path.suffix.lower()  # Normalize the extension for comparison.
        if ext == ".txt":  # Handle text files.
            content = _read_txt(path)  # Read text content.
        elif ext == ".pdf":  # Handle PDF files.
            content = _read_pdf(path)  # Extract PDF text.
        elif ext == ".docx":  # Handle DOCX files.
            content = _read_docx(path)  # Extract DOCX text.
        else:  # Handle unsupported file types.
            return {"success": False, "error": f"Unsupported file extension: {ext or 'none'}", "matches": []}  # Error.
    except Exception as exc:  # Catch any unexpected errors.
        return {"success": False, "error": str(exc), "matches": []}  # Error.

    matches: List[Dict[str, object]] = []  # Store match data.
    context = 40  # Number of characters of context around the match.
    for match in re.finditer(re.escape(keyword), content, flags=re.IGNORECASE):  # Find matches.
        start = max(match.start() - context, 0)  # Compute snippet start index.
        end = min(match.end() + context, len(content))  # Compute snippet end index.
        snippet = content[start:end]  # Extract surrounding text snippet.
        matches.append(  # Add match info to list.
            {  # Begin match dictionary.
                "start": match.start(),  # Store match start position.
                "end": match.end(),  # Store match end position.
                "snippet": snippet,  # Store the context snippet.
            }  # End match dictionary.
        )  # End append call.

    return {  # Return final search results.
        "success": True,  # Indicate success.
        "keyword": keyword,  # Echo the keyword searched for.
        "match_count": len(matches),  # Provide count of matches.
        "matches": matches,  # Provide list of match details.
        "metadata": _file_metadata(path),  # Include file metadata.
        "searched_char_count": len(content),  # Report how much text was searched.
    }  # End response dictionary.
