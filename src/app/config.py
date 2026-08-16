from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_pro_price_id: str = ""

    checkout_success_url: str = "http://localhost:8000/billing/success"
    checkout_cancel_url: str = "http://localhost:8000/billing/cancel"


settings = Settings()
