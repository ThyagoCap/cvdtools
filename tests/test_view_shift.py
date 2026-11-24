"""Translação do modelo (todos os objetos menos o hub) e teste dos pontos do MoveObjects."""

import types

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.view import root_points, shift_model_x


class Obj:
    def __init__(self, name, typeName, **props):
        self.name, self.typeName = name, typeName
        self.__dict__.update(props)


def fake_orc(valid: dict, moves: list):
    """MoveObjects que aceita só os índices em ``valid[nome]`` (ex.: base 1) e registra os movimentos."""
    orc = types.SimpleNamespace()
    orc.MoveObjectDisplacementSpecification = lambda d: tuple(d)
    orc.MoveObjectPoint = lambda obj, i: (obj, i)

    def move(spec, pts):
        for obj, i in pts:
            if i not in valid.get(obj.name, ()):
                raise RuntimeError("Invalid parameter (Unrecognised point)")
        if spec != (0.0, 0.0, 0.0):
            moves.append((spec, [(o.name, i) for o, i in pts]))
    orc.MoveObjects = move
    return orc


def model():
    objs = [
        Obj("General", "General"), Obj("Environment", "Environment"), Obj("Line Type", "Line type"),
        Obj("MCV", "6D buoy", Connection="Free"), Obj("Masterlink", "3D buoy", Connection="Free"),
        Obj("Hub Height", "Shape", Connection="Anchored"),
        Obj("Crane Wire", "Line", EndAConnection="Fixed", EndBConnection="MCV"),
        Obj("Winch Wire", "Line", EndAConnection="Fixed", EndBConnection="Masterlink"),
        Obj("Line", "Line", EndAConnection="MCV", EndBConnection="Anchored"),
        Obj("Link CB 1", "Link", EndAConnection="Line", EndBConnection="Masterlink"),
    ]
    return types.SimpleNamespace(objects=objs, **{"__getitem__": None})


def test_root_points_probe_one_based_and_skip_hub(legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    valid = {"MCV": {1}, "Masterlink": {1}, "Hub Height": {1}, "Crane Wire": {1, 2}, "Winch Wire": {1, 2},
             "Line": {1, 2}, "Link CB 1": {1, 2}}
    moves = []
    m = model()
    pts = root_points(m, case, fake_orc(valid, moves))
    got = {(label, i) for _, i, label, _ in pts}
    assert got == {("MCV", 1), ("Masterlink", 1), ("Crane Wire End A", 1), ("Winch Wire End A", 1),
                   ("Line End B", 2)}
    assert all("Hub" not in label for _, _, label, _ in pts)


def test_shift_uses_moveobjects_with_probed_points(legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    valid = {"MCV": {0}, "Masterlink": {0}, "Crane Wire": {0, 1}, "Winch Wire": {0, 1}, "Line": {0, 1},
             "Link CB 1": {0, 1}}
    moves = []
    method = shift_model_x(model(), case, -0.583, fake_orc(valid, moves))
    assert method == "MoveObjects"
    assert moves[0][0] == (-0.583, 0.0, 0.0)
    assert ("Line", 1) in moves[0][1] and ("Crane Wire", 0) in moves[0][1] and len(moves[0][1]) == 5


def test_two_ends_moves_mcv_b_and_hub_b_but_not_hub(legacy_inputs):
    """0.11.5: com 2 extremidades só o 'Hub Height' fica; MCV B e hub B andam com o resto (corpo rígido)."""
    case = case_from_legacy_json(legacy_inputs)
    case.roles.update({"HUB_B": "Hub Height B", "MCV_B": "MCV B"})
    m = model()
    m.objects += [Obj("MCV B", "6D buoy", Connection="Fixed"), Obj("Hub Height B", "Shape", Connection="Anchored")]
    pts = root_points(m, case)
    labels = {label for _, _, label, _ in pts}
    assert {"MCV B", "Hub Height B", "MCV"} <= labels and "Hub Height" not in labels
