import requests

from config import API_HOST, API_PASSWORD, API_TIMEOUT_SECONDS, API_USERNAME


class MakeRequest:
    def __init__(self):
        self.host = API_HOST
        self.username = API_USERNAME
        self.password = API_PASSWORD
        self.last_error = ""
        self.headers = {}
        self.timeout = API_TIMEOUT_SECONDS
        self.session = requests.Session()
        self.auth()

    def auth(self):
        if not self.username or not self.password:
            self.last_error = "Missing API credentials (REELTUG_API_USERNAME / REELTUG_API_PASSWORD)."
            return False, None

        body = {"username": self.username, "password": self.password}
        try:
            r = self.session.post(self.host + "/login", json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            self.last_error = f"API login request failed: {exc}"
            return False, None

        if r.status_code == 200:
            token = r.json()["access_token"]
            self.headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            self.last_error = ""
            return True, r

        self.last_error = f"API login failed with status {r.status_code}."
        return False, r

    def make_get(self, endpoint, timeout=None, retries=0):
        url = self.host + endpoint
        request_timeout = self.timeout if timeout is None else timeout

        r = None
        last_exc = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, headers=self.headers, timeout=request_timeout)
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
                self.last_error = f"GET {endpoint} failed (attempt {attempt + 1}/{retries + 1}): {exc}"
        if last_exc is not None:
            return False, None

        if r.status_code == 401:
            result, auth_res = self.auth()
            if not result:
                if auth_res is not None:
                    self.last_error = f"GET {endpoint} unauthorized. Re-auth failed with status {auth_res.status_code}."
                return False, auth_res

            try:
                r = self.session.get(url, headers=self.headers, timeout=request_timeout)
            except requests.RequestException as exc:
                self.last_error = f"GET {endpoint} failed after re-auth: {exc}"
                return False, None
            if r.status_code == 401:
                self.last_error = f"GET {endpoint} unauthorized after re-auth."
                return False, r

        if r.status_code != 200:
            self.last_error = f"GET {endpoint} returned status {r.status_code}."
            return False, r

        self.last_error = ""
        return r.json(), r

    def make_post(self, endpoint, body=None):
        if body is None:
            body = {}

        url = self.host + endpoint
        try:
            r = self.session.post(url, headers=self.headers, json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            self.last_error = f"POST {endpoint} failed: {exc}"
            return False, None

        if r.status_code == 401:
            result, auth_res = self.auth()
            if not result:
                if auth_res is not None:
                    self.last_error = f"POST {endpoint} unauthorized. Re-auth failed with status {auth_res.status_code}."
                return False, auth_res

            try:
                r = self.session.post(url, headers=self.headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                self.last_error = f"POST {endpoint} failed after re-auth: {exc}"
                return False, None
            if r.status_code == 401:
                self.last_error = f"POST {endpoint} unauthorized after re-auth."
                return False, r

        if r.status_code != 200:
            self.last_error = f"POST {endpoint} returned status {r.status_code}."
            return False, r

        self.last_error = ""
        return r.json(), r
