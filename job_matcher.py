"""
Job Matching Engine - Semantic and hybrid search for resume-to-job matching.
Handles job description processing, candidate retrieval, scoring, and output.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import numpy as np

from dotenv import load_dotenv

load_dotenv()

from resume_rag import ResumeVectorDB, EmbeddingService, load_resumes_from_files


class JobMatcher:
    """Match candidates to jobs using semantic and hybrid search."""
    
    def __init__(self, vector_db: ResumeVectorDB):
        """
        Initialize job matcher.
        
        Args:
            vector_db: ResumeVectorDB instance
        """
        self.vector_db = vector_db
        self.embedding_service = vector_db.embedding_service

    def extract_requirements(self, jd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract structured requirements from job description.
        
        Args:
            jd: Job description dict with required_skills, must_haves, etc.
        
        Returns:
            Structured requirements dict
        """
        return {
            "title": jd.get('title', ''),
            "company": jd.get('company', ''),
            "required_skills": jd.get('required_skills', []),
            "must_haves": jd.get('must_haves', []),
            "nice_to_haves": jd.get('nice_to_haves', []),
            "min_years_experience": jd.get('min_years_experience', 0),
            "description": jd.get('description', '')
        }

    def _extract_years_from_requirement(self, requirement: str) -> Optional[int]:
        """Extract years number from requirement string."""
        match = re.search(r'(\d+)\+?\s+years?', requirement.lower())
        return int(match.group(1)) if match else None

    def _keyword_score_boost(self, chunk_metadata: Dict[str, Any], requirements: Dict[str, Any]) -> Tuple[float, List[str]]:
        """
        Calculate keyword-based score boost.
        
        Args:
            chunk_metadata: Metadata from retrieved chunk
            requirements: Extracted requirements
        
        Returns:
            (boost_score, matched_items)
        """
        boost = 0.0
        matched_items = []
        
        # Extract experience requirement
        min_years = requirements.get('min_years_experience', 0)
        candidate_years = chunk_metadata.get('experience_years', 0)
        
        if candidate_years >= min_years and min_years > 0:
            boost += 10
            matched_items.append(f"{candidate_years}+ years experience (required {min_years}+)")
        
        # Skill matching
        candidate_skills = chunk_metadata.get('skills', [])
        required_skills = requirements.get('required_skills', [])
        
        matched_skills = []
        for skill in required_skills:
            if any(skill.lower() in cs.lower() or cs.lower() in skill.lower() 
                   for cs in candidate_skills):
                matched_skills.append(skill)
                boost += 5
        
        if matched_skills:
            matched_items.append(f"Skills: {', '.join(matched_skills)}")
        
        # Must-have requirements
        for must_have in requirements.get('must_haves', []):
            if 'years' in must_have.lower():
                req_years = self._extract_years_from_requirement(must_have)
                if req_years and candidate_years >= req_years:
                    boost += 10
                    matched_items.append(f"Meets: {must_have}")
        
        return boost, matched_items

    def semantic_search(self, jd: Dict[str, Any], top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Semantic search for relevant resume chunks.
        
        Args:
            jd: Job description dict
            top_k: Number of results to return
        
        Returns:
            List of matched chunks with scores
        """
        # Combine description and required skills for better search
        search_text = f"""{jd.get('title', '')}
{jd.get('description', '')}
Required Skills: {', '.join(jd.get('required_skills', []))}
        """.strip()
        
        results = self.vector_db.semantic_search(search_text, top_k=top_k)
        return results

    def hybrid_search(self, jd: Dict[str, Any], top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Hybrid search combining semantic and keyword matching.
        
        Args:
            jd: Job description dict
            top_k: Number of results to return
        
        Returns:
            List of matched candidates with hybrid scores
        """
        # Extract requirements
        requirements = self.extract_requirements(jd)
        
        # Get semantic search results (get more for re-ranking)
        semantic_results = self.semantic_search(jd, top_k=top_k * 2)
        
        # Re-rank with keyword scores
        ranked_results = []
        for result in semantic_results:
            # Semantic score (0-100)
            semantic_score = result.get('similarity_score', 0) * 100
            
            # Keyword boost
            keyword_boost, matched_items = self._keyword_score_boost(
                result.get('metadata', {}),
                requirements
            )
            
            # Hybrid score: 70% semantic + 30% keyword
            hybrid_score = semantic_score * 0.7 + keyword_boost * 0.3
            hybrid_score = min(100, hybrid_score)  # Cap at 100
            
            result['hybrid_score'] = hybrid_score
            result['semantic_score_normalized'] = semantic_score
            result['keyword_boost'] = keyword_boost
            result['matched_items'] = matched_items
            
            ranked_results.append(result)
        
        # Sort by hybrid score descending
        ranked_results.sort(key=lambda x: x['hybrid_score'], reverse=True)
        
        return ranked_results[:top_k]

    def _generate_reasoning(self, jd: Dict[str, Any], chunk: Dict[str, Any], 
                           matched_items: List[str]) -> str:
        """Generate human-readable match reasoning."""
        candidate_name = chunk.get('metadata', {}).get('source_resume', 'Unknown')
        section = chunk.get('metadata', {}).get('section_name', '')
        
        reasoning_parts = [f"Strong match in {section.lower()}"]
        
        if matched_items:
            reasoning_parts.extend(matched_items[:2])  # Top 2 matched items
        
        return ". ".join(reasoning_parts) + "."

    def match_candidates(self, jd: Dict[str, Any], top_k: int = 10) -> Dict[str, Any]:
        """
        Find best matching candidates for a job description.
        
        Args:
            jd: Job description dict
            top_k: Number of top candidates to return
        
        Returns:
            Formatted output with top matches
        """
        # Run hybrid search
        matches = self.hybrid_search(jd, top_k=top_k)
        
        # Group by resume to avoid duplicates (multiple chunks from same resume)
        resume_scores = {}
        for match in matches:
            source_resume = match.get('metadata', {}).get('source_resume', 'Unknown')
            
            if source_resume not in resume_scores:
                resume_scores[source_resume] = {
                    "hybrid_score": match.get('hybrid_score', 0),
                    "chunks": [match],
                    "matched_items": match.get('matched_items', []),
                    "metadata": match.get('metadata', {})
                }
            else:
                # Keep highest score
                if match.get('hybrid_score', 0) > resume_scores[source_resume]['hybrid_score']:
                    resume_scores[source_resume]['hybrid_score'] = match.get('hybrid_score', 0)
                resume_scores[source_resume]['chunks'].append(match)
                # Merge matched items
                resume_scores[source_resume]['matched_items'].extend(match.get('matched_items', []))
        
        # Extract top candidates
        candidates = []
        for name, info in sorted(resume_scores.items(), 
                                key=lambda x: x[1]['hybrid_score'], reverse=True)[:top_k]:
            # Extract skills uniquely from keyword-match annotations.
            matched_skills = []
            for item in info['matched_items']:
                if "Skills:" in item:
                    skill_text = item.split(": ", 1)[-1]
                    matched_skills.extend(s.strip() for s in skill_text.split(", "))
            if not matched_skills:
                matched_skills = info['metadata'].get('skills', [])[:5]
            else:
                matched_skills = list(dict.fromkeys(matched_skills))[:5]
            
            # Get relevant excerpts from best chunks
            relevant_excerpts = []
            for chunk in sorted(info['chunks'], key=lambda x: x.get('hybrid_score', 0), reverse=True)[:2]:
                text = chunk.get('document', '')
                # Take first 150 chars
                excerpt = text[:150] + "..." if len(text) > 150 else text
                relevant_excerpts.append(excerpt)
            
            # Generate reasoning
            reasoning = self._generate_reasoning(jd, info['chunks'][0], info['matched_items'][:2])
            
            candidates.append({
                "candidate_name": name,
                "resume_path": f"data/synthetic_resumes/{name.replace(' ', '_').lower()}.json",
                "match_score": int(round(info['hybrid_score'])),
                "matched_skills": matched_skills[:5],
                "relevant_excerpts": relevant_excerpts,
                "reasoning": reasoning
            })
        
        # Format output
        return {
            "job_description": f"{jd.get('company')} - {jd.get('title')}",
            "job_id": jd.get('id', 'unknown'),
            "search_timestamp": str(np.datetime64('now')),
            "top_matches": candidates
        }


def load_job_descriptions(directory: str) -> List[Dict[str, Any]]:
    """Load all job descriptions from a directory."""
    jds = []
    for file in sorted(Path(directory).glob("*.json")):
        try:
            with open(file, 'r') as f:
                jd = json.load(f)
                jds.append(jd)
        except Exception as e:
            print(f"Error loading {file}: {e}")
    return jds


def match_all_jobs(vector_db: ResumeVectorDB, jobs_directory: str, output_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Match all job descriptions against resume database.
    
    Args:
        vector_db: ResumeVectorDB instance
        jobs_directory: Directory containing job descriptions
        output_file: Optional file to save results as JSON
    
    Returns:
        List of match results
    """
    matcher = JobMatcher(vector_db)
    jds = load_job_descriptions(jobs_directory)
    
    all_results = []
    print("\n" + "=" * 80)
    print("Job Matching Results")
    print("=" * 80)
    
    for jd in jds:
        print(f"\n📋 Matching: {jd.get('company')} - {jd.get('title')}")
        print("-" * 80)
        
        result = matcher.match_candidates(jd, top_k=10)
        all_results.append(result)
        
        # Display top 3 matches
        for i, match in enumerate(result['top_matches'][:3], 1):
            print(f"\n{i}. {match['candidate_name']} - Score: {match['match_score']}")
            print(f"   Skills: {', '.join(match['matched_skills'])}")
            print(f"   Reason: {match['reasoning']}")
    
    # Save results if requested
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"\n✓ Results saved to {output_file}")
    
    print("\n" + "=" * 80)
    return all_results


if __name__ == "__main__":
    import sys
    
    print("=" * 80)
    print("Job Matching Engine Test")
    print("=" * 80)
    
    try:
        # Open existing vector DB without wiping persisted embeddings.
        print("\nInitializing vector database...")
        db = ResumeVectorDB(reset_collection=False)

        info = db.get_collection_info()
        if info.get("total_chunks", 0) == 0:
            print("Vector database is empty; indexing resumes...")
            resumes = load_resumes_from_files("data/synthetic_resumes")
            db.add_resumes_batch(resumes)
            db.persist()
        else:
            print(f"✓ Loaded vector database ({info['total_chunks']} chunks)")
        
        # Run matching
        results = match_all_jobs(
            db,
            "data/job_descriptions",
            output_file="data/matching_results.json"
        )
        
        print("\n✓ Job matching complete!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
