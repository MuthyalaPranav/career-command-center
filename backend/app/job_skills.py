import re
from collections.abc import Iterator

# Canonical display names and aliases, with longer terms taking precedence.
SKILL_ALIASES = {
    "Python": (), "Java": (), "JavaScript": ("JS",), "TypeScript": ("TS",),
    "C#": (), "C++": (), ".NET": ("dotnet",), "Golang": (), "Rust": (), "Ruby": (),
    "PHP": (), "Kotlin": (), "Swift": (), "Bash": (), "PowerShell": (),
    "HTML": ("HTML5",), "CSS": ("CSS3",), "Tailwind CSS": ("Tailwind",),
    "React": ("React.js", "ReactJS", "React JS"), "React Native": (),
    "Vue.js": ("Vue", "VueJS"), "Angular": (), "Next.js": ("NextJS",),
    "Node.js": ("NodeJS", "Node JS"), "Express.js": ("ExpressJS",),
    "Django": (), "Flask": (), "FastAPI": (), "Spring Boot": (),
    "REST API": ("REST APIs", "RESTful API", "RESTful APIs", "RESTful services"),
    "GraphQL": (), "gRPC": (), "OAuth": ("OAuth2", "OAuth 2.0"),
    "SQL": (), "PostgreSQL": ("Postgres",), "MySQL": (), "SQLite": (),
    "SQL Server": ("Microsoft SQL Server", "MSSQL"), "NoSQL": (), "MongoDB": (), "Redis": (),
    "Elasticsearch": (), "Snowflake": (), "BigQuery": (), "Apache Spark": ("Spark",),
    "Apache Kafka": ("Kafka",), "Apache Airflow": ("Airflow",), "ETL": (),
    "Data Modeling": ("data modelling",), "Data Warehousing": (),
    "AWS": ("Amazon Web Services",), "AWS Lambda": (), "Amazon S3": ("S3",),
    "Azure": ("Microsoft Azure",), "Google Cloud": ("GCP", "Google Cloud Platform"),
    "Docker": (), "Kubernetes": ("K8s",), "Terraform": (), "Ansible": (),
    "Linux": (), "Git": (), "GitHub": (), "GitHub Actions": (), "GitLab CI": (),
    "Jenkins": (), "CI/CD": ("CI / CD", "continuous integration and delivery"),
    "DevOps": (), "Agile": (), "Scrum": (), "Microservices": (),
    "Unit Testing": ("unit tests",), "Integration Testing": ("integration tests",),
    "Playwright": (), "Selenium": (), "pytest": (),
    "Pandas": (), "NumPy": (), "scikit-learn": (), "PyTorch": (), "TensorFlow": (),
    "Machine Learning": (), "Deep Learning": (), "Natural Language Processing": ("NLP",),
    "Generative AI": ("GenAI", "Gen AI", "generative artificial intelligence"),
    "Large Language Models": ("large language model", "LLM", "LLMs"), "Prompt Engineering": (),
    "Figma": (), "Tableau": (), "Power BI": ("PowerBI",), "Microsoft Excel": ("Excel",),
}

CANONICAL_SKILLS = {
    term.casefold(): canonical
    for canonical, aliases in SKILL_ALIASES.items()
    for term in (canonical, *aliases)
}
SKILL_PATTERN = re.compile(
    r"(?<![\w.+#])(?:"
    + "|".join(map(re.escape, sorted(CANONICAL_SKILLS, key=len, reverse=True)))
    + r")(?![\w+#]|\.\w)",
    re.IGNORECASE,
)


def find_skills(text: str) -> Iterator[tuple[str, re.Match[str]]]:
    for match in SKILL_PATTERN.finditer(text):
        canonical = CANONICAL_SKILLS.get(match[0].casefold())
        if canonical is not None:
            yield canonical, match
