
import time
def t(m):
    s = time.time()
    print(f"Importing {m}...", end="", flush=True)
    __import__(m)
    print(f" done ({time.time()-s:.2f}s)")

t("app.core.config")
t("app.core.logger")
t("app.db.supabase_client")
t("app.api.routes.summarize")
t("app.api.routes.subscription")
t("app.api.routes.chat")
t("app.api.routes.transactions")
print("All imports done")
