
import time
def t(m):
    s = time.time()
    print(f"Importing {m}...", end="", flush=True)
    __import__(m)
    print(f" done ({time.time()-s:.2f}s)")

t("app.services.audio_service")
t("app.services.pdf_service")
t("app.services.rag_service")
t("app.services.summarizer_service")
t("app.services.subscription_service")
t("app.core.auth_guard")
t("app.core.queue_manager")
print("All sub-imports done")
