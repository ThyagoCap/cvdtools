"""Biblioteca de curvas externas (``cvd_curves.xlsx``).

Problema: curvas obtidas fora do OrcaFlex (histerese de uma linha flexível,
momento resistente de uma vértebra de BR, tração × deformação de um cabo
sintético) eram citadas na planilha geral só como texto
("Histerese - WSI WSI 152.1524.xlsx [15]") e tinham de ser digitadas à mão no
modelo.

Solução: cada curva é registrada UMA vez numa planilha padronizada; a Input
Sheet (ou um Override) aponta para ela; o builder grava a curva no modelo como
"variable data" e o checklist confere ponto a ponto.

Formato da planilha de curvas
-----------------------------
Aba ``Índice`` (uma linha por curva):

    ID | Tipo | Load case | Aba | Unidade X | Unidade Y | Histerese (S/N) | MBL (kN)
       | Nome no OrcaFlex | Fonte (documento/rev) | Aliases | Comentário

- ``Tipo``: ``bending`` (momento × curvatura: histerese de linha, vértebra de BR)
  ou ``axial`` (tração × deformação: cabo sintético, extensão de guincho).
- ``Load case``: condição a que a curva corresponde (pressão interna, tração,
  ciclo...). Curvas da mesma família em load cases diferentes têm IDs diferentes.
- ``Aba``: nome da aba com os pontos (coluna A = X, coluna B = Y, cabeçalho na linha 1).
- ``Aliases``: textos exatos já usados na Input Sheet que significam esta curva
  (separados por ';'). Assim a planilha geral não precisa ser editada.

Formas de referência aceitas numa célula da Input Sheet ou em Overrides:

- ``curva:ID`` (ou ``curve:ID``)
- ``arquivo.xlsx#ID`` (outra planilha de curvas no mesmo formato, caminho relativo ao projeto)
- o texto exato de um alias
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

KINDS = {
    "bending": {
        "label": "Momento fletor × curvatura",
        "x_units": {"1/m": 1.0, "1/mm": 1000.0, "rad/m": 1.0},
        "y_units": {"kN.m": 1.0, "kNm": 1.0, "N.m": 1e-3, "tf.m": 9.80665},
        "hysteretic": True,
        "template_role": "CURVE_TEMPLATE_BENDING",
        "api_types": ["BendingStiffness"],
    },
    "axial": {
        "label": "Tração × deformação",
        "x_units": {"-": 1.0, "%": 0.01, "mm/m": 1e-3},
        "y_units": {"kN": 1.0, "N": 1e-3, "tf": 9.80665, "%MBL": None},
        "hysteretic": False,
        "template_role": "CURVE_TEMPLATE_AXIAL",
        "api_types": ["AxialStiffness", "LineTypeAxialStiffness"],
    },
}

# Parâmetro -> tipo de curva aceito
PARAM_KIND = {
    "line.eix": "bending", "br.eix": "bending", "winch_ext.eix": "bending",
    "winch_ext.ea": "axial", "winch_wire.ea": "axial", "crane_wire.ea": "axial",
}

INDEX_SHEET = "Índice"
INDEX_COLUMNS = ["ID", "Tipo", "Load case", "Aba", "Unidade X", "Unidade Y", "Histerese (S/N)", "MBL (kN)",
                 "Nome no OrcaFlex", "Fonte (documento/rev)", "Aliases", "Comentário"]

_REF_PREFIX = re.compile(r"^\s*(curva|curve)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_REF_FILE = re.compile(r"^\s*(.+?\.xlsx)\s*#\s*(.+?)\s*$", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


@dataclass
class Curve:
    id: str
    kind: str
    x: list[float]
    y: list[float]
    x_unit: str
    y_unit: str
    load_case: str = ""
    hysteretic: bool = False
    mbl: Optional[float] = None
    orcaflex_name: str = ""
    source: str = ""
    aliases: list[str] = field(default_factory=list)
    file: str = ""
    sheet: str = ""

    @property
    def name(self) -> str:
        return self.orcaflex_name or self.id

    def si(self) -> tuple[list[float], list[float]]:
        """Pontos em unidades do OrcaFlex (1/m e kN.m; deformação adimensional e kN)."""
        kx = KINDS[self.kind]["x_units"][self.x_unit]
        ky = KINDS[self.kind]["y_units"][self.y_unit]
        if ky is None:  # %MBL
            ky = self.mbl / 100.0
        return [v * kx for v in self.x], [v * ky for v in self.y]

    def to_json(self) -> dict:
        return {"curve_id": self.id, "kind": self.kind, "load_case": self.load_case, "orcaflex_name": self.name,
                "points": len(self.x), "file": self.file, "sheet": self.sheet, "source": self.source}

    def __str__(self) -> str:
        return f"curva {self.id} ({self.load_case or 'sem load case'})"


@dataclass
class CurveIssue:
    level: str   # "ERRO" | "AVISO"
    curve_id: str
    message: str


def check_curve(c: Curve) -> list[CurveIssue]:
    issues = []
    spec = KINDS.get(c.kind)
    if spec is None:
        return [CurveIssue("ERRO", c.id, f"Tipo '{c.kind}' inválido (use {', '.join(KINDS)})")]
    if c.x_unit not in spec["x_units"]:
        issues.append(CurveIssue("ERRO", c.id, f"Unidade X '{c.x_unit}' inválida para {c.kind}: {list(spec['x_units'])}"))
    if c.y_unit not in spec["y_units"]:
        issues.append(CurveIssue("ERRO", c.id, f"Unidade Y '{c.y_unit}' inválida para {c.kind}: {list(spec['y_units'])}"))
    if c.y_unit == "%MBL" and not c.mbl:
        issues.append(CurveIssue("ERRO", c.id, "Unidade Y em %MBL exige a coluna 'MBL (kN)'"))
    if c.hysteretic and not spec["hysteretic"]:
        issues.append(CurveIssue("ERRO", c.id, f"Histerese não se aplica a curvas do tipo {c.kind}"))
    if len(c.x) < 2:
        issues.append(CurveIssue("ERRO", c.id, f"Curva com {len(c.x)} ponto(s); mínimo 2"))
    if len(c.x) != len(c.y):
        issues.append(CurveIssue("ERRO", c.id, "Colunas X e Y com tamanhos diferentes"))
    if any(b <= a for a, b in zip(c.x, c.x[1:])):
        issues.append(CurveIssue("ERRO", c.id, "X precisa ser estritamente crescente"))
    if c.x and (c.x[0] != 0 or c.y[0] != 0):
        issues.append(CurveIssue("AVISO", c.id, f"Primeiro ponto é ({c.x[0]:g}, {c.y[0]:g}); o usual é (0, 0)"))
    if any(b < a for a, b in zip(c.y, c.y[1:])):
        issues.append(CurveIssue("AVISO", c.id, "Y não é monotônico crescente"))
    if not c.load_case:
        issues.append(CurveIssue("AVISO", c.id, "Load case não informado"))
    if not c.source:
        issues.append(CurveIssue("AVISO", c.id, "Fonte (documento/revisão) não informada"))
    return issues


class CurveLibrary:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.curves: dict[str, Curve] = {}
        self.aliases: dict[str, str] = {}
        self.files: list[Path] = []
        self.issues: list[CurveIssue] = []
        self._extra: dict[Path, "CurveLibrary"] = {}

    # ---- carga
    @classmethod
    def load(cls, paths: list[Path], base_dir: Optional[Path] = None) -> "CurveLibrary":
        lib = cls(base_dir)
        for p in paths:
            lib.add_file(Path(p))
        return lib

    def add_file(self, path: Path) -> None:
        for c in read_curve_file(path):
            if c.id in self.curves:
                self.issues.append(CurveIssue("ERRO", c.id, f"ID repetido em {path.name} e {self.curves[c.id].file}"))
                continue
            self.curves[c.id] = c
            self.issues.extend(check_curve(c))
            for a in c.aliases:
                key = _norm(a)
                if key in self.aliases and self.aliases[key] != c.id:
                    self.issues.append(CurveIssue("ERRO", c.id, f"Alias '{a}' também usado por {self.aliases[key]}"))
                self.aliases[key] = c.id
        self.files.append(path)

    # ---- consulta
    @staticmethod
    def looks_like_ref(text: str) -> bool:
        return bool(_REF_PREFIX.match(str(text)) or _REF_FILE.match(str(text)))

    def lookup(self, ref: str) -> Curve:
        text = str(ref).strip()
        m = _REF_PREFIX.match(text)
        if m:
            cid = m.group(2)
            if cid not in self.curves:
                raise KeyError(f"Curva '{cid}' não existe na biblioteca ({', '.join(p.name for p in self.files) or 'nenhum arquivo'})")
            return self.curves[cid]
        m = _REF_FILE.match(text)
        if m:
            path = (self.base_dir / m.group(1)).resolve()
            if path not in self._extra:
                if not path.is_file():
                    raise KeyError(f"Planilha de curvas '{m.group(1)}' não encontrada em {self.base_dir}")
                self._extra[path] = CurveLibrary.load([path], self.base_dir)
                self.issues.extend(self._extra[path].issues)
            return self._extra[path].lookup(f"curva:{m.group(2)}")
        key = _norm(text)
        if key in self.aliases:
            return self.curves[self.aliases[key]]
        raise KeyError(f"'{text}' não está registrado na biblioteca de curvas: use 'curva:ID' ou cadastre o texto "
                       f"na coluna Aliases do Índice")


def read_curve_file(path: Path) -> list[Curve]:
    from openpyxl import load_workbook

    from .values import parse_cell

    path = Path(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if INDEX_SHEET not in wb.sheetnames:
            raise KeyError(f"{path.name} não tem a aba '{INDEX_SHEET}' (gere um modelo com 'cvd curves init')")
        rows = list(wb[INDEX_SHEET].iter_rows(values_only=True))
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        missing = [c for c in INDEX_COLUMNS if c not in header]
        if missing:
            raise KeyError(f"Aba '{INDEX_SHEET}' de {path.name} sem as colunas {missing}")
        idx = {c: header.index(c) for c in INDEX_COLUMNS}
        curves = []
        for r in rows[1:]:
            rec = {c: (r[i] if i < len(r) else None) for c, i in idx.items()}
            if not rec["ID"]:
                continue
            sheet = str(rec["Aba"] or rec["ID"]).strip()
            if sheet not in wb.sheetnames:
                raise KeyError(f"{path.name}: curva {rec['ID']} aponta para a aba '{sheet}', que não existe")
            xs, ys = [], []
            for pr in wb[sheet].iter_rows(min_row=2, values_only=True):
                if pr is None or len(pr) < 2 or pr[0] is None or pr[1] is None:
                    if xs:
                        break
                    continue
                xs.append(float(parse_cell(pr[0])))
                ys.append(float(parse_cell(pr[1])))
            mbl = rec["MBL (kN)"]
            curves.append(Curve(
                id=str(rec["ID"]).strip(), kind=str(rec["Tipo"] or "").strip().lower(), x=xs, y=ys,
                x_unit=str(rec["Unidade X"] or "").strip(), y_unit=str(rec["Unidade Y"] or "").strip(),
                load_case=str(rec["Load case"] or "").strip(),
                hysteretic=str(rec["Histerese (S/N)"] or "").strip().lower() in ("s", "sim", "y", "yes", "true", "1"),
                mbl=float(mbl) if mbl not in (None, "") else None,
                orcaflex_name=str(rec["Nome no OrcaFlex"] or "").strip(),
                source=str(rec["Fonte (documento/rev)"] or "").strip(),
                aliases=[a.strip() for a in str(rec["Aliases"] or "").split(";") if a.strip()],
                file=path.name, sheet=sheet))
        return curves
    finally:
        wb.close()


# --------------------------------------------------------------------------- modelo de planilha

def write_curve_template(path: Path, curves: list[Curve]) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} já existe")
    wb = Workbook()
    ws = wb.active
    ws.title = INDEX_SHEET
    ws.append(INDEX_COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
    for c in curves:
        ws.append([c.id, c.kind, c.load_case, c.sheet or c.id, c.x_unit, c.y_unit, "S" if c.hysteretic else "N",
                   c.mbl, c.orcaflex_name, c.source, "; ".join(c.aliases), ""])
        cs = wb.create_sheet((c.sheet or c.id)[:31])
        cs.append([f"X [{c.x_unit}]", f"Y [{c.y_unit}]"])
        for x, y in zip(c.x, c.y):
            cs.append([x, y])
    for col, w in zip("ABCDEFGHIJKL", (22, 9, 18, 22, 10, 10, 14, 10, 30, 30, 45, 50)):
        ws.column_dimensions[col].width = w
    readme = wb.create_sheet("LEIA-ME", 0)
    for line in (__doc__ or "").strip().splitlines():
        readme.append([line])
    readme.column_dimensions["A"].width = 110
    wb.save(path)
    return path


def curve_from_sheet(workbook: Path, sheet: str, curve_id: str, x_col: int = 1, y_col: int = 3,
                     x_unit: str = "%", y_unit: str = "kN", kind: str = "axial", first_row: int = 2,
                     source: str = "", load_case: str = "") -> Curve:
    """Copia uma curva tabelada de uma aba qualquer (ex.: 'Winch Extension Stiffness' da planilha geral)."""
    from openpyxl import load_workbook

    wb = load_workbook(workbook, data_only=True, read_only=True)
    try:
        xs, ys = [], []
        for r in wb[sheet].iter_rows(min_row=first_row, values_only=True):
            x = r[x_col - 1] if len(r) >= x_col else None
            y = r[y_col - 1] if len(r) >= y_col else None
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                xs.append(float(x))
                ys.append(float(y))
            elif xs:
                break
    finally:
        wb.close()
    if xs and xs[0] != 0:
        xs.insert(0, 0.0)
        ys.insert(0, 0.0)
    return Curve(curve_id, kind, xs, ys, x_unit, y_unit, load_case=load_case, source=source,
                 orcaflex_name=curve_id, sheet=curve_id)
