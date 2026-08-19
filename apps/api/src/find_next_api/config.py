from functools import lru_cache
from urllib.parse import quote

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    web_origin: str = "http://127.0.0.1:3000"
    database_url: str | None = None
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5434
    postgres_db: str = "findstocks"
    postgres_user: str = "findstocks"
    postgres_password: SecretStr = SecretStr("findstocks")
    alpha_vantage_api_key: SecretStr = SecretStr("")
    upstox_access_token: SecretStr = SecretStr("")
    upstox_analytics_token: SecretStr = SecretStr("")
    refresh_yahoo_batch_size: int = 25
    refresh_alpha_vantage_batch_size: int = 5

    @property
    def upstox_token(self) -> str:
        return (
            self.upstox_analytics_token.get_secret_value()
            or self.upstox_access_token.get_secret_value()
        )

    @property
    def effective_database_url(self) -> str:
        component_fields = {"postgres_db", "postgres_user", "postgres_password"}
        if self.database_url and not component_fields.issubset(self.model_fields_set):
            return self.database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        database = quote(self.postgres_db, safe="")
        return (
            f"postgresql://{user}:{password}@{self.postgres_host}:"
            f"{self.postgres_port}/{database}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
