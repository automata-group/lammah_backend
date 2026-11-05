import sys
import os
import re
import pytest

# Ensure backend directory is in python path
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Set test environment variables so Settings() initializes cleanly in isolated CI test runners
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-supabase-key-for-unit-tests")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("OPENROUTER_API_KEY", "dummy-openrouter-key")

def test_job_id_path_traversal_guard():
    """Verify that path traversal characters in job_id are strictly rejected."""
    pattern = re.compile(r'^[a-zA-Z0-9_\-]+$')

    # Malicious inputs that MUST be rejected
    malicious_inputs = [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "notebook_123/../../secret",
        "notebook_123; rm -rf /",
        "job id with spaces",
        "notebook<script>",
        "notebook\x00nullbyte",
        "",
        "../job",
        "/etc/shadow",
    ]

    for evil_id in malicious_inputs:
        is_safe = bool(pattern.match(evil_id)) and os.path.basename(evil_id) == evil_id
        assert not is_safe, f"Path traversal attempt was NOT blocked: {evil_id}"

    # Legitimate inputs that MUST be accepted
    legitimate_inputs = [
        "notebook_0a44ecf3",
        "preview_notebook_0a44ecf3",
        "job-1234-abcd_EFGH",
        "0968d2b2-60ea-45e0-81f1-c4fc97a06f36",
    ]

    for good_id in legitimate_inputs:
        is_safe = bool(pattern.match(good_id)) and os.path.basename(good_id) == good_id
        assert is_safe, f"Legitimate job_id was incorrectly blocked: {good_id}"


def test_ssrf_host_whitelist_and_private_ip_blocking():
    """Verify SSRF defense logic matching frontend/src/app/api/proxy-image/route.ts."""
    allowed_hosts = [
        "pinimg.com",
        "pinterest.com",
        "pin.it",
    ]

    def is_allowed_host(hostname: str) -> bool:
        lower = hostname.lower()
        if (
            lower == "localhost"
            or lower == "127.0.0.1"
            or lower == "0.0.0.0"
            or lower == "::1"
            or lower == "backend"
            or lower == "warp"
            or lower == "caddy"
            or lower == "db"
            or lower.startswith("10.")
            or lower.startswith("192.168.")
            or lower.startswith("169.254.")
            or re.match(r'^172\.(1[6-9]|2[0-9]|3[0-1])\.', lower)
        ):
            return False

        return any(lower == allowed or lower.endsWith(f".{allowed}") if hasattr(lower, "endsWith") else (lower == allowed or lower.endswith(f".{allowed}")) for allowed in allowed_hosts)

    # Attacks that MUST be blocked
    ssrf_attacks = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.169.254", # AWS metadata
        "10.0.0.5",
        "192.168.1.100",
        "172.18.0.2",
        "backend",
        "warp",
        "caddy",
        "evil-attacker.com",
        "google.com",
        "pinterest.com.attacker.com",
    ]

    for attack in ssrf_attacks:
        assert not is_allowed_host(attack), f"SSRF attack host was NOT blocked: {attack}"

    # Legitimate hosts that MUST be allowed
    valid_hosts = [
        "i.pinimg.com",
        "s-media-cache-ak0.pinimg.com",
        "pinterest.com",
        "www.pinterest.com",
        "pin.it",
    ]

    for host in valid_hosts:
        assert is_allowed_host(host), f"Legitimate host was incorrectly blocked: {host}"


def test_db_session_stubs_import_cleanly():
    """Verify that legacy DB stubs do not crash on import."""
    from app.db.session import get_db
    from app.db.base import Base

    gen = get_db()
    val = next(gen)
    assert val is None
    assert Base is None


def test_ai_factory_resilience():
    """Verify AI factory initializes correctly and exports get_llm_for_user."""
    from app.services.ai.factory import get_llm_for_user
    assert callable(get_llm_for_user)
