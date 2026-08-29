"""Safe authentication facade; secrets are never returned."""
class AuthStatus:
    def __init__(self, client): self.client = client
    def authenticate(self) -> bool: return self.client.authenticate()
    def status(self) -> dict: return dict(getattr(self.client, "last_auth", {"status":"NOT_ATTEMPTED"}))
