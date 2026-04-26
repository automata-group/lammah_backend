
import time
import os
def t(m):
    s = time.time()
    print(f"Importing {m}...", end="", flush=True)
    __import__(m)
    print(f" done ({time.time()-s:.2f}s)")

t("langchain.text_splitter")
t("app.db.vector_store")
print("All rag sub-imports done")
