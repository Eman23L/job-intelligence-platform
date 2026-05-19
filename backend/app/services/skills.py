import re
from dataclasses import dataclass


SKILL_TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Languages": {
        "Python": ["python"],
        "SQL": ["sql"],
        "JavaScript": ["javascript", "js"],
        "TypeScript": ["typescript", "ts"],
    },
    "Frontend": {
        "React": ["react"],
        "Next.js": ["next.js", "nextjs", "next js"],
    },
    "Backend": {
        "FastAPI": ["fastapi"],
        "REST APIs": ["rest api", "rest apis", "restful api"],
    },
    "Databases": {
        "PostgreSQL": ["postgresql", "postgres"],
        "Supabase": ["supabase"],
    },
    "Data engineering": {
        "ETL": ["etl"],
        "ELT": ["elt"],
        "data pipelines": ["data pipeline", "data pipelines"],
        "data modelling": ["data modelling", "data modeling"],
        "data quality": ["data quality"],
        "orchestration": ["orchestration", "orchestrate"],
        "Airflow": ["airflow", "apache airflow"],
    },
    "Cloud/platform": {
        "Azure": ["azure"],
        "AWS": ["aws", "amazon web services"],
        "Docker": ["docker"],
        "Terraform": ["terraform"],
        "GitHub Actions": ["github actions"],
        "Azure DevOps": ["azure devops"],
        "CI/CD": ["ci/cd", "cicd", "continuous integration"],
    },
    "Modern data stack": {
        "Microsoft Fabric": ["microsoft fabric", "fabric"],
        "Azure Data Factory": ["azure data factory", "adf"],
        "Databricks": ["databricks"],
        "PySpark": ["pyspark"],
        "dbt": ["dbt"],
        "lakehouse": ["lakehouse"],
    },
    "AI automation": {
        "LLM workflows": ["llm workflow", "llm workflows"],
        "RAG": ["rag", "retrieval augmented generation"],
        "agents": ["agents", "agentic"],
        "evaluation": ["evaluation", "evals"],
        "guardrails": ["guardrails"],
        "prompt management": ["prompt management", "prompt engineering"],
    },
    "Automation": {
        "workflow automation": ["workflow automation"],
        "process automation": ["process automation"],
        "systems integration": ["systems integration", "system integration"],
        "internal tools": ["internal tools", "internal tooling"],
    },
    "Scraping": {
        "BeautifulSoup": ["beautifulsoup", "beautiful soup", "bs4"],
        "Requests": ["requests"],
        "Playwright": ["playwright"],
        "JSON": ["json"],
    },
}

ESSENTIAL_MARKERS = ("must have", "required", "essential", "you will need", "requirements")
NICE_MARKERS = ("nice to have", "desirable", "bonus", "preferred", "would be a plus")


@dataclass(frozen=True)
class ExtractedSkill:
    name: str
    category: str
    importance: str | None
    evidence_text: str


@dataclass(frozen=True)
class ExcludedTechnologyMention:
    name: str
    severity: str
    evidence_text: str


def extract_skills(text: str) -> list[ExtractedSkill]:
    sentences = _sentences(text)
    extracted: list[ExtractedSkill] = []
    seen: set[str] = set()
    for category, skills in SKILL_TAXONOMY.items():
        for skill_name, aliases in skills.items():
            evidence = _first_matching_sentence(sentences, aliases)
            if evidence and skill_name not in seen:
                extracted.append(
                    ExtractedSkill(
                        name=skill_name,
                        category=category,
                        importance=_classify_importance(evidence),
                        evidence_text=evidence,
                    )
                )
                seen.add(skill_name)
    return extracted


def detect_excluded_technologies(text: str, excluded_names: list[str]) -> list[ExcludedTechnologyMention]:
    sentences = _sentences(text)
    mentions: list[ExcludedTechnologyMention] = []
    for name in excluded_names:
        evidence = _first_matching_sentence(sentences, [name])
        if evidence:
            mentions.append(
                ExcludedTechnologyMention(
                    name=name,
                    severity=_classify_excluded_severity(evidence),
                    evidence_text=evidence,
                )
            )
    return mentions


def _classify_importance(sentence: str) -> str | None:
    lowered = sentence.lower()
    if any(marker in lowered for marker in ESSENTIAL_MARKERS):
        return "essential"
    if any(marker in lowered for marker in NICE_MARKERS):
        return "nice_to_have"
    return None


def _classify_excluded_severity(sentence: str) -> str:
    importance = _classify_importance(sentence)
    if importance == "essential":
        return "essential requirement"
    if importance == "nice_to_have":
        return "nice-to-have"
    return "minor mention"


def _first_matching_sentence(sentences: list[str], aliases: list[str]) -> str | None:
    for sentence in sentences:
        lowered = sentence.lower()
        for alias in aliases:
            if _contains_term(lowered, alias.lower()):
                return sentence
    return None


def _contains_term(text: str, term: str) -> bool:
    if re.search(r"[\s/+\-.]", term):
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+|(?:\s+-\s+)", cleaned)
    return [part.strip(" -") for part in parts if part.strip(" -")]
