import json
import time
import urllib.parse
import urllib.request

API_BASE = "https://api.simkl.com"

class SimklApi:
    def __init__(self, addon):
        self.addon = addon
        self.client_id = addon.getSettingString("client_id").strip()
        self.token = addon.getSettingString("access_token").strip()
        self.debug = addon.getSettingBool("debug_logging")

    def save_token(self, token: str):
        self.addon.setSettingString("access_token", token)
        self.token = token

    def is_configured(self) -> bool:
        return bool(self.client_id)

    def is_authorized(self) -> bool:
        return bool(self.token)

    def _headers(self, auth=False):
        # SIMKL requires simkl-api-key header with your client_id. [1](https://forums.trakt.tv/t/next-episode-missing-from-watched-progress-on-api/88273)
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
            "User-Agent": "kodi-simkl-watching/0.1.1",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path, params=None, auth=False):
        if not self.client_id:
            raise RuntimeError("Missing SIMKL Client ID in add-on settings.")
        if auth and not self.token:
            raise RuntimeError("Missing SIMKL access token. Authorize first.")

        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, headers=self._headers(auth=auth))
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data
    def get_show_details_full(self, simkl_id: int):
        """
        Fetch show details with full metadata.
        SIMKL supports extended=full for complete info.  [2](https://www.reddit.com/r/StremioAddons/comments/sw8ucz/sync_stremio_progress_to_trakt/)
        """
        return self._get(f"/tv/{simkl_id}", params={"extended": "full"}, auth=False)
    # -------------------------
    # PIN / device flow (already working)
    # -------------------------
    def request_pin(self):
        # GET /oauth/pin?client_id=... [1](https://forums.trakt.tv/t/next-episode-missing-from-watched-progress-on-api/88273)
        return self._get("/oauth/pin", params={"client_id": self.client_id}, auth=False)

    def poll_pin(self, user_code: str, interval: int, expires_in: int, progress_cb=None):
        # Poll GET /oauth/pin/{user_code} [1](https://forums.trakt.tv/t/next-episode-missing-from-watched-progress-on-api/88273)
        deadline = time.time() + int(expires_in)
        wait = max(1, int(interval))

        while time.time() < deadline:
            if progress_cb:
                remaining = int(deadline - time.time())
                progress_cb(remaining)

            res = self._get(f"/oauth/pin/{user_code}", auth=False)
            token = res.get("access_token") or res.get("token")
            if token:
                return token

            time.sleep(wait)

        return None

    # -------------------------
    # Step 4A: Watching items
    # -------------------------
    def get_watching_shows(self):
        """
        Fetch items from the user's SIMKL Watching list.

        SIMKL exposes list-style endpoints under /sync/all-items/*
        (commonly seen as /sync/all-items/shows/<listname> in other integrations). [4](https://www.reddit.com/r/sonarr/comments/10mf426/does_list_importing_from_simkl_work/)

        We'll start with 'watching'. If SIMKL returns a different shape, debug logging will show it.
        """
        data = self._get("/sync/all-items/shows/watching", auth=True)

        return data