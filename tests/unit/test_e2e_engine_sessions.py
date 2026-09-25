"""Session identity resets when a turn requests a new session or user."""

from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.schema import Case, Turn


class _Capture:
    def __enter__(self):
        return []

    def __exit__(self, *args):
        return False


class _Spy:
    def capture(self):
        return _Capture()


class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, session_id):
        self.session_id = session_id
        self.message = _Msg("ok")
        self.tools_used: list = []
        self.processing_time_ms = 1
        self.pending_approvals: list = []


class _Orch:
    """Records the (session_id_in, user_id) of each process call."""

    def __init__(self):
        self.seen: list[tuple] = []
        self.scopes: list[str | None] = []
        self._n = 0

    async def process(
        self, *, messages, session_id, user_id, user_name, spreadsheet_id=None
    ):
        self.seen.append((session_id, user_id))
        self.scopes.append(spreadsheet_id)
        if session_id is None:
            self._n += 1
            session_id = 1000 + self._n
        return _Resp(session_id)


async def test_new_session_and_user_switch_reset_identity():
    case = Case(
        id="U1",
        category="memory",
        title="session/user routing",
        turns=[
            Turn(user="a"),  # 0: first turn -> new session
            Turn(user="b"),  # 1: reuse the session
            Turn(user="c", new_session=True),  # 2: fresh session
            Turn(user="d", as_user=2),  # 3: user switch -> fresh session
        ],
    )
    orch = _Orch()
    await run_case_inprocess(orch, _Spy(), _Spy(), case, None, None)

    # (session_id passed in, user_id) per turn.
    assert orch.seen[0] == (None, 1)  # cold start
    assert orch.seen[1] == (1001, 1)  # session reused
    assert orch.seen[2] == (None, 1)  # new_session dropped the id
    assert orch.seen[3] == (None, 2)  # user switch dropped the id, ran as user 2


async def test_plain_case_reuses_one_session():
    case = Case(
        id="U2",
        category="routing",
        title="no memory features",
        turns=[Turn(user="a"), Turn(user="b"), Turn(user="c")],
    )
    orch = _Orch()
    await run_case_inprocess(orch, _Spy(), _Spy(), case, None, None)

    assert orch.seen[0] == (None, 1)
    assert orch.seen[1] == (1001, 1)
    assert orch.seen[2] == (1001, 1)


async def test_turns_bind_the_spreadsheet_they_name():
    """A turn's logical spreadsheet name reaches process() as the real id."""
    case = Case(
        id="S1",
        category="multi_spreadsheet",
        title="per-turn spreadsheet binding",
        turns=[
            Turn(user="a", spreadsheet="Toko Jakarta"),
            Turn(user="b", spreadsheet="Toko Surabaya"),
            Turn(user="c"),  # unset -> the user's default
        ],
    )
    orch = _Orch()
    await run_case_inprocess(
        orch,
        _Spy(),
        _Spy(),
        case,
        None,
        None,
        spreadsheet_ids={"Toko Jakarta": "jkt-id", "Toko Surabaya": "sby-id"},
    )
    assert orch.scopes == ["jkt-id", "sby-id", None]


async def test_unseeded_spreadsheet_name_raises_instead_of_defaulting():
    """A typo must fail loudly: silently using the default would score a leak
    case as a pass against the wrong workspace."""
    case = Case(
        id="S2",
        category="multi_spreadsheet",
        title="unknown spreadsheet name",
        turns=[Turn(user="a", spreadsheet="Toko Bandung")],
    )
    orch = _Orch()
    try:
        await run_case_inprocess(
            orch,
            _Spy(),
            _Spy(),
            case,
            None,
            None,
            spreadsheet_ids={"Toko Jakarta": "x"},
        )
    except KeyError as exc:
        assert "Toko Bandung" in str(exc)
    else:
        raise AssertionError("expected KeyError for an unseeded spreadsheet name")
    assert orch.seen == []
