"""
cargar_catalogo_trcv4.py — Migracion inicial del catalogo de amenazas
TRCv4 a Supabase (tablas categorias_amenaza, catalogo_amenazas,
amenaza_tipo_activo).

DECISION (ya documentada en Metodologia_Activos_y_Dependencias.md del
proyecto, no inventada por este script): la categoria "Amenazas de
Inteligencia Artificial" se excluye por completo -- no hay activos de
tipo IA/Sistemas IA/Agentes IA en el inventario, y esos valores tampoco
existen en el ENUM tipo_magerit de schema_supabase.sql. Este script
excluye esas filas automaticamente e imprime cuales fueron.

Uso (con el .venv activo, desde la carpeta del proyecto):
    python cargar_catalogo_trcv4.py catalogo_amenazas_TRCv4.xlsx

Requiere .streamlit/secrets.toml con SUPABASE_URL y SUPABASE_KEY
(el mismo archivo que usa app.py). Requiere que catalogo_amenazas
este vacia -- este script es solo para la carga inicial.
"""
import sys

import toml
import openpyxl
from supabase import create_client

CATEGORIA_IA_EXCLUIDA = "Amenazas de Inteligencia Artificial"

TIPO_MAP = {
    "Información": "INFORMACION", "Servicio": "SERVICIO", "Software": "SOFTWARE",
    "Equipamiento": "EQUIPAMIENTO", "Comunicaciones": "COMUNICACIONES",
    "Instalaciones": "INSTALACIONES", "Personal": "PERSONAL",
}


def cargar_secrets():
    cfg = toml.load(".streamlit/secrets.toml")
    return cfg["SUPABASE_URL"], cfg["SUPABASE_KEY"]


def leer_catalogo(path):
    """Lee la pestaña Catalogo_Amenazas (cabecera en filas 1-2, datos
    desde la fila 3). Devuelve (filas_a_cargar, amenazas_excluidas)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Catalogo_Amenazas"]
    filas, excluidas = [], []
    for r in ws.iter_rows(min_row=3, values_only=True):
        amenaza = r[0]
        if amenaza is None:
            continue
        tipo_amenaza, prob, activo_ppal, subcat = r[1], r[2], r[3], r[4]
        d, i, c, a, t = r[5], r[6], r[7], r[8], r[9]

        if tipo_amenaza == CATEGORIA_IA_EXCLUIDA:
            excluidas.append(amenaza)
            continue

        tipos = [TIPO_MAP[x.strip()] for x in (activo_ppal or "").split(";") if x.strip()]
        filas.append({
            "amenaza": amenaza, "categoria": tipo_amenaza, "probabilidad": prob,
            "subcategoria": subcat, "tipos_activo": tipos,
            "D": d, "I": i, "C": c, "A": a, "T": t,
        })
    return filas, excluidas


def main():
    if len(sys.argv) != 2:
        print("Uso: python cargar_catalogo_trcv4.py catalogo_amenazas_TRCv4.xlsx")
        sys.exit(1)

    url, key = cargar_secrets()
    sb = create_client(url, key)

    # Salvaguarda: no cargar dos veces sobre datos ya existentes.
    existentes = sb.table("catalogo_amenazas").select("id", count="exact").execute()
    if (existentes.count or 0) > 0:
        print(f"ABORTADO: catalogo_amenazas ya tiene {existentes.count} fila(s). "
              "Este script es solo para la carga inicial sobre tabla vacia.")
        sys.exit(1)

    filas, excluidas = leer_catalogo(sys.argv[1])

    print(f"Filas a cargar: {len(filas)}")
    print(f"Filas excluidas (categoria '{CATEGORIA_IA_EXCLUIDA}'): {len(excluidas)}")
    for a in excluidas:
        print(f"   - {a}")
    print()

    # 1) Categorias (deduplicadas, preservando orden de aparicion)
    categorias = list(dict.fromkeys(f["categoria"] for f in filas))
    cat_id = {}
    for nombre in categorias:
        res = sb.table("categorias_amenaza").insert({"nombre": nombre}).execute()
        cat_id[nombre] = res.data[0]["id"]
    print(f"Categorias creadas: {len(cat_id)}")

    # 2) Amenazas + 3) relacion con tipos de activo
    n_amenazas, n_relaciones = 0, 0
    for f in filas:
        res = sb.table("catalogo_amenazas").insert({
            "amenaza": f["amenaza"],
            "categoria_id": cat_id[f["categoria"]],
            "probabilidad": f["probabilidad"],
            "subcategoria": f["subcategoria"],
            "degradacion_d": f["D"], "degradacion_i": f["I"], "degradacion_c": f["C"],
            "degradacion_a": f["A"], "degradacion_t": f["T"],
            "version": "v4", "vigente": True,
        }).execute()
        amenaza_id = res.data[0]["id"]
        n_amenazas += 1

        for tipo in f["tipos_activo"]:
            sb.table("amenaza_tipo_activo").insert({
                "amenaza_id": amenaza_id, "tipo_activo": tipo,
            }).execute()
            n_relaciones += 1

    print(f"Amenazas cargadas: {n_amenazas}")
    print(f"Relaciones amenaza-tipo_activo cargadas: {n_relaciones}")
    print("Carga completada.")


if __name__ == "__main__":
    main()
