import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
import chromadb
from chromadb.config import Settings
from app.core.config import settings

def get_chroma_client():
    # Ensure the directory exists
    os.makedirs(settings.CHROMA_DB_DIR, exist_ok=True)
    
    # Initialize ChromaDB client persisting to disk
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_DIR)
    return client

def get_vector_collection(collection_name: str = "lectures_collection"):
    client = get_chroma_client()
    collection = client.get_or_create_collection(name=collection_name)
    return collection
