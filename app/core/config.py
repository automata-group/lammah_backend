from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Smart Lecture Summarizer"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # CORS
    BACKEND_CORS_ORIGINS: list[str] = [
        "https://lammah.site",
        "https://www.lammah.site",
        "https://api.lammah.site",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:18080",
        "http://127.0.0.1:18080",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3003",
        "http://localhost:5173",
    ]
    
    # SUPABASE
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str | None = None
    
    # VECTOR DB
    CHROMA_DB_DIR: str = "./chroma_data"
    
    # SECURITY
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # AI APIs
    GEMINI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GPT_AUDIO_KEY: str | None = None
    YOUTUBE_DOWNLOADER_URL: str | None = None

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()
