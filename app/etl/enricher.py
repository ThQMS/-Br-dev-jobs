import re
import subprocess
import sys
from functools import lru_cache

import spacy
import structlog
from spacy.language import Language
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Job
from app.models.schemas import JobCreate

logger = structlog.get_logger(__name__)

_SPACY_MODEL = "pt_core_news_sm"

# ── Technology catalogue ──────────────────────────────────────────────────────
# (canonical display name, regex pattern matched case-insensitively)

_RAW_PATTERNS: list[tuple[str, str]] = [
    # Languages
    ("Python", r"\bpython\b"),
    ("JavaScript", r"\bjavascript\b"),
    ("TypeScript", r"\btypescript\b"),
    ("Java", r"\bjava\b"),
    ("Go", r"\bgolang\b|\bgo\b"),
    ("Rust", r"\brust\b"),
    ("Kotlin", r"\bkotlin\b"),
    ("Swift", r"\bswift\b"),
    ("C#", r"\bc#\b|\bcsharp\b"),
    ("C++", r"\bc\+\+\b|\bcpp\b"),
    ("PHP", r"\bphp\b"),
    ("Ruby", r"\bruby\b"),
    ("Scala", r"\bscala\b"),
    ("Dart", r"\bdart\b"),
    ("Elixir", r"\belixir\b"),
    ("Clojure", r"\bclojure\b"),
    ("R", r"\blinguagem\s+r\b|\br\s+lang\b"),
    # Frontend
    ("React", r"\breact(?:\.js)?\b|\breactjs\b"),
    ("Vue.js", r"\bvue(?:\.js)?\b|\bvuejs\b"),
    ("Angular", r"\bangular(?:js)?\b"),
    ("Next.js", r"\bnext(?:\.js)?\b|\bnextjs\b"),
    ("Nuxt.js", r"\bnuxt(?:\.js)?\b|\bnuxtjs\b"),
    ("Svelte", r"\bsvelte(?:kit)?\b"),
    ("Remix", r"\bremix\b"),
    # Backend frameworks
    ("Node.js", r"\bnode(?:\.js)?\b|\bnodejs\b"),
    ("Express", r"\bexpress(?:\.js)?\b"),
    ("NestJS", r"\bnest(?:\.js|js)?\b"),
    ("FastAPI", r"\bfastapi\b"),
    ("Django", r"\bdjango\b"),
    ("Flask", r"\bflask\b"),
    ("Spring Boot", r"\bspring\s+boot\b|\bspring\b"),
    ("Laravel", r"\blaravel\b"),
    ("Rails", r"\bruby\s+on\s+rails\b|\brails\b"),
    ("ASP.NET", r"\basp\.net\b"),
    ("Gin", r"\bgin\s+framework\b|\bgin\b"),
    ("FastHTTP", r"\bfasthttp\b"),
    # Databases
    ("PostgreSQL", r"\bpostgresql\b|\bpostgres\b|\bpgsql\b"),
    ("MySQL", r"\bmysql\b"),
    ("SQLite", r"\bsqlite\b"),
    ("MongoDB", r"\bmongodb\b|\bmongo\b"),
    ("Redis", r"\bredis\b"),
    ("Elasticsearch", r"\belasticsearch\b|\belastic\b"),
    ("Cassandra", r"\bcassandra\b"),
    ("DynamoDB", r"\bdynamodb\b"),
    ("SQL Server", r"\bsql\s+server\b|\bmssql\b"),
    ("Oracle DB", r"\boracle\s+(?:db|database)\b"),
    ("ClickHouse", r"\bclickhouse\b"),
    ("BigQuery", r"\bbigquery\b"),
    ("Snowflake", r"\bsnowflake\b"),
    # Containers / Infra
    ("Docker", r"\bdocker\b"),
    ("Kubernetes", r"\bkubernetes\b|\bk8s\b"),
    ("Terraform", r"\bterraform\b"),
    ("Ansible", r"\bansible\b"),
    ("Helm", r"\bhelm\b"),
    # CI/CD
    ("GitHub Actions", r"\bgithub\s+actions\b"),
    ("GitLab CI", r"\bgitlab\s+ci(?:/cd)?\b"),
    ("Jenkins", r"\bjenkins\b"),
    ("ArgoCD", r"\bargocd\b|\bargo\s+cd\b"),
    ("CircleCI", r"\bcircleci\b"),
    # Cloud
    ("AWS", r"\baws\b|\bamazon\s+web\s+services\b"),
    ("GCP", r"\bgcp\b|\bgoogle\s+cloud\b"),
    ("Azure", r"\bazure\b"),
    ("Vercel", r"\bvercel\b"),
    ("Heroku", r"\bheroku\b"),
    # APIs / Messaging
    ("GraphQL", r"\bgraphql\b"),
    ("gRPC", r"\bgrpc\b"),
    ("REST", r"\brest(?:ful)?\s*(?:api|apis)\b"),
    ("WebSockets", r"\bwebsocket(?:s)?\b"),
    ("Kafka", r"\bkafka\b"),
    ("RabbitMQ", r"\brabbitmq\b"),
    ("Celery", r"\bcelery\b"),
    ("SQS", r"\bsqs\b|\bamazon\s+sqs\b"),
    # Tools / DevOps
    ("Git", r"\bgit\b"),
    ("Linux", r"\blinux\b|\bubuntu\b|\bdebian\b"),
    ("Nginx", r"\bnginx\b"),
    ("Prometheus", r"\bprometheus\b"),
    ("Grafana", r"\bgrafana\b"),
    ("Airflow", r"\bairflow\b"),
    ("dbt", r"\bdbt\b"),
    # Data / ML
    ("Pandas", r"\bpandas\b"),
    ("NumPy", r"\bnumpy\b"),
    ("TensorFlow", r"\btensorflow\b"),
    ("PyTorch", r"\bpytorch\b|\btorch\b"),
    ("scikit-learn", r"\bscikit[- ]learn\b|\bsklearn\b"),
    ("Spark", r"\bapache\s+spark\b|\bpyspark\b|\bspark\b"),
    ("LLM", r"\bllm\b|\blarge\s+language\s+model\b"),
]

# Compiled once at import time
_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _RAW_PATTERNS
]


# ── spaCy loader ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_nlp() -> Language:
    try:
        return spacy.load(_SPACY_MODEL)
    except OSError:
        logger.info("spacy_model_downloading", model=_SPACY_MODEL)
        subprocess.run(
            [sys.executable, "-m", "spacy", "download", _SPACY_MODEL],
            check=True,
        )
        return spacy.load(_SPACY_MODEL)


# ── JobEnricher ───────────────────────────────────────────────────────────────


class JobEnricher:
    """Extracts technology mentions from job descriptions using keyword matching.

    spaCy is loaded lazily on first use and cached for the process lifetime.
    """

    def extract_technologies(self, description: str) -> list[str]:
        """Return tech names found in description, deduplicated and sorted by mention frequency."""
        counts: list[tuple[str, int]] = []
        for canonical, pattern in _TECH_PATTERNS:
            n = len(pattern.findall(description))
            if n > 0:
                counts.append((canonical, n))
        counts.sort(key=lambda x: -x[1])
        return [name for name, _ in counts]

    def enrich(self, job: JobCreate) -> JobCreate:
        """Return a copy of job with the technologies field populated."""
        text = f"{job.title} {job.raw_description or ''}"
        technologies = self.extract_technologies(text)
        return job.model_copy(update={"technologies": technologies})


# Module-level singleton — spaCy loads once per process
_enricher = JobEnricher()


# ── Persistence helper ────────────────────────────────────────────────────────


async def enrich_and_save(session: AsyncSession, job_create: JobCreate) -> Job:
    enriched = _enricher.enrich(job_create)
    job = Job(
        external_id=enriched.external_id,
        source=enriched.source,
        title=enriched.title,
        company=enriched.company,
        city=enriched.city,
        state=enriched.state,
        remote=enriched.remote,
        contract_type=enriched.contract_type,
        seniority=enriched.seniority,
        salary_min=enriched.salary_min,
        salary_max=enriched.salary_max,
        technologies=enriched.technologies,
        raw_description=enriched.raw_description,
        url=enriched.url,
        content_hash=enriched.content_hash,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job
