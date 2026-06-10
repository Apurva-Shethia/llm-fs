"""
Example usage of the RAG Resume Matching System

This script demonstrates how to:
1. Initialize the RAG system from scratch
2. Add resumes to the vector database
3. Match candidates to a job description
4. Analyze results
"""

import json
from pathlib import Path
from resume_rag import ResumeVectorDB, load_resumes_from_files
from job_matcher import JobMatcher, load_job_descriptions


def main():
    """Main example function."""
    
    print("\n" + "="*80)
    print("RAG Resume Matching System - Usage Example")
    print("="*80)
    
    # Step 1: Initialize Vector Database
    print("\n[Step 1] Initializing vector database...")
    print("-"*80)
    
    try:
        vector_db = ResumeVectorDB(persist_dir="data/chroma_db")
        print("✓ Vector database initialized")
    except Exception as e:
        print(f"✗ Error initializing vector DB: {e}")
        print("  Make sure GEMINI_API_KEY is set: export GEMINI_API_KEY='your-key'")
        return
    
    # Step 2: Load and Index Resumes
    print("\n[Step 2] Loading and indexing resumes...")
    print("-"*80)
    
    try:
        resumes = load_resumes_from_files("data/synthetic_resumes")
        print(f"  Loaded {len(resumes)} resumes")
        
        summary = vector_db.add_resumes_batch(resumes)
        print(f"\n✓ Indexed {summary['total_resumes']} resumes")
        print(f"  Total chunks: {summary['total_chunks']}")
        
        # Get collection info
        info = vector_db.get_collection_info()
        print(f"  Collection size: {info['total_chunks']} chunks")
        
    except Exception as e:
        print(f"✗ Error loading resumes: {e}")
        return
    
    # Step 3: Persist Vector Database
    print("\n[Step 3] Persisting vector database...")
    print("-"*80)
    
    try:
        vector_db.persist()
        print("✓ Vector database persisted to disk")
    except Exception as e:
        print(f"Note: {e}")
    
    # Step 4: Load Job Descriptions
    print("\n[Step 4] Loading job descriptions...")
    print("-"*80)
    
    try:
        jds = load_job_descriptions("data/job_descriptions")
        print(f"✓ Loaded {len(jds)} job descriptions")
        
        for jd in jds:
            print(f"  • {jd.get('company')} - {jd.get('title')}")
            
    except Exception as e:
        print(f"✗ Error loading job descriptions: {e}")
        return
    
    # Step 5: Match First Job
    print("\n[Step 5] Running matching for first job...")
    print("-"*80)
    
    if jds:
        try:
            matcher = JobMatcher(vector_db)
            first_jd = jds[0]
            
            print(f"\nMatching: {first_jd.get('company')} - {first_jd.get('title')}")
            print(f"Required Skills: {', '.join(first_jd.get('required_skills', []))}")
            print(f"Minimum Experience: {first_jd.get('min_years_experience')}+ years")
            
            # Run matching
            result = matcher.match_candidates(first_jd, top_k=10)
            
            # Display top matches
            print("\n✓ Top 5 Matches:")
            print("-"*80)
            
            for i, match in enumerate(result['top_matches'][:5], 1):
                print(f"\n{i}. {match['candidate_name']}")
                print(f"   Score: {match['match_score']}/100")
                print(f"   Skills: {', '.join(match['matched_skills'][:3])}")
                print(f"   Reason: {match['reasoning']}")
            
            # Save full results
            output_file = "example_matching_results.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2)
            
            print(f"\n✓ Full results saved to {output_file}")
            
        except Exception as e:
            print(f"✗ Error running matching: {e}")
            import traceback
            traceback.print_exc()
            return
    
    # Step 6: Run All Matching
    print("\n[Step 6] Running matching for all jobs...")
    print("-"*80)
    
    try:
        matcher = JobMatcher(vector_db)
        all_results = []
        
        for idx, jd in enumerate(jds, 1):
            result = matcher.match_candidates(jd, top_k=10)
            all_results.append(result)
            
            top_score = result['top_matches'][0]['match_score'] if result['top_matches'] else 0
            print(f"  {idx}. {jd.get('company')} - {jd.get('title')}: "
                  f"Top score {top_score}/100")
        
        # Save all results
        output_file = "all_matching_results.json"
        with open(output_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        print(f"\n✓ All results saved to {output_file}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return
    
    # Step 7: Show Examples
    print("\n[Step 7] Additional Examples...")
    print("-"*80)
    
    # Custom job matching
    custom_jd = {
        "title": "Python Developer",
        "company": "Custom Company",
        "description": "Looking for experienced Python developer with AWS and Docker",
        "required_skills": ["Python", "AWS", "Docker"],
        "must_haves": ["3+ years Python experience"],
        "nice_to_haves": ["FastAPI", "Kubernetes"],
        "min_years_experience": 3,
        "id": "custom_jd"
    }
    
    print("\nCustom Job Matching Example:")
    print(f"  Job: {custom_jd['company']} - {custom_jd['title']}")
    
    try:
        result = matcher.match_candidates(custom_jd, top_k=5)
        
        # Show top 3
        for i, match in enumerate(result['top_matches'][:3], 1):
            print(f"\n  {i}. {match['candidate_name']} (Score: {match['match_score']})")
            
    except Exception as e:
        print(f"  Note: {e}")
    
    print("\n" + "="*80)
    print("✓ Example Complete!")
    print("="*80)
    print("\nNext steps:")
    print("• Run analysis.ipynb for comprehensive metrics")
    print("• See README.md for full documentation")
    print("• Modify parameters in resume_rag.py and job_matcher.py as needed")
    print()


if __name__ == "__main__":
    main()
