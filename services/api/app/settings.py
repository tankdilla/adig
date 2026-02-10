from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    admin_token: str = "MaryAndDarrell2026."
    session_secret: str = "WYq9VE9hrR09lcvJtPGbVc4TlgHsjZuDj8dE5kqgCYA"
    session_cookie_name: str = "h2n_admin"

settings = Settings()
