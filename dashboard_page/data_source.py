from json import load
from os import environ
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DashboardAPIError(Exception):
    pass


def request_json(base_url, path, **params):
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urlencode(params)
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:
            payload = load(response)
    except HTTPError as exc:
        raise DashboardAPIError(f"{path} returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DashboardAPIError(f"Could not reach {base_url}{path}.") from exc
    except ValueError as exc:
        raise DashboardAPIError(f"Invalid URL or JSON response for {path}.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise DashboardAPIError(f"{path} returned an unsupported response format.")
    return payload


def get_games():
    base_url = environ.get("DASHBOARD_COLLECTOR_URL", "http://127.0.0.1:8200")
    payload = request_json(base_url, "/v1/games")
    games = payload.get("games")
    if not isinstance(games, list) or any(
        not isinstance(game, dict)
        or type(game.get("app_id")) is not int
        or not isinstance(game.get("name"), str)
        or type(game.get("enabled")) is not bool
        for game in games
    ):
        raise DashboardAPIError("The API returned an invalid game directory.")
    return payload


def get_overview(app_id, hours):
    base_url = environ.get("DASHBOARD_OVERVIEW_URL", "http://127.0.0.1:8210")
    payload = request_json(base_url, "/v1/dashboard/overview", app_id=app_id, hours=hours)
    fields = {"as_of", "range", "status", "summary", "comparison", "trends", "alert", "review_samples"}
    if (
        payload.get("app_id") != app_id
        or not isinstance(payload.get("buckets"), list)
        or len(payload["buckets"]) != hours
        or not fields <= payload.keys()
    ):
        raise DashboardAPIError("The API did not return a snapshot for the selected game and time range.")
    return payload
