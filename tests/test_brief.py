"""The brief parser takes plain symmetry statements and ignores negated, hedged or off-centre ones."""

from agentic_cad import brief

BOX = {"x": 40.0, "y": 60.0, "z": 40.0}


def kinds(text: str, dims: dict = BOX) -> set[tuple]:
    return {(c.kind, c.axis, c.folds) for c in brief.parse(text, dims)}


def test_plain_planes():
    text = "The part is symmetric about the vertical plane X = 20 and about the horizontal plane Z = 20; " \
           "it is **not** symmetric front‑to‑back."
    assert kinds(text) == {("mirror", "x", 0), ("mirror", "z", 0)}


def test_named_plane_and_direction():
    assert kinds("The body is mirror‑symmetric about the central Y–Z plane (X = 20).") == {("mirror", "x", 0)}
    assert kinds("The banks mirror each other, so the part is symmetric top‑to‑bottom in Z but **not** "
                 "symmetric in X.") == {("mirror", "z", 0)}


def test_negated_hedged_and_off_centre_are_ignored():
    assert kinds("The part has no plane or rotational symmetry.") == set()
    assert kinds("The part is roughly symmetric about the X = 20 mid-plane for the flange.") == set()
    assert kinds("The part is symmetric about its mid-height plane Z = 20 for the main circular features.") == set()
    assert kinds("The part is symmetric about the plane X = 12.") == set()


def test_turned_parts():
    square = {"x": 50.0, "y": 90.0, "z": 50.0}
    assert kinds("## Overall shape\nThe part is a body of revolution about the Y axis, so it is fully axisymmetric.",
                 square) == {("turn", "y", brief.REVOLUTION_FOLDS)}
    assert kinds("The fin pattern is four‑fold symmetric about Z.", {"x": 50, "y": 50, "z": 30}) == {("turn", "z", 4)}
    # a revolution about Y needs equal X and Z sizes
    assert kinds("The part is a body of revolution about the Y axis.", BOX | {"z": 30.0}) == set()
    assert kinds("The overall envelope is a cone of revolution about the Z axis.", square) == set()
