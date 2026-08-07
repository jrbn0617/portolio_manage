from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://portfolio:portfolio@localhost:5432/portfolio"
    cors_origins: str = "http://localhost:5173"

    # .env는 pykrx가 쓰는 KRX_ID/KRX_PW 등 이 앱의 설정 필드로 선언되지 않은
    # 값도 함께 담고 있으므로, 알 수 없는 키가 있어도 에러 내지 않는다.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
