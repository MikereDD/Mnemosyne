from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import httpx
import pytest

from mnemosyne.providers import musicbrainz
from mnemosyne.providers.musicbrainz import MusicBrainzError, fetch_album, parse_album


RELEASE_ID = "7516696d-6a9f-4166-84ee-5d27416e1ef1"
RELEASE_URL = f"https://musicbrainz.org/release/{RELEASE_ID}"
RECORDING_ID = "9124fd30-7071-4aeb-a259-cd272091fe3a"


@pytest.fixture(autouse=True)
def reset_request_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(musicbrainz, "_last_request_started", None)


@pytest.fixture
def release_payload() -> dict:
    return {
        "id": RELEASE_ID,
        "title": "INSCAPE",
        "date": "2018-12-07",
        "artist-credit": [{"name": "Alexandra Stréliski", "artist": {"name": "Alexandra Stréliski"}}],
        "media": [
            {
                "position": 1,
                "track-count": 2,
                "tracks": [
                    {
                        "position": 1,
                        "number": "1",
                        "title": "Plus tôt",
                        "length": 154000,
                        "recording": {"id": RECORDING_ID, "title": "Other recording title"},
                    },
                    {
                        "position": 2,
                        "number": "2",
                        "recording": {"title": "The Quiet Voice", "length": 183000},
                    },
                ],
            }
        ],
    }


def test_parses_release_tracks_with_unicode_and_recording_fallback(release_payload: dict) -> None:
    album = parse_album(release_payload, RELEASE_URL + "/")

    assert album.source_url == RELEASE_URL
    assert album.release_id == RELEASE_ID
    assert album.title == "INSCAPE"
    assert album.artist == "Alexandra Stréliski"
    assert album.date == "2018-12-07"
    assert [(track.disc, track.number, track.title, track.artist) for track in album.tracks] == [
        (1, 1, "Plus tôt", "Alexandra Stréliski"),
        (1, 2, "The Quiet Voice", "Alexandra Stréliski"),
    ]
    assert [track.length_ms for track in album.tracks] == [154000, 183000]
    assert [track.recording_id for track in album.tracks] == [RECORDING_ID, None]
    with pytest.raises(FrozenInstanceError):
        album.title = "Changed"


def test_preserves_credited_names_and_joinphrases_with_per_track_artists(release_payload: dict) -> None:
    release_payload["artist-credit"] = [
        {"name": "Credited alias", "artist": {"name": "Canonical name"}, "joinphrase": " feat. "},
        {"artist": {"name": "Second artist"}},
    ]
    first, second = release_payload["media"][0]["tracks"]
    first["artist-credit"] = [{"name": "Track artist"}]
    first["recording"]["artist-credit"] = [{"name": "Recording artist"}]
    second["recording"]["artist-credit"] = [{"name": "Recording artist"}]

    album = parse_album(release_payload, RELEASE_URL)

    assert album.artist == "Credited alias feat. Second artist"
    assert [track.artist for track in album.tracks] == ["Track artist", "Recording artist"]


def test_sorts_multiple_discs_by_positions_not_vinyl_number_labels(release_payload: dict) -> None:
    medium = release_payload["media"][0]
    medium["tracks"].reverse()
    second_medium = deepcopy(medium)
    second_medium["position"] = 2
    second_medium["tracks"][0]["number"] = "B2"
    second_medium["tracks"][1]["number"] = "B1"
    release_payload["media"] = [second_medium, medium]

    album = parse_album(release_payload, RELEASE_URL)

    assert [(track.disc, track.number) for track in album.tracks] == [(1, 1), (1, 2), (2, 1), (2, 2)]


@pytest.mark.parametrize("value", [None, "", "2018", "2018-12", "2018-12-07", "2024-02-29"])
def test_accepts_optional_and_partial_dates(release_payload: dict, value: str | None) -> None:
    release_payload["date"] = value
    assert parse_album(release_payload, RELEASE_URL).date == (value or None)


@pytest.mark.parametrize("value", ["2018-13", "2018-02-29", "2018/12/07", "0000", "18", 2018, True])
def test_rejects_malformed_dates(release_payload: dict, value: object) -> None:
    release_payload["date"] = value
    with pytest.raises(MusicBrainzError, match="release date"):
        parse_album(release_payload, RELEASE_URL)


@pytest.mark.parametrize(
    "url",
    [
        f"http://musicbrainz.org/release/{RELEASE_ID}",
        f"https://example.com/release/{RELEASE_ID}",
        f"https://musicbrainz.org.example.com/release/{RELEASE_ID}",
        f"https://musicbrainz.org@evil.example/release/{RELEASE_ID}",
        f"https://user:password@musicbrainz.org/release/{RELEASE_ID}",
        f"https://musicbrainz.org:443/release/{RELEASE_ID}",
        f"https://musicbrainz.org/ws/2/release/{RELEASE_ID}?fmt=json",
        f"https://musicbrainz.org/release/{RELEASE_ID}?query=anything",
        f"https://musicbrainz.org/release/{RELEASE_ID}#tracklist",
        f"https://musicbrainz.org/release/{RELEASE_ID}/edit",
        "https://musicbrainz.org/release/not-an-id",
        "https://[invalid",
    ],
)
def test_invalid_urls_never_make_a_request(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Invalid URL caused a request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MusicBrainzError, match="MusicBrainz release page"):
            fetch_album(url, client=client)


def test_release_group_gives_edition_selection_instruction() -> None:
    with pytest.raises(MusicBrainzError, match="Choose a release edition"):
        fetch_album(f"https://musicbrainz.org/release-group/{RELEASE_ID}")


@pytest.mark.parametrize("field", ["title", "artist-credit", "media"])
def test_rejects_missing_album_metadata(release_payload: dict, field: str) -> None:
    del release_payload[field]
    with pytest.raises(MusicBrainzError, match="missing|no complete track list"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_response_for_different_release(release_payload: dict) -> None:
    release_payload["id"] = RECORDING_ID
    with pytest.raises(MusicBrainzError, match="different release"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_partial_track_list(release_payload: dict) -> None:
    release_payload["media"][0]["track-count"] = 11
    with pytest.raises(MusicBrainzError, match="incomplete track list"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_partial_disc_list_when_declared(release_payload: dict) -> None:
    release_payload["media-count"] = 2
    with pytest.raises(MusicBrainzError, match="incomplete disc list"):
        parse_album(release_payload, RELEASE_URL)


@pytest.mark.parametrize("position", [0, -1, None, "1", True])
def test_rejects_invalid_track_positions(release_payload: dict, position: object) -> None:
    release_payload["media"][0]["tracks"][0]["position"] = position
    with pytest.raises(MusicBrainzError, match="track position must be a positive integer"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_repeated_and_missing_track_positions(release_payload: dict) -> None:
    release_payload["media"][0]["tracks"][1]["position"] = 1
    with pytest.raises(MusicBrainzError, match="repeats disc 1, track 1"):
        parse_album(release_payload, RELEASE_URL)
    release_payload["media"][0]["tracks"][1]["position"] = 3
    with pytest.raises(MusicBrainzError, match="track positions are incomplete"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_repeated_and_missing_disc_positions(release_payload: dict) -> None:
    release_payload["media"].append(deepcopy(release_payload["media"][0]))
    with pytest.raises(MusicBrainzError, match="repeats disc 1"):
        parse_album(release_payload, RELEASE_URL)
    release_payload["media"][1]["position"] = 3
    with pytest.raises(MusicBrainzError, match="disc positions are incomplete"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_untitled_track(release_payload: dict) -> None:
    release_payload["media"][0]["tracks"][0]["title"] = " "
    release_payload["media"][0]["tracks"][0]["recording"]["title"] = ""
    with pytest.raises(MusicBrainzError, match="has no track title"):
        parse_album(release_payload, RELEASE_URL)


def test_rejects_malformed_artist_credit(release_payload: dict) -> None:
    release_payload["artist-credit"] = [{"name": "Artist", "joinphrase": False}]
    with pytest.raises(MusicBrainzError, match="artist credit is incomplete"):
        parse_album(release_payload, RELEASE_URL)


@pytest.mark.parametrize("length", [-1, "123", True])
def test_rejects_invalid_track_lengths(release_payload: dict, length: object) -> None:
    release_payload["media"][0]["tracks"][0]["length"] = length
    with pytest.raises(MusicBrainzError, match="length is invalid"):
        parse_album(release_payload, RELEASE_URL)


def test_fetch_uses_fixed_api_parameters_and_keeps_supplied_client_open(release_payload: dict) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=release_payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        album = fetch_album(RELEASE_URL, client=client)
        assert not client.is_closed
    assert album.title == "INSCAPE"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "musicbrainz.org"
    assert request.url.path == f"/ws/2/release/{RELEASE_ID}"
    assert dict(request.url.params) == {"inc": "recordings+artist-credits+release-groups", "fmt": "json"}
    assert request.headers["User-Agent"] == musicbrainz._USER_AGENT
    assert request.headers["Accept"] == "application/json"
    assert request.extensions["timeout"] == {"connect": 5.0, "read": 15.0, "write": 15.0, "pool": 15.0}


def test_requests_start_no_more_than_once_per_second(
    release_payload: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    times = iter([0.0, 0.0, 0.25, 1.0])
    sleeps: list[float] = []
    monkeypatch.setattr(musicbrainz.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(musicbrainz.time, "sleep", sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=release_payload))) as client:
        fetch_album(RELEASE_URL, client=client)
        fetch_album(RELEASE_URL, client=client)
    assert sleeps == [0.75]


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "could not find"), (429, "Try again later"), (503, "Try again later"), (400, "HTTP 400")],
)
def test_reports_http_errors(status: int, message: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
        with pytest.raises(MusicBrainzError, match=message):
            fetch_album(RELEASE_URL, client=client)


def test_refuses_redirects_even_when_supplied_client_follows_them() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example/metadata"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(MusicBrainzError, match="HTTP 302"):
            fetch_album(RELEASE_URL, client=client)
    assert requested_hosts == ["musicbrainz.org"]


def test_reports_network_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("connection timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MusicBrainzError, match="Could not retrieve MusicBrainz metadata"):
            fetch_album(RELEASE_URL, client=client)


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"not-json"), httpx.Response(200, json=[])])
def test_reports_invalid_json_responses(response: httpx.Response) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(MusicBrainzError, match="invalid"):
            fetch_album(RELEASE_URL, client=client)
