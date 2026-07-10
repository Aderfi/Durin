"""
Scraper del índice ATC/DDD (atcddd.fhi.no) -> genera un JSON plano {codigo: nombre}
recorriendo la jerarquía completa (nivel 1 a 5).

Estructura del sitio: cada página ?code=X lista como texto plano (no tabla) los
hijos directos de X como enlaces ?code=... Las páginas de nivel 4 son hoja y
muestran una tabla de sustancias (nivel 5), cuyos códigos/nombres se extraen
sin necesidad de visitar cada sustancia.

Longitudes de código por nivel: 1 -> 1 char, 2 -> 3, 3 -> 4, 4 -> 5, 5 -> 7.

Requisitos:
    pip install requests beautifulsoup4 tenacity

Uso:
    python atc_scraper.py --output codes.json
    python atc_scraper.py --root A10          # solo un subárbol (pruebas)
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

BASE_URL = "https://atcddd.fhi.no/atc_ddd_index/"
REQUEST_DELAY_SECONDS = 1.0
HEADERS = {"User-Agent": "atc-scraper/1.0 (uso academico/investigacion)"}
ATC_CODE_RE = re.compile(r"^[A-Z]\d{0,2}[A-Z]{0,2}\d{0,2}$")
LEVEL5_CODE_LENGTH = 7  # las sustancias (nivel 5) no tienen página propia que recorrer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
def fetch_page(code: str) -> str:
    """Descarga el HTML de la página de un código ATC (code='' para la página raíz)."""
    url = f"{BASE_URL}?code={code}&showdescription=no" if code else BASE_URL
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def extract_code_from_href(href: str) -> str | None:
    """Extrae el valor del parámetro ?code=... de una URL. None si no aplica."""
    query = parse_qs(urlparse(href).query)
    values = query.get("code")
    if not values:
        return None
    code = values[0]
    return code if ATC_CODE_RE.match(code) else None


def parse_links(html: str) -> list[tuple[str, str]]:
    """Extrae todos los pares (codigo, nombre) de los enlaces ?code=... de la página."""
    soup = BeautifulSoup(html, "html.parser")
    pairs = []
    for link in soup.find_all("a", href=True):
        code = extract_code_from_href(link["href"])
        if code is None:
            continue
        name = link.get_text(strip=True)
        if name:
            pairs.append((code, name))
    return pairs


def load_checkpoint(path: Path) -> tuple[dict, set[str]]:
    """Devuelve (codes, visited). Acepta checkpoints antiguos (dict plano de códigos)."""
    if not path.exists():
        return {}, set()
    logger.info("Reanudando desde checkpoint: %s", path)
    data = json.loads(path.read_text())
    if "codes" in data and "visited" in data:
        return data["codes"], set(data["visited"])
    return data, set()  # formato antiguo: solo nombres, sin registro de visitados


def save_checkpoint(path: Path, codes: dict, visited: set[str]) -> None:
    payload = {"codes": codes, "visited": sorted(visited)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def scrape_recursive(code: str, results: dict, visited: set[str], checkpoint_path: Path) -> None:
    """
    Recorre en profundidad desde `code`. Añade a `results` todos sus descendientes.
    No visita códigos de nivel 5: sus nombres ya vienen en la tabla de la página padre.
    """
    if code in visited:
        return
    visited.add(code)

    logger.info("Procesando %r (%d códigos acumulados)", code or "(raiz)", len(results))
    html = fetch_page(code)

    all_pairs = parse_links(html)
    # Descendientes: códigos que empiezan por el código actual y no son el propio código.
    # Esto descarta el rastro de ancestros (breadcrumb) que aparece arriba de cada página.
    descendants = [(c, n) for c, n in all_pairs if c != code and c.startswith(code)]

    for child_code, child_name in descendants:
        results.setdefault(child_code, child_name)

    save_checkpoint(checkpoint_path, results, visited)
    time.sleep(REQUEST_DELAY_SECONDS)

    for child_code, _ in descendants:
        if len(child_code) < LEVEL5_CODE_LENGTH:
            scrape_recursive(child_code, results, visited, checkpoint_path)


def main():
    parser = argparse.ArgumentParser(description="Scraper del índice ATC/DDD")
    parser.add_argument("--output", default="codes.json", help="Fichero JSON de salida")
    parser.add_argument("--checkpoint", default="checkpoint.json", help="Fichero de progreso")
    parser.add_argument("--root", default="", help="Código ATC raíz (vacío = índice completo)")
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    output_path = Path(args.output)
    results, visited = load_checkpoint(checkpoint_path)

    if args.root and args.root not in results:
        # incluir el propio código raíz con su nombre (aparece en su breadcrumb)
        pairs = dict(parse_links(fetch_page(args.root)))
        if args.root in pairs:
            results[args.root] = pairs[args.root]
        time.sleep(REQUEST_DELAY_SECONDS)

    scrape_recursive(args.root, results, visited, checkpoint_path)

    output_path.write_text(
        json.dumps(dict(sorted(results.items())), ensure_ascii=False, indent=2)
    )
    logger.info("Terminado. %d códigos guardados en %s", len(results), output_path)


if __name__ == "__main__":
    main()
