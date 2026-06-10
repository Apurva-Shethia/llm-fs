"""
Resume RAG System - Document chunking, embedding, and retrieval.
Handles resume processing, embedding generation, and semantic search.
"""

import json
import re
import os
import shutil
import hashlib
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
import chromadb

from dotenv import load_dotenv

load_dotenv()

# Try to import google.genai for embeddings
try:
    from google import genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False


class ResumeChunker:
    """Intelligently chunk resume documents while preserving structure."""

    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        """
        Initialize chunker.
        
        Args:
            chunk_size: Target tokens per chunk (approximate)
            overlap: Overlap in tokens between chunks
        """
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_resume(self, resume: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Chunk a resume into sections.
        
        Args:
            resume: Resume dict with name, skills, education, work_history, etc.
        
        Returns:
            List of chunks with text and metadata.
        """
        chunks = []
        
        # Chunk 1: Basic info & skills
        basic_text = f"""
Name: {resume.get('name', 'Unknown')}
Email: {resume.get('email', '')}
Location: {resume.get('location', '')}
Experience Years: {resume.get('experience_years', 0)}

Skills: {', '.join(resume.get('skills', []))}
        """.strip()
        
        chunks.append({
            "text": basic_text,
            "metadata": {
                "source_resume": resume.get('name', 'Unknown'),
                "chunk_type": "basics",
                "section_name": "Basic Info & Skills",
                "skills": resume.get('skills', []),
                "experience_years": resume.get('experience_years', 0),
                "location": resume.get('location', '')
            }
        })
        
        # Chunk 2: Education
        if resume.get('education'):
            education_text = f"""
Education:
{chr(10).join(['- ' + edu for edu in resume.get('education', [])])}
            """.strip()
            
            chunks.append({
                "text": education_text,
                "metadata": {
                    "source_resume": resume.get('name', 'Unknown'),
                    "chunk_type": "education",
                    "section_name": "Education",
                    "skills": resume.get('skills', []),
                    "experience_years": resume.get('experience_years', 0)
                }
            })
        
        # Chunk 3: Work history
        if resume.get('work_history'):
            work_text = "Work Experience:\n"
            for job in resume.get('work_history', []):
                work_text += f"\n{job['title']} at {job['company']} ({job['start_year']}-{job['end_year']})\n"
                work_text += f"{job['description']}\n"
            
            chunks.append({
                "text": work_text.strip(),
                "metadata": {
                    "source_resume": resume.get('name', 'Unknown'),
                    "chunk_type": "work_experience",
                    "section_name": "Work Experience",
                    "skills": resume.get('skills', []),
                    "experience_years": resume.get('experience_years', 0)
                }
            })
        
        return chunks


class EmbeddingService:
    """Generate embeddings using Google Gemini embedding models."""

    EMBEDDING_DIM = 3072
    
    def __init__(self, model: str = "gemini-embedding-001", api_key: Optional[str] = None):
        """
        Initialize embedding service.
        
        Args:
            model: Gemini embedding model name
            api_key: Optional API key; uses GEMINI_API_KEY env var if not provided
        """
        if not HAS_GENAI:
            raise ImportError("google-genai not installed. Run: pip install google-genai")
        
        api_key = api_key or os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.embedding_cache = {}
        self.use_local_fallback = os.getenv("USE_LOCAL_EMBEDDINGS", "").lower() in {
            "1", "true", "yes"
        }

    def _local_embedding(self, text: str) -> np.ndarray:
        """Generate a deterministic local embedding for fallback use."""
        vector = np.zeros(self.EMBEDDING_DIM, dtype=np.float32)
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.EMBEDDING_DIM
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = np.linalg.norm(vector)
        return vector if norm == 0 else vector / norm

    def _retry_delay_seconds(self, error: Exception) -> float:
        """Extract retry delay from Gemini quota/rate-limit errors."""
        match = re.search(r"retry in ([0-9.]+)s", str(error), re.IGNORECASE)
        if match:
            return max(float(match.group(1)), 1.0)
        return 45.0

    def _should_retry(self, error: Exception) -> bool:
        message = str(error)
        return "429" in message or "RESOURCE_EXHAUSTED" in message

    def embed_text(self, text: str, batch: bool = False) -> np.ndarray:
        """
        Generate embedding for text.
        
        Args:
            text: Text to embed
            batch: If True, expects list of texts
        
        Returns:
            Embedding vector (or list of vectors if batch=True)
        """
        # Check cache
        text_key = text[:100] if isinstance(text, str) else str(text)[:100]
        if text_key in self.embedding_cache:
            return self.embedding_cache[text_key]
        
        if self.use_local_fallback:
            if isinstance(text, list):
                return [self._local_embedding(item) for item in text]
            return self._local_embedding(text)

        max_retries = 3
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                if isinstance(text, list):
                    result = self.client.models.embed_content(
                        model=self.model,
                        contents=text
                    )
                    return [np.array(item.values) for item in result.embeddings]

                result = self.client.models.embed_content(
                    model=self.model,
                    contents=text
                )
                embedding = np.array(result.embeddings[0].values)
                self.embedding_cache[text_key] = embedding
                return embedding
            except Exception as e:
                last_error = e
                if self._should_retry(e) and attempt < max_retries - 1:
                    delay = self._retry_delay_seconds(e)
                    print(
                        f"Embedding rate limited; retrying in {delay:.0f}s "
                        f"(attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    continue
                break

        print(f"Error generating embedding: {last_error}")
        # Fall back to a deterministic local embedding so the pipeline can
        # continue even when the Gemini API is rate-limited.
        if isinstance(text, list):
            return [self._local_embedding(item) for item in text]
        return self._local_embedding(text)


class ResumeVectorDB:
    """Vector database for resume chunks using ChromaDB."""
    
    def __init__(self, persist_dir: str = "data/chroma_db", embedding_service: Optional[EmbeddingService] = None, reset_collection: bool = True):
        """
        Initialize Vector DB.
        
        Args:
            persist_dir: Directory to persist ChromaDB
            embedding_service: EmbeddingService instance (creates default if None)
        """
        self.persist_dir = persist_dir

        if reset_collection and os.path.exists(persist_dir):
            shutil.rmtree(persist_dir)

        os.makedirs(persist_dir, exist_ok=True)
        
        # Initialize ChromaDB client using the current persistence API.
        self.client = chromadb.PersistentClient(path=persist_dir)
        
        # Reset stale collections so persisted embeddings from older runs do not
        # conflict with the current embedding dimension.
        if reset_collection:
            try:
                self.client.delete_collection(name="resumes")
            except Exception:
                pass

        # Create collection
        self.collection = self.client.get_or_create_collection(
            name="resumes",
            metadata={"hnsw:space": "cosine"}
        )
        
        self.embedding_service = embedding_service or EmbeddingService()
        self.resume_cache = {}  # Track added resumes

    def add_resume(self, resume: Dict[str, Any], resume_id: str) -> Tuple[int, List[str]]:
        """
        Add a resume to the vector database.
        
        Args:
            resume: Resume dict
            resume_id: Unique resume identifier
        
        Returns:
            (num_chunks_added, chunk_ids)
        """
        chunker = ResumeChunker()
        chunks = chunker.chunk_resume(resume)
        
        chunk_ids = []
        chunk_texts = [chunk["text"] for chunk in chunks]
        embeddings = self.embedding_service.embed_text(chunk_texts)

        if isinstance(embeddings, np.ndarray):
            embeddings = [embeddings]

        for chunk_idx, chunk in enumerate(chunks):
            chunk_id = f"{resume_id}_chunk_{chunk_idx}"
            chunk_ids.append(chunk_id)
            
            # Use the batch embedding generated for all resume chunks.
            embedding = embeddings[chunk_idx]
            
            # Add to ChromaDB
            self.collection.add(
                ids=[chunk_id],
                embeddings=[embedding.tolist()],
                documents=[chunk["text"]],
                metadatas=[chunk["metadata"]]
            )
        
        self.resume_cache[resume_id] = {
            "name": resume.get('name', 'Unknown'),
            "chunks": len(chunks),
            "chunk_ids": chunk_ids
        }
        
        return len(chunks), chunk_ids

    def add_resumes_batch(self, resume_list: List[Tuple[Dict[str, Any], str]]) -> Dict[str, Any]:
        """
        Add multiple resumes efficiently.
        
        Args:
            resume_list: List of (resume_dict, resume_id) tuples
        
        Returns:
            Summary of added resumes
        """
        total_chunks = 0
        added_resumes = []
        
        for resume, resume_id in resume_list:
            try:
                num_chunks, chunk_ids = self.add_resume(resume, resume_id)
                total_chunks += num_chunks
                added_resumes.append({
                    "resume_id": resume_id,
                    "name": resume.get('name', 'Unknown'),
                    "chunks": num_chunks,
                    "email": resume.get('email', '')
                })
                print(f"✓ Added {resume.get('name', 'Unknown')} ({num_chunks} chunks)")
            except Exception as e:
                print(f"✗ Error adding resume {resume_id}: {e}")
        
        print(f"\n✓ Added {len(added_resumes)} resumes with {total_chunks} total chunks")
        return {
            "total_resumes": len(added_resumes),
            "total_chunks": total_chunks,
            "resumes": added_resumes
        }

    def semantic_search(self, query: str, top_k: int = 10, filters: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Semantic search for similar resume chunks.
        
        Args:
            query: Search query text
            top_k: Number of results to return
            filters: Optional metadata filters
        
        Returns:
            List of results with scores and metadata
        """
        # Generate query embedding
        query_embedding = self.embedding_service.embed_text(query)
        
        # Query ChromaDB
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding.tolist()],
                n_results=top_k,
                where=filters if filters else None,
                include=["documents", "metadatas", "distances", "embeddings"]
            )
        except Exception as e:
            print(f"Error querying ChromaDB: {e}")
            return []
        
        # Format results (convert distance to similarity score 0-1)
        formatted_results = []
        if results and results['ids'] and len(results['ids']) > 0:
            for idx, (chunk_id, doc, metadata, distance) in enumerate(zip(
                results['ids'][0],
                results['documents'][0],
                results['metadatas'][0],
                results['distances'][0]
            )):
                # Convert Euclidean distance to similarity (0-1)
                similarity_score = 1 / (1 + distance) if distance != 1 else 0.0
                
                formatted_results.append({
                    "chunk_id": chunk_id,
                    "document": doc,
                    "metadata": metadata,
                    "similarity_score": similarity_score,
                    "distance": distance
                })
        
        return formatted_results

    def persist(self):
        """Persist ChromaDB to disk.

        PersistentClient writes to disk automatically, so this is a no-op for
        compatibility with older call sites.
        """
        print(f"✓ Vector database persisted to {self.persist_dir}")

    def get_collection_info(self) -> Dict[str, Any]:
        """Get information about the collection."""
        try:
            count = self.collection.count()
            return {
                "collection_name": "resumes",
                "total_chunks": count,
                "total_resumes": len(self.resume_cache),
                "persisted": True
            }
        except Exception as e:
            return {
                "error": str(e),
                "total_chunks": 0,
                "total_resumes": len(self.resume_cache)
            }


def load_resumes_from_files(directory: str) -> List[Tuple[Dict[str, Any], str]]:
    """Load all JSON resumes from a directory."""
    resumes = []
    for file in sorted(Path(directory).glob("*.json")):
        try:
            with open(file, 'r') as f:
                resume = json.load(f)
                resume_id = resume.get('id', file.stem)
                resumes.append((resume, resume_id))
        except Exception as e:
            print(f"Error loading {file}: {e}")
    return resumes


if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("Resume RAG System - Vector Database Setup")
    print("=" * 60)
    
    # Load résumés
    print("\n[1/3] Loading resumes...")
    resumes = load_resumes_from_files("data/synthetic_resumes")
    print(f"Loaded {len(resumes)} resumes")
    
    # Initialize vector DB
    print("\n[2/3] Initializing vector database...")
    try:
        db = ResumeVectorDB()
        summary = db.add_resumes_batch(resumes)
        db.persist()
    except Exception as e:
        print(f"Error setting up vector DB: {e}")
        print("Make sure GEMINI_API_KEY is set and chromadb is installed")
        sys.exit(1)
    
    # Test semantic search
    print("\n[3/3] Testing semantic search...")
    test_query = "Python backend developer with machine learning experience"
    results = db.semantic_search(test_query, top_k=3)
    
    print(f"\nTop 3 results for query: '{test_query}'")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result['metadata'].get('source_resume', 'Unknown')} "
              f"(Score: {result['similarity_score']:.3f})")
        print(f"   Section: {result['metadata'].get('section_name', 'Unknown')}")
        print(f"   Preview: {result['document'][:100]}...")
    
    print("\n" + "=" * 60)
    print("✓ RAG system setup complete!")
    print("=" * 60)
