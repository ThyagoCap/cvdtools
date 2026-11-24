"""Ferramentas de regressão: garantem que o código novo reproduz a pipeline antiga."""

from .compare import Difference, Tolerance, compare, format_report

__all__ = ["Difference", "Tolerance", "compare", "format_report"]
