"""Runtime configuration from environment variables (.env via docker compose env_file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_prefix maps fields to the .env names: api_token -> JOBSEARCH_API_TOKEN,
    # searxng_url -> JOBSEARCH_SEARXNG_URL, etc.
    model_config = SettingsConfigDict(env_prefix="jobsearch_", extra="ignore")

    # Required: bearer token for the REST API
    api_token: str = ""

    # Internal SearXNG base URL (compose service name)
    searxng_url: str = "http://searxng:8080"

    # SQLite database file path
    database_path: str = "/data/jobs.db"

    # Comma-separated enabled sources
    sources: str = "bundesagentur,searxng"

    # Optional: comma-separated job-board domains -> site: OR-groups for SearXNG
    site_filters: str = ""

    # Optional: EURES API key (unset = eures connector disabled)
    eures_api_key: str = ""

    # Honest User-Agent for upstream calls — do not spoof a browser.
    user_agent: str = "jobsearch-gateway/0.1.0 (homelab)"

    @property
    def enabled_sources(self) -> list[str]:
        return [s.strip() for s in self.sources.split(",") if s.strip()]

    @property
    def site_filter_list(self) -> list[str]:
        return [s.strip() for s in self.site_filters.split(",") if s.strip()]


settings = Settings()