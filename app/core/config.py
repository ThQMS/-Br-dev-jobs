from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "br-dev-jobs"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/brdevjobs"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Scraping schedule
    scrape_interval_hours: int = 6
    max_concurrent_scrapers: int = 4
    playwright_headless: bool = True

    # Source URLs
    gupy_api_url: str = "https://portal.api.gupy.io/api/job"
    remoteok_rss_url: str = "https://remoteok.com/remote-dev-jobs.rss"

    # Cache
    cache_ttl_seconds: int = 300

    # CORS — comma-separated origins; "*" allows all
    cors_allow_origins: str = "*"


settings = Settings()
