#!/usr/bin/env python
import json
import re
from pathlib import Path

import polars as pl

_FILE_NAME = "icd_codes.json"
_FILE_OUTPUT = Path("plain_icd_codes.json")

_ICD_PATTERN = re.compile(
    r"^[A-Z]\d{2}(\.\d{1,2})?$"
)  # Ejemplo: A00, A00.0, A00.1, etc.


def import_file(file_name: str | Path):
    return json.load(open(file_name))


def plain_json(data: dict) -> list[dict]:
    """Convierte un diccionario anidado en una lista de diccionarios planos."""
    result = []

    def flatten_node(codigo: str, datos: dict, parent_code: str | None):
        # Extraer la descripción y subcategorías
        descripcion = datos.get("descripcion", "")
        subcategorias = datos.get("subcategorias", {})

        # Crear el diccionario plano para este nodo
        flat_node = {
            "codigo": codigo,
            "descripcion": descripcion,
            "parent_code": parent_code,
        }
        result.append(flat_node)

        # Recursivamente procesar las subcategorías
        for sub_codigo, sub_datos in subcategorias.items():
            flatten_node(sub_codigo, sub_datos, codigo)

    # Iniciar el proceso de aplanamiento desde la raíz
    for root_codigo, root_datos in data.items():
        flatten_node(root_codigo, root_datos, None)

    return result


def main():
    df = pl.DataFrame(plain_json(import_file(_FILE_NAME)))

    df = df.filter(
        pl.col("codigo").str.contains(_ICD_PATTERN.pattern),
    )
    df.drop_in_place("parent_code")
    print(df[:20])

    df = {row["codigo"]: row["descripcion"] for row in df.to_dicts()}

    json.dump(
        df, open(_FILE_OUTPUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2
    )
    # Dict structure: {"codigo value": "descripcion"}

    print(df)


if __name__ == "__main__":
    main()
