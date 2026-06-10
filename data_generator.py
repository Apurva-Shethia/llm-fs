"""
Synthetic data generator for RAG system.
Generates diverse synthetic resumes and job descriptions for testing.
"""

import json
import random
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timedelta


class SyntheticDataGenerator:
    """Generate diverse synthetic resumes and job descriptions."""

    # Skill pools by category
    SKILL_POOLS = {
        "backend": ["Python", "Java", "Go", "Node.js", "Django", "FastAPI", "Spring Boot", "PostgreSQL", "MySQL"],
        "frontend": ["React", "Vue.js", "Angular", "TypeScript", "CSS", "HTML5", "Next.js", "Svelte"],
        "devops": ["Docker", "Kubernetes", "AWS", "GCP", "Azure", "Terraform", "Jenkins", "GitLab CI"],
        "ml": ["Python", "TensorFlow", "PyTorch", "Scikit-learn", "Machine Learning", "Deep Learning", 
               "NLP", "Computer Vision", "SQL", "Pandas"],
        "data": ["SQL", "Python", "Spark", "Hadoop", "Tableau", "PowerBI", "R", "Data Analysis", "ETL"],
        "devops_extra": ["Linux", "Bash", "Docker", "Ansible", "Monitoring", "CloudFormation"]
    }

    JOB_ROLES = [
        {"title": "Senior Backend Engineer", "skills": "backend", "years": 5},
        {"title": "Full-Stack Developer", "skills": ["backend", "frontend"], "years": 3},
        {"title": "Machine Learning Engineer", "skills": "ml", "years": 4},
        {"title": "Data Engineer", "skills": "data", "years": 3},
        {"title": "DevOps Engineer", "skills": "devops", "years": 4},
        {"title": "Frontend Engineer", "skills": "frontend", "years": 2},
        {"title": "Data Scientist", "skills": "ml", "years": 3},
        {"title": "Platform Engineer", "skills": ["devops", "backend"], "years": 5},
        {"title": "Software Engineer II", "skills": "backend", "years": 4},
        {"title": "Lead Software Engineer", "skills": ["backend", "devops"], "years": 7},
    ]

    EDUCATION = [
        "BS Computer Science",
        "BS Engineering",
        "MS Computer Science",
        "MS Data Science",
        "BS Mathematics",
        "PhD Computer Science",
        "BS Physics",
        "MS Electrical Engineering"
    ]

    COMPANIES = [
        "Google", "Meta", "Amazon", "Microsoft", "Apple", "Netflix", "Uber", "Airbnb",
        "Twitter", "Stripe", "Notion", "Figma", "Canva", "DuckDuckGo"
    ]

    FIRST_NAMES = [
        "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry",
        "Iris", "Jack", "Kate", "Liam", "Maya", "Noah", "Olivia", "Patrick",
        "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
        "Yara", "Zoe"
    ]

    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Chen", "Wang", "Kim", "Lee", "Patel",
        "Kumar", "Singh", "O'Brien", "O'Connor", "Mueller"
    ]

    CITIES = [
        "San Francisco", "New York", "Seattle", "Austin", "Boston", "Denver",
        "Chicago", "Los Angeles", "Toronto", "London", "Berlin", "Singapore"
    ]

    @staticmethod
    def generate_resume(resume_id: int) -> Dict[str, Any]:
        """Generate a single synthetic resume."""
        # Pick a random job role
        job = random.choice(SyntheticDataGenerator.JOB_ROLES)
        
        # Name
        first_name = random.choice(SyntheticDataGenerator.FIRST_NAMES)
        last_name = random.choice(SyntheticDataGenerator.LAST_NAMES)
        name = f"{first_name} {last_name}"
        
        # Email
        email = f"{first_name.lower()}.{last_name.lower()}@email.com"
        
        # Experience years: 1-15
        experience_years = random.randint(1, 15)
        
        # Skills: draw from relevant skill pool(s)
        skill_categories = job["skills"] if isinstance(job["skills"], list) else [job["skills"]]
        skills = []
        for cat in skill_categories:
            skills.extend(random.sample(SyntheticDataGenerator.SKILL_POOLS[cat], k=random.randint(2, 4)))
        skills = list(set(skills))[:8]  # Unique, max 8 skills
        
        # Education
        education = [random.choice(SyntheticDataGenerator.EDUCATION)]
        if random.random() > 0.7:  # 30% have multiple degrees
            education.append(random.choice(SyntheticDataGenerator.EDUCATION))
        
        # Location
        location = random.choice(SyntheticDataGenerator.CITIES)
        
        # Work experience entries
        work_history = []
        years_placed = 0
        num_jobs = random.randint(2, 4)
        
        for i in range(num_jobs):
            remaining_years = experience_years - years_placed
            if remaining_years <= 0:
                break
            
            max_role_years = min(4, remaining_years)
            role_years = random.randint(1, max(1, max_role_years))
            years_placed += role_years
            
            end_date = datetime.now() - timedelta(days=365 * years_placed)
            start_date = end_date - timedelta(days=365 * role_years)
            
            desc_parts = []
            if random.random() > 0.5:
                desc_parts.append("Developed, maintained, and improved production systems.")
            desc_parts.append("Collaborated with cross-functional teams.")
            desc_parts.append(random.choice(["Led projects.", "Mentored junior engineers.", "Optimized performance."]))
            
            work_history.append({
                "title": random.choice([job["title"]] + [r["title"] for r in SyntheticDataGenerator.JOB_ROLES[:3]]),
                "company": random.choice(SyntheticDataGenerator.COMPANIES),
                "start_year": start_date.year,
                "end_year": end_date.year,
                "description": " ".join(desc_parts)
            })
            years_placed += role_years
        
        return {
            "id": f"resume_{resume_id:03d}",
            "name": name,
            "email": email,
            "location": location,
            "experience_years": experience_years,
            "skills": skills,
            "education": education,
            "work_history": work_history
        }

    @staticmethod
    def generate_job_description(jd_id: int) -> Dict[str, Any]:
        """Generate a single synthetic job description."""
        # Pick a random job role
        job = random.choice(SyntheticDataGenerator.JOB_ROLES)
        
        # Title and company
        title = job["title"]
        company = random.choice(SyntheticDataGenerator.COMPANIES)
        
        # Required skills
        skill_categories = job["skills"] if isinstance(job["skills"], list) else [job["skills"]]
        required_skills = []
        for cat in skill_categories:
            required_skills.extend(random.sample(SyntheticDataGenerator.SKILL_POOLS[cat], k=random.randint(2, 3)))
        required_skills = list(set(required_skills))
        
        # Must-haves: experience requirement
        min_years = random.randint(2, 8)
        must_haves = [f"{min_years}+ years of software development experience"]
        
        if random.random() > 0.5:
            must_haves.append(f"Experience with {random.choice(required_skills)}")
        
        if random.random() > 0.4:
            must_haves.append("Strong problem-solving skills")
        
        # Nice-to-haves
        nice_to_haves = [
            f"Experience with {random.choice(required_skills)}",
            "Experience in agile environments",
            "Open source contribution experience",
            "Mentoring junior engineers",
            "Performance optimization experience"
        ]
        nice_to_haves = random.sample(nice_to_haves, k=random.randint(1, 3))
        
        # Description
        description = f"""
We are looking for a talented {title} to join our team at {company}. 
In this role, you will work on {random.choice(['scalable backend systems', 'cutting-edge frontend applications', 'ML infrastructure', 'data platforms', 'DevOps systems'])}.
You will collaborate with experienced engineers and have the opportunity to grow your skills.
        """.strip()
        
        return {
            "id": f"jd_{jd_id:02d}",
            "title": title,
            "company": company,
            "description": description,
            "required_skills": required_skills,
            "must_haves": must_haves,
            "nice_to_haves": nice_to_haves,
            "min_years_experience": min_years,
            "location": random.choice(SyntheticDataGenerator.CITIES)
        }

    @staticmethod
    def save_resumes(output_dir: str, count: int = 30) -> List[str]:
        """Generate and save synthetic resumes."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        saved_files = []
        for i in range(1, count + 1):
            resume = SyntheticDataGenerator.generate_resume(i)
            file_path = output_path / f"resume_{i:03d}.json"
            
            with open(file_path, 'w') as f:
                json.dump(resume, f, indent=2)
            
            saved_files.append(str(file_path))
            print(f"Generated {resume['name']} → {file_path.name}")
        
        print(f"\n✓ Generated {count} synthetic resumes in {output_path}")
        return saved_files

    @staticmethod
    def save_job_descriptions(output_dir: str, count: int = 5) -> List[str]:
        """Generate and save synthetic job descriptions."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        saved_files = []
        for i in range(1, count + 1):
            jd = SyntheticDataGenerator.generate_job_description(i)
            file_path = output_path / f"job_{i:02d}.json"
            
            with open(file_path, 'w') as f:
                json.dump(jd, f, indent=2)
            
            saved_files.append(str(file_path))
            print(f"Generated {jd['company']} - {jd['title']} → {file_path.name}")
        
        print(f"\n✓ Generated {count} synthetic job descriptions in {output_path}")
        return saved_files


if __name__ == "__main__":
    # Generate 30 synthetic resumes
    resume_files = SyntheticDataGenerator.save_resumes("data/synthetic_resumes", count=30)
    
    # Generate 5 synthetic job descriptions
    jd_files = SyntheticDataGenerator.save_job_descriptions("data/job_descriptions", count=5)
