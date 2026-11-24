"""OrcFxAPI falso para testes sem OrcaFlex.

Um "modelo" é um dicionário {objeto: {propriedade: valor}} salvo em JSON.
Suporta o que o builder, o checklist, a extração e os mares usam.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

BASE_OBJECTS = {
    "General": {"StageDuration": [15.0, 600.0], "StaticsMaxIterations": 10000, "StaticsMinDamping": 200,
                "StaticsMaxDamping": 1000, "ImplicitUseVariableTimeStep": "No", "JacobianPerturbationFactor": 1e-5,
                "ImplicitConstantTimeStep": 0.1, "TargetLogSampleInterval": 0.1},
    "Environment": {"WaterDepth": 1000.0, "SeabedNormalStiffness": 1.0, "SeabedShearStiffness": 1.0,
                    "WaveType": "Airy", "WaveDirection": 0.0, "WaveHs": 1.0, "WaveTp": 8.0},
    "Island Performer": {"InitialHeading": 30.0},
    "Hub Height": {"Length": 1.0, "ShapeType": "Elastic solid"},
    "MCV": {"Mass": 1.0, "Volume": 0.1, "VertexX": [0.0] * 12, "VertexY": [0.0] * 12, "VertexZ": [0.0] * 12,
            "InitialZ": 0.0, "Height": 1.986, "CentreOfMassX": 0.203, "CentreOfMassY": 0.0, "CentreOfMassZ": 0.929,
            "CentreOfVolumeX": 0.104, "CentreOfVolumeY": 0.0, "CentreOfVolumeZ": 0.772,
            "MomentOfInertiaX": 100.0, "MomentOfInertiaY": 100.0, "MomentOfInertiaZ": 100.0},
    "Crane Wire": {"Length": [100.0, 20.0], "EndBX": 0.0, "EndBY": 0.0, "EndBZ": 0.0, "StaticsStep1": "None"},
    "Winch Wire": {"Length": [832.0, 400.0], "LineType": ["Winch Wire Type", "Winch Extension Wire Type"],
                   "NumberOfSegments": [50, 50], "StaticsStep1": "None", "EndAInitialArcLength": 0.0,
                   "EndAConnection": "Fixed", "EndAX": 17.0, "EndAY": 0.0, "EndAZ": 25.0},
    "Masterlink": {"InitialX": 18.5, "InitialY": 0.0, "InitialZ": -10.0, "Connection": "Free", "Mass": 0.5,
                   "Volume": 0.06},
    "Line": {"EndAConnection": "Fixed", "EndAX": 0.0, "EndAY": 0.0, "EndAZ": 0.0, "EndAAzimuth": 0.0,
             "EndADeclination": 90.0, "EndAGamma": 0.0, "EndBX": 0.0, "EndBConnection": "Anchored", "TopEnd": "End B",
             "Length": [1.0, 1.0, 1.0, 1.0, 1.0], "NumberOfSegments": [1, 1, 1, 1, 1],
             "AttachmentType": ["Bend Restrictor"], "Attachmentx": [0.0], "Attachmenty": [0.0], "Attachmentz": [0.0],
             "AttachmentzRelativeTo": ["End A"], "AttachmentName": ["Bend Restrictor Element"],
             "ContentsMethod": "Uniform", "ContentsDensity": 1.025, "StaticsStep1": "None"},
    "Bend Restrictor": {"Length": 1.0, "LineType": "Bend Restrictor Type", "ConnectionArclength": 0.0,
                        "ConnectionArclengthRelativeTo": "End A"},
    "Bend Restrictor Element": {},
    "Bend Restrictor Stiffness Curve": {"_type": "BendingStiffness", "IndependentValue": [0.0, 1.0, 2.0],
                                        "DependentValue": [0.0, 1.0, 2.0], "Hysteretic": "No"},
    "Link CB 1": {"EndAZ": 0.0, "UnstretchedLength": 1.0, "Stiffness": 1.0},
    "Link CB 2": {"EndAZ": 0.0, "UnstretchedLength": 1.0, "Stiffness": 1.0},
}
for _lt in ("Line Type", "Crane Wire Type", "Winch Wire Type", "Winch Extension Wire Type", "Bend Restrictor Type",
            "EndFitting Type"):
    BASE_OBJECTS[_lt] = {"OD": 0.25, "ID": 0.0, "MassPerUnitLength": 0.18, "xMinRadius": 1.0, "EA": 700000.0,
                         "EIx": 120.0, "GJ": 80.0, "SeabedLateralFrictionCoefficient": 0.5,
                         "SeabedAxialFrictionCoefficient": 0.5}


class FakeObject:
    def __init__(self, model, name):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_name", name)

    @property
    def _props(self):
        return self._model.objects[self._name]

    def __getattr__(self, item):
        if item == "type":
            return self._props.get("_type", "?")
        if item == "NumberOfSections":
            return len(self._props["Length"])
        if item == "NodeArclengths":
            out, acc = [0.0], 0.0
            for length, n in zip(self._props["Length"], self._props.get("NumberOfSegments", [1] * 99)):
                for _ in range(int(n)):
                    acc += length / int(n)
                    out.append(acc)
            return out
        if item == "CreateClone":
            def clone(name=None, model=None):
                self._model.objects[name] = json.loads(json.dumps(self._props))
                return FakeObject(self._model, name)
            return clone
        if item == "CumulativeLength":
            out, acc = [], 0.0
            for x in self._props["Length"]:
                acc += x
                out.append(acc)
            return out
        props = self._props
        if item in props:
            return props[item]
        for k in props:  # o OrcFxAPI não diferencia maiúsculas nos nomes de dados (Initialz == InitialZ)
            if k.lower() == item.lower():
                return props[k]
        raise AttributeError(f"{self._name} não tem {item}")

    def __setattr__(self, item, value):
        if item == "NumberOfSections":  # redimensiona as listas por seção, como o OrcaFlex
            for key, fill in (("Length", 1.0), ("LineType", None), ("NumberOfSegments", 1)):
                cur = self._props.setdefault(key, [])
                del cur[value:]
                cur.extend([fill if fill is not None else (cur[-1] if cur else "") ] * (value - len(cur)))
            return
        for k in list(self._props):
            if k.lower() == item.lower():
                item = k
                break
        self._props[item] = list(value) if isinstance(value, (list, tuple)) else value

    def StaticResult(self, var, extra=None):
        return self._model.results.get((self._name, var, str(extra)), [0.0])[0]

    # resultados
    def TimeHistory(self, var, period=None, extra=None):
        series = self._model.results.get((self._name, var, str(extra)), [0.0])
        if period is not None and getattr(period, "kind", None) == "static":
            return [series[0]]
        return list(series)

    def FrequencyDomainResults(self, var, extra=None):
        return self._model.fd_result(self._name, var, extra)

    def FrequencyDomainMPM(self, var, hours, extra=None):
        return self._model.fd_mpm(self._name, var, extra, hours)

    def RangeGraph(self, var, period=None, objectExtra=None, arclengthRange=None, stormDurationHours=None):
        # como o ctypes do OrcFxAPI real: o 4º argumento precisa ser um ArclengthRange
        if arclengthRange is not None and not (isinstance(arclengthRange, tuple) and arclengthRange[:1] == ("arc",)):
            raise TypeError(f"argument 4: expected LP_ArclengthRange instance instead of {type(arclengthRange).__name__}")
        if stormDurationHours is not None:
            return self._model.fd_range(self._name, var, arclengthRange, stormDurationHours)
        vals = self._model.results.get((self._name, var, "range"), [0.0])
        if getattr(period, "kind", None) == "specified" and period.start < 0:
            vals = vals[:1]
        return types.SimpleNamespace(Min=list(vals), Max=list(vals), Mean=list(vals))

    def GetData(self, name, index):
        return self._props[name]


class FakeModel:
    def __init__(self, path=None):
        self.path = path
        self.results: dict = {}
        if path is None:
            self.objects = {}
        else:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.objects = data["objects"]
            self.results = {tuple(k.split("|")): v for k, v in data.get("results", {}).items()}
            self.state = types.SimpleNamespace(name=data.get("state", "SimulationStopped"))
            self.simulationComplete = data.get("complete", True)

    def __getitem__(self, name):
        if name not in self.objects:
            raise KeyError(f"Objeto {name!r} não existe")
        return FakeObject(self, name)

    @property
    def environment(self):
        return self["Environment"]

    @property
    def general(self):
        return self["General"]

    def DestroyObject(self, obj):
        del self.objects[obj._name]

    def CreateObject(self, otype, name):
        props = {"_type": otype}
        if otype == "Constraint":   # como o OrcaFlex: todos os graus de liberdade presos, eixos globais
            props.update(DOFFree=["No"] * 6, InitialX=0.0, InitialY=0.0, InitialZ=0.0)
        self.objects[name] = props
        return FakeObject(self, name)

    def NewVariationModel(self, path):
        self.objects = json.loads(Path(path).read_text(encoding="utf-8"))["objects"]
        self.objects["_base"] = {"file": str(path)}

    def SaveData(self, path):
        Path(path).write_text(json.dumps({
            "objects": self.objects,
            "results": {"|".join(k): v for k, v in self.results.items()},
        }), encoding="utf-8")

    def SaveWaveSearchSpreadsheet(self, path):
        hs = self.objects["Environment"].get("WaveHs", 1.0)
        # Formato imitado: o 4º campo é o instante (vírgula decimal, como no Windows pt-BR)
        Path(path).write_text(
            f"Wave search\nLargest rise at {100 + hs:.2f}".replace(".", ",") + " s\n"
            f"Largest fall at {900 + hs:.2f}".replace(".", ",") + " s\n", encoding="utf-8")

    def CalculateStatics(self):
        pass

    def RunSimulation(self):
        self.check_frequency_domain()

    def check_frequency_domain(self):
        """Como o OrcaFlex 11.6: FD não aceita rigidez flexional histerética (mensagem real do GLF27)."""
        if self.objects.get("General", {}).get("DynamicsSolutionMethod") != "Frequency domain":
            return
        for name, props in self.objects.items():
            ei = props.get("EIx") if isinstance(props, dict) else None
            if isinstance(ei, str) and self.objects.get(ei, {}).get("Hysteretic") == "Yes":
                raise RuntimeError("Operation not available (This model uses a feature (hysteretic bend stiffness, "
                                   f"line type '{name}') that is not available for frequency domain analysis.)")

    def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
        pass

    def SaveSimulation(self, path):
        self.SaveData(path)


class _Period:
    def __init__(self, kind, start=None, end=None):
        self.kind, self.start, self.end = kind, start, end


def make_module() -> types.ModuleType:
    m = types.ModuleType("OrcFxAPI")
    m.Model = FakeModel
    m.PeriodNum = types.SimpleNamespace(StaticState="static")
    m.Period = lambda num: _Period("static")
    m.SpecifiedPeriod = lambda a, b: _Period("specified", a, b)
    m.oeEndA, m.oeEndB, m.oeTouchdown = "EndA", "EndB", "Touchdown"
    m.oeArcLength = lambda s: f"arc={float(s):g}"
    m.arSpecifiedArclengths = lambda a, b: ("arc", a, b)
    m.ObjectType = types.SimpleNamespace(Link="Link", Buoy3D="Buoy3D", Constraint="Constraint")
    m.DLLVersion = lambda: "fake"
    return m


def write_base_model(path: Path) -> Path:
    model = FakeModel()
    model.objects = json.loads(json.dumps(BASE_OBJECTS))
    model.SaveData(path)
    return path
