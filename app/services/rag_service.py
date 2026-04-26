from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.db.vector_store import get_vector_collection

def process_and_store_document(document_id: str, pages: list[dict]):
    """
    Splits document text into chunks per page and stores them in ChromaDB with page_number metadata.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    
    collection = get_vector_collection()
    total_chunks = 0
    
    for page in pages:
        if not page["text"]:
            continue
            
        chunks = text_splitter.split_text(page["text"])
        if not chunks:
            continue
            
        collection.add(
            documents=chunks,
            metadatas=[{"document_id": document_id, "page_number": page["page_number"], "chunk_index": i} for i in range(len(chunks))],
            ids=[f"{document_id}_p{page['page_number']}_{i}" for i in range(len(chunks))]
        )
        total_chunks += len(chunks)
    
    return total_chunks

def query_relevant_context(query: str, n_results: int = 5, document_id: str | None = None) -> list[str]:
    """
    Retrieves the most relevant chunks from ChromaDB and returns them formatted with their page numbers.
    Filters by document_id if provided (Critical to prevent cross-lecture data leaking).
    """
    collection = get_vector_collection()
    
    if document_id:
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            where={"document_id": document_id}
        )
    else:
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
    
    if not results or not results.get('documents') or not results['documents'][0]:
        return []
        
    documents = results['documents'][0]
    metadatas = results['metadatas'][0] if results.get('metadatas') else []
    
    formatted_results = []
    for i, doc in enumerate(documents):
        page_num = metadatas[i].get("page_number", "Unknown") if i < len(metadatas) and metadatas[i] else "Unknown"
        # 1-indexed for the prompt formatting (since typical human pages are 1-based)
        page_hum = page_num + 1 if isinstance(page_num, int) else page_num
        formatted_results.append(f"[Page {page_hum}]: {doc}")
        
    return formatted_results
