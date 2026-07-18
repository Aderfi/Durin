import argparse
import json
import logging

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

URL_BASE = "https://icd.who.int/browse10/2019/en"


def expandir_arbol_completo(page) -> None:
    """
    Inyecta JavaScript para iterar los 4 niveles solicitados.
    Introduce un pacing (retardo) entre clics para asegurar que las
    peticiones AJAX no sean dropeadas por exceso de concurrencia.
    """
    logger.info(
        "Iniciando la expansión secuencial. Este proceso tomará entre 5 y 10 minutos..."
    )

    # Desactivamos el timeout del contexto de Playwright.
    # Al espaciar los clics para proteger el servidor,
    # el proceso superará los 30s por defecto.
    page.set_default_timeout(0)

    script_expansion = """
    async () => {
        const delay = ms => new Promise(r => setTimeout(r, ms));
        let max_niveles = 4; // Los 4 niveles que componen la jerarquía que buscas

        for (let nivel = 0; nivel < max_niveles; nivel++) {
        //  Capturar estáticamente los botones de expansión disponibles en este momento
            let colapsados = document.querySelectorAll('.ygtv-collapsed .ygtvspacer');

            if (colapsados.length === 0) {
                break;
            }

            for (let i = 0; i < colapsados.length; i++) {
                colapsados[i].click();
                // 150ms de retardo asegura un máximo de ~6-7 peticiones por segundo.
                await delay(150);
            }

            // Margen adicional de seguridad para que el renderizado del DOM se asiente
            // antes de capturar el siguiente nivel de profundidad.
            await delay(5000);
        }
    }
    """
    page.evaluate(script_expansion)
    logger.info("Árbol expandido con éxito. Procediendo a parsear el HTML...")


def parsear_nodo_yui(nodo_div):
    """Parsea recursivamente un nodo y sus hijos dentro de la estructura YUI."""
    enlace_etiqueta = nodo_div.find("a", class_="ygtvlabel")
    if not enlace_etiqueta:
        return None, None

    codigo = enlace_etiqueta.get("data-id", "").strip()
    if not codigo:
        return None, None

    span_codigo = enlace_etiqueta.find("span", class_="icode")
    if span_codigo:
        texto_codigo = span_codigo.get_text(strip=True)
        descripcion = enlace_etiqueta.get_text(strip=True)[len(texto_codigo) :].strip()
    else:
        descripcion = enlace_etiqueta.get_text(strip=True)

    datos_nodo = {"descripcion": descripcion}

    diccionario_hijos = {}
    contenedor_hijos = nodo_div.find("div", class_="ygtvchildren", recursive=False)

    if contenedor_hijos:
        items_hijos = contenedor_hijos.find_all(
            "div", class_="ygtvitem", recursive=False
        )
        for hijo in items_hijos:
            c_codigo, c_datos = parsear_nodo_yui(hijo)
            if c_codigo:
                diccionario_hijos[c_codigo] = c_datos

    if diccionario_hijos:
        datos_nodo["subcategorias"] = diccionario_hijos

    return codigo, datos_nodo


def extraer_jerarquia(html_content: str) -> dict:
    """Busca el contenedor principal y desencadena la extracción."""
    soup = BeautifulSoup(html_content, "lxml")
    jerarquia_final = {}

    # Apuntar de manera unívoca al nodo raíz (Nivel 0)
    enlace_raiz = soup.find("a", attrs={"data-id": "root"})
    if not enlace_raiz:
        logger.error(
            "No se encontró el nodo 'root'. Posible bloqueo de red o fallo de carga."
        )
        return jerarquia_final

    tabla_raiz = enlace_raiz.find_parent("table")
    if not tabla_raiz:
        return jerarquia_final

    contenedor_hijos = tabla_raiz.find_next_sibling("div", class_="ygtvchildren")

    if contenedor_hijos:
        capitulos = contenedor_hijos.find_all("div", class_="ygtvitem", recursive=False)
        for capitulo in capitulos:
            codigo, datos = parsear_nodo_yui(capitulo)
            if codigo:
                jerarquia_final[codigo] = datos

    return jerarquia_final


def main():
    parser = argparse.ArgumentParser(description="Scraper ICD-10 Estructural Completo")
    parser.add_argument(
        "--output", default="icd_codes.json", help="Fichero JSON anidado"
    )
    args = parser.parse_args()

    ruta_salida = args.output

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=True)
        pagina = navegador.new_page()

        try:
            logger.info(f"Navegando a {URL_BASE}")
            pagina.goto(URL_BASE, wait_until="networkidle")

            expandir_arbol_completo(pagina)
            html_completo = pagina.content()

        except Exception as e:
            logger.error(f"Error de ejecución en Chromium: {e}")
            navegador.close()
            return

        navegador.close()

    logger.info("Transformando HTML a estructura JSON...")
    arbol_datos = extraer_jerarquia(html_completo)

    if arbol_datos:
        with open(ruta_salida, "w", encoding="utf-8") as f:
            json.dump(arbol_datos, f, ensure_ascii=False, indent=4)
        logger.info(
            f"Extracción exitosa. {len(arbol_datos)} \
              Capítulos guardados en '{ruta_salida}'."
        )
    else:
        logger.warning("No se extrajeron datos.")


if __name__ == "__main__":
    main()
